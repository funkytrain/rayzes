"""
Write-back to GRAMPS XML.

Modifica el XML original en memoria (lxml con fallback ElementTree),
añadiendo notas y tags sin colisión de IDs/handles.
Nunca escribe en disco; to_bytes() devuelve el resultado gzip.
"""

from __future__ import annotations

import gzip
import re
import secrets
import datetime
from typing import Optional

NS = "http://gramps-project.org/xml/1.7.2/"


# ─────────────────────────────────────────────────────────────────────────────
# GrampsIDAllocator
# ─────────────────────────────────────────────────────────────────────────────

class GrampsIDAllocator:
    """
    Gestiona handles/IDs sin colisión con el árbol original.

    existing_handles: set de todos los handles del XML
    max_ids: {'note': N, 'tag': N, ...} calculados a partir de los IDs del GrampsDB
    """

    def __init__(self, existing_handles: set, max_ids: dict) -> None:
        self._handles = set(existing_handles)
        self._max_note = max_ids.get('note', 0)
        self._max_tag = max_ids.get('tag', 0)

    @classmethod
    def from_db(cls, db) -> 'GrampsIDAllocator':
        """
        Construye el allocator a partir de un GrampsDB ya parseado.
        Los handles de tags no están en GrampsDB → se pasan a 0 (se asignará desde XML).
        """
        handles: set = set()
        handles.update(db.persons.keys())
        handles.update(db.families.keys())
        handles.update(db.events.keys())
        handles.update(db.notes.keys())
        handles.update(db.places.keys())

        def _max_numeric_id(id_iter) -> int:
            max_n = 0
            for gramps_id in id_iter:
                m = re.search(r'(\d+)$', gramps_id or '')
                if m:
                    max_n = max(max_n, int(m.group(1)))
            return max_n

        # notes dict: handle → str (text). No .id attribute — use keys as handles.
        max_note = _max_numeric_id(db.notes.keys()) if hasattr(db, 'notes') else 0

        return cls(
            existing_handles=handles,
            max_ids={'note': max_note, 'tag': 0},
        )

    @classmethod
    def from_db_and_xml(cls, db, root, backend: str) -> 'GrampsIDAllocator':
        """
        Versión extendida que también extrae max tag ID del XML directamente.
        """
        alloc = cls.from_db(db)
        # Parse tags from XML to get real max tag ID
        max_tag = 0
        for el in root.iter():
            local = el.tag.split('}')[-1] if '}' in el.tag else el.tag
            if local.lower() == 'tag':
                tid = el.get('id', '')
                m = re.search(r'(\d+)$', tid)
                if m:
                    max_tag = max(max_tag, int(m.group(1)))
        alloc._max_tag = max_tag
        return alloc

    def next_note_id(self) -> str:
        self._max_note += 1
        return f"N{self._max_note:04d}"

    def next_tag_id(self) -> str:
        self._max_tag += 1
        return f"T{self._max_tag:04d}"

    def new_handle(self) -> str:
        """Handle único de 28 chars hex prefijado con '_'."""
        while True:
            h = '_' + secrets.token_hex(14)
            if h not in self._handles:
                self._handles.add(h)
                return h


# ─────────────────────────────────────────────────────────────────────────────
# Helpers XML
# ─────────────────────────────────────────────────────────────────────────────

def _tag_local(el) -> str:
    tag = el.tag
    return tag.split('}')[-1] if '}' in tag else tag


def _ns_tag(local: str) -> str:
    return f"{{{NS}}}{local}"


def _make_note_element(backend: str, note_id: str, handle: str, text: str):
    """Crea un elemento <note> con su texto."""
    if backend == 'lxml':
        from lxml import etree
        note_el = etree.Element(_ns_tag('note'))
        note_el.set('handle', handle)
        note_el.set('change', str(int(datetime.datetime.now().timestamp())))
        note_el.set('id', note_id)
        note_el.set('type', 'General')
        styledtext = etree.SubElement(note_el, _ns_tag('styledtext'))
        text_el = etree.SubElement(styledtext, _ns_tag('text'))
        text_el.text = text
    else:
        import xml.etree.ElementTree as ET
        note_el = ET.Element(_ns_tag('note'))
        note_el.set('handle', handle)
        note_el.set('change', str(int(datetime.datetime.now().timestamp())))
        note_el.set('id', note_id)
        note_el.set('type', 'General')
        styledtext = ET.SubElement(note_el, _ns_tag('styledtext'))
        text_el = ET.SubElement(styledtext, _ns_tag('text'))
        text_el.text = text
    return note_el


