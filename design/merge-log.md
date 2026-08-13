# Merge Log

## 2026-08-13 — V2 Consolidation and Durable Evidence Anchors

- Removed the retired V1 frontend and temporary shadow/demo prototypes; `frontend-v2` is now the only maintained UI and only deployment target.
- Removed fake-data fallbacks, the obsolete extraction-review stage, dead extraction API/types and unused V2 state modules. The active flow is now intake, machine review, finding decision and completion.
- Kept extraction as a background operation with live query refresh; partial quality signals proceed into review as actionable findings instead of forcing a separate field-by-field checkpoint.
- Updated local startup, setup, CORS and container definitions for the single V2 frontend. Local vision startup remains asynchronous and cannot tear down the API while the model is loading.
- Added a version-scoped ZIP member manifest to the existing database. Archive members use index plus content hash as machine identity, while repaired UTF-8 names are presentation metadata; historical graphs remain bound to their original document version.
- Added a unified evidence-anchor finalizer for direct finding evidence. It persists only uniquely confirmed structured rows, semantic table rows, strict contiguous text or parameter rows, and leaves ambiguous evidence at page level instead of drawing guessed boxes.
- Added non-destructive lazy backfill for historical evidence anchors and reusable preview ETags/cache entries.
- Updated project documentation, repository hygiene tests and ignore rules while preserving the real database, logs, sample sets and preview caches.
- Verification: backend `476 passed, 13 skipped`; V2 unit tests `12 passed`; V2 production build and TypeScript unused-symbol checks passed; production dependency audit found zero vulnerabilities; shell syntax, YAML parsing and `git diff --check` passed. A real V2 rerun of `EMC-20260810-4q39sm` completed 210 units with zero failed units and persisted 229/328 direct-evidence coordinates; the reported conflict retained 3/3 anchored evidence items, member 41 displayed its correct Chinese name, and repeated preview access reused the 304 cache path.

## 2026-08-11 — Aggregated Bug Review and Traceability Fixes

- Poll document-set and extraction-result queries only while a document is in an active extraction state, so background completion appears without a manual refresh.
- Expose detected standard references in the V2 scope dialog and persist a reviewer-entered skip reason when no matching published standard is selected.
- Return saved extraction text to the review UI and use it as the traceable preview for Office and ZIP sources that browsers cannot embed.
- Recover legacy GBK ZIP member names before original-record metadata is structured; member lookup continues to use the untouched archive key.
- Present persisted check states using anchored versus expected evidence counts, preserving valid exhaustive-scan passes with zero positive findings.
- Correct V2 responsive navigation, source-file controls, mixed-script status labels, long standard requirements, custom disabled states, and the test-plan `.xls` format hint.
- Align completion statistics with the persisted `false_positive` decision code; reject unsupported historical decision codes instead of dereferencing a missing option.
- Route locked or actively running tasks to the machine-review stage from both dashboard and task cards; preserve per-document version history when another history request fails.
- Render unknown run progress as an indeterminate animation, remove the 250-field extraction-view truncation, abort obsolete source downloads, and use layout-stable demo ruler marks.
- Keep live rate-limit setting reads off the FastAPI event loop while retaining the cross-thread in-memory lock; database cancellation now waits for the bounded SQLite operation to release its connection rather than detaching a worker write.
- Log database-backed configuration fallback using only the setting key and exception type. Parse raw-record filenames by stable structural anchors so item/operator underscores are retained, and sort arbitrarily long numeric sequences without integer conversion.
- Verification: backend `424 passed, 13 skipped`; V2 unit tests `8 passed`; V2 and primary frontend production builds passed; local Chromium checks passed at 1100 px and 700 px without console errors or horizontal overflow. Locked tasks opened `/run`, the unknown-progress animation was active, and ruler marks used flex layout.

## 2026-07-17 — Full Code and Documentation Audit Remediation

