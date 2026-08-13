# UI/UX Redesign Prompt — EMC Report Review System

> 将此 prompt 完整发送给 ChatGPT（推荐 GPT-4o 或 Claude Sonnet/Opus），让它为你生成全新的前端代码。
> 生成结果放入 `frontend-v2/` 目录，作为影子版本，不修改现有 `frontend/` 的任何文件。
>
> **对 AI 的要求：请充分使用 web_search、web_fetch、Canvas/Artifact 等工具主动上网搜索最新最优秀的方案。先出设计稿确认方向，再写代码。不要只凭训练数据做决策。**

---

## 项目背景

这是一个 **EMC 检测报告智能审核系统**。用户上传四份业务文档（委托单、试验计划、原始记录、检测报告），系统自动提取文档内容，使用 LLM + 证据图算法进行四文档交叉一致性审核，最终生成问题清单，逐一展示原文证据供审核员人工裁决。

### 用户角色
- **审核员 (reviewer)**：主要使用者，上传文档、核查提取结果、审核机器发现的问题、做出人工裁决
- **管理员 (admin)**：管理用户、项目组、标准库、系统运维
- **标准审核员 (standard_reviewer)**：管理和发布测试标准知识库

### 核心业务流程
```
上传四份文档 → AI 提取文档内容 → 人工核查提取结果 → 锁定并提交审核
→ 证据图构建 + 交叉验证 → 逐条审核问题（查看原文证据）→ 人工裁决 → 退回修订或归档
```

---

## 技术约束

### 必须保留的核心功能：错误原图高亮显示

这是系统最重要的亮点功能，**必须在新的 UI 中完整保留**。

**工作原理：**
1. 后端 `document_preview.py` 的 `render_evidence_page()` 函数接收 PDF 文件、页码、证据原文 quote 和 bbox 坐标
2. 使用 PyMuPDF (fitz) 在原始 PDF 页面上精确定位证据文字位置
3. 在定位到的文字区域绘制**半透明黄色高亮矩形**（fill color: yellow at 24% opacity, border: red）
4. 将标注后的页面渲染为 PNG 图片返回前端
5. 前端在审核问题详情中展示这些高亮图片，让审核员一眼看到"问题出在原始文件的哪个位置"

**API 端点（保持不变）：**
```
GET /api/sets/{set_id}/review-runs/{graph_id}/evidence/{evidence_id}/preview?renderer=normalized-quote-v2
```
返回：带黄色高亮的 PNG 页面图片，响应头包含 `X-Evidence-Highlight-Strategy` 和 `X-Evidence-Highlight-Count`

**前端展示方式（可重新设计交互，但功能要保留）：**
- 审核问题详情中展示每个证据来源的高亮页面
- 支持缩放（zoom in/out）
- 点击可展开全屏 lightbox 查看大图
- 支持键盘操作（+/- 缩放，Esc 关闭）
- 多证据时可在 tab 间切换
- 同时展示提取的原文文本（exact_quote）

---

## 后端 API（可直接使用，也可重新组织）

以下是已有的后端 API，前端可以全部使用，也可以根据需要建议后端改动（但不能改动核心审核算法）。

### 认证
- `POST /api/auth/login` — 登录
- `GET /api/auth/me` — 当前用户信息

### 文档集管理
- `POST /api/sets` — 创建文档集
- `GET /api/sets` — 列出文档集
- `GET /api/sets/{id}` — 文档集详情（含文件状态、提取状态）
- `POST /api/sets/{id}/documents` — 上传文档（FormData: file + doc_type）
- `DELETE /api/sets/{id}/documents/{doc_id}` — 删除文档
- `POST /api/sets/{id}/lock` — 锁定文档集
- `POST /api/sets/{id}/revision` — 创建修订版本

### 文档提取
- `GET /api/sets/{id}/extractions` — 获取所有文档的 AI 提取结果
- `GET /api/sets/{id}/documents/{doc_id}/extraction` — 单个文档的提取结果
- `PATCH /api/sets/{id}/documents/{doc_id}/overrides` — 保存人工修改
- `POST /api/sets/{id}/documents/{doc_id}/retry-extraction` — 重试提取

