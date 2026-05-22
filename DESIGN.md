# MemoryManager 设计文档

> **本文与 README 的分工**:`README.md` 回答"怎么用、有什么功能";本文回答"为什么这么设计、内部逻辑怎么跑、每个决策的依据是什么"。
> 适合用于:项目答辩、设计复盘、二次开发前的背景阅读。

---

## 目录

- [0. TL;DR(一分钟电梯陈述)](#0-tldr一分钟电梯陈述)
- [1. 问题与目标](#1-问题与目标)
- [2. 整体架构](#2-整体架构)
- [3. 记忆分类体系](#3-记忆分类体系)
- [4. 写入流程 add_feedback](#4-写入流程-add_feedback)
- [5. 读取流程 get_context](#5-读取流程-get_context)
- [6. 设计推导链(为什么是这样)](#6-设计推导链为什么是这样)
- [7. 技术选型与决策依据](#7-技术选型与决策依据)
- [8. 加分项实现](#8-加分项实现)
- [9. 测试覆盖](#9-测试覆盖)
- [10. 工程化](#10-工程化)
- [11. 已知限制](#11-已知限制)
- [12. 未来 Roadmap](#12-未来-roadmap)
- [13. 设计哲学总结](#13-设计哲学总结)
- [附录 A:配置项速查](#附录-a配置项速查)
- [附录 B:文件清单与代码量](#附录-b文件清单与代码量)
- [附录 C:答辩要点](#附录-c答辩要点)

---

## 0. TL;DR(一分钟电梯陈述)

`MemoryManager` 是投资经理(PM)Bot 的轻量反馈记忆模块。它要解决的根本矛盾是:**反馈信息无限增长,而 LLM 的 context 预算有限**。

整个系统本质上做两件事:

1. **写入时做"有损但智能的压缩"**——把自由文本反馈分类、抽取、去重、消解冲突,沉淀为结构化记忆;
2. **读取时做"预算约束下的相关性最大化"**——按 `profile + query` 多因子打分,在 `limit` 名额内返回最相关的记忆。

技术上:Python 3.10+、SQLite 单文件存储、**零第三方依赖**(仅 `pytest`)、LLM 全部 mock 但留好接口。核心代码 598 行,7 个测试 0.07s 全绿,CI 覆盖 Python 3.10/3.11/3.12。

---

## 1. 问题与目标

### 1.1 业务背景

投资经理用一个 Bot:它每天读市场日报、搜索并推荐项目,把推荐发到 IM 群/频道。PM 在 IM 里给反馈,例如:

```
"A 项目可以推进"             ← 推进意图
"以后少推太早期的海外项目"     ← 长期偏好
"这个公司商业化太弱"         ← 项目负反馈
```

历史日报、项目和反馈越来越多,**不可能每次都把全部历史塞进 prompt**。需要一个记忆管理器来保存、整理、读取和使用这些反馈。

### 1.2 核心矛盾

> **信息无限增长 vs context 预算有限。**

一旦接受这个矛盾是核心,后面所有设计——分类、存储、去重、排序、过期——都只是它的推论。这是理解整个项目的总钥匙。

### 1.3 对外契约(API)

题目硬约束的两个方法:

```python
add_feedback(message: dict) -> list[dict]
get_context(profile_id: str, query: str, limit: int = 5) -> list[dict]
```

输入消息格式(至少包含这 4 个字段,`profile_id` 与 `message` 必填):

```json
{
  "profile_id": "ai_security",
  "session_id": "discord_ai_security",
  "message": "以后少推太早期的海外项目",
  "created_at": "2026-05-09T10:00:00Z"
}
```

---

## 2. 整体架构

### 2.1 模块划分与依赖关系

8 个核心文件(`memory_manager/` 包,598 行),单向依赖:

```
            ┌─────────────────────┐
            │   MemoryManager     │   对外门面,只暴露 add_feedback / get_context
            │     (manager.py)    │
            └──────┬──────────────┘
                   │  组合调用
   ┌───────────────┼───────────────┬────────────────┐
   ▼               ▼               ▼                ▼
┌──────────┐  ┌──────────┐   ┌──────────┐    ┌─────────────┐
│classifier│  │extractor │   │retriever │    │   storage   │
│ 规则分类  │  │实体+关键词│   │打分+多样性│    │  SQLite 封装 │
└──────────┘  └──────────┘   └──────────┘    └──────┬──────┘
                                                    │
                                          ┌─────────┴─────────┐
                                          │  raw_messages 表   │
                                          │   memories 表      │
                                          └───────────────────┘

  ┌─────────┐   ┌────────┐
  │ schemas │   │ config │   Enum/dataclass + 所有可调参数(权重/TTL/停用词)
  └─────────┘   └────────┘
```

- **`manager`** 是唯一的门面,依赖其余所有模块;
- 其余模块互不依赖(唯一例外:`storage.find_duplicate` 内部调用 `extractor.normalize_content` 做归一化比较);
- 这种"星形 + 叶子无耦合"的结构,让任何一层都能单独替换实现(例如把规则 `classifier` 换成 LLM,其他模块零改动)。

### 2.2 数据 Schema(两张表)

**`raw_messages`(原始反馈,审计留底,永不删除)**

| 字段 | 类型 | 用途 |
|---|---|---|
| `message_id` | TEXT PK (UUID) | 唯一标识 |
| `profile_id` | TEXT, INDEXED | 谁的反馈 |
| `session_id` | TEXT nullable | 来自哪个对话(未来短期记忆用) |
| `message` | TEXT | 原始文本 |
| `created_at` | TEXT (ISO8601) | 反馈实际产生时间 |

**`memories`(精炼后的记忆,真正参与召回)**

| 字段 | 类型 | 用途 |
|---|---|---|
| `memory_id` | TEXT PK (UUID) | 唯一标识 |
| `profile_id` | TEXT, INDEXED | profile 隔离 |
| `raw_message_id` | TEXT (FK) | 溯源到原始反馈 |
| `type` | TEXT, INDEXED | 6 类之一,决定 priority 和 TTL |
| `content` | TEXT | 精炼后的记忆文本(MVP 阶段 = 原文) |
| `entities` | TEXT (JSON list) | 命名实体,如 "OpenAI" |
| `keywords` | TEXT (JSON list) | 切词后的关键词袋 |
| `created_at` / `updated_at` | TEXT (ISO8601) | 时间衰减用 |
| `weight` | REAL, 默认 1.0 | 同一反馈重复出现次数 |
| `ttl_days` | INTEGER nullable | 软过期阈值,`null` = 永久 |
| `status` | TEXT | `active` / `superseded` / `expired` |

**复合索引** `(profile_id, type, status)`:覆盖查询热路径的过滤组合,一次命中索引。

所有时间统一用 **ISO8601 UTC 字符串**,内部 `datetime.utcnow()`,持久化时 `.isoformat()`。

---

## 3. 记忆分类体系

### 3.1 六类记忆总表

| 类型 (`MemoryType`) | 触发关键词 | type_priority | 默认 TTL |
|---|---|---|---|
| `long_term_preference` | 以后/少推/不要/避免/不喜欢/我更倾向 | **1.0** | **永久 (None)** |
| `project_negative` | 弱/差/贵/不行/商业化/估值高/赛道小 | 0.9 | 30 天 |
| `project_advance` | 可以推进/约一下/见一下/可以投/感兴趣/聊聊 | 0.8 | 30 天 |
| `project_positive` | 不错/有意思/亮点/看好(且不含推进词) | 0.6 | 30 天 |
| `market_info` | 赛道/趋势/最近热/在火(且无具体项目) | 0.4 | 7 天 |
| `temp_status` | 本周/下周/在路上/最近忙 | 0.3 | 3 天 |

### 3.2 分类背后的世界观

分类的真正目的**不是归档,而是为"召回时怎么用"提前打标签**。

PM 的反馈天然分四个层次:

1. **关于人**:长期偏好(人格画像)——最重要,必须最高优先、永不过期;
2. **关于项目**:再细分为推进/喊停/评价三种意图;
3. **关于市场**:风口、趋势——上下文调味;
4. **关于自身**:本周忙、出差中——短命噪声。

`type_priority` 的梯度依据:长期偏好是人格设定(1.0);负面信号比正面更稀缺、决策含金量更高(`negative 0.9 > positive 0.6`);市场和状态权重最低。

`TTL` 的差异化依据:`temp_status="本周在路上"` 三天后毫无意义,而"以后少推国内大模型"是个性画像,永久保留。差异化 TTL 让召回不被噪声淹没。

**类型不是一个静态标签,而是编码了"这条记忆有多重要、能活多久"的元信息**——这是分类体系的核心思想。

### 3.3 兜底策略

未命中任何规则的反馈,**保守归类为 `project_advance`**——宁可保留也别丢有价值反馈。这是一个有意识的"召回优先于精确"的取舍。

---

## 4. 写入流程 add_feedback

### 4.1 七步流程

```
add_feedback(message, now=None)
 │
 ├─ 1. 校验:profile_id 和 message 必填(空 → ValueError)
 │
 ├─ 2. 解析时间戳 _resolve_ts:
 │      优先 message["created_at"] → 注入的 now → utcnow()
 │      Z 后缀归一化为 +00:00(兼容 Python 3.10 fromisoformat)
 │
 ├─ 3. 写 raw_messages 表(留底,永不删,审计可追溯)
 │
 ├─ 4. 分类 classifier.classify(text)
 │      规则关键词命中即归类;_llm_classify mock 接口预留
 │
 ├─ 5. 抽取:
 │      entities = 正则匹配大写英文名(OpenAI) + 中文 "XX公司/项目/平台/赛道"
 │      keywords = 中英文标点切词 + 停用词过滤(长度 < 2 也丢)
 │
 ├─ 6. 去重判定:
 │      归一化文本(去标点+去空白+小写)
 │      查 (profile_id, type, status=active) 是否已有同归一化文本
 │      ├─ 命中 → weight += 1, updated_at = ts, 不新增  → 返回
 │      └─ 未命中 → 继续
 │
 ├─ 7. 冲突检测 _resolve_conflicts:
 │      同 profile + 实体交集非空 + 类型对立(advance ↔ negative)
 │      → 旧记忆 status 改为 'superseded'(软删,留历史)
 │
 └─ 插入新 memory,返回 [dict]
```

### 4.2 关键决策与依据

| 决策 | 选择 | 依据 |
|---|---|---|
| **去重算法** | 归一化字符串相等,**不用模糊匹配** | 模糊去重(编辑距离/embedding)把两条不同反馈错误合并的代价,远高于偶尔留一条近似重复。MVP 阶段宁可漏判,不可误判 |
| **去重命中后** | `weight += 1`,**不覆盖原文** | 原文有信息价值;weight 顺势成为"反复强调"的强度信号,排序时用得上 |
| **冲突检测条件** | 只在 advance ↔ negative 触发 | `positive` vs `negative` **不算冲突**(PM 完全可以"OpenAI 不错但太贵"),只有明确决策反转才 supersede。宁可放过,不可错杀 |
| **冲突后处理** | 标 `superseded` 而非删除 | 软删保留完整轨迹,便于审计和未来训练数据回流 |
| **时间戳优先级** | `created_at` → `now` → `utcnow` | `created_at` 是"反馈实际产生时间"。批量导入历史 IM 时,它和写入时间可能差很远,而时间衰减必须基于前者才有意义 |

---

## 5. 读取流程 get_context

### 5.1 四步流程

```
get_context(profile_id, query, limit=5, now=None)
 │
 ├─ 1. 候选召回:
 │      SELECT * FROM memories WHERE profile_id=? AND status='active'
 │      (profile 强隔离,SQL 层就过滤掉别人的记忆)
 │
 ├─ 2. 软 TTL 过滤:
 │      对每条候选,若 ttl_days 不为 null 且 (now - updated_at) > ttl_days:
 │        UPDATE status='expired',不进候选
 │      (读时触发,免后台 cron)
 │
 ├─ 3. 多因子打分 score_one(每条候选)
 │
 └─ 4. Top-K 多样性保护:排序取前 limit;
        若候选含长期偏好但未进 Top-K,强制塞 1 条,再重排
```

### 5.2 多因子打分公式

```
score = 0.4 * keyword_match       # query 与 (keywords ∪ entities ∪ content切词) 的 Jaccard
      + 0.2 * recency_decay       # exp(-Δdays / 30)
      + 0.3 * type_priority       # 分类表里的固定值 0.3~1.0
      + 0.1 * weight_normalized   # min(weight / 10, 1.0)
```

**核心思想:相关性不是单一维度,而是多个独立维度的加权和。**

| 因子 | 权重 | 回答的问题 | 设计依据 |
|---|---|---|---|
| keyword_match | 0.4 | "字面像不像" | 召回主驱动力;Jaccard 简单稳定,无 IDF 偏差 |
| recency_decay | 0.2 | "还新不新鲜" | 投资判断会变,30 天前的"看好"该打折;τ=30 让月度数据自然滑出 |
| type_priority | 0.3 | "本身重不重要" | 给长期偏好/负面信号保底,不被一次性临时反馈挤掉 |
| weight_normalized | 0.1 | "被强调过几次" | 反复说的是真信号;上限 1.0 截断防止刷分 |

权重分配体现优先级:字面匹配最重,但**绝不让它独裁**——这正是纯关键词搜索的缺陷,用类型权重和时间衰减去平衡。

### 5.3 两个特殊规则

长期偏好有个尴尬:字面("少推国内")常和 query("找海外项目")对不上,纯关键词会被埋没。但它恰恰**最不该被忘记**——它定义了 PM 是谁。所以给两层保护:

1. **keyword_match floor = 0.3**:长期偏好的关键词匹配设下限,哪怕字面零命中,也能靠 `0.4×0.3 + 0.3×1.0 = 0.42` 起步分进入前列;
2. **Top-K 多样性保护**:排序后若长期偏好没进名额,**强制替换末位塞入 1 条**,防止 Bot "忘掉" PM 的人格设定。

这两条直接借鉴 **MemGPT 的 "core memory 常驻"**:把"人格设定"和"具体事实"区别对待——事实可被时间和相关性淘汰,人格不行。

---

## 6. 设计推导链(为什么是这样)

> 这一节把所有决策串成一条因果链。读完应该能形成"每个设计都是从上一个推导出来的"的心智模型,而不是孤立记功能点。

### 6.1 从本质出发

记忆管理的本质矛盾是**信息无限增长 vs context 有限**,所以核心任务是**有损压缩 + 智能召回**。这个矛盾决定了一切。

### 6.2 一步步推导

**① 为什么必须先分类,而不是一股脑存？**
因为反馈含金量天差地别——人格设定该长期生效,临时状态三天即弃。分类是为了在写入时一次性打好"重要性 + 寿命"的标签,让后续每次读取直接受益。

**② 存储选型,本质是判断"当前瓶颈在哪"。**
MVP 阶段最高频的操作是"按 profile 隔离 + 多条件排序",这是**结构化查询**问题,不是语义问题。所以选 SQLite(一行 WHERE + ORDER BY 搞定、零依赖、可调试),而不是向量库(解决的是我还没遇到的瓶颈,代价是重依赖)。关键是:**未来要加向量,只需旁边加一张 embeddings 表,不换底座**。

**③ 写入不是 append,是"先理解再存"。**
朴素写入会迅速积累两类垃圾:冗余(同一句说十遍)和矛盾(先推进后喊停)。所以写入每一步都在对抗它们——分类抽取把文本变结构,去重对抗冗余,冲突检测对抗矛盾。

**④ 读取是"预算内的相关性最大化"。**
约束是 `limit`(防 prompt 过长),目标是塞进最相关的。难点在于相关性是多维的,所以用四因子加权,每个因子对应一种"相关"的维度。

**⑤ 长期偏好享有特权。**
因为它最不该被忘记,但又最容易被关键词埋没。所以用 floor + 多样性保护,把人格和事实区别对待。

**⑥ mock 与接口,是"克制"的具体形态。**
LLM 全 mock 但不写空函数,`_llm_classify` 真实回落到规则,主流程随时能跑;生产只替换函数体。`session_id` 字段当前没用上但留着,为跨会话短期记忆预埋。

### 6.3 三个核心权衡(决策依据的精华)

如果只记三件事,记这三个"为什么选 A 不选 B":

1. **去重宁可漏判,不可误判**——用严格相等而非模糊匹配。错误合并的代价高于近似重复。
2. **冲突检测宁可放过,不可错杀**——只在明确决策反转上 supersede。把正常的"既欣赏又顾虑"误判成矛盾会污染记忆。
3. **技术选型看瓶颈,不看时髦**——选 SQLite 不选向量库。当前瓶颈是结构化查询,但选型不堵死未来加向量的路。

三个权衡的共同点:**都在"激进 vs 保守"之间选了保守**。因为记忆系统一旦污染,下游所有推荐都会跟着错,**错误成本是放大的**。

---

## 7. 技术选型与决策依据

### 7.1 为什么 SQLite,不是 JSON / Vector DB

| 维度 | JSON 文件 | Vector DB (FAISS/Chroma) | **SQLite** |
|---|---|---|---|
| profile 过滤 | 遍历手写 | metadata 过滤(慢) | `WHERE` 一行,有索引 |
| 多条件排序 | 内存 sort | 后处理 | SQL 直出 |
| 并发安全 | 文件锁手搓 | OK | WAL 模式开箱即用 |
| 引入依赖 | 0 | 1+ 第三方包 | 0(标准库) |
| 单文件运维 | ✅ | ❌ | ✅ |
| 调试体验 | 文本可读 | 二进制黑盒 | `sqlite3` CLI 即查即看 |

- **对比 JSON**:profile 过滤 + 多条件排序在 SQL 里一行搞定,手写遍历可读性差且无索引;
- **对比 Vector DB**:MVP 阶段语义检索不是瓶颈,关键词 + 类型权重已覆盖 90% 场景;引入向量库违背"零依赖"约束,且解决的是当前还没遇到的问题;
- **SQLite 优势**:单文件、零运维、原生 JSON 字段、标准库自带、易调试。

### 7.2 为什么 LLM 用 mock

`classifier._llm_classify(text)` 已定义,mock 实现回落到规则分类器,**主流程完整闭环、随时能跑**。生产环境只需把函数体替换为真实 LLM 调用(返回 JSON 含 `type` 字段),其他模块零改动。

mock 的好处:测试 0.07s 跑完、不烧 token、CI 不依赖 API key、规则结果可解释便于诊断。

---

## 8. 加分项实现

题目"加分项"3 条,全部完成:

| 加分项 | 实现 | 对应测试 |
|---|---|---|
| 记忆过期/降权 | TTL 软过期(`_is_expired` + 读时落 `expired`)+ weight 累加 | `test_ttl_expiration` |
| 冲突反馈处理 | advance ↔ negative supersede + 同实体匹配 | `test_conflict_resolution` |
| 业界方案调研 | README §6:Mem0 / Letta / LangGraph / LlamaIndex,逐项写借鉴点 | — |

**业界借鉴的具体落点:**

- **Mem0**(LLM 驱动的"抽取→检索现有→ADD/UPDATE/DELETE"闭环)→ 写入时先 `find_duplicate` 再决定合并还是新建,而非无脑 append;
- **Letta / MemGPT**(core memory 常驻 + archival memory 按需检索)→ 长期偏好的 floor + 多样性保护,等价于 core memory 必带;
- **LangGraph**(state + checkpointer 状态机)→ 预留 `session_id` 字段,为未来短期 working memory 留口;
- **LlamaIndex**(RAG + memory module 的 hybrid search)→ 把 entities 并入词袋,以及未来 keyword + embedding 混合检索的接口设计。

---

## 9. 测试覆盖

`tests/test_memory.py` 共 **7 个用例**(题目要求 3-5),全部用 `tmp_path` fixture 拿独立 DB;时间相关用例通过 `now` 参数 / `message["created_at"]` 注入,**完全不依赖系统钟**。全套 0.07s 跑完。

| # | 用例 | 题目要求 | 覆盖意图 |
|---|---|---|---|
| 1 | `test_profile_isolation` | ✅ 必测 | A 写入 B 取不到,DB 层隔离正确 |
| 2 | `test_long_term_preference_recalled_next_round` | ✅ 必测 | 长期偏好在无关 query 下也被召回,floor + 多样性起效 |
| 3 | `test_negative_feedback_by_entity_query` | ✅ 必测 | "OpenAI" 查询命中针对该实体的 negative 反馈 |
| 4 | `test_dedup_no_infinite_duplicates` | ✅ 必测 | 同反馈加 10 次,DB 仅 1 条 memory + weight=10 |
| 5 | `test_ttl_expiration` | 加分 | temp_status 3 天 TTL,注入 10 天后查不到且状态为 expired |
| 6 | `test_conflict_resolution` | 加分 | advance + negative 同实体,旧的 superseded |
| 7 | `test_respects_caller_provided_created_at` | 题目隐含 | `created_at` 被尊重并参与 recency 排序 |

---

## 10. 工程化

| 项 | 现状 |
|---|---|
| 核心代码量 | 598 行(`memory_manager/` 包),含 demo + 测试共 842 行 |
| 依赖 | 仅 `pytest`,零业务三方依赖 |
| 类型注解 | 全量,每个文件头 `from __future__ import annotations` |
| 注释语言 | 代码 / commit / 文档全英,README + 本文全中文 |
| 连接管理 | `storage` 层用 `@contextmanager`,保证连接关闭不泄漏 |
| 数据转换 | dataclass(`Memory` / `RawMessage`)↔ row dict 集中在 storage 层 |
| Commit 历史 | 5 个语义化 commit:scaffold → core → test+demo → docs+ci → fix |
| CI | GitHub Actions,Python 3.10/3.11/3.12 三个 matrix,跑 pytest + demo smoke test |
| 仓库 | https://github.com/srx7703/memory-manager-mvp |

**5 个 commit 的演进轨迹:**

```
f9d9a0b  chore: scaffold project layout              (.gitignore + requirements)
7a9d856  feat: implement core MemoryManager          (8 个模块一次成型)
f56cf5b  test: 6 pytest cases + end-to-end demo
67497ad  docs+ci: Chinese README + GitHub Actions
fbe8226  fix: respect caller-provided created_at      (闭合 created_at gap + 第 7 个测试)
```

---

## 11. 已知限制

诚实列出取舍边界——**知道自己的边界在哪,比假装没有边界更专业**:

- **关键词匹配对同义词/语义近似无能为力**:"赛道窄" 与 "市场小" 当前 Jaccard=0,需 embedding 才能解决;
- **规则分类器对复杂语句易误判**:双重否定("不是不行")、反讽、混合信号("真厉害,贵得离谱")无法正确处理,需 LLM 兜底;
- **TTL 是软过期**:仅在 `get_context` 调用时触发状态更新,大数据量下需后台定时任务批量清理 `expired` 行;
- **未做并发安全**:当前默认 journal 模式,高并发写可能锁表,生产建议开 WAL 模式或前置写队列;
- **中文分词依赖标点切分**:连续中文短语(如"大模型项目")被当作单 token,query 字面不一致时可能漏召回。

---

## 12. 未来 Roadmap

按优先级:

1. **LLM 抽取**:替换规则分类器和实体抽取,处理双重否定/隐含意图/跨句指代(`_llm_classify` 已留接口);
2. **embedding + 向量检索**:对 keyword_match 做 hybrid(字面 Jaccard + 向量余弦),解决同义词;
3. **多人协作下的记忆归属**:多 PM 共用 Bot 时按"谁说的"分层,投决会级别反馈升级为团队 memory,需共识机制;
4. **基于访问频次的遗忘曲线**:被反复召回的 weight 提升,长期未召回的衰减加速(类艾宾浩斯);
5. **跨 session 上下文 memory**:配合 `session_id` 做短期 working memory,借鉴 LangGraph 的 state + checkpointer;
6. **记忆审计/人工编辑界面**:让 PM 看到 Bot 记住了什么、手动修改删除,建立信任。

---

## 13. 设计哲学总结

把整个项目浓缩成三个词:**克制、可演进、可审计**。

- **克制**:每个功能都问"现在真的需要吗",598 行做完全部需求,零业务依赖;
- **可演进**:规则可换 LLM、SQLite 可加向量表、`session_id` 预留——升级路径处处留口;
- **可审计**:原始消息永久留底、冲突记忆软删不硬删、时间戳尊重来源——任何记忆的来龙去脉都查得到。

这三者不是孤立的优点,而是**同一种工程价值观在不同层面的投影**:在资源、复杂度、可信度三个约束下,做一个"现在够用、将来好改、出事能查"的系统。

---

## 附录 A:配置项速查

全部集中在 `config.py`,调参不碰业务逻辑:

```python
TYPE_PRIORITY = {              # 排序里的 type 分量
    "long_term_preference": 1.0,
    "project_negative": 0.9,
    "project_advance": 0.8,
    "project_positive": 0.6,
    "market_info": 0.4,
    "temp_status": 0.3,
}

DEFAULT_TTL_DAYS = {           # None = 永久
    "long_term_preference": None,
    "project_negative": 30,
    "project_advance": 30,
    "project_positive": 30,
    "market_info": 7,
    "temp_status": 3,
}

SCORE_WEIGHTS = {"keyword": 0.4, "recency": 0.2, "type": 0.3, "weight": 0.1}
RECENCY_DECAY_TAU = 30         # 时间衰减常数 exp(-Δdays / τ)
LONG_TERM_FLOOR = 0.3          # 长期偏好的 keyword_match 下限
STOPWORDS = {"的", "了", "是", ...}   # 分词过滤
CONFLICT_PAIRS = [("project_advance", "project_negative")]   # 冲突对
```

---

## 附录 B:文件清单与代码量

```
memory_manager_project/
├── README.md                       使用文档(中文)
├── DESIGN.md                       本文:设计与逻辑详解
├── requirements.txt                仅 pytest
├── demo.py                         端到端演示              (90 行)
├── .gitignore
├── .github/workflows/ci.yml        CI:3.10/3.11/3.12 跑 pytest + demo
├── memory_manager/                 ← 核心包,共 598 行
│   ├── __init__.py                 包入口/导出              (16 行)
│   ├── manager.py                  MemoryManager 门面       (152 行)
│   ├── classifier.py               规则分类 + LLM mock 接口  (50 行)
│   ├── extractor.py                实体/关键词/归一化        (50 行)
│   ├── retriever.py                多因子打分 + 多样性保护    (81 行)
│   ├── storage.py                  SQLite 封装              (135 行)
│   ├── schemas.py                  Enum + dataclass         (62 行)
│   └── config.py                   全部可调参数             (52 行)
└── tests/
    ├── __init__.py
    └── test_memory.py              7 个 pytest 用例         (154 行)
```

---

## 附录 C:答辩要点

被问"这个项目最值得讲的是什么",可以这样回答:

> **三点:**
>
> **1. 设计哲学** —— 用最简单的规则 + SQLite 做出能用的 v0,但每个关键路径都留了升级口子(LLM 分类、向量检索、共识机制)。MVP 不是糙,而是**克制**。
>
> **2. 业界经验内化** —— Mem0 的写时合并、MemGPT 的 core memory、LangGraph 的 state,都不是抄,而是把它们的核心抽象映射到这个具体问题:长期偏好 floor、多样性保护、session_id 预留。
>
> **3. 工程素养可量化** —— 零三方依赖、600 行内核心、CI 三套 Python、5 个语义化 commit、7 个测试 0.07s 全绿。每一项都对应一个工程价值观:轻量、可维护、可演进、可审计。

常见追问与应答:

- **"为什么不直接用 LLM 分类?"** → MVP 阶段规则可解释、零成本、可测试;LLM 接口已留(`_llm_classify`),生产可无缝切换。先证明流程闭环,再上重武器。
- **"去重为什么不用语义相似?"** → 误判代价高于漏判。错误合并两条不同反馈会污染记忆,而记忆污染会被下游推荐放大。MVP 阶段选保守。
- **"TTL 软过期会不会让表无限增长?"** → 会,这是已知限制(见 §11),生产需补后台定时清理。当前选软过期是为了免去 cron 的运维成本。
- **"profile 隔离怎么保证?"** → 数据库层 `WHERE profile_id=?`,不是应用层过滤,从源头杜绝串号;且有 `(profile_id, type, status)` 复合索引保证性能。
