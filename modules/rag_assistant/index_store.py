from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import scipy.sparse

from modules.rag_assistant.chunker import RagChunk

INDEX_DIR = Path(__file__).parent.parent.parent / "data" / "rag_index"

_META_FILE = INDEX_DIR / "index_meta.json"
_CHUNKS_FILE = INDEX_DIR / "chunks.json"
_TFIDF_MATRIX_FILE = INDEX_DIR / "tfidf_matrix.npz"
_TFIDF_VOCAB_FILE = INDEX_DIR / "tfidf_vocab.json"
_EMBEDDINGS_FILE = INDEX_DIR / "embeddings.npy"


@dataclass
class RagIndex:
    chunks: list[RagChunk]
    strategy: str                                   # "tfidf" | "embeddings"
    vectorizer: Optional[object] = None             # TfidfVectorizer
    tfidf_matrix: Optional[scipy.sparse.csr_matrix] = None
    embeddings: Optional[np.ndarray] = None         # shape (n, dim)
    meta: dict = field(default_factory=dict)
    tree_stats: str = ""                            # pre-computed global statistics block


def index_exists() -> bool:
    return _META_FILE.exists() and _CHUNKS_FILE.exists() and _TFIDF_MATRIX_FILE.exists()


def get_stored_gramps_hash() -> Optional[str]:
    if not _META_FILE.exists():
        return None
    try:
        meta = json.loads(_META_FILE.read_text(encoding="utf-8"))
        return meta.get("gramps_hash")
    except Exception:
        return None


def get_stored_doc_filenames() -> list[str]:
    if not _META_FILE.exists():
        return []
    try:
        meta = json.loads(_META_FILE.read_text(encoding="utf-8"))
        return meta.get("doc_filenames", [])
    except Exception:
        return []


def save_index(index: RagIndex) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # chunks.json
    _CHUNKS_FILE.write_text(
        json.dumps([
            {
                "chunk_id": c.chunk_id,
                "source_type": c.source_type,
                "source_label": c.source_label,
                "text": c.text,
                "metadata": c.metadata,
            }
            for c in index.chunks
        ], ensure_ascii=False, indent=None),
        encoding="utf-8",
    )

    # TF-IDF matrix + vocab
    if index.tfidf_matrix is not None:
        scipy.sparse.save_npz(str(_TFIDF_MATRIX_FILE), index.tfidf_matrix)
    if index.vectorizer is not None:
        vocab_data = {
            "vocabulary": {k: int(v) for k, v in index.vectorizer.vocabulary_.items()},
            "idf": index.vectorizer.idf_.tolist(),
        }
        _TFIDF_VOCAB_FILE.write_text(json.dumps(vocab_data, ensure_ascii=False), encoding="utf-8")

    # embeddings (optional)
    if index.embeddings is not None:
        np.save(str(_EMBEDDINGS_FILE), index.embeddings)
    elif _EMBEDDINGS_FILE.exists():
        _EMBEDDINGS_FILE.unlink()

    # tree_stats + meta (last — signals completion)
    meta_to_save = dict(index.meta)
    meta_to_save["tree_stats"] = index.tree_stats
    _META_FILE.write_text(json.dumps(meta_to_save, ensure_ascii=False, indent=2), encoding="utf-8")


def load_index() -> Optional[RagIndex]:
    if not index_exists():
        return None
    try:
        meta = json.loads(_META_FILE.read_text(encoding="utf-8"))

        raw_chunks = json.loads(_CHUNKS_FILE.read_text(encoding="utf-8"))
        chunks = [
            RagChunk(
                chunk_id=c["chunk_id"],
                source_type=c["source_type"],
                source_label=c["source_label"],
                text=c["text"],
                metadata=c.get("metadata", {}),
            )
            for c in raw_chunks
        ]

        tfidf_matrix = scipy.sparse.load_npz(str(_TFIDF_MATRIX_FILE))

        from sklearn.feature_extraction.text import TfidfVectorizer, TfidfTransformer
        vocab_data = json.loads(_TFIDF_VOCAB_FILE.read_text(encoding="utf-8"))
        vectorizer = TfidfVectorizer()
        vectorizer.vocabulary_ = vocab_data["vocabulary"]
        transformer = TfidfTransformer()
        transformer.idf_ = np.array(vocab_data["idf"])
        vectorizer._tfidf = transformer

        embeddings = None
        if _EMBEDDINGS_FILE.exists():
            embeddings = np.load(str(_EMBEDDINGS_FILE))

        return RagIndex(
            chunks=chunks,
            strategy=meta.get("strategy", "tfidf"),
            vectorizer=vectorizer,
            tfidf_matrix=tfidf_matrix,
            embeddings=embeddings,
            meta=meta,
            tree_stats=meta.get("tree_stats", ""),
        )
    except Exception:
        return None
