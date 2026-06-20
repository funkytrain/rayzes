# updated_app_v3_fixed_v3.py
# Genealogía Testigos — versión REAL con sistema de confirmación integrado
# Guarda como UTF-8 (preferible UTF-8 with BOM)

import os
import re
import json
import math
import tempfile
import datetime
from pathlib import Path
from collections import Counter, defaultdict
import streamlit as st
import pandas as pd
from modules.shared.utils import (
    strip_accents, normalize_name, haversine_km, year_from_date_str,
)
from modules.shared.gramps_parser import parse_gramps as _parse_gramps_shared
from modules.shared.confirmed_links_store import ConfirmedLinksStore
from modules.testigos.dataset import WitnessDataset, build_witness_dataset

# Optional libraries (graceful fallback)
try:
    import folium
except Exception:
    folium = None
try:
    import networkx as nx
except Exception:
    nx = None
try:
    from pyvis.network import Network
except Exception:
    Network = None
try:
    from lxml import etree
except Exception:
    etree = None
import re as _re
try:
    from dateutil import parser as _dateutil_parser
    def _parse_gramps_date(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return pd.NaT
        s = str(val).strip()
        if not s:
            return pd.NaT
        try:
            return pd.Timestamp(_dateutil_parser.parse(s))
        except Exception:
            return pd.NaT
except ImportError:
    def _parse_gramps_date(val):
        if val is None:
            return pd.NaT
        return pd.to_datetime(val, errors='coerce')

def _parse_date_series(series):
    """Parse a Series of GRAMPS date strings into Timestamps (object dtype).

    pandas 2.x dropped dateutil fallback for out-of-range dates (pre-1970),
    so we go through dateutil directly.  The resulting column is object dtype
    (mix of Timestamp / NaT); callers that need min/max groupby should use
    _year_from_date_str + integer arithmetic instead.
    """
    return series.map(_parse_gramps_date)

_year_from_date_str = year_from_date_str  # alias — función real en modules.shared.utils

def _year_series(series):
    """Return a Series of int years from GRAMPS date strings (NaN where unparseable)."""
    return series.map(_year_from_date_str).astype('Int64')

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
WITNESS_CSV  = DATA_DIR / "witnesses_final_bom.csv"
PLACES_CSV   = DATA_DIR / "places_report_bom.csv"
NOTES_CSV    = DATA_DIR / "notes_report_bom.csv"
SUPER_CSV    = DATA_DIR / "superpadrinos_final_bom.csv"
GRAMPS_PATH  = DATA_DIR / "data.gramps"
CONFIRMED_PATH               = DATA_DIR / "confirmed_links.json"
NOTE_CATEGORY_OVERRIDES_PATH   = DATA_DIR / "note_category_overrides.json"
IDENTITY_RESOLUTION_FILE       = DATA_DIR / "identity_resolution_results.json"

# Almacén tipado para confirmed_links.json — punto único de acceso
_store = ConfirmedLinksStore(CONFIRMED_PATH)

def load_note_category_overrides() -> dict:
    """Carga correcciones manuales de categoría (texto_nota → categoría)."""
    if NOTE_CATEGORY_OVERRIDES_PATH.exists():
        try:
            return json.loads(NOTE_CATEGORY_OVERRIDES_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def save_note_category_overrides(overrides: dict):
    NOTE_CATEGORY_OVERRIDES_PATH.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2), encoding='utf-8'
    )

import uuid
from datetime import datetime, timezone as _tz

from translations import t, get_lang
from modules.testigos.surname_systems import SURNAME_SYSTEMS, get_system, list_systems_for_selector
from modules.testigos.possible_relatives import (
    detect_possible_relatives,
    possible_relatives_to_dict,
    possible_relatives_from_dict,
    aggregate_by_witness,
    aggregate_by_surname,
    CONFIDENCE_NONE, CONFIDENCE_VERY_LOW, CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM, CONFIDENCE_HIGH,
    CONFIDENCE_LABELS_ES, CONFIDENCE_LABELS_EN, CONFIDENCE_COLORS,
    _CONFIDENCE_RANK,
)

USER = "admin"

# ---------------- Utilities ----------------
# strip_accents, normalize_name, haversine_km, year_from_date_str → modules.shared.utils
normalize = normalize_name  # alias para compatibilidad con llamadas existentes

def embed_folium(m, width=900, height=600):
    if folium is None:
        st.error(t("err_folium_no"))
        return
    html = m._repr_html_()
    st.components.v1.html(html, width=width, height=height)

# ---------------- Confirmaciones: carga/guardar y canonical mapping ----------------
from difflib import SequenceMatcher

def load_confirmations():
    """Carga el JSON de confirmaciones desde disco (delega a ConfirmedLinksStore)."""
    _store.load()
    return _store.get_all()

def save_confirmations(conf):
    """Guarda confirmaciones en disco (delega a ConfirmedLinksStore)."""
    ok = _store.save_dict(conf, user=USER)
    if not ok:
        st.error(t("err_guardar_conf", e="write error"))
    return ok

# Similarity function (prefer rapidfuzz if available)
try:
    from rapidfuzz import fuzz
    def name_similarity(a,b):
        if not a or not b: return 0
        return int(fuzz.token_sort_ratio(str(a), str(b)))
except Exception:
    def name_similarity(a,b):
        a = str(a or "").lower(); b = str(b or "").lower()
        if not a or not b: return 0
        return int(SequenceMatcher(None, a, b).ratio() * 100)

def build_canonical_map_from_conf(conf):
    cmap = {}
    for canon, raws in conf.get('same', {}).items():
        for r in raws:
            cmap[r] = canon
        cmap.setdefault(canon, canon)
    return cmap

def apply_confirmations_to_df(df_in, conf):
    cmap = build_canonical_map_from_conf(conf)
    def map_raw(r):
        if r in cmap:
            return cmap[r]
        return r
    d = df_in.copy()
    if 'witness_raw' not in d.columns:
        if 'witness_norm' in d.columns:
            d['witness_raw'] = d['witness_norm'].astype(str)
        else:
            d['witness_raw'] = ''
    d['witness_canon'] = d['witness_raw'].apply(lambda x: map_raw(str(x)))
    return d

# ---------------- Data loading ----------------
@st.cache_data(ttl=3600)
def load_csv(path):
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p, encoding='utf-8-sig')
    except Exception:
        try:
            return pd.read_csv(p, encoding='utf-8', errors='ignore', engine='python')
        except Exception:
            return pd.DataFrame()

def load_data_from_xml_or_csv():
    """
    Carga datos desde el archivo GRAMPS subido por el usuario o desde la API.
    Retorna: df, df_places
    """
    override_db = st.session_state.get("_gramps_web_db_override")
    if override_db is not None:
        events_data = override_db.to_witness_events()
        places_map  = override_db.to_places_map()
    else:
        xml_path = st.session_state.get('tst_gramps_xml_path')
        if not xml_path or not Path(xml_path).exists():
            return pd.DataFrame(), pd.DataFrame()
        events_data, _, places_map = parse_gramps_xml_full(xml_path)

    if not events_data:
        st.warning(t("data_no_extraer_xml"))
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(events_data)
    places_list = [
        {
            'place_id': pid,
            'place_name': pdata['name'],
            'name': pdata['name'],
            'lat': pdata['lat'],
            'lon': pdata['lon']
        }
        for pid, pdata in places_map.items()
    ]
    df_places = pd.DataFrame(places_list) if places_list else pd.DataFrame()
    df['witness_norm'] = df['witness_raw'].apply(normalize)
    return df, df_places

# Carga de datos en render_page() → build_witness_dataset()

# ---------------- GRAMPS parsing: unified shared parser (cached by content) ----------------
@st.cache_data(ttl=3600, show_spinner=False)
def _load_gramps_db(content_bytes: bytes):
    """Parsea bytes de GRAMPS XML → GrampsDB (cacheado por contenido, no por ruta)."""
    return _parse_gramps_shared(content_bytes)


# ---------------- GRAMPS parsing: FULL extraction ----------------
@st.cache_data(ttl=3600)
def parse_gramps_xml_full(gramps_path):
    """Parsea el XML de GRAMPS. Delega al parser unificado de modules.shared.gramps_parser."""
    p = Path(gramps_path)
    if not p.exists():
        st.error(t("err_archivo_no_encontrado", path=gramps_path))
        return [], {}, {}
    try:
        db = _load_gramps_db(p.read_bytes())
    except ValueError as e:
        st.error(t("err_parse_xml", e=e))
        return [], {}, {}
    except Exception as e:
        st.error(t("err_parse_xml", e=e))
        import traceback
        st.error(traceback.format_exc())
        return [], {}, {}
    return db.to_witness_events(), {}, db.to_places_map()

# ---------------- GRAMPS indexing (delegated to GrampsDB.to_gramps_index) ----------------
@st.cache_data(ttl=3600)
def index_gramps(gramps_path):
    """
    Indexa personas del árbol GRAMPS.
    Retorna (persons_map, id_map) delegando al parser unificado.
    """
    p = Path(gramps_path)
    if not p.exists():
        return {}, {}
    try:
        db = _load_gramps_db(p.read_bytes())
        return db.to_gramps_index()
    except Exception:
        return {}, {}

# gramps_index, gramps_id_map, by_witness, subj_id_map son accesibles via WitnessDataset

# ---------------- Resolve active GRAMPS path ----------------
def get_active_gramps_path():
    """Devuelve la ruta (str) al archivo GRAMPS subido por el usuario, o None si no está disponible."""
    ss_path = st.session_state.get('tst_gramps_xml_path')
    if ss_path and Path(ss_path).exists():
        return ss_path
    return None

# ---------------- family_label (robust) ----------------
def family_label(node, _gramps_id_map=None, _subj_id_map=None, _df=None):
    try:
        s = str(node)
        if s.startswith("F:ID:"):
            pid = s.split("F:ID:")[-1]
            pid_s = str(pid)
            gmap = _gramps_id_map or {}
            smap = _subj_id_map or {}
            if pid_s in gmap and gmap[pid_s]:
                return gmap[pid_s]
            if pid_s in smap and smap[pid_s]:
                return smap[pid_s]
            if _df is not None:
                try:
                    df_match = _df[_df['subj_id'].astype(str)==pid_s]
                    if not df_match.empty and df_match.iloc[0].get('subj_name'):
                        return df_match.iloc[0].get('subj_name')
                except Exception:
                    pass
            return f"(ID:{pid_s})"
        if s.startswith("F:SN:"):
            return s.replace("F:SN:","").strip() or "Family"
        return s
    except Exception:
        return str(node)

# ---------------- Helper analysis functions ----------------
def build_family_graph(df_in, use_person_id_if_available=True):
    if 'nx' not in globals() or nx is None:
        return None
    G = nx.Graph()
    for _, r in df_in.iterrows():
        wit_norm = normalize(r.get('witness_canon') or r.get('witness_raw') or r.get('witness_norm') or "")
        if not wit_norm:
            continue
        wit_raw = r.get('witness_raw') or r.get('witness_canon') or ""
        if not wit_raw.strip():
            continue
        subj_node = f"F:ID:{r.get('subj_id')}" if use_person_id_if_available and r.get('subj_id') else f"F:SN:{(r.get('subj_name') or '').split()[-1] if r.get('subj_name') else ''}"
        wit_node = f"W:{wit_norm}::{wit_raw}"
        G.add_node(subj_node, type='family')
        G.add_node(wit_node, type='witness')
        if G.has_edge(subj_node, wit_node):
            G[subj_node][wit_node]['weight'] += 1
        else:
            G.add_edge(subj_node, wit_node, weight=1)
    return G

def build_place_connections(by_witness_map, places_index_map, year_from=0, year_to=9999, min_apps=1, max_km=0.0, fuzzy_threshold=70):
    connections = Counter()
    conn_examples = defaultdict(list)
    def year_ok(date_iso):
        if date_iso is None or pd.isna(date_iso) or str(date_iso).strip()=="": return True
        try:
            y = _parse_gramps_date(date_iso).year
            if year_from>0 and y<year_from: return False
            if year_to<9999 and y>year_to: return False
            return True
        except:
            return True
    for wnorm, events in by_witness_map.items():
        if fuzzy_threshold and fuzzy_threshold>0 and len(wnorm) < 2:
            pass
        places = [ev.get('place_name') for ev in events if ev.get('place_name') and year_ok(ev.get('date_iso'))]
        places = [p for p in places if p]
        if len(set(places)) < 2: continue
        cnts = Counter(places)
        unique = sorted(set(places))
        for i in range(len(unique)):
            for j in range(i+1, len(unique)):
                p1 = unique[i]; p2 = unique[j]
                if cnts[p1] < min_apps or cnts[p2] < min_apps: continue
                info1 = places_index_map.get(p1, {}); info2 = places_index_map.get(p2, {})
                lat1 = info1.get('lat'); lon1 = info1.get('lon'); lat2 = info2.get('lat'); lon2 = info2.get('lon')
                if lat1 not in (None,'') and lat2 not in (None,'') and max_km and float(max_km)>0:
                    dist = haversine_km(lat1, lon1, lat2, lon2)
                    if dist is None:
                        pass
                    else:
                        if dist > float(max_km): continue
                connections[(p1,p2)] += 1
                conn_examples[(p1,p2)].append({'witness': wnorm, 'count_p1': cnts[p1], 'count_p2': cnts[p2]})
    return connections, conn_examples

def reciprocity_pairs(df_in, min_count=1):
    pairs = Counter()
    for _, r in df_in.iterrows():
        subj = r.get('subj_id') or normalize(r.get('subj_name') or "")
        wit = normalize(r.get('witness_canon') or r.get('witness_norm') or "")
        if not subj or not wit: continue
        pairs[(str(subj), wit)] += 1
    res=[]; seen=set()
    for (a,w),cnt in pairs.items():
        rev = pairs.get((w,a),0)
        if rev and (a,w) not in seen and (w,a) not in seen and cnt>=min_count and rev>=min_count:
            res.append({'entity_a':a,'entity_b':w,'count_ab':int(cnt),'count_ba':int(rev)})
            seen.add((a,w)); seen.add((w,a))
    return pd.DataFrame(res)

def generations_compadrazgo(df, max_year_gap=35, min_gap=8, max_results=500):
    """
    Versión optimizada: agrupa por apellido en lugar de comparar par a par (O(n²)).
    Para cada apellido de sujeto, busca si el mismo apellido aparece como testigo
    en un evento posterior dentro del rango de años, indicando posible continuidad generacional.
    """
    df2 = df.copy()
    df2["year"] = _year_series(df2["date_iso"])
    df2 = df2[df2["year"].notna()].copy()
    if df2.empty:
        return pd.DataFrame()
    df2["year"] = df2["year"].astype(int)

    df2["subj_name"] = df2["subj_name"].fillna("").astype(str).str.strip()
    df2["witness_norm"] = df2["witness_norm"].fillna("").astype(str).str.strip()

    def last_surname(x):
        x = str(x or "").strip()
        return x.split()[-1] if x else ""

    df2["subj_surname"] = df2["subj_name"].apply(last_surname)
    df2["wit_surname"] = df2["witness_norm"].apply(last_surname)
    df2 = df2.sort_values("year").reset_index(drop=True)

    # Index: surname -> list of (year, subj_name, wit_surname, subj_surname)
    subj_by_sn = defaultdict(list)
    wit_by_sn = defaultdict(list)
    for _, r in df2.iterrows():
        if r["subj_surname"]:
            subj_by_sn[r["subj_surname"]].append(r)
        if r["wit_surname"]:
            wit_by_sn[r["wit_surname"]].append(r)

    rows = []
    seen = set()
    # For each surname, cross subj events with wit events in the gap range
    all_sn = set(subj_by_sn.keys()) & set(wit_by_sn.keys())
    for sn in all_sn:
        subj_events = subj_by_sn[sn]  # events where sn is the subject surname
        wit_events = wit_by_sn[sn]    # events where sn is the witness surname
        for pe in subj_events:        # "parent generation" event
            for ce in wit_events:     # "child generation" event (witness carries the surname)
                gap = ce["year"] - pe["year"]
                if gap < min_gap or gap > max_year_gap:
                    continue
                key = (pe.name, ce.name)
                if key in seen:
                    continue
                seen.add(key)
                score = 2  # same surname subj→wit
                if pe["wit_surname"] == ce["wit_surname"] and pe["wit_surname"]:
                    score += 2
                if pe["subj_surname"] == ce["subj_surname"] and pe["subj_surname"]:
                    score += 1
                rows.append({
                    "child_event_date": ce["date_dt"],
                    "child_subject": ce["subj_name"],
                    "child_subj_surname": ce["subj_surname"],
                    "child_witness_surname": ce["wit_surname"],
                    "parent_event_date": pe["date_dt"],
                    "parent_subject": pe["subj_name"],
                    "parent_subj_surname": pe["subj_surname"],
                    "parent_witness_surname": pe["wit_surname"],
                    "year_gap": gap,
                    "score": score,
                })
                if len(rows) >= max_results:
                    break
            if len(rows) >= max_results:
                break

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

def stability_mobility_stats(by_witness_map, places_index_map):
    rows=[]
    for w, events in by_witness_map.items():
        coords=[]; years=[]
        for ev in events:
            info = places_index_map.get(ev.get('place_name'), {})
            lat = info.get('lat'); lon = info.get('lon')
            if lat in (None,'') or lon in (None,''): continue
            coords.append((float(lat), float(lon), ev.get('place_name')))
            try:
                y = _parse_gramps_date(ev.get('date_iso')).year
            except:
                y=None
            if y and not pd.isna(y): years.append(int(y))
        if not coords: continue
        unique_places = len(set([c[2] for c in coords]))
        dists=[]; prev=None
        for lat,lon,pname in coords:
            if prev:
                km = haversine_km(prev[0], prev[1], lat, lon)
                if km is not None: dists.append(km)
            prev=(lat,lon)
        avg_km = sum(dists)/len(dists) if dists else 0.0
        span = (max(years)-min(years)) if years else 0
        rows.append({'witness':w,'unique_places':unique_places,'avg_km':avg_km,'span_years':span,'appearances':len(events)})
    return pd.DataFrame(rows).sort_values('appearances', ascending=False)

def apply_event_confirmations_and_rebuild_witness_canon(df_ref, by_witness_ref):
    """
    Aplica todas las confirmaciones guardadas a df_ref y reconstruye witness_canon.
    Retorna (df_modificado, by_witness_nuevo) — no modifica estado global.

    Lógica:
    1. MERGE (event_groups): todos los eventos de un grupo reciben el mismo
       witness_canon = nombre más frecuente del grupo.  Si hay varios grupos
       con el mismo nombre base se numeran: "Ana Garcia (1)", "Ana Garcia (2)".
    2. DIFERENTES: pares de event_ids marcados como diferentes.
    3. Eventos sin confirmación: mantienen witness_raw como canon salvo colisión.
    """
    df = df_ref

    conf = load_confirmations()
    event_groups = conf.get('event_groups', {})   # gid -> [event_id, ...]
    different_pairs = conf.get('different', [])   # [[eid_a, eid_b], ...]

    # ── Paso 1: mapa event_id -> group_id ──────────────────────────────────
    event_to_gid: dict[str, str] = {}
    for gid, evlist in event_groups.items():
        for ev in evlist:
            event_to_gid[str(ev)] = str(gid)

    # ── Paso 2: nombre representativo de cada group_id ──────────────────────
    # El nombre más frecuente entre los witness_raw de los eventos del grupo.
    gid_name_counts: dict[str, Counter] = defaultdict(Counter)
    for _, row in df.iterrows():
        eid = str(row.get('event_id', ''))
        wraw = str(row.get('witness_raw') or row.get('witness_norm') or '')
        gid = event_to_gid.get(eid)
        if gid:
            gid_name_counts[gid][wraw] += 1

    gid_repr: dict[str, str] = {}
    for gid, cnts in gid_name_counts.items():
        gid_repr[gid] = cnts.most_common(1)[0][0] if cnts else f'identity_{gid[:8]}'

    # ── Paso 3: detectar grupos con el mismo nombre base → numerarlos ───────
    # norm_name -> lista ordenada de gids que tienen ese nombre
    norm_to_gids: dict[str, list[str]] = defaultdict(list)
    for gid, rep in gid_repr.items():
        norm_to_gids[normalize(rep)].append(gid)

    # gid -> canon final (con sufijo si hay colisión)
    gid_canon: dict[str, str] = {}
    for norm, gids in norm_to_gids.items():
        if len(gids) == 1:
            gid_canon[gids[0]] = gid_repr[gids[0]]          # único: sin sufijo
        else:
            for idx, gid in enumerate(gids, start=1):
                gid_canon[gid] = f'{gid_repr[gid]} ({idx})'  # varios: numerar

    # ── Paso 4: pares "diferentes" → union-find de grupos de identidad ───────
    # Dos event_ids marcados como diferentes NUNCA pueden compartir canon.
    # Construimos grupos de eventos que SÍ son la misma persona usando union-find,
    # respetando que los event_groups ya son la verdad de los merges.
    # Para los eventos SIN group_id, usamos el event_id como su propio grupo.

    uf_parent: dict[str, str] = {}

    def _uf_find(x: str) -> str:
        uf_parent.setdefault(x, x)
        if uf_parent[x] != x:
            uf_parent[x] = _uf_find(uf_parent[x])
        return uf_parent[x]

    def _uf_union(a: str, b: str) -> None:
        ra, rb = _uf_find(a), _uf_find(b)
        if ra != rb:
            uf_parent[rb] = ra

    # Inicializar: cada event_id pertenece a su gid (o a sí mismo si no tiene gid)
    all_eids = df['event_id'].astype(str).unique()
    for eid in all_eids:
        gid = event_to_gid.get(eid, eid)   # si no tiene grupo, su propio ID
        uf_parent.setdefault(eid, gid)
        uf_parent.setdefault(gid, gid)
        _uf_union(eid, gid)

    # Los eventos del mismo group_id ya están unidos; los "diferentes" NO se unen
    # (no hace falta hacer nada más con union-find para los diferentes:
    # simplemente NO llamamos _uf_union para esos pares)

    # ── Paso 5: para eventos sin gid, detectar si comparten nombre con un gid ──
    # Si un evento libre (sin group_id) tiene el mismo nombre normalizado que un
    # grupo confirmado, necesita un canon distinto para no confundirse.
    # Recopilamos los nombres normalizados de todos los grupos confirmados.
    confirmed_norm_names: set[str] = {normalize(v) for v in gid_canon.values()}

    # ── Paso 6: asignar witness_canon a cada fila del dataframe ──────────────
    # Para eventos sin group_id ni confirmación de "diferente": agrupamos por
    # witness_raw normalizado, pero si ese nombre colisiona con uno confirmado,
    # lo marcamos con sufijo especial para indicar "sin confirmar".

    # Primero, identificar qué nombres normalizados libres colisionan
    free_norm_counter: Counter = Counter()
    for _, row in df.iterrows():
        eid = str(row.get('event_id', ''))
        if eid not in event_to_gid:
            wraw = str(row.get('witness_raw') or row.get('witness_norm') or '')
            free_norm_counter[normalize(wraw)] += 1

    # Construir el canon final fila a fila
    canon_col = []
    identity_col = []

    for _, row in df.iterrows():
        eid = str(row.get('event_id', ''))
        wraw = str(row.get('witness_raw') or row.get('witness_norm') or '')
        gid = event_to_gid.get(eid)

        if gid:
            # Evento en un group_id confirmado
            canon = gid_canon.get(gid, wraw)
            identity = gid
        else:
            # Evento libre: mantener witness_raw, pero numerar si colisiona con
            # un nombre de grupo confirmado (para no mezclarlos visualmente)
            norm = normalize(wraw)
            if norm in confirmed_norm_names:
                # Buscar cuántos grupos confirmados tienen este nombre
                n_confirmed = sum(
                    1 for v in gid_canon.values() if normalize(v).startswith(norm)
                )
                canon = f'{wraw} ({n_confirmed + 1})'
            else:
                canon = wraw
            identity = eid  # identidad = propio evento (sin confirmar)

        canon_col.append(canon)
        identity_col.append(identity)

    df['witness_canon'] = canon_col
    df['witness_identity'] = identity_col

    # ── Paso 7: reconstruir by_witness con el nuevo canon ────────────────────
    new_by_witness: dict[str, list] = defaultdict(list)
    for _, r in df.iterrows():
        key = normalize(str(r.get('witness_canon') or r.get('witness_raw') or ''))
        if key:
            new_by_witness[key].append(dict(r))

    # Snapshot CSV opcional para depuración externa
    try:
        df.to_csv('witnesses_applied_event_groups.csv', index=False, encoding='utf-8-sig')
    except Exception:
        pass

    return df, new_by_witness


def apply_event_confirmations_and_rebuild_witness_canon_from(dataset: 'WitnessDataset'):
    """Opera sobre dataset.df y actualiza dataset.by_witness. Sin globals."""
    dataset.df, dataset.by_witness = apply_event_confirmations_and_rebuild_witness_canon(
        dataset.df, dataset.by_witness
    )


def surnames_stats(df_in, topn=100):
    dfc = df_in.copy()
    dfc['wit_surname'] = dfc['witness_canon'].astype(str).apply(lambda x: x.split()[-1] if x and len(str(x).split())>0 else "")
    dfc['subj_surname'] = dfc['subj_name'].astype(str).apply(lambda x: x.split()[-1] if x and len(str(x).split())>0 else "")
    wit_counts = dfc['wit_surname'].value_counts().head(topn).reset_index(); wit_counts.columns=['surname','count_wit']
    subj_counts = dfc['subj_surname'].value_counts().head(topn).reset_index(); subj_counts.columns=['surname','count_subj']
    merged = pd.merge(wit_counts, subj_counts, on='surname', how='outer').fillna(0)
    merged = merged.sort_values(['count_wit','count_subj'], ascending=False)
    return merged

def role_analysis(df_in):
    roles = df_in.copy()
    def norm_type(x):
        if x is None: return "unknown"
        s = str(x).strip().lower()
        if s in ("baptism","bautismo","bautism","bautizado","bautismos"):
            return "bautismo"
        if s in ("marriage","matrimonio","matrimonios","wedding"):
            return "matrimonio"
        if s in ("death","defuncion","fallecimiento","defunción"):
            return "defuncion"
        return s or "unknown"
    if 'type' in roles.columns:
        roles['evt_type'] = roles['type'].apply(norm_type)
    else:
        roles['evt_type'] = "unknown"
    roles['witness_norm'] = roles.get('witness_canon', roles.get('witness_norm')).astype(str).fillna("")
    res = roles.groupby(['witness_norm','evt_type']).size().reset_index(name='count')
    if res.empty:
        return pd.DataFrame()
    pivot = res.pivot(index='witness_norm', columns='evt_type', values='count').fillna(0)
    pivot['total'] = pivot.sum(axis=1)
    pivot = pivot.sort_values('total', ascending=False).reset_index()
    return pivot

# ---------------- Fase 3: Timeline de apellidos ----------------

try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_OK = True
except Exception:
    PLOTLY_OK = False

try:
    import jellyfish
    JELLYFISH_OK = True
except Exception:
    jellyfish = None
    JELLYFISH_OK = False

_NOBLE_PARTICLES = {'de', 'del', 'de la', 'de los', 'de las', 'von', 'van', 'la', 'los', 'las', 'el', 'y'}

def extract_surname_improved(full_name):
    """
    Extrae el apellido de un nombre completo, incluyendo partículas nobiliarias
    compuestas ('de la', 'del', 'de los', etc.) que preceden al apellido.
    Ignora títulos al inicio ('Don', 'Doña', 'Fray', etc.).
    """
    if not full_name or str(full_name).strip() in ('', 'nan', 'None'):
        return ""
    tokens = str(full_name).strip().split()
    if not tokens:
        return ""
    # Remove leading titles
    _titles = {'don', 'doña', 'dona', 'fray', 'sor', 'dr', 'dr.', 'dra', 'dra.'}
    while tokens and tokens[0].lower().rstrip('.') in _titles:
        tokens = tokens[1:]
    if not tokens:
        return ""
    # The surname is everything from the last non-particle "base" token
    # together with any immediately-preceding particles.
    # Find the last token that is NOT a simple particle — that is the base of the surname.
    _simple_particles = {'de', 'del', 'von', 'van', 'la', 'el', 'los', 'las', 'y', 'e', 'das', 'dos'}
    # Walk backwards to find the last "real" token
    last_real = len(tokens) - 1
    # Collect surname: last_real plus any preceding particles
    # Build from right: last real word, then prepend particles
    surname_tokens = [tokens[last_real]]
    i = last_real - 1
    while i >= 1:  # keep at least one "first name" token
        tok = tokens[i].lower().rstrip('.')
        if tok in _simple_particles:
            surname_tokens.insert(0, tokens[i])
            i -= 1
        else:
            break
    surname = " ".join(surname_tokens)
    return surname if surname.lower() not in _simple_particles else ""


def surname_timeline_analysis(df_in, min_appearances=3):
    """
    Genera un DataFrame con la primera aparición de cada apellido de testigo,
    ordenado cronológicamente, junto a estadísticas de actividad.
    """
    df_w = df_in.copy()
    col = 'witness_canon' if 'witness_canon' in df_w.columns else 'witness_raw'
    import re as _re
    def _year_from_str(s):
        m = _re.match(r'^(\d{4})', str(s).strip()) if s and str(s) not in ('nan','None','') else None
        return int(m.group(1)) if m else None

    df_w['_sn'] = df_w[col].astype(str).apply(extract_surname_improved)
    df_w['_year'] = df_w['date_iso'].apply(_year_from_str)
    df_w['_date_str'] = df_w['date_iso'].astype(str).str.strip().str[:10]
    df_w = df_w[df_w['_sn'].str.strip() != ""]
    df_w = df_w[df_w['_year'].notna()]

    results = []
    for sn, grp in df_w.groupby('_sn'):
        if len(grp) < min_appearances:
            continue
        first_idx = grp['_year'].idxmin()
        last_idx = grp['_year'].idxmax()
        first_row = grp.loc[first_idx]
        first_year = int(first_row['_year'])
        last_year = int(grp.loc[last_idx, '_year'])
        active_years = last_year - first_year
        total = len(grp)
        unique_families = grp['subj_name'].astype(str).apply(extract_surname_improved).nunique()
        unique_places = grp['place_name'].astype(str).nunique()
        results.append({
            'apellido': sn,
            'primera_aparicion': first_row['_date_str'],
            'ultimo_evento': grp.loc[last_idx, '_date_str'],
            'años_activo': active_years,
            'total_apariciones': total,
            'familias_apadrinadas': unique_families,
            'lugares_distintos': unique_places,
            'primera_persona': first_row.get(col, ""),
            'primer_lugar': first_row.get('place_name', ""),
        })

    if not results:
        return pd.DataFrame()
    out = pd.DataFrame(results).sort_values('primera_aparicion')
    return out.reset_index(drop=True)


def calculate_surname_diversity_over_time(df_in, window_years=10):
    """
    Calcula el índice de Shannon de diversidad de apellidos de testigos
    en ventanas temporales solapadas.
    """
    import math as _math, re as _re2
    df_w = df_in.copy()
    col = 'witness_canon' if 'witness_canon' in df_w.columns else 'witness_raw'
    df_w['_sn'] = df_w[col].astype(str).apply(extract_surname_improved)
    def _yr(s):
        m = _re2.match(r'^(\d{4})', str(s).strip()) if s and str(s) not in ('nan','None','') else None
        return int(m.group(1)) if m else None
    df_w['_year'] = df_w['date_iso'].apply(_yr)
    df_w = df_w[df_w['_sn'].str.strip() != ""]
    df_w = df_w[df_w['_year'].notna()]
    if df_w.empty:
        return pd.DataFrame()

    min_year = int(df_w['_year'].min())
    max_year = int(df_w['_year'].max())
    rows = []
    step = max(1, window_years // 2)
    for y in range(min_year, max_year + 1, step):
        y_end = y + window_years
        mask = (df_w['_year'] >= y) & (df_w['_year'] < y_end)
        sub = df_w[mask]
        if sub.empty:
            continue
        counts = sub['_sn'].value_counts()
        total = counts.sum()
        shannon = -sum((c / total) * _math.log(c / total) for c in counts if c > 0)
        rows.append({
            'periodo': f"{y}–{y_end-1}",
            'año_inicio': y,
            'diversidad_shannon': round(shannon, 4),
            'apellidos_unicos': len(counts),
            'total_apariciones': int(total),
        })
    return pd.DataFrame(rows)


# ---------------- Fase 4: Línea temporal interactiva ----------------

def aggregate_timeline_by_period(df_in, period='decade'):
    """
    Agrega eventos por período temporal. period: 'year' | 'decade'
    Returns: DataFrame con columnas período, total_eventos, testigos_unicos, lugares_unicos, familias_unicas
    """
    import re as _re3
    df_w = df_in.copy()
    col = 'witness_canon' if 'witness_canon' in df_w.columns else 'witness_raw'
    def _yr3(s):
        m = _re3.match(r'^(\d{4})', str(s).strip()) if s and str(s) not in ('nan','None','') else None
        return int(m.group(1)) if m else None
    df_w['_year'] = df_w['date_iso'].apply(_yr3)
    df_w = df_w[df_w['_year'].notna()]
    if df_w.empty:
        return pd.DataFrame()

    if period == 'year':
        df_w['_periodo'] = df_w['_year']
    else:  # decade
        df_w['_periodo'] = (df_w['_year'] // 10 * 10).astype(int)

    rows = []
    for p, grp in df_w.groupby('_periodo'):
        label = str(p) if period == 'year' else f"{p}s"
        wits = grp[col].astype(str).nunique()
        places = grp['place_name'].astype(str).nunique()
        families = grp['subj_name'].astype(str).apply(extract_surname_improved).nunique()
        new_sn = grp[col].astype(str).apply(extract_surname_improved)
        rows.append({
            'periodo': label,
            'año': int(p),
            'total_eventos': len(grp),
            'testigos_unicos': int(wits),
            'lugares_unicos': int(places),
            'familias_unicas': int(families),
            'apellidos_testigos': int(new_sn.nunique()),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values('año').reset_index(drop=True)


def detect_temporal_anomalies(timeline_df, threshold_std=2.0):
    """
    Detecta períodos con picos o caídas anómalas de actividad.
    Returns list of dicts con período, tipo ('peak'|'drop'), valor, desviación
    """
    if timeline_df.empty or 'total_eventos' not in timeline_df.columns:
        return []
    vals = timeline_df['total_eventos'].values
    mean = float(pd.Series(vals).mean())
    std = float(pd.Series(vals).std())
    if std == 0:
        return []
    anomalies = []
    for _, row in timeline_df.iterrows():
        z = (row['total_eventos'] - mean) / std
        if abs(z) >= threshold_std:
            anomalies.append({
                'periodo': row['periodo'],
                'tipo': 'pico' if z > 0 else 'caída',
                'total_eventos': int(row['total_eventos']),
                'z_score': round(z, 2),
            })
    return sorted(anomalies, key=lambda x: abs(x['z_score']), reverse=True)


def calculate_network_metrics_over_time(df_in, period='decade'):
    """
    Calcula métricas de red por período: densidad estimada (testigos/eventos),
    componentes, ratio de nuevas incorporaciones.
    """
    import re as _re4
    df_w = df_in.copy()
    col = 'witness_canon' if 'witness_canon' in df_w.columns else 'witness_raw'
    def _yr4(s):
        m = _re4.match(r'^(\d{4})', str(s).strip()) if s and str(s) not in ('nan','None','') else None
        return int(m.group(1)) if m else None
    df_w['_year'] = df_w['date_iso'].apply(_yr4)
    df_w = df_w[df_w['_year'].notna()]
    if df_w.empty:
        return pd.DataFrame()

    if period == 'year':
        df_w['_periodo'] = df_w['_year']
    else:
        df_w['_periodo'] = (df_w['_year'] // 10 * 10).astype(int)

    seen_witnesses = set()
    rows = []
    for p in sorted(df_w['_periodo'].unique()):
        grp = df_w[df_w['_periodo'] == p]
        wits = set(grp[col].astype(str).dropna())
        new_wits = wits - seen_witnesses
        seen_witnesses |= wits
        label = str(p) if period == 'year' else f"{p}s"
        ratio_new = round(len(new_wits) / max(len(wits), 1), 3)
        rows.append({
            'periodo': label,
            'año': int(p),
            'testigos_periodo': len(wits),
            'testigos_nuevos': len(new_wits),
            'ratio_nuevos': ratio_new,
            'densidad_estimada': round(len(wits) / max(len(grp), 1), 3),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ---------------- UI Pages ----------------

# Ensure session storage for confirmations
def render_sidebar_upload():
    """Uploader GRAMPS para Testigos (parte superior del sidebar)."""
    st.sidebar.markdown(t("sidebar_gramps_header"))

    if st.session_state.get("gramps_web_connected"):
        st.sidebar.info(t("gramps_web_source_active"))
        # Si no hay xml_path en disco, intentar usar shared_gramps_bytes (subido manualmente)
        if 'tst_gramps_xml_path' not in st.session_state or \
                not Path(st.session_state['tst_gramps_xml_path']).exists():
            _shared_bytes = st.session_state.get('shared_gramps_bytes')
            if _shared_bytes:
                _shared_name = st.session_state.get('shared_gramps_name', 'gramps_web.gramps')
                _temp_path = BASE_DIR / f"temp_{_shared_name}"
                _temp_path.write_bytes(_shared_bytes)
                st.session_state['tst_gramps_xml_path'] = str(_temp_path)
            else:
                # Intentar descargar automáticamente desde la API
                _api_url   = st.session_state.get("gramps_web_url_saved", st.session_state.get("gramps_web_url", "")).rstrip("/")
                _api_token = st.session_state.get("gramps_web_token", "")
                if _api_url and _api_token:
                    try:
                        from modules.shared.gramps_api_client import download_gramps_export
                        with st.sidebar:
                            with st.spinner("Descargando datos del servidor..."):
                                _dl_bytes = download_gramps_export(_api_url, _api_token)
                        _temp_path = BASE_DIR / "temp_gramps_web.gramps"
                        _temp_path.write_bytes(_dl_bytes)
                        st.session_state['tst_gramps_xml_path'] = str(_temp_path)
                        st.session_state['shared_gramps_bytes'] = _dl_bytes
                        st.session_state['shared_gramps_name'] = "gramps_web.gramps"
                        st.rerun()
                    except Exception as _e:
                        st.sidebar.warning(
                            f"No se pudo descargar automáticamente ({_e}). "
                            "Sube un archivo .gramps manualmente."
                        )
                        st.sidebar.file_uploader(
                            t("sidebar_gramps_uploader"), type=['gramps', 'xml'],
                            key="tst_api_fallback_upload"
                        )
                else:
                    st.sidebar.warning("Sube un archivo .gramps para usar Testigos.")
                    _uploaded = st.sidebar.file_uploader(
                        t("sidebar_gramps_uploader"), type=['gramps', 'xml'],
                        key="tst_api_fallback_upload"
                    )
                    if _uploaded:
                        _bytes = _uploaded.read()
                        _temp_path = BASE_DIR / f"temp_{_uploaded.name}"
                        _temp_path.write_bytes(_bytes)
                        st.session_state['tst_gramps_xml_path'] = str(_temp_path)
                        st.session_state['shared_gramps_bytes'] = _bytes
                        st.session_state['shared_gramps_name'] = _uploaded.name
                        st.rerun()
    else:
        shared_bytes = st.session_state.get('shared_gramps_bytes')
        shared_name = st.session_state.get('shared_gramps_name', '')

        if shared_bytes and 'tst_gramps_xml_path' not in st.session_state:
            temp_path = BASE_DIR / f"temp_{shared_name}"
            with open(temp_path, 'wb') as f:
                f.write(shared_bytes)
            st.session_state['tst_gramps_xml_path'] = str(temp_path)

        if shared_bytes:
            st.sidebar.success(t("sidebar_gramps_loaded", name=shared_name))
            if st.sidebar.button(t("sidebar_gramps_reload")):
                st.cache_data.clear()
                st.rerun()
        else:
            uploaded_file = st.sidebar.file_uploader(t("sidebar_gramps_uploader"), type=['gramps', 'xml'])
            if uploaded_file is not None:
                file_bytes = uploaded_file.getbuffer().tobytes()
                temp_path = BASE_DIR / f"temp_{uploaded_file.name}"
                with open(temp_path, 'wb') as f:
                    f.write(file_bytes)
                st.session_state['tst_gramps_xml_path'] = str(temp_path)
                st.session_state['shared_gramps_bytes'] = file_bytes
                st.session_state['shared_gramps_name'] = uploaded_file.name
                st.sidebar.success(t("sidebar_gramps_loaded", name=uploaded_file.name))
                if st.sidebar.button(t("sidebar_gramps_reload")):
                    st.cache_data.clear()
                    st.rerun()


def render_sidebar():
    """Controles de subsección de Testigos (radio de páginas)."""
    st.sidebar.markdown("---")
    st.sidebar.markdown(t("sidebar_sections"))
    _menu_options = [
        t("menu_explorar"), t("menu_mapa"), t("menu_grafo"), t("menu_superpadrinos"),
        t("menu_notas"), t("menu_analisis"), t("menu_timeline"), t("menu_confirmar"),
        t("menu_bayesiana"), t("menu_pendientes"), t("menu_trayectoria"), t("menu_informe"),
        t("menu_posibles_familiares"),
        t("menu_identity_resolution"),
    ]
    _override = st.session_state.get('tst_menu_override')
    if _override and _override in _menu_options:
        del st.session_state['tst_menu_override']
        _menu_default_idx = _menu_options.index(_override)
    else:
        saved_text = st.session_state.get('tst_menu', '')
        if saved_text in _menu_options:
            _menu_default_idx = _menu_options.index(saved_text)
        else:
            _menu_default_idx = st.session_state.get('tst_menu_idx', 0)

    # Solo corregir el valor del radio si el texto no pertenece a la lista actual
    # (cambio de idioma). En navegación normal no machacamos el clic del usuario.
    if st.session_state.get('tst_menu', '') not in _menu_options:
        st.session_state['tst_menu'] = _menu_options[_menu_default_idx]

    st.sidebar.radio(t("sidebar_goto"), _menu_options, index=_menu_default_idx, key="tst_menu")

    current_text = st.session_state.get("tst_menu", _menu_options[0])

    # ── Configuración LLM (compartida con RAG assistant) ────────────────────
    st.sidebar.markdown("---")
    st.sidebar.markdown(t("sidebar_llm_header"))
    st.sidebar.text_input(
        t("sidebar_llm_url"),
        value=st.session_state.get("rag_llm_base_url", "http://127.0.0.1:9292/v1"),
        key="rag_llm_base_url",
    )
    st.sidebar.text_input(
        t("sidebar_llm_model"),
        value=st.session_state.get("rag_llm_model", "qwen3-14b"),
        key="rag_llm_model",
    )
    st.session_state['tst_menu_idx'] = _menu_options.index(current_text) if current_text in _menu_options else _menu_default_idx
    st.session_state["tst_active_page"] = _menu_options[st.session_state['tst_menu_idx']]


def render_page(ctx=None):
    """Carga datos e invoca la página activa de Testigos."""
    # Inicializar session_state de confirmaciones
    if 'tst_confirmed_links' not in st.session_state:
        try:
            if CONFIRMED_PATH.exists():
                st.session_state['tst_confirmed_links'] = json.loads(CONFIRMED_PATH.read_text(encoding='utf-8'))
            else:
                st.session_state['tst_confirmed_links'] = load_confirmations()
        except Exception:
            st.session_state['tst_confirmed_links'] = load_confirmations()

    if 'tst_note_category_overrides' not in st.session_state:
        st.session_state['tst_note_category_overrides'] = load_note_category_overrides()

    # ── Resolver fuente de datos GRAMPS ──────────────────────────────────────
    # Prioridad: ctx.gramps.db (API/caché) → bytes del ctx → archivo en disco
    gramps_db = None
    if ctx is not None and ctx.gramps.db is not None:
        gramps_db = ctx.gramps.db
    elif ctx is not None and ctx.gramps.bytes_:
        try:
            gramps_db = _load_gramps_db(ctx.gramps.bytes_)
        except ValueError as e:
            st.error(t("err_parse_xml", e=e))
            return
    else:
        xml_path = st.session_state.get('tst_gramps_xml_path')
        if not xml_path or not Path(xml_path).exists():
            if st.session_state.get("gramps_web_connected"):
                st.info(
                    "Testigos necesita el archivo .gramps completo. "
                    "Sube un archivo .gramps usando el selector del sidebar."
                )
            else:
                st.info(t("sidebar_gramps_header"))
                st.warning(t("data_no_gramps_xml"))
            return
        try:
            gramps_db = _load_gramps_db(Path(xml_path).read_bytes())
        except ValueError as e:
            st.error(t("err_parse_xml", e=e))
            return

    # ── Controles del mapa (sidebar) ─────────────────────────────────────────
    menu = st.session_state.get("tst_active_page", t("menu_explorar"))
    map_controls = {
        'map_mode': '1 — Migraciones',
        'year_from': 0, 'year_to': 9999,
        'fuzzy': 70, 'min_apps': 2, 'max_dist': 0.0,
    }
    if menu == t("menu_mapa"):
        st.sidebar.markdown(t("sidebar_map_controls"))
        map_controls['map_mode'] = st.sidebar.selectbox(
            t("sidebar_map_mode"),
            [t("map_mode_1"), t("map_mode_3"), t("map_mode_4"), t("map_mode_5"), t("map_mode_7"), t("map_mode_9")],
            index=1
        )
        map_controls['year_from'] = st.sidebar.number_input(t("sidebar_year_from"), value=0, min_value=0, max_value=9999)
        map_controls['year_to'] = st.sidebar.number_input(t("sidebar_year_to"), value=9999, min_value=0, max_value=9999)
        map_controls['fuzzy'] = st.sidebar.slider(t("sidebar_fuzzy"), 40, 100, 70)
        map_controls['min_apps'] = st.sidebar.slider(t("sidebar_min_apps"), 1, 20, 2)
        map_controls['max_dist'] = st.sidebar.number_input(t("sidebar_max_dist"), value=0.0, min_value=0.0)

    # ── Construir WitnessDataset ──────────────────────────────────────────────

    _df_notes = load_csv(NOTES_CSV)
    _df_super = load_csv(SUPER_CSV)

    dataset = build_witness_dataset(
        gramps_db=gramps_db,
        store=_store,
        df_notes=_df_notes,
        df_super=_df_super,
        map_controls=map_controls,
    )

    if dataset.df.empty:
        st.error(t("data_no_extraer_xml"))
        return

    # Ensure expected columns exist
    expected_cols = ['subj_id', 'subj_name', 'witness_raw', 'witness_norm',
                     'place_name', 'lat', 'lon', 'date_iso', 'note', 'event_id', 'type']
    for c in expected_cols:
        if c not in dataset.df.columns:
            dataset.df[c] = None
    dataset.df['_date_parsed'] = _parse_date_series(
        dataset.df['date_iso'] if 'date_iso' in dataset.df.columns else pd.Series(dtype=str)
    )

    # Apply event confirmations and rebuild witness_canon
    apply_event_confirmations_and_rebuild_witness_canon_from(dataset)

    # Store dataset and ctx in session_state for sub-renders within this module
    st.session_state['tst_dataset'] = dataset
    st.session_state['tst_df_global'] = dataset.df
    st.session_state['tst_by_witness'] = dataset.by_witness
    st.session_state['tst_ctx'] = ctx

    # Dispatcher — dataset passed explicitly; no module-level globals needed
    if menu == t("menu_explorar"):
        page_explorar(dataset)
    elif menu == t("menu_mapa"):
        page_mapa(dataset)
    elif menu == t("menu_grafo"):
        page_grafo(dataset)
    elif menu == t("menu_superpadrinos"):
        page_superpadrinos(dataset)
    elif menu == t("menu_notas"):
        page_notas(dataset)
    elif menu == t("menu_analisis"):
        page_analisis(dataset)
    elif menu == t("menu_timeline"):
        page_timeline(dataset)
    elif menu == t("menu_confirmar"):
        page_confirmar_coincidencias(dataset)
    elif menu == t("menu_bayesiana"):
        page_bayesian_identidad(dataset)
    elif menu == t("menu_pendientes"):
        page_pendientes(dataset)
    elif menu == t("menu_trayectoria"):
        page_trayectoria_vital(dataset)
    elif menu == t("menu_informe"):
        page_informe(dataset)
    elif menu == t("menu_posibles_familiares"):
        page_posibles_familiares(dataset)
    elif menu == t("menu_identity_resolution"):
        page_identity_resolution(dataset)

    try:
        save_confirmations(load_confirmations())
    except Exception:
        pass

def page_explorar(dataset: WitnessDataset):
    df = dataset.df
    import re as _re_exp
    st.header(t("hdr_explorar"))
    df_f = df.copy()

    with st.expander(t("explorar_filtros"), expanded=True):
        col_q, col_type = st.columns(2)
        with col_q:
            q = st.text_input(t("explorar_buscar"), key="search_explorar")
        with col_type:
            type_options = sorted(df['type'].dropna().astype(str).unique().tolist())
            sel_types = st.multiselect(t("explorar_tipo_evento"), type_options, default=[], key="explorar_types")

        col_place, col_year = st.columns(2)
        with col_place:
            place_options = sorted(df['place_name'].dropna().astype(str).unique().tolist())
            if len(place_options) > 500:
                st.caption(t("explorar_primeros_500", n=len(place_options)))
                place_options = place_options[:500]
            sel_places = st.multiselect(t("explorar_lugar"), place_options, default=[], key="explorar_places")
        with col_year:
            def _yr_exp(s):
                m = _re_exp.match(r'^(\d{4})', str(s).strip()) if s and str(s) not in ('nan', 'None', '') else None
                return int(m.group(1)) if m else None
            years_all = df['date_iso'].apply(_yr_exp).dropna()
            yr_range = None
            if not years_all.empty:
                yr_min, yr_max = int(years_all.min()), int(years_all.max())
                if yr_min < yr_max:
                    yr_range = st.slider(t("explorar_rango_anios"), yr_min, yr_max, (yr_min, yr_max), key="explorar_years")
                else:
                    yr_range = (yr_min, yr_max)
                    st.write(t("explorar_anio_unico", year=yr_min))

    # Aplicar filtros
    if q:
        ql = q.lower()
        df_f = df_f[
            df_f['witness_raw'].astype(str).str.lower().str.contains(ql, na=False) |
            df_f['subj_name'].astype(str).str.lower().str.contains(ql, na=False) |
            df_f['place_name'].astype(str).str.lower().str.contains(ql, na=False)
        ]
    if sel_types:
        df_f = df_f[df_f['type'].astype(str).isin(sel_types)]
    if sel_places:
        df_f = df_f[df_f['place_name'].astype(str).isin(sel_places)]
    if yr_range is not None:
        df_f = df_f.copy()
        df_f['_yr_f'] = df_f['date_iso'].apply(_yr_exp)
        df_f = df_f[df_f['_yr_f'].between(yr_range[0], yr_range[1])]
        df_f = df_f.drop(columns=['_yr_f'])

    st.write(t("explorar_resultados", n=len(df_f), total=len(df)))
    st.dataframe(df_f.head(500), use_container_width=True)

    if not df_f.empty:
        csv_bytes = df_f.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            t("explorar_export_csv"),
            data=csv_bytes,
            file_name="explorar_filtrado.csv",
            mime="text/csv",
            key="explorar_export_csv"
        )

def page_mapa(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    MAP_MODE = dataset.map_mode
    YEAR_FROM = dataset.year_from
    YEAR_TO = dataset.year_to
    FUZZY = dataset.fuzzy
    MIN_APPS = dataset.min_apps
    MAX_DIST = dataset.max_dist
    st.header(t("hdr_mapa"))
    connections, conn_examples = build_place_connections(by_witness, places_index, year_from=YEAR_FROM, year_to=YEAR_TO, min_apps=MIN_APPS, max_km=MAX_DIST, fuzzy_threshold=FUZZY)
    mode = MAP_MODE
    if mode.startswith("1"):
        st.subheader(t("sub_migraciones"))

        # Filtro por categoría de nota (F4)
        map_cat_filter = st.multiselect(
            t("mapa_filtro_categoria"),
            _ALL_NOTE_CATEGORIES, default=[], key="map_cat_filter"
        )
        all_wits = sorted(by_witness.keys())
        if map_cat_filter:
            col_wit = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
            notes_classified = df[df['note'].notna() & (df['note'] != "")].copy()
            notes_classified['_cat'] = notes_classified['note'].apply(classify_note)
            filtered_wits_set = set(
                normalize(str(w))
                for w in notes_classified[notes_classified['_cat'].isin(map_cat_filter)][col_wit].astype(str)
            )
            all_wits = [w for w in all_wits if w in filtered_wits_set]

        sel = st.selectbox(t("mapa_seleccionar_testigo"), [""] + all_wits, key="map_wit_sel2")
        typed = st.text_input(t("mapa_escribe_nombre"), key="map_wit_type2")
        chosen = sel if sel else typed
        if chosen:
            chosen_norm = normalize(chosen)
            evs = by_witness.get(chosen_norm, [])
            if not evs:
                st.info(t("empty_padrino"))
            else:
                coords = []
                for ev in evs:
                    info = places_index.get(ev.get('place_name'), {})
                    lat = info.get('lat'); lon = info.get('lon')
                    if lat in (None, '') or lon in (None, ''): continue
                    note_val = str(ev.get('note', '') or '')
                    if note_val in ('nan', 'None'): note_val = ''
                    coords.append((
                        _parse_gramps_date(ev.get('date_iso')),
                        float(lat), float(lon),
                        ev.get('place_name'), ev.get('event_id'), note_val
                    ))
                coords = sorted(coords, key=lambda x: x[0] if not pd.isna(x[0]) else pd.Timestamp(0))
                if folium is None:
                    st.warning(t("err_folium_no2"))
                else:
                    if coords:
                        center_lat = sum(c[1] for c in coords) / len(coords)
                        center_lon = sum(c[2] for c in coords) / len(coords)
                        m = folium.Map(location=[center_lat, center_lon], zoom_start=8, tiles="CartoDB positron")
                        # AntPath animado para mostrar dirección temporal
                        try:
                            from folium.plugins import AntPath
                            AntPath(
                                locations=[(c[1], c[2]) for c in coords],
                                color='#2979ff', weight=3, opacity=0.8,
                                dash_array=[10, 20], delay=800
                            ).add_to(m)
                        except Exception:
                            folium.PolyLine([(c[1], c[2]) for c in coords], color='#2979ff', weight=3).add_to(m)
                        # Marcadores numerados por orden cronológico
                        for i, (d, lat, lon, pname, eid, note_txt) in enumerate(coords, 1):
                            date_str = str(d.date()) if pd.notna(d) else "?"
                            popup_html = f"<b>{i}. {pname}</b><br>{date_str}<br><small>Evento: {eid}</small>"
                            if note_txt:
                                popup_html += f"<br><i>{note_txt}</i>"
                            folium.Marker(
                                location=[lat, lon],
                                popup=folium.Popup(popup_html, max_width=250),
                                tooltip=f"{i}. {pname} ({date_str})",
                                icon=folium.DivIcon(
                                    html=f'<div style="background:#2979ff;color:white;border-radius:50%;width:24px;height:24px;'
                                         f'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;'
                                         f'border:2px solid white;box-shadow:1px 1px 3px #0006">{i}</div>',
                                    icon_size=(24, 24), icon_anchor=(12, 12)
                                )
                            ).add_to(m)
                        embed_folium(m, width=1100, height=600)
                candidates = gramps_index.get(normalize(chosen), [])
                st.subheader(t("sub_candidatos_gramps"))
                if not candidates:
                    st.info(t("empty_gramps"))
                else:
                    for i,c in enumerate(candidates):
                        st.write(f"{i+1}. {c.get('name')} (ID {c.get('id')})")
                    idx = st.number_input(t("label_num_candidato"), min_value=0, max_value=len(candidates), value=0)
                    if idx>0:
                        cand = candidates[idx-1]
                        if st.button(t("mapa_confirmar"), key="confirm_map"):
                            st.session_state.setdefault('confirmed_links', {})[normalize(chosen)] = {'witness_name': chosen, 'person_id': cand.get('id'), 'person_name': cand.get('name')}
                            try:
                                CONFIRMED_PATH.write_text(json.dumps(st.session_state['tst_confirmed_links'], ensure_ascii=False, indent=2), encoding='utf-8')
                            except:
                                pass
                            st.success(t("guardado"))
    elif mode.startswith("3"):
        st.subheader(t("sub_relaciones_lugares"))
        if not connections:
            st.info(t("empty_conexiones"))
            return
        rows = []
        for (p1,p2),cnt in connections.items():
            info1 = places_index.get(p1, {})
            info2 = places_index.get(p2, {})
            dist = None
            if info1.get('lat') not in (None,'') and info2.get('lat') not in (None,''):
                dist = haversine_km(info1['lat'], info1.get('lon'), info2['lat'], info2.get('lon'))
            exs = conn_examples.get((p1,p2), [])[:5]
            example_wits = ", ".join(sorted(set([e['witness'] for e in exs]))) if exs else ""
            rows.append({'from': p1, 'to': p2, 'count': int(cnt), 'distance_km': (round(dist,1) if dist else None), 'example_witnesses': example_wits})
        df_pairs = pd.DataFrame(rows).sort_values('count', ascending=False)

        # Filtro adicional por distancia máxima en la tabla
        max_distances = [d for d in df_pairs['distance_km'] if d is not None]
        if max_distances:
            max_distance_filter = st.slider(
                t("mapa_filtro_dist"),
                min_value=0.0,
                max_value=float(max(max_distances)),
                value=float(max(max_distances)),
                step=10.0
            )
            df_pairs = df_pairs[
                (df_pairs['distance_km'].isna()) |
                (df_pairs['distance_km'] <= max_distance_filter)
            ]
        if folium:
            def _valid_coord(x):
                if x is None or x == '': return False
                try: return not math.isnan(float(x))
                except: return False
            valid_places = [v for v in places_index.values() if _valid_coord(v.get('lat')) and _valid_coord(v.get('lon'))]
            if valid_places:
                lats = [float(v['lat']) for v in valid_places]
                lons = [float(v['lon']) for v in valid_places]
                m = folium.Map(
                    location=[sum(lats)/len(lats), sum(lons)/len(lons)],
                    zoom_start=8, tiles="CartoDB positron"
                )
                # Escala de color y grosor según count
                counts = [r['count'] for _, r in df_pairs.head(200).iterrows()]
                max_count = max(counts) if counts else 1

                def _connection_color(count, max_c):
                    # Verde claro (pocos) → naranja → rojo (muchos)
                    ratio = count / max_c
                    if ratio < 0.33:
                        return '#43a047'   # verde
                    elif ratio < 0.66:
                        return '#fb8c00'   # naranja
                    else:
                        return '#e53935'   # rojo

                def _midpoint_offset(lat1, lon1, lat2, lon2, factor=0.15):
                    # Punto medio desplazado para simular curva
                    mlat = (lat1 + lat2) / 2
                    mlon = (lon1 + lon2) / 2
                    dlat = lat2 - lat1
                    dlon = lon2 - lon1
                    return mlat - dlon * factor, mlon + dlat * factor

                # Marcadores de lugares (agrupados)
                from folium.plugins import MarkerCluster
                place_cluster = MarkerCluster(name=t("mapa_lugares_cluster")).add_to(m)
                drawn_places = set()

                for _, r in df_pairs.head(200).iterrows():
                    i1 = places_index.get(r['from'])
                    i2 = places_index.get(r['to'])
                    if not i1 or not i2:
                        continue
                    if not _valid_coord(i1.get('lat')) or not _valid_coord(i1.get('lon')):
                        continue
                    if not _valid_coord(i2.get('lat')) or not _valid_coord(i2.get('lon')):
                        continue
                    lat1, lon1 = float(i1['lat']), float(i1['lon'])
                    lat2, lon2 = float(i2['lat']), float(i2['lon'])
                    color = _connection_color(r['count'], max_count)
                    weight = 2 + min(r['count'], 12)
                    wits_preview = r.get('example_witnesses', '') or ''
                    tooltip_txt = f"<b>{t('mapa_tooltip_conexion', from_=r['from'], to_=r['to'], count=r['count'])}</b>"
                    if wits_preview:
                        tooltip_txt += f"<br><small>{wits_preview[:80]}</small>"
                    dist_txt = f"  ·  {r['distance_km']} km" if r.get('distance_km') else ""

                    # Curva aproximada via puntos intermedios
                    mlat, mlon = _midpoint_offset(lat1, lon1, lat2, lon2)
                    n_steps = 12
                    curve_pts = []
                    for step in range(n_steps + 1):
                        t_s = step / n_steps
                        # Interpolación cuadrática de Bézier
                        clat = (1-t_s)**2 * lat1 + 2*(1-t_s)*t_s * mlat + t_s**2 * lat2
                        clon = (1-t_s)**2 * lon1 + 2*(1-t_s)*t_s * mlon + t_s**2 * lon2
                        curve_pts.append((clat, clon))

                    folium.PolyLine(
                        curve_pts, color=color, weight=weight, opacity=0.75,
                        tooltip=folium.Tooltip(tooltip_txt + dist_txt)
                    ).add_to(m)

                    # Marcadores de lugar (solo una vez por lugar)
                    for place_name, lat, lon in [(r['from'], lat1, lon1), (r['to'], lat2, lon2)]:
                        if place_name not in drawn_places:
                            drawn_places.add(place_name)
                            folium.CircleMarker(
                                location=[lat, lon], radius=5,
                                color='#37474f', fill=True, fill_color='white', fill_opacity=0.9,
                                tooltip=place_name
                            ).add_to(m)

                # Leyenda de colores
                legend_html = f"""
                <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                     padding:10px 14px;border-radius:8px;box-shadow:0 2px 6px #0003;font-size:13px;">
                  <b>{t('mapa_leyenda_titulo')}</b><br>
                  <span style="color:#43a047">&#9644;</span> {t('mapa_leyenda_pocas')} &nbsp;
                  <span style="color:#fb8c00">&#9644;</span> {t('mapa_leyenda_medias')} &nbsp;
                  <span style="color:#e53935">&#9644;</span> {t('mapa_leyenda_muchas')}
                </div>"""
                m.get_root().html.add_child(folium.Element(legend_html))
                folium.LayerControl().add_to(m)
                embed_folium(m, width=1100, height=550)
        else:
            st.warning(t("err_folium_no3"))
        st.markdown(t("analisis_tabla_relaciones"))
        st.dataframe(df_pairs[['from','to','count','distance_km','example_witnesses']].head(500), use_container_width=True)
    elif mode.startswith("4"):
        st.subheader(t("sub_centros_atraccion"))
        if not connections:
            st.info(t("empty_datos"))
            return
        center_count = Counter()
        conn_detail = defaultdict(list)
        for (p1,p2),cnt in connections.items():
            center_count[p1]+=cnt
            center_count[p2]+=cnt
            conn_detail[p1].append((p2,cnt)); conn_detail[p2].append((p1,cnt))
        items = sorted(center_count.items(), key=lambda x:-x[1])[:200]
        rows = [{'place':p,'count':c,'connections':"; ".join([f"{dst}({cnt})" for dst,cnt in conn_detail[p]])} for p,c in items]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    elif mode.startswith("5"):
        st.subheader(t("sub_clusters_dbscan"))
        try:
            from sklearn.cluster import DBSCAN
            import numpy as np
        except Exception:
            st.error(t("err_dbscan_libs"))
            return
        coords = []
        names = []
        for pname, info in places_index.items():
            lat = info.get('lat'); lon = info.get('lon')
            try:
                if lat in (None,"") or lon in (None,""):
                    continue
                latf = float(lat); lonf = float(lon)
            except Exception:
                continue
            if not (np.isfinite(latf) and np.isfinite(lonf)):
                continue
            coords.append((latf, lonf))
            names.append(pname)
        if not coords:
            st.info(t("empty_geocodificados"))
            return
        eps_km = st.number_input(t("label_eps_km"), value=10.0, min_value=0.5)
        min_samples = st.slider(t("mapa_dbscan_min_samples"), 1, 10, 2)
        coords_arr = np.array(coords, dtype=float)
        mask_finite = np.all(np.isfinite(coords_arr), axis=1)
        coords_arr = coords_arr[mask_finite]
        names = [n for i,n in enumerate(names) if mask_finite[i]]
        if coords_arr.shape[0] < 2:
            st.info(t("empty_puntos_cluster"))
            return
        coords_rad = np.radians(coords_arr)
        kms_per_radian = 6371.0088
        try:
            db = DBSCAN(eps=eps_km / kms_per_radian, min_samples=min_samples, metric='haversine').fit(coords_rad)
        except ValueError as e:
            st.error(t("err_dbscan", e=e))
            return
        labels = db.labels_
        dfc = pd.DataFrame({'place': names, 'lat': coords_arr[:,0], 'lon': coords_arr[:,1], 'cluster': labels})
        st.dataframe(dfc.sort_values(['cluster','place']).head(500), use_container_width=True)
        if folium:
            m = folium.Map(location=[coords_arr[0][0], coords_arr[0][1]], zoom_start=7)
            import random
            palette = {}
            for cl in sorted(set(labels)):
                if cl == -1:
                    palette[cl] = "#888888"
                else:
                    palette[cl] = "#{:06x}".format(random.randint(0,0xFFFFFF))
            for _, r in dfc.iterrows():
                folium.CircleMarker(location=[r['lat'], r['lon']], radius=4, color=palette[r['cluster']], popup=f"{r['place']} (cluster {r['cluster']})").add_to(m)
            embed_folium(m, width=1100, height=600)
        else:
            st.info(t("err_folium_clusters"))
    elif mode.startswith("7"):
        st.subheader(t("sub_padrinos_km"))
        max_km_mov = st.number_input(t("label_descartar_km"), value=0.0, min_value=0.0)
        results = []
        for w, events in by_witness.items():
            evlist = []
            for ev in events:
                info = places_index.get(ev.get('place_name'), {})
                lat = info.get('lat'); lon = info.get('lon')
                if lat in (None,'') or lon in (None,''): continue
                d = _parse_gramps_date(ev.get('date_iso'))
                evlist.append((d, ev.get('place_name'), float(lat), float(lon), ev.get('event_id')))
            if len(evlist) < 2: continue
            evlist = sorted(evlist, key=lambda x: x[0] if not pd.isna(x[0]) else pd.Timestamp(0))
            for i in range(len(evlist)-1):
                d1 = evlist[i]; d2 = evlist[i+1]
                km = haversine_km(d1[2], d1[3], d2[2], d2[3])
                if km is None: continue
                if max_km_mov and float(max_km_mov) > 0 and km > float(max_km_mov):
                    continue
                if MAX_DIST and float(MAX_DIST) > 0 and km > float(MAX_DIST):
                    continue
                results.append({'witness': w, 'from': d1[1], 'to': d2[1], 'date_from': d1[0], 'date_to': d2[0], 'km': km})
        rdf = pd.DataFrame(results)
        if rdf.empty:
            st.info(t("empty_movimientos"))
            return
        rdf['km'] = rdf['km'].astype(float)
        rdf['km_display'] = rdf['km'].apply(lambda x: f"{x:.1f}")
        st.dataframe(rdf.sort_values('km').head(1000), use_container_width=True)
        if folium:
            avg_lat = next((v['lat'] for v in places_index.values() if v['lat'] is not None), 40.0)
            avg_lon = next((v['lon'] for v in places_index.values() if v['lon'] is not None), -3.7)
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=7)
            for _, row in rdf.head(200).iterrows():
                i1 = places_index.get(row['from']); i2 = places_index.get(row['to'])
                if not i1 or not i2: continue
                folium.PolyLine([(float(i1['lat']), float(i1['lon'])), (float(i2['lat']), float(i2['lon']))], color='red', weight=2, tooltip=f"{row['witness']} {row['km']:.1f} km").add_to(m)
            embed_folium(m, width=1100, height=600)
    elif mode.startswith("9"):
        st.subheader(t("sub_pares_conectados"))
        if not connections:
            st.info(t("empty_pares"))
            return
        rows = []
        for (p1,p2),cnt in connections.items():
            i1 = places_index.get(p1); i2 = places_index.get(p2)
            if not i1 or not i2: continue
            lat1 = i1.get('lat'); lat2 = i2.get('lat')
            if lat1 in (None,'') or lat2 in (None,''): continue
            if MAX_DIST and float(MAX_DIST) > 0:
                dist = haversine_km(lat1, i1.get('lon'), lat2, i2.get('lon'))
                if dist and dist > float(MAX_DIST):
                    continue
            rows.append({'from': p1, 'to': p2, 'count': int(cnt)})
        df_pairs = pd.DataFrame(rows).sort_values('count', ascending=False)
        st.dataframe(df_pairs.head(500), use_container_width=True)
    else:
        st.info(t("mapa_modo_no_reconocido"))

def page_grafo(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    subj_id_map = dataset.subj_id_map
    st.header(t("hdr_grafo"))
    df_f = df.copy()
    max_graph_nodes = st.sidebar.number_input(t("grafo_max_nodos"), min_value=50, max_value=800, value=300)
    if nx is None or Network is None:
        st.warning(t("grafo_no_disponible"))
        return
    G_full = build_family_graph(df_f, use_person_id_if_available=True)
    if G_full is None:
        st.error(t("grafo_no_se_pudo"))
        return

    # Limitar por grado: quedarse con los nodos más conectados
    if G_full.number_of_nodes() > max_graph_nodes:
        top_nodes = sorted(G_full.nodes(), key=lambda n: G_full.degree(n), reverse=True)[:max_graph_nodes]
        G = G_full.subgraph(top_nodes).copy()
    else:
        G = G_full

    st.write(t("grafo_nodos_aristas", nodes=G_full.number_of_nodes(), edges=G_full.number_of_edges()))
    if G_full.number_of_nodes() > max_graph_nodes:
        st.caption(t("grafo_caption_truncado", shown=max_graph_nodes, total=G_full.number_of_nodes()))

    k_val = st.sidebar.slider(t("grafo_k_betweenness"), 0, 2000, 0)
    try:
        # Betweenness sobre el grafo completo para métricas correctas
        if k_val and k_val > 0:
            bw = nx.betweenness_centrality(G_full, k=int(k_val))
        else:
            bw = nx.betweenness_centrality(G_full)
    except Exception as e:
        st.error(t("grafo_error_betweenness", e=e))
        bw = {n: 0.0 for n in G_full.nodes()}
    bridge = sorted([(n, bw.get(n, 0.0)) for n in bw if str(n).startswith("W:")], key=lambda x: -x[1])[:200]
    bridge = [(str(n).split("::")[-1] if "::" in str(n) else str(n), round(v, 6)) for n, v in bridge]
    st.subheader(t("sub_top_puentes"))
    st.dataframe(pd.DataFrame(bridge, columns=[t("grafo_col_testigo"), 'betweenness']), use_container_width=True)

    net = Network(height="700px", width="100%", notebook=False)
    # Detener la física automáticamente tras estabilizar para evitar temblor continuo
    net.set_options("""
    {
      "physics": {
        "enabled": true,
        "stabilization": { "iterations": 200, "fit": true },
        "barnesHut": { "gravitationalConstant": -8000, "springLength": 95 }
      },
      "interaction": { "navigationButtons": true, "zoomView": true }
    }
    """)
    for n, a in G.nodes(data=True):
        label = family_label(n, gramps_id_map, subj_id_map, df) if str(n).startswith("F:") else (str(n).split("::")[-1] if "::" in str(n) else str(n))
        color = '#1f77b4' if str(n).startswith("F:") else '#ff7f0e'
        net.add_node(n, label=str(label), title=str(label), color=color, size=10 + G.degree(n))
    for u, v, a in G.edges(data=True):
        net.add_edge(u, v, value=a.get('weight', 1))

    # Deshabilitar física tras estabilización para fijar posiciones
    net.html = net.html if hasattr(net, 'html') else ""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.html')
    net.save_graph(tmp.name)
    html = open(tmp.name, 'r', encoding='utf-8').read()
    # Inyectar callback para deshabilitar física tras estabilización
    html = html.replace(
        "network = new vis.Network(container, data, options);",
        "network = new vis.Network(container, data, options);\n"
        "network.on('stabilized', function() { network.setOptions({ physics: { enabled: false } }); });"
    )
    st.components.v1.html(html, height=700, width=1100)

def _render_witness_profile(witness_name: str):
    """Panel de perfil detallado de un testigo: notas, clase social, rango de vida."""
    df = st.session_state.get('tst_df_global')
    if df is None:
        return
    col_wit = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
    wit_events = df[df[col_wit].astype(str) == witness_name].copy()
    if wit_events.empty:
        st.info(t("empty_testigo"))
        return

    wit_events['date_dt'] = _parse_date_series(wit_events['date_iso'])
    wit_events['_year_sort'] = _year_series(wit_events['date_iso'])
    wit_sorted = wit_events.sort_values('_year_sort')

    _dates_valid = [d for d in wit_events['date_dt'] if d is not None and not (isinstance(d, float) and pd.isna(d)) and d is not pd.NaT]
    first_date = min(_dates_valid) if _dates_valid else None
    last_date  = max(_dates_valid) if _dates_valid else None

    c1, c2, c3 = st.columns(3)
    c1.metric("Primera aparición", str(first_date.date()) if first_date is not None else "?")
    c2.metric("Última aparición",  str(last_date.date())  if last_date is not None else "?")
    c3.metric("Total eventos", len(wit_events))

    # Clase social
    raw_name = str(wit_sorted['witness_raw'].iloc[0]) if 'witness_raw' in wit_sorted.columns else witness_name
    notes_concat = " | ".join(wit_sorted['note'].dropna().astype(str).tolist())
    social = _extract_social_class_from_note(notes_concat) or _extract_social_class_from_name(raw_name) or "No detectada"
    st.write(f"{t('label_clase_social')} {social}")

    # Notas con categoría
    notes_ev = wit_sorted[wit_sorted['note'].notna() & (wit_sorted['note'].astype(str).str.strip() != "")].copy()
    if not notes_ev.empty:
        notes_ev['categoría'] = notes_ev['note'].apply(classify_note)
        cats_summary = notes_ev['categoría'].value_counts()
        st.write(t("super_cat_notas", cats=", ".join(f"{k} ({v})" for k, v in cats_summary.items())))
        st.dataframe(
            notes_ev[['date_iso', 'type', 'place_name', 'note', 'categoría']].rename(
                columns={'date_iso': 'fecha', 'type': 'evento', 'place_name': 'lugar'}
            ),
            use_container_width=True
        )
    else:
        st.info(t("notas_testigo_sin_notas"))

    # Estimación de rango de vida
    life_notes = []
    menor_ev = wit_sorted[wit_sorted['note'].astype(str).str.lower().str.contains(r'\bmenor\b', na=False)]
    if not menor_ev.empty:
        menor_date = menor_ev['date_dt'].iloc[0]
        if menor_date is not None and menor_date is not pd.NaT:
            life_notes.append(f"Aparece como 'Menor' en {menor_date.year} → nacido aprox. {menor_date.year - 17}–{menor_date.year}")
    if not life_notes and first_date is not None:
        life_notes.append(f"Primera aparición en {first_date.year} → nacido antes de {first_date.year - 18}")
    if life_notes:
        st.write(t("super_estimacion_vida"))
        for ln in life_notes:
            st.write(f"  - {ln}")

    # ── Búsqueda de documentos de archivo ──────────────────────────────────
    _ctx = st.session_state.get('tst_ctx')
    _render_archive_search_panel(witness_name, wit_sorted, _ctx)


def _render_country_selector(places: list[str], key_suffix: str = "") -> str:
    """
    Muestra un selector de país para la búsqueda de archivo.
    Detecta automáticamente el país por los topónimos y permite corrección manual.
    Devuelve el código de país seleccionado.
    """
    from modules.testigos.archive_sources import detect_country_from_places, ARCHIVE_SOURCES
    from modules.testigos.surname_systems import SURNAME_SYSTEMS

    detected = detect_country_from_places(places) or "es"

    lang = get_lang()
    country_options = []
    country_codes = []
    for code, sources in ARCHIVE_SOURCES.items():
        if sources:
            label = sources[0].country_name_es if lang == "es" else sources[0].country_name_en
            country_options.append(f"{label} ({code.upper()})")
            country_codes.append(code)

    default_idx = country_codes.index(detected) if detected in country_codes else 0
    key = f"arch_country_{key_suffix}"

    chosen_label = st.selectbox(
        t("archive_country_selector"),
        country_options,
        index=default_idx,
        key=key,
        help=t("archive_country_help"),
    )
    chosen_idx = country_options.index(chosen_label)
    chosen_code = country_codes[chosen_idx]

    # Mostrar aviso si alguna fuente del país no admite búsqueda automática
    from modules.testigos.archive_sources import get_sources_for_country
    sources = get_sources_for_country(chosen_code)
    manual_sources = [s for s in sources if not s.can_scrape and s.url_template and s.manual_only_reason_es]
    if manual_sources and not any(s.can_scrape for s in sources):
        st.info(
            t("archive_manual_only_warning") + "\n\n" +
            "\n".join(f"- **{s.name}**: {s.manual_only_reason_es if lang == 'es' else s.manual_only_reason_en}"
                      for s in manual_sources[:3])
        )

    return chosen_code


def _render_archive_search_panel(witness_name: str, wit_sorted, ctx=None):
    """Sección de búsqueda de documentos históricos en archivos para un testigo."""
    from modules.testigos.archive_search import (
        is_important_witness, get_important_witnesses,
        WitnessArchiveResult, search_archive_for_witness,
    )
    from modules.testigos.archive_search_store import (
        load_store, save_store, get_cached, put_result, clear_result,
    )

    # Determinar si el testigo es "importante"
    notes_rows = wit_sorted[
        wit_sorted['note'].notna() & (wit_sorted['note'].astype(str).str.strip() != "")
    ]['note'].astype(str).unique().tolist()

    if not notes_rows:
        return

    important_notes = [
        (note, classify_note(note))
        for note in notes_rows
        if is_important_witness(note, classify_note(note))
    ]
    if not important_notes:
        return

    st.markdown("---")
    with st.expander(t("archive_search_expander"), expanded=False):
        note_text, note_cat = important_notes[0]
        st.caption(f"**{t('archive_note_label')}** {note_text}  ·  **{t('archive_cat_label')}** {note_cat}")

        # Config LLM — desde ctx si está disponible, si no desde session_state
        if ctx is not None:
            base_url    = ctx.llm.base_url
            model       = ctx.llm.model
            llm_timeout = ctx.llm.timeout
            llm_provider = ctx.llm.provider
            llm_api_key  = ctx.llm.api_key
        else:
            base_url     = st.session_state.get("rag_llm_base_url", "http://127.0.0.1:9292/v1")
            model        = st.session_state.get("rag_llm_model", "qwen3-14b")
            llm_timeout  = st.session_state.get("rag_llm_timeout", 300)
            llm_provider = st.session_state.get("rag_llm_provider", "local")
            llm_api_key  = st.session_state.get("rag_llm_api_key") or None

        # Años y lugares
        years = []
        for _, row in wit_sorted.iterrows():
            d = row.get('date_iso', '')
            if d:
                try:
                    y = int(str(d)[:4])
                    if 1000 < y < 2100:
                        years.append(y)
                except Exception:
                    pass
        places = list({str(row.get('place_name', '')) for _, row in wit_sorted.iterrows() if row.get('place_name')})
        raw_name = str(wit_sorted['witness_raw'].iloc[0]) if 'witness_raw' in wit_sorted.columns else witness_name

        # Selector de país
        country_code = _render_country_selector(
            places=sorted(places),
            key_suffix=f"profile_{witness_name[:20]}",
        )

        candidate = WitnessArchiveResult(
            witness_name=raw_name,
            witness_norm=witness_name,
            note=note_text,
            note_category=note_cat,
            year_min=min(years) if years else None,
            year_max=max(years) if years else None,
            places=sorted(places),
            country_code=country_code,
        )

        # Cargar store y comprobar caché
        store = load_store()
        cached = get_cached(store, candidate)

        col_btn, col_clr = st.columns([3, 1])
        search_clicked = col_btn.button(
            t("archive_search_btn"),
            key=f"arch_search_{witness_name}",
            help=t("archive_search_help"),
        )
        if cached and col_clr.button(t("archive_clear_cache"), key=f"arch_clr_{witness_name}"):
            clear_result(store, candidate)
            save_store(store)
            cached = None
            st.rerun()

        if search_clicked and not cached:
            with st.spinner(t("archive_searching")):
                result = search_archive_for_witness(
                    candidate,
                    llm_cfg=ctx.llm if ctx is not None else None,
                    base_url=base_url,
                    model=model,
                    llm_timeout=llm_timeout,
                    provider=llm_provider,
                    api_key=llm_api_key,
                )
            put_result(store, result)
            save_store(store)
            cached = result

        if cached:
            _render_archive_result(cached)
        elif not search_clicked:
            st.info(t("archive_not_searched_yet"))


def _render_archive_result(result):
    """Muestra los documentos encontrados y el resumen del LLM."""
    if result.search_status == "error":
        st.error(f"{result.error_msg}")
        return

    if result.llm_summary:
        st.info(f"**{result.llm_summary}**")

    if not result.documents:
        st.warning(t("archive_no_docs"))
        return

    verified = [d for d in result.documents if d.relevance_score > 0]
    links_only = [d for d in result.documents if d.relevance_score == 0]

    if verified:
        st.markdown(f"**{t('archive_docs_found', n=len(verified))}**")
        for doc in verified:
            score_pct = int(doc.relevance_score * 100)
            color = "#2e7d32" if score_pct >= 70 else ("#f57c00" if score_pct >= 40 else "#c62828")
            badge = f'<span style="background:{color};color:white;padding:2px 8px;border-radius:10px;font-size:0.8em">{score_pct}%</span>'
            st.markdown(
                f"{badge} **[{doc.title}]({doc.url})**  \n"
                f"<small>_{doc.archive}_  ·  {doc.relevance_reason}</small>",
                unsafe_allow_html=True,
            )

    if links_only:
        st.markdown(f"**{t('archive_search_links')}**")
        for doc in links_only:
            st.markdown(f"- [{doc.title}]({doc.url})")


def page_superpadrinos(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    subj_id_map = dataset.subj_id_map
    df_notes = dataset.df_notes
    df_super = dataset.df_super
    st.header(t("hdr_superpadrinos"))
    col_wit = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
    if 'witness_canon' in df.columns:
        sup = df['witness_canon'].value_counts().reset_index(); sup.columns = ['witness', 'appearances']
        tmp = df[['witness_canon', 'date_iso']].copy()
        tmp['_year'] = _year_series(tmp['date_iso'])
        grouped = tmp.groupby('witness_canon')['_year'].agg(
            first_date='min', last_date='max'
        ).reset_index()
        grouped['span_years'] = (grouped['last_date'] - grouped['first_date']).fillna(0).astype(int)
        sup = pd.merge(sup, grouped.rename(columns={'witness_canon': 'witness'}), on='witness', how='left')
    else:
        sup = df['witness_raw'].value_counts().reset_index(); sup.columns = ['witness', 'appearances']
        sup['span_years'] = 0

    min_count = st.slider(t("super_min_apariciones"), 1, 500, 5)
    max_span = int(sup['span_years'].max()) if not sup.empty and sup['span_years'].max() > 0 else 200
    span_limit = st.slider(t("super_max_span_years"), 0, max(max_span, 200), max(max_span, 200), key="super_span_limit")
    sup_df = sup[(sup['appearances'] >= min_count) & (sup['span_years'] <= span_limit)].sort_values('appearances', ascending=False)

    # Filtro por categoría de nota (F4)
    cat_filter = st.multiselect(
        t("super_cat_filter"),
        _ALL_NOTE_CATEGORIES, default=[], key="super_cat_filter"
    )
    if cat_filter:
        notes_classified = df[df['note'].notna() & (df['note'] != "")].copy()
        notes_classified['_cat'] = notes_classified['note'].apply(classify_note)
        filtered_witnesses = set(
            notes_classified[notes_classified['_cat'].isin(cat_filter)][col_wit].astype(str)
        )
        sup_df = sup_df[sup_df['witness'].isin(filtered_witnesses)]

    st.dataframe(sup_df.head(500), use_container_width=True)

    # Panel de perfil de testigo (F2)
    st.markdown("---")
    st.subheader(t("sub_perfil_testigo"))
    wit_options = [""] + list(sup_df['witness'].head(300).astype(str))
    sel_wit = st.selectbox(t("super_sel_testigo"), wit_options, key="super_wit_profile")
    if sel_wit:
        _render_witness_profile(sel_wit)

        # Exportar informe HTML del testigo seleccionado
        st.markdown("---")
        st.subheader(t("sub_exportar_informe"))
        if st.button(t("super_export_html"), key="super_export_html"):
            col_w = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
            wit_events_df_sup = df[df[col_w].astype(str) == sel_wit].copy()
            wit_events_df_sup['_dt'] = _parse_date_series(wit_events_df_sup['date_iso'])
            wit_events_df_sup['_yr'] = _year_series(wit_events_df_sup['date_iso'])
            wit_events_df_sup = wit_events_df_sup.sort_values('_yr')

            _sup_valid = [d for d in wit_events_df_sup['_dt'] if d is not None and d is not pd.NaT]
            first_dt_sup = min(_sup_valid) if _sup_valid else None
            last_dt_sup  = max(_sup_valid) if _sup_valid else None
            active_years_sup = (
                int(last_dt_sup.year - first_dt_sup.year)
                if first_dt_sup is not None and last_dt_sup is not None else 0
            )
            stab_sup = stability_mobility_stats(
                {sel_wit: wit_events_df_sup.to_dict('records')}, places_index
            )
            avg_km_sup = float(stab_sup['avg_km'].iloc[0]) if not stab_sup.empty else 0.0

            stats_sup = {
                t("super_col_nombre"): sel_wit,
                t("super_col_primer_ev"): str(first_dt_sup.date()) if pd.notna(first_dt_sup) else '?',
                t("super_col_ultimo_ev"): str(last_dt_sup.date()) if pd.notna(last_dt_sup) else '?',
                t("super_col_anios"): active_years_sup,
                t("super_col_lugares"): int(wit_events_df_sup['place_name'].dropna().nunique()),
                t("super_col_total_ev"): len(wit_events_df_sup),
                t("super_col_dist_media"): f"{avg_km_sup:.1f}",
            }

            # Mapa
            _map_html_sup = None
            if folium is not None:
                coords_sup = []
                for _i_sup, (_, _row_sup) in enumerate(wit_events_df_sup.iterrows()):
                    _info_sup = places_index.get(str(_row_sup.get('place_name', '')), {})
                    _lat_sup = _info_sup.get('lat')
                    _lon_sup = _info_sup.get('lon')
                    if _lat_sup in (None, '') or _lon_sup in (None, ''):
                        continue
                    try:
                        coords_sup.append((float(_lat_sup), float(_lon_sup),
                                           str(_row_sup.get('place_name', '')),
                                           str(_row_sup.get('date_iso', ''))))
                    except (ValueError, TypeError):
                        pass
                if coords_sup:
                    m_sup = folium.Map(location=[coords_sup[0][0], coords_sup[0][1]], zoom_start=9,
                                       tiles='CartoDB positron')
                    folium.PolyLine(
                        [(c[0], c[1]) for c in coords_sup], color='darkred', weight=2
                    ).add_to(m_sup)
                    for _idx_sup, (_lat2, _lon2, _pname2, _date2) in enumerate(coords_sup):
                        folium.Marker(
                            [_lat2, _lon2],
                            popup=f"{_idx_sup + 1}. {_pname2} ({_date2})"
                        ).add_to(m_sup)
                    _map_html_sup = m_sup.get_root().render()

            display_sup = wit_events_df_sup[
                [c for c in ['date_iso', 'type', 'place_name', 'subj_name', 'note']
                 if c in wit_events_df_sup.columns]
            ].copy()
            display_sup.columns = [
                {'date_iso': t("notas_col_fecha"), 'type': t("explorar_tipo_evento"),
                 'place_name': t("notas_col_lugar"),
                 'subj_name': t("notas_col_sujeto"), 'note': t("notas_col_nota")}.get(c, c)
                for c in display_sup.columns
            ]

            html_report_sup = generate_witness_html_report(
                witness_name=sel_wit,
                events_df=display_sup,
                stats_dict=stats_sup,
                folium_map_html=_map_html_sup,
                plotly_chart_html=None,
            )
            st.download_button(
                t("dl_html"),
                data=html_report_sup.encode('utf-8'),
                file_name=f"superpadrino_{normalize(sel_wit)[:30]}.html",
                mime="text/html",
                key="super_dl_html"
            )
            pdf_bytes_sup, pdf_err_sup = try_export_pdf(html_report_sup)
            if pdf_bytes_sup:
                st.download_button(
                    t("dl_pdf"),
                    data=pdf_bytes_sup,
                    file_name=f"superpadrino_{normalize(sel_wit)[:30]}.pdf",
                    mime="application/pdf",
                    key="super_dl_pdf"
                )
            elif pdf_err_sup:
                st.warning(t("informe_error_pdf", e=pdf_err_sup))
            else:
                st.caption(t("informe_pdf_no"))

def page_notas(dataset: WitnessDataset):
    df = dataset.df
    st.header(t("hdr_notas"))
    notes_df = df[df['note'].notna() & (df['note'] != "")].copy()
    if notes_df.empty:
        st.info(t("notas_no_hay"))
        return

    # Construir columna 'categorías' como string con comas (soporta multi-categoría)
    _cur_overrides = st.session_state.get('tst_note_category_overrides', {})
    notes_df['categoría'] = notes_df['note'].apply(lambda n: classify_note(n, _cur_overrides))
    notes_df['categorías'] = notes_df['note'].apply(
        lambda n: ', '.join(get_note_categories(n, _cur_overrides))
    )

    # Filtro por categoría (comprueba todas las categorías de cada nota)
    sel_cats = st.multiselect(
        t("notas_filtrar_categoria"), _ALL_NOTE_CATEGORIES,
        default=_ALL_NOTE_CATEGORIES, key="notes_cat_filter"
    )

    # Búsqueda de texto libre
    q = st.text_input(t("notas_buscar"), key="notes_q_live")

    # Aplicar filtros — una nota pasa si CUALQUIERA de sus categorías está en sel_cats
    if sel_cats:
        def _note_matches_cats(note_text):
            return bool(set(get_note_categories(note_text, _cur_overrides)) & set(sel_cats))
        nf = notes_df[notes_df['note'].apply(_note_matches_cats)].copy()
    else:
        nf = notes_df.copy()
    if q and q.strip():
        qn = q.strip().lower()
        mask = (nf['note'].astype(str).str.lower().str.contains(qn, na=False) |
                nf['witness_raw'].astype(str).str.lower().str.contains(qn, na=False) |
                nf['subj_name'].astype(str).str.lower().str.contains(qn, na=False))
        nf = nf[mask]

    st.write(t("notas_filas", n=len(nf), total=len(notes_df)))

    # Gráfico de distribución de categorías (sobre los datos filtrados por categoría, sin filtro de texto)
    if sel_cats:
        cat_base = notes_df[notes_df['note'].apply(_note_matches_cats)]
    else:
        cat_base = notes_df
    # Cada nota puede contribuir a múltiples categorías en el gráfico
    from collections import Counter as _Counter
    _cat_counter = _Counter()
    for note_text in cat_base['note']:
        for _c in get_note_categories(note_text, _cur_overrides):
            if _c in _ALL_NOTE_CATEGORIES:
                _cat_counter[_c] += 1
    cat_counts = pd.Series({c: _cat_counter.get(c, 0) for c in _ALL_NOTE_CATEGORIES})
    if PLOTLY_OK:
        import plotly.express as _px
        import plotly.graph_objects as _go
        _colors = _px.colors.qualitative.Set2[:len(cat_counts)]
        fig_cats = _go.Figure(_go.Bar(
            x=cat_counts.index.tolist(),
            y=cat_counts.values.tolist(),
            marker_color=_colors,
        ))
        fig_cats.update_layout(
            title=t("notas_dist_titulo"),
            xaxis_title=t("notas_dist_x"),
            yaxis_title=t("notas_dist_y"),
            showlegend=False,
        )
        st.plotly_chart(fig_cats, use_container_width=True)
    else:
        st.bar_chart(cat_counts)

    # Tabla editable: 'categorías' como texto libre separado por comas
    st.caption(t("notas_caption_editar"))
    _cats_hint = ', '.join(_ALL_NOTE_CATEGORIES)
    edit_df = nf[['witness_raw', 'subj_name', 'place_name', 'date_iso', 'note', 'categorías']].head(1000).copy()
    edited = st.data_editor(
        edit_df,
        column_config={
            'witness_raw':  st.column_config.TextColumn(t("notas_col_testigo"),   disabled=True),
            'subj_name':    st.column_config.TextColumn(t("notas_col_sujeto"),   disabled=True),
            'place_name':   st.column_config.TextColumn(t("notas_col_lugar"),    disabled=True),
            'date_iso':     st.column_config.TextColumn(t("notas_col_fecha"),    disabled=True),
            'note':         st.column_config.TextColumn(t("notas_col_nota"),     disabled=True),
            'categorías':   st.column_config.TextColumn(
                                t("notas_col_categoria"),
                                help=f"Valores válidos (separar con comas si hay varias): {_cats_hint}",
                            ),
        },
        use_container_width=True,
        hide_index=True,
        key="notes_editor",
    )

    # Detectar cambios respecto a los valores actuales y guardarlos
    changed = edited[edited['categorías'] != edit_df['categorías']]
    if not changed.empty:
        # Validar que todas las categorías introducidas sean válidas
        _valid_set = set(_ALL_NOTE_CATEGORIES)
        _invalid_rows = []
        for _, row in changed.iterrows():
            raw_val = str(row['categorías']).strip()
            parts = [p.strip() for p in raw_val.split(',') if p.strip()]
            bad = [p for p in parts if p not in _valid_set]
            if bad:
                _invalid_rows.append(f"«{raw_val}» → desconocido: {bad}")
        if _invalid_rows:
            st.warning(
                f"Hay categorías no reconocidas. Valores válidos: {_cats_hint}\n\n" +
                "\n".join(_invalid_rows)
            )
        else:
            if st.button(t("notas_guardar", n=len(changed)), key="notes_override_save"):
                overrides = st.session_state.get('tst_note_category_overrides', {})
                for _, row in changed.iterrows():
                    raw_val = str(row['categorías']).strip()
                    parts = [p.strip() for p in raw_val.split(',') if p.strip()]
                    # Guardar como string simple si es una sola categoría, con comas si son varias
                    overrides[str(row['note'])] = raw_val if len(parts) > 1 else (parts[0] if parts else 'otro')
                st.session_state['tst_note_category_overrides'] = overrides
                save_note_category_overrides(overrides)
                st.success(t("notas_guardadas", n=len(changed)))
                st.rerun()

    # Resumen de correcciones manuales guardadas
    overrides = st.session_state.get('tst_note_category_overrides', {})
    if overrides:
        with st.expander(t("notas_correcciones", n=len(overrides)), expanded=False):
            ov_df = pd.DataFrame([
                {'nota': k, 'categoría(s) asignada(s)': v if isinstance(v, str) else ', '.join(v)}
                for k, v in overrides.items()
            ])
            st.dataframe(ov_df, use_container_width=True)
            if st.button(t("notas_borrar"), key="notes_clear_overrides"):
                st.session_state['tst_note_category_overrides'] = {}
                save_note_category_overrides({})
                st.success(t("notas_borradas"))
                st.rerun()

# ---------------- Fase 1 & 2: Funciones de análisis ----------------

@st.cache_data(ttl=3600)
def parse_families_and_marriages(gramps_path):
    """
    Parsea el XML de GRAMPS y extrae:
    - families_map: {family_handle -> {father_handle, mother_handle, children: [handle,...], id}}
    - marriages: lista de dicts con info de cada evento de tipo Marriage

    Para Fase 1: families_map permite agrupar hijos por familia y ordenarlos por fecha.
    Para Fase 2: marriages contiene testigos y cónyuges (personas con role=Primary en ese evento).

    Returns: (marriages_list, families_map)
    """
    p = Path(gramps_path)
    if etree is None or not p.exists():
        return [], {}
    try:
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        tree = etree.parse(str(p), parser)
        root = tree.getroot()
        ns = root.nsmap.get(None)
        GT = lambda x: f"{{{ns}}}{x}" if ns else x

        # 1. Parsear personas: handle -> {id, name, event_refs, childof}
        persons = {}
        for per in root.findall(".//" + GT("person")):
            handle = per.get('handle')
            pid = per.get('id')
            name_el = per.find(GT("name"))
            fullname = ""
            if name_el is not None:
                parts = []
                for tag in ("first", "middle", "surname", "prefix", "suffix"):
                    el = name_el.find(GT(tag))
                    if el is not None:
                        txt = "".join(el.itertext()).strip()
                        if txt:
                            parts.append(txt)
                fullname = " ".join(parts).strip()
            event_refs = {}
            for evref in per.findall(GT("eventref")):
                ev_h = evref.get('hlink')
                role = evref.get('role', 'Primary')
                if ev_h:
                    event_refs[ev_h] = role
            childof_handles = [co.get('hlink') for co in per.findall(GT("childof")) if co.get('hlink')]
            if handle:
                persons[handle] = {
                    'id': pid,
                    'name': fullname,
                    'event_refs': event_refs,
                    'childof': childof_handles,
                }

        # Índice inverso: event_handle -> lista de (person_handle, role)
        event_to_persons = defaultdict(list)
        for ph, pdata in persons.items():
            for ev_h, role in pdata['event_refs'].items():
                event_to_persons[ev_h].append((ph, role))

        # 2. Parsear familias
        families_map = {}
        for fam in root.findall(".//" + GT("family")):
            fh = fam.get('handle')
            fid = fam.get('id')
            father_h = fam.find(GT("father"))
            mother_h = fam.find(GT("mother"))
            children = [cr.get('hlink') for cr in fam.findall(GT("childref")) if cr.get('hlink')]
            if fh:
                families_map[fh] = {
                    'id': fid,
                    'father_handle': father_h.get('hlink') if father_h is not None else None,
                    'mother_handle': mother_h.get('hlink') if mother_h is not None else None,
                    'children': children,
                }

        # 3. Parsear lugares (para nombre en matrimonios)
        places_map = {}
        for place_el in root.findall(".//" + GT("placeobj")):
            ph2 = place_el.get('handle')
            ptitle = place_el.find(GT("ptitle"))
            pname_el = place_el.find(GT("pname"))
            name = ""
            if ptitle is not None and ptitle.text:
                name = ptitle.text.strip()
            elif pname_el is not None:
                name = pname_el.get('value', '').strip()
            if ph2:
                places_map[ph2] = name

        # 4. Parsear eventos de matrimonio
        marriages = []
        for ev in root.findall(".//" + GT("event")):
            type_el = ev.find(GT("type"))
            if type_el is None or (type_el.text or "").strip() != "Marriage":
                continue
            ev_handle = ev.get('handle')
            ev_id = ev.get('id')
            date_iso = None
            dv = ev.find(GT("dateval"))
            if dv is not None:
                date_iso = dv.get('val')
            else:
                ds = ev.find(GT("datestr"))
                if ds is not None:
                    date_iso = ds.get('val')
            place_el2 = ev.find(GT("place"))
            place_name = ""
            if place_el2 is not None:
                place_name = places_map.get(place_el2.get('hlink', ''), '')
            witnesses = []
            for attr in ev.findall(GT("attribute")):
                if attr.get('type') == "Witness" and attr.get('value'):
                    witnesses.append(attr.get('value').strip())
            # Cónyuges: personas con role=Primary para este evento
            spouses = [
                persons[ph]['name']
                for ph, role in event_to_persons.get(ev_handle, [])
                if role == 'Primary' and ph in persons
            ]
            spouse_handles = [
                ph
                for ph, role in event_to_persons.get(ev_handle, [])
                if role == 'Primary' and ph in persons
            ]
            marriages.append({
                'event_id': ev_id,
                'event_handle': ev_handle,
                'date_iso': date_iso,
                'place_name': place_name,
                'witnesses': witnesses,
                'spouses': spouses,
                'spouse_handles': spouse_handles,
            })

        return marriages, families_map

    except Exception as e:
        return [], {}


def detect_witness_marriages(df_baptisms, marriages, families_map, max_years_gap=80):
    """
    Fase 2: detecta casos donde un testigo de bautismo se casa posteriormente
    con alguien de la misma familia apellidada que el bautizado.

    Criterios estrictos para evitar falsos positivos:
    1. El testigo del bautismo aparece como CÓNYUGE en el matrimonio (no solo testigo).
    2. Uno de los cónyuges comparte apellido con el bautizado (mismo apellido final).
    3. El matrimonio ocurre DESPUÉS del bautismo y dentro de max_years_gap años.
    4. Cada par (testigo_norm, matrimonio_event_handle) se registra una sola vez,
       con el bautismo más relevante (mismo apellido preferido).
    """
    try:
        from rapidfuzz import fuzz as _fuzz
        _sim = lambda a, b: _fuzz.token_sort_ratio(a, b)
        _threshold = 85
    except ImportError:
        from difflib import SequenceMatcher
        _sim = lambda a, b: int(SequenceMatcher(None, a, b).ratio() * 100)
        _threshold = 83

    col = 'witness_canon' if 'witness_canon' in df_baptisms.columns else 'witness_raw'

    def _sn(name):
        """Extrae el último token como apellido, ignorando partículas."""
        particles = {'de', 'del', 'la', 'el', 'los', 'las', 'von', 'van', 'y', 'e'}
        parts = [p for p in str(name).strip().split() if normalize(p) not in particles]
        return normalize(parts[-1]) if parts else ""

    def _year(date_str):
        try:
            y = int(str(date_str)[:4])
            return y if 1400 <= y <= 2100 else None
        except Exception:
            return None

    # Filtrar filas con testigo y subj_name conocidos
    df_b = df_baptisms[
        df_baptisms[col].notna() &
        (df_baptisms[col].astype(str).str.strip() != "") &
        df_baptisms['subj_name'].notna() &
        (df_baptisms['subj_name'].astype(str).str.strip() != "")
    ].copy()

    # Construir índice: apellido_bautizado -> [(wit_norm, wit_raw, bapt_date, baptized_name)]
    # Esto evita el bucle O(n × m) completo
    sn_to_baptisms = defaultdict(list)
    for _, row in df_b.iterrows():
        baptized_name = str(row.get('subj_name') or "").strip()
        sn = _sn(baptized_name)
        if not sn:
            continue
        wit_raw = str(row.get(col) or "").strip()
        if not wit_raw:
            continue
        sn_to_baptisms[sn].append({
            'wit_norm': normalize(wit_raw),
            'wit_raw': wit_raw,
            'bapt_date': str(row.get('date_iso') or ""),
            'baptized_name': baptized_name,
            'bapt_year': _year(row.get('date_iso')),
        })

    # Para cada matrimonio, ver si algún cónyuge coincide en apellido con bautizados
    # y si el otro cónyuge fue testigo de uno de esos bautismos
    seen = set()  # (wit_norm, mar_event_handle) ya registrado
    results = []

    for mar in marriages:
        spouses = mar.get('spouses', [])
        if not spouses:
            continue
        mar_year = _year(mar.get('date_iso'))
        mar_date = str(mar.get('date_iso') or "")

        # Para cada cónyuge del matrimonio, ver si hay bautizados con mismo apellido
        for i, sp_family in enumerate(spouses):
            sp_family_sn = _sn(sp_family)
            if not sp_family_sn:
                continue

            baptisms_same_sn = sn_to_baptisms.get(sp_family_sn, [])
            if not baptisms_same_sn:
                continue

            # El otro cónyuge sería el potencial "padrino que se casó con la familia"
            other_spouses = [s for j, s in enumerate(spouses) if j != i]
            if not other_spouses:
                continue
            sp_witness_candidate = other_spouses[0]
            sp_wit_norm = normalize(sp_witness_candidate)

            for bapt in baptisms_same_sn:
                wit_norm = bapt['wit_norm']

                # ¿El testigo del bautismo es el cónyuge foráneo del matrimonio?
                if _sim(wit_norm, sp_wit_norm) < _threshold:
                    continue

                mar_key = (wit_norm, mar['event_handle'])
                if mar_key in seen:
                    continue

                # Restricción temporal
                bapt_year = bapt['bapt_year']
                if mar_year and bapt_year:
                    gap = mar_year - bapt_year
                    if gap < 0 or gap > max_years_gap:
                        continue
                    years_gap = gap
                else:
                    years_gap = None

                seen.add(mar_key)
                results.append({
                    'padrino': bapt['wit_raw'],
                    'bautismo_fecha': bapt['bapt_date'],
                    'bautizado': bapt['baptized_name'],
                    'matrimonio_fecha': mar_date,
                    'matrimonio_lugar': mar.get('place_name', ''),
                    'conyuge_familia': sp_family,
                    'años_entre_eventos': years_gap,
                    'nota': f"Padrino de {_sn(bapt['baptized_name']).title()} → casa con {sp_family}",
                })

    return results


def calculate_witness_prestige(witness_norm, by_witness_map, places_index_map):
    """
    Score de prestigio 0-100:
    - 40%: Total de apariciones (normalizado respecto al máximo global)
    - 30%: Diversidad geográfica (nº de lugares únicos, normalizado)
    - 30%: Span temporal activo (años, normalizado)
    """
    events = by_witness_map.get(witness_norm, [])
    if not events:
        return 0.0

    total_apps = len(events)
    places = set(ev.get('place_name', '') for ev in events if ev.get('place_name'))
    unique_places = len(places)

    years = []
    for ev in events:
        d = ev.get('date_iso') or ev.get('_date_parsed') or ''
        try:
            y = int(str(d)[:4])
            if 1400 <= y <= 2100:
                years.append(y)
        except Exception:
            pass
    span_years = (max(years) - min(years)) if len(years) >= 2 else 0

    # Maximos de referencia (ajusta si tus datos son muy distintos)
    max_apps = max(len(v) for v in by_witness_map.values()) if by_witness_map else 1
    max_places = 20
    max_span = 50

    score_apps = min(1.0, total_apps / max_apps) * 40
    score_places = min(1.0, unique_places / max_places) * 30
    score_span = min(1.0, span_years / max_span) * 30

    return round(score_apps + score_places + score_span, 1)


def birth_order_analysis(df_in, families_map, places_index_map, by_witness_map):
    """
    Fase 1: analiza si los primogénitos tienen padrinos más prestigiosos.

    Para cada familia (padre + madre + hijos), ordena los hijos por fecha
    de bautismo e infiere el orden de nacimiento. Luego calcula el prestige
    score del testigo de cada bautismo.

    Returns: DataFrame con columnas family_id, parent_names, child_name,
             birth_order, birth_date, witness_name, prestige_score
    """
    col = 'witness_canon' if 'witness_canon' in df_in.columns else 'witness_raw'

    # Índice de bautismos por subj_name normalizado
    bapt_by_name = defaultdict(list)
    for _, row in df_in.iterrows():
        sn = normalize(str(row.get('subj_name') or ""))
        if sn:
            bapt_by_name[sn].append(row)

    # Agrupación heurística por apellido del sujeto + lugar + ventana temporal
    return _birth_order_heuristic(df_in, col, by_witness_map, places_index_map)


def _birth_order_heuristic(df_in, col, by_witness_map, places_index_map):
    """
    Agrupa hijos por apellido del sujeto + lugar principal + ventana de 40 años.
    Filtra grupos con >= 2 hijos con fecha conocida.
    """
    import re as _re
    df_w = df_in.copy()
    df_w['_sn_subj'] = df_w['subj_name'].astype(str).apply(
        lambda n: normalize(n.strip().split()[-1]) if n.strip() else ""
    )
    df_w['_year'] = df_w['date_iso'].apply(
        lambda s: int(_re.match(r'^(\d{4})', str(s).strip()).group(1))
        if s and _re.match(r'^(\d{4})', str(s).strip()) else None
    )
    df_w = df_w[df_w['_sn_subj'] != ""]
    df_w = df_w[df_w['_year'].notna()]
    df_w = df_w[df_w[col].notna() & (df_w[col].astype(str).str.strip() != "")]

    results = []
    # Agrupar por (apellido_sujeto, lugar)
    for (sn, place), grp in df_w.groupby(['_sn_subj', 'place_name']):
        if len(grp) < 2:
            continue
        grp_sorted = grp.sort_values('_year')
        min_y = int(grp_sorted['_year'].min())
        max_y = int(grp_sorted['_year'].max())
        if max_y - min_y > 45:  # ventana máxima de una generación
            continue

        family_id = f"{sn}_{normalize(place)}"
        # Inferir nombres de padre/madre del apellido
        parent_names = f"Familia {sn.title()} ({place})"

        for order, (_, row) in enumerate(grp_sorted.iterrows(), start=1):
            wit_norm = normalize(str(row.get(col) or ""))
            wit_events = by_witness_map.get(wit_norm, [])
            wit_places = len(set(ev.get('place_name', '') for ev in wit_events if ev.get('place_name')))
            prestige = calculate_witness_prestige(wit_norm, by_witness_map, places_index_map)
            results.append({
                'family_id': family_id,
                'parent_names': parent_names,
                'child_name': str(row.get('subj_name') or ""),
                'birth_order': order,
                'birth_date': str(row.get('date_iso') or ""),
                'witness_name': str(row.get(col) or ""),
                'witness_apariciones': len(wit_events),
                'witness_lugares': wit_places,
                'prestige_score': prestige,
            })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


@st.cache_data(ttl=3600)
def extract_note_relations(df_in, _gramps_index):
    """Parsea las notas de testigos buscando relaciones familiares explícitas.

    Devuelve lista de dicts:
      {testigo, tipo_relacion, persona_mencionada, evento_id, fecha, lugar, en_gramps}
    """
    patterns = [
        (r'viud[ao]\s+de\s+([a-z\s]{2,40})',                 'cónyuge_fallecido'),
        (r'(?:muger|mujer|esposa|esposo)\s+de\s+([a-z\s]{2,40})', 'cónyuge'),
        (r'hija?\s+de\s+([a-z\s]{2,40})',                    'hijo/a'),
        (r'herman[ao]\s+de\s+([a-z\s]{2,40})',               'hermano/a'),
        (r'esposa\s+del?\s+padrino',                          'cónyuge_copadrino'),
        (r'esposo\s+de\s+la\s+madrina',                       'cónyuge_copadrino'),
    ]

    col_wit = 'witness_canon' if 'witness_canon' in df_in.columns else 'witness_raw'
    results = []

    for _, row in df_in.iterrows():
        note = str(row.get('note', '') or '')
        if not note or note in ('nan', 'None', ''):
            continue
        note_norm = normalize(note)
        witness = str(row.get(col_wit, '') or row.get('witness_raw', ''))
        event_id = str(row.get('event_id', ''))
        fecha = str(row.get('date_iso', ''))
        lugar = str(row.get('place_name', ''))

        # "Consortes": relación con co-testigo (se resuelve en post-proceso)
        if 'consortes' in note_norm:
            results.append({
                'testigo': witness, 'tipo_relacion': 'consortes',
                'persona_mencionada': '', 'evento_id': event_id,
                'fecha': fecha, 'lugar': lugar, 'en_gramps': False,
            })
            continue

        for pattern, rel_type in patterns:
            m = re.search(pattern, note_norm)
            if m:
                if m.lastindex and m.lastindex >= 1:
                    persona = ' '.join(m.group(1).strip().split()[:4])
                else:
                    persona = ''
                in_gramps = bool(_gramps_index.get(normalize(persona), []) if persona else False)
                results.append({
                    'testigo': witness, 'tipo_relacion': rel_type,
                    'persona_mencionada': persona, 'evento_id': event_id,
                    'fecha': fecha, 'lugar': lugar, 'en_gramps': in_gramps,
                })
                break

    # Post-proceso: resolver "consortes" → co-testigo del mismo evento
    if results:
        event_witnesses = df_in.groupby('event_id')[col_wit].apply(list).to_dict()
        for r in results:
            if r['tipo_relacion'] == 'consortes' and r['evento_id']:
                co_wits = [str(w) for w in event_witnesses.get(r['evento_id'], [])
                           if str(w) not in (r['testigo'], 'nan', 'None', '')]
                if co_wits:
                    r['persona_mencionada'] = co_wits[0]
                    r['en_gramps'] = bool(_gramps_index.get(normalize(co_wits[0]), []))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONALIDAD: Endogamia y patrones de padrinos cerrados
# ─────────────────────────────────────────────────────────────────────────────

def compute_endogamy_stats(_df_in, min_events: int = 2):
    """
    Calcula coeficiente de endogamia por familia (apellido de sujeto).

    Returns:
        pd.DataFrame con columnas:
            familia, total_eventos, padrinos_unicos, coef_endogamia,
            idx_diversidad, apellidos_padrinos_n, apellidos_padrinos
    """
    if _df_in is None or _df_in.empty:
        return pd.DataFrame()

    col_wit = 'witness_canon' if 'witness_canon' in _df_in.columns else 'witness_raw'

    df_clean = _df_in.dropna(subset=['subj_name']).copy()
    df_clean['_apellido_fam'] = df_clean['subj_name'].apply(extract_surname_improved)
    df_clean = df_clean[df_clean['_apellido_fam'] != '']

    rows = []
    for familia, group in df_clean.groupby('_apellido_fam'):
        familia = str(familia)
        total = len(group)
        if total < min_events:
            continue
        testigos = [str(w) for w in group[col_wit].dropna() if str(w) not in ('', 'nan', 'None')]
        if not testigos:
            continue
        unicos = len(set(testigos))
        coef = round(1.0 - unicos / total, 4)
        idx = round(unicos / total, 4)
        aps = {extract_surname_improved(w) for w in set(testigos)}
        aps.discard('')
        rows.append({
            'familia': familia,
            'total_eventos': total,
            'padrinos_unicos': unicos,
            'coef_endogamia': coef,
            'idx_diversidad': idx,
            'apellidos_padrinos_n': len(aps),
            'apellidos_padrinos': ', '.join(sorted(aps)),
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values('coef_endogamia', ascending=False).reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def compute_mutual_godparent_clusters(_df_in, min_count: int = 1):
    """
    Detecta pares de entidades con relación de compadrazgo recíproco.
    Enriquece el resultado de reciprocity_pairs con apellidos de familia.
    """
    try:
        rp = reciprocity_pairs(_df_in, min_count=min_count)
        if rp.empty:
            return rp
        # Añadir apellido de familia para entity_a y entity_b
        def _fam(name):
            return extract_surname_improved(str(name)) if name else ''
        rp['apellido_a'] = rp['entity_a'].apply(_fam)
        rp['apellido_b'] = rp['entity_b'].apply(_fam)
        return rp
    except Exception:
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONALIDAD: Rango social y familias
# ─────────────────────────────────────────────────────────────────────────────

def _extract_two_surnames(full_name: str) -> str:
    """
    Extrae los dos últimos apellidos de un nombre completo, ignorando
    títulos iniciales y partículas sueltas ('de', 'del', etc.).
    Devuelve p.ej. 'García López' en lugar de solo 'López'.
    """
    if not full_name or str(full_name).strip() in ('', 'nan', 'None'):
        return ""
    tokens = str(full_name).strip().split()
    _titles = {'don', 'doña', 'dona', 'fray', 'sor', 'dr', 'dr.', 'dra', 'dra.'}
    while tokens and tokens[0].lower().rstrip('.') in _titles:
        tokens = tokens[1:]
    if not tokens:
        return ""
    _particles = {'de', 'del', 'von', 'van', 'la', 'el', 'los', 'las', 'y', 'e', 'das', 'dos'}
    # Identificar tokens "reales" (no partículas) de derecha a izquierda
    real_indices = [i for i, t in enumerate(tokens) if t.lower().rstrip('.') not in _particles]
    if not real_indices:
        return tokens[-1] if tokens else ""
    # Tomar los dos últimos tokens reales con sus partículas precedentes
    if len(real_indices) >= 2:
        start = real_indices[-2]
    else:
        start = real_indices[-1]
    return " ".join(tokens[start:])


@st.cache_data(ttl=1800, show_spinner=False)
def high_rank_witness_families(_df_in, min_events: int = 1, rank_categories=("rango_social",)):
    """
    Para cada testigo cuyas notas tienen categoría rango_social (o la indicada),
    calcula en qué familias (dos apellidos + lugar) actúa y cuántas veces.

    Returns:
        families_df: familias con columnas [family_id, label, n_rank_witnesses, n_rank_events, witnesses_list]
        witnesses_df: testigos con columnas [witness, note, category, n_families, n_events, families_list]
    """
    if _df_in is None or _df_in.empty:
        return pd.DataFrame(), pd.DataFrame()

    col_wit = 'witness_canon' if 'witness_canon' in _df_in.columns else 'witness_norm'

    # Clasificar notas y filtrar testigos con rango
    rows_with_note = _df_in[_df_in['note'].notna() & (_df_in['note'].astype(str).str.strip() != '')].copy()
    if rows_with_note.empty:
        return pd.DataFrame(), pd.DataFrame()

    rows_with_note['_note_cat'] = rows_with_note['note'].apply(classify_note)
    rank_rows = rows_with_note[rows_with_note['_note_cat'].isin(rank_categories)].copy()

    if rank_rows.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Extraer dos apellidos del sujeto y crear family_id + label con lugar
    rank_rows['_subj_two_sn'] = rank_rows['subj_name'].astype(str).apply(_extract_two_surnames)
    rank_rows['_subj_sn'] = rank_rows['subj_name'].astype(str).apply(extract_surname_improved)
    rank_rows['_family_id'] = rank_rows.apply(
        lambda r: f"{normalize(r['_subj_sn']) or '__sin_nombre'}_{normalize(str(r.get('place_name') or ''))}", axis=1
    )
    rank_rows['_family_label'] = rank_rows.apply(
        lambda r: f"{r['_subj_two_sn'].title() if r['_subj_two_sn'] else '(sin nombre)'} ({r.get('place_name') or '?'})", axis=1
    )
    rank_rows['_witness'] = rank_rows[col_wit].astype(str).apply(normalize)
    rank_rows['_witness_raw'] = rank_rows[col_wit].astype(str)

    # --- DataFrame de familias ---
    fam_rows = []
    for fid, grp in rank_rows.groupby('_family_id'):
        label = grp['_family_label'].iloc[0]
        witnesses = grp['_witness_raw'].unique().tolist()
        n_events = len(grp)
        fam_rows.append({
            'family_id': fid,
            'label': label,
            'n_rank_witnesses': len(witnesses),
            'n_rank_events': n_events,
            'witnesses_list': ', '.join(sorted(witnesses)),
        })
    families_df = pd.DataFrame(fam_rows).sort_values('n_rank_witnesses', ascending=False).reset_index(drop=True)
    families_df = families_df[families_df['n_rank_events'] >= min_events]

    # --- DataFrame de testigos ---
    wit_rows = []
    for wname, grp in rank_rows.groupby('_witness'):
        labels = grp['_family_label'].unique().tolist()
        cat = grp['_note_cat'].mode().iloc[0] if not grp['_note_cat'].empty else ''
        # Nota más representativa (la más frecuente)
        notes_counts = grp['note'].value_counts()
        top_note = notes_counts.index[0] if not notes_counts.empty else ''
        wit_raw = grp['_witness_raw'].iloc[0]
        wit_rows.append({
            'witness': wit_raw,
            'note': top_note,
            'category': cat,
            'n_families': len(grp['_family_id'].unique()),
            'n_events': len(grp),
            'families_list': ', '.join(sorted(labels)),
        })
    witnesses_df = pd.DataFrame(wit_rows).sort_values('n_families', ascending=False).reset_index(drop=True)

    return families_df, witnesses_df


def build_rank_witness_graph(_df_in, min_events: int = 1, rank_categories=("rango_social", "profesión")):
    """
    Construye grafo bipartito NetworkX: testigos de rango ↔ familias (apellido+lugar).
    Nodos testigo: 'W:{norm}', nodos familia: 'F:{family_id}'.
    Aristas ponderadas por nº de eventos compartidos.
    """
    if 'nx' not in globals() or nx is None:
        return None
    if _df_in is None or _df_in.empty:
        return None

    col_wit = 'witness_canon' if 'witness_canon' in _df_in.columns else 'witness_norm'

    rows_with_note = _df_in[_df_in['note'].notna() & (_df_in['note'].astype(str).str.strip() != '')].copy()
    if rows_with_note.empty:
        return None

    rows_with_note['_note_cat'] = rows_with_note['note'].apply(classify_note)
    rank_rows = rows_with_note[rows_with_note['_note_cat'].isin(rank_categories)].copy()
    if rank_rows.empty:
        return None

    rank_rows['_subj_sn'] = rank_rows['subj_name'].astype(str).apply(extract_surname_improved)
    rank_rows['_subj_two_sn'] = rank_rows['subj_name'].astype(str).apply(_extract_two_surnames)
    rank_rows['_family_id'] = rank_rows.apply(
        lambda r: f"{normalize(r['_subj_sn']) or '__sin_nombre'}_{normalize(str(r.get('place_name') or ''))}", axis=1
    )
    rank_rows['_family_label'] = rank_rows.apply(
        lambda r: f"{r['_subj_two_sn'].title() if r['_subj_two_sn'] else '(sin nombre)'} ({r.get('place_name') or '?'})", axis=1
    )
    rank_rows['_witness_norm'] = rank_rows[col_wit].astype(str).apply(normalize)
    rank_rows['_witness_raw'] = rank_rows[col_wit].astype(str)

    G = nx.Graph()
    for _, row in rank_rows.iterrows():
        w_node = f"W:{row['_witness_norm']}"
        f_node = f"F:{row['_family_id']}"
        if not G.has_node(w_node):
            G.add_node(w_node, type='witness', label=row['_witness_raw'], category=row['_note_cat'])
        if not G.has_node(f_node):
            G.add_node(f_node, type='family', label=row['_family_label'])
        if G.has_edge(w_node, f_node):
            G[w_node][f_node]['weight'] += 1
        else:
            G.add_edge(w_node, f_node, weight=1)

    # Filtrar familias con menos de min_events apariciones de testigos de rango
    if min_events > 1:
        to_remove = [
            n for n in list(G.nodes())
            if G.nodes[n].get('type') == 'family'
            and sum(G[n][nb].get('weight', 1) for nb in G.neighbors(n)) < min_events
        ]
        G.remove_nodes_from(to_remove)
        # Limpiar testigos aislados tras el filtro
        G.remove_nodes_from([n for n in list(G.nodes()) if G.degree(n) == 0])

    return G if len(G.nodes) > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONALIDAD: Familias puente — brokers sociales
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def compute_bridge_families(_df_in, k_betweenness: int = 0):
    """
    Ranking de familias puente: familias con alta betweenness centrality en el
    grafo bipartito familias-testigos y alta diversidad de apellidos de padrinos.

    Returns:
        pd.DataFrame con columnas:
            node, label, betweenness, n_padrinos, n_apellidos_padrinos,
            score_apertura, bridge_index, total_eventos, apellidos_padrinos
    """
    if nx is None or _df_in is None or _df_in.empty:
        return pd.DataFrame()

    G = build_family_graph(_df_in, use_person_id_if_available=True)
    if G is None or G.number_of_nodes() == 0:
        return pd.DataFrame()

    try:
        if k_betweenness > 0:
            bw = nx.betweenness_centrality(G, k=k_betweenness)
        else:
            bw = nx.betweenness_centrality(G)
    except Exception:
        bw = {}

    family_nodes = [n for n in G.nodes() if str(n).startswith('F:')]
    if not family_nodes:
        return pd.DataFrame()

    max_bw = max((bw.get(fn, 0.0) for fn in family_nodes), default=1.0) or 1.0

    rows = []
    for fn in family_nodes:
        witness_neighbors = [v for v in G.neighbors(fn) if str(v).startswith('W:')]
        # Extraer nombre raw del nodo W:{norm}::{raw}
        raws = []
        for wn in witness_neighbors:
            parts = str(wn).split('::', 1)
            raws.append(parts[1] if len(parts) == 2 else parts[0])

        aps = {extract_surname_improved(r) for r in raws}
        aps.discard('')
        n_aps = len(aps)
        n_wit = len(witness_neighbors)
        score_apertura = round(n_aps / max(1, n_wit), 4)
        bw_val = bw.get(fn, 0.0)
        bw_norm = round(bw_val / max_bw, 4)
        bridge_index = round(0.6 * bw_norm + 0.4 * score_apertura, 4)
        total_ev = sum(G[fn][w].get('weight', 1) for w in witness_neighbors)
        rows.append({
            'node': fn,
            'label': family_label(fn, gramps_id_map, subj_id_map, df),
            'betweenness': round(bw_val, 6),
            'betweenness_norm': bw_norm,
            'n_padrinos': n_wit,
            'n_apellidos_padrinos': n_aps,
            'score_apertura': score_apertura,
            'bridge_index': bridge_index,
            'total_eventos': total_ev,
            'apellidos_padrinos': ', '.join(sorted(aps)),
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values('bridge_index', ascending=False).reset_index(drop=True)


def get_family_godparent_connections(family_node: str, G):
    """
    Para una familia en el grafo, devuelve tabla de padrinos y otras familias
    con las que comparte cada padrino.

    Returns:
        pd.DataFrame con columnas: padrino, otras_familias, n_otras_familias, peso
    """
    if G is None or family_node not in G:
        return pd.DataFrame()

    rows = []
    for wn in G.neighbors(family_node):
        if not str(wn).startswith('W:'):
            continue
        parts = str(wn).split('::', 1)
        raw_name = parts[1] if len(parts) == 2 else str(wn)
        peso = G[family_node][wn].get('weight', 1)
        otras = [
            family_label(v, gramps_id_map, subj_id_map, df)
            for v in G.neighbors(wn)
            if str(v).startswith('F:') and v != family_node
        ]
        rows.append({
            'padrino': raw_name,
            'otras_familias': ', '.join(sorted(otras)) if otras else '—',
            'n_otras_familias': len(otras),
            'peso': peso,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values('n_otras_familias', ascending=False).reset_index(drop=True)


def page_analisis(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    subj_id_map = dataset.subj_id_map
    df_notes = dataset.df_notes
    df_super = dataset.df_super
    CONF = _store.get_all()
    st.header(t("hdr_analisis"))
    st.subheader(t("sub_reciprocidad"))
    minc = st.number_input(t("analisis_min_reciproco"), value=1, min_value=1)
    df_rec = reciprocity_pairs(df, min_count=minc)
    if df_rec.empty:
        st.info(t("analisis_no_reciprocidad"))
    else:
        st.dataframe(df_rec, use_container_width=True)
    st.subheader(t("sub_generaciones"))
    maxgap = st.number_input(t("analisis_max_generaciones"), value=30, min_value=10, max_value=80)
    gen_df = generations_compadrazgo(df, max_year_gap=int(maxgap))
    if gen_df.empty:
        st.info(t("analisis_no_generaciones"))
    else:
        st.dataframe(gen_df, use_container_width=True)
    st.subheader(t("sub_estabilidad"))
    stab = stability_mobility_stats(by_witness, places_index)
    if stab.empty:
        st.info(t("empty_datos"))
    else:
        stab['class'] = stab.apply(lambda r: t("analisis_clase_estable") if r['avg_km']<10 and r['unique_places']<=2 else (t("analisis_clase_movil") if r['avg_km']>50 or r['unique_places']>5 else t("analisis_clase_moderado")), axis=1)
        max_span = int(stab['span_years'].max()) if not stab.empty else 300
        max_km = float(stab['avg_km'].max()) if not stab.empty else 1000.0
        fc1, fc2 = st.columns(2)
        with fc1:
            filter_span = st.slider(t("analisis_filtrar_span"), 0, max_span, max_span, key="stab_span")
        with fc2:
            filter_km = st.slider(t("analisis_filtrar_km"), 0, int(max_km), int(max_km), key="stab_km")
        stab_f = stab[(stab['span_years'] <= filter_span) & (stab['avg_km'] <= filter_km)]
        st.write(t("analisis_mostrando_testigos", n=len(stab_f), total=len(stab)))
        st.dataframe(stab_f.head(200), use_container_width=True)
        st.write(stab_f['class'].value_counts())
    st.subheader(t("sub_apellidos"))
    st.dataframe(surnames_stats(df).head(200), use_container_width=True)
    st.subheader(t("sub_roles"))
    roles_df = role_analysis(df)
    if roles_df is None or roles_df.empty:
        st.info(t("analisis_no_roles"))
    else:
        st.dataframe(roles_df.head(200), use_container_width=True)
    st.subheader(t("sub_panel_estadistico"))
    total_events = len(df)
    unique_wits = int(df['witness_canon'].nunique()) if 'witness_canon' in df.columns else int(df['witness_norm'].nunique())
    st.write(t("label_total_eventos"), total_events)
    st.write(t("label_testigos_unicos"), unique_wits)
    try:
        mobility = stability_mobility_stats(by_witness, places_index)
        if mobility is None or mobility.empty:
            st.info(t("analisis_no_movilidad"))
        else:
            st.markdown(t("analisis_top10"))
            mob_f = mobility[(mobility['span_years'] <= filter_span) & (mobility['avg_km'] <= filter_km)]
            st.dataframe(mob_f.head(10), use_container_width=True)
    except Exception as e:
        st.error(t("analisis_no_movilidad_calc", e=e))
    st.markdown(t("analisis_top_apellidos"))
    try:
        surn = surnames_stats(df)
        if surn is None or surn.empty:
            st.info(t("analisis_no_apellidos"))
        else:
            st.dataframe(surn.head(20), use_container_width=True)
    except Exception as e:
        st.error(t("analisis_error_apellidos", e=e))

    # ── Fase 3: Timeline de apellidos ──────────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_timeline_apellidos"))
    min_apps_sn = st.slider(t("analisis_min_apps_apellido"), 1, 20, 3, key="sn_min_apps")
    try:
        sn_tl = surname_timeline_analysis(df, min_appearances=min_apps_sn)
        if sn_tl.empty:
            st.info(t("analisis_no_timeline"))
        else:
            st.write(t("analisis_apellidos_count", n=len(sn_tl), min=min_apps_sn))
            st.dataframe(sn_tl, use_container_width=True)

            if PLOTLY_OK:
                sn_busqueda = st.text_input(t("analisis_buscar_apellido"), key="sn_busqueda").strip()
                sn_tl_plot = sn_tl[sn_tl['apellido'].str.contains(sn_busqueda, case=False, na=False)] if sn_busqueda else sn_tl
                fig_tl = px.scatter(
                    sn_tl_plot,
                    x='primera_aparicion',
                    y='total_apariciones',
                    text='apellido',
                    size='total_apariciones',
                    color='años_activo',
                    hover_data=['primera_persona', 'primer_lugar', 'familias_apadrinadas', 'lugares_distintos'],
                    title=t("analisis_primera_aparicion_titulo"),
                    labels={
                        'primera_aparicion': t("analisis_primera_aparicion_x"),
                        'total_apariciones': t("analisis_primera_aparicion_y"),
                        'años_activo': t("analisis_primera_aparicion_color"),
                    },
                    color_continuous_scale='Viridis',
                )
                fig_tl.update_traces(textposition='top center', textfont_size=9)
                fig_tl.update_layout(height=500)
                st.plotly_chart(fig_tl, use_container_width=True)
            else:
                st.warning(t("err_plotly_instalar"))

            st.markdown(t("sub_diversidad_apellidos"))
            window_sn = st.slider(t("analisis_ventana_temporal"), 5, 30, 10, key="sn_window")
            try:
                div_df = calculate_surname_diversity_over_time(df, window_years=window_sn)
                if div_df.empty:
                    st.info(t("analisis_no_datos_diversidad"))
                else:
                    if PLOTLY_OK:
                        fig_div = px.line(
                            div_df,
                            x='periodo',
                            y='diversidad_shannon',
                            markers=True,
                            hover_data=['apellidos_unicos', 'total_apariciones'],
                            title=t("analisis_shannon_titulo", w=window_sn),
                            labels={'periodo': t("analisis_shannon_x"), 'diversidad_shannon': t("analisis_shannon_y")},
                        )
                        fig_div.update_layout(height=350)
                        st.plotly_chart(fig_div, use_container_width=True)
                    st.dataframe(div_df, use_container_width=True)
            except Exception as e:
                st.error(t("analisis_error_diversidad", e=e))
    except Exception as e:
        st.error(t("analisis_error_timeline_ap", e=e))

    # ── Fase 2: Padrinos que se casan con la familia ──────────────────────────
    st.markdown("---")
    st.subheader(t("sub_padrinos_casan"))
    st.caption(
        "Detecta casos donde un padrino de bautismo aparece posteriormente como cónyuge "
        "en un matrimonio con alguien del mismo apellido que el bautizado. "
        "Criterio: el padrino se identifica como cónyuge en el XML, su apellido no coincide con el de la familia "
        "apadrinada, y el matrimonio ocurre dentro del rango temporal configurado."
    )
    try:
        _gramps_path = get_active_gramps_path()
        if _gramps_path and etree is not None:
            marriages, families_map = parse_families_and_marriages(_gramps_path)
            if not marriages and not families_map:
                st.info(t("data_no_gramps"))
            else:
                max_gap_f2 = st.slider(
                    t("analisis_rango_bautismo_matrimonio"),
                    min_value=5, max_value=80, value=50, step=5, key="f2_max_gap"
                )
                wm_links = detect_witness_marriages(df, marriages, families_map, max_years_gap=max_gap_f2)
                if not wm_links:
                    st.info(t("data_no_gramps"))
                else:
                    wm_df = pd.DataFrame(wm_links)
                    st.write(t("analisis_casos_padrino_casan", n=len(wm_df), gap=max_gap_f2))
                    st.dataframe(
                        wm_df[[
                            'padrino', 'bautismo_fecha', 'bautizado',
                            'matrimonio_fecha', 'matrimonio_lugar',
                            'conyuge_familia', 'años_entre_eventos'
                        ]].sort_values('años_entre_eventos'),
                        use_container_width=True
                    )
        else:
            st.info(t("data_req_gramps_matrimonio"))
    except Exception as e:
        st.error(t("analisis_error_padrinos_mat", e=e))
        import traceback; st.error(traceback.format_exc())

    # ── Fase 1: Orden de nacimiento en padrinazgo ─────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_orden_nacimiento"))
    st.caption(
        "Analiza si los primogénitos reciben padrinos más 'importantes' que los hijos posteriores. "
        "Las familias se detectan heurísticamente: bautizados con el mismo apellido, en el mismo lugar "
        "y dentro de una ventana de 45 años. El **score de prestigio** (0–100) combina: "
        "apariciones totales del padrino (40%), lugares distintos donde actúa (30%), "
        "y años de actividad documentada (30%). Un padrino con score alto es alguien con "
        "mucha presencia en los registros, en múltiples lugares y a lo largo del tiempo."
    )
    try:
        _gramps_path2 = get_active_gramps_path()
        if _gramps_path2 and etree is not None:
            _, families_map_f1 = parse_families_and_marriages(_gramps_path2)
            if not families_map_f1:
                st.info(t("data_no_familias"))
            else:
                bo_df = birth_order_analysis(df, families_map_f1, places_index, by_witness)
                if bo_df.empty:
                    st.info(t("data_no_hijos"))
                else:
                    n_families = bo_df['family_id'].nunique()
                    st.write(t("analisis_n_familias", n=n_families))

                    # Tabla de prestigio promedio por orden — solo primeros 8 órdenes
                    avg_df = bo_df[bo_df['birth_order'] <= 8].groupby('birth_order').agg(
                        n_casos=('prestige_score', 'count'),
                        prestige_promedio=('prestige_score', 'mean'),
                        apariciones_padrino=('witness_apariciones', 'mean'),
                        lugares_padrino=('witness_lugares', 'mean'),
                    ).reset_index()
                    avg_df.columns = [
                        t("analisis_col_orden"), 'Nº casos',
                        t("analisis_col_score"), t("analisis_col_apariciones_prom"),
                        t("analisis_col_lugares_prom")
                    ]
                    avg_df[t("analisis_col_orden")] = avg_df[t("analisis_col_orden")].astype(int)
                    avg_df[t("analisis_col_score")] = avg_df[t("analisis_col_score")].round(1)
                    avg_df[t("analisis_col_apariciones_prom")] = avg_df[t("analisis_col_apariciones_prom")].round(1)
                    avg_df[t("analisis_col_lugares_prom")] = avg_df[t("analisis_col_lugares_prom")].round(1)
                    st.write(t("analisis_comparativa_orden"))
                    st.dataframe(avg_df, use_container_width=True)

                    # Interpretación textual
                    if len(avg_df) >= 2:
                        score_1 = avg_df[avg_df[t("analisis_col_orden")] == 1][t("analisis_col_score")].values
                        score_2 = avg_df[avg_df[t("analisis_col_orden")] == 2][t("analisis_col_score")].values
                        if len(score_1) and len(score_2):
                            diff = round(float(score_1[0]) - float(score_2[0]), 1)
                            if diff > 2:
                                st.success(t("analisis_primogenito_mas", diff=diff))
                            elif diff < -2:
                                st.info(t("analisis_segundo_mas", diff=abs(diff)))
                            else:
                                st.info(t("analisis_sin_diferencia"))

                    # Detalle por familia filtrable
                    st.write(t("analisis_detalle_familia"))
                    bo_display = bo_df[[
                        'parent_names', 'child_name', 'birth_order',
                        'birth_date', 'witness_name',
                        'witness_apariciones', 'witness_lugares', 'prestige_score'
                    ]].copy()
                    bo_display.columns = [
                        t("analisis_familias_analizadas"), 'Hijo/a', 'Orden',
                        t("analisis_col_fecha_bautismo"), 'Padrino',
                        t("analisis_col_apariciones_padrino"), t("analisis_col_lugares_padrino"),
                        t("analisis_col_score_prestigio")
                    ]
                    st.dataframe(
                        bo_display.sort_values([t("analisis_familias_analizadas"), 'Orden']),
                        use_container_width=True
                    )
        else:
            st.info(t("data_req_gramps_grupos"))
    except Exception as e:
        st.error(t("analisis_error_orden", e=e))
        import traceback; st.error(traceback.format_exc())

    # ── Relaciones inferidas desde notas ──────────────────────────────────────
    st.markdown("---")
    with st.expander(t("sub_relaciones_notas"), expanded=False):
        st.caption(t("analisis_rel_caption"))
        try:
            rels = extract_note_relations(df, gramps_index)
            if not rels:
                st.info(t("analisis_no_relaciones_notas"))
            else:
                rel_df = pd.DataFrame(rels)
                all_rel_types = sorted(rel_df['tipo_relacion'].unique().tolist())
                col_r1, col_r2 = st.columns([3, 1])
                with col_r1:
                    sel_rel = st.multiselect(
                        t("analisis_filtrar_rel"), all_rel_types,
                        default=all_rel_types, key="analisis_rel_filter"
                    )
                with col_r2:
                    show_gramps_only = st.checkbox(t("analisis_solo_gramps"), key="analisis_rel_gramps")
                rf = rel_df[rel_df['tipo_relacion'].isin(sel_rel)] if sel_rel else rel_df.copy()
                if show_gramps_only:
                    rf = rf[rf['en_gramps'] == True]
                n_gramps = int(rel_df['en_gramps'].sum())
                st.write(t("analisis_n_relaciones", n=len(rf), gramps=n_gramps))
                st.dataframe(
                    rf[['testigo', 'tipo_relacion', 'persona_mencionada', 'fecha', 'lugar', 'en_gramps']].head(500),
                    use_container_width=True
                )
                st.write(t("analisis_por_tipo"))
                st.write(rel_df['tipo_relacion'].value_counts())
        except Exception as e:
            st.error(t("analisis_error_relaciones", e=e))

    # ── Endogamia y patrones de padrinos cerrados ──────────────────────────
    st.markdown("---")
    st.subheader(t("sub_endogamia"))
    st.caption(t("analisis_endogamia_caption"))
    try:
        min_ev_endo = st.slider(t("analisis_min_eventos_familia"), 1, 30, 3, key="endo_min_ev")
        endo_df = compute_endogamy_stats(df, min_events=min_ev_endo)
        if endo_df.empty:
            st.info(t("analisis_no_endogamia"))
        else:
            total_fam = len(endo_df)
            pct_endo = round(100 * (endo_df['coef_endogamia'] > 0.5).sum() / total_fam, 1)
            pct_open = round(100 * (endo_df['idx_diversidad'] > 0.8).sum() / total_fam, 1)
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(t("analisis_familias_analizadas"), total_fam)
            mc2.metric(t("analisis_pct_endogamicas"), f"{pct_endo}%")
            mc3.metric(t("analisis_pct_abiertas"), f"{pct_open}%")

            show_cols = ['familia', 'total_eventos', 'padrinos_unicos', 'coef_endogamia', 'idx_diversidad', 'apellidos_padrinos_n', 'apellidos_padrinos']
            st.dataframe(
                endo_df[show_cols].rename(columns={
                    'familia': 'Familia',
                    'total_eventos': 'Eventos',
                    'padrinos_unicos': 'Padrinos únicos',
                    'coef_endogamia': 'Coef. endogamia',
                    'idx_diversidad': 'Índice diversidad',
                    'apellidos_padrinos_n': 'N° apellidos padrinos',
                    'apellidos_padrinos': 'Apellidos de padrinos',
                }),
                use_container_width=True,
                height=380,
            )

            if PLOTLY_OK:
                import plotly.graph_objects as _go_endo
                _max_ev = max(int(endo_df['total_eventos'].max()), 1)
                _sizeref = max(_max_ev / 800, 0.3)
                _sizes = [max(int(v), 1) for v in endo_df['total_eventos'].tolist()]
                fig_endo = _go_endo.Figure(data=_go_endo.Scatter(
                    x=endo_df['idx_diversidad'].tolist(),
                    y=endo_df['total_eventos'].tolist(),
                    mode='markers',
                    marker=dict(
                        size=_sizes,
                        sizemode='area',
                        sizeref=_sizeref,
                        sizemin=6,
                        color=endo_df['coef_endogamia'].tolist(),
                        colorscale='RdYlGn_r',
                        colorbar=dict(title=t("analisis_endo_scatter_color")),
                        opacity=0.85,
                        line=dict(width=0.5, color='rgba(255,255,255,0.3)'),
                    ),
                    text=endo_df['familia'].tolist(),
                    customdata=list(zip(
                        endo_df['padrinos_unicos'].tolist(),
                        endo_df['apellidos_padrinos_n'].tolist(),
                        endo_df['coef_endogamia'].tolist(),
                    )),
                    hovertemplate=(
                        '<b>%{text}</b><br>'
                        + t("analisis_endo_scatter_x") + ': %{x:.3f}<br>'
                        + t("analisis_endo_scatter_y") + ': %{y}<br>'
                        + t("analisis_endo_scatter_color") + ': %{customdata[2]:.3f}<br>'
                        + 'Padrinos únicos: %{customdata[0]}<br>'
                        + 'N° apellidos: %{customdata[1]}<extra></extra>'
                    ),
                ))
                fig_endo.update_layout(
                    title=t("analisis_endo_scatter_titulo"),
                    xaxis=dict(title=t("analisis_endo_scatter_x"), range=[-0.05, 1.1]),
                    yaxis=dict(title=t("analisis_endo_scatter_y"), rangemode='tozero'),
                    height=500,
                )
                st.plotly_chart(fig_endo, use_container_width=True)

        st.markdown(t("analisis_reciprocidad"))
        st.caption(t("analisis_pares_caption"))
        min_rec_endo = st.number_input(t("analisis_min_apariciones"), value=1, min_value=1, key="endo_min_rec")
        rec_df = compute_mutual_godparent_clusters(df, min_count=int(min_rec_endo))
        if rec_df.empty:
            st.info(t("analisis_no_reciprocidades"))
        else:
            st.dataframe(rec_df.head(200), use_container_width=True)
    except Exception as e:
        st.error(t("analisis_error_endogamia", e=e))

    # ── Familias puente — brokers sociales ─────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_familias_puente"))
    st.caption(t("analisis_puentes_caption"))
    try:
        if nx is None:
            st.warning(t("err_networkx"))
        else:
            k_bw = st.slider(
                t("analisis_k_betweenness"),
                0, 500, 100, step=50, key="bridge_k_bw",
                help="Un valor > 0 usa aproximación por muestreo (más rápido).",
            )
            with st.spinner(t("analisis_calc_puentes")):
                bridge_df = compute_bridge_families(df, k_betweenness=k_bw)

            if bridge_df.empty:
                st.info(t("analisis_no_puentes"))
            else:
                show_cols_br = ['label', 'total_eventos', 'n_padrinos', 'n_apellidos_padrinos',
                                'score_apertura', 'bridge_index', 'apellidos_padrinos']
                st.dataframe(
                    bridge_df[show_cols_br].head(20).rename(columns={
                        'label': 'Familia',
                        'total_eventos': 'Eventos',
                        'n_padrinos': 'Padrinos distintos',
                        'n_apellidos_padrinos': 'Apellidos padrinos',
                        'score_apertura': 'Score apertura',
                        'bridge_index': 'Índice puente',
                        'apellidos_padrinos': 'Apellidos de padrinos',
                    }),
                    use_container_width=True,
                    height=380,
                )

                fam_labels = bridge_df['label'].head(20).tolist()
                sel_bridge = st.selectbox(t("analisis_sel_familia"), [""] + fam_labels, key="bridge_sel_fam")
                if sel_bridge:
                    node_row = bridge_df[bridge_df['label'] == sel_bridge]
                    if not node_row.empty:
                        fn = node_row.iloc[0]['node']
                        G_bridge = build_family_graph(df, use_person_id_if_available=True)
                        conn_df = get_family_godparent_connections(fn, G_bridge)
                        if conn_df.empty:
                            st.info(t("analisis_no_conexiones"))
                        else:
                            st.markdown(t("analisis_padrinos_familia", fam=sel_bridge))
                            st.dataframe(
                                conn_df.rename(columns={
                                    'padrino': 'Padrino',
                                    'otras_familias': 'Otras familias conectadas',
                                    'n_otras_familias': 'N° familias',
                                    'peso': 'Apariciones',
                                }),
                                use_container_width=True,
                            )
    except Exception as e:
        st.error(t("analisis_error_puentes", e=e))

    # ── Rango social y familias ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader(t("analisis_rango_titulo"))
    st.caption(t("analisis_rango_caption"))
    try:
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            rango_min_events = st.slider(t("analisis_rango_min_eventos"), 1, 20, 1, key="rango_min_ev")
        with col_r2:
            rango_cats = st.multiselect(
                t("analisis_rango_cats"),
                ["rango_social", "profesión"],
                default=["rango_social"],
                key="rango_cats_filter",
            )

        fam_df, wit_df = high_rank_witness_families(df, min_events=rango_min_events, rank_categories=tuple(rango_cats) if rango_cats else ("rango_social",))

        if fam_df.empty:
            st.info(t("analisis_rango_sin_datos"))
        else:
            tab_fam, tab_wit, tab_grafo = st.tabs([
                t("analisis_rango_tab_familias"),
                t("analisis_rango_tab_testigos"),
                t("analisis_rango_tab_grafo"),
            ])

            with tab_fam:
                st.caption(t("analisis_rango_fam_caption"))
                st.dataframe(
                    fam_df.rename(columns={
                        'label':            'Familia',
                        'n_rank_witnesses': 'Testigos de rango distintos',
                        'n_rank_events':    'Eventos con testigo de rango',
                        'witnesses_list':   'Testigos',
                    }).drop(columns=['family_id'], errors='ignore'),
                    use_container_width=True,
                    height=400,
                )

            with tab_wit:
                st.caption(t("analisis_rango_wit_caption"))
                st.dataframe(
                    wit_df.rename(columns={
                        'witness':       'Testigo',
                        'note':          'Nota (rango)',
                        'category':      'Categoría',
                        'n_families':    'Familias distintas',
                        'n_events':      'Total eventos',
                        'families_list': 'Familias',
                    }),
                    use_container_width=True,
                    height=400,
                )

            with tab_grafo:
                if nx is None or Network is None:
                    st.warning(t("err_networkx"))
                else:
                    with st.spinner(t("analisis_calc_puentes")):
                        G_rank = build_rank_witness_graph(df, min_events=rango_min_events, rank_categories=tuple(rango_cats) if rango_cats else ("rango_social",))
                    if G_rank is None:
                        st.info(t("analisis_rango_sin_datos"))
                    else:
                        net_rank = Network(height="620px", width="100%", notebook=False)
                        for n, a in G_rank.nodes(data=True):
                            if a.get('type') == 'witness':
                                color = '#e07b39'
                                size  = 12 + 3 * G_rank.degree(n)
                            else:
                                color = '#4a90d9'
                                size  = 10 + 2 * G_rank.degree(n)
                            net_rank.add_node(
                                str(n),
                                label=str(a.get('label', n)),
                                title=str(a.get('label', n)),
                                color=color,
                                size=size,
                            )
                        for u, v, a in G_rank.edges(data=True):
                            net_rank.add_edge(str(u), str(v), value=a.get('weight', 1))
                        import tempfile as _tf
                        _tmp_rank = _tf.NamedTemporaryFile(delete=False, suffix='.html')
                        _tmp_rank_name = _tmp_rank.name
                        _tmp_rank.close()
                        net_rank.save_graph(_tmp_rank_name)
                        _html_rank = open(_tmp_rank_name, 'r', encoding='utf-8').read()
                        st.components.v1.html(_html_rank, height=640, width=1100)
    except Exception as e:
        st.error(f"Error en análisis de rango social: {e}")


def build_similarity_clusters(unique_raws, threshold=78):
    """
    Construye clusters por similitud de nombres (closure transitivo).
    Devuelve lista de clusters (cada uno es lista de raw_name).
    """
    buckets = defaultdict(list)
    for r in unique_raws:
        key = (str(r).strip().lower()[:2], min(50, max(0, len(str(r).strip())//3)))
        buckets[key].append(r)

    pair_edges = []
    for key, raws in buckets.items():
        if len(raws) < 2:
            continue
        for i in range(len(raws)):
            for j in range(i+1, len(raws)):
                a = raws[i]; b = raws[j]
                score = name_similarity(a, b)
                if score >= threshold:
                    pair_edges.append((a, b))

    # union-find
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pair_edges:
        union(a, b)

    clusters = defaultdict(list)
    for x in parent:
        clusters[find(x)].append(x)

    return [sorted(v) for v in clusters.values() if len(v) >= 2]

# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONALIDAD: Comparador de dos testigos
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def witness_comparison_stats(wit_a: str, wit_b: str, _by_witness: dict, _places_index: dict) -> dict:
    """
    Calcula estadísticas comparativas de dos testigos para apoyar la decisión
    de confirmar o descartar que sean la misma persona.

    Returns:
        dict con métricas de cada testigo, solapamientos y resultado bayesiano.
    """
    events_a = _by_witness.get(wit_a, [])
    events_b = _by_witness.get(wit_b, [])

    def _stats(evts):
        years = []
        places = set()
        families = set()
        for e in evts:
            d = str(e.get('date_iso', '') or '')
            if len(d) >= 4 and d[:4].isdigit():
                years.append(int(d[:4]))
            p = str(e.get('place_name', '') or '')
            if p and p not in ('nan', 'None'):
                places.add(p)
            sn = extract_surname_improved(str(e.get('subj_name', '') or ''))
            if sn:
                families.add(sn)
        return {
            'count': len(evts),
            'year_min': min(years) if years else None,
            'year_max': max(years) if years else None,
            'places': places,
            'families': families,
        }

    sa = _stats(events_a)
    sb = _stats(events_b)

    familias_comun = sa['families'] & sb['families']
    lugares_comun = sa['places'] & sb['places']
    overlap_temporal = bool(
        sa['year_min'] is not None and sb['year_min'] is not None
        and sa['year_min'] <= (sb['year_max'] or sa['year_min'])
        and sb['year_min'] <= (sa['year_max'] or sb['year_min'])
    )

    bayes_result = {'probability_same_person': None, 'recommendation': 'n/a', 'explanation': ''}
    try:
        bayes_result = bayesian_identity_probability(
            events_a, events_b, wit_a, wit_b,
            prior=0.05, places_index=_places_index,
        )
    except Exception:
        pass

    return {
        'count_a': sa['count'], 'year_min_a': sa['year_min'], 'year_max_a': sa['year_max'],
        'places_a_n': len(sa['places']), 'families_a_n': len(sa['families']),
        'count_b': sb['count'], 'year_min_b': sb['year_min'], 'year_max_b': sb['year_max'],
        'places_b_n': len(sb['places']), 'families_b_n': len(sb['families']),
        'familias_comun': familias_comun,
        'lugares_comun': lugares_comun,
        'overlap_temporal': overlap_temporal,
        'bayes_result': bayes_result,
    }


def _render_comparison_map(events_a, events_b, places_index_map, label_a, label_b):
    """Renderiza mapa folium bicolor con las trayectorias de dos testigos."""
    if folium is None:
        st.warning(t("err_folium_comparativo"))
        return

    all_lats, all_lons = [], []

    def _get_coords(evts):
        coords = []
        for e in evts:
            try:
                lat = float(e.get('lat') or 0)
                lon = float(e.get('lon') or 0)
                if lat and lon:
                    coords.append((lat, lon, str(e.get('date_iso', '') or ''), str(e.get('place_name', '') or '')))
            except Exception:
                pass
        coords.sort(key=lambda x: x[2])
        return coords

    coords_a = _get_coords(events_a)
    coords_b = _get_coords(events_b)
    all_lats += [c[0] for c in coords_a + coords_b]
    all_lons += [c[1] for c in coords_a + coords_b]

    if not all_lats:
        st.info(t("empty_coords_comparativo"))
        return

    center = [sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)]
    m = folium.Map(location=center, zoom_start=9, tiles='CartoDB positron')

    for color, coords, label in [('blue', coords_a, label_a), ('red', coords_b, label_b)]:
        for lat, lon, fecha, place in coords:
            folium.CircleMarker(
                location=[lat, lon],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
                popup=folium.Popup(f"<b>{label}</b><br>{place}<br>{fecha}", max_width=200),
            ).add_to(m)
        if len(coords) > 1:
            folium.PolyLine(
                [(c[0], c[1]) for c in coords],
                color=color,
                weight=2,
                opacity=0.6,
                tooltip=label,
            ).add_to(m)

    # Leyenda simple
    legend_html = (
        '<div style="position:fixed;bottom:30px;left:30px;background:white;'
        'padding:8px 12px;border:1px solid #ccc;border-radius:6px;font-size:13px;z-index:9999;">'
        f'<span style="color:blue">&#9679;</span> {label_a}&nbsp;&nbsp;'
        f'<span style="color:red">&#9679;</span> {label_b}'
        '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend_html))
    embed_folium(m, width=900, height=400)


def page_confirmar_coincidencias(dataset: WitnessDataset):
    """
    Página mejorada de confirmaciones:
      - clusters automáticos
      - selección por eventos
      - fusionar eventos (misma persona)
      - marcar eventos como diferentes (pairwise)
      - marcar cluster como revisado (status)
      - persistencia de acciones (user + timestamp)
    """
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    st.header(t("hdr_confirmar"))

    # ── Comparador de dos testigos ──────────────────────────────────────────
    with st.expander(t("analisis_comparar_testigos"), expanded=False):
        st.caption(t("analisis_comparar_caption"))
        try:
            all_wit_list = sorted(by_witness.keys())
            col_a, col_b = st.columns(2)
            wit_sel_a = col_a.selectbox("Testigo A", [""] + all_wit_list, key="comp_wit_a")
            wit_sel_b = col_b.selectbox("Testigo B", [""] + all_wit_list, key="comp_wit_b")

            if wit_sel_a and wit_sel_b and wit_sel_a != wit_sel_b:
                stats_cmp = witness_comparison_stats(wit_sel_a, wit_sel_b, by_witness, places_index)

                # Métricas paralelas
                mc1, mc2 = st.columns(2)
                with mc1:
                    st.markdown(f"**{wit_sel_a}**")
                    st.metric(t("super_apariciones"), stats_cmp['count_a'])
                    ya, yb_a = stats_cmp['year_min_a'], stats_cmp['year_max_a']
                    st.metric(t("super_periodo_activo"), f"{ya}–{yb_a}" if ya else "—")
                    st.metric(t("super_lugares_unicos"), stats_cmp['places_a_n'])
                    st.metric(t("super_familias"), stats_cmp['families_a_n'])
                with mc2:
                    st.markdown(f"**{wit_sel_b}**")
                    st.metric(t("super_apariciones"), stats_cmp['count_b'])
                    ya2, yb_b = stats_cmp['year_min_b'], stats_cmp['year_max_b']
                    st.metric(t("super_periodo_activo"), f"{ya2}–{yb_b}" if ya2 else "—")
                    st.metric(t("super_lugares_unicos"), stats_cmp['places_b_n'])
                    st.metric(t("super_familias"), stats_cmp['families_b_n'])

                # Score bayesiano
                bayes = stats_cmp['bayes_result']
                prob = bayes.get('probability_same_person')
                if prob is not None:
                    prob_pct = int(round(float(prob) * 100))
                    rec = str(bayes.get('recommendation', ''))
                    st.metric(t("timeline_prob_misma"), f"{prob_pct}%", delta=rec)
                    explanation = bayes.get('explanation', '')
                    if explanation:
                        st.caption(explanation)

                # Solapamiento
                st.markdown(t("sub_solapamiento"))
                fc = stats_cmp['familias_comun']
                lc = stats_cmp['lugares_comun']
                st.write(t("analisis_familias_comun", val=', '.join(sorted(fc)) if fc else t("analisis_ninguna")))
                st.write(t("analisis_lugares_comun", val=', '.join(sorted(lc)) if lc else t("analisis_ninguno")))
                st.write(t("analisis_actividad_simultanea", val=t("analisis_si") if stats_cmp['overlap_temporal'] else t("analisis_no_val")))

                # Tablas paralelas
                ta1, ta2 = st.columns(2)
                ev_cols = [c for c in ['date_iso', 'type', 'place_name', 'subj_name'] if c in df.columns]
                col_rename = {'date_iso': t("notas_col_fecha"), 'type': t("explorar_tipo_evento"), 'place_name': t("notas_col_lugar"), 'subj_name': t("notas_col_sujeto")}
                with ta1:
                    st.caption(t("analisis_eventos_de", wit=wit_sel_a))
                    df_cmp_a = pd.DataFrame(by_witness.get(wit_sel_a, []))
                    if not df_cmp_a.empty and ev_cols:
                        st.dataframe(
                            df_cmp_a[[c for c in ev_cols if c in df_cmp_a.columns]]
                            .sort_values('date_iso', na_position='last')
                            .rename(columns=col_rename),
                            use_container_width=True, height=260,
                        )
                with ta2:
                    st.caption(t("analisis_eventos_de", wit=wit_sel_b))
                    df_cmp_b = pd.DataFrame(by_witness.get(wit_sel_b, []))
                    if not df_cmp_b.empty and ev_cols:
                        st.dataframe(
                            df_cmp_b[[c for c in ev_cols if c in df_cmp_b.columns]]
                            .sort_values('date_iso', na_position='last')
                            .rename(columns=col_rename),
                            use_container_width=True, height=260,
                        )

                # Mapa bicolor
                _render_comparison_map(
                    by_witness.get(wit_sel_a, []),
                    by_witness.get(wit_sel_b, []),
                    places_index,
                    wit_sel_a,
                    wit_sel_b,
                )
            elif wit_sel_a and wit_sel_b and wit_sel_a == wit_sel_b:
                st.info(t("empty_selecciona_dos"))
        except Exception as e:
            st.error(t("analisis_error_comparador", e=e))

    conf = load_confirmations()
    tmp = df.copy()
    if 'witness_raw' not in tmp.columns:
        tmp['witness_raw'] = tmp.get('witness_norm', '').astype(str)
    tmp['witness_raw'] = tmp['witness_raw'].astype(str).fillna("")
    counts = tmp['witness_raw'].value_counts().to_dict()
    unique_raws = sorted(list(counts.keys()))

    st.markdown(t("confirmar_ajustar"))
    cluster_threshold = st.slider(t("confirmar_umbral"), 40, 100, 78)

    clusters = build_similarity_clusters(unique_raws, threshold=cluster_threshold)
    st.write(t("confirmar_clusters_encontrados", n=len(clusters)))

    max_clusters = st.number_input(t("label_mostrar_n_clusters"), 1, 500, 20)
    clusters = clusters[:max_clusters]

    # ocultar clusters totalmente revisados si checkbox activo
    hide_reviewed = st.checkbox(t("confirmar_ocultar_revisados"), value=True)

    # helper: determine cluster state (pending/reviewed/partial)
    def cluster_state(cluster_members):
        # if all events for members are present in event_groups or marked different with status -> reviewed
        conf_local = load_confirmations()
        status = conf_local.get('status', {})
        # if any event for members not in status -> pending
        for m in cluster_members:
            evs = tmp[tmp['witness_raw'] == m]['event_id'].astype(str).tolist()
            for e in evs:
                s = status.get(e)
                if not s:
                    return "pending"
        return "reviewed"

    display_count = 0
    for ci, cl in enumerate(clusters, start=1):
        state = cluster_state(cl)
        if hide_reviewed and state == "reviewed":
            continue
        display_count += 1

        # icon based on state
        icon = "⚪"
        if state == "reviewed":
            icon = "🟢"
        elif state == "pending":
            icon = "⚪"

        exp_label = f"{icon} Cluster {ci} — {len(cl)} miembros"
        with st.expander(exp_label, expanded=(state!="reviewed")):
            # build event-level rows
            rows = []
            for m in cl:
                evs = tmp[tmp['witness_raw']==m][['event_id','date_iso','place_name']].astype(str)
                for idx, evrow in evs.iterrows():
                    rows.append({
                        'raw': m,
                        'event_id': evrow['event_id'],
                        'date': evrow['date_iso'],
                        'place': evrow['place_name']
                    })
            if not rows:
                st.write(t("confirmar_no_apariciones"))
                continue

            events_df = pd.DataFrame(rows)
            st.write(t("confirmar_apariciones_detectadas"))
            # show full table
            st.dataframe(events_df, use_container_width=True)

            # generate checkboxes per row (we use keys based on row index to avoid duplicates
            # when the same event_id appears under multiple cluster members)
            st.markdown(t("confirmar_sel_merge"))
            merge_selected = []
            for ri, r in enumerate(rows):
                k = f"merge_evt_{ci}_{ri}_{r['event_id']}"
                if st.checkbox(f"{r['event_id']} — {r['raw']} — {r['date']} — {r['place']}", key=k):
                    merge_selected.append(r['event_id'])

            if st.button(t("confirmar_merge", ci=ci), key=f"btn_merge_ev_{ci}"):
                if len(merge_selected) < 2:
                    st.warning(t("confirmar_sel_min2_merge"))
                else:
                    # create new event_group id
                    gid = str(uuid.uuid4())
                    conf_local = load_confirmations()
                    ev_groups = conf_local.get('event_groups', {})
                    ev_groups[gid] = list(dict.fromkeys(merge_selected))  # dedup preserving order
                    conf_local['event_groups'] = ev_groups
                    # record status per event
                    for e in ev_groups[gid]:
                        conf_local.setdefault('status', {})[str(e)] = {
                            'state': 'same',
                            'timestamp': datetime.now(_tz.utc).isoformat(),
                            'user': USER
                        }
                    save_confirmations(conf_local)
                    # rebuild witness_canon/disambiguation
                    apply_event_confirmations_and_rebuild_witness_canon()
                    compute_endogamy_stats.clear()
                    compute_bridge_families.clear()
                    st.success(t("confirmar_fusionados", n=len(ev_groups[gid]), gid=gid))
                    st.rerun()

            st.markdown(t("confirmar_sel_diff"))
            diff_selected = []
            for ri, r in enumerate(rows):
                k2 = f"diff_evt_{ci}_{ri}_{r['event_id']}"
                if st.checkbox(f"{r['event_id']} — {r['raw']} — {r['date']} — {r['place']}", key=k2):
                    diff_selected.append(r['event_id'])

            if st.button(t("confirmar_diferentes", ci=ci), key=f"btn_diff_ev_{ci}"):
                if len(diff_selected) < 2:
                    st.warning(t("confirmar_sel_min2_diff"))
                else:
                    conf_local = load_confirmations()
                    dif = conf_local.get('different', [])
                    for i in range(len(diff_selected)):
                        for j in range(i+1, len(diff_selected)):
                            a = str(diff_selected[i]); b = str(diff_selected[j])
                            pair = [a, b]
                            if pair not in dif and [b, a] not in dif:
                                dif.append(pair)
                            conf_local.setdefault('status', {})[a] = {'state':'different','timestamp':datetime.now(_tz.utc).isoformat(),'user':USER}
                            conf_local.setdefault('status', {})[b] = {'state':'different','timestamp':datetime.now(_tz.utc).isoformat(),'user':USER}
                    conf_local['different'] = dif
                    save_confirmations(conf_local)
                    compute_endogamy_stats.clear()
                    compute_bridge_families.clear()
                    st.success(t("confirmar_marcados_diferentes", n=len(diff_selected)))
                    st.rerun()

            # Botón "el resto son diferentes": calcula qué eventos del cluster NO están
            # ya en un event_group confirmado y los marca como diferentes entre sí y
            # respecto a los que sí están fusionados.
            if st.button(t("confirmar_resto_diferentes", ci=ci), key=f"btn_rest_diff_{ci}",
                         help="Marca como 'diferente' todo evento de este cluster que no esté ya en un merge confirmado"):
                conf_local = load_confirmations()
                # Recopilar todos los event_ids ya fusionados en algún grupo
                merged_ids: set[str] = set()
                for grp_ids in conf_local.get('event_groups', {}).values():
                    merged_ids.update(str(e) for e in grp_ids)

                # Eventos de este cluster que NO están en ningún merge
                all_cluster_ids = [str(r['event_id']) for r in rows]
                unmerged = [eid for eid in dict.fromkeys(all_cluster_ids) if eid not in merged_ids]

                if len(unmerged) == 0:
                    st.info(t("confirmar_todos_en_grupo"))
                else:
                    dif = conf_local.get('different', [])
                    sts = conf_local.setdefault('status', {})

                    # Pares entre los no-fusionados (personas distintas entre sí)
                    for i in range(len(unmerged)):
                        for j in range(i + 1, len(unmerged)):
                            a, b = unmerged[i], unmerged[j]
                            if [a, b] not in dif and [b, a] not in dif:
                                dif.append([a, b])
                            sts[a] = {'state': 'different', 'timestamp': datetime.now(_tz.utc).isoformat(), 'user': USER}
                            sts[b] = {'state': 'different', 'timestamp': datetime.now(_tz.utc).isoformat(), 'user': USER}

                    # También pares cruzados entre no-fusionados y cada grupo de merge del cluster
                    for grp_ids in conf_local.get('event_groups', {}).values():
                        grp_set = {str(e) for e in grp_ids}
                        # Solo grupos que contengan al menos un evento de este cluster
                        if not grp_set & set(all_cluster_ids):
                            continue
                        for u in unmerged:
                            rep = next(iter(grp_set))  # un representante del grupo
                            if [u, rep] not in dif and [rep, u] not in dif:
                                dif.append([u, rep])
                            sts[u] = {'state': 'different', 'timestamp': datetime.now(_tz.utc).isoformat(), 'user': USER}

                    conf_local['different'] = dif
                    save_confirmations(conf_local)
                    compute_endogamy_stats.clear()
                    compute_bridge_families.clear()
                    st.success(t("confirmar_restantes", n=len(unmerged)))
                    st.rerun()

            # Mark cluster reviewed: set status for all events in cluster if not present
            if st.button(t("confirmar_revisado", ci=ci), key=f"btn_review_cluster_{ci}"):
                conf_local = load_confirmations()
                sts = conf_local.setdefault('status', {})
                # mark all events for cluster as reviewed if not present
                for r in rows:
                    eid = str(r['event_id'])
                    if eid not in sts:
                        sts[eid] = {'state':'reviewed','timestamp': datetime.now(_tz.utc).isoformat(), 'user': USER}
                conf_local['status'] = sts
                save_confirmations(conf_local)
                compute_endogamy_stats.clear()
                compute_bridge_families.clear()
                st.success(t("confirmar_cluster_revisado"))
                st.rerun()

    if display_count == 0:
        st.info(t("empty_clusters_mapa"))

def page_timeline(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    st.header(t("hdr_timeline"))

    if not PLOTLY_OK:
        st.error(t("timeline_plotly_instalar"))
        return

    import re as _re5
    col = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
    df_t = df.copy()
    def _yr5(s):
        m = _re5.match(r'^(\d{4})', str(s).strip()) if s and str(s) not in ('nan','None','') else None
        return int(m.group(1)) if m else None
    df_t['_year'] = df_t['date_iso'].apply(_yr5)
    valid_years = df_t['_year'].dropna()
    if valid_years.empty:
        st.warning(t("timeline_no_fecha2"))
        return

    min_y, max_y = int(valid_years.min()), int(valid_years.max())

    # ── Controles ──────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    with c1:
        period = st.radio(t("analisis_agreg"), [t("analisis_por_anio"), t("analisis_por_decada")], horizontal=True)
    with c2:
        metric = st.selectbox(t("label_metrica_principal"), [
            "total_eventos", "testigos_unicos", "lugares_unicos",
            "familias_unicas", "apellidos_testigos"
        ])
    with c3:
        year_range = st.slider(t("explorar_rango_anios"), min_y, max_y, (min_y, max_y), step=1)

    period_key = 'year' if period == t("analisis_por_anio") else 'decade'

    # ── Agregación ─────────────────────────────────────────────────────────
    tl = aggregate_timeline_by_period(df, period=period_key)
    if tl.empty:
        st.info(t("timeline_no_suficientes"))
        return

    # Filtrar por rango
    tl = tl[(tl['año'] >= year_range[0]) & (tl['año'] <= year_range[1])]
    if tl.empty:
        st.info(t("timeline_no_rango"))
        return

    metric_labels = {
        'total_eventos': 'Total eventos',
        'testigos_unicos': 'Testigos únicos',
        'lugares_unicos': 'Lugares únicos',
        'familias_unicas': 'Familias únicas',
        'apellidos_testigos': 'Apellidos de testigos',
    }

    # ── Gráfico principal ──────────────────────────────────────────────────
    anomalies = detect_temporal_anomalies(tl)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=tl['periodo'],
        y=tl[metric],
        mode='lines+markers',
        name=metric_labels.get(metric, metric),
        line=dict(color='royalblue', width=2),
        marker=dict(size=6),
        hovertemplate='<b>%{x}</b><br>' + metric_labels.get(metric, metric) + ': %{y}<extra></extra>',
    ))
    # Marcar anomalías
    for a in anomalies:
        period_rows = tl[tl['periodo'] == a['periodo']]
        if not period_rows.empty:
            fig.add_vline(
                x=period_rows.index[0],
                line_dash='dot',
                line_color='red' if a['tipo'] == 'pico' else 'orange',
                annotation_text=f"{a['tipo'].upper()} ({a['z_score']}σ)",
                annotation_position='top',
                annotation_font_size=9,
            )
    fig.update_layout(
        title=t("timeline_evolucion_titulo", metric=metric_labels.get(metric, metric)),
        xaxis_title=t("timeline_periodo"),
        yaxis_title=metric_labels.get(metric, metric),
        height=420,
        hovermode='x unified',
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Tabla resumen ──────────────────────────────────────────────────────
    with st.expander(t("analisis_ver_tabla")):
        st.dataframe(tl, use_container_width=True)

    # ── Anomalías ──────────────────────────────────────────────────────────
    if anomalies:
        st.markdown(t("sub_actividad_anomala"))
        st.dataframe(pd.DataFrame(anomalies), use_container_width=True)
    else:
        st.info(t("analisis_no_anomalas"))

    # ── Métricas de red ────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_metricas_red"))
    net_df = calculate_network_metrics_over_time(df, period=period_key)
    net_df = net_df[(net_df['año'] >= year_range[0]) & (net_df['año'] <= year_range[1])]
    if not net_df.empty:
        fig_net = go.Figure()
        fig_net.add_trace(go.Bar(
            x=net_df['periodo'],
            y=net_df['testigos_nuevos'],
            name=t("timeline_nuevos_barra"),
            marker_color='steelblue',
        ))
        fig_net.add_trace(go.Scatter(
            x=net_df['periodo'],
            y=net_df['ratio_nuevos'],
            name=t("timeline_nuevos_ratio"),
            yaxis='y2',
            mode='lines+markers',
            line=dict(color='darkorange', width=2),
        ))
        fig_net.update_layout(
            title=t("timeline_nuevos_titulo"),
            xaxis_title=t("timeline_periodo"),
            yaxis_title=t("timeline_nuevos_y"),
            yaxis2=dict(title=t("timeline_nuevos_y2"), overlaying='y', side='right', range=[0, 1]),
            height=380,
            hovermode='x unified',
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
        )
        st.plotly_chart(fig_net, use_container_width=True)
        with st.expander(t("analisis_ver_metricas")):
            st.dataframe(net_df, use_container_width=True)

    # ── Gantt de testigos más activos ──────────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_periodo_activo"))
    top_n_gantt = st.slider(t("timeline_gantt_n"), 5, 50, 20, key="gantt_n")
    try:
        import re as _re
        df_g = df.copy()
        # Extraer año directamente del string para evitar el límite de int64 nanoseconds de Pandas
        def _extract_year(s):
            if not s or str(s) in ('nan', 'None', ''): return None
            m = _re.match(r'^(\d{4})', str(s).strip())
            return int(m.group(1)) if m else None
        df_g['_year'] = df_g['date_iso'].apply(_extract_year)
        df_g = df_g[df_g['_year'].notna()]
        # Normalizar date_iso a YYYY-MM-DD para Plotly (que acepta strings ISO)
        def _to_iso_str(s):
            s = str(s).strip()
            if _re.match(r'^\d{4}-\d{2}-\d{2}$', s): return s
            if _re.match(r'^\d{4}-\d{2}$', s): return s + '-01'
            if _re.match(r'^\d{4}$', s): return s + '-01-01'
            return None
        df_g['_date_str'] = df_g['date_iso'].apply(_to_iso_str)
        df_g = df_g[df_g['_date_str'].notna()]
        col_w = 'witness_canon' if 'witness_canon' in df_g.columns else 'witness_raw'
        top_witnesses = df_g[col_w].astype(str).value_counts().head(top_n_gantt).index.tolist()
        df_g = df_g[df_g[col_w].isin(top_witnesses)]
        gantt_rows = []
        for w, grp in df_g.groupby(col_w):
            dates_sorted = sorted(grp['_date_str'].tolist())
            inicio = dates_sorted[0]
            fin = dates_sorted[-1]
            # Plotly timeline requiere Fin > Inicio
            if fin <= inicio:
                year = int(inicio[:4])
                fin = f"{year+1}{inicio[4:]}"
            gantt_rows.append({
                'Testigo': str(w)[:40],
                'Inicio': inicio,
                'Fin': fin,
                'Total_eventos': len(grp),
            })
        if gantt_rows:
            gantt_df = pd.DataFrame(gantt_rows).sort_values('Total_eventos', ascending=False)
            gantt_df['Inicio'] = pd.to_datetime(gantt_df['Inicio'], errors='coerce')
            gantt_df['Fin'] = pd.to_datetime(gantt_df['Fin'], errors='coerce')
            gantt_df = gantt_df.dropna(subset=['Inicio', 'Fin'])
            fig_gantt = px.timeline(
                gantt_df,
                x_start='Inicio',
                x_end='Fin',
                y='Testigo',
                color='Total_eventos',
                color_continuous_scale='Blues',
                title=t("timeline_gantt_titulo", n=top_n_gantt),
                hover_data=['Total_eventos'],
            )
            fig_gantt.update_yaxes(autorange="reversed")
            fig_gantt.update_layout(height=max(350, top_n_gantt * 22))
            st.plotly_chart(fig_gantt, use_container_width=True)
    except Exception as e:
        st.error(t("analisis_error_gantt", e=e))


# ──────────────────────────────────────────────────────────────────────────────
# FASE 5: Probabilidad bayesiana de identidad
# ──────────────────────────────────────────────────────────────────────────────

# Tablas demográficas históricas para España rural (siglos XVIII-XIX)
# Fuente: estimaciones basadas en censos históricos españoles y literatura
# demográfica (Pérez Moreda, 1980; INE series históricas)
_LIFE_EXPECTANCY_SPAIN = {
    (1700, 1800): 32,   # Esperanza de vida al nacer ~30-35 años
    (1800, 1850): 37,
    (1850, 1900): 42,
    (1900, 1950): 50,
}

# Movilidad típica en zonas rurales de España (pre-ferrocarril < 1860)
# Basado en respuestas del usuario: raramente más de 100 km, normalmente decenas
_MOBILITY_PARAMS = {
    'pre_1860': {
        'typical_radius_km': 25,   # Radio típico de movilidad
        'hard_limit_km': 100,      # Raramente más de esto
        'scale': 20.0,             # Parámetro escala de distribución exponencial
    },
    'post_1860': {
        'typical_radius_km': 60,
        'hard_limit_km': 300,
        'scale': 50.0,
    },
}

# Indicadores de clase social en notas (títulos honoríficos)
_SOCIAL_TITLES = {
    'alto': {'don', 'doña', 'dona', 'señor', 'señora', 'noble', 'hidalgo',
              'licenciado', 'licenciada', 'doctor', 'dr', 'dra', 'fray',
              'sor', 'maestro', 'regidor', 'alcalde', 'escribano', 'notario'},
    'medio': {'oficial', 'artesano', 'mercader', 'comerciante', 'boticario'},
    'bajo': {'jornalero', 'labrador', 'pastor', 'criado', 'mozo'},
}


def _life_expectancy_for_year(year: int) -> float:
    """Devuelve esperanza de vida estimada para la época."""
    for (y0, y1), exp in _LIFE_EXPECTANCY_SPAIN.items():
        if y0 <= year < y1:
            return float(exp)
    if year < 1700:
        return 30.0
    return 55.0


def _mobility_params_for_year(year: int) -> dict:
    return _MOBILITY_PARAMS['pre_1860'] if year < 1860 else _MOBILITY_PARAMS['post_1860']


def _extract_social_class_from_note(note_text: str) -> str | None:
    """
    Analiza el texto de notas buscando indicadores de clase social.
    Devuelve 'alto', 'medio', 'bajo' o None.
    """
    if not note_text or str(note_text).strip() in ('', 'nan', 'None'):
        return None
    txt_lower = normalize(str(note_text))
    for cls, tokens in _SOCIAL_TITLES.items():
        for tok in tokens:
            if tok in txt_lower.split():
                return cls
    return None


def _extract_social_class_from_name(raw_name: str) -> str | None:
    """Detecta clase social por título en el propio nombre del testigo."""
    if not raw_name:
        return None
    tokens_lower = set(normalize(str(raw_name)).split())
    for cls, title_set in _SOCIAL_TITLES.items():
        if tokens_lower & title_set:
            return cls
    return None


# ── Clasificación de notas de testigos ───────────────────────────────────────

# Tokens de relación de parentesco/vínculo con otra persona o co-testigo.
# Incluye variantes catalanas/valencianas/latinas frecuentes en registros históricos.
_RELATION_PHRASES = {
    # Cónyuge explícito con co-testigo o persona mencionada
    'consortes', 'coniuges', 'conjuges', 'conyuges', 'conjugues', 'consorte',
    'conyuge', 'conjuges',
    # Esposa/marido del otro testigo
    'esposa del padrino', 'esposo del padrino', 'mujer del padrino',
    'muller del padri', 'esposa de la madrina',
    # Relaciones con sujeto del evento o familia
    'su muger', 'su mujer', 'sa muller', 'su esposa', 'su marido',
    'su madre', 'su padre', 'su abuelo', 'su abuela', 'su tio', 'su tia',
    'su hija', 'su hijo', 'su hermana', 'su hermano', 'su prima', 'su primo',
    'su sobrina', 'su sobrino', 'su nieto', 'su nieta', 'su yerno', 'su nuera',
    'su padrino', 'su madrina', 'el abuelo', 'la abuela',
    'abuelo materno', 'abuelo paterno', 'abuela materna', 'abuela paterna',
    'su abuelo materno', 'su abuelo paterno', 'su abuela materna', 'su abuela paterna',
    'su medio hermano', 'su medio hermana',
    'padre del contrayente', 'madre del contrayente',
    'el padre de la novia', 'la madre del novio',
    # Relaciones simples (sin posesivo — solo cuando aparecen solos)
    'hermano', 'hermana', 'primo', 'prima', 'cunado', 'cunada', 'yerno', 'nuera',
    'abuelo', 'abuela', 'abuelos',
    # Catalán/valenciano/latín
    'germans', 'filla', 'filio', 'filla del sobredit', 'neboda', 'sogre',
    'nora', 'sa nora',
    # Relación con persona mencionada por nombre (patrón dinámico)
    # "muller de X", "mujer de X", "viuda de X" — se detectan via regex más abajo
}

# Tokens de rango social / títulos nobiliarios
_RANGO_SOCIAL_TOKENS = {
    'don', 'dona', 'senor', 'senora', 'noble', 'hidalgo', 'hidalgos',
    'marques', 'marquesa', 'conde', 'condesa', 'baron', 'baronesa',
    'cavaller', 'caballero', 'ciudadano', 'ciutada', 'ciutadans',
    'senyor', 'senyora',
}

# Tokens de profesión / oficio / cargo eclesiástico o civil
_PROFESION_TOKENS = {
    # Eclesiásticos
    'presbitero', 'presbiter', 'prevere', 'mossen', 'mosse', 'mosen',
    'canonge', 'canonigo', 'capellan', 'capella', 'cura', 'vicario',
    'arcediano', 'chantre', 'sacerdote', 'beneficiado', 'clerigo',
    'ermitano', 'beata', 'fray', 'sor', 'hermano', 'reverendo',
    'sacristan', 'sacristà', 'sacrista',
    # Notariales / judiciales
    'notario', 'notari', 'escribano', 'secretario', 'procurador',
    'jurado', 'jurat', 'regidor', 'alcalde', 'teniente', 'portero',
    # Médicos / académicos
    'doctor', 'licenciado', 'medico', 'cirujano', 'siruja',
    'maestro', 'catedratico',
    # Oficios
    'artesano', 'mercader', 'boticario', 'oficial', 'labrador',
    'jornalero', 'pastor', 'criado', 'estudiante', 'mancebo',
    'ferrer', 'argenter', 'sastre', 'carpintero', 'albanil',
    'peixcador', 'espaser', 'espardenyer', 'pintor',
    # Militares
    'capitan', 'teniente',
}

# Estado civil: palabras que indican estado marital o etapa vital
_ESTADO_CIVIL_TOKENS = {
    'viuda', 'viudo', 'doncella', 'donsella', 'soltero', 'soltera',
    'casado', 'casada', 'menor', 'mayor', 'major',
}

_ALL_NOTE_CATEGORIES = ['relación', 'profesión', 'rango_social', 'estado_civil', 'origen', 'otro']


def classify_note(note_text: str, _overrides: dict | None = None) -> str:
    """Clasifica el texto de una nota de testigo en una categoría.

    Orden de prioridad:
      1. Corrección manual (override)
      2. Relación (cónyuge con co-testigo, parentesco con sujeto, "muller de X"...)
      3. Rango social (marqués, cavaller, ciudadano...)
      4. Profesión (presbítero, notario, escribano, sacristán...)
      5. Estado civil (viuda, doncella, menor, casado...)
      6. Origen (natural de, vecino de, "de [topónimo]"...)
      7. otro
    """
    if not note_text or str(note_text).strip() in ('', 'nan', 'None'):
        return 'otro'
    raw = str(note_text).strip()

    # 1. Override manual persiste sobre todo
    if _overrides is None:
        _overrides = st.session_state.get('tst_note_category_overrides', {})
    if raw in _overrides:
        v = _overrides[raw]
        if isinstance(v, list):
            return v[0] if v else 'otro'
        # Comma-sep string: devolver la primera categoría
        parts = [p.strip() for p in str(v).split(',') if p.strip()]
        return parts[0] if parts else 'otro'

    txt = normalize(raw)
    txt_clean = re.sub(r'[^\w\s]', ' ', txt)
    tokens = set(txt_clean.split())

    # 2. Relación
    # 2a. Frases fijas (cónyuge con co-testigo, parentesco posesivo)
    for phrase in _RELATION_PHRASES:
        if phrase in txt_clean:
            return 'relación'
    # 2b. Patrones "muller/mujer/muger/esposa/marido/viuda/viudo de [nombre]"
    if re.search(r'\b(muller|mujer|muger|esposa|esposo|marido|viuda|viudo|filla|filio|sogre|nora)\s+de\b', txt):
        return 'relación'
    # 2c. Tokens sueltos que solo indican parentesco (abuelo, hermanos, etc.)
    _relation_tokens = {'abuelo', 'abuela', 'abuelos', 'hermanos', 'germans',
                        'neboda', 'sogre', 'nora'}
    if tokens & _relation_tokens:
        return 'relación'

    # 3. Rango social
    if tokens & _RANGO_SOCIAL_TOKENS:
        return 'rango_social'

    # 4. Profesión
    if tokens & _PROFESION_TOKENS:
        return 'profesión'

    # 5. Estado civil
    if tokens & _ESTADO_CIVIL_TOKENS:
        return 'estado_civil'

    # 6. Origen (se evalúa al final para no absorber "viuda de X")
    if re.search(r'\b(natural|vecino|vecina|vecinos|vezino|vezina|vezinos|'
                 r'oriundo|oriunda|habitante|residente)\b', txt):
        return 'origen'
    if re.search(r'\bde\s+[a-z]{3,}', txt):
        return 'origen'

    return 'otro'


def get_note_categories(note_text: str, _overrides: dict | None = None) -> list[str]:
    """Retorna todas las categorías de una nota, soportando multi-categoría.

    Si hay override con comas ('origen, estado_civil'), devuelve ['origen', 'estado_civil'].
    Si no hay override o solo hay una categoría, devuelve una lista con un elemento.
    """
    if _overrides is None:
        _overrides = st.session_state.get('tst_note_category_overrides', {})
    raw = str(note_text).strip()
    if raw in _overrides:
        v = _overrides[raw]
        if isinstance(v, list):
            cats = [c for c in v if c in _ALL_NOTE_CATEGORIES]
            return cats if cats else [classify_note(note_text, _overrides)]
        parts = [p.strip() for p in str(v).split(',') if p.strip() in _ALL_NOTE_CATEGORIES]
        return parts if parts else [classify_note(note_text, _overrides)]
    return [classify_note(note_text, _overrides)]


# ── Feature 1: Similitud nominal ─────────────────────────────────────────────

def calculate_name_similarity_features(name_a: str, name_b: str) -> dict:
    """
    Calcula múltiples métricas de similitud nominal.
    Devuelve dict con: edit_distance, phonetic_match, token_jaccard, token_overlap_score.
    """
    na = normalize(str(name_a or ""))
    nb = normalize(str(name_b or ""))

    if not na or not nb:
        return {'edit_distance': 0.0, 'phonetic_match': 0.0,
                'token_jaccard': 0.0, 'token_overlap_score': 0.0}

    # Levenshtein normalizado (via rapidfuzz si disponible, si no SequenceMatcher)
    try:
        from rapidfuzz import fuzz as _fuzz
        edit_score = _fuzz.ratio(na, nb) / 100.0
    except Exception:
        edit_score = SequenceMatcher(None, na, nb).ratio()

    # Fonética: Metaphone (jellyfish) o Soundex simplificado
    phonetic_score = 0.0
    if JELLYFISH_OK and jellyfish:
        try:
            # Compara el primer token (nombre de pila)
            tok_a = na.split()[0] if na.split() else na
            tok_b = nb.split()[0] if nb.split() else nb
            # Doble Metaphone
            dm_a = jellyfish.metaphone(tok_a)
            dm_b = jellyfish.metaphone(tok_b)
            if dm_a and dm_b:
                phonetic_score = 1.0 if dm_a == dm_b else SequenceMatcher(None, dm_a, dm_b).ratio()
        except Exception:
            phonetic_score = 0.0
    else:
        # Fallback: similitud del primer token
        toks_a = na.split(); toks_b = nb.split()
        if toks_a and toks_b:
            phonetic_score = SequenceMatcher(None, toks_a[0], toks_b[0]).ratio()

    # Jaccard de tokens
    set_a = set(na.split()); set_b = set(nb.split())
    if set_a | set_b:
        jaccard = len(set_a & set_b) / len(set_a | set_b)
    else:
        jaccard = 0.0

    # Token overlap ponderado: palabras comunes / min(tokens)
    min_len = min(len(set_a), len(set_b)) if set_a and set_b else 1
    overlap = len(set_a & set_b) / max(1, min_len)

    return {
        'edit_distance': round(edit_score, 4),
        'phonetic_match': round(phonetic_score, 4),
        'token_jaccard': round(jaccard, 4),
        'token_overlap_score': round(min(1.0, overlap), 4),
    }


# ── Feature 2: Plausibilidad temporal ────────────────────────────────────────

def calculate_temporal_probability(year_a: int | None, year_b: int | None) -> float:
    """
    P(misma persona viva en year_b | primera vista en year_a).
    Usa modelo de supervivencia exponencial basado en esperanza de vida histórica.
    """
    if year_a is None or year_b is None:
        return 0.5  # sin información: neutral

    gap = abs(int(year_b) - int(year_a))
    ref_year = min(int(year_a), int(year_b))
    life_exp = _life_expectancy_for_year(ref_year)

    # P(sobrevivir gap años) = exp(-gap / life_exp)
    # Si el gap > 2 * life_exp es prácticamente imposible
    prob = math.exp(-gap / life_exp)
    return round(max(0.0, min(1.0, prob)), 4)


# ── Feature 3: Plausibilidad geográfica ──────────────────────────────────────

def calculate_geographic_probability(dist_km: float | None, year_ref: int | None) -> float:
    """
    P(misma persona | distancia dist_km).
    Usa distribución exponencial con escala según época.
    Si dist_km es None (sin geocoding), retorna 0.5 (neutral).
    """
    if dist_km is None:
        return 0.5

    year = year_ref or 1820
    params = _mobility_params_for_year(year)
    scale = params['scale']
    hard_limit = params['hard_limit_km']

    if dist_km > hard_limit:
        return 0.02   # prácticamente imposible para la época

    # Distribución exponencial: P = exp(-dist / scale)
    prob = math.exp(-dist_km / scale)
    return round(max(0.0, min(1.0, prob)), 4)


# ── Feature 4: Contexto social ───────────────────────────────────────────────

def calculate_social_context_score(events_a: list[dict], events_b: list[dict]) -> dict:
    """
    Mide overlap en contexto social entre dos grupos de eventos de testigos.
    events_a / events_b: listas de dicts con campos subj_name, place_name, note, witness_raw.

    Devuelve dict: {
        'shared_family_surnames': int,
        'shared_places': int,
        'social_class_match': bool | None,
        'social_class_a': str | None,
        'social_class_b': str | None,
        'combined_score': float
    }
    """
    # Apellidos de familias apadrinadas
    def _surnames(evs):
        snames = set()
        for e in evs:
            sn = extract_surname_improved(str(e.get('subj_name', '')))
            if sn:
                snames.add(normalize(sn))
        return snames

    sn_a = _surnames(events_a)
    sn_b = _surnames(events_b)
    shared_fam = len(sn_a & sn_b)

    # Lugares compartidos
    places_a = {normalize(str(e.get('place_name', ''))) for e in events_a if e.get('place_name')}
    places_b = {normalize(str(e.get('place_name', ''))) for e in events_b if e.get('place_name')}
    shared_pl = len(places_a & places_b)

    # Clase social: desde nota o desde nombre propio
    def _class_for_events(evs):
        # Prioridad 1: desde notas
        for e in evs:
            c = _extract_social_class_from_note(str(e.get('note', '')))
            if c:
                return c
        # Prioridad 2: desde nombre del testigo
        for e in evs:
            c = _extract_social_class_from_name(str(e.get('witness_raw', '')))
            if c:
                return c
        return None

    cls_a = _class_for_events(events_a)
    cls_b = _class_for_events(events_b)

    if cls_a and cls_b:
        class_match = (cls_a == cls_b)
    else:
        class_match = None  # sin información

    # Score combinado 0-1
    # Familias compartidas contribuyen hasta 0.5, lugares hasta 0.3, clase hasta 0.2
    total_fam = max(1, len(sn_a | sn_b))
    total_pl = max(1, len(places_a | places_b))

    fam_score = min(1.0, shared_fam / max(1, min(3, total_fam))) * 0.5
    pl_score = min(1.0, shared_pl / max(1, min(3, total_pl))) * 0.3
    if class_match is True:
        cls_score = 0.2
    elif class_match is False:
        cls_score = -0.1
    else:
        cls_score = 0.0

    combined = round(max(0.0, min(1.0, fam_score + pl_score + cls_score)), 4)

    return {
        'shared_family_surnames': shared_fam,
        'shared_places': shared_pl,
        'social_class_match': class_match,
        'social_class_a': cls_a,
        'social_class_b': cls_b,
        'combined_score': combined,
    }


# ── Función principal bayesiana ───────────────────────────────────────────────

def _calculate_prior(name_norm: str, total_witnesses: int, name_freq: float) -> float:
    """Prior P(misma persona) ajustado por frecuencia del nombre."""
    base = 0.05
    # Nombres raros -> prior más alto; nombres comunes -> prior más bajo
    freq_factor = 1.0 / (1.0 + name_freq * 10)
    return round(max(0.005, min(0.3, base * freq_factor)), 6)


def bayesian_identity_probability(
    events_a: list[dict],
    events_b: list[dict],
    name_a: str,
    name_b: str,
    prior: float = 0.05,
    places_index: dict | None = None,
) -> dict:
    """
    Calcula P(misma persona | toda la evidencia) usando Naive Bayes.

    Args:
        events_a: lista de eventos del testigo A (cada dict tiene date_iso, place_name, etc.)
        events_b: lista de eventos del testigo B
        name_a: nombre canónico de A
        name_b: nombre canónico de B
        prior: probabilidad a priori de ser la misma persona
        places_index: índice de lugares (para extraer lat/lon)

    Returns:
        dict con probability_same_person, recommendation, feature_contributions, explanation
    """
    # ── Extraer años representativos ──
    import re as _re_b
    def _year(evs):
        years = []
        for e in evs:
            m = _re_b.match(r'^(\d{4})', str(e.get('date_iso', '') or ''))
            if m:
                years.append(int(m.group(1)))
        return int(sum(years) / len(years)) if years else None

    year_a = _year(events_a)
    year_b = _year(events_b)

    # ── Distancia geográfica media ──
    def _avg_coords(evs, pidx):
        lats, lons = [], []
        for e in evs:
            # Intentar desde el evento directamente
            lat = e.get('lat'); lon = e.get('lon')
            if lat and lon:
                try:
                    lats.append(float(lat)); lons.append(float(lon))
                    continue
                except Exception:
                    pass
            # Intentar desde places_index
            if pidx:
                pname = normalize(str(e.get('place_name', '')))
                for pdata in pidx.values():
                    if normalize(str(pdata.get('name', ''))) == pname:
                        try:
                            lats.append(float(pdata['lat'])); lons.append(float(pdata['lon']))
                        except Exception:
                            pass
                        break
        if lats and lons:
            return sum(lats)/len(lats), sum(lons)/len(lons)
        return None, None

    pidx = places_index or {}
    lat_a, lon_a = _avg_coords(events_a, pidx)
    lat_b, lon_b = _avg_coords(events_b, pidx)

    if lat_a is not None and lat_b is not None:
        dist_km = haversine_km(lat_a, lon_a, lat_b, lon_b)
    else:
        dist_km = None

    # ── Calcular features ──
    name_feats = calculate_name_similarity_features(name_a, name_b)
    temp_prob = calculate_temporal_probability(year_a, year_b)
    geo_prob = calculate_geographic_probability(dist_km, year_a or year_b)
    social = calculate_social_context_score(events_a, events_b)

    # ── Pesos del modelo (según plan: nombre 40%, temporal 25%, geo 20%, social 15%) ──
    W_NAME = 0.40
    W_TEMP = 0.25
    W_GEO  = 0.20
    W_SOC  = 0.15

    # Score de nombre: promedio ponderado de sub-métricas (edit 40%, phonetic 35%, jaccard 25%)
    name_score = (
        name_feats['edit_distance']       * 0.40 +
        name_feats['phonetic_match']      * 0.35 +
        name_feats['token_jaccard']       * 0.25
    )

    # ── Naive Bayes: likelihood ratio para cada feature ──
    # P(feature | mismo) / P(feature | diferente) -> ratio de verosimilitud
    # Simplificación: usamos los scores directamente como likelihood ratio relativo
    # L(same)/L(diff) para nombre: high name_score -> ratio > 1
    # Usamos función sigmoide centrada en 0.5 para mapear scores a likelihood ratios

    def _lr(score, sensitivity=4.0):
        """Convierte un score 0-1 en un likelihood ratio. Score=0.5 -> LR=1 (neutral)."""
        # LR = exp(sensitivity * (score - 0.5))
        return math.exp(sensitivity * (score - 0.5))

    # Likelihood ratios
    lr_name = _lr(name_score, sensitivity=5.0)
    lr_temp = _lr(temp_prob, sensitivity=3.0)
    lr_geo  = _lr(geo_prob,  sensitivity=3.0)
    lr_soc  = _lr(social['combined_score'], sensitivity=2.0)

    # Posterior via Bayes rule con likelihood ratios
    # Odds posterior = Odds prior × LR1 × LR2 × ...
    prior_odds = prior / (1 - prior)
    posterior_odds = prior_odds * lr_name * lr_temp * lr_geo * lr_soc
    posterior_prob = posterior_odds / (1 + posterior_odds)
    posterior_prob = round(max(0.001, min(0.999, posterior_prob)), 4)

    # ── Contribuciones normalizadas (para la UI de barras) ──
    total_lr_log = abs(math.log(lr_name)) + abs(math.log(lr_temp)) + abs(math.log(lr_geo)) + abs(math.log(lr_soc)) + 1e-9
    contributions = {
        'nombre': round(abs(math.log(lr_name)) / total_lr_log, 3),
        'temporal': round(abs(math.log(lr_temp)) / total_lr_log, 3),
        'geografico': round(abs(math.log(lr_geo)) / total_lr_log, 3),
        'social': round(abs(math.log(lr_soc)) / total_lr_log, 3),
    }

    # ── Recomendación ──
    if posterior_prob >= 0.95:
        recommendation = 'auto_merge'
    elif posterior_prob >= 0.70:
        recommendation = 'review'
    else:
        recommendation = 'different'

    # ── Explicación en texto ──
    parts = []
    if name_score >= 0.85:
        parts.append(f"nombre muy similar ({int(name_score*100)}%)")
    elif name_score >= 0.60:
        parts.append(f"nombre moderadamente similar ({int(name_score*100)}%)")
    else:
        parts.append(f"nombre poco similar ({int(name_score*100)}%)")

    if year_a and year_b:
        gap = abs(year_b - year_a)
        if temp_prob >= 0.7:
            parts.append(f"brecha temporal plausible ({gap} años)")
        elif temp_prob >= 0.3:
            parts.append(f"brecha temporal posible ({gap} años)")
        else:
            parts.append(f"brecha temporal improbable ({gap} años)")

    if dist_km is not None:
        if geo_prob >= 0.7:
            parts.append(f"distancia cercana ({dist_km:.0f} km)")
        elif geo_prob >= 0.3:
            parts.append(f"distancia moderada ({dist_km:.0f} km)")
        else:
            parts.append(f"distancia grande ({dist_km:.0f} km)")
    else:
        parts.append("sin datos de distancia")

    if social['shared_family_surnames'] > 0:
        parts.append(f"{social['shared_family_surnames']} familia(s) en común")
    if social['social_class_match'] is True:
        parts.append("clase social coincidente")
    elif social['social_class_match'] is False:
        parts.append("clase social diferente")

    explanation = "; ".join(parts)

    return {
        'name_a': name_a,
        'name_b': name_b,
        'year_a': year_a,
        'year_b': year_b,
        'dist_km': round(dist_km, 1) if dist_km is not None else None,
        'probability_same_person': posterior_prob,
        'prior': prior,
        'name_score': round(name_score, 4),
        'temp_prob': temp_prob,
        'geo_prob': geo_prob,
        'social_combined': social['combined_score'],
        'social_detail': social,
        'name_features': name_feats,
        'feature_contributions': contributions,
        'recommendation': recommendation,
        'explanation': explanation,
    }


# ── Grafo de probabilidades y resolución de clusters ─────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def build_identity_probability_graph(
    _df_in,
    _places_index: dict,
    min_name_sim: int = 60,
    prior: float = 0.05,
) -> dict:
    """
    Construye grafo de probabilidades bayesianas entre testigos con nombre similar.
    Solo compara pares con similitud nominal >= min_name_sim para reducir complejidad.

    Returns:
        dict: {(norm_a, raw_a): {(norm_b, raw_b): result_dict}}
    """
    df_work = _df_in.copy()
    col = 'witness_canon' if 'witness_canon' in df_work.columns else 'witness_raw'
    if col not in df_work.columns:
        return {}

    # Índice: raw_name -> lista de eventos
    witness_events: dict[str, list[dict]] = defaultdict(list)
    for _, row in df_work.iterrows():
        raw = str(row.get(col, '') or '')
        if not raw or raw in ('nan', 'None'):
            continue
        witness_events[raw].append(row.to_dict())

    unique_raws = sorted(witness_events.keys())
    if len(unique_raws) < 2:
        return {}

    # Frecuencias para el prior
    total_w = len(unique_raws)
    freq_map = {r: len(witness_events[r]) / max(1, sum(len(v) for v in witness_events.values()))
                for r in unique_raws}

    # Bucketing por primeras letras (mismo que build_similarity_clusters)
    buckets: dict[tuple, list] = defaultdict(list)
    for r in unique_raws:
        key = (normalize(r)[:2], min(50, max(0, len(r) // 3)))
        buckets[key].append(r)

    graph: dict[tuple, dict] = {}
    for bucket_raws in buckets.values():
        if len(bucket_raws) < 2:
            continue
        for i in range(len(bucket_raws)):
            for j in range(i + 1, len(bucket_raws)):
                ra = bucket_raws[i]; rb = bucket_raws[j]
                sim = name_similarity(normalize(ra), normalize(rb))
                if sim < min_name_sim:
                    continue

                evs_a = witness_events[ra]
                evs_b = witness_events[rb]
                dyn_prior = _calculate_prior(normalize(ra), total_w, freq_map.get(ra, 0.01))
                result = bayesian_identity_probability(
                    evs_a, evs_b, ra, rb,
                    prior=dyn_prior,
                    places_index=_places_index,
                )
                key_a = ra; key_b = rb
                graph.setdefault(key_a, {})[key_b] = result
                graph.setdefault(key_b, {})[key_a] = result

    return graph


def resolve_identity_clusters(
    identity_graph: dict,
    high_threshold: float = 0.95,
    review_threshold: float = 0.70,
) -> dict:
    """
    Clasifica pares en tres categorías según su probabilidad.

    Returns:
        dict con claves 'auto_merge', 'needs_review', 'different'
        Cada valor es lista de dicts con info del par.
    """
    auto_merge = []
    needs_review = []
    different = []

    seen_pairs = set()
    for raw_a, neighbors in identity_graph.items():
        for raw_b, result in neighbors.items():
            pair_key = tuple(sorted([raw_a, raw_b]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            prob = result['probability_same_person']
            entry = {
                'name_a': raw_a,
                'name_b': raw_b,
                'probability': prob,
                'recommendation': result['recommendation'],
                'explanation': result['explanation'],
                'year_a': result.get('year_a'),
                'year_b': result.get('year_b'),
                'dist_km': result.get('dist_km'),
                'name_score': result.get('name_score'),
                'full_result': result,
            }
            if prob >= high_threshold:
                auto_merge.append(entry)
            elif prob >= review_threshold:
                needs_review.append(entry)
            else:
                different.append(entry)

    # Ordenar por probabilidad desc
    auto_merge.sort(key=lambda x: -x['probability'])
    needs_review.sort(key=lambda x: -x['probability'])
    different.sort(key=lambda x: -x['probability'])

    return {
        'auto_merge': auto_merge,
        'needs_review': needs_review,
        'different': different,
    }


# ── Página UI Fase 5 ──────────────────────────────────────────────────────────

def page_bayesian_identidad(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    st.header(t("hdr_bayesiana"))
    st.markdown(
        "Calcula la probabilidad de que dos registros de testigos con nombre similar "
        "correspondan a la **misma persona**, considerando nombre, época, distancia y contexto social."
    )

    # ── Parámetros ──
    with st.expander(t("timeline_params"), expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            min_sim = st.slider(t("bayes_min_sim"), 40, 95, 60,
                                help="Solo se comparan pares con esta similitud mínima")
        with c2:
            high_thr = st.slider(t("bayes_umbral_merge"), 80, 99, 95,
                                 help="Por encima de este umbral se sugiere fusión automática") / 100.0
        with c3:
            rev_thr = st.slider(t("bayes_umbral_rev"), 40, 85, 70,
                                help="Entre umbral revisión y auto-merge: requiere revisión manual") / 100.0

        if rev_thr >= high_thr:
            st.warning(t("confirmar_umbral_warning"))
            rev_thr = high_thr - 0.05

    # ── Construir grafo ──
    st.info(t("timeline_error_bayes"))
    with st.spinner(t("bayes_analizando")):
        pidx = places_index
        try:
            identity_graph = build_identity_probability_graph(df, pidx, min_name_sim=min_sim)
        except Exception as e:
            st.error(t("timeline_error_grafo_id", e=e))
            return

    if not identity_graph:
        st.warning(t("confirmar_no_pares"))
        return

    clusters_result = resolve_identity_clusters(identity_graph, high_thr, rev_thr)
    n_merge = len(clusters_result['auto_merge'])
    n_review = len(clusters_result['needs_review'])
    n_diff = len(clusters_result['different'])

    # ── Métricas resumen ──
    st.markdown("---")
    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric(t("bayes_auto_merge"), n_merge, help=f"Prob ≥ {high_thr*100:.0f}%")
    col_m2.metric(t("bayes_revision"), n_review, help=f"Prob {rev_thr*100:.0f}%–{high_thr*100:.0f}%")
    col_m3.metric(t("bayes_diferentes"), n_diff, help=f"Prob < {rev_thr*100:.0f}%")

    # ── Modo de visualización ──
    st.markdown("---")
    view_mode = st.radio(
        t("bayes_mostrar"),
        [t("bayes_alta_prob"), t("bayes_dudosos"), t("bayes_dif_label"), t("bayes_todos")],
        horizontal=True,
    )

    if view_mode == t("bayes_alta_prob"):
        pairs_to_show = clusters_result['auto_merge']
        mode_label = "auto-merge"
    elif view_mode == t("bayes_dudosos"):
        pairs_to_show = clusters_result['needs_review']
        mode_label = "revisión"
    elif view_mode == t("bayes_dif_label"):
        pairs_to_show = clusters_result['different']
        mode_label = "diferentes"
    else:
        pairs_to_show = (clusters_result['auto_merge'] +
                         clusters_result['needs_review'] +
                         clusters_result['different'])
        pairs_to_show.sort(key=lambda x: -x['probability'])
        mode_label = "todos"

    if not pairs_to_show:
        st.info(t("pendientes_no_pares_cat", mode_label=mode_label))
        return

    st.markdown(t("bayes_n_pares_cat", n=len(pairs_to_show), cat=mode_label))
    max_pairs = st.number_input(t("label_mostrar_n_pares"), 1, 500, 20, key="bay_max_pairs")
    pairs_to_show = pairs_to_show[:max_pairs]

    # ── Carga confirmaciones actuales para botones ──
    conf_bay = load_confirmations()

    for pi, pair in enumerate(pairs_to_show, start=1):
        prob = pair['probability']
        pct = int(prob * 100)
        bar_filled = "█" * (pct // 5)
        bar_empty = "░" * (20 - pct // 5)
        bar_str = bar_filled + bar_empty

        color_icon = "🟢" if prob >= high_thr else ("🟡" if prob >= rev_thr else "🔴")
        label = f"{color_icon} Par {pi}: {pair['name_a'][:30]} vs {pair['name_b'][:30]} — {pct}%"

        with st.expander(label, expanded=(pi <= 3)):
            # ── Tarjeta principal ──
            st.markdown(f"""
<div style="background:#1e1e2e;padding:14px;border-radius:8px;font-family:monospace;">
<b style="font-size:1.1em">Probabilidad: {pct}% {bar_str}</b><br><br>
<span style="color:#aef">{pair['name_a']}</span> &nbsp;({pair.get('year_a') or '?'})<br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>vs</b><br>
<span style="color:#fae">{pair['name_b']}</span> &nbsp;({pair.get('year_b') or '?'})<br>
</div>
""", unsafe_allow_html=True)

            st.markdown(f"{t('bayes_explicacion')} {pair['explanation']}")

            # ── Detalle de features ──
            fr = pair['full_result']
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Similitud nombre", f"{int(fr.get('name_score', 0)*100)}%")
            c2.metric("Plaus. temporal", f"{int(fr.get('temp_prob', 0)*100)}%")
            c3.metric("Plaus. geográfica",
                      f"{int(fr.get('geo_prob', 0)*100)}%" +
                      (f" ({fr['dist_km']:.0f} km)" if fr.get('dist_km') is not None else " (sin datos)"))
            c4.metric("Contexto social", f"{int(fr.get('social_combined', 0)*100)}%")

            # Sub-métricas de nombre
            st.markdown(f"**{t('timeline_detalle_nom')}**")
            nf = fr.get('name_features', {})
            cols_nf = st.columns(4)
            cols_nf[0].metric("Edit distance", f"{int(nf.get('edit_distance',0)*100)}%")
            cols_nf[1].metric("Fonético", f"{int(nf.get('phonetic_match',0)*100)}%")
            cols_nf[2].metric("Jaccard tokens", f"{int(nf.get('token_jaccard',0)*100)}%")
            cols_nf[3].metric("Overlap tokens", f"{int(nf.get('token_overlap_score',0)*100)}%")

            # Detalle social
            sd = fr.get('social_detail', {})
            if sd.get('shared_family_surnames', 0) > 0 or sd.get('social_class_a') or sd.get('social_class_b'):
                st.markdown(f"**{t('timeline_detalle_ctx')}**")
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric(t("bayes_familias_comun"), sd.get('shared_family_surnames', 0))
                sc2.metric(t("bayes_lugares_comun"), sd.get('shared_places', 0))
                cls_txt = (f"{sd.get('social_class_a','?')} / {sd.get('social_class_b','?')}"
                           if sd.get('social_class_a') or sd.get('social_class_b')
                           else "sin datos")
                sc3.metric(t("bayes_clase_social"), cls_txt)

            # Gráfico de contribuciones
            if PLOTLY_OK:
                contribs = fr.get('feature_contributions', {})
                if contribs:
                    import plotly.graph_objects as _go
                    fig_c = _go.Figure(_go.Bar(
                        x=list(contribs.keys()),
                        y=[v * 100 for v in contribs.values()],
                        marker_color=['steelblue', 'coral', 'mediumseagreen', 'mediumpurple'],
                        text=[f"{v*100:.1f}%" for v in contribs.values()],
                        textposition='outside',
                    ))
                    fig_c.update_layout(
                        title=t("bayes_factor_titulo"),
                        yaxis_title=t("bayes_factor_y"),
                        height=260,
                        margin=dict(l=20, r=20, t=40, b=20),
                    )
                    st.plotly_chart(fig_c, use_container_width=True)

            # ── Botones de acción ──
            st.markdown("---")
            ba1, ba2, ba3 = st.columns(3)
            btn_merge_key = f"bay_merge_{pi}_{pair['name_a'][:10]}_{pair['name_b'][:10]}"
            btn_diff_key  = f"bay_diff_{pi}_{pair['name_a'][:10]}_{pair['name_b'][:10]}"
            btn_skip_key  = f"bay_skip_{pi}_{pair['name_a'][:10]}_{pair['name_b'][:10]}"

            with ba1:
                if st.button(t("confirmar_misma_persona"), key=btn_merge_key):
                    conf_bay = load_confirmations()
                    # Buscar todos los eventos de ambos testigos y crear event_group
                    col_ev = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
                    evs_a_ids = df[df[col_ev].astype(str) == pair['name_a']]['event_id'].astype(str).tolist()
                    evs_b_ids = df[df[col_ev].astype(str) == pair['name_b']]['event_id'].astype(str).tolist()
                    all_ids = list(set(evs_a_ids + evs_b_ids))
                    if all_ids:
                        gid = str(uuid.uuid4())
                        conf_bay.setdefault('event_groups', {})[gid] = all_ids
                        for eid in all_ids:
                            conf_bay.setdefault('status', {})[eid] = {
                                'state': 'same',
                                'timestamp': datetime.now(_tz.utc).isoformat(),
                                'user': USER,
                            }
                        save_confirmations(conf_bay)
                        apply_event_confirmations_and_rebuild_witness_canon()
                        st.success(t("confirmar_fusionados2", n=len(all_ids)))
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning(t("bayes_no_eventos"))

            with ba2:
                if st.button(t("confirmar_personas_diferentes"), key=btn_diff_key):
                    conf_bay = load_confirmations()
                    col_ev = 'witness_canon' if 'witness_canon' in df.columns else 'witness_raw'
                    evs_a_ids = df[df[col_ev].astype(str) == pair['name_a']]['event_id'].astype(str).tolist()
                    evs_b_ids = df[df[col_ev].astype(str) == pair['name_b']]['event_id'].astype(str).tolist()
                    all_ids = list(set(evs_a_ids + evs_b_ids))
                    dif = conf_bay.get('different', [])
                    for eid_a in evs_a_ids:
                        for eid_b in evs_b_ids:
                            p = [str(eid_a), str(eid_b)]
                            if p not in dif and [p[1], p[0]] not in dif:
                                dif.append(p)
                        conf_bay.setdefault('status', {})[str(eid_a)] = {
                            'state': 'different',
                            'timestamp': datetime.now(_tz.utc).isoformat(),
                            'user': USER,
                        }
                    for eid_b in evs_b_ids:
                        conf_bay.setdefault('status', {})[str(eid_b)] = {
                            'state': 'different',
                            'timestamp': datetime.now(_tz.utc).isoformat(),
                            'user': USER,
                        }
                    conf_bay['different'] = dif
                    save_confirmations(conf_bay)
                    compute_endogamy_stats.clear()
                    compute_bridge_families.clear()
                    st.success(t("confirmar_par_diferentes"))
                    st.rerun()

            with ba3:
                if st.button(t("confirmar_omitir"), key=btn_skip_key):
                    st.info(t("bayes_omitido"))

    # ── Tabla resumen exportable ──
    st.markdown("---")
    with st.expander(t("timeline_tabla_pares")):
        all_pairs = (clusters_result['auto_merge'] +
                     clusters_result['needs_review'] +
                     clusters_result['different'])
        if all_pairs:
            summary_df = pd.DataFrame([{
                'Testigo A': p['name_a'],
                'Testigo B': p['name_b'],
                'Probabilidad (%)': int(p['probability'] * 100),
                'Recomendación': p['recommendation'],
                'Año A': p.get('year_a') or '',
                'Año B': p.get('year_b') or '',
                'Distancia km': p.get('dist_km') or '',
                'Similitud nombre (%)': int((p.get('full_result', {}).get('name_score', 0) or 0) * 100),
                'Explicación': p.get('explanation', ''),
            } for p in all_pairs])
            st.dataframe(summary_df.sort_values('Probabilidad (%)', ascending=False),
                         use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de exportación HTML/PDF
# ─────────────────────────────────────────────────────────────────────────────

def generate_witness_html_report(
    witness_name: str,
    events_df,
    stats_dict: dict,
    folium_map_html=None,
    plotly_chart_html=None,
    title=None,
) -> str:
    """Genera un informe HTML auto-contenido para un testigo."""
    from datetime import datetime as _dt_html, timezone as _tz_html
    title = title or t("informe_html_titulo_testigo", name=witness_name)
    report_date = _dt_html.now(_tz_html.utc).strftime("%Y-%m-%d %H:%M UTC")

    stats_rows = "\n".join(
        f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
        for k, v in stats_dict.items()
        if not k.startswith('_')
    )

    display_cols = [t("notas_col_fecha"), t("explorar_tipo_evento"), t("notas_col_lugar"), t("notas_col_sujeto"), t("notas_col_nota")]
    actual_cols = [c for c in display_cols if c in events_df.columns]
    events_rows = ""
    for _, row in events_df.iterrows():
        cells = "".join(f"<td>{str(row.get(c, ''))}</td>" for c in actual_cols)
        events_rows += f"<tr>{cells}</tr>\n"
    header_cells = "".join(
        f'<th onclick="sortTable({i})">{c}</th>' for i, c in enumerate(actual_cols)
    )

    map_section = ""
    if folium_map_html:
        map_section = (
            f"<h2>{t('sub_tray_geografica')}</h2>"
            "<div style='width:100%;height:520px;overflow:hidden;border:1px solid #ccc;'>"
            + folium_map_html
            + "</div>"
        )

    chart_section = f"<h2>{t('sub_actividad_anio')}</h2>{plotly_chart_html}" if plotly_chart_html else ""

    sort_js = """
<script>
function sortTable(n) {
  var table = document.getElementById("evtTable");
  var rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;
  switching = true; dir = "asc";
  while (switching) {
    switching = false; rows = table.rows;
    for (i = 1; i < rows.length - 1; i++) {
      shouldSwitch = false;
      x = rows[i].getElementsByTagName("TD")[n];
      y = rows[i+1].getElementsByTagName("TD")[n];
      var cmp = dir == "asc"
        ? x.innerHTML.toLowerCase() > y.innerHTML.toLowerCase()
        : x.innerHTML.toLowerCase() < y.innerHTML.toLowerCase();
      if (cmp) { shouldSwitch = true; break; }
    }
    if (shouldSwitch) {
      rows[i].parentNode.insertBefore(rows[i+1], rows[i]);
      switching = true; switchcount++;
    } else if (switchcount == 0 && dir == "asc") { dir = "desc"; switching = true; }
  }
}
</script>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ font-family: Georgia, serif; margin: 2em; color: #222; background: #fff; }}
    h1 {{ color: #4a0e0e; border-bottom: 2px solid #4a0e0e; padding-bottom: .3em; }}
    h2 {{ color: #333; margin-top: 2em; border-bottom: 1px solid #ddd; padding-bottom: .2em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .92em; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
    th {{ background: #f0e6d3; cursor: pointer; user-select: none; }}
    th:hover {{ background: #e0d0b0; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    .stats-table td {{ width: 50%; }}
    .footer {{ color: #888; font-size: .85em; margin-top: 3em; border-top: 1px solid #ccc; padding-top: 1em; }}
  </style>
  {sort_js}
</head>
<body>
  <h1>{title}</h1>
  <p style="color:#666;">{t("informe_html_generado", date=report_date)}</p>

  <h2>{t("sub_perfil_testigo")}</h2>
  <table class="stats-table"><tbody>{stats_rows}</tbody></table>

  {map_section}

  {chart_section}

  <h2>{t("menu_explorar")} ({len(events_df)})</h2>
  <table id="evtTable">
    <thead><tr>{header_cells}</tr></thead>
    <tbody>{events_rows}</tbody>
  </table>

  <div class="footer">Genealogía Testigos &mdash; {report_date}</div>
</body>
</html>"""
    return html


def generate_family_html_report(
    family_surname: str,
    events_df,
    stats_dict: dict,
    folium_map_html=None,
    endogamy_dict=None,
    plotly_chart_html=None,
    title=None,
) -> str:
    """
    Genera informe HTML auto-contenido para una familia (apellido de sujeto).
    Mismo estilo que generate_witness_html_report.
    """
    import datetime as _dt
    report_date = _dt.date.today().isoformat()
    title = title or t("informe_html_titulo_familia", name=family_surname)

    # Estadísticas
    stats_rows = ""
    for k, v in stats_dict.items():
        stats_rows += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>\n"

    # Tabla de eventos
    events_rows = ""
    if events_df is not None and not events_df.empty:
        for _, row in events_df.iterrows():
            events_rows += (
                f"<tr>"
                f"<td>{row.get('date_iso','')}</td>"
                f"<td>{row.get('type','')}</td>"
                f"<td>{row.get('place_name','')}</td>"
                f"<td>{row.get('subj_name','')}</td>"
                f"<td>{row.get('witness_canon') or row.get('witness_raw','')}</td>"
                f"<td>{str(row.get('note',''))[:120]}</td>"
                f"</tr>\n"
            )

    # Sección endogamia
    endo_section = ""
    if endogamy_dict:
        endo_section = f"""
        <h2>Análisis de endogamia</h2>
        <table><thead><tr><th>Métrica</th><th>Valor</th></tr></thead><tbody>
        {"".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in endogamy_dict.items())}
        </tbody></table>
        """

    # Sección mapa
    map_section = ""
    if folium_map_html:
        map_section = f"<h2>Mapa geográfico</h2><div style='width:100%;height:450px'>{folium_map_html}</div>"

    # Sección gráfico
    chart_section = ""
    if plotly_chart_html:
        chart_section = f"<h2>Actividad temporal</h2>{plotly_chart_html}"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{font-family: Georgia, serif; max-width: 1100px; margin: 0 auto; padding: 20px; color: #222;}}
    h1 {{color: #4a3728; border-bottom: 2px solid #c8a96e; padding-bottom: 8px;}}
    h2 {{color: #6b4c2a; margin-top: 32px;}}
    table {{border-collapse: collapse; width: 100%; margin-bottom: 24px;}}
    th {{background: #f0e6d3; color: #4a3728; padding: 8px; text-align: left; border: 1px solid #d4b896;}}
    td {{padding: 6px 8px; border: 1px solid #e0cdb5; vertical-align: top;}}
    tr:nth-child(even) {{background: #faf5ef;}}
    .footer {{margin-top: 40px; color: #999; font-size: 12px; border-top: 1px solid #ddd; padding-top: 8px;}}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <h2>{t("informe_html_estadisticas")}</h2>
  <table><thead><tr><th>{t("informe_html_metrica")}</th><th>{t("informe_html_valor")}</th></tr></thead><tbody>
  {stats_rows}
  </tbody></table>
  {map_section}
  {chart_section}
  <h2>{t("informe_html_eventos")}</h2>
  <table>
    <thead><tr><th>{t("informe_html_col_fecha")}</th><th>{t("informe_html_col_tipo")}</th><th>{t("informe_html_col_lugar")}</th><th>{t("informe_html_col_sujeto")}</th><th>{t("informe_html_col_testigo")}</th><th>{t("informe_html_col_nota")}</th></tr></thead>
    <tbody>{events_rows}</tbody>
  </table>
  {endo_section}
  <div class="footer">{t("informe_html_footer", date=report_date)}</div>
</body>
</html>"""
    return html


def generate_network_html_report(_df, _by_witness: dict, _places_index: dict, top_n: int = 20) -> str:
    """
    Genera informe HTML auto-contenido con estadísticas generales de la red.
    """
    import datetime as _dt
    report_date = _dt.date.today().isoformat()

    total_events = len(_df) if _df is not None else 0
    unique_witnesses = len(_by_witness)
    unique_places = int(_df['place_name'].nunique()) if _df is not None and 'place_name' in _df.columns else 0

    date_min, date_max = '', ''
    if _df is not None and 'date_iso' in _df.columns:
        dates = _df['date_iso'].dropna().sort_values()
        if not dates.empty:
            date_min, date_max = str(dates.iloc[0])[:10], str(dates.iloc[-1])[:10]

    # Top testigos por apariciones
    top_wit = sorted(_by_witness.items(), key=lambda x: len(x[1]), reverse=True)[:top_n]
    top_rows = ""
    for w, evts in top_wit:
        top_rows += f"<tr><td>{w}</td><td>{len(evts)}</td></tr>\n"

    # Top lugares
    place_rows = ""
    if _df is not None and 'place_name' in _df.columns:
        for place, cnt in _df['place_name'].value_counts().head(top_n).items():
            place_rows += f"<tr><td>{place}</td><td>{cnt}</td></tr>\n"

    # Timeline por año
    timeline_rows = ""
    if _df is not None and 'date_iso' in _df.columns:
        _df_t = _df.copy()
        _df_t['year'] = _df_t['date_iso'].astype(str).str[:4]
        _df_t = _df_t[_df_t['year'].str.match(r'^\d{4}$')]
        for year, cnt in _df_t.groupby('year').size().items():
            timeline_rows += f"<tr><td>{year}</td><td>{cnt}</td></tr>\n"

    # Top familias puente
    bridge_section = ""
    try:
        br_df = compute_bridge_families(_df)
        if not br_df.empty:
            br_rows = ""
            for _, row in br_df.head(top_n).iterrows():
                br_rows += (
                    f"<tr><td>{row['label']}</td>"
                    f"<td>{row['bridge_index']}</td>"
                    f"<td>{row['n_padrinos']}</td>"
                    f"<td>{row['apellidos_padrinos']}</td></tr>\n"
                )
            bridge_section = f"""
            <h2>Top familias puente</h2>
            <table>
              <thead><tr><th>Familia</th><th>Índice puente</th><th>Padrinos distintos</th><th>Apellidos padrinos</th></tr></thead>
              <tbody>{br_rows}</tbody>
            </table>"""
    except Exception:
        pass

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Informe de red — Genealogía Testigos</title>
  <style>
    body {{font-family: Georgia, serif; max-width: 1100px; margin: 0 auto; padding: 20px; color: #222;}}
    h1 {{color: #4a3728; border-bottom: 2px solid #c8a96e; padding-bottom: 8px;}}
    h2 {{color: #6b4c2a; margin-top: 32px;}}
    table {{border-collapse: collapse; width: 100%; margin-bottom: 24px;}}
    th {{background: #f0e6d3; color: #4a3728; padding: 8px; text-align: left; border: 1px solid #d4b896;}}
    td {{padding: 6px 8px; border: 1px solid #e0cdb5; vertical-align: top;}}
    tr:nth-child(even) {{background: #faf5ef;}}
    .stat-grid {{display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 24px;}}
    .stat-box {{background: #f9f3ea; border: 1px solid #d4b896; border-radius: 8px; padding: 16px 24px; min-width: 140px;}}
    .stat-box .val {{font-size: 2em; font-weight: bold; color: #4a3728;}}
    .stat-box .lbl {{font-size: 0.85em; color: #888;}}
    .footer {{margin-top: 40px; color: #999; font-size: 12px; border-top: 1px solid #ddd; padding-top: 8px;}}
  </style>
</head>
<body>
  <h1>Informe de red — Genealogía Testigos</h1>
  <div class="stat-grid">
    <div class="stat-box"><div class="val">{total_events}</div><div class="lbl">Total eventos</div></div>
    <div class="stat-box"><div class="val">{unique_witnesses}</div><div class="lbl">Testigos únicos</div></div>
    <div class="stat-box"><div class="val">{unique_places}</div><div class="lbl">Lugares únicos</div></div>
    <div class="stat-box"><div class="val">{date_min}</div><div class="lbl">Primer evento</div></div>
    <div class="stat-box"><div class="val">{date_max}</div><div class="lbl">Último evento</div></div>
  </div>
  <h2>Top {top_n} testigos por apariciones</h2>
  <table><thead><tr><th>Testigo</th><th>Apariciones</th></tr></thead>
  <tbody>{top_rows}</tbody></table>
  <h2>Top {top_n} lugares</h2>
  <table><thead><tr><th>Lugar</th><th>Eventos</th></tr></thead>
  <tbody>{place_rows}</tbody></table>
  <h2>Actividad por año</h2>
  <table><thead><tr><th>Año</th><th>Eventos</th></tr></thead>
  <tbody>{timeline_rows}</tbody></table>
  {bridge_section}
  <div class="footer">Genealogía Testigos &mdash; {report_date}</div>
</body>
</html>"""
    return html


def try_export_pdf(html_str: str):
    """
    Intenta convertir HTML a PDF. Retorna (bytes, None) si tiene éxito,
    o (None, str) con el mensaje de error si falla.
    """
    try:
        import pdfkit
        return pdfkit.from_string(html_str, False), None
    except ImportError:
        pass
    except Exception as e:
        return None, f"pdfkit: {e}"
    try:
        from weasyprint import HTML as _WHTML
        return _WHTML(string=html_str).write_pdf(), None
    except ImportError:
        pass
    except Exception as e:
        return None, f"weasyprint: {e}"
    return None, None  # ninguna librería instalada


# ─────────────────────────────────────────────────────────────────────────────
# Nueva página: Trayectoria vital
# ─────────────────────────────────────────────────────────────────────────────

def page_trayectoria_vital(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    st.header(t("hdr_trayectoria"))

    all_witnesses = sorted(by_witness.keys())
    sel_wit = st.selectbox(
        t("super_sel_testigo"),
        [""] + all_witnesses,
        key="tray_wit_sel"
    )
    typed_wit = st.text_input(t("tray_escribe"), key="tray_wit_type")
    chosen = sel_wit if sel_wit else (normalize(typed_wit) if typed_wit else "")

    if not chosen:
        st.info(t("tray_selecciona"))
        return

    events = by_witness.get(chosen, [])
    if not events:
        st.warning(t("tray_no_eventos", chosen=chosen))
        return

    # DataFrame ordenado cronológicamente
    wit_df = pd.DataFrame(events).copy()
    wit_df['_dt'] = _parse_date_series(wit_df['date_iso'])
    wit_df['_yr'] = _year_series(wit_df['date_iso'])
    wit_df = wit_df.sort_values('_yr').reset_index(drop=True)

    # ── Métricas resumen ──────────────────────────────────────────────────────
    _tray_valid = [d for d in wit_df['_dt'] if d is not None and d is not pd.NaT]
    first_dt = min(_tray_valid) if _tray_valid else None
    last_dt  = max(_tray_valid) if _tray_valid else None
    active_years = (int(last_dt.year - first_dt.year)
                    if first_dt is not None and last_dt is not None else 0)
    unique_places_list = wit_df['place_name'].dropna().astype(str).unique().tolist()
    n_unique_places = len(unique_places_list)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(t("tray_metric_primer"), str(first_dt.date()) if first_dt is not None else "?")
    c2.metric(t("tray_metric_ultimo"), str(last_dt.date()) if last_dt is not None else "?")
    c3.metric(t("tray_metric_anos"), active_years)
    c4.metric(t("tray_metric_lugares"), n_unique_places)
    c5.metric(t("tray_metric_total"), len(events))

    # ── Tabla cronológica ─────────────────────────────────────────────────────
    st.subheader(t("sub_cronologia"))
    display_cols_tray = ['date_iso', 'type', 'place_name', 'subj_name', 'note']
    present_cols = [c for c in display_cols_tray if c in wit_df.columns]
    display_df = wit_df[present_cols].copy()
    display_df.columns = [
        {'date_iso': t("notas_col_fecha"), 'type': t("explorar_tipo_evento"),
         'place_name': t("notas_col_lugar"),
         'subj_name': t("notas_col_sujeto"), 'note': t("notas_col_nota")}.get(c, c)
        for c in present_cols
    ]
    st.dataframe(display_df, use_container_width=True)

    # ── Mapa de trayectoria ───────────────────────────────────────────────────
    st.subheader(t("sub_tray_geografica"))
    _map_html_tray = None
    coords_tray = []
    if folium is None:
        st.warning(t("err_folium_no3"))
    else:
        coords_tray = []
        for i, (_, row) in enumerate(wit_df.iterrows()):
            info = places_index.get(str(row.get('place_name', '')), {})
            lat = info.get('lat')
            lon = info.get('lon')
            if lat in (None, '') or lon in (None, ''):
                continue
            try:
                lat_f, lon_f = float(lat), float(lon)
            except (ValueError, TypeError):
                continue
            coords_tray.append({
                'seq': i + 1,
                'lat': lat_f, 'lon': lon_f,
                'place': str(row.get('place_name', '')),
                'date': str(row.get('date_iso', '')),
                'type': str(row.get('type', '')),
                'subj': str(row.get('subj_name', '')),
            })
        if coords_tray:
            m_tray = folium.Map(
                location=[coords_tray[0]['lat'], coords_tray[0]['lon']],
                zoom_start=9,
                tiles='CartoDB positron',
            )
            latlons = [(c['lat'], c['lon']) for c in coords_tray]
            folium.PolyLine(latlons, color='darkblue', weight=2.5, opacity=0.7).add_to(m_tray)
            for c in coords_tray:
                popup_html = (
                    f"<b>{c['seq']}. {c['place']}</b><br>"
                    f"Fecha: {c['date']}<br>Tipo: {c['type']}<br>Sujeto: {c['subj']}"
                )
                folium.Marker(
                    location=[c['lat'], c['lon']],
                    popup=folium.Popup(popup_html, max_width=260),
                    icon=folium.DivIcon(
                        html=(
                            f'<div style="background:#2c5f8a;color:white;border-radius:50%;'
                            f'width:22px;height:22px;text-align:center;line-height:22px;'
                            f'font-size:11px;font-weight:bold;">{c["seq"]}</div>'
                        ),
                        icon_size=(22, 22),
                        icon_anchor=(11, 11),
                    )
                ).add_to(m_tray)
            embed_folium(m_tray, width=1100, height=480)
            _map_html_tray = m_tray.get_root().render()
        else:
            st.info(t("tray_no_coords"))

    # ── Gráfico de actividad ──────────────────────────────────────────────────
    st.subheader(t("sub_actividad_anio"))
    _chart_html_tray = None
    if PLOTLY_OK:
        import plotly.express as _px_tray
        tray_tl = aggregate_timeline_by_period(wit_df, period='year')
        if not tray_tl.empty:
            # Rellenar años sin eventos para que el eje X sea continuo y las barras tengan anchura consistente
            _yr_min_tray = int(tray_tl['año'].min())
            _yr_max_tray = int(tray_tl['año'].max())
            if _yr_min_tray < _yr_max_tray:
                _all_years = pd.DataFrame({'año': range(_yr_min_tray, _yr_max_tray + 1)})
                _all_years['periodo'] = _all_years['año'].astype(str)
                tray_tl = _all_years.merge(tray_tl[['año', 'total_eventos']], on='año', how='left').fillna(0)
                tray_tl['total_eventos'] = tray_tl['total_eventos'].astype(int)

            _max_ev = int(tray_tl['total_eventos'].max())
            fig_tray = _px_tray.bar(
                tray_tl, x='periodo', y='total_eventos',
                title=t("tray_chart_titulo", wit=chosen),
                labels={'periodo': t("tray_chart_x"), 'total_eventos': t("tray_chart_y")},
                color_discrete_sequence=['#5b9bd5'],
            )
            fig_tray.update_layout(
                height=300,
                showlegend=False,
                coloraxis_showscale=False,
                xaxis=dict(
                    type='category',
                    tickangle=-45,
                    tickfont=dict(size=10),
                ),
                yaxis=dict(
                    dtick=1,
                    range=[0, max(_max_ev + 0.5, 2)],
                    title=t("tray_chart_y"),
                ),
                bargap=0.15,
            )
            st.plotly_chart(fig_tray, use_container_width=True)
            _chart_html_tray = fig_tray.to_html(include_plotlyjs='cdn', full_html=False)
    else:
        st.info(t("timeline_plotly_no"))

    # ── Familias apadrinadas ──────────────────────────────────────────────────
    st.subheader(t("sub_familias_apadrinadas"))
    if 'subj_name' in wit_df.columns:
        families = wit_df['subj_name'].dropna().astype(str)
        fam_counts = families.apply(extract_surname_improved).value_counts()
        if not fam_counts.empty:
            fam_df = fam_counts.reset_index()
            fam_df.columns = ['Apellido', 'Veces']
            st.dataframe(fam_df.head(30), use_container_width=True)

    # ── Rango geográfico ──────────────────────────────────────────────────────
    if coords_tray:
        max_dist_km = 0.0
        for ii in range(len(coords_tray)):
            for jj in range(ii + 1, len(coords_tray)):
                d = haversine_km(
                    coords_tray[ii]['lat'], coords_tray[ii]['lon'],
                    coords_tray[jj]['lat'], coords_tray[jj]['lon']
                )
                if d and d > max_dist_km:
                    max_dist_km = d
        st.subheader(t("sub_rango_geografico"))
        stab_tray = stability_mobility_stats({chosen: events}, places_index)
        if not stab_tray.empty:
            row_s = stab_tray.iloc[0]
            col_g1, col_g2, col_g3 = st.columns(3)
            col_g1.metric(t("tray_dist_consecutiva"), f"{row_s['avg_km']:.1f} km")
            col_g2.metric(t("tray_max_dist"), f"{max_dist_km:.1f} km")
            col_g3.metric(t("tray_lugares_unicos"), int(row_s['unique_places']))

    # ── Exportar informe ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_exportar_informe"))
    stats_tray = {
        t("super_col_nombre"): chosen,
        t("super_col_primer_ev"): str(first_dt.date()) if pd.notna(first_dt) else '?',
        t("super_col_ultimo_ev"): str(last_dt.date()) if pd.notna(last_dt) else '?',
        t("super_col_anios"): active_years,
        t("super_col_lugares"): n_unique_places,
        t("super_col_total_ev"): len(events),
    }
    if st.button(t("tray_export_html"), key="tray_export_html"):
        html_report = generate_witness_html_report(
            witness_name=chosen,
            events_df=display_df,
            stats_dict=stats_tray,
            folium_map_html=_map_html_tray,
            plotly_chart_html=_chart_html_tray,
        )
        st.download_button(
            t("dl_html"),
            data=html_report.encode('utf-8'),
            file_name=f"{t('informe_file_testigo')}_{normalize(chosen)[:30]}.html",
            mime="text/html",
            key="tray_dl_html"
        )
        pdf_bytes, pdf_err = try_export_pdf(html_report)
        if pdf_bytes:
            st.download_button(
                t("dl_pdf"),
                data=pdf_bytes,
                file_name=f"trayectoria_{normalize(chosen)[:30]}.pdf",
                mime="application/pdf",
                key="tray_dl_pdf"
            )
        elif pdf_err:
            st.warning(t("informe_error_pdf", e=pdf_err))
            st.caption(t("informe_caption_html"))
        else:
            st.caption(t("informe_caption_pdf_no"))


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONALIDAD: Informe narrativo exportable (Testigo / Familia / Red global)
# ─────────────────────────────────────────────────────────────────────────────

def page_informe(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    subj_id_map = dataset.subj_id_map
    st.header(t("hdr_informe"))
    st.caption(t("informe_caption_genera"))

    tipo = st.radio(
        t("informe_tipo_label"),
        [t("informe_tipo_testigo"), t("informe_tipo_familia"), t("informe_tipo_red")],
        horizontal=True,
        key="informe_tipo",
    )

    # ── Informe de Testigo ──────────────────────────────────────────────────
    if tipo == t("informe_tipo_testigo"):
        all_wit = sorted(by_witness.keys())
        sel_wit = st.selectbox(t("informe_sel_testigo"), [""] + all_wit, key="informe_wit_sel")
        if sel_wit and st.button(t("informe_btn_testigo"), key="informe_wit_btn"):
            with st.spinner(t("informe_generando")):
                try:
                    events = by_witness.get(sel_wit, [])
                    wit_df = pd.DataFrame(events)

                    # Estadísticas
                    stats = {}
                    if not wit_df.empty:
                        years = [int(str(d)[:4]) for d in wit_df['date_iso'].dropna() if str(d)[:4].isdigit()]
                        stats[t("informe_col_total_eventos")] = len(wit_df)
                        stats[t("informe_col_periodo")] = f"{min(years)}–{max(years)}" if years else '—'
                        if 'place_name' in wit_df.columns:
                            stats[t("informe_col_lugares")] = wit_df['place_name'].nunique()
                        if 'subj_name' in wit_df.columns:
                            fams = {extract_surname_improved(str(n)) for n in wit_df['subj_name'].dropna()}
                            fams.discard('')
                            stats[t("informe_col_familias")] = len(fams)

                    # Endogamia de familias que apadrinó
                    endo_section_html = None
                    try:
                        endo_all = compute_endogamy_stats(df, min_events=2)
                        if not endo_all.empty and not wit_df.empty:
                            fams_apadrinadas = {extract_surname_improved(str(n)) for n in wit_df['subj_name'].dropna()}
                            fams_apadrinadas.discard('')
                            endo_filt = endo_all[endo_all['familia'].isin(fams_apadrinadas)]
                            if not endo_filt.empty:
                                rows_e = ""
                                for _, r in endo_filt.iterrows():
                                    rows_e += f"<tr><td>{r['familia']}</td><td>{r['coef_endogamia']}</td><td>{r['idx_diversidad']}</td><td>{r['total_eventos']}</td></tr>\n"
                                endo_section_html = f"""
                                <h2>{t("informe_html_endo_titulo")}</h2>
                                <table>
                                  <thead><tr><th>{t("informe_html_col_familia")}</th><th>{t("informe_html_col_endo")}</th><th>{t("informe_html_col_div")}</th><th>{t("informe_html_col_eventos")}</th></tr></thead>
                                  <tbody>{rows_e}</tbody>
                                </table>"""
                    except Exception:
                        pass

                    # Notas clasificadas
                    notes_section = ""
                    if not wit_df.empty and 'note' in wit_df.columns:
                        notes_with_cat = []
                        for _, r in wit_df.iterrows():
                            note_t = str(r.get('note', '') or '')
                            if note_t and note_t not in ('nan', 'None'):
                                cat = classify_note(note_t)
                                notes_with_cat.append({t("informe_col_nota"): note_t[:200], t("informe_col_categoria"): cat,
                                                       t("informe_col_fecha"): r.get('date_iso', ''), t("informe_col_lugar"): r.get('place_name', '')})
                        if notes_with_cat:
                            n_rows = ""
                            for n in notes_with_cat:
                                n_rows += f"<tr><td>{n[t('informe_col_fecha')]}</td><td>{n[t('informe_col_lugar')]}</td><td>{n[t('informe_col_categoria')]}</td><td>{n[t('informe_col_nota')]}</td></tr>\n"
                            notes_section = f"""
                            <h2>{t("informe_html_notas_titulo")}</h2>
                            <table>
                              <thead><tr><th>{t("informe_col_fecha")}</th><th>{t("informe_col_lugar")}</th><th>{t("informe_col_categoria")}</th><th>{t("informe_col_nota")}</th></tr></thead>
                              <tbody>{n_rows}</tbody>
                            </table>"""

                    # Mapa
                    map_html_str = None
                    if folium is not None and not wit_df.empty:
                        try:
                            coords_w = []
                            for _, r in wit_df.iterrows():
                                try:
                                    lt, ln = float(r.get('lat') or 0), float(r.get('lon') or 0)
                                    if lt and ln:
                                        coords_w.append((lt, ln, str(r.get('date_iso', '')), str(r.get('place_name', ''))))
                                except Exception:
                                    pass
                            coords_w.sort(key=lambda x: x[2])
                            if coords_w:
                                m_w = folium.Map(location=[coords_w[0][0], coords_w[0][1]], zoom_start=9, tiles='CartoDB positron')
                                for lat_w, lon_w, fecha_w, place_w in coords_w:
                                    folium.CircleMarker([lat_w, lon_w], radius=6, color='darkblue', fill=True,
                                                        popup=folium.Popup(f"{place_w}<br>{fecha_w}", max_width=200)).add_to(m_w)
                                if len(coords_w) > 1:
                                    folium.PolyLine([(c[0], c[1]) for c in coords_w], color='darkblue', weight=2).add_to(m_w)
                                import io as _io
                                map_html_str = m_w._repr_html_()
                        except Exception:
                            pass

                    # Gráfico Plotly
                    chart_html_str = None
                    try:
                        import plotly.express as _px_inf
                        if not wit_df.empty and 'date_iso' in wit_df.columns:
                            wit_df['_year'] = wit_df['date_iso'].astype(str).str[:4]
                            yr_counts = wit_df[wit_df['_year'].str.match(r'^\d{4}$')].groupby('_year').size().reset_index(name='eventos')
                            fig_inf = _px_inf.bar(yr_counts, x='_year', y='eventos', labels={'_year': t("informe_chart_x"), 'eventos': t("informe_chart_y")}, title=t("informe_chart_titulo"))
                            chart_html_str = fig_inf.to_html(include_plotlyjs='cdn', full_html=False)
                    except Exception:
                        pass

                    # Combinar secciones adicionales en stats_dict
                    extra_html = (endo_section_html or '') + notes_section
                    if extra_html:
                        stats['_extra_sections'] = extra_html

                    wit_report_html = generate_witness_html_report(
                        sel_wit, wit_df, stats,
                        folium_map_html=map_html_str,
                        plotly_chart_html=chart_html_str,
                    )
                    # Insertar secciones extra antes del footer
                    if extra_html:
                        wit_report_html = wit_report_html.replace(
                            '<div class="footer">', extra_html + '\n  <div class="footer">'
                        )
                        stats.pop('_extra_sections', None)

                    fname_wit = f"{t('informe_file_testigo')}_{normalize(sel_wit)[:40]}.html"
                    st.download_button(t("dl_html"), data=wit_report_html.encode('utf-8'),
                                       file_name=fname_wit, mime="text/html", key="informe_wit_dl_html")
                    pdf_bytes, pdf_err = try_export_pdf(wit_report_html)
                    if pdf_bytes:
                        st.download_button(t("dl_pdf"), data=pdf_bytes,
                                           file_name=fname_wit.replace('.html', '.pdf'),
                                           mime="application/pdf", key="informe_wit_dl_pdf")
                    else:
                        st.caption(t("informe_pdf_no"))
                except Exception as e:
                    st.error(t("informe_error_testigo", e=e))

    # ── Informe de Familia ──────────────────────────────────────────────────
    elif tipo == t("informe_tipo_familia"):
        fam_options = sorted({extract_surname_improved(str(n)) for n in df['subj_name'].dropna() if n} - {''})
        sel_fam = st.selectbox(t("informe_sel_familia"), [""] + fam_options, key="informe_fam_sel")
        if sel_fam and st.button(t("informe_btn_familia"), key="informe_fam_btn"):
            with st.spinner(t("bayes_generando_familia")):
                try:
                    col_fam = 'subj_name'
                    fam_df = df[df[col_fam].apply(lambda n: extract_surname_improved(str(n or ''))) == sel_fam].copy()

                    stats_fam = {
                        t("informe_col_total_eventos"): len(fam_df),
                        'Padrinos distintos': fam_df[('witness_canon' if 'witness_canon' in fam_df.columns else 'witness_raw')].nunique(),
                    }
                    if 'date_iso' in fam_df.columns:
                        dates_f = fam_df['date_iso'].dropna().sort_values()
                        if not dates_f.empty:
                            stats_fam[t("super_col_primer_ev")] = str(dates_f.iloc[0])[:10]
                            stats_fam[t("super_col_ultimo_ev")] = str(dates_f.iloc[-1])[:10]
                    if 'place_name' in fam_df.columns:
                        stats_fam[t("informe_col_lugares")] = fam_df['place_name'].nunique()

                    # Endogamia para esta familia
                    endo_dict_fam = None
                    try:
                        endo_all_f = compute_endogamy_stats(df, min_events=1)
                        row_e = endo_all_f[endo_all_f['familia'] == sel_fam]
                        if not row_e.empty:
                            r = row_e.iloc[0]
                            endo_dict_fam = {
                                'Coeficiente de endogamia': r['coef_endogamia'],
                                'Índice de diversidad': r['idx_diversidad'],
                                'Padrinos únicos': r['padrinos_unicos'],
                                'Apellidos de padrinos': r['apellidos_padrinos'],
                            }
                    except Exception:
                        pass

                    # Mapa
                    fam_map_html = None
                    if folium is not None and not fam_df.empty and 'lat' in fam_df.columns:
                        try:
                            coords_f = [(float(r['lat']), float(r['lon']), str(r.get('date_iso', '')), str(r.get('place_name', '')))
                                        for _, r in fam_df.dropna(subset=['lat', 'lon']).iterrows()
                                        if float(r['lat']) and float(r['lon'])]
                            if coords_f:
                                m_f = folium.Map(location=[coords_f[0][0], coords_f[0][1]], zoom_start=9, tiles='CartoDB positron')
                                for lt_f, ln_f, d_f, p_f in coords_f:
                                    folium.CircleMarker([lt_f, ln_f], radius=6, color='green', fill=True,
                                                        popup=folium.Popup(f"{p_f}<br>{d_f}", max_width=200)).add_to(m_f)
                                fam_map_html = m_f._repr_html_()
                        except Exception:
                            pass

                    # Gráfico de padrinos a lo largo del tiempo
                    fam_chart_html = None
                    try:
                        import plotly.express as _px_fam
                        if not fam_df.empty and 'date_iso' in fam_df.columns:
                            fam_df['_year'] = fam_df['date_iso'].astype(str).str[:4]
                            yr_f = fam_df[fam_df['_year'].str.match(r'^\d{4}$')].groupby('_year').size().reset_index(name='eventos')
                            fig_fam = _px_fam.bar(yr_f, x='_year', y='eventos',
                                                  labels={'_year': t("informe_chart_x"), 'eventos': t("informe_chart_y")},
                                                  title=f'{t("informe_chart_titulo")} — {sel_fam}')
                            fam_chart_html = fig_fam.to_html(include_plotlyjs='cdn', full_html=False)
                    except Exception:
                        pass

                    fam_report_html = generate_family_html_report(
                        sel_fam, fam_df, stats_fam,
                        folium_map_html=fam_map_html,
                        endogamy_dict=endo_dict_fam,
                        plotly_chart_html=fam_chart_html,
                    )
                    fname_fam = f"{t('informe_file_familia')}_{normalize(sel_fam)[:40]}.html"
                    st.download_button(t("dl_html"), data=fam_report_html.encode('utf-8'),
                                       file_name=fname_fam, mime="text/html", key="informe_fam_dl_html")
                    pdf_bytes_f, _ = try_export_pdf(fam_report_html)
                    if pdf_bytes_f:
                        st.download_button(t("dl_pdf"), data=pdf_bytes_f,
                                           file_name=fname_fam.replace('.html', '.pdf'),
                                           mime="application/pdf", key="informe_fam_dl_pdf")
                    else:
                        st.caption(t("informe_pdf_no"))
                except Exception as e:
                    st.error(t("informe_error_familia", e=e))

    # ── Informe de Red global ───────────────────────────────────────────────
    elif tipo == t("informe_tipo_red"):
        st.markdown(t("informe_caption_red"))
        if st.button(t("informe_btn_red"), key="informe_red_btn"):
            with st.spinner(t("informe_generando")):
                try:
                    net_report_html = generate_network_html_report(df, by_witness, places_index, top_n=25)
                    st.download_button(t("dl_html"), data=net_report_html.encode('utf-8'),
                                       file_name=f"{t('informe_file_red')}.html", mime="text/html",
                                       key="informe_red_dl_html")
                    pdf_bytes_n, _ = try_export_pdf(net_report_html)
                    if pdf_bytes_n:
                        st.download_button(t("dl_pdf"), data=pdf_bytes_n,
                                           file_name="informe_red_completa.pdf",
                                           mime="application/pdf", key="informe_red_dl_pdf")
                    else:
                        st.caption(t("informe_pdf_no"))
                except Exception as e:
                    st.error(t("informe_error_red", e=e))


# ─────────────────────────────────────────────────────────────────────────────
# Nueva página: Pendientes (casos sin resolver)
# ─────────────────────────────────────────────────────────────────────────────

def page_pendientes(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    st.header(t("hdr_pendientes"))
    st.markdown(
        "Clusters de nombres similares donde no todos los eventos han sido confirmados. "
        "Ordenados por prioridad para ayudarte a decidir por dónde empezar."
    )

    conf = load_confirmations()
    status = conf.get('status', {})

    cluster_threshold = st.sidebar.slider(
        "Umbral clusters Pendientes (%)", 40, 100, 78,
        key="pendientes_threshold"
    )

    tmp = df.copy()
    tmp['witness_raw'] = tmp['witness_raw'].astype(str).fillna("") if 'witness_raw' in tmp.columns else ""
    unique_raws = sorted(tmp['witness_raw'].value_counts().index.tolist())

    with st.spinner(t("analisis_calc_clusters")):
        clusters = build_similarity_clusters(unique_raws, threshold=cluster_threshold)

    total_events = len(df)
    confirmed_event_ids = set(str(k) for k in status.keys())

    pending_clusters = []
    resolved_count = 0

    for cl in clusters:
        cl_events = tmp[tmp['witness_raw'].isin(cl)]
        cl_event_ids = cl_events['event_id'].astype(str).tolist()

        unconfirmed = [eid for eid in cl_event_ids if eid not in confirmed_event_ids]
        if not unconfirmed:
            resolved_count += 1
            continue

        cl_dates = cl_events['date_iso'].dropna().astype(str)
        date_min = cl_dates.min() if not cl_dates.empty else '?'
        date_max = cl_dates.max() if not cl_dates.empty else '?'
        date_range_str = (
            f"{date_min[:4]}–{date_max[:4]}"
            if date_min != '?' and len(date_min) >= 4 else '?'
        )

        places_list = cl_events['place_name'].dropna().astype(str).unique().tolist()

        # Probabilidad bayesiana (máx entre pares, cap 5)
        max_prob = 0.0
        cl_raws = list(cl)
        n_check = min(len(cl_raws), 5)
        for i_b in range(n_check):
            for j_b in range(i_b + 1, n_check):
                evs_i = tmp[tmp['witness_raw'] == cl_raws[i_b]].to_dict('records')
                evs_j = tmp[tmp['witness_raw'] == cl_raws[j_b]].to_dict('records')
                try:
                    res = bayesian_identity_probability(
                        evs_i, evs_j, cl_raws[i_b], cl_raws[j_b],
                        prior=0.05, places_index=places_index
                    )
                    max_prob = max(max_prob, res.get('probability_same_person', 0.0))
                except Exception:
                    pass

        uncertainty = round(1.0 - max_prob, 3)
        priority_score = round(len(cl_event_ids) * uncertainty, 2)

        pending_clusters.append({
            'variantes': " / ".join(cl_raws[:3]) + (" …" if len(cl_raws) > 3 else ""),
            'n_variantes': len(cl),
            'n_eventos': len(cl_event_ids),
            'n_pendientes': len(unconfirmed),
            'rango_fechas': date_range_str,
            'lugares': "; ".join(places_list[:3]),
            'incertidumbre_bayes': uncertainty,
            'prioridad': priority_score,
        })

    # ── Métricas globales ─────────────────────────────────────────────────────
    total_clusters = len(clusters)
    n_pending = len(pending_clusters)
    total_pending_events = sum(p['n_pendientes'] for p in pending_clusters)
    pct_resolved = round(len(confirmed_event_ids) / max(1, total_events) * 100, 1)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("pendientes_metric_clusters"), n_pending, t("pendientes_metric_de_total", total=total_clusters))
    m2.metric(t("pendientes_metric_eventos_pend"), total_pending_events)
    m3.metric(t("pendientes_metric_eventos_conf"), len(confirmed_event_ids))
    m4.metric(t("pendientes_metric_dataset"), f"{pct_resolved}%")

    st.progress(min(pct_resolved / 100.0, 1.0), text=t("pendientes_progreso", pct=pct_resolved))

    # ── Tabla de pendientes ───────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_clusters_pendientes"))

    if not pending_clusters:
        st.success(t("pendientes_no_hay"))
        return

    pending_clusters.sort(key=lambda x: -x['prioridad'])
    pend_df = pd.DataFrame(pending_clusters)

    st.dataframe(
        pend_df.head(200),
        use_container_width=True,
        column_config={
            'prioridad': st.column_config.NumberColumn("Prioridad", format="%.2f"),
            'incertidumbre_bayes': st.column_config.ProgressColumn(
                "Incertidumbre bayesiana", min_value=0.0, max_value=1.0, format="%.3f"
            ),
            'n_variantes': st.column_config.NumberColumn(t("pendientes_col_variantes")),
            'n_eventos': st.column_config.NumberColumn(t("pendientes_col_eventos")),
            'n_pendientes': st.column_config.NumberColumn(t("pendientes_col_pendientes")),
            'rango_fechas': st.column_config.TextColumn(t("pendientes_col_rango")),
            'variantes': st.column_config.TextColumn(t("pendientes_col_nombres")),
            'lugares': st.column_config.TextColumn(t("pendientes_col_lugares")),
        }
    )

    # Exportar lista de pendientes
    if not pend_df.empty:
        csv_pend = pend_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            t("pendientes_export_csv"),
            data=csv_pend,
            file_name="pendientes.csv",
            mime="text/csv",
            key="pendientes_export_csv"
        )

    # ── Acceso rápido ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(t("sub_acceso_rapido"))
    if st.button(t("pendientes_goto"), key="pendientes_goto_confirm"):
        st.session_state['tst_menu_override'] = t("menu_confirmar")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TESTIGOS EN ÁRBOL — funciones de soporte
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600)
def _get_gramps_index_rich(gramps_path_val):
    """Construye el índice enriquecido del árbol GRAMPS (cacheado)."""
    gi, _ = index_gramps(gramps_path_val)
    return gi


def _get_gramps_index_from_override():
    """Obtiene el índice GRAMPS directamente del DB del override de la API."""
    override_db = st.session_state.get("_gramps_web_db_override")
    if override_db is None:
        return {}
    gi, _ = override_db.to_gramps_index()
    return gi


def _is_plausible_gramps_match(events: list, candidate: dict,
                                max_geo_km: float = 200.0,
                                gramps_idx: dict = None) -> bool:
    """
    Rechaza un candidato GRAMPS si sus datos temporales o geográficos son
    incompatibles con la actividad del testigo.

    Devuelve False (incompatible) solo cuando hay datos que lo contradicen
    claramente. Cuando la persona no tiene coordenadas propias, intenta
    obtenerlas de sus hijos o nietos (proxy geográfico generacional).
    Si tras ese proceso sigue sin haber coordenadas, devuelve False para
    evitar falsos positivos con nombres comunes.
    """
    import re as _re_m

    years = []
    lats, lons = [], []
    for e in events:
        m = _re_m.match(r'(\d{4})', str(e.get('date_iso', '') or ''))
        if m:
            years.append(int(m.group(1)))
        try:
            lats.append(float(e.get('lat') or ''))
            lons.append(float(e.get('lon') or ''))
        except Exception:
            pass

    # ── Validación temporal ──────────────────────────────────────────────────
    if years:
        year_mean = int(sum(years) / len(years))
        birth_y = candidate.get('birth_year')
        death_y = candidate.get('death_year')
        if birth_y is not None:
            age = year_mean - birth_y
            if age < 10 or age > 100:
                return False
        if death_y is not None and death_y < min(years):
            return False

    # ── Validación geográfica ────────────────────────────────────────────────
    if lats and lons:
        wit_lat = sum(lats) / len(lats)
        wit_lon = sum(lons) / len(lons)

        p_lat = candidate.get('birth_lat') or candidate.get('death_lat')
        p_lon = candidate.get('birth_lon') or candidate.get('death_lon')

        # Si el candidato no tiene coordenadas propias, buscar en descendientes
        if (p_lat is None or p_lon is None) and gramps_idx is not None:
            rel_lats, rel_lons = [], []
            relatives = list(candidate.get('children', []))
            # Añadir nietos: hijos de los hijos
            for child_name in list(relatives):
                child_key = normalize(child_name)
                for child_entry in gramps_idx.get(child_key, []):
                    for gc_name in child_entry.get('children', []):
                        if gc_name not in relatives:
                            relatives.append(gc_name)
            for rel_name in relatives:
                rel_key = normalize(rel_name)
                for rel_entry in gramps_idx.get(rel_key, []):
                    rlat = rel_entry.get('birth_lat') or rel_entry.get('death_lat')
                    rlon = rel_entry.get('birth_lon') or rel_entry.get('death_lon')
                    if rlat is not None and rlon is not None:
                        rel_lats.append(float(rlat))
                        rel_lons.append(float(rlon))
            if rel_lats:
                p_lat = sum(rel_lats) / len(rel_lats)
                p_lon = sum(rel_lons) / len(rel_lons)

        if p_lat is not None and p_lon is not None:
            dist = haversine_km(wit_lat, wit_lon, float(p_lat), float(p_lon))
            if dist is not None and dist > max_geo_km:
                return False
        else:
            # Sin coordenadas propias ni de descendientes: rechazar para
            # evitar falsos positivos con nombres comunes sin anclaje geográfico.
            return False

    return True

def _cached_candidates(name_thr_val, max_dist_val, gramps_path_val, by_witness_keys):
    """
    Genera y cachea candidatos testigo↔GRAMPS usando session_state.
    by_witness_keys se usa como clave de invalidación de caché.
    """
    cache_key = f"_cand_{name_thr_val}_{max_dist_val}_{gramps_path_val}_{by_witness_keys}"
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached
    if st.session_state.get("_gramps_web_db_override") is not None:
        gi = _get_gramps_index_from_override()
    else:
        gi = _get_gramps_index_rich(gramps_path_val)
    result = find_gramps_candidates(by_witness, gi, places_index,
                                    name_threshold=name_thr_val,
                                    max_dist_km=max_dist_val)
    st.session_state[cache_key] = result
    return result

def find_gramps_candidates(by_witness_dict, gramps_index_rich, places_idx,
                           name_threshold=75, max_dist_km=150,
                           max_candidates_per_witness=5):
    """
    Genera candidatos (testigo ↔ persona GRAMPS) usando scoring multicriteria:
      - Similitud de nombre (40 %)
      - Coherencia temporal (30 %)
      - Coherencia geográfica (30 %)

    Retorna lista de dicts ordenada por score descendente.
    """
    import re as _re_c

    def _witness_summary(events):
        """Resumen de actividad de un testigo a partir de su lista de eventos."""
        years = []
        lats, lons, place_names = [], [], []
        families = set()
        for e in events:
            m = _re_c.match(r'(\d{4})', str(e.get('date_iso', '') or ''))
            if m: years.append(int(m.group(1)))
            try:
                lat = float(e.get('lat') or '')
                lon = float(e.get('lon') or '')
                lats.append(lat); lons.append(lon)
            except Exception:
                pass
            pn = e.get('place_name')
            if pn: place_names.append(pn)
            sn = e.get('subj_name')
            if sn:
                parts = str(sn).split()
                if parts: families.add(parts[-1])
        return {
            'count': len(events),
            'year_min': min(years) if years else None,
            'year_max': max(years) if years else None,
            'year_mean': int(sum(years)/len(years)) if years else None,
            'lat_mean': sum(lats)/len(lats) if lats else None,
            'lon_mean': sum(lons)/len(lons) if lons else None,
            'places': sorted(set(place_names))[:10],
            'families': sorted(families)[:10],
        }

    def _score_temporal(wit_sum, person):
        """Coherencia temporal: 0-1. Neutral (0.5) si no hay datos."""
        year_w = wit_sum.get('year_mean')
        birth_y = person.get('birth_year')
        death_y = person.get('death_year')
        if year_w is None or birth_y is None:
            return 0.5
        # La persona debería tener entre 15 y 90 años durante la actividad del testigo
        age_at_activity = year_w - birth_y
        if age_at_activity < 10 or age_at_activity > 100:
            return 0.0
        # Si ya había muerto antes de la actividad del testigo
        if death_y and death_y < (wit_sum.get('year_min') or year_w):
            return 0.0
        # Score basado en edad más plausible (20-60 años → score máximo)
        if 15 <= age_at_activity <= 70:
            return 1.0
        if 10 <= age_at_activity < 15 or 70 < age_at_activity <= 85:
            return 0.6
        return 0.3

    def _score_geo(wit_sum, person, max_km):
        """Coherencia geográfica: 0-1. Neutral (0.5) si no hay datos."""
        wit_lat = wit_sum.get('lat_mean')
        wit_lon = wit_sum.get('lon_mean')
        # Coordenadas de referencia GRAMPS: preferir nacimiento, luego defunción
        p_lat = person.get('birth_lat') or person.get('death_lat')
        p_lon = person.get('birth_lon') or person.get('death_lon')
        if wit_lat is None or wit_lon is None or p_lat is None or p_lon is None:
            # Sin coords del testigo → intentar con places_idx
            if wit_lat is None and wit_sum.get('places') and places_idx:
                for pname in wit_sum['places']:
                    pk = normalize(pname)
                    pdata = places_idx.get(pk)
                    if pdata:
                        try:
                            wit_lat = float(pdata.get('lat') or '')
                            wit_lon = float(pdata.get('lon') or '')
                            break
                        except Exception:
                            pass
            if wit_lat is None or p_lat is None:
                return 0.5
        dist = haversine_km(wit_lat, wit_lon, float(p_lat), float(p_lon))
        if dist is None:
            return 0.5
        if max_km and max_km > 0 and dist > max_km:
            return 0.0
        # Score decreciente con distancia
        if dist <= 20:
            return 1.0
        if dist <= 50:
            return 0.85
        if dist <= 100:
            return 0.65
        if dist <= 200:
            return 0.40
        return 0.15

    # ── Patrón de notas: extrae pares (término_relación, nombre_X) ───────────
    # Lógica: si la nota dice "A es muller de X", y el candidato GRAMPS tiene
    # registrado a X como cónyuge (con coincidencia de nombre Y apellido),
    # se aplica un bonus al score porque la nota corrobora la identificación.
    _NOTE_REL_TERMS = (
        r'mujer|muger|muier|muller|mulher|esposa|esposo|marido|consorte'
        r'|viuda|viudo|bivda|bivdo|marit|v[íi]dua|uxor|coniunx|coniuge'
        r'|padre|madre|pare|mare|pai|mai|pater|mater'
        r'|hijo|hija|fill|filla|fillo|filho|filha|filius|filia'
        r'|fillol|fillola'
        r'|hermano|hermana|germ[àa]|germana|jerm[àa]|jermana|irmao|irman|irm[aã]'
        r'|abuelo|abuela|avi|[àa]via|nieto|nieta|n[eé]t|n[eé]ta|neto|neta'
        r'|tio|tia|oncle|tieta|sobrino|sobrina|nebot|neboda'
        r'|cu[nñ]ado|cu[nñ]ada|yerno|nuera|gendre|jendre|nora|sogre|sogra|xenro'
        r'|cunyat|cunyada'
    )
    _NOTE_EXTRACT_PATTERN = _re_c.compile(
        r'\b(?:' + _NOTE_REL_TERMS + r')\s+de\s+([^\.,;:\(\)\n]{3,60})',
        _re_c.IGNORECASE
    )

    def _extract_note_x_names(events):
        """
        Extrae de las notas del testigo los nombres X mencionados tras
        'relación de', devolviendo lista de (x_norm, x_raw, fragmento).
        Solo términos de cónyuge: la relación más habitual y la que
        permite verificar contra cónyuges registrados en el árbol.
        """
        seen = set()
        results_local = []
        for ev in events:
            note = ev.get('note') or ''
            for m in _NOTE_EXTRACT_PATTERN.finditer(note):
                raw_x = m.group(1).strip()
                # Eliminar títulos o texto tras coma ("Hieroni Biarnes, notari")
                raw_x = _re_c.sub(r'\s*,.*$', '', raw_x).strip()
                x_norm = normalize(raw_x)
                if not x_norm or len(x_norm) < 4 or x_norm in seen:
                    continue
                seen.add(x_norm)
                results_local.append((x_norm, raw_x, m.group(0).strip()))
        return results_local

    def _note_spouse_bonus(candidate_person, note_x_names, _gramps_index_rich=None):
        """
        Comprueba si algún X mencionado en las notas del testigo coincide
        (nombre + apellido) con un cónyuge registrado del candidato GRAMPS.

        Requiere coincidencia de apellido principal (última palabra) Y al menos
        las primeras 4 letras del nombre de pila — evita falsos positivos por
        nombres de pila comunes.

        Devuelve (bonus, hint) donde bonus=0.15 si hay coincidencia, 0 si no.
        """
        if not note_x_names:
            return 0.0, None

        # Construir lista de cónyuges del candidato con sus nombres normalizados
        spouse_norm_names = []
        for raw_sp in candidate_person.get('spouses', []):
            sp_norm = normalize(str(raw_sp))
            if sp_norm:
                spouse_norm_names.append((sp_norm, str(raw_sp)))

        if not spouse_norm_names:
            return 0.0, None

        for x_norm, x_raw, fragment in note_x_names:
            x_parts = x_norm.split()
            if len(x_parts) < 2:
                continue  # necesitamos al menos nombre + apellido
            x_apellido = x_parts[-1]
            x_nombre_ini = x_parts[0][:4]

            for sp_norm, sp_raw in spouse_norm_names:
                sp_parts = sp_norm.split()
                if not sp_parts:
                    continue
                sp_apellido = sp_parts[-1]
                sp_nombre_ini = sp_parts[0][:4] if sp_parts else ''

                # Exigir coincidencia de apellido Y inicio de nombre de pila
                if (len(x_apellido) >= 3 and x_apellido == sp_apellido
                        and x_nombre_ini and sp_nombre_ini
                        and x_nombre_ini == sp_nombre_ini):
                    hint = (f'"{fragment}" — '
                            f'{t("testigos_arbol_nota_conyuge_arbol")}: {sp_raw}')
                    return 0.15, hint

        return 0.0, None

    results = []
    # Construir lookup plano persona→datos e índice por inicial de apellido
    all_persons = []
    by_surname_initial = defaultdict(list)
    for norm_key, pers_list in gramps_index_rich.items():
        for p in pers_list:
            all_persons.append(p)
            p_norm = normalize(p.get('name', ''))
            parts = p_norm.split()
            if parts:
                by_surname_initial[parts[-1][:2]].append(p)

    for wit_canon, events in by_witness_dict.items():
        if not events: continue
        wit_sum = _witness_summary(events)
        note_x_names = _extract_note_x_names(events)

        # Pre-filtrar personas por inicial de apellido para reducir comparaciones
        wit_parts = wit_canon.split()
        if wit_parts:
            wit_surname_ini = wit_parts[-1][:2]
            # Candidatos que comparten inicial de apellido + todos si el testigo
            # tiene solo un token (puede ser apellido o nombre de pila)
            candidate_pool = by_surname_initial.get(wit_surname_ini, [])
            if len(wit_parts) == 1 or len(candidate_pool) < 20:
                # Ampliar con iniciales adyacentes o usar todos si pool muy pequeño
                candidate_pool = all_persons
        else:
            candidate_pool = all_persons

        per_scores = []
        for p in candidate_pool:
            p_name = p.get('name', '')
            if not p_name: continue
            sim = name_similarity(wit_canon, p_name)
            if sim < name_threshold: continue
            t_score = _score_temporal(wit_sum, p)
            g_score = _score_geo(wit_sum, p, max_dist_km)
            base = round(0.40 * (sim / 100.0) + 0.30 * t_score + 0.30 * g_score, 4)
            if base < 0.35: continue

            note_bonus, note_hint = _note_spouse_bonus(p, note_x_names, gramps_index_rich)
            total = round(min(base + note_bonus, 1.0), 4)

            per_scores.append({
                'witness_canon': wit_canon,
                'gramps_id': p.get('id'),
                'gramps_name': p_name,
                'score': total,
                'score_detail': {
                    'nombre': round(sim / 100.0, 4),
                    'temporal': round(t_score, 4),
                    'geografico': round(g_score, 4),
                    'nota_bonus': round(note_bonus, 4),
                },
                'note_hint': note_hint,
                'witness_summary': wit_sum,
                'gramps_person': p,
            })

        per_scores.sort(key=lambda x: x['score'], reverse=True)
        results.extend(per_scores[:max_candidates_per_witness])

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def find_kinship_with_subject(gramps_person, witness_events, gramps_index_rich):
    """
    Busca lazos de parentesco (hasta 2 grados) entre la persona GRAMPS candidata
    y los sujetos de los eventos en que el testigo participó.

    Grado 1: padre/madre, hijo/a, cónyuge, hermano/a
    Grado 2: abuelo/a, nieto/a, tío/a, sobrino/a, cuñado/a,
             hermanastro/a (mismo padre o madre por parte de cónyuge)

    Estrategia de resolución del sujeto:
    1. subj_id del evento → búsqueda directa por ID en el árbol (fiable)
    2. Fallback: nombre normalizado (frágil, solo si no hay ID)

    Fuente adicional (source='note'):
    3. Parseo de notas del evento con patrón "relación de nombre":
       si el nombre mencionado coincide con el sujeto del evento
       (o con la persona GRAMPS candidata), se infiere el parentesco.

    Devuelve lista de dicts ordenada por grado:
      { subj_name, kinship_label, degree, source, note_mention }
      source: 'tree' (árbol GRAMPS) | 'note' (inferido de nota)
      note_mention: fragmento de nota original que originó la inferencia (solo si source='note')
    """
    if not gramps_person or not witness_events:
        return []

    gp_id = str(gramps_person.get('id') or '')

    # ── Índice plano ID→persona (construido una vez por llamada) ──────────────
    id_to_person = {}
    for pers_list in gramps_index_rich.values():
        for p in pers_list:
            pid = p.get('id')
            if pid:
                id_to_person[str(pid)] = p

    def _ids_of(person, field):
        """IDs de los parientes de primer grado de 'person' en el campo 'field'
        (parents, children, spouses). Cada entrada puede ser nombre o ID según
        cómo se construyó el índice; resolvemos ambos."""
        ids = set()
        for raw in person.get(field, []):
            raw_s = str(raw)
            # Si parece un ID de GRAMPS (Ixxxx)
            if raw_s in id_to_person:
                ids.add(raw_s)
                continue
            # Intentar por nombre normalizado
            norm = normalize(raw_s)
            for p in gramps_index_rich.get(norm, []):
                ids.add(str(p['id']))
        return ids

    def _resolve_subj(ev):
        """Devuelve la persona GRAMPS del sujeto del evento, o None."""
        sid = str(ev.get('subj_id') or '').strip()
        if sid and sid in id_to_person:
            return id_to_person[sid]
        sname = normalize(ev.get('subj_name') or '')
        cands = gramps_index_rich.get(sname, [])
        return cands[0] if cands else None

    # IDs de parientes de 1er grado del candidato (gp)
    gp_parent_ids  = _ids_of(gramps_person, 'parents')
    gp_child_ids   = _ids_of(gramps_person, 'children')
    gp_spouse_ids  = _ids_of(gramps_person, 'spouses')
    # Hermanos: personas que comparten al menos un padre con gp
    gp_sibling_ids = set()
    for pid in gp_parent_ids:
        parent_p = id_to_person.get(pid)
        if parent_p:
            gp_sibling_ids |= _ids_of(parent_p, 'children')
    gp_sibling_ids.discard(gp_id)

    def _kinship(sp_id):
        """
        Devuelve (label, degree) o None si no se encuentra relación en ≤2 grados.
        """
        if not sp_id or sp_id not in id_to_person:
            return None
        sp = id_to_person[sp_id]

        # ── Grado 0: misma persona ────────────────────────────────────────────
        if sp_id == gp_id:
            return (t("kinship_es_mismo"), 0)

        sp_parent_ids = _ids_of(sp, 'parents')
        sp_child_ids  = _ids_of(sp, 'children')
        sp_spouse_ids = _ids_of(sp, 'spouses')

        # ── Grado 1 ───────────────────────────────────────────────────────────
        # gp es padre/madre de sp
        if gp_id in sp_parent_ids:
            return (t("kinship_padre_madre"), 1)
        # gp es hijo/a de sp
        if gp_id in sp_child_ids:
            return (t("kinship_hijo_hija"), 1)
        # gp es cónyuge de sp
        if gp_id in sp_spouse_ids or sp_id in gp_spouse_ids:
            return (t("kinship_conyuge"), 1)
        # gp y sp comparten padre → hermanos
        if gp_parent_ids and sp_parent_ids and gp_parent_ids & sp_parent_ids:
            return (t("kinship_hermano_hermana"), 1)

        # ── Grado 2 ───────────────────────────────────────────────────────────
        # gp es abuelo/a de sp: gp es padre de uno de los padres de sp
        for sp_par_id in sp_parent_ids:
            sp_par = id_to_person.get(sp_par_id)
            if sp_par and gp_id in _ids_of(sp_par, 'parents'):
                return (t("kinship_abuelo_abuela"), 2)

        # gp es nieto/a de sp: sp es padre de uno de los padres de gp
        for gp_par_id in gp_parent_ids:
            gp_par = id_to_person.get(gp_par_id)
            if gp_par and sp_id in _ids_of(gp_par, 'parents'):
                return (t("kinship_nieto_nieta"), 2)

        # gp es tío/a de sp: gp es hermano/a de un padre de sp
        if gp_sibling_ids & sp_parent_ids:
            return (t("kinship_tio_tia"), 2)

        # gp es sobrino/a de sp: sp es hermano/a de un padre de gp
        sp_sibling_ids = set()
        for sp_par_id in sp_parent_ids:
            sp_par = id_to_person.get(sp_par_id)
            if sp_par:
                sp_sibling_ids |= _ids_of(sp_par, 'children')
        sp_sibling_ids.discard(sp_id)
        if sp_id not in gp_parent_ids and gp_parent_ids & sp_sibling_ids:
            # sp es hermano de un padre de gp → sp es tío de gp → gp es sobrino de sp
            pass  # ya cubierto como tío desde el punto de vista de sp
        if sp_sibling_ids & gp_parent_ids:
            return (t("kinship_sobrino_sobrina"), 2)

        # gp es cuñado/a de sp: gp es cónyuge de un hermano/a de sp
        sp_sibling_ids2 = set()
        for sp_par_id in sp_parent_ids:
            sp_par = id_to_person.get(sp_par_id)
            if sp_par:
                sp_sibling_ids2 |= _ids_of(sp_par, 'children')
        sp_sibling_ids2.discard(sp_id)
        if gp_id in {sid for sib_id in sp_sibling_ids2
                     for p in [id_to_person.get(sib_id)] if p
                     for sid in _ids_of(p, 'spouses')}:
            return (t("kinship_cunado_cunada"), 2)

        # sp es cuñado/a de gp: sp es cónyuge de un hermano/a de gp
        if sp_id in {sid for sib_id in gp_sibling_ids
                     for p in [id_to_person.get(sib_id)] if p
                     for sid in _ids_of(p, 'spouses')}:
            return (t("kinship_cunado_cunada"), 2)

        return None

    # ── Tabla de términos relacionales → (translation_key, degree) ──────────────
    # Cubre variantes ortográficas históricas, castellano, valenciano/catalán,
    # gallego/portugués y latín notarial frecuente en registros parroquiales.
    _NOTE_RELATIONS = [
        # ── Cónyuge / grado 1 ────────────────────────────────────────────────
        # Castellano y variantes antiguas
        (r'mujer',         'kinship_conyuge', 1),
        (r'muger',         'kinship_conyuge', 1),
        (r'muier',         'kinship_conyuge', 1),
        (r'esposa',        'kinship_conyuge', 1),
        (r'esposo',        'kinship_conyuge', 1),
        (r'marido',        'kinship_conyuge', 1),
        (r'consorte',      'kinship_conyuge', 1),
        (r'viuda',         'kinship_conyuge', 1),
        (r'viudo',         'kinship_conyuge', 1),
        (r'bivda',         'kinship_conyuge', 1),  # grafía antigua castellana
        (r'bivdo',         'kinship_conyuge', 1),
        # Valenciano/catalán
        (r'muller',        'kinship_conyuge', 1),  # muller de (val./cat.)
        (r'mulher',        'kinship_conyuge', 1),  # grafía lusista ocasional
        (r'esposa',        'kinship_conyuge', 1),  # igual en val./cat.
        (r'marit',         'kinship_conyuge', 1),  # marido en val./cat.
        (r'v[íi]dua',      'kinship_conyuge', 1),  # viuda val./cat. (vidua/vídua)
        (r'viudo',         'kinship_conyuge', 1),  # viudo val./cat.
        (r'v[íi]udo',      'kinship_conyuge', 1),
        # Galego/portugués
        (r'muller',        'kinship_conyuge', 1),  # coincide con val./cat.
        # Latín notarial
        (r'uxor',          'kinship_conyuge', 1),  # uxor de (esposa en latín)
        (r'coniunx',       'kinship_conyuge', 1),  # cónyuge en latín
        (r'coniuge',       'kinship_conyuge', 1),

        # ── Padre/Madre / grado 1 ─────────────────────────────────────────────
        # Castellano
        (r'padre',         'kinship_padre_madre', 1),
        (r'madre',         'kinship_padre_madre', 1),
        # Valenciano/catalán
        (r'pare',          'kinship_padre_madre', 1),  # padre val./cat.
        (r'mare',          'kinship_padre_madre', 1),  # madre val./cat.
        # Gallego/portugués
        (r'pai',           'kinship_padre_madre', 1),
        (r'mai',           'kinship_padre_madre', 1),
        # Latín notarial
        (r'pater',         'kinship_padre_madre', 1),
        (r'mater',         'kinship_padre_madre', 1),

        # ── Hijo/Hija / grado 1 ───────────────────────────────────────────────
        # Castellano
        (r'hijo',          'kinship_hijo_hija', 1),
        (r'hija',          'kinship_hijo_hija', 1),
        # Valenciano/catalán
        (r'fill',          'kinship_hijo_hija', 1),   # fill/filla val./cat.
        (r'filla',         'kinship_hijo_hija', 1),
        (r'fillol',        'kinship_hijo_hija', 1),   # ahijado val./cat. (fillol/fillola)
        (r'fillola',       'kinship_hijo_hija', 1),
        # Gallego/portugués
        (r'fillo',         'kinship_hijo_hija', 1),
        (r'filho',         'kinship_hijo_hija', 1),
        (r'filha',         'kinship_hijo_hija', 1),
        # Latín notarial
        (r'filius',        'kinship_hijo_hija', 1),
        (r'filia',         'kinship_hijo_hija', 1),

        # ── Hermano/Hermana / grado 1 ─────────────────────────────────────────
        # Castellano
        (r'hermano',       'kinship_hermano_hermana', 1),
        (r'hermana',       'kinship_hermano_hermana', 1),
        # Valenciano/catalán
        (r'germ[àa]',      'kinship_hermano_hermana', 1),  # germà/germa (hermano val./cat.)
        (r'germana',       'kinship_hermano_hermana', 1),  # hermana val./cat.
        (r'jerm[àa]',      'kinship_hermano_hermana', 1),  # grafía antigua val.
        (r'jermana',       'kinship_hermano_hermana', 1),
        # Gallego/portugués
        (r'irmao',         'kinship_hermano_hermana', 1),
        (r'irman',         'kinship_hermano_hermana', 1),
        (r'irm[aã]',       'kinship_hermano_hermana', 1),

        # ── Abuelo/Abuela / grado 2 ───────────────────────────────────────────
        # Castellano
        (r'abuelo',        'kinship_abuelo_abuela', 2),
        (r'abuela',        'kinship_abuelo_abuela', 2),
        # Valenciano/catalán
        (r'avi',           'kinship_abuelo_abuela', 2),   # abuelo val./cat.
        (r'[àa]via',       'kinship_abuelo_abuela', 2),   # abuela val./cat. (àvia/avia)
        # Gallego/portugués
        (r'avoo?',         'kinship_abuelo_abuela', 2),
        (r'avoa?',         'kinship_abuelo_abuela', 2),

        # ── Nieto/Nieta / grado 2 ─────────────────────────────────────────────
        # Castellano
        (r'nieto',         'kinship_nieto_nieta', 2),
        (r'nieta',         'kinship_nieto_nieta', 2),
        # Valenciano/catalán
        (r'n[eé]t',        'kinship_nieto_nieta', 2),    # nét/net (nieto val./cat.)
        (r'n[eé]ta',       'kinship_nieto_nieta', 2),    # néta/neta (nieta val./cat.)
        # Gallego/portugués
        (r'neto',          'kinship_nieto_nieta', 2),
        (r'neta',          'kinship_nieto_nieta', 2),

        # ── Tío/Tía / grado 2 ────────────────────────────────────────────────
        # Castellano
        (r'tio',           'kinship_tio_tia', 2),
        (r'tia',           'kinship_tio_tia', 2),
        # Valenciano/catalán (igual que castellano en forma)
        (r'oncle',         'kinship_tio_tia', 2),   # tío val./cat.
        (r'tieta',         'kinship_tio_tia', 2),   # tía val./cat. (tieta/tia)

        # ── Sobrino/Sobrina / grado 2 ─────────────────────────────────────────
        # Castellano
        (r'sobrino',       'kinship_sobrino_sobrina', 2),
        (r'sobrina',       'kinship_sobrino_sobrina', 2),
        # Valenciano/catalán
        (r'nebot',         'kinship_sobrino_sobrina', 2),  # sobrino val./cat.
        (r'neboda',        'kinship_sobrino_sobrina', 2),  # sobrina val./cat.
        (r'nebot',         'kinship_sobrino_sobrina', 2),

        # ── Cuñado/Cuñada / grado 2 ───────────────────────────────────────────
        # Castellano
        (r'cu[nñ]ado',     'kinship_cunado_cunada', 2),
        (r'cu[nñ]ada',     'kinship_cunado_cunada', 2),
        (r'yerno',         'kinship_cunado_cunada', 2),
        (r'nuera',         'kinship_cunado_cunada', 2),
        # Valenciano/catalán
        (r'cunyat',        'kinship_cunado_cunada', 2),   # cuñado val./cat.
        (r'cunyada',       'kinship_cunado_cunada', 2),   # cuñada val./cat.
        (r'cuñat',         'kinship_cunado_cunada', 2),   # grafía mixta val.
        (r'cuñada',        'kinship_cunado_cunada', 2),
        (r'gendre',        'kinship_cunado_cunada', 2),   # yerno val./cat.
        (r'jendre',        'kinship_cunado_cunada', 2),   # grafía antigua val.
        (r'nora',          'kinship_cunado_cunada', 2),   # nuera val./cat./gallego
        (r'sogre',         'kinship_cunado_cunada', 2),   # suegro val./cat.
        (r'sogra',         'kinship_cunado_cunada', 2),   # suegra val./cat.
        # Gallego/portugués
        (r'xenro',         'kinship_cunado_cunada', 2),
    ]

    # Patrón general: <relación> de <nombre> — captura todo lo que sigue a "de"
    # hasta fin de frase (punto, coma, paréntesis, fin de cadena)
    _NOTE_PATTERN = re.compile(
        r'\b(' + '|'.join(r[0] for r in _NOTE_RELATIONS) + r')'
        r'\s+de\s+([^\.,;:\(\)\n]{3,60})',
        re.IGNORECASE
    )

    # ── Índice de nombres del sujeto y del candidato GRAMPS para comparar ──────
    gp_name_norm = normalize(gramps_person.get('name') or '')

    # ── Bucle principal: árbol ────────────────────────────────────────────────
    found = {}  # subj_name → (label, degree, source, note_mention)
    for ev in witness_events:
        subj_name = ev.get('subj_name') or ''
        if not subj_name or subj_name in found:
            continue
        sp = _resolve_subj(ev)
        if sp is None:
            continue
        result = _kinship(str(sp.get('id', '')))
        if result:
            label, degree = result
            existing = found.get(subj_name)
            if existing is None or degree < existing[1]:
                found[subj_name] = (label, degree, 'tree', None)

    # ── Bucle secundario: notas ───────────────────────────────────────────────
    # Solo se aplica si no hay ya resultado de árbol para ese sujeto.
    for ev in witness_events:
        subj_name = ev.get('subj_name') or ''
        note_text = ev.get('note') or ''
        if not subj_name or not note_text:
            continue

        subj_name_norm = normalize(subj_name)

        for m in _NOTE_PATTERN.finditer(note_text):
            rel_term  = m.group(1).lower()
            named_str = m.group(2).strip()
            named_norm = normalize(named_str)

            # Buscar la translation key que corresponde al término encontrado
            kin_key = None
            kin_deg = None
            for pattern, key, deg in _NOTE_RELATIONS:
                if re.fullmatch(pattern, rel_term, re.IGNORECASE):
                    kin_key = key
                    kin_deg = deg
                    break
            if kin_key is None:
                continue

            label = t(kin_key)
            note_fragment = m.group(0).strip()

            # Caso A: el nombre mencionado en la nota coincide con el sujeto
            # → el testigo es <relación> del sujeto
            if named_norm and subj_name_norm and (
                named_norm in subj_name_norm or subj_name_norm in named_norm
                or (len(named_norm) >= 4 and
                    (named_norm[:6] in subj_name_norm or subj_name_norm[:6] in named_norm))
            ):
                existing = found.get(subj_name)
                if existing is None or (existing[2] == 'note' and kin_deg < existing[1]):
                    found[subj_name] = (label, kin_deg, 'note', note_fragment)

            # Caso B: el nombre mencionado coincide con el candidato GRAMPS
            # → la nota describe al propio testigo ("muller de X") donde X es el sujeto
            # En este caso registramos que el sujeto es cónyuge/relación inversa del testigo
            elif named_norm and gp_name_norm and (
                named_norm in gp_name_norm or gp_name_norm in named_norm
                or (len(named_norm) >= 4 and
                    (named_norm[:6] in gp_name_norm or gp_name_norm[:6] in named_norm))
            ):
                # La relación mencionada describe al testigo respecto a "named",
                # que podría ser el sujeto u otro. Si el sujeto es quien aparece
                # en el evento, registramos la relación con el sujeto.
                existing = found.get(subj_name)
                if existing is None or (existing[2] == 'note' and kin_deg < existing[1]):
                    found[subj_name] = (label, kin_deg, 'note', note_fragment)

    return [
        {
            'subj_name':     k,
            'kinship_label': v[0],
            'degree':        v[1],
            'source':        v[2],
            'note_mention':  v[3],
        }
        for k, v in sorted(found.items(), key=lambda x: x[1][1])
    ]


def page_testigos_arbol():
    st.header(t("hdr_testigos_arbol"))

    gramps_path = get_active_gramps_path()
    if not gramps_path:
        st.warning(t("testigos_arbol_no_gramps"))
        return

    conf = load_confirmations()
    gl = conf.get('gramps_links', {'confirmed': {}, 'discarded': []})
    confirmed_map = gl.get('confirmed', {})
    discarded_list = gl.get('discarded', [])
    discarded_set = set(tuple(x) for x in discarded_list if len(x) == 2)

    # ── Métricas rápidas ──────────────────────────────────────────────────────
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric(t("testigos_arbol_confirmados"), len(confirmed_map))
    mc2.metric(t("testigos_arbol_descartados"), len(discarded_set))

    # ── Controles ─────────────────────────────────────────────────────────────
    with st.expander(t("testigos_arbol_controles"), expanded=True):
        col_s1, col_s2 = st.columns(2)
        name_thr = col_s1.slider(t("testigos_arbol_umbral_nombre"), 55, 95, 75, 5,
                                  key="ta_name_thr")
        max_dist = col_s2.slider(t("testigos_arbol_max_dist"), 0, 500, 150, 10,
                                  key="ta_max_dist")

    # ── Generar candidatos (cacheados en session_state) ──────────────────────
    by_witness_keys = tuple(sorted(by_witness.keys()))
    _cand_cache_key = f"_cand_{name_thr}_{max_dist}_{gramps_path}_{by_witness_keys}"
    if _cand_cache_key not in st.session_state:
        with st.spinner("Calculando candidatos…"):
            all_candidates = _cached_candidates(name_thr, max_dist, gramps_path, by_witness_keys)
    else:
        all_candidates = _cached_candidates(name_thr, max_dist, gramps_path, by_witness_keys)

    # Filtrar ya decididos
    pending = [c for c in all_candidates
               if c['witness_canon'] not in confirmed_map
               and (c['witness_canon'], c['gramps_id']) not in discarded_set]

    mc3.metric(t("testigos_arbol_pendientes"), len(pending))

    # ── Tabs: Pendientes / Confirmados / Descartados ──────────────────────────
    tab_pend, tab_conf, tab_desc = st.tabs([
        t("testigos_arbol_pendientes"),
        t("testigos_arbol_confirmados"),
        t("testigos_arbol_descartados"),
    ])

    # ── TAB PENDIENTES ────────────────────────────────────────────────────────
    with tab_pend:
        if not pending:
            st.info(t("testigos_arbol_sin_candidatos"))
        for idx, cand in enumerate(pending):
            wit = cand['witness_canon']
            gname = cand['gramps_name']
            gid = cand['gramps_id']
            score_pct = int(round(cand['score'] * 100))
            sd = cand['score_detail']
            ws = cand['witness_summary']
            gp = cand['gramps_person']

            label = f"[{score_pct}%] **{wit}** ↔ {gname} ({gid})"
            with st.expander(label, expanded=False):
                # Score desglosado
                sc1, sc2, sc3_col = st.columns(3)
                sc1.metric(t("testigos_arbol_score_nombre"), f"{int(sd['nombre']*100)}%")
                sc2.metric(t("testigos_arbol_score_temporal"), f"{int(sd['temporal']*100)}%")
                sc3_col.metric(t("testigos_arbol_score_geo"), f"{int(sd['geografico']*100)}%")
                if sd.get('nota_bonus', 0) > 0 and cand.get('note_hint'):
                    st.success(f"🗒 +{int(sd['nota_bonus']*100)}% {t('testigos_arbol_score_nota')}: {cand['note_hint']}")

                st.markdown("---")
                col_wit, col_gramps = st.columns(2)

                # Columna TESTIGO
                with col_wit:
                    st.markdown(f"**{t('testigos_arbol_testigo')}: {wit}**")
                    st.write(f"· {t('super_apariciones')}: **{ws['count']}**")
                    if ws['year_min'] and ws['year_max']:
                        st.write(f"· {t('super_periodo_activo')}: {ws['year_min']}–{ws['year_max']}")
                    if ws['places']:
                        st.write(f"· {t('super_lugares_unicos')}: {', '.join(ws['places'][:5])}")
                    if ws['families']:
                        st.write(f"· {t('super_familias')} (apellidos): {', '.join(ws['families'][:8])}")

                # Columna PERSONA EN ÁRBOL
                with col_gramps:
                    st.markdown(f"**{t('testigos_arbol_persona')}: {gname}** ({gid})")
                    birth_str = str(gp.get('birth_year') or '—')
                    if gp.get('birth_place'):
                        birth_str += f", {gp['birth_place']}"
                    death_str = str(gp.get('death_year') or '—')
                    if gp.get('death_place'):
                        death_str += f", {gp['death_place']}"
                    st.write(f"· {t('testigos_arbol_nacimiento')}: {birth_str}")
                    st.write(f"· {t('testigos_arbol_defuncion')}: {death_str}")
                    if gp.get('parents'):
                        st.write(f"· {t('testigos_arbol_padres')}: {', '.join(gp['parents'])}")
                    if gp.get('spouses'):
                        st.write(f"· {t('testigos_arbol_conyuges')}: {', '.join(gp['spouses'])}")
                    if gp.get('children'):
                        st.write(f"· {t('testigos_arbol_hijos')}: {', '.join(gp['children'][:8])}")
                    if gp.get('notes'):
                        st.markdown(f"*{t('testigos_arbol_notas')}:*")
                        for note_txt in gp['notes']:
                            st.caption(note_txt)

                # ── Parentesco con sujetos del evento ────────────────────────
                gi_rich_kin = _get_gramps_index_rich(gramps_path)
                kin_links = find_kinship_with_subject(gp, by_witness.get(wit, []), gi_rich_kin)
                if kin_links:
                    st.markdown(f"**{t('testigos_arbol_parentesco')}:**")
                    for kl in kin_links[:8]:
                        degree_str = f" *(gr. {kl['degree']})*" if kl['degree'] > 0 else ""
                        if kl.get('source') == 'note':
                            st.write(f"· {kl['kinship_label']}{degree_str} de **{kl['subj_name']}** "
                                     f"*(🗒 {t('kinship_from_note')}: \"{kl['note_mention']}\")*")
                        else:
                            st.write(f"· {kl['kinship_label']}{degree_str} de **{kl['subj_name']}**")

                st.markdown("---")
                b1, b2, b3 = st.columns(3)
                if b1.button(t("testigos_arbol_confirmar"), key=f"ta_ok_{idx}"):
                    conf2 = load_confirmations()
                    conf2.setdefault('gramps_links', {'confirmed': {}, 'discarded': []})
                    conf2['gramps_links']['confirmed'][wit] = gid
                    save_confirmations(conf2)
                    st.rerun()
                if b2.button(t("testigos_arbol_descartar"), key=f"ta_no_{idx}"):
                    conf2 = load_confirmations()
                    conf2.setdefault('gramps_links', {'confirmed': {}, 'discarded': []})
                    discarded = conf2['gramps_links'].get('discarded', [])
                    if [wit, gid] not in discarded:
                        discarded.append([wit, gid])
                    conf2['gramps_links']['discarded'] = discarded
                    save_confirmations(conf2)
                    st.rerun()
                b3.button(t("testigos_arbol_posponer"), key=f"ta_skip_{idx}", disabled=True)

    # ── TAB CONFIRMADOS ───────────────────────────────────────────────────────
    with tab_conf:
        if not confirmed_map:
            st.info(t("testigos_arbol_sin_confirmados"))
        else:
            gi_rich = _get_gramps_index_rich(gramps_path)
            for wit_c, gid_c in list(confirmed_map.items()):
                gname_c = gramps_id_map.get(gid_c, gid_c)
                with st.expander(f"✅ {wit_c} ↔ {gname_c} ({gid_c})", expanded=False):
                    # Mostrar ficha completa de la persona confirmada
                    gp_c = None
                    for pers_list in gi_rich.values():
                        for p in pers_list:
                            if p['id'] == gid_c:
                                gp_c = p
                                break
                        if gp_c: break
                    if gp_c:
                        birth_str = str(gp_c.get('birth_year') or '—')
                        if gp_c.get('birth_place'): birth_str += f", {gp_c['birth_place']}"
                        death_str = str(gp_c.get('death_year') or '—')
                        if gp_c.get('death_place'): death_str += f", {gp_c['death_place']}"
                        st.write(f"· {t('testigos_arbol_nacimiento')}: {birth_str}")
                        st.write(f"· {t('testigos_arbol_defuncion')}: {death_str}")
                        if gp_c.get('parents'):
                            st.write(f"· {t('testigos_arbol_padres')}: {', '.join(gp_c['parents'])}")
                        if gp_c.get('spouses'):
                            st.write(f"· {t('testigos_arbol_conyuges')}: {', '.join(gp_c['spouses'])}")
                        if gp_c.get('children'):
                            st.write(f"· {t('testigos_arbol_hijos')}: {', '.join(gp_c['children'][:8])}")
                        if gp_c.get('notes'):
                            st.markdown(f"*{t('testigos_arbol_notas')}:*")
                            for note_txt in gp_c['notes']:
                                st.caption(note_txt)
                    if st.button(t("testigos_arbol_revertir"), key=f"ta_rev_conf_{wit_c}"):
                        conf2 = load_confirmations()
                        conf2.get('gramps_links', {}).get('confirmed', {}).pop(wit_c, None)
                        save_confirmations(conf2)
                        st.rerun()

    # ── TAB DESCARTADOS ───────────────────────────────────────────────────────
    with tab_desc:
        if not discarded_set:
            st.info(t("testigos_arbol_sin_descartados"))
        else:
            for i, (wit_d, gid_d) in enumerate(sorted(discarded_set)):
                gname_d = gramps_id_map.get(gid_d, gid_d)
                col_d1, col_d2 = st.columns([4, 1])
                col_d1.write(f"❌ **{wit_d}** ↔ {gname_d} ({gid_d})")
                if col_d2.button(t("testigos_arbol_revertir"), key=f"ta_rev_desc_{i}"):
                    conf2 = load_confirmations()
                    disc2 = conf2.get('gramps_links', {}).get('discarded', [])
                    if [wit_d, gid_d] in disc2:
                        disc2.remove([wit_d, gid_d])
                    conf2['gramps_links']['discarded'] = disc2
                    save_confirmations(conf2)
                    st.rerun()



# ─────────────────────────────────────────────────────────────────────────────
# PÁGINA: Posibles familiares por coincidencia de apellido
# ─────────────────────────────────────────────────────────────────────────────

def _save_possible_relatives(relatives):
    """Persiste los posibles familiares en confirmed_links.json."""
    conf_data = load_confirmations()
    conf_data["possible_relatives"] = possible_relatives_to_dict(relatives)
    save_confirmations(conf_data)


# ─────────────────────────────────────────────────────────────────────────────
# PÁGINA: Posibles familiares por coincidencia de apellido
# ─────────────────────────────────────────────────────────────────────────────

def page_posibles_familiares(dataset: WitnessDataset):
    df = dataset.df
    by_witness = dataset.by_witness
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    st.title(t("pfam_titulo"))
    st.info(t("pfam_descripcion"))

    # ── Sidebar: configuración ────────────────────────────────────────────────
    lang = get_lang()
    systems_list = list_systems_for_selector()
    system_options = [s[1] if lang == "es" else s[2] for s in systems_list]
    system_codes   = [s[0] for s in systems_list]

    saved_system = st.session_state.get("pfam_system_code", "es")
    default_idx = system_codes.index(saved_system) if saved_system in system_codes else 0

    chosen_system_label = st.sidebar.selectbox(
        t("pfam_sistema_label"),
        system_options,
        index=default_idx,
        help=t("pfam_sistema_help"),
        key="pfam_system_selector",
    )
    chosen_system_idx = system_options.index(chosen_system_label)
    chosen_system_code = system_codes[chosen_system_idx]
    st.session_state["pfam_system_code"] = chosen_system_code

    system_obj = get_system(chosen_system_code)
    notes = system_obj.genealogical_notes_es if lang == "es" else system_obj.genealogical_notes_en
    with st.sidebar.expander(t("pfam_notas_sistema"), expanded=False):
        st.markdown(notes)

    conf_options_codes = [CONFIDENCE_VERY_LOW, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH]
    conf_labels_map = CONFIDENCE_LABELS_ES if lang == "es" else CONFIDENCE_LABELS_EN
    conf_options_labels = [conf_labels_map[c] for c in conf_options_codes]
    saved_min_conf = st.session_state.get("pfam_min_conf", CONFIDENCE_LOW)
    min_conf_default = conf_options_codes.index(saved_min_conf) if saved_min_conf in conf_options_codes else 1

    chosen_min_conf_label = st.sidebar.selectbox(
        t("pfam_min_confianza"),
        conf_options_labels,
        index=min_conf_default,
        key="pfam_min_conf_selector",
    )
    chosen_min_conf = conf_options_codes[conf_options_labels.index(chosen_min_conf_label)]
    st.session_state["pfam_min_conf"] = chosen_min_conf

    only_unreviewed = st.sidebar.checkbox(
        t("pfam_solo_sin_revisar"),
        value=st.session_state.get("pfam_only_unreviewed", False),
        key="pfam_only_unreviewed",
    )
    recalculate = st.sidebar.button(t("pfam_recalcular"), key="pfam_recalcular_btn")

    # df es el global cargado por render_page() antes de llamar a esta función
    conf_data    = load_confirmations()
    gramps_links = conf_data.get("gramps_links", {})
    existing_flags = possible_relatives_from_dict(conf_data.get("possible_relatives", {}))

    # Disparador: recalcular si cambia sistema, confianza mínima o se pulsa botón
    cache_key = f"pfam_cache_{chosen_system_code}_{chosen_min_conf}"
    if recalculate or cache_key not in st.session_state:
        with st.spinner("Calculando…"):
            kinship_map = {}
            # gramps_links_enriched: combina confirmaciones manuales + búsqueda
            # automática por nombre normalizado en el índice GRAMPS.
            # Esto garantiza que "En árbol" se muestre aunque el investigador
            # no haya vinculado manualmente el testigo en "Testigos en árbol".
            gramps_links_enriched = {
                "confirmed": dict(gramps_links.get("confirmed", {})),
                "discarded": gramps_links.get("discarded", []),
            }
            try:
                gramps_idx = _get_gramps_index_rich(str(GRAMPS_PATH))
                gramps_id_map_local = {
                    p["id"]: p["name"]
                    for pl in gramps_idx.values() for p in pl if p.get("id")
                }
                by_wit_local = {}
                for _, row in df.iterrows():
                    wc = str(row.get("witness_canon", "") or "")
                    if wc:
                        by_wit_local.setdefault(wc, []).append(row.to_dict())

                # Para cada testigo no vinculado manualmente, buscar en el índice
                # GRAMPS por nombre normalizado (lookup directo O(1)).
                already_confirmed = set(gramps_links_enriched["confirmed"].keys())
                for wc in by_wit_local:
                    if wc in already_confirmed:
                        continue
                    wc_norm = normalize(wc)
                    candidates = gramps_idx.get(wc_norm, [])
                    if candidates:
                        wit_events = by_wit_local[wc]
                        # Si hay varios homónimos en el árbol, elegir el más plausible
                        # descartando los que sean temporal o geográficamente imposibles.
                        plausible = [c for c in candidates
                                     if _is_plausible_gramps_match(wit_events, c,
                                                                    gramps_idx=gramps_idx)]
                        if plausible:
                            gramps_links_enriched["confirmed"][wc] = plausible[0].get("id", "")

                # Construir kinship_map indexado por event_id para mayor precisión.
                # find_kinship_with_subject devuelve [{subj_name, kinship_label, degree, ...}]
                # por cada evento del testigo; mapeamos cada event_id a su label.
                for wc, gid in gramps_links_enriched["confirmed"].items():
                    if wc not in by_wit_local or not gid:
                        continue
                    gp = gramps_idx.get(normalize(gramps_id_map_local.get(gid, "")), [])
                    if not gp:
                        continue
                    kin_entries = find_kinship_with_subject(gp[0], by_wit_local[wc], gramps_idx)
                    if not kin_entries:
                        continue
                    # Indexar cada entrada por event_id (viene en el dict de evento)
                    for ev in by_wit_local[wc]:
                        ev_id = str(ev.get("event_id", ""))
                        ev_subj = str(ev.get("subj_name", "") or "")
                        if not ev_id:
                            continue
                        # Buscar la entrada kinship cuyo subj_name coincida con este evento
                        for ke in kin_entries:
                            ks = str(ke.get("subj_name", ""))
                            if (normalize(ks) == normalize(ev_subj) or
                                    (ks and normalize(ks) in normalize(ev_subj))):
                                kinship_map[ev_id] = ke.get("kinship_label", "")
                                break
                        else:
                            # Sin coincidencia exacta: usar la primera entrada disponible
                            # (el parentesco más probable para este testigo)
                            if kin_entries:
                                kinship_map[ev_id] = kin_entries[0].get("kinship_label", "")
            except Exception:
                gramps_links_enriched = gramps_links
                kinship_map = {}

            relatives = detect_possible_relatives(
                df=df,
                system_code=chosen_system_code,
                gramps_links=gramps_links_enriched,
                kinship_map=kinship_map,
                min_confidence=chosen_min_conf,
                only_unreviewed=False,   # filtramos después para mantener conteo correcto
                existing_flags=existing_flags,
            )
        st.session_state[cache_key] = relatives
        st.session_state["pfam_last_relatives"] = relatives
    else:
        relatives = st.session_state.get("pfam_last_relatives",
                                          st.session_state.get(cache_key, {}))

    # Aplicar filtro de revisados en memoria
    display_relatives = relatives
    if only_unreviewed:
        display_relatives = {eid: r for eid, r in relatives.items() if not r.reviewed}

    n_total   = len(relatives)
    n_rev     = sum(1 for r in relatives.values() if r.reviewed)
    n_pending = n_total - n_rev
    st.caption(t("pfam_n_encontrados", n=n_total, rev=n_rev, pen=n_pending))

    if not display_relatives:
        st.warning(t("pfam_sin_resultados"))
        return

    st.markdown(t("pfam_info_confianza"))

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_lista, tab_testigo, tab_apellido, tab_rev = st.tabs([
        t("pfam_tab_lista"),
        t("pfam_tab_por_testigo"),
        t("pfam_tab_por_apellido"),
        t("pfam_tab_revision"),
    ])

    # ── TAB 1: Lista completa ─────────────────────────────────────────────────
    with tab_lista:
        pos_label = {0: t("pfam_pos_primario"), 1: t("pfam_pos_secundario")}

        def _conf_badge(conf: str) -> str:
            color = CONFIDENCE_COLORS.get(conf, "#ccc")
            label = (CONFIDENCE_LABELS_ES if lang == "es" else CONFIDENCE_LABELS_EN).get(conf, conf)
            return f'<span style="background:{color};padding:2px 8px;border-radius:4px;font-size:.85em">{label}</span>'

        rows = []
        for eid, r in sorted(display_relatives.items(),
                              key=lambda x: x[1].score, reverse=True):
            rev_label = ""
            if r.review_result == "confirmed_relative":
                rev_label = t("pfam_resultado_confirmado")
            elif r.review_result == "discarded":
                rev_label = t("pfam_resultado_descartado")
            elif r.reviewed:
                rev_label = "—"
            else:
                rev_label = t("pfam_resultado_pendiente")

            rows.append({
                t("pfam_col_testigo"):           r.witness_canon,
                t("pfam_col_sujeto"):            r.subj_name,
                t("pfam_col_apellido_testigo"):  r.witness_surname,
                t("pfam_col_apellido_sujeto"):   r.subj_surname,
                t("pfam_col_pos_testigo"):       pos_label.get(r.witness_surname_position,
                                                               t("pfam_pos_adicional")),
                t("pfam_col_pos_sujeto"):        pos_label.get(r.subj_surname_position,
                                                               t("pfam_pos_adicional")),
                t("pfam_col_similitud"):         f"{r.surname_similarity:.0%}",
                t("pfam_col_score"):             f"{r.score:.2f}",
                t("pfam_col_confianza"):         (CONFIDENCE_LABELS_ES if lang == "es"
                                                  else CONFIDENCE_LABELS_EN).get(r.confidence, r.confidence),
                t("pfam_col_en_arbol"):          "✓" if r.gramps_candidate_id else "—",
                t("pfam_col_gramps_id"):         r.gramps_candidate_id or "—",
                t("pfam_col_parentesco"):        r.kinship_label or "—",
                t("pfam_col_fecha"):             r.event_date[:10],
                t("pfam_col_tipo"):              r.event_type,
                t("pfam_col_lugar"):             r.event_place,
                t("pfam_col_resultado"):         rev_label,
                "event_id":                      eid,
            })

        import pandas as _pd
        df_display = _pd.DataFrame(rows)
        st.dataframe(
            df_display.drop(columns=["event_id"], errors="ignore"),
            use_container_width=True,
            height=500,
        )

        csv_bytes = df_display.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("pfam_exportar_csv"),
            data=csv_bytes,
            file_name="posibles_familiares.csv",
            mime="text/csv",
        )

    # ── TAB 2: Por testigo ───────────────────────────────────────────────────
    with tab_testigo:
        rows_wit = aggregate_by_witness(display_relatives)
        if not rows_wit:
            st.warning(t("pfam_sin_resultados"))
        else:
            import pandas as _pd
            df_wit = _pd.DataFrame(rows_wit)
            rename_wit = {
                "witness_canon":    t("pfam_col_testigo"),
                "n_events":         t("pfam_col_wit_n_eventos"),
                "n_families":       t("pfam_col_wit_n_familias"),
                "max_confidence":   t("pfam_col_wit_max_conf"),
                "max_score":        t("pfam_col_wit_max_score"),
                "shared_surnames":  t("pfam_col_wit_apellidos"),
                "kinship_in_tree":  t("pfam_col_wit_arbol"),
                "confirmed":        t("pfam_col_wit_confirmados"),
                "discarded":        t("pfam_col_wit_descartados"),
                "pending":          t("pfam_col_wit_pendientes"),
            }
            # Traducir etiquetas de confianza
            conf_labels = CONFIDENCE_LABELS_ES if lang == "es" else CONFIDENCE_LABELS_EN
            df_wit["max_confidence"] = df_wit["max_confidence"].map(
                lambda c: conf_labels.get(c, c)
            )
            df_wit = df_wit.rename(columns=rename_wit)
            st.dataframe(df_wit, use_container_width=True, height=450)

    # ── TAB 3: Por apellido ──────────────────────────────────────────────────
    with tab_apellido:
        rows_sn = aggregate_by_surname(display_relatives)
        if not rows_sn:
            st.warning(t("pfam_sin_resultados"))
        else:
            import pandas as _pd
            df_sn = _pd.DataFrame(rows_sn)
            rename_sn = {
                "surname":          t("pfam_col_sn_apellido"),
                "n_entries":        t("pfam_col_sn_entradas"),
                "n_witnesses":      t("pfam_col_sn_testigos"),
                "n_subjects":       t("pfam_col_sn_sujetos"),
                "max_confidence":   t("pfam_col_sn_max_conf"),
                "year_range":       t("pfam_col_sn_rango"),
            }
            conf_labels = CONFIDENCE_LABELS_ES if lang == "es" else CONFIDENCE_LABELS_EN
            df_sn["max_confidence"] = df_sn["max_confidence"].map(
                lambda c: conf_labels.get(c, c)
            )
            df_sn = df_sn.rename(columns=rename_sn)
            st.dataframe(df_sn, use_container_width=True, height=450)

    # ── TAB 4: Revisión ──────────────────────────────────────────────────────
    with tab_rev:
        st.subheader(t("pfam_revision_titulo"))
        pending_items = [
            (eid, r) for eid, r in display_relatives.items()
            if not r.reviewed
        ]
        if not pending_items:
            st.success(t("pfam_revision_sin_pendientes"))
        else:
            for eid, r in sorted(pending_items, key=lambda x: x[1].score, reverse=True):
                conf_color = CONFIDENCE_COLORS.get(r.confidence, "#ccc")
                conf_label = (CONFIDENCE_LABELS_ES if lang == "es"
                              else CONFIDENCE_LABELS_EN).get(r.confidence, r.confidence)
                with st.expander(
                    f"**{r.witness_canon}** → {r.subj_name}  "
                    f"| {r.witness_surname} ≈ {r.subj_surname}  "
                    f"| score {r.score:.2f}",
                    expanded=False,
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.markdown(f"**{t('pfam_revision_testigo')}:** {r.witness_canon}")
                    c1.markdown(f"**{t('pfam_revision_sujeto')}:** {r.subj_name}")
                    c2.markdown(f"**{t('pfam_revision_fecha')}:** {r.event_date[:10]} · {r.event_place}")
                    c2.markdown(
                        f"**{t('pfam_revision_apellido')}:** "
                        f"{r.witness_surname} (pos.{r.witness_surname_position+1}) ≈ "
                        f"{r.subj_surname} (pos.{r.subj_surname_position+1})"
                    )
                    c3.markdown(
                        f"**{t('pfam_revision_score')}:** {r.score:.2f}  "
                        f"<span style='background:{conf_color};padding:2px 7px;"
                        f"border-radius:4px;font-size:.85em'>{conf_label}</span>",
                        unsafe_allow_html=True,
                    )
                    c3.markdown(
                        f"**{t('pfam_revision_arbol')}:** "
                        f"{'✓ ' + (r.gramps_candidate_id or '') if r.gramps_candidate_id else '—'}"
                    )
                    if r.kinship_label:
                        c3.markdown(f"**{t('pfam_revision_parentesco')}:** {r.kinship_label}")

                    col_confirm, col_discard = st.columns(2)
                    if col_confirm.button(
                        t("pfam_revision_confirmar"),
                        key=f"pfam_confirm_{eid}",
                        type="primary",
                    ):
                        relatives[eid].reviewed = True
                        relatives[eid].review_result = "confirmed_relative"
                        _save_possible_relatives(relatives)
                        st.success(t("pfam_revision_guardado"))
                        st.rerun()

                    if col_discard.button(
                        t("pfam_revision_descartar"),
                        key=f"pfam_discard_{eid}",
                    ):
                        relatives[eid].reviewed = True
                        relatives[eid].review_result = "discarded"
                        _save_possible_relatives(relatives)
                        st.success(t("pfam_revision_guardado"))
                        st.rerun()


def _save_possible_relatives(relatives):
    """Persiste los posibles familiares en confirmed_links.json."""
    conf_data = load_confirmations()
    conf_data["possible_relatives"] = possible_relatives_to_dict(relatives)
    save_confirmations(conf_data)


# ─────────────────────────────────────────────────────────────────────────────
# Página: Resolución árbol–testigos
# ─────────────────────────────────────────────────────────────────────────────

def page_identity_resolution(dataset: WitnessDataset):
    """
    Motor de identidad: cruza todos los testigos con todas las personas del árbol GRAMPS
    usando el modelo bayesiano existente para generar una cola de trabajo priorizada.
    """
    df = dataset.df
    by_witness = dataset.by_witness
    places_index = dataset.places_index
    gramps_index = dataset.gramps_index
    gramps_id_map = dataset.gramps_id_map
    subj_id_map = dataset.subj_id_map
    from modules.testigos.identity_resolution import (
        build_candidate_pairs, score_candidate_pairs, prioritize_pairs,
        load_resolution_results, save_resolution_results, CandidatePair,
    )
    import dataclasses
    from datetime import datetime as _dt

    st.title(t("ir_title"))
    st.caption(t("ir_description"))

    # ── Verificar fuente de datos GRAMPS ─────────────────────────────────────
    _ir_override_db = st.session_state.get("_gramps_web_db_override")
    content_bytes = st.session_state.get("shared_gramps_bytes")
    if not content_bytes and _ir_override_db is None:
        st.info(t("ir_no_file"))
        return

    # ── Configuración en sidebar ──────────────────────────────────────────────
    st.sidebar.markdown("---")
    name_threshold = st.sidebar.slider("Similitud mínima de nombre (%)", 50, 95, 65, step=5, key="ir_name_threshold") / 100.0
    time_window    = st.sidebar.slider(t("ir_time_window_label"), 10, 100, 50, step=5, key="ir_time_window")
    geo_km         = st.sidebar.slider(t("ir_geo_km_label"), 10, 500, 150, step=10, key="ir_geo_km")

    # ── Panel de control ──────────────────────────────────────────────────────
    col_run, col_save = st.columns([2, 1])
    with col_run:
        run_clicked = st.button(t("ir_run_btn"), type="primary")
    with col_save:
        save_clicked = st.button(t("ir_save_btn"))

    # ── Ejecutar análisis ─────────────────────────────────────────────────────
    if run_clicked:
        with st.spinner(t("ir_running")):
            try:
                if _ir_override_db is not None:
                    db = _ir_override_db
                else:
                    db = _parse_gramps_shared(content_bytes)
            except Exception as e:
                st.error(f"Error al parsear GRAMPS: {e}")
                return

            _store.load()
            conf_data = _store.get_all()
            confirmed = set(conf_data.get('gramps_links', {}).get('confirmed', {}).keys())
            discarded = set(conf_data.get('gramps_links', {}).get('discarded', []))

            pairs_raw, truncated = build_candidate_pairs(
                by_witness        = by_witness,
                gramps_db         = db,
                already_confirmed = confirmed,
                already_discarded = discarded,
                time_window       = time_window,
                geo_km            = geo_km,
                name_threshold    = name_threshold,
            )

            if truncated:
                st.warning(t("ir_truncated_warning").format(n=5_000))

            scored      = score_candidate_pairs(pairs_raw, by_witness, db, places_index)
            prioritized = prioritize_pairs(scored)

            st.session_state['ir_pairs']       = [dataclasses.asdict(p) for p in prioritized]
            st.session_state['ir_current_idx'] = None
            st.session_state['ir_run_ts']      = _dt.now().strftime('%Y-%m-%d %H:%M')

    # ── Guardar resultados ────────────────────────────────────────────────────
    if save_clicked:
        raw_pairs = st.session_state.get('ir_pairs', [])
        if raw_pairs:
            fields = set(CandidatePair.__dataclass_fields__.keys())
            objs = [CandidatePair(**{k: v for k, v in p.items() if k in fields})
                    for p in raw_pairs]
            ok = save_resolution_results(objs, IDENTITY_RESOLUTION_FILE)
            st.success(t("ir_saved_ok")) if ok else st.error(t("ir_saved_error"))
        else:
            st.info(t("ir_no_data"))

    # ── Mostrar resultados ────────────────────────────────────────────────────
    pairs_data = st.session_state.get('ir_pairs', [])
    run_ts     = st.session_state.get('ir_run_ts')

    if not pairs_data:
        saved = load_resolution_results(IDENTITY_RESOLUTION_FILE)
        if saved:
            pairs_data = saved
            st.session_state['ir_pairs'] = saved
        else:
            st.info(t("ir_no_data"))
            return

    # Métricas (excluye pares ya gestionados en sesión)
    n_auto   = sum(1 for p in pairs_data if p.get('recommendation') == 'auto_merge')
    n_review = sum(1 for p in pairs_data if p.get('recommendation') == 'review')
    n_diff   = sum(1 for p in pairs_data if p.get('recommendation') == 'different')
    n_confirmed = sum(1 for p in pairs_data if p.get('recommendation') == 'confirmed')
    n_discarded = sum(1 for p in pairs_data if p.get('recommendation') == 'discarded')
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("ir_metric_auto"),      n_auto)
    c2.metric(t("ir_metric_review"),    n_review)
    c3.metric(t("ir_metric_discarded"), n_diff)
    c4.metric("✅ Gestionados", n_confirmed + n_discarded)
    if run_ts:
        st.caption(f"Último análisis: {run_ts}")

    # Filtro
    filter_opt = st.radio(
        "Filtro",
        [t("ir_filter_all"), t("ir_filter_auto"), t("ir_filter_review")],
        horizontal=True,
        key="ir_filter",
        label_visibility="collapsed",
    )
    _active = [p for p in pairs_data if p.get('recommendation') not in ('confirmed', 'discarded')]
    if filter_opt == t("ir_filter_auto"):
        filtered = [p for p in _active if p.get('recommendation') == 'auto_merge']
    elif filter_opt == t("ir_filter_review"):
        filtered = [p for p in _active if p.get('recommendation') == 'review']
    else:
        filtered = _active

    if not filtered:
        st.info(t("ir_no_data"))
        return

    # Tabla de pares
    import pandas as _pd
    table_rows = []
    for i, p in enumerate(filtered):
        prob = p.get('probability', 0.0)
        table_rows.append({
            '_idx':              i,
            t("ir_col_witness"): p.get('witness_name', ''),
            t("ir_col_person"):  p.get('person_name', ''),
            t("ir_col_prob"):    f"{prob:.0%}",
            t("ir_col_rec"):     p.get('recommendation', ''),
        })
    df_table = _pd.DataFrame(table_rows)

    selected_rows = st.dataframe(
        df_table.drop(columns=['_idx']),
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="ir_table",
    )

    sel_indices = []
    if hasattr(selected_rows, 'selection'):
        sel_indices = selected_rows.selection.get('rows', [])
    if sel_indices:
        st.session_state['ir_current_idx'] = table_rows[sel_indices[0]]['_idx']

    cur_idx = st.session_state.get('ir_current_idx')

    if cur_idx is not None and 0 <= cur_idx < len(filtered):
        pair = filtered[cur_idx]
        st.markdown("---")
        st.subheader(
            f"{t('ir_detail_witness')}: **{pair.get('witness_name', '')}**  →  "
            f"{t('ir_detail_person')}: **{pair.get('person_name', '')}**"
        )

        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(f"**{t('ir_detail_witness')}**")
            st.write(f"Años: {pair.get('witness_year_min') or '?'} – {pair.get('witness_year_max') or '?'}")
            places_w = pair.get('witness_places') or []
            st.write(f"Lugares: {', '.join(places_w[:5]) if places_w else '—'}")
        with col_r:
            st.markdown(f"**{t('ir_detail_person')}**")
            st.write(f"Nacimiento: {pair.get('person_birth_year') or '—'}  |  Muerte: {pair.get('person_death_year') or '—'}")
            places_p = pair.get('person_places') or []
            st.write(f"Lugares: {', '.join(places_p[:5]) if places_p else '—'}")
            st.write(f"GRAMPS ID: {pair.get('gramps_id', '')}")

        # ── Ficha enriquecida de la persona en el árbol ───────────────────────
        gramps_path = get_active_gramps_path()
        if gramps_path:
            gi_rich = _get_gramps_index_rich(gramps_path)
            gp_detail = None
            for pers_list in gi_rich.values():
                for p_ in pers_list:
                    if p_.get('id') == pair.get('gramps_id'):
                        gp_detail = p_
                        break
                if gp_detail:
                    break
            if gp_detail:
                extra_lines = []
                if gp_detail.get('parents'):
                    extra_lines.append(f"· {t('testigos_arbol_padres')}: {', '.join(gp_detail['parents'])}")
                if gp_detail.get('spouses'):
                    extra_lines.append(f"· {t('testigos_arbol_conyuges')}: {', '.join(gp_detail['spouses'])}")
                if gp_detail.get('children'):
                    extra_lines.append(f"· {t('testigos_arbol_hijos')}: {', '.join(gp_detail['children'][:8])}")
                if extra_lines:
                    with st.expander(t('testigos_arbol_persona'), expanded=False):
                        for line in extra_lines:
                            st.write(line)
                        if gp_detail.get('notes'):
                            st.markdown(f"*{t('testigos_arbol_notas')}:*")
                            for note_txt in gp_detail['notes']:
                                st.caption(note_txt)

                # ── Parentesco con sujetos de los eventos ─────────────────────
                kin_links = find_kinship_with_subject(
                    gp_detail,
                    by_witness.get(pair.get('witness_name', ''), []),
                    gi_rich,
                )
                if kin_links:
                    st.markdown(f"**{t('testigos_arbol_parentesco')}:**")
                    for kl in kin_links[:8]:
                        degree_str = f" *(gr. {kl['degree']})*" if kl['degree'] > 0 else ""
                        if kl.get('source') == 'note':
                            st.write(f"· {kl['kinship_label']}{degree_str} de **{kl['subj_name']}** "
                                     f"*(🗒 {t('kinship_from_note')}: \"{kl['note_mention']}\")*")
                        else:
                            st.write(f"· {kl['kinship_label']}{degree_str} de **{kl['subj_name']}**")

        br = pair.get('bayesian_result') or {}
        fc = br.get('feature_contributions') or {}
        if fc:
            try:
                import plotly.graph_objects as _go
                labels = list(fc.keys())
                values = [float(fc[k]) for k in labels]
                fig = _go.Figure(_go.Bar(x=labels, y=values, marker_color='steelblue'))
                fig.update_layout(
                    height=220,
                    margin=dict(l=0, r=0, t=20, b=0),
                    yaxis=dict(range=[0, 1]),
                    title_text="Contribución por factor",
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                st.write(fc)

        explanation = br.get('explanation', '')
        if explanation:
            st.info(explanation)

        pending_note = st.text_area(
            "Nota de identificación (opcional)",
            value=st.session_state.get(f"ir_pending_note_{cur_idx}", ""),
            key=f"ir_pending_note_{cur_idx}",
            placeholder="Ej: Mismo Diego Yllescas que aparece como padrino en el bautismo de 1681.",
            height=80,
        )

        b1, b2 = st.columns(2)
        with b1:
            if st.button(t("ir_confirm_btn"), key=f"ir_confirm_{cur_idx}", type="primary"):
                _store.load()
                _store.link_to_gramps(
                    pair.get('witness_name', ''),
                    pair.get('pid', ''),
                    pair.get('person_name', ''),
                )
                if pending_note.strip():
                    _store.set_link_note(pair.get('witness_name', ''), pending_note.strip())
                _store.save()
                witness_name = pair.get('witness_name', '')
                ir_pairs = st.session_state.get('ir_pairs', [])
                for p in ir_pairs:
                    if p.get('witness_name') == witness_name and p.get('pid') == pair.get('pid'):
                        p['recommendation'] = 'confirmed'
                        break
                st.session_state['ir_pairs'] = ir_pairs
                st.session_state['ir_current_idx'] = None
                st.rerun()
        with b2:
            if st.button(t("ir_discard_btn"), key=f"ir_discard_{cur_idx}"):
                _store.load()
                _store.discard_gramps_link(pair.get('witness_name', ''))
                _store.save()
                witness_name = pair.get('witness_name', '')
                ir_pairs = st.session_state.get('ir_pairs', [])
                for p in ir_pairs:
                    if p.get('witness_name') == witness_name and p.get('pid') == pair.get('pid'):
                        p['recommendation'] = 'discarded'
                        break
                st.session_state['ir_pairs'] = ir_pairs
                st.session_state['ir_current_idx'] = None
                st.rerun()

    # ── Sección de descartados (sesión actual) ────────────────────────────────
    discarded_pairs = [p for p in pairs_data if p.get('recommendation') == 'discarded']
    if discarded_pairs:
        with st.expander(f"Descartados en esta sesión ({len(discarded_pairs)})", expanded=False):
            for i, p in enumerate(discarded_pairs):
                col_name, col_btn = st.columns([4, 1])
                col_name.write(f"**{p.get('witness_name', '')}** → {p.get('person_name', '')}  "
                               f"({p.get('probability', 0):.0%})")
                if col_btn.button("Recuperar", key=f"ir_restore_{i}"):
                    _store.load()
                    _store.restore_gramps_link(p.get('witness_name', ''))
                    _store.save()
                    ir_pairs = st.session_state.get('ir_pairs', [])
                    for q in ir_pairs:
                        if q.get('witness_name') == p.get('witness_name') and q.get('pid') == p.get('pid'):
                            q['recommendation'] = 'review'
                            break
                    st.session_state['ir_pairs'] = ir_pairs
                    st.rerun()

    # ── Confirmados persistidos ───────────────────────────────────────────────
    st.markdown("---")
    _store.load()
    confirmed_map = _store.get_all().get('gramps_links', {}).get('confirmed', {})
    if confirmed_map:
        with st.expander(f"✅ Confirmados ({len(confirmed_map)})", expanded=False):
            gramps_path = get_active_gramps_path()
            gi_rich_c = _get_gramps_index_rich(gramps_path) if gramps_path else {}
            for wit_c, link_data in list(confirmed_map.items()):
                pid_c   = link_data.get('pid', '') if isinstance(link_data, dict) else str(link_data)
                pname_c = link_data.get('name', pid_c) if isinstance(link_data, dict) else gramps_id_map.get(str(link_data), str(link_data))
                with st.expander(f"✅ {wit_c}  →  {pname_c}", expanded=False):
                    # Ficha de la persona confirmada
                    gp_c = None
                    for pers_list in gi_rich_c.values():
                        for p_ in pers_list:
                            if p_.get('id') == pid_c:
                                gp_c = p_
                                break
                        if gp_c:
                            break
                    if gp_c:
                        birth_str = str(gp_c.get('birth_year') or '—')
                        if gp_c.get('birth_place'):
                            birth_str += f", {gp_c['birth_place']}"
                        death_str = str(gp_c.get('death_year') or '—')
                        if gp_c.get('death_place'):
                            death_str += f", {gp_c['death_place']}"
                        st.write(f"· {t('testigos_arbol_nacimiento')}: {birth_str}")
                        st.write(f"· {t('testigos_arbol_defuncion')}: {death_str}")
                        if gp_c.get('parents'):
                            st.write(f"· {t('testigos_arbol_padres')}: {', '.join(gp_c['parents'])}")
                        if gp_c.get('spouses'):
                            st.write(f"· {t('testigos_arbol_conyuges')}: {', '.join(gp_c['spouses'])}")
                        if gp_c.get('children'):
                            st.write(f"· {t('testigos_arbol_hijos')}: {', '.join(gp_c['children'][:8])}")
                        if gp_c.get('notes'):
                            st.markdown(f"*{t('testigos_arbol_notas')}:*")
                            for note_txt in gp_c['notes']:
                                st.caption(note_txt)

                    # Nota de identificación editable
                    current_note = link_data.get('note', '') if isinstance(link_data, dict) else ''
                    note_key = f"ir_conf_note_{wit_c}"
                    edited_note = st.text_area(
                        "Nota de identificación",
                        value=st.session_state.get(note_key, current_note),
                        key=note_key,
                        placeholder="Añade aquí el razonamiento o evidencia de la identificación.",
                        height=80,
                    )
                    if st.button("Guardar nota", key=f"ir_save_note_{wit_c}"):
                        _store.load()
                        _store.set_link_note(wit_c, edited_note.strip())
                        _store.save()
                        st.success("Nota guardada.")
                        st.rerun()

                    if st.button(t("testigos_arbol_revertir"), key=f"ir_rev_conf_{wit_c}"):
                        _store.load()
                        _store.restore_gramps_link(wit_c)
                        _store.get_all().get('gramps_links', {}).get('confirmed', {}).pop(wit_c, None)
                        _store.save()
                        st.rerun()


