"""The only gate between generated SQL and execution.

``validate_sql`` parses SQL with SQLGlot (DuckDB dialect) and returns a
``ValidatedSQL`` only when every rule holds. ``ValidatedSQL`` cannot be created
anywhere else, and the executor accepts nothing else, so unvalidated SQL can never
run. Rules (each failure reason starts with its rule name):

* ``empty`` / ``too_long`` / ``parse_error`` — basic input checks.
* ``multiple_statements`` — exactly one statement.
* ``not_select`` — the statement must be a SELECT or a set operation (UNION, ...);
  this allowlist rejects every write, DDL, session, and utility statement,
  including ones SQLGlot only knows as generic commands (LOAD, CALL, ...).
* ``not_select`` also applies inside the query: DESCRIBE/SUMMARIZE/SHOW subqueries
  and any write or utility node anywhere are rejected.
* ``forbidden_table`` — every table reference, resolved **per scope**, must be the
  active dataset's table ``dataset`` or a CTE visible in that scope; no
  schema/catalog qualifiers, file paths, or other tables (system catalogs included).
* ``forbidden_table_function`` — no table functions or table macros in FROM/JOIN
  (they can read catalogs, files, or generate unbounded rows).
* ``forbidden_function`` — no file/network readers, system or configuration
  functions, error-raising functions, or functions that can generate huge values.
* ``too_complex`` / ``recursive_cte`` / ``cartesian_join`` / ``too_many_joins`` —
  resource limits (joins must relate both sides with an equality or USING).
* ``unknown_column`` — every column must exist in the active dataset (or a CTE).

The executor adds a read-only, no-external-access sandbox, a timeout, and a row
limit as defense in depth; this module does not rely on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from backend.core.errors import SQLValidationError

TABLE_NAME = "dataset"
MAX_SQL_CHARS = 20_000
MAX_JOINS = 5
MAX_NODES = 2_000
MAX_PAREN_DEPTH = 64

_DUCKDB_TYPES = {
    "integer": "BIGINT",
    "numeric": "DOUBLE",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "boolean": "BOOLEAN",
    "text": "VARCHAR",
}
_ALLOWED_ROOTS = (exp.Select, exp.SetOperation)
_FORBIDDEN_FUNCTIONS = {
    # file / network / SQL-execution functions
    "glob", "query", "query_table", "sniff_csv", "json_execute_serialized_sql",
    "load", "install", "attach",
    # environment / configuration / catalog
    "getenv", "current_setting", "current_schema", "current_schemas", "which_secret",
    # functions that put arbitrary values into error messages
    "error",
    # unbounded generators / huge values (they ignore timeouts and memory limits)
    "range", "generate_series", "repeat", "unnest_range", "list_resize", "array_resize",
    "pg_sleep", "sleep", "sleep_ms", "write_log",
    "lpad", "rpad", "pad", "histogram", "histogram_values", "repeat_row", "bar", "printf", "format",
}  # fmt: skip
_FORBIDDEN_PREFIXES = (
    "read_", "parquet_", "iceberg_", "delta_", "sqlite_", "postgres_", "mysql_",
    "duckdb_", "pragma_", "json_execute", "http", "s3_", "azure_", "gcs_", "ducklake_",
)  # fmt: skip
_FORBIDDEN_FUNCTION_CLASSES = tuple(
    cls
    for cls in (
        getattr(exp, name, None)
        for name in ("ReadCSV", "ReadParquet", "GenerateSeries", "ExplodingGenerateSeries")
    )
    if cls is not None
)

_COMMENT_ONLY = tuple(c for c in (getattr(exp, "Semicolon", None),) if c is not None)
# Statement / utility nodes that must not appear anywhere inside a query.
_FORBIDDEN_NODES = tuple(
    cls
    for cls in (
        getattr(exp, name, None)
        for name in (
            "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Copy",
            "Attach", "Detach", "Install", "Pragma", "Set", "Use", "Transaction", "Commit",
            "Rollback", "Command", "Describe", "Summarize", "Show", "Into", "Analyze",
        )
    )
    if cls is not None
)  # fmt: skip
_TOKEN = object()


class ValidatedSQL:
    """SQL that passed :func:`validate_sql`. Immutable; cannot be built directly."""

    __slots__ = ("sql", "tables")

    def __init__(self, sql: str, tables: frozenset[str] = frozenset(), *, _token: object = None):
        if _token is not _TOKEN:
            raise TypeError("ValidatedSQL can only be created by validate_sql().")
        object.__setattr__(self, "sql", sql)
        object.__setattr__(self, "tables", tables)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ValidatedSQL is immutable.")

    def __repr__(self) -> str:
        return f"ValidatedSQL({self.sql!r})"


@dataclass(frozen=True)
class TableSchema:
    """Columns of the active dataset's table (name -> logical type)."""

    columns: dict[str, str] = field(default_factory=dict)
    table: str = TABLE_NAME

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {
            self.table: {
                name: _DUCKDB_TYPES.get(logical, "VARCHAR")
                for name, logical in self.columns.items()
            }
        }


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _is_forbidden_function(node: exp.Func) -> bool:
    if isinstance(node, _FORBIDDEN_FUNCTION_CLASSES):
        return True
    name = _function_name(node)
    return name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_PREFIXES)


def _relates_both_sides(condition: exp.Expression) -> bool:
    """True if an equality compares qualified columns of two different tables."""
    for eq in condition.find_all(exp.EQ):
        left = {c.table for c in eq.left.find_all(exp.Column)}
        right = {c.table for c in eq.right.find_all(exp.Column)}
        if left and right and "" not in left | right and left.isdisjoint(right):
            return True
    return False


