from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
import scipy.sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from modules.rag_assistant.chunker import RagChunk
from modules.rag_assistant.index_store import RagIndex


# ─────────────────────────────────────────────────────────────────────────────
# TF-IDF index
# ─────────────────────────────────────────────────────────────────────────────

def build_tfidf_index(chunks: list[RagChunk]) -> tuple[TfidfVectorizer, scipy.sparse.csr_matrix]:
    vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=1,
        max_features=60_000,
        sublinear_tf=True,
    )
    corpus = [c.text for c in chunks]
    matrix = vectorizer.fit_transform(corpus)
    return vectorizer, matrix


def query_tfidf(
    query: str,
    vectorizer: TfidfVectorizer,
    matrix: scipy.sparse.csr_matrix,
    chunks: list[RagChunk],
    top_k: int = 5,
) -> list[tuple[RagChunk, float]]:
    qvec = vectorizer.transform([query])
    scores = cosine_similarity(qvec, matrix).flatten()
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [(chunks[i], float(scores[i])) for i in top_indices if scores[i] > 0.0]


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings index (optional — requires /v1/embeddings endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def _embed_batch(
    texts: list[str],
    base_url: str,
    model: str,
    timeout: int = 60,
    api_key: str | None = None,
) -> Optional[np.ndarray]:
    import requests
    url = base_url.rstrip("/") + "/embeddings"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.post(
            url,
            json={"model": model, "input": texts},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        vectors = [item["embedding"] for item in data["data"]]
        return np.array(vectors, dtype=np.float32)
    except Exception:
        return None


def try_build_embeddings_index(
    chunks: list[RagChunk],
    base_url: str,
    model: str,
    batch_size: int = 32,
    provider: str = "local",
    api_key: str | None = None,
) -> Optional[np.ndarray]:
    # Claude API has no embeddings endpoint — skip and use TF-IDF
    if provider == "claude":
        return None

    effective_base_url = base_url
    if provider == "openai_remote":
        effective_base_url = "https://api.openai.com/v1"

    all_vecs: list[np.ndarray] = []
    texts = [c.text for c in chunks]

    for i in range(0, len(texts), batch_size):
        batch = texts[i: i + batch_size]
        vecs = _embed_batch(batch, effective_base_url, model, api_key=api_key)
        if vecs is None:
            return None
        all_vecs.append(vecs)

    if not all_vecs:
        return None
    return np.vstack(all_vecs)


def _cosine_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    normed = matrix / norms
    return (normed @ q).flatten()


def query_embeddings(
    query: str,
    embeddings: np.ndarray,
    chunks: list[RagChunk],
    base_url: str,
    model: str,
    top_k: int = 5,
    provider: str = "local",
    api_key: str | None = None,
) -> list[tuple[RagChunk, float]]:
    effective_base_url = base_url
    if provider == "openai_remote":
        effective_base_url = "https://api.openai.com/v1"
    qvec = _embed_batch([query], effective_base_url, model, api_key=api_key)
    if qvec is None:
        return []
    scores = _cosine_scores(qvec[0], embeddings)
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [(chunks[i], float(scores[i])) for i in top_indices if scores[i] > 0.0]


# ─────────────────────────────────────────────────────────────────────────────
# Unified entry points
# ─────────────────────────────────────────────────────────────────────────────

def build_index(
    chunks: list[RagChunk],
    base_url: str,
    model: str,
    gramps_hash: str,
    doc_filenames: list[str],
    db=None,
    provider: str = "local",
    api_key: str | None = None,
) -> RagIndex:
    embeddings: Optional[np.ndarray] = None
    strategy = "tfidf"

    try:
        embeddings = try_build_embeddings_index(
            chunks, base_url, model, batch_size=32,
            provider=provider, api_key=api_key,
        )
        if embeddings is not None:
            strategy = "embeddings"
    except Exception:
        pass

    vectorizer, tfidf_matrix = build_tfidf_index(chunks)

    tree_stats = ""
    if db is not None:
        from modules.rag_assistant.chunker import build_tree_stats
        try:
            tree_stats = build_tree_stats(db)
        except Exception:
            pass

    n_gramps = sum(1 for c in chunks if not c.source_type.startswith("doc_"))
    n_docs = len(chunks) - n_gramps

    meta = {
        "strategy": strategy,
        "build_ts": datetime.utcnow().isoformat(),
        "gramps_hash": gramps_hash,
        "n_chunks": len(chunks),
        "n_chunks_gramps": n_gramps,
        "n_chunks_docs": n_docs,
        "doc_filenames": sorted(doc_filenames),
        "model_url": base_url,
        "model_name": model,
    }

    return RagIndex(
        chunks=chunks,
        strategy=strategy,
        vectorizer=vectorizer,
        tfidf_matrix=tfidf_matrix,
        embeddings=embeddings,
        meta=meta,
        tree_stats=tree_stats,
    )


def retrieve(
    query: str,
    index: RagIndex,
    top_k: int = 5,
    base_url: str = "http://127.0.0.1:9292/v1",
    model: str = "qwen3",
    provider: str = "local",
    api_key: str | None = None,
) -> list[tuple[RagChunk, float]]:
    if index.strategy == "embeddings" and index.embeddings is not None:
        results = query_embeddings(
            query, index.embeddings, index.chunks, base_url, model,
            top_k=top_k, provider=provider, api_key=api_key,
        )
        if results:
            return results
        # fallback to tfidf if embedding query fails
    if index.vectorizer is not None and index.tfidf_matrix is not None:
        return query_tfidf(query, index.vectorizer, index.tfidf_matrix, index.chunks, top_k=top_k)
    return []
