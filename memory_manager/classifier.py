"""规则记忆类型分类器;留 `_llm_classify` 接口供生产替换。未命中默认 project_advance(保守策略)。"""
from __future__ import annotations

from typing import List, Tuple

from .schemas import MemoryType

# 每类的触发关键词,顺序即优先级。project_positive 要求 *不含* 推进词,在 classify() 内判定。
_RULES: List[Tuple[MemoryType, List[str]]] = [
    (MemoryType.LONG_TERM_PREFERENCE,
     ["以后", "少推", "不要", "避免", "不喜欢", "我更倾向", "我倾向", "别再"]),
    (MemoryType.PROJECT_NEGATIVE,
     ["弱", "差", "贵", "不行", "商业化", "估值高", "赛道小", "看不上"]),
    (MemoryType.PROJECT_ADVANCE,
     ["可以推进", "约一下", "见一下", "可以投", "感兴趣", "聊聊", "推进", "约个"]),
    (MemoryType.PROJECT_POSITIVE, ["不错", "有意思", "亮点", "看好"]),
    (MemoryType.MARKET_INFO, ["赛道", "趋势", "最近热", "在火", "风口"]),
    (MemoryType.TEMP_STATUS, ["本周", "下周", "在路上", "最近忙", "今天忙", "周末"]),
]

_ADVANCE_WORDS = ["可以推进", "约一下", "见一下", "可以投", "推进", "约个"]


def _hit(text: str, kws: List[str]) -> bool:
    return any(k in text for k in kws)


def classify(text: str) -> MemoryType:
    """规则分类。未命中默认 project_advance(保守策略,避免遗漏有价值反馈)。"""
    if _hit(text, _RULES[0][1]):
        return MemoryType.LONG_TERM_PREFERENCE
    if _hit(text, _RULES[1][1]):
        return MemoryType.PROJECT_NEGATIVE
    if _hit(text, _RULES[2][1]):
        return MemoryType.PROJECT_ADVANCE
    if _hit(text, _RULES[3][1]) and not _hit(text, _ADVANCE_WORDS):
        return MemoryType.PROJECT_POSITIVE
    if _hit(text, _RULES[4][1]) and "公司" not in text and "项目" not in text:
        return MemoryType.MARKET_INFO
    if _hit(text, _RULES[5][1]):
        return MemoryType.TEMP_STATUS
    return MemoryType.PROJECT_ADVANCE


def _llm_classify(text: str) -> MemoryType:
    """LLM 分类接口(mock):当前直接复用规则结果;生产环境替换为真实调用。

    TODO: 接入真实 LLM(Claude / GPT-4o),解析 JSON 形式的 type 字段。
    """
    return classify(text)
