# EMC检测报告智能审核系统 — 开发文档

## 一、项目概述

### 1.1 背景
为广电计量EMC检测实验室开发的智能化报告审核系统。用户上传测试报告（Word/PDF），系统调用DeepSeek大模型API进行两阶段合规审核（粗扫描→详细审核），审核结果以红色高亮直接标注在报告原文上。支持人工标注、项目组协作、版本管理和迭代复审。

### 1.2 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python FastAPI + uvicorn |
| 前端 | React 18 + TypeScript + Vite |
| AI | DeepSeek API (deepseek-chat, temperature=0) |
| 文件解析 | mammoth (DOCX→HTML), PyPDF2 (PDF), BeautifulSoup |
| 数据库 | SQLite WAL mode |
| 认证 | JWT HS256 (3 角色: admin/reviewer/viewer) |
| 部署 | Docker Compose (3 服务: backend + frontend + admin) |

---

## 二、系统架构

### 2.1 项目结构

```
ReportReviewer/
├── start.sh / deploy.sh / deploy-remote.sh
├── docker-compose.yml
├── CLAUDE.md
├── workload.md                          # 本文档
├── backend/
│   ├── main.py                          # FastAPI 入口 + 日志中间件(reqId) + CORS
│   ├── config.py                        # 环境变量
│   ├── models.py                        # Pydantic 模型
│   ├── database.py                      # SQLite CRUD + WAL + 异步包装
│   ├── auth.py                          # JWT 认证 + 角色守卫
│   ├── rate_limit.py                    # 线程安全限流
│   ├── pytest.ini                       # asyncio_mode=auto
│   ├── requirements.txt
│   ├── services/
│   │   ├── deepseek_client.py           # 两阶段审核 + token估算 + 流式 + 重试
│   │   ├── prompts.py                   # Prompt 模板 + Token 常量
│   │   ├── streaming_parser.py          # SSE 增量 JSON 解析器
│   │   ├── parser.py                    # PDF/Word 文本提取 + HTML 转换
│   │   ├── exporter.py                  # PDF/Word/Excel 导出
│   │   └── suppression_filter.py        # difflib 误报抑制
│   ├── routers/
│   │   ├── report.py                    # 上传/审核/历史/导出/标注/规则/标准
│   │   ├── auth.py                      # 登录/用户管理
│   │   ├── admin.py                     # 系统设置/管理统计
│   │   └── groups.py                    # 项目组 CRUD
│   ├── tests/
│   │   ├── conftest.py                  # 测试基础设施
│   │   ├── utils.py                     # 断言工具 + 数据工厂
│   │   ├── test_crud_reports.py         # 10 tests
│   │   ├── test_crud_users.py           # 8 tests
│   │   ├── test_crud_rules.py           # 5 tests
│   │   ├── test_crud_groups.py          # 7 tests
│   │   ├── test_crud_standards.py       # 5 tests
│   │   ├── test_crud_tags.py            # 5 tests
│   │   ├── test_crud_settings.py        # 3 tests
│   │   ├── test_concurrent.py           # 5 tests
│   │   └── test_permissions.py          # 14 tests
│   └── utils/
│       ├── highlighter.py               # 6-pass 错误标红
│       ├── logger.py                    # structlog JSONL 日志
│       └── constants.py                 # 颜色/标签常量
├── frontend/                            # 主界面 :5173
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx                      # 侧边栏 + 6-tab
│       ├── api.ts / types.ts / constants.ts
│       ├── index.css                    # 深色模式 + 响应式 + WCAG AA
│       └── components/
│           ├── ReportUploader.tsx       # 流式上传 + 批量模式
│           ├── ReportReviewer.tsx       # 双栏审核 + 人工标注 + 导出
│           ├── ReportHistory.tsx        # 版本胶囊 + FTS5 搜索 + 对比
│           ├── ProjectGroups.tsx        # 网格卡片 + 成员管理
│           ├── RulesManager.tsx         # 审核规则 CRUD
│           ├── StandardsBrowser.tsx     # EMC 标准库
│           ├── StatsDashboard.tsx       # Recharts 仪表盘 + 骨架屏
│           ├── BatchReviewer.tsx        # 批量审核结果
│           ├── ReportComparison.tsx     # 并排报告对比
│           ├── UserManagement.tsx       # 用户管理
│           ├── LoginPage.tsx            # 登录
│           └── ErrorBoundary.tsx        # 错误边界
└── admin/                               # 后台管理 :5174
    ├── vite.config.ts
    ├── Dockerfile / nginx.conf
    └── src/
        ├── App.tsx                      # 侧边栏 + 5 页面
        └── components/                  # Dashboard, AllReports, UserManager...
```

