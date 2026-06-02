# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

EMC检测报告智能审核系统 — an intelligent report review system for EMC testing labs. Users upload test reports (Word/PDF), the system calls the DeepSeek API to perform compliance review using a two-stage approach (coarse scan → detailed review), and displays errors with red highlighting directly on the original report content. Supports human annotation, project groups for team collaboration, and iterative re-upload with AI-powered diff comparison.

## Project structure

```
ReportReviewer/
├── start.sh                         # 一键启动（backend + frontend + admin）
├── deploy.sh / deploy-remote.sh     # 部署脚本
├── docker-compose.yml
├── backend/
│   ├── main.py                      # FastAPI 入口, 4 routers
│   ├── config.py                    # 环境变量
│   ├── models.py                    # Pydantic 模型
│   ├── database.py                  # SQLite + 迁移 + CRUD
│   ├── auth.py                      # JWT (Authorization header only)
│   ├── rate_limit.py                # 线程安全限流
│   ├── services/
│   │   ├── deepseek_client.py
│   │   ├── prompts.py
│   │   ├── streaming_parser.py
│   │   ├── parser.py
│   │   ├── exporter.py
│   │   └── suppression_filter.py
│   ├── routers/
│   │   ├── report.py                # 上传/审核/历史/导出/标注/timeline
│   │   ├── auth.py                  # 登录/用户管理(含姓名)
│   │   ├── admin.py                 # 管理设置
│   │   └── groups.py                # 项目组 CRUD (NEW)
│   ├── tests/
│   │   ├── conftest.py                 # 测试基础设施（temp DB + 日志 + 用户夹具）
│   │   ├── utils.py                    # 断言辅助（assert_api_db, assert_log_chain, normalize）
│   │   ├── pytest.ini                  # asyncio_mode = auto
│   │   ├── test_crud_reports.py        # 10 tests: 读取/标注/删除/恢复
│   │   ├── test_crud_users.py          # 8 tests: 用户 CRUD
│   │   ├── test_crud_rules.py          # 5 tests: 规则 CRUD
│   │   ├── test_crud_groups.py         # 7 tests: 项目组 CRUD + 成员管理
│   │   ├── test_crud_standards.py      # 5 tests: 标准 CRUD
│   │   ├── test_crud_tags.py           # 5 tests: 标签 CRUD
│   │   ├── test_crud_settings.py       # 3 tests: 系统设置读写
│   │   ├── test_concurrent.py          # 5 tests: 并发标注/角色变更/令牌过期
│   │   └── test_permissions.py         # 14 tests: viewer/reviewer/admin/未认证 权限边界
│   └── utils/
│       ├── highlighter.py
│       ├── logger.py                   # structlog JSONL（LOG_DIR 支持环境变量覆盖）
│       └── constants.py
├── frontend/                        # 主界面 port 5173
│   ├── vite.config.ts               # Vite + /api proxy
│   ├── index.html
│   └── src/
│       ├── App.tsx                  # 6 tabs (上传审核|配置|标准库|项目组|仪表盘|报告)
│       ├── api.ts                   # axios + blob export + 403 handle
│       ├── types.ts
│       ├── index.css                # 暗色模式/响应式/打印/无障碍
│       └── components/
│           ├── ReportUploader.tsx    # 先选文件→开始审核→流式进度
│           ├── ReportReviewer.tsx    # 双栏审核+标注+导出+操作记录
│           ├── ReportHistory.tsx     # 版本胶囊 Root/V2/V3, 对比/删除
│           ├── ProjectGroups.tsx     # 项目组网格卡片+成员管理 (NEW)
│           ├── UserManagement.tsx    # 工号+姓名+角色
│           ├── StatsDashboard.tsx    # 骨架屏+刷新
│           ├── BatchReviewer.tsx
│           ├── ReportComparison.tsx
│           ├── RulesManager.tsx
│           ├── StandardsBrowser.tsx
│           ├── LoginPage.tsx
│           └── ErrorBoundary.tsx     # (NEW)
└── admin/                           # 后台管理 port 5174 (独立SPA)
    ├── vite.config.ts
    └── src/
        ├── App.tsx                  # 侧边栏+5页面
        ├── api.ts / types.ts
        └── components/              # Dashboard, AllReports, UserManager, etc.
```

