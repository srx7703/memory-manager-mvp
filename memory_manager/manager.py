"""MemoryManager 对外门面:add_feedback / get_context 两入口,组合分类/抽取/去重/冲突/检索全流程。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .classifier import classify
from .config import CONFLICT_PAIRS, DEFAULT_TTL_DAYS
from .extractor import extract_entities, extract_keywords, normalize_content
from .retriever import rank_and_select
from .schemas import Memory, MemoryStatus, RawMessage
from .storage import Storage


def _utc_now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _resolve_ts(message_ts: Optional[str], now: Optional[datetime]) -> str:
    """优先尊重调用方传入的 created_at,fallback 到注入的 now,再兜底 utcnow。

    把 ISO8601 的 Z 后缀归一化为 +00:00,保证 Python 3.10 的 datetime.fromisoformat
    能解析(3.11+ 才原生支持 Z)。
    """
    if message_ts:
        return message_ts.replace("Z", "+00:00") if message_ts.endswith("Z") else message_ts
    return _utc_now(now).isoformat()


class MemoryManager:
    """投资经理 Bot 的轻量记忆管理器。"""

    def __init__(self, db_path: str = "./memory.db") -> None:
        self.storage = Storage(db_path)

    def add_feedback(
        self,
        message: Dict[str, Any],
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """写入一条反馈,返回本次新增/更新的 memory 列表(dict 形式)。"""
        profile_id = message.get("profile_id")
        text = message.get("message")
        if not profile_id or not text:
            raise ValueError("message 必须包含 profile_id 与 message 字段")

        ts = _resolve_ts(message.get("created_at"), now)
        raw = RawMessage(
            message_id=_new_id(),
            profile_id=profile_id,
            session_id=message.get("session_id"),
            message=text,
            created_at=ts,
        )
        self.storage.insert_raw(raw)

        mem_type = classify(text)
        entities = extract_entities(text)
        keywords = extract_keywords(text)

        # 去重:同 profile + 同 type + 归一化内容相同 → weight += 1
        content_norm = normalize_content(text)
        dup = self.storage.find_duplicate(profile_id, mem_type.value, content_norm)
        if dup is not None:
            new_weight = dup.weight + 1.0
            self.storage.update_memory_weight(dup.memory_id, new_weight, ts)
            dup.weight = new_weight
            dup.updated_at = ts
            return [dup.to_dict()]

        # 冲突:同 profile + 共享实体 + 类型对立 → 旧记忆 superseded
        self._resolve_conflicts(profile_id, mem_type.value, entities)

        mem = Memory(
            memory_id=_new_id(),
            profile_id=profile_id,
            raw_message_id=raw.message_id,
            type=mem_type.value,
            content=text,
            entities=entities,
            keywords=keywords,
            created_at=ts,
            updated_at=ts,
            weight=1.0,
            ttl_days=DEFAULT_TTL_DAYS.get(mem_type.value),
            status=MemoryStatus.ACTIVE.value,
        )
        self.storage.insert_memory(mem)
        return [mem.to_dict()]

    def _resolve_conflicts(
        self, profile_id: str, new_type: str, new_entities: List[str]
    ) -> None:
        if not new_entities:
            return
        new_set = set(new_entities)
        opposite_types: List[str] = []
        for a, b in CONFLICT_PAIRS:
            if new_type == a:
                opposite_types.append(b)
            elif new_type == b:
                opposite_types.append(a)
        if not opposite_types:
            return
        olds = self.storage.list_active_by_profile_and_types(profile_id, opposite_types)
        for old in olds:
            if new_set & set(old.entities):
                self.storage.update_status(old.memory_id, MemoryStatus.SUPERSEDED.value)

    def get_context(
        self,
        profile_id: str,
        query: str,
        limit: int = 5,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """按 query 检索该 profile 的 active 记忆,做软 TTL 过滤后 Top-K 返回。"""
        cur = _utc_now(now)
        all_active = self.storage.list_active_by_profile(profile_id)

        candidates: List[Memory] = []
        for m in all_active:
            if self._is_expired(m, cur):
                self.storage.update_status(m.memory_id, MemoryStatus.EXPIRED.value)
                continue
            candidates.append(m)

        scored = rank_and_select(query, candidates, limit, cur)
        result: List[Dict[str, Any]] = []
        for mem, score in scored:
            d = mem.to_dict()
            d["score"] = round(score, 4)
            result.append(d)
        return result

    @staticmethod
    def _is_expired(mem: Memory, now: datetime) -> bool:
        if mem.ttl_days is None:
            return False
        try:
            updated = datetime.fromisoformat(mem.updated_at)
        except ValueError:
            return False
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        delta_days = (now - updated).total_seconds() / 86400.0
        return delta_days > mem.ttl_days