### 2.2 审核流程

```
用户上传 Word/PDF
      │
      ▼
parser.py         mammoth DOCX→HTML（去图片）/ PyPDF2 文本提取
                  返回 { plain_text, html_content }
      │
      ▼
report.py         从 HTML 提取目录结构 (h1-h4)
      │ plain_text + toc + html_content
      ▼
deepseek_client   token 估算 (len/1.8)
                  若 >120K → 按章节优先级过滤:
                    HIGH (测试结果/结论/限值) → 全文
                    MEDIUM (标准/方法/设备) → 标题+500字
                    LOW (附件/照片/声明) → 跳过
      │ review_text
      ▼
Stage 1 粗扫描     SCAN_PROMPT + TOC → { checklist: [{severity, location, flag}] }
(180s)            无问题 → 直接返回 pass
      │ checklist
      ▼
Stage 2 详细审核   REVIEW_PROMPT + TOC + checklist
(300s)            → { overall_result, review_items[], summary }
      │ review_items
      ▼
suppression_filter  difflib 匹配历史抑制模式 (threshold 0.8)
                    自动标记 false_positive
      │ filtered review_items
      ▼
highlighter.py    6-pass HTML 匹配 → 标红原文
                  未匹配项标注虚线边框
      │ highlighted_html
      ▼
前端双栏展示       左栏: 报告原文（标红+编号徽章）
                  右栏: 错误卡片列表（5 种人工标注状态）
                  双向点击导航 + 筛选切换
```

### 2.3 SSE 流式事件

| 事件 | 触发时机 | payload |
|------|---------|---------|
| `progress` | 阶段切换 | `{phase, message, elapsed_seconds}` |
| `checklist_summary` | Stage 1 完成 | `{total_flags, severities, sample_flags}` |
| `review_item` | Stage 2 逐条产出 | 单个 ReviewItem |
| `result` | 全部完成 | `{report_id, overall_result, highlighted_html, ...}` |
| `error` | 异常终止 | `{message}` |
| `batch_start` | 批量开始 | `{total, filenames}` |
| `batch_progress` | 单文件完成 | `{filename, result, issues, duration_ms}` |
| `batch_done` | 批量结束 | 全量结果汇总 |

---

## 三、数据库表

| 表名 | 说明 | 关键列 |
|------|------|--------|
| `reports` | 审核报告 | id, employee_id, filename, original_content, review_items(JSON), highlighted_html, overall_result, is_deleted, tags, created_at |
| `users` | 用户 | employee_id, name, role(admin/reviewer/viewer), created_at |
| `review_rules` | 自定义规则 | id, name, description, category, severity, keywords(JSON), pattern, enabled |
| `emc_standards` | EMC标准 | id, code, title, organization, category, version, clauses(JSON), is_builtin |
| `project_groups` | 项目组 | id, name, description, created_by, created_at |
| `project_group_members` | 组成员 | group_id, employee_id |
| `tags` | 标签 | id, name |
| `system_settings` | 系统设置 | key, value |
| `audit_log` | 审计日志 | id, action, employee_id, report_id, detail(JSON), ip, timestamp |
| `suppression_patterns` | 抑制模式 | id, original_text, error_description, severity, suppressed_by, suppressed_at |
| `review_items_fts` | FTS5全文索引 | original_text, error_description, location, report_id(UNINDEXED) |

---

## 四、API 接口

