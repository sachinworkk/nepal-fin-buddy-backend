"""Local cross-encoder reranker (bge-reranker-v2-m3) on CUDA when available.

Loaded lazily on first use so importing app.rag never pulls torch. Runs in-process:
the RTX 5050 (8GB) holds the ~1.2GB fp16 model easily.
"""

import asyncio
import functools

import structlog

log = structlog.get_logger()

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

_model = None
_lock = asyncio.Lock()


def _load():
    global _model
    if _model is not None:
        return _model
    import torch
    from sentence_transformers import CrossEncoder

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("reranker.loading", model=MODEL_NAME, device=device)
    model = CrossEncoder(MODEL_NAME, device=device, max_length=512)
    if device == "cuda":
        model.model.half()
    _model = model
    return _model


async def score(query: str, docs: list[str]) -> list[float]:
    """Sigmoid-normalised relevance scores for (query, doc) pairs."""
    async with _lock:
        model = await asyncio.to_thread(_load)
        pairs = [(query, d[:4000]) for d in docs]
        raw = await asyncio.to_thread(model.predict, pairs)
        import numpy as np

        return [float(s) for s in 1 / (1 + np.exp(-np.asarray(raw, dtype=float)))]


def is_loaded() -> bool:
    return _model is not None


def reset() -> None:
    """Drop the cached model (used by tests)."""
    global _model
    _model = None
    functools.cache_clear = getattr(functools, "cache_clear", None)  # no-op guard
