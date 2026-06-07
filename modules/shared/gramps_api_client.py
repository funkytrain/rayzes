"""
Cliente REST para Gramps Web API.

Produce un GrampsDB idéntico al que genera parse_gramps() en gramps_parser.py,
permitiendo usar la API de Gramps Web como fuente de datos alternativa al
file upload de .gramps XML.

Uso público:
    token = get_token(base_url, username, password)
    db    = fetch_gramps_db(base_url, token)
"""

from __future__ import annotations

from dataclasses import field
from typing import Optional

import requests

from modules.shared.gramps_parser import (
    GrampsDB,
    GrampsEvent,
    GrampsFamily,
    GrampsPlace,
    GrampsPerson,
    GrampsWitness,
)
from modules.shared.utils import safe_year

# ─────────────────────────────────────────────────────────────────────────────
# Autenticación
# ─────────────────────────────────────────────────────────────────────────────

def get_token(base_url: str, username: str, password: str) -> str:
    """Obtiene un JWT access_token de Gramps Web. Lanza requests.HTTPError si falla."""
    url = f"{base_url.rstrip('/')}/api/token"
    resp = requests.post(url, json={"username": username, "password": password}, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ─────────────────────────────────────────────────────────────────────────────
# Paginación
# ─────────────────────────────────────────────────────────────────────────────

_PAGESIZE = 200

def _paginate(base_url: str, endpoint: str, token: str, extra: dict | None = None) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    all_items: list = []
    page = 0
    while True:
        params = {"page": page, "pagesize": _PAGESIZE}
        if extra:
            params.update(extra)
        resp = requests.get(
            f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}",
            headers=headers,
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        # La API puede devolver lista directa o wrapper {"data": [...]}
        items = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            break
        all_items.extend(items)
        if len(items) < _PAGESIZE:
            break
        page += 1
    return all_items


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de mapeo
# ─────────────────────────────────────────────────────────────────────────────

_GENDER_MAP = {1: "M", 0: "F", 2: "U"}

_MARRIAGE_T = {"marriage", "matrimonio", "casamiento", "married"}
_BIRTH_T    = {"birth", "nacimiento"}
_BAPTISM_T  = {"baptism", "christening", "christen", "bautismo", "bautizo",
               "christened", "baptized", "baptised"}
_DEATH_T    = {"death", "burial", "cremation", "entierro", "defunción",
               "defuncion", "óbito", "obito"}


def _person_full_name(p: dict) -> str:
    pname = p.get("primary_name") or {}
    first = (pname.get("first_name") or "").strip()
    surname_list = pname.get("surname_list") or []
    last = " ".join(
        (s.get("surname") or "").strip()
        for s in surname_list
        if (s.get("surname") or "").strip()
    )
    full = (first + " " + last).strip()
    return full or (p.get("handle") or "")


def _parse_dateval(date_obj: dict | None) -> Optional[str]:
    """Convierte el objeto date de la API Gramps Web a string ISO (año, año-mes o año-mes-día)."""
    if not date_obj:
        return None
    dateval = date_obj.get("dateval")
    if dateval and len(dateval) >= 3:
        day, month, year = dateval[0], dateval[1], dateval[2]
        if year:
            if month and day:
                return f"{year:04d}-{month:02d}-{day:02d}"
            elif month:
                return f"{year:04d}-{month:02d}"
            else:
                return str(year)
    text = (date_obj.get("text") or "").strip()
    return text or None


# ─────────────────────────────────────────────────────────────────────────────
# Constructores de entidades
# ─────────────────────────────────────────────────────────────────────────────

def _build_notes(raw_notes: list) -> dict:
    result = {}
    for n in raw_notes:
        handle = n.get("handle")
        if not handle:
            continue
        text = ((n.get("text") or {}).get("string") or "").strip()
        result[handle] = text
    return result


def _build_places(raw_places: list) -> dict:
    result = {}
    for p in raw_places:
        handle = p.get("handle")
        if not handle:
            continue
        name_obj = p.get("name") or {}
        name = (name_obj.get("value") or p.get("title") or "").strip()
        try:
            lat = float(p["lat"]) if p.get("lat") else None
        except (ValueError, TypeError):
            lat = None
        try:
            lon = float(p["long"]) if p.get("long") else None
        except (ValueError, TypeError):
            lon = None
        result[handle] = GrampsPlace(handle=handle, name=name, lat=lat, lon=lon)
    return result


def _build_persons_first_pass(raw_people: list, notes: dict) -> dict:
    """Primera pasada: construye GrampsPerson con nombre, sexo, notes y event_handles."""
    result = {}
    for p in raw_people:
        handle = p.get("handle")
        if not handle:
            continue
        gramps_id = p.get("gramps_id") or handle
        name = _person_full_name(p)
        sex = _GENDER_MAP.get(p.get("gender"), "")
        note_texts = [notes[nh] for nh in (p.get("note_list") or []) if nh in notes]
        has_parents = bool(p.get("parent_family_list"))
        # event_handles se llena aquí con todos los refs; el rol se filtra en _build_events
        event_handles = [ref["ref"] for ref in (p.get("event_ref_list") or []) if ref.get("ref")]
        result[handle] = GrampsPerson(
            handle=handle,
            id=gramps_id,
            name=name,
            sex=sex,
            has_parents=has_parents,
            event_handles=list(event_handles),
            note_texts=note_texts,
        )
    return result


def _build_events(
    raw_events: list,
    notes: dict,
    places: dict,
    persons: dict,
    raw_people: list,
) -> dict:
    """
    Construye GrampsEvent para cada evento.
    - Primary subject: primera persona cuyo event_ref_list tiene role "Primary" (o vacío) para ese evento.
    - Witnesses: desde attribute_list[type=="Witness"] y desde event_ref_list[role=="Witness"] en personas.
    """
    # Construir primary_by_event y witness_persons_by_event desde raw_people
    primary_by_event: dict = {}      # ev_handle → (person_handle, person_name)
    witness_persons_by_event: dict = {}  # ev_handle → [person_name]

    for rp in raw_people:
        phandle = rp.get("handle")
        if not phandle or phandle not in persons:
            continue
        pname = persons[phandle].name
        for ref in (rp.get("event_ref_list") or []):
            ev_h = ref.get("ref")
            if not ev_h:
                continue
            role = (ref.get("role") or "").lower()
            if role == "witness":
                witness_persons_by_event.setdefault(ev_h, []).append(pname)
            elif ev_h not in primary_by_event:
                # role "primary" o vacío → sujeto principal
                primary_by_event[ev_h] = (phandle, pname)

    result = {}
    for ev in raw_events:
        handle = ev.get("handle")
        if not handle:
            continue
        gramps_id = ev.get("gramps_id") or handle
        ev_type = (ev.get("type") or "Unknown")
        date_iso = _parse_dateval(ev.get("date"))
        place_handle = ev.get("place") or None

        note_texts = [notes[nh] for nh in (ev.get("note_list") or []) if nh in notes]

        # Testigos desde attribute_list
        witnesses: list = []
        for attr in (ev.get("attribute_list") or []):
            if (attr.get("type") or "").strip() == "Witness":
                wit_name = (attr.get("value") or "").strip()
                if not wit_name:
                    continue
                wit_note_texts = [notes[nh] for nh in (attr.get("note_list") or []) if nh in notes]
                witnesses.append(GrampsWitness(name=wit_name, note=" | ".join(wit_note_texts)))

        # Testigos desde personas con role="Witness" en este evento
        for pname in witness_persons_by_event.get(handle, []):
            witnesses.append(GrampsWitness(name=pname, note=""))

        subj_handle, subj_name = primary_by_event.get(handle, (None, ""))

        result[handle] = GrampsEvent(
            handle=handle,
            id=gramps_id,
            type=ev_type,
            date_iso=date_iso,
            place_handle=place_handle,
            subject_handle=subj_handle,
            subject_name=subj_name or "",
            witnesses=witnesses,
            note_texts=note_texts,
        )
    return result


def _fix_family_event_subjects(raw_families: list, persons: dict, events: dict) -> None:
    """
    Asigna subject_name compuesto ("Padre + Madre") a eventos de matrimonio
    cuyo subject_name está vacío, igual que el parser XML en el paso 4b.
    """
    for rf in raw_families:
        father_h = rf.get("father_handle")
        mother_h = rf.get("mother_handle")
        father_name = persons[father_h].name if father_h and father_h in persons else ""
        mother_name = persons[mother_h].name if mother_h and mother_h in persons else ""
        if not (father_name or mother_name):
            continue
        subject = (
            f"{father_name} + {mother_name}" if father_name and mother_name
            else father_name or mother_name
        )
        for ref in (rf.get("event_ref_list") or []):
            ev_h = ref.get("ref")
            if ev_h and ev_h in events and not events[ev_h].subject_name:
                events[ev_h].subject_name = subject


def _build_families(
    raw_families: list,
    persons: dict,
    events: dict,
    notes: dict,
    places: dict,
) -> dict:
    result = {}
    for rf in raw_families:
        handle = rf.get("handle")
        if not handle:
            continue
        gramps_id = rf.get("gramps_id") or handle
        father_h = rf.get("father_handle") or None
        mother_h = rf.get("mother_handle") or None
        child_handles = [cr["ref"] for cr in (rf.get("child_ref_list") or []) if cr.get("ref")]

        mar_year = mar_place = None
        mar_notes: list = []
        for ref in (rf.get("event_ref_list") or []):
            ev_h = ref.get("ref")
            if ev_h and ev_h in events:
                ev = events[ev_h]
                if ev.type.lower() in _MARRIAGE_T:
                    mar_year = safe_year(ev.date_iso)
                    place_obj = places.get(ev.place_handle or "") if ev.place_handle else None
                    mar_place = place_obj.name if place_obj else None
                    mar_notes = list(ev.note_texts)
                    break

        result[handle] = GrampsFamily(
            handle=handle,
            id=gramps_id,
            husband_handle=father_h,
            wife_handle=mother_h,
            child_handles=list(dict.fromkeys(child_handles)),
            marriage_year=mar_year,
            marriage_place=mar_place,
            marriage_notes=mar_notes,
        )
    return result


def _enrich_persons(persons: dict, events: dict, places: dict, families: dict) -> None:
    """
    Enriquece GrampsPerson con birth/baptism/death year+place y events_summary.
    También marca has_parents basándose en child_handles de familias.
    Muta persons in-place, igual que el parser XML en el paso 5.
    """
    # Marcar has_parents desde families (complementa el first pass)
    children_set: set = set()
    for fam in families.values():
        children_set.update(fam.child_handles)

    for phandle, person in persons.items():
        if phandle in children_set:
            person.has_parents = True

        for ev_h in person.event_handles:
            ev = events.get(ev_h)
            if not ev:
                continue
            ev_type_l = ev.type.lower()
            year = safe_year(ev.date_iso)
            place_obj = places.get(ev.place_handle or "") if ev.place_handle else None
            place_name = place_obj.name if place_obj else None

            person.events_summary.append({"type": ev_type_l, "year": year, "place": place_name})

            if ev_type_l in _BIRTH_T:
                if not person.birth_year and year:
                    person.birth_year = year
                if not person.birth_place and place_name:
                    person.birth_place = place_name
            elif ev_type_l in _BAPTISM_T:
                if not person.baptism_year and year:
                    person.baptism_year = year
                if not person.baptism_place and place_name:
                    person.baptism_place = place_name
            elif ev_type_l in _DEATH_T:
                if not person.death_year and year:
                    person.death_year = year
                if not person.death_place and place_name:
                    person.death_place = place_name


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada público
# ─────────────────────────────────────────────────────────────────────────────

def fetch_gramps_db(base_url: str, token: str) -> GrampsDB:
    """
    Descarga todos los datos genealógicos de la API de Gramps Web y devuelve
    un GrampsDB idéntico al producido por parse_gramps() desde XML.

    Lanza requests.HTTPError o requests.ConnectionError ante fallos de red.
    """
    raw_notes    = _paginate(base_url, "/api/notes",    token)
    raw_places   = _paginate(base_url, "/api/places",   token)
    raw_people   = _paginate(base_url, "/api/people",   token)
    raw_events   = _paginate(base_url, "/api/events",   token)
    raw_families = _paginate(base_url, "/api/families", token)

    notes   = _build_notes(raw_notes)
    places  = _build_places(raw_places)
    persons = _build_persons_first_pass(raw_people, notes)
    events  = _build_events(raw_events, notes, places, persons, raw_people)

    _fix_family_event_subjects(raw_families, persons, events)

    families = _build_families(raw_families, persons, events, notes, places)
    _enrich_persons(persons, events, places, families)

    persons_by_gramps_id = {p.id: handle for handle, p in persons.items()}

    return GrampsDB(
        persons=persons,
        persons_by_gramps_id=persons_by_gramps_id,
        families=families,
        events=events,
        places=places,
        notes=notes,
    )
