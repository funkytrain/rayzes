import math
import statistics
from collections import defaultdict
from pathlib import Path

import streamlit as st
import pandas as pd

from translations import t
from modules.shared.gramps_parser import parse_gramps, GrampsDB
from modules.shared.utils import normalize_name, haversine_km
from modules.shared.geo_viz import (
    GEN_COLORS, plot_geo_migration, build_ancestor_geo_data,
)

DATA_DIR = Path("data")
HISTORICAL_DIR = DATA_DIR / "historical"

# Umbral de dispersión geográfica para alertar apellidos potencialmente no relacionados
_DISPERSION_WARNING_KM = 300


# ============================================================
# Funciones puras
# ============================================================

def build_surname_geo_series(db: GrampsDB) -> tuple[list[dict], list[dict]]:
    """
    Para cada persona con coordenadas conocidas devuelve:
    {pid, name, surname, year, lat, lon, place_name}
    - year = baptism_year ?? birth_year
    - surname: primer apellido normalizado via normalize_name()
    - coordenadas: de db.to_places_map()
    Personas sin coordenadas → lista 'unmapped' separada.
    """
    places_map = db.to_places_map()
    place_by_name: dict[str, dict] = {}
    for _h, pl in places_map.items():
        if pl.get("lat") is not None and pl.get("lon") is not None:
            place_by_name[normalize_name(pl["name"])] = pl

    mapped, unmapped = [], []
    for handle, person in db.persons.items():
        year = person.baptism_year or person.birth_year
        raw_name = person.name or ""
        parts = raw_name.split()
        surname = normalize_name(parts[-1]) if len(parts) > 1 else normalize_name(parts[0]) if parts else ""

        place_name = person.baptism_place or person.birth_place or ""
        norm_place = normalize_name(place_name)

        pl_info = place_by_name.get(norm_place)
        if pl_info and pl_info.get("lat") is not None and year:
            mapped.append({
                "pid": handle,
                "name": raw_name,
                "surname": surname,
                "year": year,
                "lat": pl_info["lat"],
                "lon": pl_info["lon"],
                "place_name": pl_info["name"],
            })
        else:
            row = {
                "pid": handle,
                "name": raw_name,
                "surname": surname,
                "year": year,
                "place_name": place_name,
            }
            unmapped.append(row)

    return mapped, unmapped