- Repaired document-set unlock/delete contracts and added transactional cleanup of every set-scoped artifact.
- Scoped statistics and project groups by user, enforced group membership/ownership, and restricted audit-log access to administrators.
- Protected published standards from physical deletion and aligned standard writes with the standard-reviewer role.
- Made upload limits, rate limits, batch exports and the DeepSeek endpoint honor validated runtime settings.
- Made SSE failures terminal and replayable, registered long-running standard jobs, and consolidated startup into one FastAPI lifespan.
- Removed the inactive custom-rule UI and legacy admin report page; all visible admin statistics and issue lists now use document-set data.
- Migrated PDF reading to `pypdf`, added `.env.example`, and split frontend/admin routes into production chunks below Vite's advisory limit.
- Added audit-specific regression tests for permissions, cleanup, settings, statistics, standard deletion and progress termination.
- Verification: backend `414 passed, 5 skipped`; reviewer and admin production builds passed; both npm production audits reported zero vulnerabilities; SQLite integrity and foreign-key checks passed; application lifespan startup/shutdown smoke passed.

## 2026-07-16 — Real Review Progress Timing

- Replaced the frontend's fixed per-step duration formula with backend wall-clock timing based on a monotonic clock.
- Added `duration_ms` for completed stages and cumulative `elapsed_ms` to authenticated SSE progress events.
- Render each step from its explicit `active` or `done` event so completing one step no longer marks the next step active prematurely.
- Measure concurrent module review from the first module start until the final module completes; browsers without stream support show a generic running state instead of simulated progress.
- Added privacy-safe `pipeline_progress_step_done` logs and regression coverage for active/done and done-only stages.

## 2026-07-16 — Raw-Record Column Geometry Reconciliation

- Re-rendered the source PDF for `EMC-20260716-0abp8n` and confirmed the
  voltage-fluctuation row contains required performance `1)` and actual
  performance `2)` in separate physical columns.
- Found that Flash transcription merged both footnotes into the required column
  while the row-count-only quality gate still marked extraction complete.
- Extended the independent PDF table inventory with row-ending cell geometry.
- Added deterministic reconciliation for short atomic fields such as required
  performance, actual performance and verdict, without changing narrative or
  multi-line specification cells.
- Added correction-count observability and regression coverage for adjacent
  column shifts. Replaying the cached model payload against the source PDF now
  yields required `1)`, actual `2)`, verdict `符合`, with quality complete.

## 2026-07-16 — Composite Condition Separator Normalization

- Confirmed from run `92c58cd0cce2` that `EMC-20260716-0abp8n` incorrectly
  reported `25.1℃ 53% 100.4kPa` versus `25.1℃/53%RH/100.4kPa` as a conflict.
- The LLM extraction and evidence were correct; deterministic normalization
  retained report slashes and produced two different comparison strings.
- Treat slash and vertical-bar characters as separators when at least one
  adjacent side is non-numeric, while preserving numeric ratios such as `1/2`.
- Added regression coverage for equivalent environmental conditions and ratio
  preservation.

## 2026-07-16 — Full Code Audit Remediation

### Correctness and state

- Made review claiming and completion atomic so duplicate submissions cannot run concurrently or overwrite state.
- Replaced issue rows on every run, including clean runs, so old findings cannot survive a zero-issue re-review.
- Recomputed check totals from the actual executed check IDs after report-driven and retained legacy checks are merged.
- Kept long-running review progress streams alive and restored failed reviews to a revision state.

### Extraction quality

- Fixed the inverted raw-record table gate: emission records require data rows; immunity records do not.
- Removed duplicate empty-PDF failures and persisted test-plan/raw-record partial extraction details in structured data.
- Converted partial extraction into an explicit system issue so incomplete evidence cannot produce a silent clean result.

### Security and resilience

