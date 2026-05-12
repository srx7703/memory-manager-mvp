"""SQLite 存储层:连接管理、建表、CRUD;dataclass ↔ row 转换集中在此。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator, List, Optional

from .schemas import Memory, MemoryStatus, RawMessage

_DDL = """
CREATE TABLE IF NOT EXISTS raw_messages (
    message_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, session_id TEXT,
    message TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_profile ON raw_messages(profile_id);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, raw_message_id TEXT,
    type TEXT NOT NULL, content TEXT NOT NULL,
    entities TEXT NOT NULL, keywords TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0, ttl_days INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_mem_profile ON memories(profile_id);
CREATE INDEX IF NOT EXISTS idx_mem_type    ON memories(type);
CREATE INDEX IF NOT EXISTS idx_mem_compound ON memories(profile_id, type, status);
"""


class Storage:
    """sqlite3 轻封装,所有写入走 `connect()` 上下文,自动 close 防泄漏。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with self.connect() as conn:
            conn.executescript(_DDL)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _exec(self, sql: str, params: tuple) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def insert_raw(self, raw: RawMessage) -> None:
        self._exec(
            "INSERT INTO raw_messages(message_id, profile_id, session_id, message, created_at) "
            "VALUES(?,?,?,?,?)",
            (raw.message_id, raw.profile_id, raw.session_id, raw.message, raw.created_at),
        )

    def insert_memory(self, mem: Memory) -> None:
        self._exec(
            "INSERT INTO memories(memory_id, profile_id, raw_message_id, type, content, "
            "entities, keywords, created_at, updated_at, weight, ttl_days, status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mem.memory_id, mem.profile_id, mem.raw_message_id, mem.type, mem.content,
                json.dumps(mem.entities, ensure_ascii=False),
                json.dumps(mem.keywords, ensure_ascii=False),
                mem.created_at, mem.updated_at, mem.weight, mem.ttl_days, mem.status,
            ),
        )

    def update_memory_weight(self, memory_id: str, weight: float, updated_at: str) -> None:
        self._exec(
            "UPDATE memories SET weight=?, updated_at=? WHERE memory_id=?",
            (weight, updated_at, memory_id),
        )

    def update_status(self, memory_id: str, status: str) -> None:
        self._exec("UPDATE memories SET status=? WHERE memory_id=?", (status, memory_id))

    def find_duplicate(
        self, profile_id: str, mem_type: str, content_norm: str
    ) -> Optional[Memory]:
        """同 profile + 同 type + 内容归一化字符串相同 → 命中。归一化由调用方完成。"""
        from .extractor import normalize_content  # 局部导入避免循环
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE profile_id=? AND type=? AND status=?",
                (profile_id, mem_type, MemoryStatus.ACTIVE.value),
            ).fetchall()
        for r in rows:
            if normalize_content(r["content"]) == content_norm:
                return _row_to_memory(r)
        return None

    def list_active_by_profile(self, profile_id: str) -> List[Memory]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE profile_id=? AND status=?",
                (profile_id, MemoryStatus.ACTIVE.value),
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def list_active_by_profile_and_types(
        self, profile_id: str, types: List[str]
    ) -> List[Memory]:
        if not types:
            return []
        placeholders = ",".join("?" for _ in types)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE profile_id=? AND status=? AND type IN ({placeholders})",
                (profile_id, MemoryStatus.ACTIVE.value, *types),
            ).fetchall()
        return [_row_to_memory(r) for r in rows]


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        memory_id=row["memory_id"],
        profile_id=row["profile_id"],
        raw_message_id=row["raw_message_id"],
        type=row["type"],
        content=row["content"],
        entities=json.loads(row["entities"]) if row["entities"] else [],
        keywords=json.loads(row["keywords"]) if row["keywords"] else [],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        weight=float(row["weight"]),
        ttl_days=row["ttl_days"],
        status=row["status"],
    )
