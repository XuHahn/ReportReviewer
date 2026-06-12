# CLAUDE.md

Guidance for Claude Code when working with this repository.

## Project overview

**EMC报告智能审核系统 v2** — 四源一致性核查平台。上传 4 份关联文档（委托单、试验计划、原始记录、检测报告），自动提取结构化数据，交叉验证一致性，生成审核报告。

| v1（已废弃） | v2（当前） |
|------|------|
| 单文件上传 + 2-stage AI 审核 | 4 文档上传 + 交叉验证管线 |
| DeepSeek 粗扫→详细审核 | 确定性代码提取 + AI 语义提取 + 交叉比对 |
| ReportUploader.tsx | DocumentSetUploader.tsx |
| ReviewItem 标注 | PipelineValidationIssue 标注 |

## Project structure

```
ReportReviewer/
├── start.sh / deploy.sh / docker-compose.yml
├── design/                              # UX + 实现规格文档
├── backend/
│   ├── main.py                          # FastAPI, 5 routers
│   ├── models.py                        # Pydantic 模型
│   ├── database.py                      # SQLite WAL + 迁移
│   ├── auth.py / rate_limit.py
│   ├── services/
│   │   ├── deepseek_client.py           # _call_api, _parse_json
│   │   ├── prompts.py                   # TEST_PLAN_PROMPT, REPORT_PROMPT
│   │   ├── order_form_extractor.py      # 委托单 · 100% 代码
│   │   ├── test_plan_extractor.py       # 试验计划 · 100% AI
│   │   ├── report_extractor.py          # 检测报告 · AI + 代码校验
│   │   ├── raw_records_*.py             # 原始记录 · 5 模块
│   │   ├── document_set.py / pipeline.py / cross_validator.py / exceptions.py
│   │   └── parser.py / exporter.py
│   ├── routers/
│   │   ├── report.py / auth.py / admin.py / groups.py / document_set.py
│   ├── utils/ (pdf_utils.py, logger.py, constants.py)
│   └── tests/ (242 tests, 10 modules)
├── frontend/ (port 5173, 7 tabs)
│   └── src/
│       ├── App.tsx (含「四源校验」Tab)
│       ├── api.ts / types.ts
│       └── components/
│           ├── DocumentSetUploader.tsx   # ★ 四源校验主组件
│           ├── ReportHistory.tsx / ReportReviewer.tsx / ReportComparison.tsx
│           ├── RulesManager.tsx / StandardsBrowser.tsx / ProjectGroups.tsx
│           ├── StatsDashboard.tsx / LoginPage.tsx / ErrorBoundary.tsx
└── admin/ (port 5174, 8 pages)
    └── src/
        ├── App.tsx / Sidebar.tsx
        └── components/
            ├── DocumentSetManager.tsx    # ★ 文档集管理
            ├── ValidationIssueManager.tsx # ★ 校验结果管理
            ├── Dashboard.tsx / AllReports.tsx / UserManager.tsx
            ├── AuditLogs.tsx / ProjectGroupManager.tsx / SystemSettings.tsx / LoginPage.tsx
```

## Architecture

- **Backend**: Python FastAPI port 8000. SQLite WAL. 100MB upload limit.
- **Frontend**: React 18 + Vite port 5173. 7 tabs (display:none preserve state).
- **Admin**: React 18 + Vite port 5174. 8 pages with sidebar.
- **Auth**: JWT HS256. Authorization header. Roles: admin/reviewer/viewer.
- **DB tables**: reports, audit_log, review_rules, emc_standards, users, system_settings, suppression_patterns, project_groups, project_group_members, tags, document_sets, set_documents, extracted_metadata, pipeline_validation_issues, review_items_fts

### 4-Document extraction strategy

| Document | Format | Method | Key file |
|------|------|------|------|
| 委托单 | .xls | 100% deterministic | order_form_extractor.py |
| 试验计划 | PDF/DOCX | 100% AI | test_plan_extractor.py |
| 原始记录 | PDF(ZIP) | 95% code + 5% AI | raw_records_*.py |
| 检测报告 | PDF | AI + code validation | report_extractor.py |

