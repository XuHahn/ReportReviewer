"""Shared severity, result, and display constants."""

# Severity -> hex color (for HTML/CSS)
SEV_COLORS = {"error": "#b42318", "warning": "#b54708", "info": "#175cd3"}

# Severity -> Chinese labels
SEV_LABELS = {"error": "错误", "warning": "警告", "info": "注意"}

# Overall result -> Chinese labels (used in exports and audit summaries)
RESULT_LABEL = {"pass": "审核通过", "fail": "审核不通过", "warning": "需关注"}

# Severity -> RGBColor values (for python-docx)
from docx.shared import RGBColor
SEV_RGB = {
    "error": RGBColor(180, 35, 24),
    "warning": RGBColor(181, 71, 8),
    "info": RGBColor(23, 92, 211),
}
