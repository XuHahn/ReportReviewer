# 设计与技术文档索引

最后核对：2026-08-13。

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

## 维护约定

1. 现行行为以代码和自动化测试为准，计划稿必须标注状态。
2. 修改审核边界、LLM 策略、数据模型或用户流程时，同步更新本索引、根目录 `README.md` 和 `merge-log.md`。
3. 临时 UX 原型、调试输出、截图和生成报告统一放入 `tmp/`，不得提交。