### Pipeline flow

```
Upload 4 docs → Extract → Human review → Lock
  → cross_validator.py (24 checks, 3 tiers)
  → pipeline.py (orchestrate + save PipelineValidationIssues)
  → Results display + annotation
  → Export report
```

### Cross-validation checks (24 total)

| # | Check | Tier | Category |
|---|-------|------|----------|
| 0 | 测试项覆盖 (计划 vs 记录 + name fuzzy match) | P0 | coverage |
| 1 | TOC 三方覆盖 (计划+目录+记录) | P0 | toc |
| 2 | 原始记录结论扫描 | P0 | conclusion |
| 3 | 报告结论 vs 记录结论 | P0 | conclusion |
| 4 | 仪器校准有效期 | P0 | calibration |
| 5 | 仪器清单交叉比对 (报告 vs 记录, 去重后) | P0 | instrument |
| 6 | 基本信息比对 (标准编号) | P0 | basic_info |
| 7 | 测试日期跨度 | P0 | date |
| 8 | 额定电压一致性 | P1 | basic_info |
| 9 | 测试模式一致性 | P1 | method |
| 10 | 数据行数对比 (仅发射类, 跳过抗扰类) | P1 | data |
| 11 | 方法合规检查 | P1 | method |
| 12 | TOC 完整性 (目录 vs 结果) | P1 | toc |
| 13 | 多数投票 (4文档字段比对, 过滤 / - 占位符) | P0 | basic_info |
| 14 | 格式规则校验 (regex) | P0 | format |
| 15 | 物理范围校验 | P1 | range |
| 16 | 余量公式 + 超标检测 | P0 | logic |
| 17 | 总体结论 vs 单项结果 | P0 | conclusion |
| 18 | 签发日期逻辑 | P1 | date |
| 19 | 封面信息完整性 | P1 | format |
| 20 | LLM 深度语义审核 | P0 | llm |
| 21 | 供应商名称一致性 (过滤 / 占位符) | P0 | basic_info |
| 22 | 样品数量一致性 | P0 | basic_info |
| 23 | 测试计划编号一致性 (plan无编号→INFO) | P0 | basic_info |

### Key design decisions for validation

- **"/" handling**: Values like `/`, `-`, `—`, `N/A` are treated as "not provided" and excluded from multi-document comparisons (majority vote, supplier name check).
- **Coverage name matching**: When raw record test item codes don't match plan codes (e.g. "短时中断试验" vs "EQ/IC04"), a fuzzy name tokenizer (`_fuzzy_match_name`) attempts token-based matching; if found, severity is reduced to INFO instead of WARNING.
- **Data comparison skips immunity data**: The data-row comparison (`_data_comparison`) only compares rows with frequency values (emission-type: EQ/MC, EQ/MR). Immunity-type rows (EQ/IC, EQ/IR) have no frequency field and are skipped to avoid false "unmatched frequency point" warnings.
- **Instrument deduplication**: Both report and raw records instruments are deduplicated by `(manufacturer, model, serial_no)` before comparison. The raw `instruments` list preserves the full table, while `deduplicated_instruments` has unique physical instruments.
- **Plan w/o is_executed**: If all test plan items have empty `is_executed`, all items are treated as required (common for customer-provided template plans).

## Commands

```bash
# Backend
cd backend && uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm run dev          # :5173
cd admin && npm run dev             # :5174

# Tests (242 passed, 1 unrelated OCR failure)
cd backend && pytest tests/ -v

# Type check
npx -p typescript tsc -p frontend/tsconfig.json --noEmit
npx -p typescript tsc -p admin/tsconfig.json --noEmit
```

## Logging conventions

- **Library**: `structlog` with `contextvars` support. See `backend/utils/logger.py`.
- **Format**: In `dev` mode (`LOG_ENV=dev` or unset), colored console output via `structlog.dev.ConsoleRenderer`. In `prod` mode (`LOG_ENV=prod`), JSONL to stderr and `logs/app.jsonl` (daily rotation, 30-day retention).
- **Levels**:
  - `DEBUG` — only in dev mode (verbose structlog internals).
  - `INFO` — normal operations: request start/end, AI call success, pipeline start/done, CRUD actions.
  - `WARNING` — retryable failures (API retries, transient errors).
  - `ERROR` — non-retryable failures, exhausted retries, extraction errors.
