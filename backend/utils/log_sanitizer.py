import re

# field names whose values should be masked
SENSITIVE_FIELDS = frozenset({
    'customer_name', 'device_id', 'device_sn', 'phone', 'email',
    'report_content', 'original_text', 'file_content',
})

# regex patterns -> replacement
SENSITIVE_PATTERNS = [
    (re.compile(r'1[3-9]\d{9}'), '***PHONE***'),
    (re.compile(r'[\w.\-]+@[\w.\-]+\.\w{2,}'), '***EMAIL***'),
    (re.compile(r'[A-Z]{2}\d{6,}'), '***DEVICE***'),
]

_MAX_VALUE_LEN = 1000


def _mask_value(v: str) -> str:
    if len(v) <= 6:
        return v[0] + '***'
    return v[:2] + '***' + v[-2:]


def sanitize(_logger, _method_name, event_dict: dict) -> dict:
    # 1. mask sensitive field values in ctx
    ctx = event_dict.get('ctx')
    if isinstance(ctx, dict):
        for k in list(ctx):
            if k.lower() in SENSITIVE_FIELDS and isinstance(ctx[k], str):
                ctx[k] = _mask_value(ctx[k])

    # 2. apply regex patterns to all string values
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            for pattern, replacement in SENSITIVE_PATTERNS:
                if pattern.search(value):
                    value = pattern.sub(replacement, value)
            # 3. truncate long values
            if len(value) > _MAX_VALUE_LEN:
                value = value[:_MAX_VALUE_LEN] + '...[truncated]'
            event_dict[key] = value

    return event_dict