### 审核运行
- `POST /api/sets/{id}/review-runs` — 启动审核
- `GET /api/sets/{id}/review-runs` — 列出审核运行
- `GET /api/sets/{id}/review-runs/{run_id}` — 获取审核快照（含 findings, evidence, nodes, edges, decisions）
- `PUT /api/sets/{id}/review-runs/{run_id}/findings/{finding_id}/decision` — 提交人工裁决
- `GET /api/sets/{id}/review-runs/{run_id}/evidence/{evidence_id}/preview` — **证据页高亮预览（关键）**
- `GET /api/sets/{id}/review-runs/{run_id}/export?format=xlsx|pdf|evidence` — 导出

### 标准库
- `GET /api/standards` — 标准列表
- `GET /api/standards/{id}/graph` — 标准条款图
- `POST /api/standards/{id}/graph/publish` — 发布标准
- `PUT /api/sets/{id}/standards` — 选择审核使用的标准

### 项目组/用户/标签
- `GET/POST /api/groups` — 项目组管理
- `GET/POST /api/users` — 用户管理
- `GET/POST /api/tags` — 标签管理

---

## 审核问题（Findings）的数据结构

```typescript
interface EvidenceGraphSnapshot {
  run: { graph_id, set_id, status, ... }
  evidence: EvidenceGraphEvidence[]   // 证据列表
  nodes: EvidenceGraphNode[]          // 证据图节点
  edges: EvidenceGraphEdge[]          // 证据图边
  findings: EvidenceGraphFinding[]    // 审核发现的问题
  decisions: Decision[]               // 人工裁决记录
}

interface EvidenceGraphEvidence {
  evidence_id: string
  doc_id: string
  doc_type: 'order_form' | 'test_plan' | 'original_records' | 'final_report'
  filename: string
  page_number: number
  sheet_name?: string
  cell_range?: string
  bbox: number[]                      // 定位坐标 [x0, y0, x1, y1]
  exact_quote: string                 // 提取的原文
  extraction_method: string
  metadata: Record<string, any>       // 含 verdict, role_label, comparison_rows 等
}

interface EvidenceGraphFinding {
  finding_id: string
  check_id: string                    // 检查规则编号，如 GRAPH-COVERAGE-001
  status: 'confirmed_pass' | 'confirmed_error' | 'unresolved' | ...
  severity: 'error' | 'warning' | 'info'
  title: string
  description: string
  subject_node_ids: string[]
  evidence_ids: string[]              // 关联证据 ID 列表
  metadata: Record<string, any>       // 含 missing_docs, values_by_doc_type, comparison_rows 等
}
```

**问题分类逻辑（前端 presentation 层）：**
- **资料缺失 (missing)**：check_id === 'GRAPH-COVERAGE-001'，计划要求但找不到对应原始记录/报告
- **内容不一致 (conflict)**：四份文档中同一信息存在矛盾
- **报告未填写完整 (incomplete)**：签名、必填字段、目录页码等缺失
- **需要人工确认 (confirm)**：系统无法自动判断，需人工裁决
- **证据链完整 (complete)**：已确认通过的项目

---

## 设计要求

### 1. 整体风格
- 现代、简洁、专业的企业级 SaaS 界面
- 清晰的信息层级，减少视觉噪音
- 使用柔和的中性色调 + 功能性强调色（红=错误，黄=警告，绿=通过，蓝=操作）
- **支持深色模式**
- 响应式设计，桌面端为主要目标（1920px 基准），同时兼容平板
- 中文为主要语言，关键术语可附带英文

### 2. 首页 / 仪表盘
- 替换当前的侧边栏+任务列表布局
- 设计一个真正有用的仪表盘：
  - 待处理任务数（按状态分组）
  - 最近的审核任务
  - 审核通过率趋势
  - 常见问题类型分布
- 一键「新建审核」的醒目入口
- 任务列表支持搜索、筛选（状态、项目组、日期）

### 3. 审核工作流（核心页面）
重新设计从上传到裁决的完整流程，让普通用户能直观理解每一步：