- Scoped child document, diff, override and annotation access to the owning document set and enforced replacement ownership in the database transaction.
- Required authentication for review progress and moved the frontend stream from query-string JWTs to an Authorization header.
- Added incremental upload limits and ZIP count, size, ratio, traversal, encryption and symlink checks.
- Enforced a non-default JWT secret in production; password-based authentication remains deferred to deployment preparation.
- Updated Axios, DOMPurify and Vite dependencies; frontend and admin audits report zero vulnerabilities.

### Observability

- Persist logs in development and production, log unhandled requests, and add `run_id` correlation.
- Log document quality inputs, every check outcome, issue replacement counts, state conflicts and upload rejection reasons.
- Added regression coverage in `backend/tests/test_audit_regressions.py`.

### Verification

- Backend: `389 passed, 5 skipped`.
- Reviewer frontend and admin frontend: production builds passed with Vite 6.4.3.
- Reviewer frontend and admin frontend: `npm audit --omit=dev` reports zero vulnerabilities.

## 2026-07-16 — Repository and Documentation Consolidation

### Changes

- Added root `README.md` as the single project, architecture, startup and validation entry point.
- Reduced `CLAUDE.md` to a pointer and refreshed `AGENTS.md` against the report-driven pipeline.
- Added `design/README.md` with current/historical status and moved all UX HTML previews to `design/previews/`.
- Kept `example/` data ignored while allowing `example/README.md` to be versioned.
- Removed the accidental Node manifest and generated `node_modules` from the Python backend.
- Removed the obsolete standalone comparison tool that still performed retired format and data-point checks.
- Added repository hygiene tests for documentation links, preview placement and LLM call policy.

### Safety

- Preserved the active SQLite database, logs and all example datasets; SQLite WAL/SHM sidecars remain runtime-only files.
- Removed four confirmed empty SQLite files created from incorrect working directories; retained the populated `backend/review.db`.
- Added ignore rules for `tmp/`, `*.out`, broken virtual environments and Python test/type-check caches.

### Verification

- Backend: `382 passed, 5 skipped`; collection-warning noise reduced from 24 warnings to 4 dependency deprecations.
- Reviewer frontend: production build completed (`803` modules).
- Admin frontend: production build completed (`802` modules).
- `bash -n start.sh deploy.sh deploy-remote.sh setup.sh` passed.
- `git diff --check` passed.

## 2026-07-15 — DeepSeek Task Policies and Extraction Quality Gates

### Changes

- Enabled strict DeepSeek JSON Output for every production LLM call.
- Added task-specific model policies: Flash for bounded transcription/classification; Pro for variable-document extraction; Pro high-thinking for semantic and standards reasoning.
- Split stable system instructions from dynamic user evidence to improve automatic context-cache reuse.
- Reject and retry empty, truncated or trailing-text JSON rather than repairing a partial response into a false success.
- Increased report/test-plan output budgets according to each pass and added cache/token/parse metrics without logging customer text.
- Added raw-record table inventory gates, evidence validation and standard graph human confirmation.
- Fixed vendor plan detail rows whose numeric sequence identifiers could not link back to semantic test-item names.

### Real sample verification

- `大冶-灯具准入实验标准（新.xlsx）`: 35 test items, 35 detail records, 35 non-empty detail records.
- `HC_E202605287495` raw records: 2 PDFs, 5 test rows, 8 instrument occurrences, 7 unique instruments.

## 2026-07-10 — Test Plan Extraction Compatibility

### Context

- Affected document sets:
  - `EMC-20260709-apjf8u`
  - `EMC-20260709-ttr6my`
- Affected file:
  - `大冶-灯具准入实验标准（新.xlsx`

### Problems

- Vendor test plans may have sparse basic information but valid test item tables.
  The previous extractor treated sparse basic information as a hard failure.
- DeepSeek may return spreadsheet row numbers as numeric JSON values, for example
  `"编号": 1`, while `TestPlanItem.code` expects a string.
- Some Chinese test-plan fields were mapped to names not present in
  `TestPlanItem`, causing `是否执行`, `测试模式`, `认可依据`, and `验收标准` to be
  dropped during Pydantic validation.

### Changes

