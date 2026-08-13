# 原始记录混合提取整改（2026-07-15）

> 状态：已实施，作为原始记录提取和质量门禁的现行设计依据。

## 问题复盘

- `OTHER` 类型曾回退到发射频率解析器，日期、文档编号、仪器型号中的连字符数字被误识别为测试数据。
- “测试照片/测试数据”曾按整页是否存在任意 `☑` 判断，导致相邻未勾选项也被标为已勾选。
- 固定正则能可靠提取表头与仪器，但无法覆盖厂商自定义的试验条件和步骤语义。

## 新流程

1. 代码提取 PDF 文本、表头、仪器和已知稳定表格。
   文件名不符合既有七段命名规则时仍创建记录对象，后续以 PDF 正文补齐身份。
2. 未知模板不再使用通用数字正则猜测数据行。
3. 每份 PDF 都调用 `deepseek-v4-flash` JSON Output 做逐表、逐行转录，避免“代码误提取但非空”绕过 LLM。
4. 文档字段使用 `source_label + semantic + source_value + normalized_value + evidence` 通用数组，不绑定厂商 key。
5. 每个 LLM 数据行必须返回原文证据；代码验证整行证据和每个原始单元格值均能在证据中定位后才接纳。
6. `pdfplumber` 独立读取 PDF 表格几何，按结果/校准日期结束标记保守清点逻辑行；独立行数与 LLM 接纳行数不一致时标记 `partial`。
7. 测试数据以证据门禁后的 LLM 逐行结果为准；仪器按“来源文件 + 序列号 + 校准日期”合并代码和 LLM 结果，保留跨文件重复使用记录。
8. LLM 调用或质量门禁失败时保留已通过证据的部分行，并明确标记 `partial`，不得伪装成完整提取。

## 日志

- `raw_record_table_inventory`: 独立表格数、可计数表数、测试行数和仪器行数。
- `raw_record_table_extract_start`: 单文件逐表提取开始，仅记录文件名、文本长度、模型和清点表数。
- `raw_record_table_extract_done`: 模型行数、证据通过/拒绝数、独立预期行数及完整性。
- `raw_record_table_extract_failed`: 记录失败文件及截断后的错误信息。
- `raw_record_hybrid_merge`: 记录目标文件数、成功数、失败数和最终数据行数。
- `ai_call` / `llm_extract_response_summary`: 记录 `thinking`、prompt/completion tokens、`prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`。

日志不写入客户正文、样品信息或完整 LLM 返回内容。

## Prompt v2 实验结论（2026-07-15）

- 使用同一组 5 份跨模板原始记录和 `deepseek-v4-flash`。
- 真实业务数据行共 29 行，模型输出 29 行，证据门禁接纳 28 行。
- ESD 的 `±4/±6/±8kV` 三行全部保留。
- 英文传导发射的 7 个测量点全部保留，并识别出单行“不符合”与总体 `PASS` 冲突。
- 唯一拒绝行是复杂接收机仪器行：型号、序列号、校准日期发生列错位。
- 仍需独立表格行数检测；模型自己声明的 `source_row_count` 不能单独证明完整性。
- 表头区环境、样品和人员等字段需要改为通用字段数组，避免示例 Schema 限制模型只返回测试项和模式。

## Prompt v2 正式接入与真实 ZIP 回归（2026-07-15）

- `HC_E202605287495` 两份原始记录的独立清点结果：测试数据分别为 4 行、1 行；仪器表分别为 4 行、4 行。
- Flash 输出与独立清点完全一致：共接纳 13 个业务行，拒绝 0 行，两份文件质量门禁均为 `complete=true`。
- 转换后的测试数据为 5 行，仪器出现记录为 8 行；同一示波器跨文件重复出现，唯一物理仪器为 7 台。
- 单文件调用耗时约 14.2 秒和 23.7 秒；完整 ZIP 并发调用约 23 秒。
- 同一匿名 `user_id` 的后续调用分别出现 `prompt_cache_hit_tokens=1536/1664`，说明稳定 system Prompt 前缀已实际命中 DeepSeek 上下文缓存。

## DeepSeek 官方能力使用

- 使用 `response_format={"type":"json_object"}`；system Prompt 明确包含 JSON 和完整输出示例。
- `max_tokens` 默认 12000，可用 `RAW_RECORD_TABLE_MAX_TOKENS` 调整，防止大表 JSON 截断。
- 表格转录属于结构化抄录而不是复杂推理，显式设置 `thinking.disabled`，避免 Flash 默认思考增加延迟。
- 稳定规则和 JSON 示例全部放在 system Prompt，动态文件正文只放 user Prompt，以复用公共前缀缓存。
- 日志记录缓存命中/未命中 token，不记录客户正文或完整模型响应。
- API 使用不可逆哈希后的审核人员标识作为 `user_id`，实现内容安全、KVCache 和调度隔离，不传员工号等隐私值。
- 应用内并发默认限制为 6，可通过 `RAW_RECORD_FLASH_CONCURRENCY` 调整；该限制是成本和本地资源保护，不直接追随供应商最大配额。
