"""Bounded upload readers shared by API routes."""

from fastapi import HTTPException, UploadFile


async def read_upload_limited(
    file: UploadFile,
    max_bytes: int,
    *,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    """Read an upload incrementally and reject it before unbounded buffering."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, detail="文件超过上传大小限制")
        chunks.append(chunk)
    return b"".join(chunks)