**步骤 1：上传资料**
- 拖拽上传区域，4 个文档槽位清晰展示
- 每个槽位显示文档类型图标、名称、格式要求
- 上传后显示文件大小、页数、实时提取进度
- 可选的标准库选择（清晰的卡片式选择器）

**步骤 2：核查提取结果**
- 并排视图：左侧原始文档预览，右侧 AI 提取的字段
- 每个字段显示置信度指示器（高=绿色，中=黄色，低=红色）
- 点击字段可编辑修正
- 逐份文档核查，完成后打勾

**步骤 3：审核结果与裁决**（这是最复杂的页面）
- 顶部摘要栏：总问题数、严重/警告/提示分类统计、已处理进度条
- 左侧问题列表：
  - 按分组折叠（资料缺失 / 不一致 / 未完成 / 需确认）
  - 每条显示：严重级别图标、标题、简短描述、处理状态
  - 支持搜索和筛选
  - 已处理/未处理状态清晰区分
- 右侧问题详情：
  - 问题标题 + 严重级别标签
  - 「系统发现了什么」— 用通俗语言解释问题
  - **证据展示区（核心亮点）**：
    - 四文档的卡片式布局，突出哪份文档有问题
    - **带黄色高亮的原始页面截图**（使用 evidence preview API）
    - 缩放控制和全屏查看按钮
    - 原文摘录，关键词高亮（Pass/Fail 等）
    - 结构化对比表格（当有 comparison_rows 时自动展示）
  - 「这意味着什么」— 业务影响说明
  - 「建议处理方式」— 操作建议
  - 处理按钮组：
    - 要求修改报告
    - 要求补充原始记录
    - 要求核对并修正资料
    - 本次不适用
    - 机器判断有误
    - 暂缓判断
  - 处理依据文本框
  - 保存按钮

**步骤 4：完成与导出**
- 显示裁决进度
- 一键退回修订（生成修订版本）
- 导出 PDF 报告 / Excel 明细 / 证据包 ZIP
- 审核完成确认页

### 4. 标准库管理
- 标准列表页（可搜索、筛选）
- 标准详情页：条款树状导航 + 要求列表
- 知识化状态指示器
- 逐条确认/驳回界面

### 5. 导航设计
- 顶部导航栏（而非侧边栏）：Logo、主导航链接、用户头像下拉菜单
- 面包屑导航
- 页面标题清晰

### 6. 交互细节
- 所有可点击元素有 hover/active 状态
- 加载状态使用 skeleton 占位符，而非空白页面
- 操作反馈使用 toast 通知
- 危险操作需要二次确认
- 长列表使用虚拟滚动或分页
- 键盘快捷键支持（如 Esc 关闭弹窗）
- 错误状态有友好的空状态插图和文案

---

## ⚡ 执行时自行上网搜索优化（非常重要）

**在编写每一部分代码之前，请先上网搜索最新的优质方案，不要只依赖 prompt 中的建议。** 搜索目标包括但不限于：

### 搜索优秀组件库和设计资源
- 搜索 "best React UI component libraries 2026" — 寻找比 shadcn/ui 更优秀或更适合本项目的新兴组件库
- 搜索 "modern React dashboard design inspiration 2026" — 寻找仪表盘设计灵感
- 搜索 "best React file upload component drag drop 2026" — 寻找最佳的文件上传组件
- 搜索 "React PDF viewer component with annotation highlights 2026" — 寻找可标注的 PDF 查看器（可能与证据高亮功能结合）
- 搜索 "best React state management 2026" — 确认最新的状态管理最佳实践
- 搜索 "React step wizard component accessible 2026" — 寻找审核工作流的步骤向导组件
- 搜索 "Tailwind CSS component library comparison 2026" — 对比各类 Tailwind 组件库
- 搜索 "best React table component virtual scroll 2026" — 寻找最佳表格/数据展示组件
- 搜索 "React dark mode theme system best practice 2026" — 深色模式最佳实践
- 搜索 "React skeleton loading animation library 2026" — 寻找骨架屏加载方案

