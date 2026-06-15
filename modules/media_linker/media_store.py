"""
Registro persistente de imágenes ya vinculadas en ejecuciones anteriores.
Persiste en data/media_linker_registro.json.
"""

from __future__ import annotations

import json
import datetime as _dt
from pathlib import Path

_STORE_PATH = Path("data") / "media_linker_registro.json"


class MediaLinkerStore:
    def __init__(self, path: Path = _STORE_PATH) -> None:
        self._path = path
        self._data: dict[str, dict] = {}

    def load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_procesados(self) -> set[str]:
        return set(self._data.keys())

    def marcar_procesados(self, links: list[dict]) -> None:
        """
        links: lista de dicts con al menos "img_name", "gramps_id", "destino"
        """
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        for lnk in links:
            self._data[lnk["img_name"]] = {
                "gramps_id": lnk.get("gramps_id", ""),
                "destino":   lnk.get("destino", ""),
                "fecha":     ts,
            }
        self.save()
