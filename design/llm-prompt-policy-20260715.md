# LLM Prompt 与调用策略（2026-07-15）

> 状态：现行 LLM 接入规范，2026-07-16 已核对所有生产 `_call_api` 调用点。

## 目标

系统不能用同一个模型、思考模式和 prompt 处理所有任务。通解是统一调用协议与质量门禁，同时按业务问题选择不同策略；新增 LLM 调用必须先声明 `task_kind`，业务服务继续负责自己的 schema、证据约束、token 预算和失败策略。

## 日志复盘

`logs/app.jsonl` 中历史失败主要集中在三类：

1. 长响应 JSON 截断或格式损坏，涉及测试项目、仪器清单和报告数据行。
2. 静态规则与动态文档全文放在同一 user prompt，导致请求前缀不稳定，无法充分利用 DeepSeek 自动上下文缓存。
3. 调用没有明确区分转录、抽取、分类和推理任务，模型与 thinking 参数由全局默认值间接决定。

日志不得记录文档原文或模型完整响应。只记录 `stage`、`task_kind`、模型、thinking、reasoning effort、输入长度、输出长度、token 用量、缓存命中/未命中 token、解析状态及内容哈希。

## 任务策略矩阵

| task_kind | 适用问题 | 模型 | thinking | reasoning_effort | 业务质量门禁 |
|---|---|---|---|---|---|
| `fast_transcription` | 原始记录表格逐行转录 | `deepseek-v4-flash` | disabled | - | 代码先做表格清点；LLM 行数不得少于门禁；证据可回指原文 |
| `structured_extraction` | 不固定模板的试验计划、检测报告 | `deepseek-v4-pro` | disabled | - | 严格 JSON schema；完整 JSON；关键集合数量与文档线索校验；人工核查 |
| `classification` | 标准元数据、知识块候选检索 | `deepseek-v4-flash` | disabled | - | 限定候选集；结果必须属于输入 ID；无结果走确定性回退 |
| `semantic_audit` | 测试项身份、跨文档条件/结论冲突 | `deepseek-v4-pro` | enabled | high | 只允许基于输入证据；证据必须可在输入定位；候选映射双阶段复核 |
| `knowledge_reasoning` | 标准条款拆解、适用性和限值理解 | `deepseek-v4-pro` | enabled | high | 原文逐字证据；数值/单位不推断；必须人工细致确认后发布 |

## 通用 API 约束

1. 所有生产调用设置 `response_format={"type":"json_object"}`。
2. system prompt 必须明确写出 JSON，并提供符合业务 schema 的 JSON 示例。
3. 静态角色、规则和 schema 放 system；动态文档、候选集合和查询放 user，保持稳定前缀。
4. thinking 开启时不发送 `temperature`；按任务发送 `reasoning_effort`。
5. 接口只接受完整的顶层 JSON 对象。截断、尾随文本、顶层数组和空 content 均重试；不得用补括号或截取前缀后当作完整成功结果。
6. `max_tokens` 由业务输出规模确定。测试计划使用 32768；报告按 pass 拆分，结构、仪器和逐项数据分别给足预算。
7. 无证据、低置信度或质量门禁失败时，返回“需人工确认/提取失败”，不得自动判断通过。

## Prompt 设计原则

### 不固定模板抽取

- 描述字段语义和同义标签，不假设固定 sheet、页码、编号或厂商 key。
- 集合字段明确“提取全集”，包括无编号、中文名称、厂商自定义名称和不适用项。
- 每个业务对象独立成 JSON 对象；复合文本不得塞入名称字段。
- 测试细则必须使用与总表相同的测试项身份；表格行序号不能充当测试项编号。仅当总表与细则数量相同且细则标识严格为连续 `1..N` 时，代码才允许按原表顺序恢复关联。
- 缺失字段返回空值，不得用常识补齐。
- 关键字段附带原文证据和置信度。

### 语义判断

- 先由代码生成有限候选，LLM 只判断候选关系，避免在全文中自由联想。
- 区分“同一测试项”和“条件/结果一致”；同项不代表审核通过。
- 每条问题必须给出各来源的原文证据；证据无法回指则丢弃或降为待确认。
- 高风险映射采用提议与独立复核两阶段，禁止新增第二阶段候选。

### 标准知识

- 检索只使用已发布且人工确认的标准要求。
- LLM 只解释被选中的标准原文，不把模型常识当作标准条款。
- 条款拆成适用条件、测试方法、测试条件、参数限值、判定规则和引用关系。
- 数字、比较符、单位和适用前提必须保留原文，并在标准库界面逐条人工确认。

## 当前调用点

- `test_plan_extractor.py`：`structured_extraction`
- `report_extractor.py`：`structured_extraction`，按结构/仪器/单项数据多 pass
- `raw_records_llm_extractor.py`：`fast_transcription`
- `standard_knowledge.py`：`classification`
- `standard_graph.py`：`knowledge_reasoning`
- `ai_auditor.py`、`report_driven_validator.py`、`test_item_identity_resolver.py`：`semantic_audit`
- `cross_validator.py` 旧深审兜底：`semantic_audit`

## 后续验收

每次 prompt、模型或参数变更必须在固定数据集上记录：提取对象总数、正确数、遗漏数、幻觉数、证据回指率、JSON 完整率、调用耗时和 token/缓存用量。没有人工标注真值的数据只能称为回归观察，不能声称准确率。
