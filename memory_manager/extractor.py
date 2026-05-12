"""零依赖实体抽取与关键词分词;后续可替换为 LLM 抽取,接口不变。"""
from __future__ import annotations

import re
from typing import List

from .config import STOPWORDS

# 大写开头的英文名(OpenAI、Anthropic 等)
_EN_NAME_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]{1,30}\b")
# 中文模式:XX 公司 / XX 项目 / XX 平台 / XX 赛道
_CN_ENTITY_RE = re.compile(r"([一-龥A-Za-z0-9]{1,15})(?:公司|项目|平台|赛道)")
# 中英文标点 + 空白通用切词分隔符
_SPLIT_RE = re.compile(r"[\s,,。.!!??;;::、()()\[\]【】\"'`~\-/\\]+")


def extract_entities(text: str) -> List[str]:
    """抽取实体:大写英文名 + 中文 'XX公司/项目/平台/赛道' 前缀。去重保序。"""
    out: List[str] = []
    seen: set = set()
    for m in _CN_ENTITY_RE.finditer(text):
        token = m.group(1).strip()
        if token and token.lower() not in STOPWORDS and token not in seen:
            seen.add(token)
            out.append(token)
    for m in _EN_NAME_RE.finditer(text):
        token = m.group(0)
        if len(token) < 2 or token.lower() in STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def extract_keywords(text: str) -> List[str]:
    """按中英文标点和空白粗切,过滤停用词与短 token(<2)。"""
    out: List[str] = []
    seen: set = set()
    for tok in _SPLIT_RE.split(text):
        tok = tok.strip()
        if len(tok) < 2 or tok.lower() in STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def normalize_content(text: str) -> str:
    """归一化字符串(去标点 + 去空白 + 小写),仅用于去重比较。"""
    return _SPLIT_RE.sub("", text).lower()
