"""数据结构与枚举(MemoryType / MemoryStatus / Memory / RawMessage)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class MemoryType(str, Enum):
    LONG_TERM_PREFERENCE = "long_term_preference"
    PROJECT_NEGATIVE = "project_negative"
    PROJECT_ADVANCE = "project_advance"
    PROJECT_POSITIVE = "project_positive"
    MARKET_INFO = "market_info"
    TEMP_STATUS = "temp_status"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"   # 被新记忆顶替(冲突解决)
    EXPIRED = "expired"         # TTL 软过期


@dataclass
class RawMessage:
    message_id: str
    profile_id: str
    session_id: Optional[str]
    message: str
    created_at: str


@dataclass
class Memory:
    memory_id: str
    profile_id: str
    raw_message_id: Optional[str]
    type: str
    content: str
    entities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    weight: float = 1.0
    ttl_days: Optional[int] = None
    status: str = MemoryStatus.ACTIVE.value

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "profile_id": self.profile_id,
            "raw_message_id": self.raw_message_id,
            "type": self.type,
            "content": self.content,
            "entities": list(self.entities),
            "keywords": list(self.keywords),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "weight": self.weight,
            "ttl_days": self.ttl_days,
            "status": self.status,
        }