- Treat sparse basic information as non-fatal when test items or test details
  are present.
- Keep rejecting truly empty extractions with sparse basic information and no
  plan content.
- Ignore empty test item codes during EQ-code completeness validation.
- Normalize LLM scalar/list/dict values to strings before model validation.
- Map Chinese item fields to the actual `TestPlanItem` model fields.
- If a vendor plan uses a numeric row sequence as `编号`, use the semantic test
  item name as the stable item identifier.
- Added extraction debug logging requirements in
  `design/extraction-debug-logging.md`.

### Verification

- `python -m pytest backend/tests/test_test_plan.py backend/tests/test_report_driven_validator.py -q`
- `RUN_ONLINE_CASES=1 python -m pytest backend/tests/test_online_cases_corpus.py -q`
- `python -m py_compile backend/services/test_plan_extractor.py`
# 2026-07-16 审核步骤真实进度与耗时

- 修复审核步骤完成耗时由前端固定公式伪造的问题；步骤耗时现由后端基于单调时钟计算并通过 SSE 返回。
- SSE 进度事件新增 `duration_ms`（完成步骤墙钟耗时）和 `elapsed_ms`（本轮审核累计耗时）。
- 前端按每一步真实 `active` / `done` 事件渲染，不再在上一步完成时提前点亮下一步。
- 并发模块审核从该步骤首次启动计时，到最后一个模块完成为止；不支持流式响应的旧浏览器只展示笼统执行状态，不模拟步骤或耗时。
- 新增 `pipeline_progress_step_done` 结构化日志，不记录客户原文、步骤标签或模型内容。

# 2026-07-16 四文件问题证据矩阵

- 正式审核结果页和“我的报告”详情弹窗统一固定展示委托单、试验计划、原始记录、检测报告四列，不再保留两套不同的问题列表，也不再使用仅适合两份来源的旧对比卡片。
- 每份文件统一标记为冲突、一致、有证据、缺失或不参与，折叠状态可快速浏览，展开后展示文件名、原值、提取位置与溯源入口。
- 原始记录列展示 ZIP 内逐份 PDF 的语义名称、序号、模式以及“相关/已检索”状态，避免只显示压缩包名称而无法确认来源文件。
- 问题列表增加文件关系、问题类别和全文搜索筛选，并保留人工确认、忽略及打开对应核查弹窗的操作。
- 历史问题在读取接口中动态补充四文件证据结构，无需重新提交审核或迁移数据库。
- 新增证据矩阵回归测试，覆盖四文件多数一致、单文件冲突、缺失来源及原始记录多文件清单。
- 验证：后端 `396 passed, 5 skipped`；审核端与管理端生产构建通过；以 `EMC-20260716-0abp8n` 的 33 条历史问题完成正式页面折叠、展开和筛选验收。
- 结果可读性优化：问题类别和检查规则统一显示中文业务名称，内部 `category`/`check_id` 仅保留在悬浮提示中；列表、文件状态、证据原文及人工状态字号整体提升。

# 2026-07-16 运行观测中文化与耗时口径修复

- 运行观测中的内部阶段、模块和状态代码统一映射为中文业务名称，不再直接展示 `pipeline_done`、`module_llm_audit`、`partial` 或 `NAME:` 前缀。
- “累计耗时”更名为“实际总耗时”，使用整轮审核的墙钟时间；父阶段、子阶段和并行模块耗时不再重复相加。
- 后端历史指标查询同步采用整轮审核指标，旧数据在缺少整轮指标时回退为最长阶段耗时，无需迁移数据库。
- 界面新增并行阶段耗时说明，并使用“分/秒/毫秒”展示耗时。
- 新增回归测试，覆盖父子阶段与并行模块不得求和的场景。
# 2026-07-16 标准知识库工业化升级

