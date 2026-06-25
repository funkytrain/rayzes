"""
WitnessDataset — contenedor explícito de datos de testigos.

Reemplaza los 10+ globals implícitos de testigos/app.py con un dataclass
con interfaz clara. build_witness_dataset() es una función pura (sin Streamlit,
sin globals, sin I/O) que puede ser testeada en aislamiento.

Integración en testigos/app.py:
    dataset = build_witness_dataset(gramps_db, store, df_notes, df_super, map_controls)
    st.session_state['tst_dataset'] = dataset
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from modules.shared.utils import normalize_name
from modules.testigos.analysis import apply_confirmations_to_df


@dataclass
class WitnessDataset:
    # Tablas principales
    df: pd.DataFrame
    df_places: pd.DataFrame
    df_notes: pd.DataFrame
    df_super: pd.DataFrame

    # Índices derivados
    places_index: dict          # {place_name → {id, name, lat, lon}}
    subj_id_map: dict           # {subj_id → subj_name}
    gramps_index: dict          # {normalize(name) → [{id, name, birth_year, ...}]}
    gramps_id_map: dict         # {gramps_id → name}
    by_witness: dict            # {normalize(witness) → [event_dicts]}
    subj_family_label_map: dict = field(default_factory=dict)
    # {subj_id → "F0015 (Apellido1 · Apellido2)"}
    # Para cada evento de bautismo, el subj_id es el handle del hijo; este mapa
    # devuelve la familia de Gramps a la que pertenece como hijo + apellidos de padres.

    # Controles del mapa (desde sidebar)
    map_mode: str = "1 — Migraciones"
    year_from: int = 0
    year_to: int = 9999
    fuzzy: int = 70
    min_apps: int = 2
    max_dist: float = 0.0
    max_years: int = 0

    @property
    def witness_col(self) -> str:
        return 'witness_canon' if 'witness_canon' in self.df.columns else 'witness_raw'


def _build_places_index(df_places: pd.DataFrame) -> dict:
    idx: dict = {}
    if df_places is None or df_places.empty:
        return idx
    for _, r in df_places.iterrows():
        pname = r.get('place_name') or r.get('name') or r.get('title') or ''
        if not pname:
            continue
        try:
            lat = float(r.get('lat')) if r.get('lat') not in (None, '') else None
        except Exception:
            lat = None
        try:
            lon = float(r.get('lon')) if r.get('lon') not in (None, '') else None
        except Exception:
            lon = None
        idx[pname] = {
            'id': r.get('place_id') or r.get('id'),
            'name': pname,
            'lat': lat,
            'lon': lon,
        }
    return idx


def _build_subj_family_label_map(df: pd.DataFrame, gramps_db) -> dict:
    """
    Mapea subj_id (handle del hijo) a una etiqueta legible de familia Gramps:
    "F0015 (Garcia Mallo · Riesco Rodriguez)"

    Para cada subj_id, busca en qué GrampsFamily aparece ese handle como hijo,
    luego formatea: ID de familia Gramps + apellidos compuestos de los padres.
    Si no hay familia, usa el subj_name directamente.
    """
    if df is None or df.empty or gramps_db is None:
        return {}

    # handle del hijo → GrampsFamily (el sujeto es el niño bautizado)
    child_to_fam: dict = {}
    # handle del padre/madre → GrampsFamily (el sujeto es un progenitor)
    parent_to_fam: dict = {}
    for fam in gramps_db.families.values():
        for ch_handle in fam.child_handles:
            if ch_handle not in child_to_fam:
                child_to_fam[ch_handle] = fam
        if fam.husband_handle and fam.husband_handle not in parent_to_fam:
            parent_to_fam[fam.husband_handle] = fam
        if fam.wife_handle and fam.wife_handle not in parent_to_fam:
            parent_to_fam[fam.wife_handle] = fam

    def _compound_surname(name: str) -> str:
        """Extrae apellido compuesto: últimas 2 palabras del nombre completo."""
        parts = (name or '').split()
        if len(parts) >= 3:
            return ' '.join(parts[-2:])
        return name.strip()

    def _fam_label(fam) -> str:
        husband = gramps_db.persons.get(fam.husband_handle) if fam.husband_handle else None
        wife = gramps_db.persons.get(fam.wife_handle) if fam.wife_handle else None
        surname_parts = []
        if husband and husband.name:
            surname_parts.append(_compound_surname(husband.name))
        if wife and wife.name:
            surname_parts.append(_compound_surname(wife.name))
        label = fam.id
        if surname_parts:
            label += f" ({' · '.join(surname_parts)})"
        return label

    result: dict = {}
    for _, r in df.iterrows():
        sid = r.get('subj_id')
        if not sid or str(sid) in ('nan', 'None', ''):
            continue
        sid_str = str(sid)
        if sid_str in result:
            continue

        # Buscar primero como hijo (caso más común: bautismo donde el sujeto es el niño)
        fam = child_to_fam.get(sid_str)
        if fam:
            result[sid_str] = _fam_label(fam)
            continue

        # Fallback: buscar como progenitor (el sujeto es padre o madre)
        fam = parent_to_fam.get(sid_str)
        if fam:
            result[sid_str] = _fam_label(fam)
            continue

        # Último recurso: usar subj_name del evento
        sname = r.get('subj_name')
        if sname and str(sname) not in ('nan', 'None', ''):
            result[sid_str] = str(sname)

    return result


def _build_subj_id_map(df: pd.DataFrame) -> dict:
    result: dict = {}
    for _, r in df.iterrows():
        sid = r.get('subj_id')
        if sid and r.get('subj_name'):
            result[str(sid)] = r.get('subj_name')
    return result


def _build_by_witness(df: pd.DataFrame) -> dict:
    bw: dict = defaultdict(list)
    witness_col = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
    alt_col = 'witness_raw' if witness_col == 'witness_canon' else None
    for _, r in df.iterrows():
        raw = r.get(witness_col) or (r.get(alt_col) if alt_col else '') or r.get('witness_norm') or ''
        wnorm = normalize_name(raw)
        if not wnorm:
            continue
        bw[wnorm].append(dict(r))
    return bw


def build_witness_dataset(
    gramps_db,
    store,
    df_notes: pd.DataFrame,
    df_super: pd.DataFrame,
    map_controls: Optional[dict] = None,
) -> WitnessDataset:
    """
    Factory pura: sin Streamlit, sin globals, sin I/O lateral.
    gramps_db: GrampsDB (de modules.shared.gramps_parser)
    store:     ConfirmedLinksStore (de modules.shared.confirmed_links_store)
    """
    # ── Eventos y lugares desde GrampsDB ─────────────────────────────────────
    events_data = gramps_db.to_witness_events()
    places_map = gramps_db.to_places_map()

    if not events_data:
        df = pd.DataFrame()
        df_places = pd.DataFrame()
    else:
        df = pd.DataFrame(events_data)
        df['witness_norm'] = df['witness_raw'].apply(normalize_name)

        places_list = [
            {
                'place_id': pid,
                'place_name': pdata['name'],
                'name': pdata['name'],
                'lat': pdata['lat'],
                'lon': pdata['lon'],
            }
            for pid, pdata in places_map.items()
        ]
        df_places = pd.DataFrame(places_list) if places_list else pd.DataFrame()

    # ── Aplicar confirmaciones ────────────────────────────────────────────────
    store.load()
    conf = store.get_all()
    if not df.empty:
        df = apply_confirmations_to_df(df, conf)

    # ── Índices derivados ─────────────────────────────────────────────────────
    places_index = _build_places_index(df_places)
    subj_id_map = _build_subj_id_map(df) if not df.empty else {}
    gramps_index, gramps_id_map = gramps_db.to_gramps_index()
    by_witness = _build_by_witness(df) if not df.empty else defaultdict(list)
    subj_family_label_map = _build_subj_family_label_map(df, gramps_db) if not df.empty else {}

    # ── Controles del mapa ────────────────────────────────────────────────────
    ctrl = map_controls or {}

    return WitnessDataset(
        df=df,
        df_places=df_places,
        df_notes=df_notes if df_notes is not None else pd.DataFrame(),
        df_super=df_super if df_super is not None else pd.DataFrame(),
        places_index=places_index,
        subj_id_map=subj_id_map,
        gramps_index=gramps_index,
        gramps_id_map=gramps_id_map,
        by_witness=by_witness,
        subj_family_label_map=subj_family_label_map,
        map_mode=ctrl.get('map_mode', '1 — Migraciones'),
        year_from=ctrl.get('year_from', 0),
        year_to=ctrl.get('year_to', 9999),
        fuzzy=ctrl.get('fuzzy', 70),
        min_apps=ctrl.get('min_apps', 2),
        max_dist=ctrl.get('max_dist', 0.0),
        max_years=int(ctrl.get('max_years', 0)),
    )