def _is_cartesian(join: exp.Join) -> bool:
    if str(join.args.get("kind") or "").upper() == "CROSS":
        return True
    if join.args.get("using"):
        return False
    condition = join.args.get("on")
    # No condition (comma join), ON TRUE, ON a.x = a.x, ON a.x <> b.x ... all pair every row.
    return condition is None or not _relates_both_sides(condition)


def _paren_depth(sql: str) -> int:
    depth = deepest = 0
    for char in sql:
        if char == "(":
            depth += 1
            deepest = max(deepest, depth)
        elif char == ")":
            depth -= 1
    return deepest


def _table_reasons(tree: exp.Expression, schema: TableSchema) -> list[str]:
    """Resolve every table reference in its own scope (CTEs are only visible there)."""
    reasons: list[str] = []
    for clause in tree.find_all(exp.From, exp.Join):
        source = clause.this
        if not isinstance(source, exp.Table | exp.Subquery | exp.Values):
            name = type(source).__name__.lower() if source is not None else "?"
            reasons.append(f"forbidden_table_function: {name} as a table source")
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            name = table.this.sql(dialect="duckdb").split("(")[0] if table.this else "?"
            reasons.append(f"forbidden_table_function: {name}")
        elif table.args.get("db") or table.args.get("catalog"):
            reasons.append(f"forbidden_table: {table.sql(dialect='duckdb')} (qualified name)")
    if reasons:
        return reasons
    try:
        scopes = traverse_scope(tree)
    except (OptimizeError, SqlglotError, RecursionError):
        return ["unsupported_query: the query structure could not be analysed"]
    for scope in scopes:
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope):
                continue  # a CTE or derived table visible in this scope
            if table.name.lower() != schema.table.lower():
                reasons.append(f"forbidden_table: {table.name}")
    return reasons


def _structural_reasons(tree: exp.Expression, schema: TableSchema) -> list[str]:
    reasons: list[str] = []
    for node in tree.find_all(*_FORBIDDEN_NODES):
        reasons.append(f"not_select: {type(node).__name__.upper()} is not allowed in a query")
    if reasons:
        return reasons

    for with_ in tree.find_all(exp.With):
        if with_.args.get("recursive"):
            reasons.append("recursive_cte: recursive queries are not allowed")

    for node in tree.find_all(exp.Func):
        if _is_forbidden_function(node):
            reasons.append(f"forbidden_function: {_function_name(node)}")

    reasons.extend(_table_reasons(tree, schema))

    joins = list(tree.find_all(exp.Join))
    if len(joins) > MAX_JOINS:
        reasons.append(f"too_many_joins: {len(joins)} joins (maximum {MAX_JOINS})")
    for join in joins:
        if _is_cartesian(join):
            reasons.append(
                "cartesian_join: every join needs an equality between columns of both tables "
                "(or USING)"
            )
    return reasons


def _column_reasons(tree: exp.Expression, schema: TableSchema) -> list[str]:
    try:
        qualify(
            tree.copy(),
            schema=schema.sqlglot_schema(),
            dialect="duckdb",
            validate_qualify_columns=True,
        )
    except OptimizeError as error:
        message = str(error).splitlines()[0]
        if "could not be resolved" in message or "Unknown column" in message:
            return [f"unknown_column: {message}"]
        return [f"unsupported_query: {message}"]
    return []


def validate_sql(sql: str, schema: TableSchema) -> ValidatedSQL:
    """Validate one SQL query against the active dataset.

    Args:
        sql: Candidate SQL (from the LLM or from analytics templates).
        schema: Columns of the active dataset.

    Raises:
        SQLValidationError: With one reason per broken rule.
    """
    if not sql or not sql.strip():
        raise SQLValidationError(["empty: no SQL was provided"])
    if len(sql) > MAX_SQL_CHARS:
        raise SQLValidationError([f"too_long: more than {MAX_SQL_CHARS} characters"])
    if _paren_depth(sql) > MAX_PAREN_DEPTH:
        raise SQLValidationError([f"too_complex: nesting deeper than {MAX_PAREN_DEPTH} levels"])
    try:
        # A comment after the final ";" parses as a comment-only Semicolon node: ignore it.
        statements = [
            s
            for s in sqlglot.parse(sql, dialect="duckdb")
            if s is not None and not isinstance(s, _COMMENT_ONLY)
        ]
    except (SqlglotError, RecursionError):  # ParseError, TokenError, ...
        raise SQLValidationError(["parse_error: the SQL could not be parsed"]) from None
    if not statements:
        raise SQLValidationError(["empty: no SQL statement found"])
    if len(statements) > 1:
        raise SQLValidationError([f"multiple_statements: {len(statements)} statements found"])

    tree = statements[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        kind = type(tree).__name__.upper()
        raise SQLValidationError([f"not_select: {kind} statements are not allowed"])

    node_count = sum(1 for _ in tree.walk())
    if node_count > MAX_NODES:
        raise SQLValidationError([f"too_complex: {node_count} syntax nodes (max {MAX_NODES})"])

    reasons = _structural_reasons(tree, schema)
    if not reasons:
        reasons = _column_reasons(tree, schema)
    if reasons:
        raise SQLValidationError(list(dict.fromkeys(reasons)))

    tables = frozenset(t.name.lower() for t in tree.find_all(exp.Table))
    return ValidatedSQL(tree.sql(dialect="duckdb"), tables, _token=_TOKEN)
