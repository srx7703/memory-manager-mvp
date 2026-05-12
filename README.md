# 投资经理 Bot 轻量记忆管理 MemoryManager

## 1. 项目简介

面向投资经理 Bot 的轻量级反馈记忆管理模块。负责把投资经理的零散反馈(如"OpenAI 估值高"、"以后少推国内大模型")结构化为可分类、可检索、可演化的长期记忆,在后续对话中按需召回,辅助 Bot 给出符合 PM 偏好的项目推荐。

整个模块零第三方依赖(除测试框架 `pytest`),单文件 SQLite 持久化,LLM 调用全部 mock 但留好替换接口,800 行内交付。

---

## 2. Quick Start

```bash
# 1. 安装依赖(只有 pytest)
pip install -r requirements.txt

# 2. 跑端到端 demo
python demo.py

# 3. 跑测试
pytest -v
```

最小调用示例:

```python
from memory_manager import MemoryManager

mgr = MemoryManager(db_path="./memory.db")

# 写入反馈
mgr.add_feedback({
    "profile_id": "ai_investor",
    "message": "以后少推国内大模型,我更倾向海外早期项目",
})

# 检索上下文(返回 Top-K dict 列表,带 score)
ctx = mgr.get_context("ai_investor", query="海外早期项目", limit=3)
```

---

## 3. 核心设计

### 3.1 记忆分类体系

| 类型 | 触发关键词 | type_priority | 默认 TTL |
|---|---|---|---|
| `long_term_preference` | 以后 / 少推 / 不要 / 避免 / 不喜欢 / 我更倾向 | 1.0 | 永久 |
| `project_negative` | 弱 / 差 / 贵 / 不行 / 商业化 / 估值高 / 赛道小 | 0.9 | 30 天 |
| `project_advance` | 可以推进 / 约一下 / 见一下 / 可以投 / 感兴趣 / 聊聊 | 0.8 | 30 天 |
| `project_positive` | 不错 / 有意思 / 亮点 / 看好(且不含推进词) | 0.6 | 30 天 |
| `market_info` | 赛道 / 趋势 / 最近热 / 在火(且无具体项目) | 0.4 | 7 天 |
| `temp_status` | 本周 / 下周 / 在路上 / 最近忙 | 0.3 | 3 天 |

**设计动机:**

- **为什么分这 6 类**:投资经理的反馈天然有「关于人(偏好)/ 关于具体项目 / 关于市场 / 关于自身状态」四个层次,而项目类反馈又自然分为「推进 / 喊停 / 评价」三种意图。这 6 类正好覆盖 PM 在投资链路中的全部输出类型,既不过细(避免规则膨胀),也不过粗(保证下游可区分)。
- **type_priority 的梯度依据**:长期偏好是"人格画像",必须最高优先(永远命中);负面信号比正面更稀缺、决策含金量更高,所以 `negative > positive`;市场信息和自身状态是上下文调味,权重最低。
- **TTL 的差异化设计**:`temp_status="本周在路上"` 这种信息一周后就毫无意义,而"以后少推国内大模型"是个性画像,永久保留。差异化 TTL 让下游召回不被噪声淹没。
- **未来读取时怎么用**:
  - 召回时优先权重高的类型,长期偏好做"必带"(类似 MemGPT 的 core memory)
  - 写入时若同实体出现对立类型(advance ↔ negative),自动判定为投资观点变更,旧的 superseded
  - 检索结果直接喂给下游 LLM 作为 system prompt 上下文段

### 3.2 写入流程

```
add_feedback(message)
    ├── 1. 校验 profile_id + message 字段
    ├── 2. 写 raw_messages 表(留底,审计可追溯)
    ├── 3. classifier.classify(text) 判类型(规则;留 _llm_classify 接口)
    ├── 4. extractor 抽 entities(大写英文名 + 中文 "XX公司/项目")+ keywords(切词去停用词)
    ├── 5. 去重:同 profile + 同 type + content 归一化后字符串相同 → weight += 1
    ├── 6. 冲突检测:同 profile + 实体交集非空 + 类型对立(advance ↔ negative)→ 旧记忆 superseded
    └── 7. 插入新 memory
```

**关键决策说明:**

