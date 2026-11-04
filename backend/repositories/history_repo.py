"""Conversations and analyses (SQLite). A conversation belongs to exactly one dataset."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.core.errors import NotFoundError
from backend.repositories.db import connect
from backend.schemas.analysis import AnalysisResponse, Conversation, ConversationSummary
from backend.schemas.llm_outputs import HistoryTurn

MAX_TITLE_CHARS = 80


class HistoryRepository:
    """Stores every analysis with its SQL, result, insight, and usage."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def create_conversation(self, dataset_id: str, title: str) -> ConversationSummary:
        summary = ConversationSummary(
            id=uuid.uuid4().hex,
            dataset_id=dataset_id,
            title=" ".join(title.split())[:MAX_TITLE_CHARS] or "Conversation",
            created_at=datetime.now(UTC),
        )
        with connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO conversations (id, dataset_id, title, created_at) VALUES (?, ?, ?, ?)",
                (summary.id, summary.dataset_id, summary.title, summary.created_at.isoformat()),
            )
        return summary

    def get_summary(self, conversation_id: str) -> ConversationSummary:
        with connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM analyses a WHERE a.conversation_id = c.id) "
                "AS analysis_count FROM conversations c WHERE c.id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("This conversation was not found.")
        return ConversationSummary.model_validate(dict(row))

    def add_analysis(self, analysis: AnalysisResponse) -> None:
        with connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO analyses (id, conversation_id, dataset_id, question, status, sql, "
                "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    analysis.analysis_id,
                    analysis.conversation_id,
                    analysis.dataset_id,
                    analysis.question,
                    analysis.status,
                    analysis.sql,
                    analysis.model_dump_json(),
                    analysis.created_at.isoformat(),
                ),
            )

    def list_conversations(self, dataset_id: str) -> list[ConversationSummary]:
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM analyses a WHERE a.conversation_id = c.id) "
                "AS analysis_count FROM conversations c WHERE c.dataset_id = ? "
                "ORDER BY c.created_at DESC, c.rowid DESC",
                (dataset_id,),
            ).fetchall()
        return [ConversationSummary.model_validate(dict(r)) for r in rows]

    def get_conversation(self, conversation_id: str) -> Conversation:
        summary = self.get_summary(conversation_id)
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT response_json FROM analyses WHERE conversation_id = ? "
                "ORDER BY created_at, rowid",
                (conversation_id,),
            ).fetchall()
        return Conversation(
            **summary.model_dump(),
            analyses=[AnalysisResponse.model_validate_json(r["response_json"]) for r in rows],
        )

    def delete_conversation(self, conversation_id: str) -> None:
        with connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cursor.rowcount == 0:
            raise NotFoundError("This conversation was not found.")

    def recent_turns(self, conversation_id: str, n: int = 3) -> list[HistoryTurn]:
        """Last ``n`` turns, oldest first: questions, SQL, and clarifying questions only."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT question, sql, status, response_json FROM analyses "
                "WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (conversation_id, n),
            ).fetchall()
        turns = []
        for row in reversed(rows):
            sql, clarification = None, None
            if row["status"] == "clarification":
                clarification = AnalysisResponse.model_validate_json(row["response_json"]).answer
            elif row["status"] == "answered":
                sql = row["sql"]
                retention = AnalysisResponse.model_validate_json(row["response_json"]).retention
                if retention is not None:  # the long template SQL tells the model nothing
                    sql = (
                        f"-- client retention comparison: {retention.previous.label} "
                        f"({retention.previous.start} to {retention.previous.end}, end "
                        f"exclusive) vs {retention.current.label} ({retention.current.start} "
                        f"to {retention.current.end}), computed by the analytics engine"
                    )
            turns.append(
                HistoryTurn(
                    question=row["question"][:2000],
                    sql=sql,
                    clarification=clarification[:500] if clarification else None,
                )
            )
        return turns