### 4.1 端点总表

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/health` | 无 | 健康检查 |
| POST | `/api/auth/login` | 无 | 登录（不区分大小写） |
| GET | `/api/auth/me` | 登录 | 当前用户信息 |
| GET | `/api/users` | admin | 用户列表 |
| POST | `/api/users` | admin | 创建用户（含姓名） |
| PUT | `/api/users/{id}/role` | admin | 更新角色 |
| PUT | `/api/users/{id}/name` | admin | 更新姓名 |
| DELETE | `/api/users/{id}` | admin | 删除用户 |
| GET | `/api/admin/settings` | admin | 系统设置 |
| PUT | `/api/admin/settings` | admin | 更新设置 |
| GET | `/api/admin/stats` | admin | 管理统计 |
| POST | `/api/reports/upload` | reviewer+ | 阻塞式上传审核 |
| POST | `/api/reports/upload/stream` | reviewer+ | SSE 流式上传 |
| POST | `/api/reports/upload/batch` | reviewer+ | 批量上传 (≤10) |
| GET | `/api/reports/history` | 登录 | 历史列表（keyword/result/date/tag 筛选） |
| GET | `/api/reports/search` | 登录 | FTS5 全文搜索 |
| GET | `/api/reports/stats` | 登录 | 统计数据 |
| GET | `/api/reports/{id}` | 登录 | 报告详情 |
| PATCH | `/api/reports/{id}/items/{n}/annotation` | 登录 | 人工标注 (409 冲突检测) |
| GET | `/api/reports/{id}/check-updates` | 登录 | 轮询更新 |
| GET | `/api/reports/{id}/timeline` | 登录 | 操作时间线 |
| DELETE | `/api/reports/{id}` | 上传者/admin | 软删除 |
| POST | `/api/reports/{id}/restore` | admin | 恢复 |
| GET | `/api/reports/{id}/export/pdf` | 登录 | 导出 PDF |
| GET | `/api/reports/{id}/export/word` | 登录 | 导出 Word |
| GET | `/api/reports/export/batch` | 登录 | 批量 Excel |
| GET | `/api/rules` | 登录 | 规则列表 |
| POST | `/api/rules` | reviewer+ | 创建规则 |
| PUT | `/api/rules/{id}` | reviewer+ | 更新规则 |
| DELETE | `/api/rules/{id}` | reviewer+ | 删除规则 |
| GET | `/api/standards` | 登录 | 标准列表 |
| GET | `/api/standards/{id}` | 登录 | 标准详情 |
| POST | `/api/standards` | reviewer+ | 添加标准 |
| DELETE | `/api/standards/{id}` | reviewer+ | 删除标准（内置不可删） |
| GET | `/api/tags` | 登录 | 标签列表 |
| POST | `/api/tags` | admin | 创建标签 |
| PUT | `/api/tags/{id}` | admin | 更新标签 |
| DELETE | `/api/tags/{id}` | admin | 删除标签 |
| GET | `/api/groups` | 登录 | 项目组列表 |
| POST | `/api/groups` | reviewer+ | 创建项目组 |
| PUT | `/api/groups/{id}` | reviewer+ | 更新项目组 |
| DELETE | `/api/groups/{id}` | reviewer+ | 删除项目组 |
| GET | `/api/logs` | 登录 | 审计日志 |

### 4.2 认证与权限

- **JWT**: HS256，7天过期。Authorization: Bearer `<token>`
- **角色**: admin（全部权限）、reviewer（上传审核+查看全部报告）、viewer（仅查看本人报告+仪表盘）
- **数据隔离**: viewer 角色的历史记录和统计端点自动限制为本人数据

### 4.3 上传审核响应示例

```json
{
  "report_id": "ac848ba04699",
  "filename": "S20250805599001 报告.docx",
  "overall_result": "fail",
  "review_items": [{
    "severity": "error",
    "location": "报告封面",
    "original_text": "Test standard :IEC 62368-1: 2023",
    "error_description": "引用标准IEC 62368-1是安全标准，并非EMC标准",
    "standard_reference": "应引用CISPR 25、IEC 61000-6系列等",
    "suggestion": "更正测试标准为适用的EMC标准",
    "highlighted": true,
    "human_status": "pending",
    "human_comment": null
  }],
  "highlighted_html": "<p>...<mark class=\"error-highlight\">...</mark>...</p>",
  "created_at": "2026-04-28T10:30:00Z",
  "estimated_tokens": 45000,
  "token_limit": 120000,
  "truncated": false
}
```

---

## 五、AI 审核设计

### 5.1 Prompt 策略

**Stage 1 — SCAN_PROMPT (180s)**
- 角色: EMC检测领域专业审核专家
- 任务: 快速扫描全文，列出所有疑似问题点
- 输入: TOC + 报告全文
- 输出: `{"checklist": [{"severity": "error/warning/info", "location": "...", "flag": "..."}]}`
- 无问题则 checklist 为空数组

**Stage 2 — REVIEW_PROMPT (300s)**
- 任务: 逐条核实 checklist，补充遗漏
- 要求: original_text 必须从报告中逐字摘录，不得改写
- 输出: `{"overall_result": "pass/fail/warning", "review_items": [...], "summary": "..."}`
- 分级: error(限值超标/结论矛盾) > warning(数据异常/格式不规范) > info(优化建议)

### 5.2 Token 管理

```python
MAX_CONTEXT_TOKENS = 120000    # deepseek-chat 128K 上下文，留 8K 余量

