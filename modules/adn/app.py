# modules/adn/app.py
# Módulo "ADN & Genética" — Rayzes
# Tres funcionalidades: Análisis de Fundadores, Predicción de ADN compartido,
# y trazado de Línea Materna / Paterna pura.

import streamlit as st
import pandas as pd
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from collections import deque

from translations import t, get_lang
from modules.shared.gramps_parser import parse_gramps as _parse_gramps_shared


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de grafo — copiadas de modules/consanguinidad/app.py para evitar
# dependencia cruzada entre módulos.
# ─────────────────────────────────────────────────────────────────────────────

def build_graph(people, families):
    G = nx.DiGraph()
    for pid, pdata in people.items():
        G.add_node(pid, **pdata)
    for fid, fdata in families.items():
        parents = []
        if fdata.get('husband'):
            parents.append(fdata['husband'])
        if fdata.get('wife'):
            parents.append(fdata['wife'])
        for child in fdata.get('children', []):
            for p in parents:
                if p and p in G.nodes():
                    G.add_edge(p, child)
    return G


def ancestors_with_distance(G, start_id, max_gen=10):
    dist = {}
    q = deque()
    for p in G.predecessors(start_id):
        dist[p] = 1
        q.append((p, 1))
    while q:
        node, d = q.popleft()
        if d >= max_gen:
            continue
        for par in G.predecessors(node):
            if par not in dist or dist[par] > d + 1:
                dist[par] = d + 1
                q.append((par, d + 1))
    return dist


def find_paths_to_ancestor(G, start, target, max_gen=12, multi=False):
    if start == target:
        return [[start]] if multi else [start]

    if not multi:
        parent_map = {start: None}
        q = deque([(start, 0)])
        while q:
            current, depth = q.popleft()
            if depth >= max_gen:
                continue
            for parent in G.predecessors(current):
                if parent in parent_map:
                    continue
                parent_map[parent] = current
                if parent == target:
                    path = []
                    node = target
                    while node is not None:
                        path.append(node)
                        node = parent_map[node]
                    path.reverse()
                    return path
                q.append((parent, depth + 1))
        return None

    results = []
    q = deque([(start, (start,))])
    while q:
        current, path = q.popleft()
        if len(path) - 1 >= max_gen:
            continue
        for parent in G.predecessors(current):
            if parent in path:
                continue
            new_path = path + (parent,)
            if parent == target:
                results.append(list(new_path))
                continue
            q.append((parent, new_path))
    return results


def find_common_ancestors(G, a_id, b_id, max_gen=12):
    if a_id not in G.nodes() or b_id not in G.nodes():
        return []
    anc_a = ancestors_with_distance(G, a_id, max_gen=max_gen)
    anc_b = ancestors_with_distance(G, b_id, max_gen=max_gen)
    commons = set(anc_a.keys()) & set(anc_b.keys())
    out = []
    for cid in commons:
        out.append({
            'id': cid,
            'name': G.nodes[cid].get('name', cid),
            'dist_a': anc_a[cid],
            'dist_b': anc_b[cid],
        })
    out.sort(key=lambda x: (x['dist_a'] + x['dist_b'], x['dist_a'], x['dist_b']))
    return out


def compute_inbreeding(G, person_id, cache, max_gen=10):
    if person_id in cache:
        return cache[person_id]
    parents = list(G.predecessors(person_id))
    if len(parents) < 2:
        cache[person_id] = 0.0
        return 0.0
    father, mother = parents[0], parents[1]
    if father is None or mother is None:
        cache[person_id] = 0.0
        return 0.0
    anc_f = ancestors_with_distance(G, father, max_gen=max_gen)
    anc_m = ancestors_with_distance(G, mother, max_gen=max_gen)
    commons = set(anc_f.keys()) & set(anc_m.keys())
    total = 0.0
    for a in commons:
        n1 = anc_f[a]
        n2 = anc_m[a]
        FA = compute_inbreeding(G, a, cache, max_gen=max_gen)
        contrib = (0.5 ** (n1 + n2 + 1)) * (1.0 + FA)
        total += contrib
    cache[person_id] = total
    return total