- 标准要求拆分为不可编辑原文与需人工确认的中文释义，标准审核权限独立为 `standard_reviewer`。
- 每次发布创建不可变 `R1/R2/...` 快照，文档集绑定具体发布版本，历史审核不受后续重建影响。
- 增加本地 BGE-M3 向量索引、词法候选、LLM 复核和低置信度拒答门禁，删除任意首块兜底。
- 增加当前文档集、项目组、标准全局三种人工映射范围及“不覆盖”结论；项目组选择持久化到文档集，检索优先级为当前文档集、项目组、标准全局。
- 增加四文档显式标准引用清单；未选择对应标准且未填写跳过原因时禁止锁定提交。
- 标准审核界面改为原文/中文释义双语核对，问题卡可直接建立人工标准映射。
- 验证：后端 `403 passed, 5 skipped`，前台和后台构建通过；本机 BGE-M3/MPS 实测输出 1024 维向量。

# 2026-07-17 Audit V2 影子审核基础闭环

- 新增独立 V2 数据契约、SQLite 持久化、原子快照和中断恢复；不写入 V1 正式问题表。
- 新增文档统一切分、原生结构/视觉双路提取、视觉仲裁、实体身份双轮复核和完整覆盖下的缺失证明。
- 新增四来源事实矩阵、语义差异发现/反证、客观运算规划/代码执行和人工发布标准知识双轮审核。
- 新增可审计模型网关；生产 `LOCAL_ONLY` 禁止云端兜底，视觉任务在任何模式下都禁止降级为纯文本云模型。
- 新增完整 V2 影子启动、状态、取消、人工标注 API，以及审核端独立影子工作区。
- 影子工作区同时接入上传结果页和“我的报告”历史详情；修复历史详情正文不可滚动、影子面板被 flex 压缩裁剪的问题。
- 每 5 个文档单元和主要阶段持久化快照；模型能力缺失时启动前返回 503，不创建半截运行。
- 应用不再因缺少 DeepSeek Key 在导入阶段崩溃；仅实际调用官方云端时校验密钥，本地 OpenAI 兼容端点可无云端密钥运行。
- 验证：后端全量 `444 passed, 5 skipped`；审核端和管理端生产构建通过；新增密钥边界回归后 V2/网关针对性测试 `16 passed`。
- 尚未执行真实文件完整 V2 影子回归：开发机 `.env` 还未配置 PaddleOCR-VL、Qwen3-VL 和本地文本模型服务端点。
- 浏览器验收：历史文档集可见并可操作 V2 面板；模型缺失返回中文 503 提示，数据库未创建半截轮次。

# 2026-07-17 PaddleOCR-VL 完整布局协议修复

- 修复 V2 将 PaddleOCR-VL 完整流水线误当作 OpenAI Chat API 的协议问题。
- 新增专用 `/layout-parsing` 客户端，严格解析 `parsing_res_list` 并规范化布局块、阅读顺序和 bbox。
- 默认协议为 `paddle_pipeline`；`openai_chat` 仅作为显式兼容配置，布局失败不会回退云端。
- 日志仅保留图片哈希、大小、块数量、状态与耗时，不记录客户图片或 OCR 原文。
- 针对性回归：`25 passed`。

# 2026-07-20 Audit V2 真实回归可靠性整改