def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 1.8))
```

### 5.3 章节优先级过滤

| 优先级 | 关键词 | 处理 |
|--------|--------|------|
| HIGH | 测试结果/结论/限值/compliance/measured | 保留全文 |
| MEDIUM | 标准/方法/设备/temperature | 标题+前500字 |
| LOW | 附件/照片/声明/封面/blank | 跳过 |

### 5.4 重试与容错

- 指数退避重试 3 次 (2/4/8s)，仅对网络/超时/服务端/限流错误重试
- `_parse_json()`: 去注释 → 单引号转双引号 → 去尾逗号 → 提取JSON块
- temperature=0 最大化输出确定性

---

## 六、环境变量与启动

### 6.1 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `DEEPSEEK_API_KEY` | 是 | — | API密钥 (存入 `backend/.env`) |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | API地址 |
| `DB_PATH` | 否 | `review.db` | 数据库路径 |
| `JWT_SECRET` | 否 | 自动生成 | JWT签名密钥 |
| `JWT_EXPIRE_HOURS` | 否 | `168` | Token过期 (7天) |
| `LOG_ENV` | 否 | `dev` | `prod` 时写入 JSONL |
| `LOG_DIR` | 否 | `../logs` | 日志目录 |
| `EMC_PORT` | 否 | `8080` | Docker部署端口 |

### 6.2 本地开发

```bash
# 一键启动
bash start.sh

# 分别启动
cd backend && uvicorn main:app --reload --port 8000
cd frontend && npm run dev       # → :5173
cd admin && npm run dev          # → :5174

