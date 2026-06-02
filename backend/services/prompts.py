"""Prompt templates, token limits, and section classification keywords."""

# ── Stage 1: coarse scan ──────────────────────────────────────────────
SCAN_PROMPT = """
你是一位EMC检测领域的专业审核专家。请**快速扫描**以下测试报告全文，列出所有疑似的、需要仔细核查的问题点。

## 报告目录结构
{toc}

## 审核标准
1. 限值合规性：测试数据是否在标准限值范围内
2. 数据一致性：报告内数据之间是否逻辑自洽
3. 结论逻辑性：测试结论是否与数据相符
4. 格式规范性：单位符号、术语是否与标准一致
{user_rules}

## 报告全文
{report_content}

## 要求
- 对照目录逐章扫描，不遗漏任何章节
- 跨章节交叉比对（如前文限值 vs 后文实测数据）
- 每个问题只写一句简短描述（flag），不需要详细说明

## 输出格式
严格返回JSON（不要markdown围栏）：
{{
    "checklist": [
        {{
            "severity": "error",
            "location": "章节位置",
            "flag": "一句话描述问题"
        }}
    ]
}}
无问题则 checklist 为空数组。
"""

# ── Stage 2: detailed review ──────────────────────────────────────────
REVIEW_PROMPT = """
你是一位EMC检测领域的专业审核专家。以下是第一轮扫描发现的**问题清单**，请逐条进行深度审核。

## 报告目录结构
{toc}

## 第一轮扫描清单
{checklist}

## 报告全文
{report_content}

## 审核标准
1. 限值合规性：测试数据是否在标准限值范围内
2. 数据一致性：报告内数据之间是否逻辑自洽
3. 结论逻辑性：测试结论是否与数据相符
4. 格式规范性：单位符号、术语是否与标准一致
{user_rules}
{standard_limits}

## 要求
- 逐条核实清单中的每个问题：如果确认是问题，填写完整详情；如果判断不构成问题，跳过该项
- 在核实清单的同时，再次通读全文，补充第一轮可能遗漏的问题
- original_text 必须从报告中**逐字摘录**，不得改写

## 输出格式
严格返回JSON（不要markdown围栏）：
{{
    "overall_result": "pass",
    "review_items": [
        {{
            "severity": "error",
            "location": "章节或段落位置",
            "original_text": "报告中原文片段（精确摘录）",
            "error_description": "错误描述及为什么错",
            "standard_reference": "依据标准及条款",
            "suggestion": "修改建议"
        }}
    ],
    "summary": "审核总结"
}}

## 分级说明
- error：限值超标、结论与数据严重矛盾、标准引用根本性错误
- warning：数据异常需复核、格式不规范、术语不标准
- info：优化建议、最佳实践提醒
- 无问题则 review_items 为空数组
"""

# ── Token management ──────────────────────────────────────────────────
MAX_CONTEXT_TOKENS = 120000       # deepseek-chat 128K context, 8K margin
PROMPT_OVERHEAD_CHARS = 1500      # fixed char overhead from prompt template


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 1.8))


# ── Section priority classification ───────────────────────────────────
# Keywords are case-insensitive, matched against heading + first 200 chars of content

HIGH_KEYWORDS = [
    'test result', '测试结果', 'test data', '测试数据',
    '判定', '结论', 'conclusion', 'verdict',
    'compliance', 'compliant', '符合', '不符合',
    '限值', 'limit', 'measurement', '实测', 'measured',
    'summary of testing', 'test item', 'test date',
]

MEDIUM_KEYWORDS = [
    'standard', '标准', 'method', '方法', 'procedure', '步骤',
    'equipment', '设备', 'instrument', '仪器',
    'rating', '额定', 'classification', '分类',
    'safeguard', 'protection', 'insulation', '绝缘',
    'temperature', '温升', 'heating',
]

LOW_KEYWORDS = [
    'attachment', '附件', 'appendix', '附录',
    'photo', '照片', 'picture', '图片',
    'disclaimer', '声明', 'declaration',
    '资质章', 'logo', 'cover', '封面',
    'blank', '空白', 'this page is',
    'european group difference', 'national difference',
]
