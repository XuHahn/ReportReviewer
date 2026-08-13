# EMC 报告智能审核系统

系统以四份业务资料构建统一证据图，并从测试计划定义的范围出发，核验委托单、原始记录和检测报告之间的完整性与一致性。测试标准是可选参考来源，只使用人工确认并发布的要求，不自动扩大测试计划范围。

## 审核原则

- 四份必需资料：委托单、测试计划、原始记录、检测报告。
- 委托单提供委托与样品基本事实；测试计划定义“测什么、怎么测”；原始记录展开逐项执行；检测报告汇总发布结果。
- 测试计划定义应做项目；原始记录与检测报告必须形成双向覆盖证据。
- 业务上不预设测试项父子层级。计划明确列出的项目才是基础测试项；P2、P2a、P2b、模式、样品和轮次等未独立列项时只作为执行明细或覆盖维度。
- 原生文本和表格解析是首选；仅在扫描页、复杂表格或定向补证时调用千问视觉模型。
- DeepSeek/本地文本模型负责结构化提取、身份关系提议与反驳、语义检查和标准提醒。
- 每个问题必须关联原文证据；提取不完整、身份未闭合或证据不足时只能进入待确认，不能自动判错或判通过。
- 人工裁决独立保存，不覆盖机器结论和决策链。

详细设计见 [design/README.md](design/README.md) 和 [统一证据图审核方案](design/unified-evidence-graph-review-plan-20260728.md)。

## 启动

```bash
cp backend/.env.example backend/.env
# 填写 DeepSeek 与千问视觉端点配置
./start.sh
```

启动顺序为后端 → 前端 → 本地视觉模型。后端和页面会先可用，千问视觉模型在后台异步加载；页面顶部显示“视觉模型准备中”，就绪后自动更新，无需刷新页面。

正式部署时可将 `UNIFIED_REVIEW_START_LOCAL_VISION=false`，把 `UNIFIED_REVIEW_VISION_BASE_URL` 指向独立常驻的 OpenAI 兼容视觉服务；后端只做异步健康探测和请求连接，不再负责模型生命周期。

默认地址：

- 审核前端：<http://localhost:5174>
- 后端 API：<http://localhost:8000>
- API 文档：<http://localhost:8000/docs>

本地 Apple Silicon 千问视觉运行时：

```bash
scripts/model_runtime/bootstrap_dev.sh
scripts/model_runtime/start_qwen_vision.sh
```

## 模型路由

关键配置在 `backend/.env`：

```dotenv
UNIFIED_REVIEW_PROVIDER_MODE=hybrid
UNIFIED_REVIEW_VISION_BASE_URL=http://127.0.0.1:8081/v1
UNIFIED_REVIEW_VISION_MODEL=mlx-community/Qwen3-VL-8B-Instruct-4bit
UNIFIED_REVIEW_START_LOCAL_VISION=true
VISION_READY_TIMEOUT_SEC=600
UNIFIED_REVIEW_TEXT_BASE_URL=
UNIFIED_REVIEW_TEXT_MODEL=
```

`hybrid` 使用本地视觉模型 + DeepSeek 文本；配置本地文本端点后可使用 `local_only`。18GB Apple Silicon 开发机默认配置 8B 4-bit 与 12K KV；更小内存机器可改回 4B 3-bit/8K。系统只对原生解析不足的页面调用视觉模型，不重复运行全页 OCR。

历史证据页使用版本化私有缓存：首次生成 PNG 后，重复打开会命中服务端缓存或通过 ETag 返回 304，同时仍执行用户和文档集权限检查。生产环境可设置 `DOCUMENT_PREVIEW_CACHE_DIR` 指向持久化高速磁盘，并通过 `DOCUMENT_PREVIEW_CACHE_TTL_SECONDS`、`DOCUMENT_PREVIEW_CACHE_MAX_BYTES` 控制保留周期和空间上限。来源锚点与失效规则见 [证据来源锚点与历史预览缓存](design/evidence-anchor-preview-cache-20260811.md)。

## 验证

```bash
cd backend && pytest -q tests/
cd ../frontend-v2 && npm run build
```

普通测试不会调用付费模型。真实资料集必须显式配置模型服务后运行。

## 主要代码

- `backend/services/unified_review_pipeline.py`：统一审核编排。
- `backend/services/evidence_graph_review_engine.py`：证据图审核引擎。
- `backend/services/evidence_graph_extraction.py`：原生优先与定向视觉补证。
- `backend/services/evidence_graph_coverage.py`：测试计划、执行、报告覆盖核验。
- `backend/services/evidence_graph_relationships.py`：关系提议与反驳门禁。
- `backend/services/evidence_graph_store.py`：证据图、结论、事件和人工裁决持久化。
- `backend/services/evidence_graph_standard_advisory.py`：已发布标准的提醒检查。
- `frontend-v2/src/pages/ReviewWorkspace.tsx`：统一审核工作台与四阶段路由。
- `frontend-v2/src/pages/review/FindingsStage.tsx`：原图证据与人工裁决工作台。