def build_surname_trajectories(
    geo_series: list[dict], period_yrs: int = 25, min_points: int = 3
) -> dict[str, list[dict]]:
    """
    Agrupa por apellido + período de period_yrs años.
    Por cada período: {period_start, centroid_lat, centroid_lon, n_persons, places, place_name_most_common}
    Apellidos con < min_points entradas → descartados.
    Gaps de > 3 períodos consecutivos → trayectoria interrumpida (marcador None).
    """
    by_surname: dict[str, list[dict]] = defaultdict(list)
    for row in geo_series:
        by_surname[row["surname"]].append(row)

    result: dict[str, list[dict]] = {}
    for surname, rows in by_surname.items():
        if len(rows) < min_points:
            continue

        by_period: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            period_start = (row["year"] // period_yrs) * period_yrs
            by_period[period_start].append(row)

        periods_sorted = sorted(by_period.keys())
        trajectory: list[dict] = []
        for i, pstart in enumerate(periods_sorted):
            pts = by_period[pstart]
            lats = [r["lat"] for r in pts]
            lons = [r["lon"] for r in pts]
            place_counts: dict[str, int] = defaultdict(int)
            for r in pts:
                place_counts[r["place_name"]] += 1
            most_common = max(place_counts, key=place_counts.__getitem__)

            if trajectory and (pstart - periods_sorted[i - 1]) > 3 * period_yrs:
                trajectory.append(None)  # marcador de ruptura

            trajectory.append({
                "period_start": pstart,
                "centroid_lat": statistics.mean(lats),
                "centroid_lon": statistics.mean(lons),
                "n_persons": len(pts),
                "places": list(place_counts.keys()),
                "place_name_most_common": most_common,
                "persons": [{"pid": r["pid"], "name": r["name"], "year": r["year"],
                             "place_name": r["place_name"]} for r in pts],
            })

        result[surname] = trajectory
    return result


def compute_migration_distance(trajectory: list[dict]) -> dict:
    """Distancia total recorrida (suma haversine_km entre centroides consecutivos)."""
    valid_points = [p for p in trajectory if p is not None]
    total_km = 0.0
    n_segments = 0
    for i in range(1, len(valid_points)):
        p1, p2 = valid_points[i - 1], valid_points[i]
        d = haversine_km(p1["centroid_lat"], p1["centroid_lon"],
                         p2["centroid_lat"], p2["centroid_lon"])
        if d is not None:
            total_km += d
            n_segments += 1

    year_span = 0
    if len(valid_points) >= 2:
        year_span = valid_points[-1]["period_start"] - valid_points[0]["period_start"]

    avg_speed = (total_km / year_span) if year_span > 0 else 0.0
    return {
        "total_km": round(total_km, 2),
        "n_segments": n_segments,
        "year_span": year_span,
        "avg_speed_km_yr": round(avg_speed, 3),
    }


def analyze_surname_dispersion(
    surname: str,
    geo_series: list[dict],
    db: GrampsDB,
) -> dict:
    """
    Analiza si los portadores de un apellido forman un grupo geográficamente cohesionado
    o si existen clusters separados que sugieren líneas no relacionadas.

    Retorna:
    {
        "max_distance_km": float,          # mayor distancia entre dos portadores cualquiera
        "is_dispersed": bool,              # True si max_distance > umbral
        "clusters": list[dict],            # grupos geográficos detectados
        "connected_branches": int,         # nº de ramas del árbol que contienen el apellido
        "unconnected_groups": list[dict],  # clusters sin conexión familiar entre sí
        "recommendation": str,             # "ok" | "review" | "likely_unrelated"
    }
    """
    persons_in_surname = [r for r in geo_series if r["surname"] == surname]
    if len(persons_in_surname) < 2:
        return {"max_distance_km": 0.0, "is_dispersed": False, "clusters": [],
                "connected_branches": 1, "unconnected_groups": [],
                "recommendation": "ok"}

    # Calcular distancia máxima entre portadores
    max_dist = 0.0
    for i, a in enumerate(persons_in_surname):
        for b in persons_in_surname[i + 1:]:
            d = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            if d is not None and d > max_dist:
                max_dist = d

    is_dispersed = max_dist > _DISPERSION_WARNING_KM

    # Detectar clusters geográficos (agrupación simple por proximidad)
    clusters = _geo_cluster(persons_in_surname, radius_km=80)

    # Analizar conectividad familiar entre clusters usando el árbol
    unconnected_groups = _find_unconnected_clusters(clusters, db) if len(clusters) > 1 else []

    if not is_dispersed:
        recommendation = "ok"
    elif unconnected_groups:
        recommendation = "likely_unrelated"
    else:
        recommendation = "review"

    return {
        "max_distance_km": round(max_dist, 1),
        "is_dispersed": is_dispersed,
        "clusters": clusters,
        "connected_branches": len(clusters),
        "unconnected_groups": unconnected_groups,
        "recommendation": recommendation,
    }


def _geo_cluster(persons: list[dict], radius_km: float = 80) -> list[dict]:
    """
    Agrupa personas en clusters por proximidad geográfica (greedy, O(n²)).
    Cada cluster: {center_lat, center_lon, persons: list, label: str}
    """
    assigned = [False] * len(persons)
    clusters = []

    for i, p in enumerate(persons):
        if assigned[i]:
            continue
        cluster_members = [p]
        assigned[i] = True
        for j, q in enumerate(persons):
            if assigned[j]:
                continue
            d = haversine_km(p["lat"], p["lon"], q["lat"], q["lon"])
            if d is not None and d <= radius_km:
                cluster_members.append(q)
                assigned[j] = True

        lats = [m["lat"] for m in cluster_members]
        lons = [m["lon"] for m in cluster_members]
        place_names = list({m["place_name"] for m in cluster_members})
        clusters.append({
            "center_lat": statistics.mean(lats),
            "center_lon": statistics.mean(lons),
            "persons": cluster_members,
            "n": len(cluster_members),
            "label": place_names[0] if len(place_names) == 1 else f"{place_names[0]} y {len(place_names) - 1} más",
        })

    return sorted(clusters, key=lambda c: -c["n"])


def _find_unconnected_clusters(clusters: list[dict], db: GrampsDB) -> list[dict]:
    """
    Comprueba si los clusters tienen conexión familiar entre sí en el árbol.
    Dos clusters están conectados si existe al menos una familia que contenga
    miembros de ambos (padre/madre/hijo de distinto cluster).
    Devuelve los pares de clusters sin conexión familiar detectada.
    """
    # Construir mapa pid → cluster_index
    pid_to_cluster: dict[str, int] = {}
    for idx, cluster in enumerate(clusters):
        for person in cluster["persons"]:
            pid_to_cluster[person["pid"]] = idx

    # Para cada familia, ver qué clusters conecta
    connected_pairs: set[frozenset] = set()
    for fam in db.families.values():
        members_in_fam = []
        for handle in ([fam.husband_handle, fam.wife_handle] + list(fam.child_handles)):
            if handle and handle in pid_to_cluster:
                members_in_fam.append(pid_to_cluster[handle])
        unique_clusters = set(members_in_fam)
        if len(unique_clusters) > 1:
            for a in unique_clusters:
                for b in unique_clusters:
                    if a < b:
                        connected_pairs.add(frozenset({a, b}))

    # Encontrar pares de clusters sin conexión
    unconnected = []
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            if frozenset({i, j}) not in connected_pairs:
                unconnected.append({
                    "cluster_a": clusters[i]["label"],
                    "cluster_b": clusters[j]["label"],
                    "n_a": clusters[i]["n"],
                    "n_b": clusters[j]["n"],
                })

    return unconnected


def _load_historical_data() -> dict:
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    for p in HISTORICAL_DIR.glob("*.json"):
        try:
            import json
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
            place = obj.get("place", "")
            events = obj.get("events", [])
            if place and isinstance(events, list):
                data[normalize_name(place)] = events
        except Exception:
            pass
    return data


def correlate_with_historical(trajectory: list[dict], historical_data: dict,
                               period_yrs: int = 25) -> list[dict]:
    """
    Añade a cada punto de trayectoria los eventos históricos del lugar en ese período.
    Lugar sin datos → lista vacía.
    """
    enriched = []
    for point in trajectory:
        if point is None:
            enriched.append(None)
            continue
        place_key = normalize_name(point.get("place_name_most_common", ""))
        hist_events = historical_data.get(place_key, [])
        relevant = [
            ev for ev in hist_events
            if ev.get("year") and point["period_start"] <= ev["year"] < point["period_start"] + period_yrs
        ]
        enriched.append({**point, "historical_events": relevant})
    return enriched


# ============================================================
# Visualización
# ============================================================

def plot_surname_migration_map(
    trajectories: dict[str, list[dict]],
    selected_surnames: list[str],
    show_lines: bool = True,
) -> "plotly.graph_objects.Figure":
    """
    Plotly Scattermap con trayectorias de apellidos.
    Cada apellido tiene un color propio de GEN_COLORS.
    """
    import plotly.graph_objects as go

    traces = []
    for idx, surname in enumerate(selected_surnames):
        traj = [p for p in trajectories.get(surname, []) if p is not None]
        if not traj:
            continue
        color = GEN_COLORS[idx % len(GEN_COLORS)]

        lats = [p["centroid_lat"] for p in traj]
        lons = [p["centroid_lon"] for p in traj]
        texts = [
            f"<b>{surname}</b><br>{p['place_name_most_common']}<br>"
            f"{p['period_start']}–{p['period_start'] + 24}<br>n={p['n_persons']}"
            for p in traj
        ]

        if show_lines and len(traj) > 1:
            line_lats, line_lons = [], []
            for i in range(len(traj) - 1):
                line_lats += [lats[i], lats[i + 1], None]
                line_lons += [lons[i], lons[i + 1], None]
            traces.append(go.Scattermap(
                lat=line_lats, lon=line_lons,
                mode='lines',
                line=dict(width=2, color=color),
                showlegend=False,
                hoverinfo='skip',
            ))

        traces.append(go.Scattermap(
            lat=lats, lon=lons,
            mode='markers',
            marker=dict(
                size=[max(10, min(28, 10 + p['n_persons'] * 2)) for p in traj],
                color=color, opacity=0.85,
            ),
            text=texts,
            hoverinfo='text',
            name=surname,
        ))

    center_lats = [
        p["centroid_lat"]
        for s in selected_surnames
        for p in trajectories.get(s, [])
        if p is not None
    ]
    center_lons = [
        p["centroid_lon"]
        for s in selected_surnames
        for p in trajectories.get(s, [])
        if p is not None
    ]
    center_lat = statistics.mean(center_lats) if center_lats else 40.0
    center_lon = statistics.mean(center_lons) if center_lons else -3.0

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(style='open-street-map', center=dict(lat=center_lat, lon=center_lon), zoom=7),
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(title=t("mig_select_surnames"), bgcolor='rgba(255,255,255,0.8)'),
        height=550,
    )
    return fig


