"""
Cache persistente de resultados de búsqueda de archivo.

Almacena los resultados en data/archive_findings.json para no repetir
búsquedas ya realizadas.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from modules.testigos.archive_search import WitnessArchiveResult, ArchiveDocument


_STORE_FILE = Path(__file__).resolve().parents[2] / "data" / "archive_findings.json"


def _result_key(r: WitnessArchiveResult) -> str:
    return f"{r.witness_norm}::{r.note}"


def _result_to_dict(r: WitnessArchiveResult) -> dict:
    d = asdict(r)
    return d


def _result_from_dict(d: dict) -> WitnessArchiveResult:
    documents = [
        ArchiveDocument(**doc) for doc in d.pop("documents", [])
    ]
    r = WitnessArchiveResult(**d)
    r.documents = documents
    return r


def load_store() -> dict[str, WitnessArchiveResult]:
    """Carga el store desde disco. Devuelve dict keyed por witness_norm::note."""
    if not _STORE_FILE.exists():
        return {}
    try:
        raw = json.loads(_STORE_FILE.read_text(encoding='utf-8'))
        return {k: _result_from_dict(v) for k, v in raw.items()}
    except Exception:
        return {}


def save_store(store: dict[str, WitnessArchiveResult]) -> None:
    """Persiste el store en disco."""
    _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    raw = {k: _result_to_dict(v) for k, v in store.items()}
    _STORE_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')


def get_cached(
    store: dict[str, WitnessArchiveResult],
    result: WitnessArchiveResult,
) -> Optional[WitnessArchiveResult]:
    key = _result_key(result)
    cached = store.get(key)
    if cached and cached.search_status == "searched":
        return cached
    return None


def put_result(
    store: dict[str, WitnessArchiveResult],
    result: WitnessArchiveResult,
) -> None:
    key = _result_key(result)
    store[key] = result


def clear_result(
    store: dict[str, WitnessArchiveResult],
    result: WitnessArchiveResult,
) -> None:
    key = _result_key(result)
    store.pop(key, None)
