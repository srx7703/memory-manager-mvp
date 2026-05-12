"""多因子检索排序:keyword(0.4) + recency(0.2) + type(0.3) + weight(0.1)。

特殊:long_term_preference 的 keyword_match 设下限 LONG_TERM_FLOOR;
Top-K 多样性保护:若 profile 有长期偏好且未进 Top-K,强制塞入 1 条。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .config import LONG_TERM_FLOOR, RECENCY_DECAY_TAU, SCORE_WEIGHTS, TYPE_PRIORITY
from .extractor import extract_keywords
from .schemas import Memory, MemoryType


def _parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def keyword_match(query: str, mem: Memory) -> float:
    """query 与 (memory.keywords ∪ entities ∪ content 切词) 的 Jaccard 相似度。"""
    q_tokens = set(extract_keywords(query))
    mem_tokens = set(mem.keywords) | set(mem.entities) | set(extract_keywords(mem.content))
    return _jaccard(q_tokens, mem_tokens)


def recency_decay(mem: Memory, now: datetime) -> float:
    """exp(-Δdays / τ),越新越接近 1。"""
    try:
        updated = _parse_iso(mem.updated_at)
    except Exception:
        return 0.0
    delta = max(0.0, (now - updated).total_seconds() / 86400.0)
    return math.exp(-delta / RECENCY_DECAY_TAU)


def score_one(query: str, mem: Memory, now: datetime) -> float:
    km = keyword_match(query, mem)
    if mem.type == MemoryType.LONG_TERM_PREFERENCE.value:
        km = max(km, LONG_TERM_FLOOR)
    rd = recency_decay(mem, now)
    tp = TYPE_PRIORITY.get(mem.type, 0.5)
    wn = min(mem.weight / 10.0, 1.0)
    w = SCORE_WEIGHTS
    return w["keyword"] * km + w["recency"] * rd + w["type"] * tp + w["weight"] * wn


def rank_and_select(
    query: str,
    candidates: List[Memory],
    limit: int,
    now: Optional[datetime] = None,
) -> List[Tuple[Memory, float]]:
    """打分排序 + 多样性保护:若候选含长期偏好但未进 Top-K,替换末位塞入 1 条。"""
    if now is None:
        now = datetime.now(timezone.utc)

    scored: List[Tuple[Memory, float]] = [(m, score_one(query, m, now)) for m in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]

    has_long = any(m.type == MemoryType.LONG_TERM_PREFERENCE.value for m, _ in top)
    if not has_long:
        long_cands = [
            (m, s) for m, s in scored if m.type == MemoryType.LONG_TERM_PREFERENCE.value
        ]
        if long_cands and top:
            top = top[:-1] + [long_cands[0]]
            top.sort(key=lambda x: x[1], reverse=True)
        elif long_cands:
            top = [long_cands[0]]
    return top