- Excel 视觉副本改为按宽度适配页面，真实试验计划从 30 个横向碎片页收敛为 9 个完整页面；原始文件和原生结构提取不变。
- 新增内容寻址提取缓存，缓存键绑定源内容、页面图像、模型清单、提示词和 schema；只复用无错误的已同意/已仲裁制品。
- 测试项集合改用保留事实 `test_item_presence`。计划、原始记录和报告中的项目名称必须有逐字原文证据；条件、方法、结果与结论不能冒充测试项。
- 聚合状态机将完整检索后的“有证据/确认缺失”直接生成差异，没有跨文档职责的单文件元数据不再生成伪比较。
- 缺失反证和语义差异响应改为逐项 schema 门禁，单条模型格式漂移只影响本条，不能使整批结论消失。
- 实体身份批次增加耗时和接受数量日志；后台运行日志自动绑定 `set_id/run_id`，不记录客户正文。
- 同一真实文件复测轮次 `v2-c887dc914cd049cc` 已完成：试验计划 9 页共得到 35 条测试项目出现事实，旧版第 2 页漏掉的 3 项已恢复；整轮形成 196 条事实、293 条证据和 120 个比较维度。结果仍为 `machine_incomplete`，且发现条件变体被当作测试项候选，不能直接作为人工金标结论。
- 增加测试项目层级双模型复核。Flash 与 Pro 独立确认候选角色，双方确认的条件/变体保留证据但不参与跨文档比较；任何分歧、证据或 schema 问题均转人工复核。
- V2 运行诊断写入 `audit_v2_runs.diagnostics`；详情返回完整诊断，列表返回诊断数量，解决快照保存后失败原因丢失的问题。
- 验证：后端 `521 passed, 5 skipped`；审核端 747 modules 和管理端 742 modules 的生产构建通过，Python `compileall` 与 `git diff --check` 通过。整改后真实轮次为 `v2-0e6b415a3acb4de6`，最终业务质量仍需以完整结果和人工金标复核为准。

# 2026-07-21 试验计划全集与身份覆盖整改

- Excel 试验计划增加工作簿原生整表清点：代码枚举非空行和精确单元格，Flash/Pro 独立逐行分类；模型必须覆盖全部行且测试项名称只能来自原单元格。
- 页面测试项角色与整表结论冲突时关闭覆盖门禁；独立编号并带方法、执行或验收要求的脉冲子试验按独立测试项复核，纯参数变体继续排除。
- 完全同名的跨文档实体可作为厂商别名的身份锚点；同文档重复分页仅在独立验证的重叠证据链下传递合并。
- 身份提议和反证新增 `reviewed_entity_ids` 全集门禁，禁止模型在大批次中静默跳过难匹配实体。
- 有效诊断轮次 `v2-3f7da72cbc694ae9` 完整结束并暴露两条计划角色冲突和电压波动别名漏审；后续复验 `v2-2d263e5aa4f947e6` 因 DeepSeek `402 Insufficient Balance` 不具备准确率评价价值。
- 验证：后端 `535 passed, 5 skipped`；审核端 747 modules、管理端 742 modules 的生产构建通过。真实模型最终验收等待 DeepSeek 余额恢复。

# 2026-07-21 身份分块与充值后真实验收

- 整表双模型一致结论可恢复页面角色为 `unresolved` 的同名事实，并清除旧排除标记；页面双审明确判为条件或非项目时仍保持人工冲突。
- 超过 30 个同类型实体时，身份审核按文档类型两两分块；小组仍保留一次性多来源上下文。每个跨文档候选对至少共同出现一次，提议与反证仍须返回完整 `reviewed_entity_ids`。
- 最终轮次 `v2-eb099f3dd5b646b7` 完整覆盖 33 个页面单元，计划工作簿 44 行确认 35 项、0 未决；P2a/P3a 为独立项目，电压波动三来源归并为同一实体。
- 最终产生 204 条事实、344 条证据和 112 个比较维度：65 条差异、11 条等价、36 条未确定。无模型调用或 schema 门禁失败；未选择标准，因此标准条款审查按规则跳过。
- 验证：后端 `537 passed, 5 skipped`；Python `compileall` 与 `git diff --check` 通过。V2 准确率仍须对最终差异和诊断完成人工金标复核后确定。

# 2026-07-23 V2 占位符事实门禁

- 修复原始记录“委托单位：/”被当作真实客户名称，继而对试验计划执行缺失检索并生成 `/ · client` 差异的问题。
- 新增统一占位符识别，支持标量、列表和对象的全量无内容判断；混合真实内容的集合不会被误排除，数值 `0` 仍是有效事实。
- 占位符事实保留原文证据并记录排除原因，但不进入身份、字段、计算、标准或跨文档差异流程；聚合矩阵增加独立最终门禁。
- 新增三组回归测试，覆盖重复 `/`、真实值混合列表、数值与聚合旁路；后端全量验证为 `540 passed, 5 skipped`，Python `compileall` 与 `git diff --check` 通过。