# 测试
cd backend
pip install -r tests/requirements-test.txt
pytest tests/ -v                 # 61 tests, ~2s
```

### 6.3 Docker 部署

```bash
docker compose up -d             # → :8080
docker compose logs -f backend
docker compose down
```

---

## 七、测试

### 7.1 架构

- **框架**: pytest + pytest-asyncio + httpx.ASGITransport（直接测 ASGI app, 无网络层）
- **校验模式**: API 响应 ↔ 数据库真实值 + reqId 日志链追踪
- **环境**: 模块级环境变量注入 → 临时 SQLite + JSONL 日志 → `atexit` 自动清理
- **用户夹具**: admin + reviewer×2 + viewer×2，5 个 HTTP client

### 7.2 断言工具 (`tests/utils.py`)

| 函数 | 作用 |
|------|------|
| `assert_api_db(api_val, db_val, path, req_id)` | 标准化 2-way 对比 |
| `assert_log_chain(req_id, read_logs)` | reqId 日志完整性验证 |
| `normalize(value)` | 递归规范化 (""→None, 浮点6位, dict排序) |
| `extract_req_id(response)` | 提取 X-Request-Id 响应头 |

### 7.3 测试覆盖 (61 passed, 1 skipped)

| 模块 | 用例 | 覆盖 |
|------|------|------|
| `test_crud_reports` | 10 | 历史列表/详情/标注/软删除/恢复 |
| `test_crud_users` | 8 | 创建/列表/me/角色/姓名/删除 |
| `test_crud_rules` | 5 | 规则 CRUD |
| `test_crud_groups` | 7 | 项目组 CRUD + 成员级联 |
| `test_crud_standards` | 5 | 标准 CRUD + JSON条款 + 内置保护 |
| `test_crud_tags` | 5 | 标签 CRUD + 空白裁剪 |
| `test_crud_settings` | 3 | 设置读写 + 部分更新保护 |
| `test_concurrent` | 5 | 并发标注竞态 + 令牌过期 + 顺序标注 |
| `test_permissions` | 14 | 4角色×多端点权限矩阵 |

**已发现的生产 Bug** (3 个):
1. `save_rule_async` / `save_standard_async` — `**kwargs` 签名导致 POST 端点不可用
2. `logger.py` LOG_DIR 硬编码 — 测试无法重定向日志
3. 并发标注 read-modify-write 竞态 — 全量 JSON 覆盖导致并行标注互相覆盖

---

## 八、迭代记录

### 迭代1：基础搭建
- 创建 backend/ 和 frontend/ 项目结构
- 后端 FastAPI + SQLite + DeepSeek API 调用
- 前端 Vite + React 18 + TypeScript + react-dropzone

### 迭代2：改善错误展示
- 双栏布局：左侧报告原文 + 右侧错误列表
- 错误文字编号徽章 (`<sup>` 标签)
- 卡片增加原因/标准依据/建议字段
- 点击标红文字 → 右侧滚动到对应卡片并高亮

### 迭代3：保留Word原始格式
- mammoth 替代 python-docx，直接转 HTML
- 新增 ParsedReport dataclass (plain_text + html_content)
- BeautifulSoup 在 HTML 文本节点中包裹 `<mark>` 标签
- Word 元素 CSS 样式 (h1-h3, table, ul/ol)

### 迭代4：性能优化
- mammoth 去图片: 响应 7MB → 244KB
- 文本截断 12000 字符 + API 60s 超时
- 前端 120s 超时 + 耗时计数器

### 迭代5：标红匹配修复 + UI 美化
- 3-pass HTML 高亮: 精确 → 灵活空白 → 跨标签块级
- ReviewItem 新增 `highlighted: bool`
- 未匹配项虚线边框 + 黄色提示 + "未定位 N"统计
- 全局 CSS 合并到 `index.css`

### 迭代6：两阶段审核
- Stage 1 (SCAN_PROMPT, 180s): 快速扫描疑似问题清单
- Stage 2 (REVIEW_PROMPT, 300s): 逐条核实 + 补充遗漏
- temperature=0, 前端超时延长到 600s
- 从 HTML 提取 TOC 作为 prompt 上下文

### 迭代7：超限检测
- token 估算: `max(1, len/1.8)`, MAX_CONTEXT_TOKENS=120000
- 超限时前端区分 `error`(无法审核) 和 `truncated`(已过滤)

### 迭代8：章节优先级过滤
- 按 h1-h4 拆分章节，关键词分类 HIGH/MEDIUM/LOW
- 超限时逐步丢弃: medium → 最短 high
- UploadResponse 新增 `estimated_tokens`, `token_limit`, `truncated`

### 迭代9：双向点击导航
- 点击卡片 → 滚动到报告对应标红位置 + 闪烁动画
- 未匹配项 (highlighted=false) 点击不跳转

### 迭代10：产品化收尾
- start.sh 一键启动脚本
- UI 美化: 现代化配色、圆角卡片、渐变 header
- 审核历史功能 + 响应式布局

### 迭代11：审核历史搜索与筛选
- 后端新增 keyword/overall_result/date_from/date_to 参数
- 前端 ReportHistory 折叠面板 + 搜索框 + 筛选下拉

### 迭代12：审核结果导出
- `exporter.py`: PDF (Chrome headless) + Word (python-docx)
- 前端 "导出 PDF/Word" 按钮

### 迭代13：SSE 流式响应
- `POST /api/reports/upload/stream` + `streaming_parser.py`
- 前端 fetch + ReadableStream + 进度面板 + AbortController 取消
- 阶段进度条 + checklist 摘要 + 逐条 slideIn 动画

### 迭代14：安全加固与日志
- API Key 强制从 `.env` 读取
- RotatingFileHandler (10MB×5) + 100MB 上传上限
- `audit_log` 表 + 全端点审计 + DOMPurify + `.gitignore`

### 迭代15：API 重试 + 数据库加固
- DeepSeek API 指数退避重试 3 次 (2/4/8s)
- SQLite WAL + busy_timeout=5s + synchronous=NORMAL
- Chrome 路径自动检测 (macOS/Linux/Windows)

### 迭代16：安全收尾 + 启动强化
- `rate_limit.py`: 60s 窗口 5 次上传/60 次请求
- exporter Chrome 无 sandbox 回退
- `_parse_json()` 容错增强 + start.sh 预检/健康检查

### 迭代17：代码质量优化 (22 项)
- 提取共享模块: `prompts.py`, `constants.py`
- 消除重复: `_validate_and_parse()`, `prepare_review_text()`
- async 化: 所有 DB 调用 `asyncio.to_thread()` 包装
- CSS 变量体系 145 处替换 + PDF 标题检测

### 迭代18：标红匹配修复 (6-pass)
- 问题: 仅 1/20 匹配 (AI plain_text vs HTML 结构差异)
- Pass 4: 文档根级跨 `<table>` 匹配
- Pass 5: `\n` 拆分片段独立匹配
- Pass 6: 模糊匹配 (skip words)
- 效果: 1→17/20, 3→17/20, 6→37/37 (100%)，零回退

### 迭代19：多文件批量审核
- `POST /api/reports/upload/batch` + asyncio.Semaphore(3)
- 前端 BatchReviewer 卡片网格 + 自动模式识别 (单/多文件)
- SSE 事件: batch_start/progress/done

### 迭代20：Docker 容器化
- backend Dockerfile (Python 3.12-slim + chromium)
- frontend Dockerfile (多阶段: Node build → nginx)
- docker-compose.yml + 命名卷持久化

### 迭代21：一键部署脚本
- `deploy.sh`: 服务器端 (检查→配置→构建→启动→健康检查)
- `deploy-remote.sh`: 本机 rsync + 远程执行
- EMC_PORT 环境变量自定义端口

### 迭代22：性能优化与代码清理
- 修复: stream_stage1/2 阻塞事件循环 → `asyncio.to_thread()`
- api.ts 提取 `parseSSEStream()` 消除重复
- 死代码清理 + React.memo 修复

### 迭代23：统计仪表盘 + 报告对比
- `GET /api/reports/stats` + `get_stats()` (~80行聚合)
- StatsDashboard: Recharts 图表 + 骨架屏 + 空数据引导
- ReportComparison: 编辑距离匹配 + 已修复/新增/持续存在分类

### 迭代24：审核规则 + 标准库
- `review_rules` 表 + 完整 CRUD + prompt 注入 `{user_rules}`
- `emc_standards` 表 + 7 条内置标准 (CISPR 25 等) + 条款限值表格
- 前端 RulesManager (severity 色条 + 模态表单) + StandardsBrowser (可展开卡片 + 筛选)

### 迭代25：FTS5 全文搜索
- `review_items_fts` 虚拟表 (unicode61 tokenizer)
- `search_reports()`: GROUP BY 聚合 + snippet() `<mark>` 高亮
- 前端三模式切换: 文件名/全文检索/对比报告

### 迭代26：历史记录 UX 改进
- "查看"按钮修复 (handleHistorySelect 切换 tab)
- 按钮排版移入行内 + 对比模式手动开启 + 默认展开面板

### 迭代27：用户认证与权限
- `auth.py`: JWT HS256 + get_current_user + require_role
- users 表 + LoginPage (免密登录) + 401 拦截器自动登出
- 3 角色权限: admin/reviewer/viewer + 数据隔离

### 迭代28：人工标注系统
- ReviewItem 新增 5 种 human_status + human_comment + annotated_by
- `PATCH /api/reports/{id}/items/{idx}/annotation`
- 前端下拉标注 + 筛选切换 + 保存 spinner + 失败还原

### 迭代29：后台管理独立化
- Vite 多页面构建 (main + admin 入口)
- AdminApp: 全部报告 + 用户管理 + JWT 恢复 + admin 角色检查
- 主界面精简: 移除 admin tab

### 迭代30：误报抑制
- `suppression_patterns` 表 + `suppression_filter.py`
- difflib.SequenceMatcher (threshold 0.8) 自动标记 false_positive
- 标注 false_positive/ignored 时自动写入抑制模式

### 迭代31：批量 Excel 导出
- openpyxl: 汇总 Sheet + 每报告独立 Sheet
- `GET /api/reports/export/batch?ids=` (上限 50)
- 前端勾选框 + "导出已选 Excel (N)"按钮

### 迭代32：浏览器通知 + Toast
- `notify.ts`: Notification API + 权限请求
- 批量审核完成 → 系统通知 + 页面顶部绿色 toast (8s 消失)

### 迭代33：报告标签
- reports 表加 tags 列 + 上传/历史端点支持标签参数
- 前端标签输入框 + 历史标签筛选

### 迭代34：覆盖率面板 + 撤销 + 软删除
- 历史列表显示 N/M 已复核 (纯前端计算)
- 标注后 6s 可撤销 toast
- reports.is_deleted + restore_report + purge

### 迭代35：代码审查修复
- XSS: Admin AllReports DOMPurify 净化
- 角色校验: 所有写端点加 require_role
- SSE 中断: 组件卸载时 abort
- API 文档: 侧边栏加 /api/docs 链接

### 迭代36：版本管理 + 复审对比 + 实时同步
- 报告编号 EMC-YYYYMMDD-NNN，同编号自动折叠
- 上传时选历史报告 → LLM 自动对比 (fixed/new/persistent)
- 标注 since 时间戳并发控制 (409 Conflict)
- DeepSeek JSON 3 级修复 (缺逗号/未转义换行/截断恢复)

### 迭代37：代码审计与 UX 全面优化 (2026-05-25)
- 全量审查 ~10,000 行代码，发现 30+ 问题
- Token 泄露修复: 导出改为 axios blob
- Rate limiter 加 threading.Lock
- `datetime.utcnow()` → `datetime.now(timezone.utc)`
- Tab 切换 display:none 保持组件挂载
- 焦点样式 + reduced-motion + WCAG AA 对比度
- ErrorBoundary 组件防止白屏

### 迭代38：显示优化 (2026-05-25)
- 深色模式 prefers-color-scheme (20 CSS 变量)
- 响应式 768px + 打印样式 @media print
- 骨架屏 StatsDashboard + 审核页 header 重构
- 版本胶囊 Root/V2/V3

### 迭代39：上传流程重构 (2026-05-25)
- 先选文件 → "开始审核"按钮 → 流式进度
- 对比目标下拉框: 每 group 最新报告可选
- Material 设计上传栏 + 项目组下拉替换标签输入

### 迭代40：操作记录 + 删除优化 (2026-05-25)
- 审核结果页"操作记录"按钮 → 弹窗时间线 (姓名+工号)
- 我的报告弹窗加删除按钮 + root 级联删除
- 后端 timeline 端点返回用户姓名

### 迭代41：项目组管理 (2026-05-25)
- `project_groups` + `project_group_members` 表
- `/api/groups` CRUD + ProjectGroups 网格卡片 + 成员 chips + 模糊搜索
- UserManagement 加姓名列 (原地编辑)
- 后台管理独立为 `admin/` 项目 (port 5174)，移除内嵌 AdminApp

### 迭代42：侧边栏 + 设计系统 (2026-05-26)
- 主界面 + 后台管理统一侧边栏布局
- 收起 56px (仅图标) → 悬停展开 200px (图标+文字)
- 顶部 TOPBAR 页面标题

### 迭代43：全系统测试修复 (2026-05-26)
- 4 个 subagent 并行审查全部代码
- 修复 12 个 bug: create_user_async name 丢失、logger 未定义、无存在检查等
- Docker Compose 增加 admin 服务 + setup.sh 环境安装
- 操作记录颜色编码统一 (标注=紫/导出=绿/删除=红)

### 迭代44：数据一致性测试基础设施 (2026-05-27)

**测试框架与工具**
- pytest + pytest-asyncio + httpx.ASGITransport (直接测 ASGI app)
- 校验模式: API 响应 ↔ 数据库真实值 + reqId 日志链追踪
- 环境: 模块级 `os.environ` 注入 → 临时 SQLite + JSONL 日志 → `atexit` 清理

**测试环境 (conftest.py)**
- 6 个预置用户 (admin + reviewer×2 + viewer×2)
- 5 个 HTTP client (各角色 + 未认证)
- `db_conn` 直连 sqlite3.Row 验证数据库真实值
- `read_backend_logs` / `read_frontend_logs` 日志解析回调

**断言工具 (tests/utils.py)**
- `assert_api_db()`: 标准化 2-way 对比 (""→None, 浮点 6 位, dict 排序)
- `assert_log_chain()`: reqId 日志完整性验证
- `normalize()`: 递归规范化消除表示差异
- 数据工厂: `make_rule_payload()`, `make_group_payload()`, `make_minimal_report_json()`

**测试覆盖 (61 passed, 1 skipped)**

| 模块 | 用例 | 覆盖 |
|------|------|------|
| test_crud_reports | 10 | 历史/详情/标注/软删除/恢复 |
| test_crud_users | 8 | 创建/列表/me/角色/姓名/删除 |
| test_crud_rules | 5 | 规则 CRUD |
| test_crud_groups | 7 | 项目组 CRUD + 成员级联 |
| test_crud_standards | 5 | 标准 CRUD + JSON条款 + 内置保护 |
| test_crud_tags | 5 | 标签 CRUD + 空白裁剪 |
| test_crud_settings | 3 | 设置读写 + 部分更新保护 |
| test_concurrent | 5 | 并发标注竞态 + 令牌过期 + 顺序标注 |
| test_permissions | 14 | 4角色权限矩阵 |

**生产 Bug 发现与修复**
1. `save_rule_async` / `save_standard_async` — `**kwargs` 签名导致 POST 端点 500
2. `logger.py` LOG_DIR 硬编码 → 环境变量覆盖
3. 并发标注 read-modify-write 竞态记录 (全量 JSON 覆盖)

**涉及文件**: `conftest.py`(新), `utils.py`(新), `pytest.ini`(新), `test_crud_*.py`(新×7), `test_concurrent.py`(新), `test_permissions.py`(新), `requirements-test.txt`(新), `database.py`(修), `utils/logger.py`(修)

---

## 九、当前状态

### 已实现
- 上传审核: Word/PDF → 两阶段 AI → 抑制过滤 → 6-pass 标红 → 双栏展示 (SSE 流式)
- 批量审核: 多文件并行 (Semaphore(3)) + 浏览器通知 + Toast
- 版本管理: EMC-YYYYMMDD-NNN + AI diff 对比 + Root 版本胶囊折叠
- 导出: PDF (Chrome headless) / Word (python-docx) / Excel (openpyxl 批量)
- 搜索: keyword/结果/日期/标签筛选 + FTS5 全文搜索 (snippet `<mark>` 高亮)
- 人工标注: 5 种状态 + 6s 撤销 toast + 409 并发冲突检测
- 误报抑制: difflib 相似度过滤 (threshold 0.8)
- 审核规则: CRUD + prompt 注入 + 启用/禁用
- EMC 标准库: 7 条内置 (CISPR 25 等) + 自定义 + 条款限值表格
- 项目组: CRUD + 网格卡片 + 成员 chips + 模糊搜索
- 用户管理: JWT 3 角色 + 姓名原地编辑 + 401 拦截自动登出
- 统计仪表盘: Recharts 图表 + 骨架屏 + 30天趋势 + Top 10 位置
- 报告对比: 并排展示 + 编辑距离匹配 + 已修复/新增/持续分类
- UI: 深色模式 + 响应式 768px + 打印样式 + WCAG AA + ErrorBoundary
- 后台管理: 独立 SPA (port 5174) + 侧边栏 + 5 页面
- 部署: Docker Compose + deploy.sh 一键部署
- 测试: 61 用例 / 9 模块 / API↔DB 一致性 + reqId 日志追踪 + 并发/权限边界覆盖
- 安全: DOMPurify, 100MB 限制, 线程安全限流, CORS, blob 下载防 token 泄露

### 待优化
- 审核意见模板
- 向量语义相似度升级 (替代 difflib)
- 前端 Playwright E2E 测试 (UI↔API↔DB 三段式对比)
- 并发标注 read-modify-write 竞态修复 (per-item PATCH 替代全量 JSON 覆盖)