### 搜索交互模式和 UX 最佳实践
- 搜索 "enterprise document review UI UX best practices" — 企业级文档审核界面的 UX 模式
- 搜索 "split panel layout React resizable 2026" — 可拖拽分割面板组件
- 搜索 "React keyboard shortcuts hook library 2026" — 键盘快捷键方案
- 搜索 "React toast notification library 2026" — 通知/toast 最佳方案
- 搜索 "breadcrumb navigation UI pattern enterprise 2026" — 面包屑导航模式
- 搜索 "multi-step form React best practices 2026" — 多步骤表单最佳实践
- 搜索 "React diff viewer component text comparison 2026" — 文档版本差异对比组件
- 搜索 "accessible modal dialog React best practices" — 无障碍弹窗最佳实践

### 搜索动画和微交互方案
- 搜索 "React micro interaction animation library 2026" — 微交互动画库
- 搜索 "page transition animation React 2026" — 页面过渡动画
- 搜索 "React confetti celebration animation 2026" — 完成任务时的庆祝动画
- 搜索 "React progress bar stepper animation 2026" — 进度条和步骤动画

### 使用 Skills 和 Tools 辅助开发
- **使用 web_search / web_fetch 工具主动上网搜索**，不要只凭训练数据做决策
- **使用 Canvas / Artifact 工具先出设计稿**，把关键页面（仪表盘、审核工作台、证据详情）的 HTML+CSS 设计稿先生成出来给我确认，确认后再生成 React 代码
- 如果可用，使用 **Skill 工具**加载前端设计相关的 skill（如 web-artifacts-builder、frontend-design 等）
- 搜索 "shadcn/ui latest components 2026" — 确认 shadcn/ui 的最新组件列表和用法
- 搜索 "v0.dev beautiful dashboard design" — 参考 v0.dev 等 AI 设计工具生成的仪表盘范例
- 搜索 "best React icon library 2026 lightweight" — 寻找最佳图标库

### 决策原则
- 每次选择技术方案时，上网对比至少 3 个备选方案再做决定
- 优先选择有活跃维护、社区广泛使用、TypeScript 支持完善的方案
- 美观优先：在功能相同的情况下，选择视觉效果更好的方案
- 不要因为 prompt 写了 shadcn/ui 就只用它——如果搜到更好的方案，大胆替换

---

## 技术方案建议（仅作起点参考，请上网搜索后自行决策）

### 前端技术栈（建议搜索对比后决定）
- React 18+ TypeScript + Vite
- 状态管理：搜索对比 Zustand / Jotai / Legend State 后选择
- UI 框架：搜索对比 shadcn/ui / Radix Themes / Mantine / Ant Design 后选择
- 样式方案：Tailwind CSS 或 Panda CSS（搜索对比）
- 图表：搜索对比 Recharts / Tremor / Nivo 后选择
- HTTP：axios 或 TanStack Query（搜索对比）
- 路由：React Router v6 或 TanStack Router（搜索对比）
- 表单：React Hook Form + Zod 验证
- 动画：搜索 Framer Motion / Motion One / react-spring 后选择

### 目录结构（建议，可自行调整）
```
frontend-v2/
├── src/
│   ├── components/
│   │   ├── ui/           # shadcn/ui 基础组件
│   │   ├── layout/       # AppLayout, TopNav, Breadcrumb
│   │   ├── dashboard/    # Dashboard 相关组件
│   │   ├── review/       # 审核工作流组件
│   │   │   ├── UploadStep.tsx
│   │   │   ├── ExtractionReview.tsx
│   │   │   ├── FindingsWorkspace.tsx
│   │   │   ├── EvidenceCard.tsx      # 证据卡片+高亮图
│   │   │   ├── EvidenceLightbox.tsx  # 全屏查看
│   │   │   ├── ComparisonTable.tsx   # 结构化对比表
│   │   │   ├── DecisionPanel.tsx     # 人工裁决面板
│   │   │   └── CompletionStep.tsx
│   │   ├── standards/    # 标准库
│   │   └── shared/       # 共享组件
│   ├── hooks/            # 自定义 hooks
│   ├── lib/              # 工具函数、API 客户端
│   ├── pages/            # 页面组件
│   ├── stores/           # 状态管理
│   ├── types/            # TypeScript 类型（可从旧前端复用）
│   └── App.tsx
├── package.json
├── vite.config.ts
├── tailwind.config.ts
└── tsconfig.json
```