# ============================================================
# Streamlit — sidebar y página
# ============================================================

@st.cache_data(show_spinner=False, ttl=3600)
def _cached_parse(content_bytes: bytes) -> GrampsDB:
    return parse_gramps(content_bytes)


def render_sidebar_upload():
    st.sidebar.markdown(f"### {t('section_migration')}")

    if st.session_state.get("gramps_web_connected"):
        st.sidebar.info(t("gramps_web_source_active"))
    else:
        shared_bytes = st.session_state.get("shared_gramps_bytes")
        shared_name = st.session_state.get("shared_gramps_name", "")

        if shared_bytes:
            st.sidebar.success(f"📂 {shared_name}")
        else:
            uploaded = st.sidebar.file_uploader(
                t("sidebar_gramps_uploader"),
                type=["gramps"],
                key="mig_uploader",
            )
            if uploaded:
                content = uploaded.read()
                st.session_state["shared_gramps_bytes"] = content
                st.session_state["shared_gramps_name"] = uploaded.name


def render_sidebar():
    st.sidebar.markdown("---")
    st.sidebar.slider(t("mig_period_years"), min_value=10, max_value=100, value=25,
                      step=5, key="mig_period_yrs")
    st.sidebar.checkbox(t("geo_migration_lines"), value=True, key="mig_show_lines")


