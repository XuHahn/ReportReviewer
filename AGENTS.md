# AGENTS.md

Codex 和其他编码代理在本仓库中的工作约束。

## 项目概要

EMC 报告智能审核系统是四文档一致性审核平台：委托单、试验计划、原始记录、检测报告为四份必需资料，测试标准为可选知识来源。当前主流程是报告驱动审核，不再使用 v1 单报告粗扫/详审流程。

完整架构、启动方式和数据流见 `README.md`；现行设计文档见 `design/README.md`。

## 关键代码

- `backend/routers/document_set.py`：文档集上传、提取、修订、锁定、审核、导出和指标 API。
- `backend/services/pipeline.py`：审核编排、运行指标和结果合并。
- `backend/services/report_driven_validator.py`：报告驱动的主审核算法。
- `backend/services/cross_validator.py`：过渡期保留的报告内部确定性检查。
- `backend/services/test_item_identity_resolver.py`：测试项身份 LLM 提议与复核。
- `backend/services/deepseek_client.py`、`llm_policy.py`：DeepSeek JSON Output 与任务策略。
- `backend/services/standard_knowledge.py`、`standard_graph.py`：标准分块、条款关系化和人工发布。
- `frontend/src/components/DocumentSetUploader.tsx`：审核主界面。
- `frontend/src/components/DocumentReviewModal.tsx`：四文档提取结果核查。
- `frontend/src/components/StandardsBrowser.tsx`：标准库。
- `admin/src/components/DocumentSetManager.tsx`、`ValidationIssueManager.tsx`：后台文档集与问题管理。

## 当前业务边界

- 委托单以代码提取为主；试验计划和检测报告依赖 LLM 理解不固定模板；原始记录使用代码清点与 LLM 逐表转录的混合方式。
- 报告项目与原始记录必须互为全集，多或少都报错；测试计划定义要求范围。
- 名称标准化允许，测试项身份不使用无约束模糊匹配。无法确定时由 LLM 两阶段判断，仍需原文证据。
- 每条问题必须展示原文证据。无证据不能自动判错或判通过。
- 不做企业字段格式合法性检查；不重复验证仪器生成数据点的真实性。
- 未选择标准时跳过标准条款审查并提示；选择标准时只使用已人工确认和发布的要求。
- 标准要求冲突直接生成问题，最终由审核人员确认。

## 工程约束

- 先阅读现有实现、日志和测试，再改代码；不要根据文件名或旧文档猜测行为。
- 使用 `rg`/`rg --files` 搜索。手工修改文件使用 `apply_patch`。
- 保持改动范围清晰，不还原用户已有修改，不清理真实数据库、日志或样本数据。
- 新增 LLM 调用必须经过 `DeepSeekReviewer._call_api` 并声明 `task_kind`；静态规则/schema 放 system，动态数据放 user。
- LLM 结果必须经过完整 JSON、Pydantic/schema、原文证据和业务数量门禁；失败不能伪装成审核通过。
- 日志不得记录客户原文、完整 prompt 或完整模型响应；记录长度、哈希、token、缓存和解析状态。
- 新增审核检查必须有稳定 `check_id`、去重规则、来源证据、严重级别和对应测试。

## 验证命令

```bash
cd backend && pytest -q tests/
cd frontend && npm run build
cd admin && npm run build
```

测试数量会随功能变化，不在本文件硬编码。真实/线上数据集测试必须显式开启，普通单元测试不能默认调用付费 API。

## Agent 要求

### 核心交互

- 制定计划或执行任务时，遇到无法从代码、日志和数据确认的业务分歧，必须立即向用户提问，禁止自行假设。
- 解决问题必须考虑通解，不得只针对单个报告或单个厂商写死。
- 每次回复结束时打印“结束了喵”。

### Debug 与日志

- 所有 Debug 必须结合日志分析。
- 如果现有日志不足，先把可观测性需求写入 `design/`，并询问用户是否需要实现相应日志。

### 测试数据

- 正式报告：[报告书_E202508277046-1EN(RD20).docx](example/test_set_04/%E6%8A%A5%E5%91%8A%E4%B9%A6_E202508277046-1EN%28RD20%29.docx)
- 原始记录：[E202508277046原始记录.zip](example/test_set_04/E202508277046%E5%8E%9F%E5%A7%8B%E8%AE%B0%E5%BD%95.zip)
- 委托单：[EMC测试申请表E202508277046（委托单）.xls](example/test_set_04/EMC%E6%B5%8B%E8%AF%95%E7%94%B3%E8%AF%B7%E8%A1%A8E202508277046%EF%BC%88%E5%A7%94%E6%89%98%E5%8D%95%EF%BC%89.xls)
- 测试计划：[ITW-平台执行器用280电机试验大纲251014Rev1.xlsx](example/test_set_04/ITW-%E5%B9%B3%E5%8F%B0%E6%89%A7%E8%A1%8C%E5%99%A8%E7%94%A8280%E7%94%B5%E6%9C%BA%E8%AF%95%E9%AA%8C%E5%A4%A7%E7%BA%B2251014Rev1.xlsx)
- 含标准的高置信度套件：`example/HC_E202605287495/`
