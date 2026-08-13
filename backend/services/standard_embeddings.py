"""Pluggable multilingual embeddings for immutable standard releases."""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from array import array
from typing import Any

import database
from utils.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_BACKEND = os.getenv("STANDARD_EMBEDDING_BACKEND", "bge-m3").strip().lower()
EMBEDDING_MODEL = os.getenv("STANDARD_EMBEDDING_MODEL", "BAAI/bge-m3").strip()
HASH_TEST_DIMENSIONS = 256
_BGE_MODEL: Any | None = None
_BGE_MODEL_LOCK = threading.Lock()


def _requirement_text(requirement: dict[str, Any]) -> str:
    parameters = "; ".join(
        " ".join(str(parameter.get(key) or "") for key in (
            "name", "symbol", "comparator", "value", "value_min", "value_max", "unit", "raw_text",
        )).strip()
        for parameter in requirement.get("parameters") or []
    )
    return "\n".join(filter(None, [
        str(requirement.get("test_item") or ""),
        str(requirement.get("clause_title") or ""),
        str(requirement.get("original_statement") or requirement.get("evidence_quote") or ""),
        str(requirement.get("interpretation_zh") or requirement.get("statement") or ""),
        str(requirement.get("applicability") or ""),
        parameters,
    ]))


def _hash_test_embedding(text: str) -> list[float]:
    vector = [0.0] * HASH_TEST_DIMENSIONS
    terms = re.findall(r"[A-Za-z0-9./-]+|[\u4e00-\u9fff]", text.lower())
    for term in terms:
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % HASH_TEST_DIMENSIONS
        vector[index] += -1.0 if digest[2] & 1 else 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _load_bge_model():
    """Load BGE-M3 once, including when the first calls arrive concurrently."""
    global _BGE_MODEL
    if _BGE_MODEL is not None:
        return _BGE_MODEL
    with _BGE_MODEL_LOCK:
        if _BGE_MODEL is not None:
            return _BGE_MODEL
        _BGE_MODEL = _create_bge_model()
        return _BGE_MODEL


def _create_bge_model():
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("本地 BGE-M3 未安装，请安装 backend/requirements.txt") from exc
    configured_device = os.getenv("STANDARD_EMBEDDING_DEVICE", "auto").strip().lower()
    device = configured_device if configured_device in {"mps", "cpu"} else (
        "mps" if torch.backends.mps.is_available() else "cpu"
    )
    logger.info("standard_embedding_model_loading", model=EMBEDDING_MODEL, device=device)
    model = SentenceTransformer(EMBEDDING_MODEL, device=device)
    model.max_seq_length = int(os.getenv("STANDARD_EMBEDDING_MAX_LENGTH", "2048"))
    logger.info("standard_embedding_model_loaded", model=EMBEDDING_MODEL, device=device)
    return model


def encode_texts(texts: list[str]) -> tuple[str, list[list[float]]]:
    if EMBEDDING_BACKEND == "hash-test":
        return "hash-test-v1", [_hash_test_embedding(text) for text in texts]
    if EMBEDDING_BACKEND == "lexical-hash":
        # Deterministic, offline retrieval index for manually curated standards.
        # It is intentionally not a semantic model: exact names, pulse codes,
        # clause numbers and parameters remain searchable without any model call.
        return "lexical-hash-v1", [_hash_test_embedding(text) for text in texts]
    if EMBEDDING_BACKEND != "bge-m3":
        raise RuntimeError(f"不支持的标准向量后端: {EMBEDDING_BACKEND}")
    model = _load_bge_model()
    vectors = model.encode(
        texts, batch_size=max(1, int(os.getenv("STANDARD_EMBEDDING_BATCH_SIZE", "1"))),
        normalize_embeddings=True, show_progress_bar=False,
    )
    return EMBEDDING_MODEL, [vector.tolist() for vector in vectors]


def serialize_vector(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def deserialize_vector(value: bytes) -> list[float]:
    result = array("f")
    result.frombytes(value)
    return list(result)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def index_standard_release(release_id: str) -> str:
    release = database.get_standard_release(release_id)
    if not release:
        raise ValueError("标准发布版本不存在")
    requirements = list(release["snapshot"].get("requirements") or [])
    if not requirements:
        raise ValueError("标准发布版本没有有效要求")
    texts = [_requirement_text(requirement) for requirement in requirements]
    model_name, vectors = encode_texts(texts)
    rows = []
    for requirement, text, vector in zip(requirements, texts, vectors):
        rows.append({
            "requirement_id": str(requirement.get("id") or ""),
            "standard_id": release["standard_id"], "release_id": release_id,
            "model_name": model_name, "dimensions": len(vector),
            "vector": serialize_vector(vector),
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    database.replace_standard_release_embeddings(release_id, rows)
    logger.info(
        "standard_release_embeddings_built", standard_id=release["standard_id"],
        release_id=release_id, model=model_name, requirement_count=len(rows),
        dimensions=rows[0]["dimensions"],
    )
    return model_name