def render_page():
    db = st.session_state.get("_gramps_web_db_override")
    if db is None:
        content_bytes = st.session_state.get("shared_gramps_bytes")
        if not content_bytes:
            st.info(t("sidebar_gramps_uploader"))
            return
        db = _cached_parse(content_bytes)
    period_yrs = st.session_state.get("mig_period_yrs", 25)
    show_lines = st.session_state.get("mig_show_lines", True)

    st.title(t("section_migration"))
    st.caption(t("mig_page_caption"))

    with st.spinner(t("gen_inc_computing")):
        geo_series, unmapped = build_surname_geo_series(db)
        trajectories = build_surname_trajectories(geo_series, period_yrs=period_yrs)
        historical_data = _load_historical_data()

    tab_surname, tab_lineage = st.tabs([t("mig_tab_surname"), t("mig_tab_lineage")])

    # ── Tab A: Por apellido ──────────────────────────────────────────────────
    with tab_surname:
        st.caption(t("mig_tab_surname_caption"))

        if not trajectories:
            st.warning(t("geo_no_coords"))
            return

        surname_counts = {
            s: sum(p["n_persons"] for p in pts if p is not None)
            for s, pts in trajectories.items()
        }
        surnames_sorted = sorted(surname_counts, key=surname_counts.__getitem__, reverse=True)

        selected_surnames = st.multiselect(
            t("mig_select_surnames"),
            options=surnames_sorted,
            default=surnames_sorted[:3] if len(surnames_sorted) >= 3 else surnames_sorted,
            key="mig_selected_surnames",
        )

        if not selected_surnames:
            st.info(t("mig_select_surnames_hint"))
            return

        # Advertencia de variantes ortográficas
        _warn_variants(selected_surnames, geo_series)

        # Análisis de dispersión y ramas para cada apellido seleccionado
        dispersion_results = {}
        for s in selected_surnames:
            dispersion_results[s] = analyze_surname_dispersion(s, geo_series, db)

        _render_dispersion_alerts(selected_surnames, dispersion_results)

        fig = plot_surname_migration_map(trajectories, selected_surnames, show_lines=show_lines)
        st.plotly_chart(fig, use_container_width=True)

        # Tabla de distancias
        st.markdown(f"#### {t('mig_distance_table_title')}")
        dist_rows = []
        for s in selected_surnames:
            traj = trajectories.get(s, [])
            dist = compute_migration_distance(traj)
            disp = dispersion_results[s]
            dist_rows.append({
                t("mig_select_surnames"): s,
                t("mig_total_distance"): f"{dist['total_km']} km",
                t("mig_avg_speed"): f"{dist['avg_speed_km_yr']} km/año",
                t("mig_n_periods"): dist["n_segments"],
                t("mig_year_span"): f"{dist['year_span']} años",
                t("mig_max_dispersion"): f"{disp['max_distance_km']} km",
            })
        st.dataframe(pd.DataFrame(dist_rows), use_container_width=True)

        # Correlación histórica
        if historical_data:
            st.markdown(f"#### {t('mig_historical_events')}")
            st.caption(t("mig_historical_caption"))
            selected_for_hist = st.selectbox(
                t("mig_hist_select_label"),
                options=selected_surnames,
                key="mig_hist_surname",
            )
            if selected_for_hist:
                traj = trajectories.get(selected_for_hist, [])
                enriched = correlate_with_historical(traj, historical_data, period_yrs)
                found_any = False
                for point in enriched:
                    if point is None:
                        st.markdown("---")
                        continue
                    evs = point.get("historical_events", [])
                    if evs:
                        found_any = True
                        st.markdown(
                            f"**{point['period_start']}–{point['period_start'] + period_yrs - 1}**"
                            f" — {point['place_name_most_common']}"
                        )
                        for ev in evs:
                            st.markdown(f"  📜 {ev.get('description', '')}")
                if not found_any:
                    st.info(t("mig_no_historical_found"))
        else:
            st.info(t("mig_no_historical_data"))

    # ── Tab B: Por linaje ───────────────────────────────────────────────────
    with tab_lineage:
        st.caption(t("mig_tab_lineage_caption"))
        _render_lineage_tab(db, historical_data, show_lines)


