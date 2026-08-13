# 设计与技术文档索引

最后核对：2026-08-11。

## 现行规范

| 文档 | 状态 | 内容 |
|---|---|---|
| [统一证据图审核重构](unified-evidence-graph-review-plan-20260728.md) | 已实施 | 单一图审核主链、通用检查、模型职责、UI/UX、数据重建与验收 |
| [证据来源锚点与历史预览缓存](evidence-anchor-preview-cache-20260811.md) | 已实施 | 提取期坐标、历史锚点补充、版本化 PNG/ETag 缓存与清理边界 |
| [审核算法整改](audit-algorithm-revision-20260714.md) | 历史参考 | 旧报告驱动阶段的规则边界与问题经验 |
| [LLM Prompt 与调用策略](llm-prompt-policy-20260715.md) | 现行 | JSON Output、模型/Thinking 分工、证据门禁 |
| [提取 Debug 日志](extraction-debug-logging.md) | 现行 | 文档提取日志字段和排障要求 |
| [管线可观测性](pipeline_observability_requirements.md) | 现行 | 审核阶段、运行指标和失败状态 |
| [原始记录混合提取](raw-record-hybrid-extraction-20260715.md) | 历史参考 | 代码表格清点、模型转录和质量门禁经验 |
| [标准知识库方案](standard-knowledge-base-plan-20260714.md) | 已实施三期 | 双语要求、不可变发布、BGE-M3 混合检索、人工映射和引用门禁 |
| [全代码审计整改](code-audit-remediation-20260717.md) | 历史参考 | 统一证据图重构前的 API、权限和可靠性整改记录 |

## 数据与变更记录

| 文档 | 状态 | 内容 |
|---|---|---|
| [线上数据集语料](online_cases_corpus.md) | 持续维护 | 高置信度数据集来源、标签和限制 |
| [合入记录](merge-log.md) | 持续维护 | 重要功能、修复和验证命令 |

## UX 预览

预览只用于设计决策，不参与应用构建：

- [四文档核查界面](previews/four-doc-review-ux-options.html)
- [修订复审流程](previews/rereview-flow-preview.html)
- [审核结果表格](previews/review-result-table-preview.html)
- [标准库与知识化](previews/standard-library-knowledge-ux-preview.html)
- [试验计划核查方案](previews/test-plan-review-ux-options.html)
- [统一审核工作台 UX 套件（从上传到结果）](previews/ux-suite/index.html)
- [前端影子版本 V2 设计说明](frontend-v2-shadow-design.md)

## 维护约定

1. 现行行为以代码和自动化测试为准，计划稿必须标注状态。
2. 修改审核边界、LLM 策略、数据模型或用户流程时，同步更新本索引、根目录 `README.md` 和 `merge-log.md`。
3. UX HTML 统一放入 `design/previews/`，不得再次散落到项目根目录。
4. 调试输出、截图和生成报告放入 `tmp/`，该目录不提交。
