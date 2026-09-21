"""SQLite persistence for approvals and immutable audit events."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from src.marketing.models import ActionStatus, MarketingAction, utc_now


class MarketingStore:
    def __init__(self, path: str | Path = "data/marketing.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS marketing_actions (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS marketing_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT, event TEXT NOT NULL,
                    actor TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    def save_action(self, action: MarketingAction, actor: str = "system") -> MarketingAction:
        document = action.model_dump_json()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO marketing_actions(id, document, created_at) VALUES(?,?,?)",
                (action.id, document, action.created_at.isoformat()),
            )
            conn.execute(
                "INSERT INTO marketing_audit(action_id,event,actor,details,created_at) VALUES(?,?,?,?,?)",
                (action.id, "action_saved", actor, json.dumps({"status": action.status.value}), utc_now().isoformat()),
            )
        return action

    def get_action(self, action_id: str) -> MarketingAction | None:
        with self._connect() as conn:
            row = conn.execute("SELECT document FROM marketing_actions WHERE id=?", (action_id,)).fetchone()
        return MarketingAction.model_validate_json(row["document"]) if row else None

    def list_actions(self, status: ActionStatus | None = None) -> list[MarketingAction]:
        with self._connect() as conn:
            rows = conn.execute("SELECT document FROM marketing_actions ORDER BY created_at DESC").fetchall()
        actions = [MarketingAction.model_validate_json(row["document"]) for row in rows]
        return [a for a in actions if status is None or a.status == status]

    def record(self, action_id: str, event: str, actor: str, details: dict | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO marketing_audit(action_id,event,actor,details,created_at) VALUES(?,?,?,?,?)",
                (action_id, event, actor, json.dumps(details or {}, ensure_ascii=False), utc_now().isoformat()),
            )

    def audit(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT action_id,event,actor,details,created_at FROM marketing_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(row), "details": json.loads(row["details"])} for row in rows]