# ============================================================
# Helpers de UI
# ============================================================

def _render_dispersion_alerts(selected_surnames: list[str], dispersion_results: dict):
    """Muestra avisos de dispersión geográfica y análisis de ramas no conectadas."""
    for s in selected_surnames:
        disp = dispersion_results[s]
        rec = disp["recommendation"]

        if rec == "likely_unrelated":
            with st.expander(
                f"⛔ **{s}** — {t('mig_alert_likely_unrelated_title')} "
                f"({disp['max_distance_km']} km)", expanded=True
            ):
                st.error(t("mig_alert_likely_unrelated_body"))
                st.markdown(t("mig_alert_unconnected_clusters"))
                for uc in disp["unconnected_groups"]:
                    st.markdown(
                        f"- **{uc['cluster_a']}** ({uc['n_a']} pers.) ↔ "
                        f"**{uc['cluster_b']}** ({uc['n_b']} pers.): "
                        + t("mig_alert_no_family_link")
                    )
                st.markdown(t("mig_alert_recommendation_exclude"))

        elif rec == "review":
            with st.expander(
                f"⚠️ **{s}** — {t('mig_alert_review_title')} "
                f"({disp['max_distance_km']} km)"
            ):
                st.warning(t("mig_alert_review_body"))
                if disp["clusters"]:
                    cluster_labels = [f"**{c['label']}** ({c['n']} pers.)"
                                      for c in disp["clusters"][:4]]
                    st.markdown(t("mig_alert_clusters_found") + ": " + ", ".join(cluster_labels))
                st.markdown(t("mig_alert_recommendation_review"))


def _warn_variants(selected_surnames: list[str], geo_series: list[dict]):
    """Detecta si un apellido seleccionado tiene posibles variantes ortográficas en los datos."""
    all_surnames = {row["surname"] for row in geo_series}
    for s in selected_surnames:
        variants = [
            other for other in all_surnames
            if other != s and _levenshtein(s, other) <= 2 and other not in selected_surnames
        ]
        if variants:
            st.warning(
                f"⚠️ **{s}** " + t("mig_variant_warning") + ": "
                + ", ".join(f"**{v}**" for v in variants[:5])
            )


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 3:
        return 99
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        prev = dp[:]
        dp[0] = i + 1
        for j, cb in enumerate(b):
            dp[j + 1] = min(dp[j] + 1, prev[j + 1] + 1, prev[j] + (ca != cb))
    return dp[-1]