### 后端改动建议（可选）
如果能让整体体验更好，可以建议以下后端改动：
1. 添加 SSE 端点推送审核进度
2. 添加文档上传的预签名 URL 支持（大文件上传优化）
3. 添加批量操作 API（批量裁决）
4. 证据预览图增加缓存头优化加载

---

## 特别注意

1. **不要改动核心审核算法**：`backend/services/unified_review_pipeline.py`、`evidence_graph_review_engine.py`、`evidence_graph_extraction.py`、`evidence_graph_coverage.py`、`evidence_graph_relationships.py` 等核心算法文件保持不变。

2. **证据高亮 API 保持不变**：`GET /api/sets/{id}/review-runs/{graph_id}/evidence/{evidence_id}/preview` 的请求/响应格式不变。

3. **生成影子版本**：所有新代码放入 `frontend-v2/` 或新的独立目录，不要覆盖 `frontend/` 的现有文件。后端如有改动，通过新增 router 或 service 实现，不要修改现有核心文件。

4. **保留所有现有功能**：上传、提取、核查、审核、裁决、导出、标准库、项目组、用户管理等功能都要覆盖。

5. **中文界面**：所有用户可见的文案使用中文。

6. **可访问性**：遵循 WCAG 2.1 AA 标准，支持键盘导航，适当的 ARIA 标签。

---

## 工作流程

**重要：在写任何代码之前，请先完成以下准备工作：**

1. **先浏览设计参考**：搜索 "enterprise SaaS dashboard UI design 2026" 等关键词，浏览 Dribbble、Behance 上的实际设计案例截图，建立审美参考系
2. **出设计稿给我确认**：用 HTML/CSS 先做出 3-4 个关键页面的静态设计稿（仪表盘首页、审核工作台-问题详情、证据高亮弹窗、上传资料页），让我确认视觉方向后再开始写 React 代码
3. **选型确认**：搜索对比后列出最终选择的技术栈清单，让我确认后再开始

---

## 输出要求

请按以下阶段输出，每个阶段开始前先上网搜索最佳方案：

### Phase 0：设计稿确认（先做这一步）
1. 搜索浏览最新设计参考，建立审美方向
2. 用 HTML/CSS 出以下页面的静态设计稿：
   - 仪表盘首页
   - 审核工作台（问题列表 + 证据详情双栏）
   - 证据高亮弹窗/灯箱
   - 上传资料页面
3. 等待我确认设计方向

### Phase 1：基础框架
1. `frontend-v2/` 的项目初始化（package.json, vite.config, tailwind.config, tsconfig）
2. AppLayout（顶部导航 + 内容区）
3. 路由配置
4. API 客户端（复用 + 优化现有 api.ts）
5. TypeScript 类型定义
6. 主题系统（颜色、字体、间距、圆角、阴影）

### Phase 2：仪表盘
1. 统计卡片组件
2. 任务列表
3. 趋势图

### Phase 3：审核工作流
1. 上传步骤（拖拽上传卡片）
2. 提取核查步骤（并排视图）
3. **审核结果与裁决步骤**（包含证据高亮图的完整实现）
4. 完成与导出步骤

### Phase 4：标准库 + 管理页
1. 标准库浏览器
2. 项目组管理
3. 用户管理

请确保每个 Phase 的代码都可以独立运行和测试。使用 Mock 数据以便在没有后端的情况下预览 UI。

---

## 现有代码参考

以下关键文件可作为实现参考（不需要全部阅读，ChatGPT 可通过附件或引用查看）：
- `frontend/src/api.ts` — API 客户端和所有端点
- `frontend/src/types.ts` — TypeScript 类型定义
- `frontend/src/components/ReviewIssueWorkspace.tsx` — 当前审核问题工作台（含证据高亮展示逻辑）
- `frontend/src/components/reviewIssuePresentation.ts` — 问题分类和展示逻辑
- `backend/services/document_preview.py` — 证据页高亮渲染
- `backend/routers/document_set.py` — API 端点定义