# 2026-07-23 V2 结果完整展示与筛选分页

- 删除影子审核结果固定 `slice(0, 30)` 截断，禁止在无提示情况下隐藏第 31 条之后的差异。
- 新增全部、确定差异、待人工确认和测试项覆盖统计入口；增加问题类别、机器结论、人工状态、参与文件及关键词组合筛选。
- 结果按 20/50/100 条分页，始终显示完整总数、筛选后数量、当前范围和页码；人工状态由后端持久化，不受筛选和翻页影响。
- 真实轮次 `v2-eb099f3dd5b646b7` 浏览器验收：完整显示 101 条；测试项覆盖筛选为 36 条，第一页 1–20、第二页 21–36；前端 747 modules 生产构建通过。

# 2026-07-23 V2 影子审核问题一键导出

- 影子审核标题栏新增“导出问题 Excel”，导出当前选择轮次的全部确定差异和待人工确认项，不受页面筛选及分页影响。
- 新增轮次级下载接口，严格校验文档集所有权、轮次归属和完成状态；运行中、失败或中断的半成品轮次拒绝导出。
- Excel 主表使用中文业务名称，包含比较对象、字段、规则、机器判断、模型依据、四文档与测试标准的取值及原文位置、人工结论和追溯编号；附表记录轮次与数量摘要。
- 同一类型存在多份原始记录时合并全部取值和证据，不再按文档类型覆盖丢失；加入控制字符、超长单元格和公式注入防护。
- 新增生成器、API、轮次串用、未完成轮次和取消任务回归测试。后端全量 `545 passed, 5 skipped`，前端 747 modules 生产构建通过；真实轮次 `v2-eb099f3dd5b646b7` 成功导出 101 条问题。

# 2026-07-28 统一证据图正式切换

- 正式审核收敛为测试计划驱动的单一证据图：计划要求、原始执行、报告发布形成三段覆盖，父子项、别名、样品/模式/轮次通过带证据关系核验。
- 原生解析优先；扫描页、复杂表格和定向反证使用千问视觉，DeepSeek负责语义候选与独立反驳。Paddle、Tesseract、V1/V2 路由与服务全部退出生产代码。
- P1/P2a/P2b/P3a 的三来源回归形成 4 条 `confirmed_pass` 和 8 条覆盖边，不再因父项/子项或视觉漏行产生“未执行”误报。
- 标准只提供已人工确认发布的参考要求，不扩大测试计划范围；外文标准的中文释义增加模型输出和人工确认双重中文门禁。
- 审核端与管理端合并为统一工作台，支持任务树、问题、关系诊断、人工裁决、标准库、用户权限和运维日志；失败运行保留图诊断并将任务恢复为可重试锁定态。
- 当前库从空库重建，旧数据库、日志和 V2 制品归档到 `backups/pre-unified-20260728-121845/` 并完成 SQLite 与 SHA-256 校验。
- 最终验证：后端 `281 passed, 2 skipped`；前端 747 模块生产构建；npm 生产依赖 0 漏洞；Python 编译、Shell 语法、SQLite 外键/完整性、`git diff --check`、前后端与千问视觉启动烟测均通过。

# 2026-08-09 四文档职责与覆盖算法整改