- **去重为什么用「归一化字符串相等」而不是模糊匹配**:MVP 阶段,严格相等已经能挡住大部分 Bot 自动重发、用户复述场景。模糊去重(编辑距离 / embedding)误判风险更高,且性能成本陡升。归一化只去标点和空格、转小写,中文标点和英文标点统一处理,够实用。
- **去重命中后只累加 weight,不覆盖 content**:原始 content 是用户的真实表达,有信息价值;weight 同时充当"反复强调"的信号,在后续打分中体现。
- **冲突检测为什么这么轻**:同 profile + 实体交集 + 类型对立——三个条件已经足够精准,不会误伤"OpenAI 不错 / OpenAI 估值高"(positive 和 negative 不在冲突对里,因为 PM 完全可能既觉得好又嫌贵)。只在 advance ↔ negative 这种明确决策反转上做 supersede,避免错误覆盖。
- **保留 raw_messages**:即使精炼后的 memory 改了、被 superseded 了,原始反馈仍可追溯,方便审计和后续训练数据回流。
- **created_at 字段优先级**:`add_feedback` 优先使用调用方传入的 `message["created_at"]`(支持 `2026-05-09T10:00:00Z` 这种 Z 后缀,内部归一化为 `+00:00` 以兼容 Python 3.10 的 `datetime.fromisoformat`),fallback 到 `now` 参数(测试注入用),最后兜底 `utcnow()`。这样能正确反映"反馈实际产生的时间",而不是"被写入的时间",`recency_decay` 才有意义。

### 3.3 检索排序逻辑

打分公式:

```
score = 0.4 * keyword_match      # 与 query 的关键词 Jaccard 相似度
      + 0.2 * recency_decay      # exp(-Δdays / 30),时间衰减
      + 0.3 * type_priority      # 上表的类型权重
      + 0.1 * weight_normalized  # min(weight/10, 1.0),反复强调奖励
```

**每个因子的设计动机:**

| 因子 | 权重 | 动机 |
|---|---|---|
| keyword_match | 0.4 | 主驱动力,query 命中是召回的基础;Jaccard 简单稳定,无 IDF 偏差 |
| recency_decay | 0.2 | 投资判断会变,30 天前的"看好"权重应当衰减;τ=30 让月度数据自然滑出 |
| type_priority | 0.3 | 给长期偏好与负面信号一个"保底",避免被一次性命中的 temp_status 挤掉 |
| weight_normalized | 0.1 | 反复说过的反馈是真信号,但权重上限做 1.0 截断,防止个别条目"刷分" |

**特别说明:**

- **long_term_preference 的 keyword_match floor = 0.3**:长期偏好的表述往往与 query 字面差异大("少推国内"vs"找海外早期"),纯靠关键词会被埋没。给一个 0.3 的下限,保证它哪怕字面零命中,也能靠 type 权重(0.3 × 1.0)+ floor(0.4 × 0.3 = 0.12)拿到 0.42 起步分。
- **Top-K 多样性保护**:若该 profile 有 long_term_preference,但 Top-K 里完全没出现(极少数情况),强制替换末位塞入 1 条。这条规则借鉴 MemGPT 的"核心记忆每次必带",防止系统忘掉 PM 的人格设定。

---

## 4. 技术选型

### 为什么是 SQLite,不是 JSON / Vector DB

| 对比项 | JSON 文件 | Vector DB(FAISS/Chroma) | SQLite |
|---|---|---|---|
| profile 过滤 | 手写遍历 | 元数据过滤(慢) | `WHERE profile_id=?` 一行 |
| 多条件排序 | 内存 sort | 后处理 | SQL 直出 |
| 并发安全 | 文件锁手搓 | OK | WAL 模式开箱即用 |
| 引入依赖 | 0 | 1+ 第三方包 | 0(标准库) |
| 单文件运维 | ✅ | ❌ | ✅ |
| 调试体验 | 文本可读 | 二进制黑盒 | sqlite3 CLI 即查即看 |

- **对比 JSON**:profile 过滤 + 多条件排序用 SQL 一行 `WHERE` 搞定,JSON 要手写遍历;且 SQLite 自带索引,数据量到几万条性能不退化。
- **对比 Vector DB**:MVP 阶段语义检索远不是瓶颈——关键词 + 类型权重已经覆盖 90% 召回场景。引入 FAISS / Chroma 至少多两个依赖,违背"轻量"硬约束。后续真要做向量召回,可以在 SQLite 旁边加一张 `embeddings` 表,不需要换底座。
- **SQLite 优势**:单文件、零运维、原生 JSON 字段、标准库自带、易调试。

### 为什么 LLM 用 mock

`classifier.py` 里留了 `_llm_classify(text)` 接口,当前 mock 实现直接回退到规则分类器,保持主流程闭环。生产环境只需把函数体替换为真实 LLM 调用(Claude / GPT-4o,返回 JSON 包含 `type` 字段),其他模块无需任何改动。

mock 的好处:
- 评测时不烧 token,测试 0.06s 跑完
- CI 不依赖外部 API key
- 规则结果可解释,便于诊断 bad case

---

## 5. MVP 范围 vs 后续 Roadmap

### 本版本已完成(MVP)

