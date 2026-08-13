from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from services import standard_embeddings


def test_bge_model_is_created_once_for_concurrent_first_calls():
    sentinel = object()
    standard_embeddings._BGE_MODEL = None

    with patch.object(
        standard_embeddings, "_create_bge_model", return_value=sentinel,
    ) as create:
        with ThreadPoolExecutor(max_workers=8) as pool:
            models = list(pool.map(lambda _: standard_embeddings._load_bge_model(), range(16)))

    assert models == [sentinel] * 16
    create.assert_called_once_with()
    standard_embeddings._BGE_MODEL = None


def test_lexical_hash_backend_is_deterministic_and_offline(monkeypatch):
    monkeypatch.setattr(standard_embeddings, "EMBEDDING_BACKEND", "lexical-hash")

    model, first = standard_embeddings.encode_texts(["脉冲 P2a ISO 7637-2 5.6.2"])
    repeated_model, repeated = standard_embeddings.encode_texts(["脉冲 P2a ISO 7637-2 5.6.2"])

    assert model == repeated_model == "lexical-hash-v1"
    assert first == repeated
    assert len(first[0]) == standard_embeddings.HASH_TEST_DIMENSIONS