def _render_lineage_tab(db: GrampsDB, historical_data: dict, show_lines: bool):
    """Tab B: mapa de linaje ancestral reutilizando geo_viz + correlación histórica."""
    try:
        import networkx as nx
    except ImportError:
        st.error("networkx no está instalado.")
        return

    persons_dict = db.to_persons_dict()
    families_dict = db.to_families_dict()

    if not persons_dict:
        st.warning(t("gen_ext_no_file"))
        return

    person_options = {
        pid: f"{p.get('name', pid)} ({p.get('birth', '') or '?'})"
        for pid, p in sorted(persons_dict.items(), key=lambda x: x[1].get('name', ''))
    }

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_pid = st.selectbox(
            t("mig_lineage_select_person"),
            options=list(person_options.keys()),
            format_func=lambda pid: person_options[pid],
            key="mig_lineage_pid",
        )
    with col2:
        geo_max_gen = st.slider(
            t("mig_lineage_max_gen"), 2, 12, 6,
            key="mig_lin_gen_slider",
        )

    if not st.button(t("mig_lineage_generate"), key="mig_lin_generate"):
        return

    try:
        import plotly
    except ImportError:
        st.error(t("geo_plotly_missing") if _has_key("geo_plotly_missing") else "Plotly no instalado")
        return

    G = _build_graph_from_dicts(persons_dict, families_dict)
    cache: dict = {}

    def _noop_inbreeding(G, pid, cache, max_gen=8):
        return 0.0

    def _bfs_ancestors(G, pid, max_gen=8):
        result = {}
        queue = [(pid, 0)]
        visited = set()
        while queue:
            node, gen = queue.pop(0)
            if node in visited or gen > max_gen:
                continue
            visited.add(node)
            if gen > 0:
                result[node] = gen
            for pred in G.predecessors(node):
                queue.append((pred, gen + 1))
        return result

    mapped, unmapped = build_ancestor_geo_data(
        G, selected_pid, cache, max_gen=geo_max_gen,
        compute_inbreeding_fn=_noop_inbreeding,
        ancestors_with_distance_fn=_bfs_ancestors,
    )

    st.info(
        t("geo_n_mapped").format(n=len(mapped), m=len(unmapped))
        if _has_key("geo_n_mapped")
        else f"{len(mapped)} con coords, {len(unmapped)} sin coords"
    )

    if mapped:
        fig = plot_geo_migration(mapped, show_lines=show_lines, G=G, person_id=selected_pid)

        if historical_data:
            import plotly.graph_objects as go
            hist_lats, hist_lons, hist_texts = [], [], []
            for point in mapped:
                place_key = normalize_name(point["place_name"])
                evs = historical_data.get(place_key, [])
                for ev in evs:
                    hist_lats.append(point["lat"] + 0.01)
                    hist_lons.append(point["lon"] + 0.01)
                    hist_texts.append(f"📜 {ev.get('description', '')} ({ev.get('year', '?')})")
            if hist_lats:
                fig.add_trace(go.Scattermap(
                    lat=hist_lats, lon=hist_lons,
                    mode='markers',
                    marker=dict(size=10, color='orange', opacity=0.8),
                    text=hist_texts,
                    hoverinfo='text',
                    name=t("mig_historical_events"),
                ))

        st.plotly_chart(fig, use_container_width=True)

    if unmapped:
        label = t("geo_ancestors_no_coords") if _has_key("geo_ancestors_no_coords") else "Ancestros sin coordenadas"
        with st.expander(f"**{label}** ({len(unmapped)})"):
            st.dataframe(pd.DataFrame(unmapped)[['name', 'gen', 'place_name']], height=200)

    if mapped or unmapped:
        all_rows = mapped + [
            {"id": r.get("id", ""), "name": r["name"], "gen": r["gen"],
             "place_name": r["place_name"], "lat": None, "lon": None}
            for r in unmapped
        ]
        csv_data = pd.DataFrame(all_rows).to_csv(index=False)
        st.download_button(
            label=t("mig_lineage_download_csv"),
            data=csv_data,
            file_name=f"linaje_{selected_pid}.csv",
            mime="text/csv",
            key="mig_lin_csv",
        )


def _build_graph_from_dicts(persons_dict: dict, families_dict: dict):
    import networkx as nx
    G = nx.DiGraph()
    for pid, p in persons_dict.items():
        G.add_node(pid, name=p.get("name", pid),
                   birth=p.get("birth"),
                   place=p.get("place"))
    for fam in families_dict.values():
        h = fam.get("husband")
        w = fam.get("wife")
        for child in fam.get("children", []):
            if h:
                G.add_edge(h, child)
            if w:
                G.add_edge(w, child)
    return G


def _has_key(key: str) -> bool:
    try:
        return t(key) != key
    except Exception:
        return False