- 业务口径固定为：委托单提供基本事实，测试计划定义测试范围与方法，原始记录展开逐项执行，检测报告汇总发布；测试标准只提供已发布参考要求，不扩大范围。
- 删除审核结论中的人为父子测试项口径。计划明确列项是基础测试项；样品、模式、轮次、脉冲行默认作为执行明细，只有计划独立列项时才参与正向覆盖。
- 覆盖匹配改为全局一对一；确定性匹配和语义复核均禁止一条记录重复证明多个计划要求。
- 新增 `GRAPH-COVERAGE-002` 反向全集检查；计划提取完整时检查记录/报告计划外项目，计划不完整时保持未决。未消费的执行明细不冒充计划外测试项。
- 汇总标题只有在其全部明细已逐项匹配计划，且记录与报告明细一一对应时才被消化，防止“瞬态抗扰度试验”在 P1/P2a/P2b/P3a 均已覆盖后仍被误报为第五项。
- 文档人工核查状态持久化到具体文档版本；修改提取结果会使确认失效，四份最新资料未经确认不得锁定。锁定后的源文档修改由后端统一拒绝。
- 文件预览和原文件下载改用 Authorization 请求后生成 Blob URL，移除 URL 查询参数 JWT；ZIP 成员预览与原始记录解包统一执行路径、数量、解压大小、压缩比、加密项和符号链接门禁。
- 真实浏览器套件 `EMC-20260809-jgtgzc` 连续复跑：最终 178 节点、10 关系、34 个确定问题、2 个待确认、5 条完整证据链；汇总标题假阳性已消失。页面完成 PDF、Excel、证据包下载、证据图片和原文件鉴权验证。
- 最终验证：后端 `416 passed, 13 skipped`；主前端 751 模块和影子前端 3322 模块生产构建；PDF 39 页可解析，Excel 两表 53 行，ZIP 完整性通过；主前端和 v2 全导航真实浏览器烟测通过。
# 2026-08-11

- Persist source-version hashes, rendered-page geometry and unique line bboxes while unitizing documents; reviewed structured fields now use field-label context to anchor short values without fuzzy matching.
- Lazily enrich historical evidence with verified preview coordinates, while preserving locators after finding dismissal for auditability.
- Add versioned, private evidence-page PNG caching with ETag revalidation, atomic writes, bounded TTL/size cleanup, concurrent first-render deduplication and sanitized cache-hit metrics.
- V2 evidence viewers now consume the actual highlight count/strategy/anchor headers and explicitly mark unresolved pages instead of treating focus mode as proof of a highlight.
- Real V2 verification on `EMC-20260809-jgtgzc` / `DOC-CROSS-FIELD-001` produced one located highlight in each of the order form, test plan, final report and original record; a repeat open returned four HTTP 304 responses. Final verification: backend `429 passed, 13 skipped`, V2 `8 passed`, V2 and primary frontend production builds passed.

# 2026-08-11 GRAPH-SAMPLE-001 数量证据通解整改

- 对 `EMC-20260809-jgtgzc` 的真实来源、SQLite 图谱和运行日志交叉核查：计划数量 `4` 位于隐藏的 Excel E 列，实际第 5 页不可见；旧逻辑却从 `P4/12V` 中误命中数字。报告侧按全报告首次样品编号取证，错误落到第 6 页封面，而测试项局部结果实际位于第 15 页。
- 样品数量定位改为可见独立数字/明确数量标签与测试项同行的空间门禁，拒绝 `P4`、`14V`、项目序号和隐藏单元格；报告样品编号必须与测试项同页，禁止全局封面回退。
- 明确区分原文数量与派生数量：报告证据保存 `observed_sample_id`、`distinct_item_local_sample_ids` 和 `derived_count`，不再把样品编号伪装成数量原文。
- 任一侧无法形成可核验坐标时不生成问题或通过，`GRAPH-SAMPLE-001` 记录为 `system_incomplete`。真实文件离线运行得到 0 条该规则问题，原因 `plan_item_sample_count_not_visually_anchored`，预期输入 1、成功回锚 0。
- V2 真实浏览器复现确认旧图谱仍保留原问题、左侧无高亮且右侧封面编号高亮；这是历史快照，不原地改写。新规则在新审核图中生效。
- 最终验证：后端 `431 passed, 13 skipped`；V2 `8 passed` 且 3323 模块生产构建通过；主前端 751 模块生产构建通过。
