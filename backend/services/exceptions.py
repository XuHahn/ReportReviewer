"""Shared exceptions for the extraction and validation pipeline."""


class ExtractionError(Exception):
    """Raised when document extraction fails after retries."""
    def __init__(self, message: str, failures: list[dict] | None = None):
        super().__init__(message)
        self.failures = failures or []


class SizeLimitError(Exception):
    """Raised when file exceeds max size."""
    def __init__(self, filename: str, size_bytes: int, limit_mb: int = 100):
        super().__init__(f"文件 {filename} ({size_bytes/1024/1024:.1f}MB) 超过限制 ({limit_mb}MB)")
        self.filename = filename; self.size_bytes = size_bytes; self.limit_mb = limit_mb