def _make_tag_element(backend: str, tag_id: str, handle: str, name: str, color: str):
    """Crea un elemento <tag>."""
    if backend == 'lxml':
        from lxml import etree
        tag_el = etree.Element(_ns_tag('tag'))
    else:
        import xml.etree.ElementTree as ET
        tag_el = ET.Element(_ns_tag('tag'))
    tag_el.set('handle', handle)
    tag_el.set('change', str(int(datetime.datetime.now().timestamp())))
    tag_el.set('id', tag_id)
    tag_el.set('name', name)
    tag_el.set('color', color)
    tag_el.set('priority', '0')
    return tag_el


def _make_noteref(backend: str, handle: str):
    if backend == 'lxml':
        from lxml import etree
        el = etree.Element(_ns_tag('noteref'))
    else:
        import xml.etree.ElementTree as ET
        el = ET.Element(_ns_tag('noteref'))
    el.set('hlink', handle)
    return el


def _make_tagref(backend: str, handle: str):
    if backend == 'lxml':
        from lxml import etree
        el = etree.Element(_ns_tag('tagref'))
    else:
        import xml.etree.ElementTree as ET
        el = ET.Element(_ns_tag('tagref'))
    el.set('hlink', handle)
    return el


# ─────────────────────────────────────────────────────────────────────────────
# GrampsWriter
# ─────────────────────────────────────────────────────────────────────────────

