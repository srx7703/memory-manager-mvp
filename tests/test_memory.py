"""MemoryManager 核心测试集(6 条)。

覆盖 profile 隔离、长期偏好召回、按实体召回、去重、TTL 软过期、冲突解决。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory_manager import MemoryManager


@pytest.fixture
def mgr(tmp_path) -> MemoryManager:
    """每个测试拿到一份干净的 SQLite 数据库。"""
    db = tmp_path / "memory.db"
    return MemoryManager(db_path=str(db))


# ---------- 1. profile 隔离 ----------

def test_profile_isolation(mgr: MemoryManager) -> None:
    mgr.add_feedback({"profile_id": "A", "message": "Anthropic 项目可以推进,约一下"})
    result = mgr.get_context("B", "Anthropic", limit=5)
    assert result == [], "profile B 不应看到 profile A 的记忆"


# ---------- 2. 长期偏好的下一轮召回 ----------

def test_long_term_preference_recalled_next_round(mgr: MemoryManager) -> None:
    mgr.add_feedback(
        {"profile_id": "A", "message": "以后少推国内大模型,我更倾向海外早期项目"}
    )
    # 再加一些杂项,确保排序不会把它挤掉
    mgr.add_feedback({"profile_id": "A", "message": "本周在路上,下周再聊"})
    mgr.add_feedback({"profile_id": "A", "message": "AI agent 赛道最近热"})

    result = mgr.get_context("A", "随便一个完全无关的 query", limit=3)
    types = [r["type"] for r in result]
    assert "long_term_preference" in types, f"长期偏好应被召回,实际 {types}"


# ---------- 3. 按实体召回 negative 反馈 ----------

def test_negative_feedback_by_entity_query(mgr: MemoryManager) -> None:
    mgr.add_feedback({"profile_id": "A", "message": "OpenAI 公司商业化弱,估值高"})
    mgr.add_feedback({"profile_id": "A", "message": "Anthropic 项目可以推进,约一下"})

    result = mgr.get_context("A", "OpenAI 公司", limit=3)
    # 期望 negative 那条在 Top-1
    assert result, "应返回至少一条"
    assert result[0]["type"] == "project_negative", (
        f"期望 project_negative,实际 {result[0]['type']}"
    )
    assert "OpenAI" in result[0]["content"]


# ---------- 4. 去重 + weight 累加 ----------

def test_dedup_no_infinite_duplicates(mgr: MemoryManager) -> None:
    msg = {"profile_id": "A", "message": "Anthropic 项目可以推进,约一下"}
    for _ in range(10):
        mgr.add_feedback(msg)

    # 直接查表确认只有 1 条 memory
    with mgr.storage.connect() as conn:
        rows = conn.execute(
            "SELECT memory_id, weight FROM memories WHERE profile_id=?",
            ("A",),
        ).fetchall()
    assert len(rows) == 1, f"期望 1 条 memory,实际 {len(rows)}"
    assert rows[0]["weight"] == 10.0, f"期望 weight=10,实际 {rows[0]['weight']}"


# ---------- 5. TTL 软过期 ----------

def test_ttl_expiration(mgr: MemoryManager) -> None:
    now0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mgr.add_feedback(
        {"profile_id": "A", "message": "本周在路上,下周再聊"},
        now=now0,
    )

    # 注入 10 天之后(temp_status 的 TTL=3 天)
    future = now0 + timedelta(days=10)
    result = mgr.get_context("A", "本周", limit=5, now=future)
    types = [r["type"] for r in result]
    assert "temp_status" not in types, f"temp_status 应已过期,实际 {types}"

    # 验证 DB 中状态确实变为 expired
    with mgr.storage.connect() as conn:
        row = conn.execute(
            "SELECT status FROM memories WHERE profile_id=? AND type=?",
            ("A", "temp_status"),
        ).fetchone()
    assert row["status"] == "expired"


# ---------- 6. 冲突解决(advance ↔ negative) ----------

def test_conflict_resolution(mgr: MemoryManager) -> None:
    mgr.add_feedback({"profile_id": "A", "message": "Anthropic 项目可以推进,约一下"})
    mgr.add_feedback({"profile_id": "A", "message": "Anthropic 商业化弱,估值高,不行"})

    with mgr.storage.connect() as conn:
        rows = conn.execute(
            "SELECT type, status FROM memories WHERE profile_id=? ORDER BY created_at",
            ("A",),
        ).fetchall()

    # 旧 advance 应该被 superseded,新 negative 是 active
    assert rows[0]["type"] == "project_advance"
    assert rows[0]["status"] == "superseded", (
        f"advance 应被 superseded,实际 {rows[0]['status']}"
    )
    assert rows[1]["type"] == "project_negative"
    assert rows[1]["status"] == "active"


# ---------- 7. created_at 被尊重 + 影响时间衰减 ----------

def test_respects_caller_provided_created_at(mgr: MemoryManager) -> None:
    """题目要求消息至少包含 created_at;此处验证它真的被尊重,且参与 recency 排序。"""
    # 同一个 profile + 同一个 query 命中度,只靠 created_at 拉开排名
    mgr.add_feedback({
        "profile_id": "A",
        "session_id": "s1",
        "message": "OpenAI 项目可以推进,约一下",
        "created_at": "2026-04-25T10:00:00Z",   # 17 天前
    })
    mgr.add_feedback({
        "profile_id": "A",
        "session_id": "s1",
        "message": "Mistral 项目可以推进,约一下",
        "created_at": "2026-05-10T10:00:00Z",   # 2 天前
    })

    # 落库的 updated_at 应等于传入的 created_at(Z 归一化为 +00:00 后)
    with mgr.storage.connect() as conn:
        rows = conn.execute(
            "SELECT content, updated_at FROM memories WHERE profile_id=? ORDER BY content",
            ("A",),
        ).fetchall()
    by_content = {r["content"]: r["updated_at"] for r in rows}
    assert by_content["Mistral 项目可以推进,约一下"] == "2026-05-10T10:00:00+00:00"
    assert by_content["OpenAI 项目可以推进,约一下"] == "2026-04-25T10:00:00+00:00"

    # 注入 query 时间为 2026-05-12,两条都未过期(<30 天),较新的 Mistral 应排在 OpenAI 之前
    query_now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    result = mgr.get_context("A", "项目 推进", limit=2, now=query_now)
    assert len(result) == 2
    contents = [r["content"] for r in result]
    assert contents[0].startswith("Mistral"), f"较新的应排第一,实际顺序 {contents}"