- **Trace ID**: Every HTTP request gets a unique `reqId` (uuid4 hex, 8 chars) via `LoggingMiddleware`. Bound to the structlog context via `structlog.contextvars.bind_contextvars(reqId=...)`. Propagates automatically to background tasks created with `asyncio.create_task`. All log events within that request include `reqId=<value>`.
- **Required fields for event logs**:
  - `event` (message key, e.g. `"ai_call"`, `"pipeline_done"`, `"request completed"`).
  - `reqId` — always present within HTTP request scope.
  - `stage` — for AI calls: `"coarse"`, `"detailed"`, `"test_plan"`, `"report"`.
  - `set_id` — for pipeline operations.
  - `user` — employee_id for audit-relevant actions.
- **Sanitization**: Sensitive fields (`customer_name`, `device_id`, `phone`, `email`, etc.) are masked via `log_sanitizer.py`. String values over 1000 characters are truncated.
- **Key event names**:
  | Event | Context | Level |
  |------|------|------|
  | `request completed` | Every HTTP request (middleware) | INFO/WARNING/ERROR |
  | `ai_call` | Successful DeepSeek API call | INFO |
  | `API_RETRY` | DeepSeek retry attempt | WARNING |
  | `API_EXHAUSTED` | All DeepSeek retries failed | ERROR |
  | `pipeline_start` / `pipeline_done` | Cross-validation lifecycle | INFO |
  | `extraction_start` / `extraction_done` | Document extraction | INFO |
  | `JSON_PARSE` | JSON repair attempt | WARNING |

## Key APIs

**DocumentSet (new):**
POST /api/sets · GET /api/sets · GET /api/sets/{id} · POST /api/sets/{id}/documents
POST /api/sets/{id}/lock · PUT /api/sets/{id}/status · POST /api/sets/{id}/review
GET /api/sets/{id}/versions/{type} · GET /api/sets/{id}/diff
GET /api/sets/{id}/issues · PATCH /api/sets/{id}/issues/{id}/annotation

**Reports (legacy+current):**
GET /api/reports/history · GET /api/reports/search · GET /api/reports/stats
GET /api/reports/{id} · PATCH /api/reports/{id}/items/{n}/annotation
DELETE /api/reports/{id} · GET /api/reports/{id}/export/{pdf,word}

**Rules/Standards/Groups/Auth:** unchanged from v1.

## Key decisions

- DocumentSet groups 4 required + 1 optional documents. File-level versioning via parent_doc_id.
- Extraction happens at upload time, results cached in extracted_metadata.
- Lock requires all 4 docs uploaded + extracted + human-verified before cross-validation.
- No single source of truth — all docs may have errors. Cross-validation flags discrepancies for human judgment.
- PipelineValidationIssue supports human_status (pending/confirmed/ignored) + human_comment.
- All DocumentSet operations logged to audit_log.

## agent要求
### 1. 核心交互原则
- 主动确认：在制定计划或执行任务时，若遇到任何分歧点或不理解的内容，必须立即向我提问确认，禁止自行假设。
- 解决问题应考虑通解，而不是仅针对这个bug进行修复。
- 结束标识：每次回复结束时，必须打印“结束了喵”。
### 2. Debug 与日志规范
- 日志驱动：所有 Debug 行为必须结合日志进行分析。
- 缺失处理：若现有开发日志未覆盖当前排查内容，需将相关需求整理至 design 文件夹内，并主动询问我是否需要编写相关的日志记录代码。
### 3. 测试用例资源库
- 正式报告：example/E20260402869601-1正式报告.pdf
- 原始记录：example/E20260402869601原始记录.zip
- 委托单：example/E20260402869601委托单.xls
- 测试计划：example/E20260402869601测试计划.pdf