## Architecture

- **Backend**: Python FastAPI port 8000. SQLite WAL mode. 100MB upload limit.
- **Frontend**: React 18 + Vite port 5173. 6 tabs. All tab content stays mounted (`display:none`), state preserved.
- **Admin**: Separate React app port 5174. Sidebar navigation. 5 pages.
- **Database tables**: `reports`, `audit_log`, `review_rules`, `emc_standards`, `users` (with `name`), `system_settings`, `suppression_patterns`, `project_groups`, `project_group_members`, `review_items_fts` (FTS5), `tags`
- **Auth**: JWT HS256, token via Authorization header only. Login case-insensitive. Roles: admin/reviewer/viewer.
- **Tests**: 61 test cases across 9 modules. Three-way consistency pattern: API response ↔ database truth + reqId log tracing. Uses `httpx.ASGITransport` for direct ASGI testing. Temp SQLite DB per session, `LOG_ENV=prod` for JSONL log verification. See `backend/tests/conftest.py` for fixture architecture.

## Commands

```bash
# Backend
cd backend && uvicorn main:app --reload --port 8000

# Frontend main
cd frontend && npm run dev          # http://localhost:5173

# Frontend admin
cd admin && npm run dev             # http://localhost:5174

# Type-check
npx -p typescript tsc -p frontend/tsconfig.json --noEmit
npx -p typescript tsc -p admin/tsconfig.json --noEmit

# Tests (backend)
cd backend
pip install -r tests/requirements-test.txt
pytest tests/ -v                    # 61 tests, ~2s
pytest tests/ -v --tb=short         # short traceback on failure
pytest tests/test_crud_reports.py   # single module
pytest tests/ -k "test_create"      # keyword filter
```

## API endpoints (all under /api)

| Method | Path | Auth | Description |
|------|------|------|------|
| GET | /health | None | Health check |
| POST | /auth/login | None | Login (case-insensitive) |
| GET | /auth/me | JWT | Current user |
| GET/POST | /users | admin | List/create users (with name) |
| PUT | /users/{id}/role | admin | Update role |
| PUT | /users/{id}/name | admin | Update name |
| GET/PUT | /admin/settings | admin | System settings |
| GET | /admin/stats | admin | Dashboard stats |
| POST | /reports/upload | reviewer+ | Blocking upload |
| POST | /reports/upload/stream | reviewer+ | SSE streaming upload |
| POST | /reports/upload/batch | reviewer+ | Batch upload (max 10) |
| GET | /reports/history | Any | History (keyword/result/date/tag filter) |
| GET | /reports/search | Any | FTS5 search |
| GET | /reports/{id} | Any | Report detail |
| PATCH | /reports/{id}/items/{n}/annotation | Any | Annotate (409 conflict) |
| GET | /reports/{id}/check-updates | Any | Poll updates |
| GET | /reports/{id}/timeline | Any | Audit trail (with names) |
| DELETE | /reports/{id} | uploader/admin | Soft-delete (?cascade=true) |
| POST | /reports/{id}/restore | admin | Restore |
| GET | /reports/{id}/export/pdf | Any | Export PDF (blob) |
| GET | /reports/{id}/export/word | Any | Export Word (blob) |
| GET | /reports/export/batch | Any | Batch Excel (blob) |
| GET | /reports/stats | Any | Stats |
| GET/POST | /rules, /rules/{id} | reviewer+ | Rules CRUD |
| PUT/DELETE | /rules/{id} | reviewer+ | Rules CRUD |
| GET/POST | /standards, /standards/{id} | reviewer+ | Standards CRUD |
| DELETE | /standards/{id} | reviewer+ | Delete standard |
| GET/POST | /groups, /groups/{id} | Any/reviewer+ | Project groups CRUD |
| PUT/DELETE | /groups/{id} | reviewer+ | Project groups CRUD |
| GET | /logs | Any | Audit log |