class GrampsWriter:
    """
    Orquestador. Opera sobre el XML original en memoria.
    Nunca escribe en disco; to_bytes() devuelve el resultado gzip.
    """

    # Tag definitions: name → color
    _TAG_DEFS = {
        'GenHelper:Error':   '#CC0000',
        'GenHelper:Warning': '#FF8C00',
    }

    def __init__(self, content_bytes: bytes, db) -> None:
        """
        Descomprime (si gzip), parsea con lxml (fallback ET),
        instancia GrampsIDAllocator.
        """
        raw = content_bytes
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)

        self._backend: str
        self._root = None
        try:
            from lxml import etree as _lxml
            parser = _lxml.XMLParser(remove_blank_text=True, recover=True)
            self._root = _lxml.fromstring(raw, parser)
            self._backend = 'lxml'
        except Exception:
            pass
        if self._root is None:
            try:
                import xml.etree.ElementTree as ET
                self._root = ET.fromstring(raw)
                self._backend = 'et'
            except Exception as e:
                raise ValueError(
                    f"No se pudo parsear el XML del archivo .gramps: {e}. "
                    f"Primeros 200 bytes: {raw[:200]!r}"
                ) from e
        if self._root is None:
            raise ValueError("El XML del archivo .gramps resultó vacío tras el parseo.")

        self._db = db
        self._alloc = GrampsIDAllocator.from_db_and_xml(db, self._root, self._backend)

        # Acumuladores
        self._new_notes: list = []           # list[(note_handle, note_element)]
        self._new_tags: dict = {}            # name → (handle, element)

        # Índices en memoria: person_handle → element, family_handle → element
        self._person_els: dict = {}
        self._family_els: dict = {}
        self._build_element_index()

        # Log de cambios realizados
        self.changelog: list[dict] = []

    # ── Índice de elementos ───────────────────────────────────────────────────

    def _build_element_index(self) -> None:
        for el in self._root.iter():
            local = _tag_local(el)
            if local.lower() == 'person':
                h = el.get('handle')
                if h:
                    self._person_els[h] = el
            elif local.lower() == 'family':
                h = el.get('handle')
                if h:
                    self._family_els[h] = el

    # ── Tag helper ────────────────────────────────────────────────────────────

    def _get_or_create_tag(self, name: str) -> str:
        """Devuelve el handle del tag con ese nombre, creándolo si no existe."""
        if name in self._new_tags:
            return self._new_tags[name][0]

        # Buscar en XML existente
        for el in self._root.iter():
            if _tag_local(el).lower() == 'tag' and el.get('name') == name:
                return el.get('handle', '')

        # Crear nuevo
        color = self._TAG_DEFS.get(name, '#888888')
        tag_id = self._alloc.next_tag_id()
        handle = self._alloc.new_handle()
        tag_el = _make_tag_element(self._backend, tag_id, handle, name, color)
        self._new_tags[name] = (handle, tag_el)
        return handle

    # ── Nota helper ──────────────────────────────────────────────────────────

    def _note_already_exists(self, el, text_prefix: str) -> bool:
        """Comprueba si el elemento ya tiene una nota con ese texto."""
        for noteref in el:
            if _tag_local(noteref).lower() != 'noteref':
                continue
            hlink = noteref.get('hlink', '')
            existing_text = self._db.notes.get(hlink, '')
            if existing_text and existing_text.startswith(text_prefix):
                return True
        # También revisar notas recién añadidas en esta sesión
        for note_handle, note_el in self._new_notes:
            styledtext = None
            for ch in note_el:
                if _tag_local(ch).lower() == 'styledtext':
                    styledtext = ch
                    break
            if styledtext is not None:
                for ch2 in styledtext:
                    if _tag_local(ch2).lower() == 'text':
                        if (ch2.text or '').startswith(text_prefix):
                            # Check if noteref apunta a este handle en el elemento
                            for nr in el:
                                if _tag_local(nr).lower() == 'noteref' and nr.get('hlink') == note_handle:
                                    return True
        return False

    def _add_note_to_element(self, el, text: str) -> str:
        """Crea nota y añade noteref al elemento. Devuelve handle."""
        note_id = self._alloc.next_note_id()
        handle = self._alloc.new_handle()
        note_el = _make_note_element(self._backend, note_id, handle, text)
        self._new_notes.append((handle, note_el))
        el.append(_make_noteref(self._backend, handle))
        return handle

    # ── API pública ───────────────────────────────────────────────────────────

    def add_confirmation_notes(self, confirmed_links: dict) -> int:
        """
        Para cada entrada en gramps_links.confirmed:
          {witness_name: {pid, name}}
        → Nota en la persona con gramps_id=pid.
        Devuelve n notas añadidas.
        """
        today = datetime.date.today().isoformat()
        added = 0
        for witness_name, link in confirmed_links.items():
            pid = link.get('pid', '')
            pname = link.get('name', '')

            # Buscar handle de la persona por gramps_id
            handle = self._db.persons_by_gramps_id.get(pid)
            if not handle:
                continue
            el = self._person_els.get(handle)
            if el is None:
                continue

            text = (
                f"Testigo confirmado: {witness_name} identificado como "
                f"{pname} ({pid}) por GenHelper {today}."
            )
            prefix = f"Testigo confirmado: {witness_name}"
            if self._note_already_exists(el, prefix):
                continue

            self._add_note_to_element(el, text)
            self.changelog.append({
                'type': 'confirmation_note',
                'pid': pid,
                'name': pname,
                'witness': witness_name,
            })
            added += 1
        return added

    def add_witness_attribute_notes(self, confirmed_links: dict) -> int:
        """
        Para cada link confirmado que tenga nota:
          {witness_name: {pid, name, note}}
        → Busca en todos los eventos del XML el <attribute type="Witness" value="{witness_name}">
          y añade la nota como <noteref> dentro de ese atributo.
        Evita duplicados comprobando el prefijo de la nota.
        Devuelve n notas añadidas.
        """
        added = 0
        for witness_name, link in confirmed_links.items():
            if not isinstance(link, dict):
                continue
            note_text = link.get('note', '').strip()
            if not note_text:
                continue

            prefix = f"[GenHelper] {note_text[:40]}"

            for ev_el in self._root.iter():
                if _tag_local(ev_el).lower() != 'event':
                    continue
                for attr_el in ev_el:
                    if _tag_local(attr_el).lower() != 'attribute':
                        continue
                    if attr_el.get('type') != 'Witness':
                        continue
                    if (attr_el.get('value') or '').strip().lower() != witness_name.strip().lower():
                        continue
                    # Comprobar si ya existe una nota con ese prefijo en el atributo
                    if self._note_already_exists(attr_el, f"[GenHelper] {note_text[:40]}"):
                        continue
                    text = f"[GenHelper] {note_text}"
                    self._add_note_to_element(attr_el, text)
                    self.changelog.append({
                        'type': 'witness_note',
                        'witness': witness_name,
                        'note': note_text,
                    })
                    added += 1
        return added

    def add_inconsistency_tags(self, active_issues: list[dict]) -> int:
        """
        Para cada inconsistencia activa:
        - severity='error' → tag 'GenHelper:Error'
        - severity='warning' → tag 'GenHelper:Warning'
        Un solo tagref por nivel por persona/familia.
        Devuelve n tagrefs añadidos.
        """
        severity_to_tag = {
            'error':   'GenHelper:Error',
            'warning': 'GenHelper:Warning',
        }
        # Acumular qué personas/familias necesitan qué tags
        target_tags: dict = {}   # (element_handle, tag_name) → True

        for issue in active_issues:
            severity = issue.get('severity', '')
            tag_name = severity_to_tag.get(severity)
            if not tag_name:
                continue

            pid = issue.get('pid', '')
            # Buscar por gramps_id
            handle = self._db.persons_by_gramps_id.get(pid)
            if not handle:
                # Puede ser una familia
                for fam_handle, fam in self._db.families.items():
                    if fam.id == pid:
                        handle = fam_handle
                        break
            if not handle:
                continue

            target_tags[(handle, tag_name)] = True

        added = 0
        for (handle, tag_name), _ in target_tags.items():
            el = self._person_els.get(handle) or self._family_els.get(handle)
            if el is None:
                continue

            tag_handle = self._get_or_create_tag(tag_name)

            # Comprobar si ya tiene ese tagref
            already = False
            for ch in el:
                if _tag_local(ch).lower() == 'tagref' and ch.get('hlink') == tag_handle:
                    already = True
                    break
            if already:
                continue

            el.append(_make_tagref(self._backend, tag_handle))
            self.changelog.append({
                'type': 'inconsistency_tag',
                'handle': handle,
                'tag': tag_name,
            })
            added += 1
        return added

    def add_completion_candidates(self, batch_results: list[dict],
                                  min_prob: float = 0.65) -> int:
        """
        Para cada caso con top_prob >= min_prob → nota en la familia (marriage_fid).
        Devuelve n notas añadidas.
        """
        today = datetime.date.today().isoformat()
        added = 0

        for case in batch_results:
            top_prob = case.get('top_prob') or 0.0
            if top_prob < min_prob:
                continue

            marriage_fid = case.get('marriage_fid', '')
            orphan_name = case.get('orphan_name', '')
            top_candidate = case.get('top_candidate', '')
            role_needed = case.get('role_needed', '')
            results = case.get('results', [])

            # Buscar handle de la familia
            fam_handle = None
            for h, fam in self._db.families.items():
                if fam.id == marriage_fid:
                    fam_handle = h
                    break
            if not fam_handle:
                continue

            el = self._family_els.get(fam_handle)
            if el is None:
                continue

            # Factores del candidato top
            top_result = next(
                (r for r in results if r.get('name') == top_candidate),
                results[0] if results else {}
            )
            f1 = top_result.get('f1_score', 'N/A')
            f4 = top_result.get('f4_score', 'N/A')
            f5 = top_result.get('f5_score', 'N/A')

            role_label = 'padre' if role_needed == 'father' else 'madre'
            text = (
                f"Candidato probable a {role_label} de {orphan_name}: "
                f"{top_candidate} (prob={top_prob:.0%}). "
                f"Factores: F1={f1}, F4={f4}, F5={f5}. "
                f"Generado por GenHelper {today}."
            )
            prefix = f"Candidato probable a {role_label} de {orphan_name}:"
            if self._note_already_exists(el, prefix):
                continue

            self._add_note_to_element(el, text)
            self.changelog.append({
                'type': 'completion_candidate',
                'marriage_fid': marriage_fid,
                'orphan': orphan_name,
                'candidate': top_candidate,
                'prob': top_prob,
            })
            added += 1
        return added

    # ── Serialización ─────────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """
        1. Insertar <note> nuevas después del último <note> existente
        2. Insertar <tag> nuevas en la sección de tags (o crearla)
        3. Serializar
        4. gzip.compress
        """
        # 1. Insertar notas
        if self._new_notes:
            last_note_el = None
            last_note_idx = -1
            root_children = list(self._root)
            for i, child in enumerate(root_children):
                if _tag_local(child).lower() == 'note':
                    last_note_el = child
                    last_note_idx = i

            if last_note_idx >= 0:
                for idx, (_, note_el) in enumerate(self._new_notes):
                    self._root.insert(last_note_idx + 1 + idx, note_el)
            else:
                for _, note_el in self._new_notes:
                    self._root.append(note_el)

        # 2. Insertar tags
        if self._new_tags:
            last_tag_el = None
            last_tag_idx = -1
            root_children = list(self._root)
            for i, child in enumerate(root_children):
                if _tag_local(child).lower() == 'tag':
                    last_tag_el = child
                    last_tag_idx = i

            if last_tag_idx >= 0:
                for idx, (_, tag_el) in enumerate(self._new_tags.values()):
                    self._root.insert(last_tag_idx + 1 + idx, tag_el)
            else:
                # Insertar antes de las personas (al principio del documento)
                first_person_idx = -1
                for i, child in enumerate(list(self._root)):
                    if _tag_local(child).lower() == 'person':
                        first_person_idx = i
                        break
                insert_at = first_person_idx if first_person_idx >= 0 else 0
                for idx, (_, tag_el) in enumerate(self._new_tags.values()):
                    self._root.insert(insert_at + idx, tag_el)

        # 3. Serializar
        if self._backend == 'lxml':
            from lxml import etree
            xml_bytes = etree.tostring(
                self._root,
                xml_declaration=True,
                encoding='UTF-8',
                pretty_print=True,
            )
        else:
            import xml.etree.ElementTree as ET
            ET.register_namespace('', NS)
            xml_bytes = ET.tostring(self._root, encoding='unicode').encode('utf-8')
            xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes

        # 4. gzip
        return gzip.compress(xml_bytes)
