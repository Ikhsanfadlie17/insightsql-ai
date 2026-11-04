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
* ``forbidden_table`` — only the active dataset's table ``dataset`` and CTEs defined
  in the same query; no schema/catalog qualifiers, file paths, or other tables.
* ``forbidden_function`` — no file/network readers, table functions, system or
  configuration functions, or functions that can generate huge outputs.
* ``recursive_cte`` / ``cartesian_join`` / ``too_many_joins`` — resource limits.
* ``unknown_column`` — every column must exist in the active dataset (or a CTE).

The executor adds a read-only, no-external-access sandbox, a timeout, and a row
limit as defense in depth; this module does not rely on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from backend.core.errors import SQLValidationError

TABLE_NAME = "dataset"
MAX_SQL_CHARS = 20_000
MAX_JOINS = 5

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
    # unbounded generators
    "range", "generate_series", "repeat", "unnest_range",
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


def _is_cartesian(join: exp.Join) -> bool:
    if str(join.args.get("kind") or "").upper() == "CROSS":
        return True
    if join.args.get("using"):
        return False
    condition = join.args.get("on")
    if condition is None:
        return True  # comma join or join without a condition
    # ON TRUE / ON 1 = 1: a condition that references no column joins every row pair.
    return not any(condition.find_all(exp.Column))


def _structural_reasons(tree: exp.Expression, schema: TableSchema) -> list[str]:
    reasons: list[str] = []
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed_tables = cte_names | {schema.table.lower()}

    for with_ in tree.find_all(exp.With):
        if with_.args.get("recursive"):
            reasons.append("recursive_cte: recursive queries are not allowed")

    for node in tree.find_all(exp.Func):
        if _is_forbidden_function(node):
            reasons.append(f"forbidden_function: {_function_name(node)}")

    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # table functions are reported as forbidden_function
        if table.args.get("db") or table.args.get("catalog"):
            reasons.append(f"forbidden_table: {table.sql(dialect='duckdb')} (qualified name)")
        elif table.name.lower() not in allowed_tables:
            reasons.append(f"forbidden_table: {table.name}")

    joins = list(tree.find_all(exp.Join))
    if len(joins) > MAX_JOINS:
        reasons.append(f"too_many_joins: {len(joins)} joins (maximum {MAX_JOINS})")
    for join in joins:
        if _is_cartesian(join):
            reasons.append("cartesian_join: every join needs a condition on columns")
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
    try:
        # A comment after the final ";" parses as a comment-only Semicolon node: ignore it.
        statements = [
            s
            for s in sqlglot.parse(sql, dialect="duckdb")
            if s is not None and not isinstance(s, _COMMENT_ONLY)
        ]
    except ParseError:
        raise SQLValidationError(["parse_error: the SQL could not be parsed"]) from None
    if not statements:
        raise SQLValidationError(["empty: no SQL statement found"])
    if len(statements) > 1:
        raise SQLValidationError([f"multiple_statements: {len(statements)} statements found"])

    tree = statements[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        kind = type(tree).__name__.upper()
        raise SQLValidationError([f"not_select: {kind} statements are not allowed"])

    reasons = _structural_reasons(tree, schema)
    if not reasons:
        reasons = _column_reasons(tree, schema)
    if reasons:
        raise SQLValidationError(list(dict.fromkeys(reasons)))

    tables = frozenset(t.name.lower() for t in tree.find_all(exp.Table))
    return ValidatedSQL(tree.sql(dialect="duckdb"), tables, _token=_TOKEN)
