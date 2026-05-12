"""端到端演示:展示 MemoryManager 完整生命周期。

运行:python demo.py
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from memory_manager import MemoryManager


DB_PATH = "./memory.db"
SEP = "=" * 60


def _print_block(title: str) -> None:
    print(SEP)
    print(title)
    print(SEP)


def _print_results(query: str, results: List[Dict[str, Any]]) -> None:
    print(f"[Query] {query}")
    if not results:
        print("  (no memories matched)")
        return
    for i, r in enumerate(results, 1):
        print(
            f"  #{i}  type={r['type']:<22} score={r['score']:.4f}  "
            f"weight={r['weight']:.1f}  content={r['content']}"
        )


def main() -> None:
    # 1. 干净启动
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    mgr = MemoryManager(db_path=DB_PATH)

    # 2. profile_id = "ai_investor" 加入 6 条反馈,覆盖全部 6 个类型
    _print_block("Stage 1 · 写入 ai_investor 的 6 条反馈(覆盖全部 6 类)")
    ai_feedbacks = [
        # long_term_preference
        {"profile_id": "ai_investor", "message": "以后少推国内大模型,我更倾向海外早期项目"},
        # project_negative
        {"profile_id": "ai_investor", "message": "OpenAI 估值高,商业化偏弱,看不上"},
        # project_advance
        {"profile_id": "ai_investor", "message": "Anthropic 这个项目可以推进,约一下他们 CEO"},
        # project_positive
        {"profile_id": "ai_investor", "message": "Mistral 项目有意思,模型架构有亮点"},
        # market_info
        {"profile_id": "ai_investor", "message": "AI agent 赛道最近热,趋势在起"},
        # temp_status
        {"profile_id": "ai_investor", "message": "本周在路上,下周再聊"},
    ]
    for fb in ai_feedbacks:
        added = mgr.add_feedback(fb)
        print(f"  + [{added[0]['type']:<22}] {fb['message']}")

    # 3. profile_id = "fintech_investor" 加入 2 条反馈
    _print_block("Stage 2 · 写入 fintech_investor 的 2 条反馈")
    fin_feedbacks = [
        {"profile_id": "fintech_investor", "message": "Stripe 项目可以投,感兴趣聊聊"},
        {"profile_id": "fintech_investor", "message": "支付赛道趋势已经走过最热"},
    ]
    for fb in fin_feedbacks:
        added = mgr.add_feedback(fb)
        print(f"  + [{added[0]['type']:<22}] {fb['message']}")

    # 4. 三次检索
    _print_block("Stage 3 · ai_investor 检索三次")
    for q in ("海外早期项目", "OpenAI", "AI agent 赛道"):
        r = mgr.get_context("ai_investor", q, limit=3)
        _print_results(q, r)
        print()

    # 5. profile 隔离验证:用 ai_investor 的偏好词去查 fintech_investor
    _print_block("Stage 4 · profile 隔离:在 fintech_investor 中查 OpenAI")
    r = mgr.get_context("fintech_investor", "OpenAI", limit=3)
    _print_results("OpenAI (in fintech_investor)", r)

    print()
    print(SEP)
    print(f"Demo finished. DB at: {DB_PATH}")
    print(SEP)


if __name__ == "__main__":
    main()
