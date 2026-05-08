from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HISTORY_COLUMNS = ", ".join(
    (
        "id",
        "created_at",
        "text",
        "raw_text",
        "duration_ms",
        "inference_ms",
        "model",
        "backend",
        "pasted",
    )
)


class HistoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    created_at: str
    text: str
    raw_text: str | None
    duration_ms: int | None
    inference_ms: int | None
    model: str | None
    backend: str | None
    pasted: bool


def default_history_path() -> Path:
    return Path.home() / ".local" / "share" / "voicium" / "history.sqlite"


class HistoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_history_path()

    def add(
        self,
        *,
        text: str,
        raw_text: str | None = None,
        duration_ms: int | None = None,
        inference_ms: int | None = None,
        model: str | None = None,
        backend: str | None = None,
        pasted: bool = False,
    ) -> HistoryEntry:
        self._ensure_schema()
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transcriptions (
                    created_at, text, raw_text, duration_ms, inference_ms, model, backend, pasted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    text,
                    raw_text,
                    duration_ms,
                    inference_ms,
                    model,
                    backend,
                    int(pasted),
                ),
            )
            entry_id = cursor.lastrowid
        if entry_id is None:
            raise HistoryError("Unable to determine inserted history id.")
        return self.get(entry_id)

    def list(self, *, limit: int = 20) -> list[HistoryEntry]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {HISTORY_COLUMNS} FROM transcriptions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def get(self, entry_id: int) -> HistoryEntry:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {HISTORY_COLUMNS} FROM transcriptions WHERE id = ?",
                (entry_id,),
            ).fetchone()
        if row is None:
            raise HistoryError(f"History entry not found: {entry_id}")
        return _entry_from_row(row)

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw_text TEXT,
                    duration_ms INTEGER,
                    inference_ms INTEGER,
                    model TEXT,
                    backend TEXT,
                    pasted INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def format_history_entries(entries: Iterable[HistoryEntry]) -> str:
    lines = []
    for entry in entries:
        preview = entry.text.replace("\n", " ")[:80]
        lines.append(f"{entry.id}\t{entry.created_at}\t{preview}")
    return "\n".join(lines)


def _entry_from_row(row: sqlite3.Row | tuple[object, ...]) -> HistoryEntry:
    return HistoryEntry(
        id=int(row[0]),
        created_at=str(row[1]),
        text=str(row[2]),
        raw_text=str(row[3]) if row[3] is not None else None,
        duration_ms=int(row[4]) if row[4] is not None else None,
        inference_ms=int(row[5]) if row[5] is not None else None,
        model=str(row[6]) if row[6] is not None else None,
        backend=str(row[7]) if row[7] is not None else None,
        pasted=bool(row[8]),
    )