def kinship_coefficient(G, a_id, b_id, cacheF, max_gen=12):
    if a_id not in G.nodes() or b_id not in G.nodes():
        return 0.0
    anc_a = ancestors_with_distance(G, a_id, max_gen=max_gen)
    anc_b = ancestors_with_distance(G, b_id, max_gen=max_gen)
    commons = set(anc_a.keys()) & set(anc_b.keys())
    total = 0.0
    for A in commons:
        n_a = anc_a[A]
        n_b = anc_b[A]
        FA = compute_inbreeding(G, A, cacheF, max_gen=max_gen)
        contrib = (0.5 ** (n_a + n_b + 1)) * (1.0 + FA)
        total += contrib
    return total


def kinship_to_R(phi):
    return 2.0 * phi


# ─────────────────────────────────────────────────────────────────────────────
# Parseo cacheado
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False, ttl=3600)
def _cached_parse_gramps(content_bytes: bytes):
    db = _parse_gramps_shared(content_bytes)
    return db.to_persons_dict(), db.to_families_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Feature 1 — Análisis de Fundadores
# ─────────────────────────────────────────────────────────────────────────────

def get_founders(G) -> list:
    return [n for n in G.nodes() if len(list(G.predecessors(n))) == 0]


def compute_founder_contributions(G, person_id, founders, max_gen=12) -> dict:
    contributions = {}
    for f in founders:
        paths = find_paths_to_ancestor(G, person_id, f, max_gen=max_gen, multi=True)
        if not paths:
            continue
        total = sum(0.5 ** (len(p) - 1) for p in paths)
        if total > 0:
            contributions[f] = total
    return contributions


