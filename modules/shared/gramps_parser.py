"""
Parser unificado de archivos GRAMPS XML (.gramps).

Sustituye los tres parsers independientes de:
- modules/testigos/app.py: parse_gramps_xml_full()  → GrampsDB.to_witness_events()
- modules/consanguinidad/app.py: parse_gramps()      → GrampsDB.to_persons_dict() + .to_families_dict()
- modules/general/app.py: parse_gramps_extended()    → GrampsDB.to_persons_ext() + .to_families_ext()

Uso:
    db = parse_gramps(content_bytes)
    events_data = db.to_witness_events()   # para testigos
    people, families = db.to_persons_dict(), db.to_families_dict()   # para consanguinidad
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from modules.shared.utils import strip_ns, safe_year


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de parsing XML
# ─────────────────────────────────────────────────────────────────────────────

def _get_root(content_bytes: bytes):
    """Parse raw bytes → XML root. Handles gzip (.gramps), BOM, lxml, ElementTree."""
    if content_bytes.startswith(b'\xef\xbb\xbf'):
        content_bytes = content_bytes[3:]
    content_bytes = content_bytes.lstrip()

    # .gramps files are gzip-compressed XML — decompress before parsing
    if content_bytes[:2] == b'\x1f\x8b':
        import gzip
        try:
            content_bytes = gzip.decompress(content_bytes)
        except Exception as e:
            raise ValueError(f"No se pudo descomprimir el archivo .gramps: {e}") from e

    try:
        from lxml import etree as _lxml
        parser = _lxml.XMLParser(remove_blank_text=True, recover=True)
        root = _lxml.fromstring(content_bytes, parser)
        if root is not None:
            return root, 'lxml'
    except Exception:
        pass

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(content_bytes)
        return root, 'et'
    except Exception as e:
        raise ValueError(f"No se pudo parsear el XML de GRAMPS: {e}") from e


def _iter_tag(root, tagname: str, backend: str):
    """Itera todos los elementos con un tag dado, independientemente del namespace."""
    for el in root.iter():
        if strip_ns(el.tag).lower() == tagname.lower():
            yield el


def _child_text(el, tagname: str, backend: str) -> Optional[str]:
    """Primer texto no vacío del hijo con el tag indicado."""
    for ch in el:
        if strip_ns(ch.tag).lower() == tagname.lower():
            txt = ch.text.strip() if ch.text else None
            return txt or None
    return None


def _itertext(el) -> str:
    """Texto completo de un elemento incluyendo descendientes."""
    try:
        return "".join(el.itertext()).strip()
    except Exception:
        return (el.text or '').strip()


# ─────────────────────────────────────────────────────────────────────────────
# GrampsDB — modelo de dominio unificado
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GrampsPlace:
    handle: str
    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass
class GrampsWitness:
    name: str
    note: str = ""


@dataclass
class GrampsEvent:
    handle: str
    id: str
    type: str
    date_iso: Optional[str]
    place_handle: Optional[str]
    subject_handle: Optional[str]  # handle de la persona Primary
    subject_name: str               # nombre resuelto (puede venir de familia en matrimonios)
    witnesses: list = field(default_factory=list)   # list[GrampsWitness]
    note_texts: list = field(default_factory=list)  # list[str]


@dataclass
class GrampsPerson:
    handle: str
    id: str
    name: str
    sex: str = ""
    birth_year: Optional[int] = None
    birth_place: Optional[str] = None
    baptism_year: Optional[int] = None
    baptism_place: Optional[str] = None
    death_year: Optional[int] = None
    death_place: Optional[str] = None
    has_parents: bool = False
    event_handles: list = field(default_factory=list)   # list[str]
    note_texts: list = field(default_factory=list)      # list[str]
    events_summary: list = field(default_factory=list)  # list[dict] {type, year, place}


@dataclass
class GrampsFamily:
    handle: str
    id: str
    husband_handle: Optional[str] = None
    wife_handle: Optional[str] = None
    child_handles: list = field(default_factory=list)   # list[str]
    marriage_year: Optional[int] = None
    marriage_place: Optional[str] = None
    marriage_notes: list = field(default_factory=list)  # list[str]


@dataclass
class GrampsDB:
    # Claves: handle de elemento GRAMPS
    persons: dict          # handle → GrampsPerson
    persons_by_gramps_id: dict   # gramps id (Ixxxx) → handle
    families: dict         # handle → GrampsFamily
    events: dict           # handle → GrampsEvent
    places: dict           # handle → GrampsPlace
    notes: dict            # handle → str

    # ── Proyecciones ─────────────────────────────────────────────────────────

    def to_witness_events(self) -> list:
        """
        Lista plana evento×testigo compatible con parse_gramps_xml_full().
        Cada fila: {'event_id', 'event_handle', 'type', 'date_iso', 'place_name',
                    'lat', 'lon', 'subj_id', 'subj_name', 'witness_raw', 'note'}
        """
        rows = []
        for ev_handle, ev in self.events.items():
            place = self.places.get(ev.place_handle or '') if ev.place_handle else None
            place_name = place.name if place else ''
            lat = place.lat if place else None
            lon = place.lon if place else None

            if ev.witnesses:
                for w in ev.witnesses:
                    rows.append({
                        'event_id':     ev.id,
                        'event_handle': ev.handle,
                        'type':         ev.type,
                        'date_iso':     ev.date_iso,
                        'place_name':   place_name,
                        'lat':          lat,
                        'lon':          lon,
                        'subj_id':      ev.subject_handle,
                        'subj_name':    ev.subject_name,
                        'witness_raw':  w.name,
                        'note':         w.note,
                    })
            else:
                rows.append({
                    'event_id':     ev.id,
                    'event_handle': ev.handle,
                    'type':         ev.type,
                    'date_iso':     ev.date_iso,
                    'place_name':   place_name,
                    'lat':          lat,
                    'lon':          lon,
                    'subj_id':      ev.subject_handle,
                    'subj_name':    ev.subject_name,
                    'witness_raw':  '',
                    'note':         '',
                })
        return rows

    def to_persons_dict(self) -> dict:
        """
        Dict compatible con parse_gramps() de consanguinidad.
        Formato: {id → {'id', 'name', 'sex', 'birth', 'place': {'name','lat','lon'}}}
        """
        result = {}
        for handle, p in self.persons.items():
            birth_place_obj = None
            if p.birth_place:
                # Buscar lugar por nombre en el índice de lugares
                for pl in self.places.values():
                    if pl.name == p.birth_place:
                        birth_place_obj = {'name': pl.name, 'lat': pl.lat, 'lon': pl.lon}
                        break
                if not birth_place_obj:
                    birth_place_obj = {'name': p.birth_place, 'lat': None, 'lon': None}
            result[p.id] = {
                'id':    p.id,
                'name':  p.name,
                'sex':   p.sex,
                'birth': p.birth_year,
                'place': birth_place_obj,
            }
        return result

    def to_families_dict(self) -> dict:
        """
        Dict compatible con parse_gramps() de consanguinidad.
        Formato: {id → {'id', 'husband', 'wife', 'children': [id...]}}
        """
        result = {}
        for handle, fam in self.families.items():
            husband_id = self.persons[fam.husband_handle].id if fam.husband_handle and fam.husband_handle in self.persons else fam.husband_handle
            wife_id = self.persons[fam.wife_handle].id if fam.wife_handle and fam.wife_handle in self.persons else fam.wife_handle
            child_ids = [
                self.persons[ch].id if ch in self.persons else ch
                for ch in fam.child_handles
            ]
            result[fam.id] = {
                'id':       fam.id,
                'husband':  husband_id,
                'wife':     wife_id,
                'children': list(dict.fromkeys(child_ids)),
            }
        return result

    def to_persons_ext(self) -> dict:
        """
        Dict compatible con parse_gramps_extended() de general.
        Formato: {id → {'id','name','sex','birth_year','birth_place','baptism_year',
                         'baptism_place','death_year','death_place','has_parents',
                         'events','notes'}}
        """
        result = {}
        for handle, p in self.persons.items():
            result[p.id] = {
                'id':            p.id,
                'name':          p.name,
                'sex':           p.sex,
                'birth_year':    p.birth_year,
                'birth_place':   p.birth_place,
                'baptism_year':  p.baptism_year,
                'baptism_place': p.baptism_place,
                'death_year':    p.death_year,
                'death_place':   p.death_place,
                'has_parents':   p.has_parents,
                'events':        list(p.events_summary),
                'notes':         list(p.note_texts),
            }
        return result

    def to_families_ext(self) -> dict:
        """
        Dict compatible con parse_gramps_extended() de general.
        Formato: {id → {'id','husband','wife','children','marriage_year',
                         'marriage_place','marriage_notes'}}
        """
        result = {}
        for handle, fam in self.families.items():
            husband_id = self.persons[fam.husband_handle].id if fam.husband_handle and fam.husband_handle in self.persons else fam.husband_handle
            wife_id = self.persons[fam.wife_handle].id if fam.wife_handle and fam.wife_handle in self.persons else fam.wife_handle
            child_ids = [
                self.persons[ch].id if ch in self.persons else ch
                for ch in fam.child_handles
            ]
            result[fam.id] = {
                'id':              fam.id,
                'husband':         husband_id,
                'wife':            wife_id,
                'children':        list(dict.fromkeys(child_ids)),
                'marriage_year':   fam.marriage_year,
                'marriage_place':  fam.marriage_place,
                'marriage_notes':  list(fam.marriage_notes),
            }
        return result

    def to_places_map(self) -> dict:
        """Dict {handle → {'id','name','lat','lon'}} compatible con testigos."""
        return {
            handle: {'id': handle, 'name': pl.name, 'lat': pl.lat, 'lon': pl.lon}
            for handle, pl in self.places.items()
        }

    def to_gramps_index(self) -> tuple:
        """
        Builds (persons_map, id_map) compatible with index_gramps().
        persons_map: {normalize(name) → [{id, name, birth_year, death_year, birth_place,
                      death_place, birth_lat, birth_lon, death_lat, death_lon,
                      notes, parents, spouses, children}]}
        id_map:      {gramps_id → name}
        """
        from collections import defaultdict as _dd
        from modules.shared.utils import normalize_name as _norm

        # Place name → (lat, lon) lookup
        place_coords: dict = {}
        for pl in self.places.values():
            if pl.name and pl.name not in place_coords:
                place_coords[pl.name] = (pl.lat, pl.lon)

        # Build family relationships (per person handle)
        parents_map: dict = _dd(list)     # phandle → [name]
        spouses_map: dict = _dd(list)     # phandle → [name]
        children_map: dict = _dd(list)    # phandle → [name]

        for fam in self.families.values():
            h_h = fam.husband_handle
            w_h = fam.wife_handle
            h_name = self.persons[h_h].name if h_h and h_h in self.persons else None
            w_name = self.persons[w_h].name if w_h and w_h in self.persons else None

            if h_h and w_name:
                spouses_map[h_h].append(w_name)
            if w_h and h_name:
                spouses_map[w_h].append(h_name)

            child_names = [self.persons[ch].name for ch in fam.child_handles if ch in self.persons]
            if h_h:
                children_map[h_h].extend(child_names)
            if w_h:
                children_map[w_h].extend(child_names)

            parent_names = [n for n in (h_name, w_name) if n]
            for ch in fam.child_handles:
                if ch in self.persons:
                    parents_map[ch].extend(parent_names)

        persons_map: dict = {}
        id_map: dict = {}

        for phandle, person in self.persons.items():
            if not person.name:
                continue

            id_map[str(person.id)] = person.name

            blat, blon = place_coords.get(person.birth_place or '', (None, None))
            dlat, dlon = place_coords.get(person.death_place or '', (None, None))

            k = _norm(person.name)
            persons_map.setdefault(k, []).append({
                'id':          str(person.id),
                'name':        person.name,
                'birth_year':  person.birth_year,
                'death_year':  person.death_year,
                'birth_place': person.birth_place,
                'death_place': person.death_place,
                'birth_lat':   blat,
                'birth_lon':   blon,
                'death_lat':   dlat,
                'death_lon':   dlon,
                'notes':       list(person.note_texts),
                'parents':     list(dict.fromkeys(parents_map.get(phandle, []))),
                'spouses':     list(dict.fromkeys(spouses_map.get(phandle, []))),
                'children':    list(dict.fromkeys(children_map.get(phandle, []))),
            })

        return persons_map, id_map


# ─────────────────────────────────────────────────────────────────────────────
# Función principal de parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_gramps(content_bytes: bytes) -> GrampsDB:
    """
    Parsea un archivo GRAMPS XML (bytes) y devuelve un GrampsDB.
    Unifica la lógica de los tres parsers anteriores.
    Lanza ValueError si el XML no es parseable.
    """
    if content_bytes[:4] == b'PK\x03\x04':
        raise ValueError("El archivo parece ser un paquete .gpkg. Exporta como 'GRAMPS XML' (.gramps).")

    root, backend = _get_root(content_bytes)

    # ── 1. Notas ──────────────────────────────────────────────────────────────
    notes: dict = {}
    for note_el in _iter_tag(root, 'note', backend):
        nhandle = note_el.get('handle') or note_el.get('id')
        if not nhandle:
            continue
        text = ''
        for ch in note_el.iter():
            if strip_ns(ch.tag).lower() == 'text' and ch.text:
                text = ch.text.strip()
                break
        if not text:
            text = _itertext(note_el)
        notes[nhandle] = text

    # ── 2. Lugares ────────────────────────────────────────────────────────────
    places: dict = {}
    for pl_el in _iter_tag(root, 'placeobj', backend):
        ph = pl_el.get('handle') or pl_el.get('id')
        if not ph:
            continue
        pname = plat = plon = None
        for ch in pl_el:
            ctag = strip_ns(ch.tag).lower()
            if ctag in ('pname', 'ptitle', 'title', 'name'):
                pname = ch.get('value') or (ch.text.strip() if ch.text else None)
            elif ctag == 'coord':
                try:
                    plat = float(ch.get('lat') or ch.get('latitude') or '')
                except (TypeError, ValueError):
                    pass
                try:
                    plon = float(ch.get('long') or ch.get('lon') or ch.get('longitude') or '')
                except (TypeError, ValueError):
                    pass
        places[ph] = GrampsPlace(handle=ph, name=pname or '', lat=plat, lon=plon)

    # ── 3. Personas (primer paso: nombres y event refs) ───────────────────────
    persons: dict = {}
    person_handle_to_id: dict = {}
    person_eventrefs: dict = defaultdict(list)   # pid → [event_handle]
    fam_children: dict = defaultdict(list)        # fam_handle → [pid]
    fam_parents_map: dict = defaultdict(list)     # fam_handle → [pid]

    for p_el in _iter_tag(root, 'person', backend):
        pid = p_el.get('id') or p_el.get('handle') or f"UNKNOWN_{len(persons)+1}"
        phandle = p_el.get('handle') or pid
        if phandle != pid:
            person_handle_to_id[phandle] = pid

        name = sex = raw_birth_date = None
        person_notes: list = []

        for ch in p_el:
            ctag = strip_ns(ch.tag).lower()

            if ctag == 'noteref':
                nh = ch.get('hlink') or ch.get('handle')
                if nh and nh in notes:
                    person_notes.append(notes[nh])

            elif ctag in ('name', 'names'):
                first = last = ''
                for n in ch:
                    nt = strip_ns(n.tag).lower()
                    if nt in ('full', 'formatted', 'fullname'):
                        if n.text and n.text.strip():
                            name = n.text.strip()
                            break
                    elif nt in ('first', 'given'):
                        if n.text:
                            first = n.text.strip()
                    elif nt in ('last', 'surname', 'family'):
                        if n.text:
                            last = n.text.strip()
                if not name:
                    candidate = (first + ' ' + last).strip()
                    if candidate:
                        name = candidate
                # Also try multi-tag assembly (middle, prefix, suffix)
                if not name:
                    parts = []
                    for n in ch:
                        nt = strip_ns(n.tag).lower()
                        if nt in ('first', 'given', 'middle', 'surname', 'prefix', 'suffix'):
                            txt = _itertext(n)
                            if txt:
                                parts.append(txt)
                    if parts:
                        name = ' '.join(parts)

            elif ctag in ('gender', 'sex'):
                sex = ch.text.strip() if ch.text else None

            elif ctag in ('birth', 'birthdate'):
                if list(ch):
                    for gch in ch:
                        gctag = strip_ns(gch.tag).lower()
                        if gctag in ('date', 'date_iso'):
                            raw_birth_date = gch.text.strip() if gch.text else None
                        elif gctag in ('eventref', 'event_ref', 'event'):
                            h = gch.get('hlink') or gch.get('handle') or gch.get('id')
                            if h:
                                person_eventrefs[pid].append(h)
                else:
                    raw_birth_date = ch.text.strip() if ch.text else None

            elif ctag in ('eventref', 'event_ref'):
                h = ch.get('hlink') or ch.get('handle') or ch.get('id')
                if h:
                    person_eventrefs[pid].append(h)

            elif ctag == 'childof':
                fh = ch.get('hlink') or ch.get('handle') or ch.get('ref')
                if fh:
                    fam_children[fh].append(phandle)

            elif ctag == 'parentin':
                fh = ch.get('hlink') or ch.get('handle') or ch.get('ref')
                if fh:
                    fam_parents_map[fh].append(phandle)

        if not name:
            fn = p_el.find('.//{*}fullname') if backend == 'lxml' else None
            if fn is None:
                for desc in p_el.iter():
                    if strip_ns(desc.tag).lower() == 'fullname' and desc.text:
                        name = desc.text.strip()
                        break
            elif fn is not None and fn.text:
                name = fn.text.strip()
        if not name:
            name = pid

        persons[phandle] = GrampsPerson(
            handle=phandle,
            id=pid,
            name=name,
            sex=sex or '',
            birth_year=safe_year(raw_birth_date),
            note_texts=person_notes,
        )

    # ── 4. Eventos (con testigos) ─────────────────────────────────────────────
    # Build primary_person_by_event index first
    primary_by_event: dict = {}
    for phandle, person in persons.items():
        for ev_h in person_eventrefs.get(person.id, []):
            if ev_h not in primary_by_event:
                primary_by_event[ev_h] = (phandle, person.name)

    events: dict = {}
    raw_events: dict = {}   # handle → flat dict for person enrichment later

    for ev_el in _iter_tag(root, 'event', backend):
        ev_handle = ev_el.get('handle')
        ev_id = ev_el.get('id') or ev_handle
        if not ev_handle and not ev_id:
            continue
        key = ev_handle or ev_id

        ev_type = date_iso = place_hlink = None
        ev_notes: list = []
        witnesses: list = []

        for ch in ev_el:
            ctag = strip_ns(ch.tag).lower()

            if ctag == 'type':
                ev_type = (ch.text or '').strip()
            elif ctag == 'dateval':
                date_iso = ch.get('val') or date_iso
            elif ctag in ('date', 'date_iso', 'formatted'):
                if ch.text and ch.text.strip() and not date_iso:
                    date_iso = ch.text.strip()
            elif ctag in ('place', 'place_ref'):
                place_hlink = ch.get('hlink') or ch.get('handle') or place_hlink
            elif ctag == 'noteref':
                nh = ch.get('hlink') or ch.get('handle')
                if nh and nh in notes:
                    ev_notes.append(notes[nh])
            elif ctag == 'attribute':
                if ch.get('type') == 'Witness' and ch.get('value'):
                    wit_name = ch.get('value').strip()
                    wit_notes: list = []
                    for noteref in ch:
                        if strip_ns(noteref.tag).lower() == 'noteref':
                            nh = noteref.get('hlink') or noteref.get('handle')
                            if nh and nh in notes:
                                wit_notes.append(notes[nh])
                    witnesses.append(GrampsWitness(name=wit_name, note=' | '.join(wit_notes)))

        subject_handle, subject_name = primary_by_event.get(key, (None, ''))
        if not subject_handle:
            subject_handle, subject_name = primary_by_event.get(ev_id or '', (None, ''))

        ev_obj = GrampsEvent(
            handle=key,
            id=ev_id or key,
            type=ev_type or 'Unknown',
            date_iso=date_iso,
            place_handle=place_hlink,
            subject_handle=subject_handle,
            subject_name=subject_name or '',
            witnesses=witnesses,
            note_texts=ev_notes,
        )
        events[key] = ev_obj
        raw_events[key] = {
            'date': date_iso,
            'type': (ev_type or '').lower(),
            'place_handle': place_hlink,
            'place_name': places[place_hlink].name if place_hlink and place_hlink in places else None,
        }
        # Index by gramps id too when it differs from handle (needed for person_eventrefs lookups)
        if ev_id and ev_id != key and ev_id not in events:
            raw_events[ev_id] = raw_events[key]
            # Don't put ev_obj in events by gramps_id — to_witness_events iterates events once

    # ── 4b. Familias como sujeto para eventos de matrimonio ───────────────────
    event_to_family_subject: dict = {}  # ev_handle → "Padre + Madre"
    for fam_el in _iter_tag(root, 'family', backend):
        father_hlink = wife_hlink = None
        fam_evref_handles: list = []
        for ch in fam_el:
            ctag = strip_ns(ch.tag).lower()
            if ctag == 'father':
                father_hlink = ch.get('hlink') or ch.get('handle')
            elif ctag == 'mother':
                wife_hlink = ch.get('hlink') or ch.get('handle')
            elif ctag == 'eventref':
                h = ch.get('hlink') or ch.get('handle')
                if h:
                    fam_evref_handles.append(h)
        father_name = persons[father_hlink].name if father_hlink and father_hlink in persons else ''
        mother_name = persons[wife_hlink].name if wife_hlink and wife_hlink in persons else ''
        if father_name or mother_name:
            subject = (f"{father_name} + {mother_name}" if father_name and mother_name
                       else father_name or mother_name)
            for h in fam_evref_handles:
                if h in events and not events[h].subject_name:
                    events[h].subject_name = subject

    # ── 5. Enriquecer personas con datos de eventos ───────────────────────────
    BIRTH_T = {'birth', 'nacimiento'}
    BAPTISM_T = {'baptism', 'christening', 'christen', 'bautismo', 'bautizo',
                 'christened', 'baptized', 'baptised'}
    DEATH_T = {'death', 'burial', 'cremation', 'entierro', 'defunción',
               'defuncion', 'óbito', 'obito'}

    for pid, ev_handles in person_eventrefs.items():
        # pid aquí es el GRAMPS id — localizar persona
        # Buscar por id
        person = next((p for p in persons.values() if p.id == pid), None)
        if person is None:
            continue
        for h in ev_handles:
            raw = raw_events.get(h)
            if not raw:
                continue
            ev_type_l = raw['type']
            year = safe_year(raw['date'])
            place = raw['place_name']
            person.event_handles.append(h)
            person.events_summary.append({'type': ev_type_l, 'year': year, 'place': place})

            if ev_type_l in BIRTH_T:
                if not person.birth_year and year:
                    person.birth_year = year
                if not person.birth_place and place:
                    person.birth_place = place
            elif ev_type_l in BAPTISM_T:
                if not person.baptism_year and year:
                    person.baptism_year = year
                if not person.baptism_place and place:
                    person.baptism_place = place
            elif ev_type_l in DEATH_T:
                if not person.death_year and year:
                    person.death_year = year
                if not person.death_place and place:
                    person.death_place = place

    # ── 6. Familias ───────────────────────────────────────────────────────────
    fam_handle_to_fid: dict = {}
    families: dict = {}

    for fam_el in _iter_tag(root, 'family', backend):
        fid = fam_el.get('id') or fam_el.get('handle') or f"FAM_{len(families)+1}"
        fhandle = fam_el.get('handle') or fid

        husband_h = wife_h = None
        children_h: list = []
        fam_evref_handles2: list = []

        for ch in fam_el:
            ctag = strip_ns(ch.tag).lower()
            if ctag == 'father':
                husband_h = ch.get('hlink') or ch.get('handle')
            elif ctag == 'mother':
                wife_h = ch.get('hlink') or ch.get('handle')
            elif ctag in ('childref', 'child'):
                ch_h = ch.get('hlink') or ch.get('handle')
                if ch_h:
                    children_h.append(ch_h)
            elif ctag == 'eventref':
                h = ch.get('hlink') or ch.get('handle')
                if h:
                    fam_evref_handles2.append(h)

        mar_year = mar_place = None
        mar_notes: list = []
        MARRIAGE_T = {'marriage', 'matrimonio', 'casamiento', 'married'}
        for h in fam_evref_handles2:
            raw = raw_events.get(h)
            if raw and raw['type'] in MARRIAGE_T:
                mar_year = safe_year(raw['date'])
                mar_place = raw['place_name']
                mar_notes = events[h].note_texts if h in events else []
                break

        fam = GrampsFamily(
            handle=fhandle,
            id=fid,
            husband_handle=husband_h,
            wife_handle=wife_h,
            child_handles=children_h,
            marriage_year=mar_year,
            marriage_place=mar_place,
            marriage_notes=mar_notes,
        )
        families[fhandle] = fam
        fam_handle_to_fid[fhandle] = fid

    # Reconciliar <childof> / <parentin> de personas
    for fh, child_phandles in fam_children.items():
        if fh not in families:
            families[fh] = GrampsFamily(handle=fh, id=fh)
        families[fh].child_handles.extend(child_phandles)

    for fh, parent_phandles in fam_parents_map.items():
        if fh not in families:
            families[fh] = GrampsFamily(handle=fh, id=fh)
        fam = families[fh]
        if parent_phandles and not fam.husband_handle:
            fam.husband_handle = parent_phandles[0]
        if len(parent_phandles) >= 2 and not fam.wife_handle:
            fam.wife_handle = parent_phandles[1]

    # Deduplicar children y marcar has_parents
    children_set: set = set()
    for fam in families.values():
        fam.child_handles = list(dict.fromkeys(fam.child_handles))
        children_set.update(fam.child_handles)

    for phandle, person in persons.items():
        if phandle in children_set:
            person.has_parents = True

    # ── 7. Construir índice personas_by_id ────────────────────────────────────
    persons_by_gramps_id = {p.id: handle for handle, p in persons.items()}

    return GrampsDB(
        persons=persons,
        persons_by_gramps_id=persons_by_gramps_id,
        families=families,
        events=events,
        places=places,
        notes=notes,
    )