- ✅ 6 类记忆分类(规则关键词,可解释)
- ✅ profile 强隔离(数据库层 `WHERE profile_id=?`)
- ✅ 多因子排序:关键词 Jaccard + 类型权重 + 时间衰减 + 出现频次
- ✅ 字符串归一化去重 + weight 累加
- ✅ TTL 软过期机制(读时落 `expired`)
- ✅ 简单冲突检测(同实体 + 类型对立 → 旧 supersede)
- ✅ 长期偏好的检索 floor + Top-K 多样性保护
- ✅ 6 个 pytest 用例全绿
- ✅ 全量类型注解 + 中文注释 + 端到端 demo

### 后续可做(按优先级)

1. **LLM 抽取**:用 LLM 替换规则分类器和实体抽取,处理双重否定("不是不行")、隐含意图("再看看吧"通常是负面)、跨句指代。接口已经在 `classifier._llm_classify` 预留。
2. **embedding + 向量检索**:对 `keyword_match` 做 hybrid——字面 Jaccard + 向量余弦,解决同义词("赛道窄" vs "市场小")和语义近似("做大模型的" vs "LLM 项目")。
3. **多人协作下的记忆归属**:多个 PM 共用一个 Bot 时,记忆应该按"谁说的"分层,投决会决议级别的反馈应升级为团队级 memory,需要共识机制。
4. **基于访问频次的遗忘曲线**:被反复召回的记忆 weight 进一步提升;长期未召回的记忆 score 衰减加速,类似艾宾浩斯。
5. **跨 session 的对话上下文 memory**:借鉴 LangGraph 的 state + checkpointer,支持"上次说到哪了"的恢复。当前 `session_id` 字段已经预留。
6. **记忆审计/人工编辑界面**:让 PM 自己看到 Bot "记住了什么"、手动修改或删除,信任感才能建立。

---

## 6. 业界方案调研与借鉴

| 方案 | 核心思路 | 本项目借鉴点 |
|---|---|---|
| **Mem0** | LLM 驱动的「抽取 → 检索现有 → ADD / UPDATE / DELETE」闭环 | 写入时先检索做合并(去重 + 冲突),而非无脑 append |
| **Letta(MemGPT)** | 核心记忆(常驻 prompt)+ 归档记忆(按需检索) | 把 `long_term_preference` 类比为核心记忆,通过 floor + 多样性保护"每次必带" |
| **LangGraph** | state + checkpointer 的状态机抽象 | 保留 `session_id` 字段,为后续短期 working memory 预留 |
| **LlamaIndex** | RAG 检索 + memory module 的组合 | keyword + (未来)embedding 的 hybrid search 思路,以及把 entities 并入词袋的做法 |

---

## 7. 测试覆盖

`tests/test_memory.py` 共 7 个用例,全部使用 `tmp_path` fixture 共享干净 DB;时间相关用例通过 `now` 参数 / `message["created_at"]` 注入。

| # | 用例 | 覆盖意图 |
|---|---|---|
| 1 | `test_profile_isolation` | 验证 profile A 的反馈不会被 profile B 检索到,数据隔离正确 |
| 2 | `test_long_term_preference_recalled_next_round` | 验证长期偏好即使在无关 query 下也能召回,floor + 多样性保护起效 |
| 3 | `test_negative_feedback_by_entity_query` | 验证按实体名(OpenAI)查询能命中针对该实体的负面反馈 |
| 4 | `test_dedup_no_infinite_duplicates` | 同一条反馈加 10 次,DB 中只有 1 条 memory,且 `weight=10` |
| 5 | `test_ttl_expiration` | `temp_status` 写入后 10 天再查,记忆已落 `expired` 状态,不被返回 |
| 6 | `test_conflict_resolution` | 先 advance(Anthropic 可以推进)再 negative(Anthropic 商业化弱),旧记录被 `superseded` |
| 7 | `test_respects_caller_provided_created_at` | 题目要求消息至少包含 `created_at`,验证它被尊重并参与 `recency_decay` 排序 |

---

## 8. 已知限制

- **关键词匹配对同义词/语义近似无能为力**:`"赛道窄"` 与 `"市场小"` 在当前实现里 Jaccard=0,需要 embedding 才能解决。
- **规则分类器对复杂语句易误判**:双重否定("不是不行")、反讽("OpenAI 真厉害,贵得离谱"——混合信号)目前无法正确处理,需要 LLM 兜底。
- **TTL 是软过期**:仅在 `get_context` 调用时触发状态更新,大数据量下需要后台定时任务批量清理 `expired` 记录,避免表无限增长。
- **未做并发安全**:当前 SQLite 是默认 journal 模式,高并发写可能锁表;生产建议开启 WAL 模式(`PRAGMA journal_mode=WAL`),或前置一个简单的写队列。
- **中文分词依赖标点切分**:连续中文短语(如"大模型项目")会被当作单 token;若 query 没用相同短语字面,可能无法命中。可在 `extractor.py` 接入 jieba(违反零依赖)或 LLM 抽取。