@st.cache_data(show_spinner=False, ttl=3600)
def _cached_founder_stats(content_bytes: bytes, max_gen: int) -> pd.DataFrame:
    people, families = _cached_parse_gramps(content_bytes)
    G = build_graph(people, families)
    founders = get_founders(G)
    founder_set = set(founders)
    stats = {f: {"n": 0, "contribs": []} for f in founders}
    for pid in G.nodes():
        if pid in founder_set:
            continue
        for f in founders:
            paths = find_paths_to_ancestor(G, pid, f, max_gen=max_gen, multi=True)
            if not paths:
                continue
            c = sum(0.5 ** (len(p) - 1) for p in paths)
            if c > 0:
                stats[f]["n"] += 1
                stats[f]["contribs"].append(c)
    rows = []
    for f in founders:
        cl = stats[f]["contribs"]
        rows.append({
            "id": f,
            "name": G.nodes[f].get("name", f),
            "n_descendants": stats[f]["n"],
            "avg_contribution": (sum(cl) / len(cl)) if cl else 0.0,
            "max_contribution": max(cl) if cl else 0.0,
        })
    return pd.DataFrame(rows).sort_values("avg_contribution", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Feature 2 — Predicción de ADN compartido (Shared cM Project 4.0)
# ─────────────────────────────────────────────────────────────────────────────

SHARED_CM_TABLE = {
    "parent_child":      {"avg": 3479, "min": 3330, "max": 3720},
    "full_sibling":      {"avg": 2543, "min": 2209, "max": 3384},
    "half_sibling":      {"avg": 1759, "min": 1317, "max": 2312},
    "grandparent":       {"avg": 1765, "min": 1156, "max": 2311},
    "aunt_uncle":        {"avg": 1741, "min": 1349, "max": 2175},
    "great_grandparent": {"avg":  881, "min":  485, "max": 1486},
    "half_aunt_uncle":   {"avg":  884, "min":  500, "max": 1446},
    "first_cousin":      {"avg":  866, "min":  553, "max": 1225},
    "first_cousin_1r":   {"avg":  433, "min":  137, "max":  836},
    "half_first_cousin": {"avg":  459, "min":  137, "max":  856},
    "second_cousin":     {"avg":  229, "min":   41, "max":  592},
    "first_cousin_2r":   {"avg":  230, "min":   41, "max":  592},
    "second_cousin_1r":  {"avg":  122, "min":    0, "max":  316},
    "third_cousin":      {"avg":   74, "min":    0, "max":  173},
    "fourth_cousin":     {"avg":   35, "min":    0, "max":   85},
    "very_distant":      {"avg":   12, "min":    0, "max":   57},
}

# Etiquetas legibles por clave de tabla
_CM_KEY_LABELS_ES = {
    "parent_child":      "Padre/Madre — Hijo/a",
    "full_sibling":      "Hermano/a completo/a",
    "half_sibling":      "Medio hermano/a",
    "grandparent":       "Abuelo/a — Nieto/a",
    "aunt_uncle":        "Tío/a — Sobrino/a",
    "great_grandparent": "Bisabuelo/a — Bisnieto/a",
    "half_aunt_uncle":   "Medio tío/a — Medio sobrino/a",
    "first_cousin":      "Primo/a hermano/a (1.º)",
    "first_cousin_1r":   "Primo/a 1.º una vez removido",
    "half_first_cousin": "Medio primo/a hermano/a",
    "second_cousin":     "Primo/a segundo/a (2.º)",
    "first_cousin_2r":   "Primo/a 1.º dos veces removido",
    "second_cousin_1r":  "Primo/a 2.º una vez removido",
    "third_cousin":      "Primo/a tercero/a (3.º)",
    "fourth_cousin":     "Primo/a cuarto/a (4.º)",
    "very_distant":      "Pariente lejano / sin relación detectada",
}
_CM_KEY_LABELS_EN = {
    "parent_child":      "Parent — Child",
    "full_sibling":      "Full sibling",
    "half_sibling":      "Half sibling",
    "grandparent":       "Grandparent — Grandchild",
    "aunt_uncle":        "Aunt/Uncle — Niece/Nephew",
    "great_grandparent": "Great-grandparent — Great-grandchild",
    "half_aunt_uncle":   "Half aunt/uncle — Half niece/nephew",
    "first_cousin":      "1st cousin",
    "first_cousin_1r":   "1st cousin once removed",
    "half_first_cousin": "Half 1st cousin",
    "second_cousin":     "2nd cousin",
    "first_cousin_2r":   "1st cousin twice removed",
    "second_cousin_1r":  "2nd cousin once removed",
    "third_cousin":      "3rd cousin",
    "fourth_cousin":     "4th cousin",
    "very_distant":      "Distant relative / no relationship detected",
}


def _cm_key_label(cm_key: str) -> str:
    labels = _CM_KEY_LABELS_ES if get_lang() == "es" else _CM_KEY_LABELS_EN
    return labels.get(cm_key, cm_key)


def _cm_key_from_distances(da: int, db: int, n_shared_parents: int) -> str:
    da_s, db_s = sorted([da, db])
    if (da_s, db_s) == (1, 1):
        return "full_sibling" if n_shared_parents == 2 else "half_sibling"
    pairs = {
        (1, 2): "aunt_uncle",
        (1, 3): "great_grandparent",
        (2, 2): "first_cousin",
        (2, 3): "first_cousin_1r",
        (2, 4): "first_cousin_2r",
        (3, 3): "second_cousin",
        (3, 4): "second_cousin_1r",
        (4, 4): "third_cousin",
        (5, 5): "fourth_cousin",
    }
    return pairs.get((da_s, db_s), "very_distant")


def classify_to_cm_key(G, a_id: str, b_id: str, phi: float, commons: list) -> str:
    anc_a = ancestors_with_distance(G, a_id, max_gen=4)
    anc_b = ancestors_with_distance(G, b_id, max_gen=4)
    if b_id in anc_a:
        d = anc_a[b_id]
        return {1: "parent_child", 2: "grandparent", 3: "great_grandparent"}.get(d, "very_distant")
    if a_id in anc_b:
        d = anc_b[a_id]
        return {1: "parent_child", 2: "grandparent", 3: "great_grandparent"}.get(d, "very_distant")
    if not commons:
        R = kinship_to_R(phi)
        if R >= 0.45:
            return "parent_child"
        if R >= 0.22:
            return "grandparent"
        if R >= 0.11:
            return "first_cousin"
        if R >= 0.04:
            return "second_cousin"
        return "very_distant"
    c = commons[0]
    parents_a = set(G.predecessors(a_id))
    parents_b = set(G.predecessors(b_id))
    return _cm_key_from_distances(c["dist_a"], c["dist_b"], len(parents_a & parents_b))


def predict_shared_cm(phi: float, cm_key: str) -> dict:
    if cm_key in SHARED_CM_TABLE:
        return SHARED_CM_TABLE[cm_key]
    R = kinship_to_R(phi)
    est = round(R * 6800)
    return {"avg": est, "min": 0, "max": round(est * 1.5)}


# ─────────────────────────────────────────────────────────────────────────────
# Feature 3 — Trazado de líneas puras
# ─────────────────────────────────────────────────────────────────────────────

def trace_matrilineal(G, start_id: str, max_depth: int = 20):
    line = [start_id]
    current = start_id
    for _ in range(max_depth):
        parents = list(G.predecessors(current))
        females = [p for p in parents if G.nodes[p].get("sex", "") == "F"]
        if not parents:
            return line, "no_parent"
        if not females:
            return line, "sex_unknown"
        line.append(females[0])
        current = females[0]
    return line, "max_depth"


def trace_patrilineal(G, start_id: str, max_depth: int = 20):
    line = [start_id]
    current = start_id
    for _ in range(max_depth):
        parents = list(G.predecessors(current))
        males = [p for p in parents if G.nodes[p].get("sex", "") == "M"]
        if not parents:
            return line, "no_parent"
        if not males:
            return line, "sex_unknown"
        line.append(males[0])
        current = males[0]
    return line, "max_depth"


# ─────────────────────────────────────────────────────────────────────────────
# Visualizaciones
# ─────────────────────────────────────────────────────────────────────────────

def build_timeline_chart(line: list, G) -> plt.Figure:
    years, indices, labels = [], [], []
    for i, nid in enumerate(line):
        yr = G.nodes[nid].get("birth")
        if yr is not None:
            years.append(yr)
            indices.append(i)
            labels.append(f"G{i}: {G.nodes[nid].get('name', nid)}")
    fig, ax = plt.subplots(figsize=(10, max(3, len(line) * 0.5)))
    if years:
        ax.plot(years, indices, "o-", color="steelblue", linewidth=1.5, markersize=6)
        for yr, idx, lbl in zip(years, indices, labels):
            ax.annotate(lbl, (yr, idx), textcoords="offset points",
                        xytext=(8, 0), fontsize=8, va="center")
    ax.set_xlabel(t("adn_birth_year"))
    ax.set_ylabel(t("adn_generation"))
    ax.invert_yaxis()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def build_geographic_map(line: list, G):
    import math

    coords = []
    for i, nid in enumerate(line):
        place = G.nodes[nid].get("place")
        if isinstance(place, dict):
            lat = place.get("lat")
            lon = place.get("lon")
            if lat is not None and lon is not None:
                coords.append({
                    "gen": i,
                    "name": G.nodes[nid].get("name", nid),
                    "lat": lat,
                    "lon": lon,
                    "birth": G.nodes[nid].get("birth"),
                })
    if len(coords) < 2:
        return None

    all_lats = [c["lat"] for c in coords]
    all_lons = [c["lon"] for c in coords]
    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    traces = []

    # Líneas de conexión entre ancestros consecutivos de la línea
    line_lats, line_lons = [], []
    for a, b in zip(coords, coords[1:]):
        line_lats += [a["lat"], b["lat"], None]
        line_lons += [a["lon"], b["lon"], None]

    if line_lats:
        traces.append(go.Scattermap(
            lat=line_lats,
            lon=line_lons,
            mode="lines",
            line=dict(width=2, color="steelblue"),
            showlegend=False,
            hoverinfo="skip",
        ))

    # Puntos con etiquetas
    GEN_COLORS = [
        "#1d4ed8", "#2563eb", "#3b82f6", "#60a5fa",
        "#93c5fd", "#bfdbfe", "#dbeafe",
    ]
    hover_texts = [
        f"<b>G{c['gen']}: {c['name']}</b><br>Nac.: {c['birth'] or '?'}"
        for c in coords
    ]
    traces.append(go.Scattermap(
        lat=all_lats,
        lon=all_lons,
        mode="markers+text",
        text=[f"G{c['gen']}: {c['name']}" for c in coords],
        textposition="top right",
        hovertext=hover_texts,
        hoverinfo="text",
        marker=dict(
            size=12,
            color=[GEN_COLORS[c["gen"] % len(GEN_COLORS)] for c in coords],
            opacity=0.9,
        ),
        showlegend=False,
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=6,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        height=520,
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Renderizadores de pestañas
# ─────────────────────────────────────────────────────────────────────────────

def _render_founders(G, content: bytes | None, max_gen: int):
    st.header(t("adn_founders_title"))
    st.caption(t("adn_founders_caption"))

    founders = get_founders(G)
    if not founders:
        st.info(t("adn_no_founders"))
        return

    st.metric(t("adn_n_founders"), len(founders))

    sub_a, sub_b = st.tabs([t("adn_sub_individual"), t("adn_sub_pedigree_wide")])

    with sub_a:
        all_ids = sorted(G.nodes(), key=lambda x: G.nodes[x].get("name", x))
        sel = st.selectbox(
            t("adn_select_person"),
            options=all_ids,
            format_func=lambda x: f"{G.nodes[x].get('name', x)} ({x})",
            key="adn_founder_sel_person",
        )
        if st.button(t("adn_compute_founders"), key="adn_btn_compute_founders"):
            with st.spinner(t("adn_computing")):
                contribs = compute_founder_contributions(G, sel, founders, max_gen=max_gen)
            st.session_state["adn_founder_contribs"] = contribs
            st.session_state["adn_founder_sel_cache"] = sel

        contribs = st.session_state.get("adn_founder_contribs", {})
        if contribs is not None and st.session_state.get("adn_founder_sel_cache") == sel:
            total_known = sum(contribs.values())
            unknown = max(0.0, 1.0 - total_known)

            labels = [G.nodes[f].get("name", f) for f in contribs] + [t("adn_unknown")]
            values = list(contribs.values()) + [unknown]
            colors = ["#3b82f6"] * len(contribs) + ["#d1d5db"]

            fig_pie = go.Figure(go.Pie(
                labels=labels,
                values=values,
                marker=dict(colors=colors),
                hovertemplate="%{label}: %{value:.2%}<extra></extra>",
            ))
            fig_pie.update_layout(height=450, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig_pie, use_container_width=True)

            rows = [
                {"id": f, "name": G.nodes[f].get("name", f), "contribution_pct": round(v * 100, 4)}
                for f, v in sorted(contribs.items(), key=lambda x: -x[1])
            ]
            rows.append({"id": "—", "name": t("adn_unknown"), "contribution_pct": round(unknown * 100, 4)})
            df_c = pd.DataFrame(rows)
            st.dataframe(
                df_c.rename(columns={
                    "name": t("adn_col_founder_name"),
                    "id": t("adn_col_founder_id"),
                    "contribution_pct": t("adn_col_contribution_pct"),
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                t("adn_download_founders_csv"),
                data=df_c.to_csv(index=False),
                file_name=f"fundadores_{sel}.csv",
                mime="text/csv",
            )

    with sub_b:
        st.caption(t("adn_pedigree_wide_caption"))
        if st.button(t("adn_compute_pedigree_wide"), key="adn_btn_pedigree_wide"):
            if content is None:
                st.warning(t("adn_upload_warning"))
            else:
                with st.spinner(t("adn_computing_wide")):
                    df_wide = _cached_founder_stats(content, max_gen)
                st.session_state["adn_founder_wide_df"] = df_wide

        df_wide = st.session_state.get("adn_founder_wide_df")
        if df_wide is not None and not df_wide.empty:
            st.dataframe(
                df_wide.style.format({
                    "avg_contribution": "{:.4f}",
                    "max_contribution": "{:.4f}",
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                t("adn_download_pedigree_wide_csv"),
                data=df_wide.to_csv(index=False),
                file_name="fundadores_arbol_completo.csv",
                mime="text/csv",
            )


def _render_shared_dna(G, max_gen: int):
    st.header(t("adn_shared_dna_title"))
    st.caption(t("adn_shared_dna_caption"))

    all_ids = sorted(G.nodes(), key=lambda x: G.nodes[x].get("name", x))
    col1, col2 = st.columns(2)
    with col1:
        person_a = st.selectbox(
            t("adn_select_person_a"),
            options=all_ids,
            format_func=lambda x: f"{G.nodes[x].get('name', x)} ({x})",
            key="adn_dna_sel_a",
        )
    with col2:
        person_b = st.selectbox(
            t("adn_select_person_b"),
            options=all_ids,
            format_func=lambda x: f"{G.nodes[x].get('name', x)} ({x})",
            key="adn_dna_sel_b",
        )

    if st.button(t("adn_predict_dna"), key="adn_btn_predict_dna"):
        if person_a == person_b:
            st.warning("Selecciona dos individuos distintos.")
        else:
            with st.spinner(t("adn_computing")):
                cache_F: dict = {}
                phi = kinship_coefficient(G, person_a, person_b, cache_F, max_gen=max_gen)
                R = kinship_to_R(phi)
                commons = find_common_ancestors(G, person_a, person_b, max_gen=max_gen)
                cm_key = classify_to_cm_key(G, person_a, person_b, phi, commons)
                cm_range = predict_shared_cm(phi, cm_key)
                label = _cm_key_label(cm_key)
            st.session_state["adn_dna_result"] = {
                "phi": phi, "R": R, "label": label, "cm_key": cm_key,
                "cm_range": cm_range, "a_id": person_a, "b_id": person_b,
                "n_common": len(commons),
            }

    result = st.session_state.get("adn_dna_result")
    if result and result["a_id"] == person_a and result["b_id"] == person_b:
        st.markdown("---")
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Φ (kinship)", f"{result['phi']:.6f}")
        col_m2.metric("R", f"{result['R']:.4f}")
        col_m3.metric(t("adn_classification"), result["label"])

        cm = result["cm_range"]
        fig_bar = go.Figure(go.Bar(
            x=[t("adn_cm_min"), t("adn_cm_avg"), t("adn_cm_max")],
            y=[cm["min"], cm["avg"], cm["max"]],
            text=[f"{v} cM" for v in [cm["min"], cm["avg"], cm["max"]]],
            textposition="auto",
            marker_color=["#93c5fd", "#3b82f6", "#1d4ed8"],
        ))
        fig_bar.update_layout(
            title=t("adn_cm_chart_title"),
            yaxis_title="cM",
            height=350,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        if result["n_common"] == 0:
            st.warning(t("adn_no_common_ancestors_warning"))
        st.info(t("adn_pedigree_completeness_warning"))

        # Tabla de referencia completa
        with st.expander(t("adn_cm_reference_table_title") if "adn_cm_reference_table_title" in (
                {} or {}) else "📋 Tabla de referencia completa (Shared cM Project 4.0)"):
            labels = _CM_KEY_LABELS_ES if get_lang() == "es" else _CM_KEY_LABELS_EN
            ref_rows = [
                {
                    "Parentesco": labels.get(k, k),
                    "Mín. cM": v["min"],
                    "Prom. cM": v["avg"],
                    "Máx. cM": v["max"],
                }
                for k, v in SHARED_CM_TABLE.items()
            ]
            st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)


def _render_lineage(G, max_gen: int):
    st.header(t("adn_lineage_title"))
    st.caption(t("adn_lineage_caption"))

    all_ids = sorted(G.nodes(), key=lambda x: G.nodes[x].get("name", x))
    sel = st.selectbox(
        t("adn_select_person"),
        options=all_ids,
        format_func=lambda x: f"{G.nodes[x].get('name', x)} ({x})",
        key="adn_lineage_sel",
    )

    tab_mat, tab_pat = st.tabs([t("adn_tab_maternal"), t("adn_tab_paternal")])

    def _render_line_content(line: list, reason: str):
        reason_map = {
            "no_parent": t("adn_stop_no_parent"),
            "sex_unknown": t("adn_stop_sex_unknown"),
            "max_depth": t("adn_stop_max_depth"),
        }
        col_m, col_r = st.columns(2)
        col_m.metric(t("adn_generation_count"), len(line) - 1)
        col_r.caption(f"**{t('adn_stop_reason')}:** {reason_map.get(reason, reason)}")

        st.markdown("---")
        for i, nid in enumerate(line):
            attrs = G.nodes[nid]
            birth = attrs.get("birth", "?")
            place_obj = attrs.get("place")
            place_name = place_obj.get("name", "?") if isinstance(place_obj, dict) else "?"
            name = attrs.get("name", nid)
            st.markdown(
                f"**{i}.** {name} `({nid})` — {t('adn_born')}: {birth} — {t('adn_place')}: {place_name}"
            )

        st.markdown("---")
        st.subheader(t("adn_timeline_title"))
        if len(line) >= 2:
            fig = build_timeline_chart(line, G)
            st.pyplot(fig, clear_figure=True)
        else:
            st.info(t("adn_not_enough_data_chart"))

        st.subheader(t("adn_map_title"))
        geo_fig = build_geographic_map(line, G)
        if geo_fig is not None:
            st.plotly_chart(geo_fig, use_container_width=True)
        else:
            st.info(t("adn_no_geo_data"))

    with tab_mat:
        if st.button(t("adn_trace_maternal"), key="adn_btn_mat"):
            mat_line, mat_reason = trace_matrilineal(G, sel, max_depth=max_gen)
            st.session_state["adn_mat_line"] = mat_line
            st.session_state["adn_mat_reason"] = mat_reason
            st.session_state["adn_mat_sel"] = sel
        if st.session_state.get("adn_mat_sel") == sel and "adn_mat_line" in st.session_state:
            _render_line_content(st.session_state["adn_mat_line"], st.session_state["adn_mat_reason"])

    with tab_pat:
        if st.button(t("adn_trace_paternal"), key="adn_btn_pat"):
            pat_line, pat_reason = trace_patrilineal(G, sel, max_depth=max_gen)
            st.session_state["adn_pat_line"] = pat_line
            st.session_state["adn_pat_reason"] = pat_reason
            st.session_state["adn_pat_sel"] = sel
        if st.session_state.get("adn_pat_sel") == sel and "adn_pat_line" in st.session_state:
            _render_line_content(st.session_state["adn_pat_line"], st.session_state["adn_pat_reason"])


# ─────────────────────────────────────────────────────────────────────────────
# Puntos de entrada del módulo
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar_upload():
    st.sidebar.markdown(t("adn_sidebar_header"))

    if st.session_state.get("gramps_web_connected"):
        st.sidebar.info(t("gramps_web_source_active"))
    else:
        # Hereda archivo compartido si otro módulo ya lo cargó
        if st.session_state.get("shared_gramps_bytes") and "adn_uploaded_bytes" not in st.session_state:
            st.session_state["adn_uploaded_bytes"] = st.session_state["shared_gramps_bytes"]
            name = st.session_state.get("shared_gramps_name", "")
            st.sidebar.info(f"{t('adn_using_shared_file')}: **{name}**")

        uploaded = st.sidebar.file_uploader(t("adn_upload_label"), type=["gramps", "xml"])
        if uploaded is not None:
            data = uploaded.read()
            st.session_state["adn_uploaded_bytes"] = data
            st.session_state["shared_gramps_bytes"] = data
            st.session_state["shared_gramps_name"] = uploaded.name


def render_sidebar():
    st.sidebar.slider(
        t("adn_max_gen"),
        min_value=3, max_value=20, value=12, step=1,
        key="adn_max_gen",
    )


def render_page(ctx=None):
    st.title(t("adn_title"))

    max_gen = st.session_state.get("adn_max_gen", 12)

    _db = (ctx.gramps.db if ctx is not None else None) \
          or st.session_state.get("_gramps_web_db_override")
    if _db is not None:
        people   = _db.to_persons_dict()
        families = _db.to_families_dict()
    else:
        content = (ctx.gramps.bytes_ if ctx is not None else None) \
                  or st.session_state.get("adn_uploaded_bytes") \
                  or st.session_state.get("shared_gramps_bytes")
        if content is None:
            st.info(t("adn_upload_warning"))
            return
        try:
            people, families = _cached_parse_gramps(content)
        except Exception as e:
            st.error(str(e))
            return

    if not people:
        st.error(t("adn_no_people"))
        return

    G = build_graph(people, families)

    tab1, tab2, tab3 = st.tabs([
        t("adn_tab_founders"),
        t("adn_tab_shared_dna"),
        t("adn_tab_lineage"),
    ])
    with tab1:
        _render_founders(G, content, max_gen)
    with tab2:
        _render_shared_dna(G, max_gen)
    with tab3:
        _render_lineage(G, max_gen)

