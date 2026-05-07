"""
Funciones de análisis puras del módulo de testigos.
Sin dependencias de Streamlit ni de estado global.
Las funciones que antes residían en testigos/app.py se agrupan aquí para facilitar
su reutilización y pruebas unitarias.
"""

import re
import math
from collections import Counter, defaultdict

import pandas as pd

from modules.shared.utils import normalize_name, haversine_km, year_from_date_str

normalize = normalize_name  # alias para compatibilidad con lógica original

try:
    import networkx as nx
except ImportError:
    nx = None

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ_OK = True
except ImportError:
    _fuzz = None
    _RAPIDFUZZ_OK = False

try:
    import jellyfish
    JELLYFISH_OK = True
except ImportError:
    jellyfish = None
    JELLYFISH_OK = False

try:
    from dateutil import parser as _dateutil_parser
    def _parse_gramps_date(val):
        if val is None or (isinstance(val, float) and val != val):
            return None
        s = str(val).strip()
        if not s:
            return None
        try:
            import pandas as _pd
            return _pd.Timestamp(_dateutil_parser.parse(s))
        except Exception:
            return None
except ImportError:
    def _parse_gramps_date(val):
        if val is None:
            return None
        import pandas as _pd
        return _pd.to_datetime(val, errors='coerce')

def _year_series(series):
    """Return a Series of int years from GRAMPS date strings."""
    return series.map(year_from_date_str).astype('Int64')

_NOBLE_PARTICLES = {'de', 'del', 'de la', 'de los', 'de las', 'von', 'van', 'la', 'los', 'las', 'el', 'y'}

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
            'label': family_label(fn),
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
            family_label(v)
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
        _overrides = st.session_state.get('note_category_overrides', {})
    if raw in _overrides:
        return _overrides[raw]

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
