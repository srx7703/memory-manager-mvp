"""全局配置项:类型权重、TTL、打分系数、停用词等。

把所有可调参数集中在此,方便后续根据线上数据调参,不污染业务逻辑。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 各记忆类型的优先级,作为多因子排序里的 type 分量(0~1)
TYPE_PRIORITY: Dict[str, float] = {
    "long_term_preference": 1.0,
    "project_negative": 0.9,
    "project_advance": 0.8,
    "project_positive": 0.6,
    "market_info": 0.4,
    "temp_status": 0.3,
}

# 默认 TTL(天),None 表示永久保留;读时做软过期
DEFAULT_TTL_DAYS: Dict[str, Optional[int]] = {
    "long_term_preference": None,
    "project_negative": 30,
    "project_advance": 30,
    "project_positive": 30,
    "market_info": 7,
    "temp_status": 3,
}

# 检索打分权重,总和 = 1.0
SCORE_WEIGHTS: Dict[str, float] = {
    "keyword": 0.4,
    "recency": 0.2,
    "type": 0.3,
    "weight": 0.1,
}

# 时间衰减常数,exp(-Δdays / RECENCY_DECAY_TAU)
RECENCY_DECAY_TAU: int = 30

# 长期偏好类的关键词匹配下限,避免被关键词淹没
LONG_TERM_FLOOR: float = 0.3

# 停用词:分词后过滤,十几个常见词足矣
STOPWORDS = {
    "的", "了", "是", "在", "和", "也", "都", "我", "你", "他",
    "this", "the", "a", "an", "of", "to", "is", "and",
}

# 冲突对:若同 profile + 实体交集非空 + 两条记忆类型成对立组,则旧的 superseded
CONFLICT_PAIRS: List[Tuple[str, str]] = [
    ("project_advance", "project_negative"),
]
