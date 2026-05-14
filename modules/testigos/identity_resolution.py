"""
Motor de resolución de identidad entre testigos y personas del árbol GRAMPS.

Cruza sistemáticamente todos los testigos canónicos del dataset contra todas
las personas del árbol, usando el modelo bayesiano existente para generar una
cola de trabajo priorizada.

Funciones puras — sin imports de Streamlit.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from difflib import SequenceMatcher

from modules.shared.utils import haversine_km, normalize_name


# ─────────────────────────────────────────────────────────────────────────────
# Modelo de datos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CandidatePair:
    witness_name: str
    pid: str                          # GrampsPerson.handle (clave interna)
    gramps_id: str                    # GrampsPerson.id, e.g. "I0042"
    person_name: str
    witness_year_min: Optional[int]
    witness_year_max: Optional[int]
    person_birth_year: Optional[int]
    person_death_year: Optional[int]
    witness_places: list = field(default_factory=list)
    person_places: list  = field(default_factory=list)
    bayesian_result: Optional[dict]  = field(default=None)
    probability: float               = 0.0
    recommendation: str              = "different"   # auto_merge | review | different


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _witness_year_range(events: list[dict]) -> tuple[Optional[int], Optional[int]]:
    """Rango de años de actividad de un testigo a partir de sus eventos."""
    years = []
    for ev in events:
        date = ev.get('date_iso') or ''
        if date:
            try:
                y = int(str(date)[:4])
                if 1000 < y < 2100:
                    years.append(y)
            except Exception:
                pass
    if not years:
        return None, None
    return min(years), max(years)


def _witness_places(events: list[dict]) -> list[str]:
    """Lista de lugares únicos de los eventos de un testigo."""
    return list({ev.get('place_name', '') for ev in events if ev.get('place_name')})


def _witness_coords(events: list[dict]) -> Optional[tuple[float, float]]:
    """Centroide aproximado (lat, lon) de los eventos del testigo, o None."""
    lats, lons = [], []
    for ev in events:
        lat = ev.get('lat')
        lon = ev.get('lon')
        try:
            if lat is not None and lon is not None:
                lats.append(float(lat))
                lons.append(float(lon))
        except Exception:
            pass
    if lats and lons:
        return sum(lats) / len(lats), sum(lons) / len(lons)
    return None


def _person_to_events(pid: str, gramps_db: Any) -> list[dict]:
    """
    Convierte los eventos de un GrampsPerson al schema de eventos de testigos:
    {date_iso, place_name, lat, lon, subj_name, witness_raw, note}

    Resuelve coordenadas buscando place_name en gramps_db.places.values().
    """
    person = gramps_db.persons.get(pid)
    if not person:
        return []

    # Índice nombre→lugar para resolución de coordenadas
    place_by_name: dict[str, Any] = {}
    for pl in gramps_db.places.values():
        if pl.name and pl.name not in place_by_name:
            place_by_name[pl.name] = pl

    events = []
    for ev_summary in person.events_summary:
        place_name = ev_summary.get('place') or ''
        place = place_by_name.get(place_name)
        lat = place.lat if place else None
        lon = place.lon if place else None
        year = ev_summary.get('year')
        date_iso = str(year) if year else ''
        events.append({
            'date_iso':    date_iso,
            'place_name':  place_name,
            'lat':         lat,
            'lon':         lon,
            'subj_name':   person.name,
            'witness_raw': '',
            'note':        '',
        })
    return events


def _person_coords(pid: str, gramps_db: Any) -> Optional[tuple[float, float]]:
    """Coordenadas del lugar de nacimiento de la persona, o None."""
    person = gramps_db.persons.get(pid)
    if not person:
        return None
    birth_place = person.birth_place or person.baptism_place
    if not birth_place:
        return None
    for pl in gramps_db.places.values():
        if pl.name == birth_place and pl.lat is not None and pl.lon is not None:
            return pl.lat, pl.lon
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Construcción de pares candidatos
# ─────────────────────────────────────────────────────────────────────────────

def _name_similarity(a: str, b: str) -> float:
    """Similitud de nombre normalizado en [0, 1]. Rápido — para pre-filtro."""
    na = normalize_name(a)
    nb = normalize_name(b)
    if not na or not nb:
        return 0.0
    try:
        from rapidfuzz import fuzz as _fuzz
        return _fuzz.token_sort_ratio(na, nb) / 100.0
    except Exception:
        return SequenceMatcher(None, na, nb).ratio()


def _build_name_index(gramps_db: Any) -> dict[str, list[str]]:
    """
    Índice inverso normalizado: token → [pid, ...].
    Permite pre-filtrar personas por coincidencia de token de nombre
    en O(tokens × personas_por_token) en lugar de O(testigos × personas).
    """
    index: dict[str, list[str]] = {}
    for pid, person in gramps_db.persons.items():
        tokens = normalize_name(person.name).split()
        for tok in tokens:
            if len(tok) >= 3:
                index.setdefault(tok, []).append(pid)
    return index


def build_candidate_pairs(
    by_witness: dict[str, list[dict]],
    gramps_db: Any,
    already_confirmed: set[str],
    already_discarded: set[str],
    time_window: int = 50,
    geo_km: float = 100.0,
    max_pairs: int = 5_000,
    name_threshold: float = 0.60,
) -> tuple[list[CandidatePair], bool]:
    """
    Cruza cada testigo canónico contra personas del árbol con nombre similar.

    Filtros en orden de coste creciente:
    1. Filtro de nombre (token index + similitud ≥ name_threshold): elimina >99% de pares
    2. Filtro temporal: la persona tiene un año dentro de [w_min-window, w_max+window]
    3. Filtro geográfico: si ambos tienen coordenadas, distancia ≤ geo_km

    Returns:
        (pares, truncated)  — truncated=True si se alcanzó max_pairs.
    """
    pairs: list[CandidatePair] = []
    truncated = False

    # Índice nombre → pids para pre-filtro rápido
    name_index = _build_name_index(gramps_db)

    for witness_name, events in by_witness.items():
        if not witness_name:
            continue
        if witness_name in already_confirmed or witness_name in already_discarded:
            continue

        w_year_min, w_year_max = _witness_year_range(events)
        w_coords = _witness_coords(events)
        w_places = _witness_places(events)
        w_norm = normalize_name(witness_name)
        w_tokens = [tok for tok in w_norm.split() if len(tok) >= 3]

        # Candidatos por token: unión de personas que comparten al menos un token
        candidate_pids: set[str] = set()
        for tok in w_tokens:
            candidate_pids.update(name_index.get(tok, []))

        # Si no hay candidatos por token, intentar con el primer token de 2+ chars
        if not candidate_pids:
            for tok in w_norm.split():
                if len(tok) >= 2:
                    candidate_pids.update(name_index.get(tok, []))
                    break

        for pid in candidate_pids:
            person = gramps_db.persons.get(pid)
            if not person:
                continue

            # ── Filtro de nombre (similitud) ──────────────────────────────────
            sim = _name_similarity(witness_name, person.name)
            if sim < name_threshold:
                continue

            # ── Filtro temporal ───────────────────────────────────────────────
            if w_year_min is not None and w_year_max is not None:
                p_years = [
                    y for y in [person.birth_year, person.baptism_year, person.death_year]
                    if y is not None
                ]
                if p_years:
                    lo = w_year_min - time_window
                    hi = w_year_max + time_window
                    if not any(lo <= y <= hi for y in p_years):
                        continue

            # ── Filtro geográfico ─────────────────────────────────────────────
            if w_coords is not None:
                p_coords = _person_coords(pid, gramps_db)
                if p_coords is not None:
                    dist = haversine_km(w_coords[0], w_coords[1], p_coords[0], p_coords[1])
                    if dist is not None and dist > geo_km:
                        continue

            p_places = list({
                pl for pl in [person.birth_place, person.baptism_place, person.death_place]
                if pl
            })

            pairs.append(CandidatePair(
                witness_name     = witness_name,
                pid              = pid,
                gramps_id        = person.id,
                person_name      = person.name,
                witness_year_min = w_year_min,
                witness_year_max = w_year_max,
                person_birth_year= person.birth_year or person.baptism_year,
                person_death_year= person.death_year,
                witness_places   = w_places,
                person_places    = p_places,
            ))

            if len(pairs) >= max_pairs:
                truncated = True
                return pairs, truncated

    return pairs, truncated


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_candidate_pairs(
    pairs: list[CandidatePair],
    by_witness: dict[str, list[dict]],
    gramps_db: Any,
    places_index: dict,
) -> list[CandidatePair]:
    """
    Puntúa cada par usando bayesian_identity_probability() de analysis.py.

    Muta bayesian_result, probability y recommendation en cada CandidatePair.
    Devuelve la misma lista.
    """
    from modules.testigos.analysis import bayesian_identity_probability  # importación local para evitar ciclos

    for pair in pairs:
        events_a = by_witness.get(pair.witness_name, [])
        events_b = _person_to_events(pair.pid, gramps_db)

        try:
            result = bayesian_identity_probability(
                events_a   = events_a,
                events_b   = events_b,
                name_a     = pair.witness_name,
                name_b     = pair.person_name,
                places_index = places_index,
            )
            pair.bayesian_result = result
            pair.probability     = result.get('probability_same_person', 0.0)
            pair.recommendation  = result.get('recommendation', 'different')
        except Exception:
            pair.probability    = 0.0
            pair.recommendation = 'different'

    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# Priorización
# ─────────────────────────────────────────────────────────────────────────────

_REC_ORDER = {'auto_merge': 0, 'review': 1, 'different': 2}


def prioritize_pairs(scored_pairs: list[CandidatePair]) -> list[CandidatePair]:
    """
    Ordena: auto_merge primero, luego review, luego different.
    Dentro de cada tier, probabilidad descendente.
    """
    return sorted(
        scored_pairs,
        key=lambda p: (_REC_ORDER.get(p.recommendation, 3), -p.probability),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────

def _pair_to_dict(pair: CandidatePair) -> dict:
    """Serializa un CandidatePair a dict, compactando bayesian_result."""
    d = asdict(pair)
    # Compactar bayesian_result: omitir social_detail (verboso) si existe
    if d.get('bayesian_result'):
        br = dict(d['bayesian_result'])
        br.pop('social_detail', None)
        d['bayesian_result'] = br
    return d


def load_resolution_results(path: Path) -> list[dict]:
    """Lee data/identity_resolution_results.json. Devuelve [] si no existe."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return []


def save_resolution_results(pairs: list[CandidatePair], path: Path) -> bool:
    """
    Serializa la lista de pares y escribe a path.
    Devuelve True si tuvo éxito.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [_pair_to_dict(p) for p in pairs]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return True
    except Exception:
        return False
