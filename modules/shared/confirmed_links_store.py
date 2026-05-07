"""
Almacén tipado para confirmed_links.json.

Reemplaza los 37+ load_confirmations() / save_confirmations() dispersos en testigos/app.py
con un único punto de acceso que mantiene el JSON en memoria entre llamadas del mismo ciclo
de render, y lo persiste a disco mediante save().
"""

from __future__ import annotations

import json
from pathlib import Path


_EMPTY: dict = {
    'same': {},
    'different': [],
    'event_groups': {},
    'status': {},
    'gramps_links': {'confirmed': {}, 'discarded': []},
}


def _ensure_keys(data: dict) -> dict:
    data.setdefault('same', {})
    data.setdefault('different', [])
    data.setdefault('event_groups', {})
    data.setdefault('status', {})
    data.setdefault('gramps_links', {'confirmed': {}, 'discarded': []})
    gl = data['gramps_links']
    gl.setdefault('confirmed', {})
    gl.setdefault('discarded', [])
    return data


class ConfirmedLinksStore:
    """
    Acceso tipado a confirmed_links.json con caché en memoria.

    Ciclo de uso:
        store.load()                # lee disco → caché
        conf = store.get_all()      # accede a caché (por referencia)
        store.confirm_same(...)     # modifica caché
        store.save()                # caché → disco
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict = dict(_EMPTY)

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    def load(self) -> None:
        """Lee disco → caché. Siempre re-lee (no idempotente) para reflejar cambios externos."""
        if not self._path.exists():
            self._data = _ensure_keys({})
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding='utf-8',
                )
            except Exception:
                pass
            return
        try:
            txt = self._path.read_text(encoding='utf-8')
            data = json.loads(txt)
        except Exception:
            data = {}
        self._data = _ensure_keys(data)

    def save(self, user: str = "admin") -> bool:
        """Escribe caché → disco."""
        import datetime as _dt
        try:
            self._data.setdefault('meta', {})
            self._data['meta']['last_modified'] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            self._data['meta']['by'] = user
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            return True
        except Exception:
            return False

    def save_dict(self, data: dict, user: str = "admin") -> bool:
        """Reemplaza la caché con `data` y escribe a disco. Compatibilidad con save_confirmations(conf)."""
        self._data = _ensure_keys(data)
        return self.save(user=user)

    def reload(self) -> None:
        """Alias explícito de load() para code clarity."""
        self.load()

    # ── Lecturas (acceden a caché) ────────────────────────────────────────────

    def get_all(self) -> dict:
        """Devuelve la caché interna por referencia. Mutar el resultado modifica la caché."""
        return self._data

    def get_canonical(self, name: str) -> str:
        """Nombre canónico para `name`, o el propio `name` si no hay confirmación."""
        for canon, names in self._data.get('same', {}).items():
            if name == canon or name in names:
                return canon
        return name

    def is_same(self, a: str, b: str) -> bool:
        for canon, names in self._data.get('same', {}).items():
            group = {canon} | set(names)
            if a in group and b in group:
                return True
        return False

    def is_different(self, a: str, b: str) -> bool:
        for pair in self._data.get('different', []):
            if set(pair) == {a, b}:
                return True
        return False

    def get_gramps_link(self, name: str) -> 'dict | None':
        return self._data.get('gramps_links', {}).get('confirmed', {}).get(name)

    def get_status(self, key: str) -> 'dict | None':
        return self._data.get('status', {}).get(key)

    def get_event_groups(self) -> dict:
        return self._data.get('event_groups', {})

    # ── Escrituras (modifican caché; llamar a save() para persistir) ──────────

    def confirm_same(self, canon: str, raw: str, user: str = "admin") -> None:
        same = self._data.setdefault('same', {})
        names = same.setdefault(canon, [])
        if raw != canon and raw not in names:
            names.append(raw)

    def reject_pair(self, a: str, b: str, user: str = "admin") -> None:
        diff = self._data.setdefault('different', [])
        if [a, b] not in diff and [b, a] not in diff:
            diff.append([a, b])

    def link_to_gramps(self, name: str, pid: str, pname: str, user: str = "admin") -> None:
        gl = self._data.setdefault('gramps_links', {'confirmed': {}, 'discarded': []})
        gl['confirmed'][name] = {'pid': pid, 'name': pname}

    def discard_gramps_link(self, name: str, user: str = "admin") -> None:
        gl = self._data.setdefault('gramps_links', {'confirmed': {}, 'discarded': []})
        if name in gl.get('confirmed', {}):
            del gl['confirmed'][name]
        discarded = gl.setdefault('discarded', [])
        if name not in discarded:
            discarded.append(name)

    def set_event_group(self, gid: str, events: list) -> None:
        self._data.setdefault('event_groups', {})[gid] = events

    def set_status(self, key: str, state: str, user: str = "admin") -> None:
        import datetime as _dt
        self._data.setdefault('status', {})[key] = {
            'state': state,
            'timestamp': _dt.datetime.now(_dt.timezone.utc).isoformat(),
            'user': user,
        }
