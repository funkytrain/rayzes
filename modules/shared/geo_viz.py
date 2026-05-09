import math
from collections import defaultdict

GEN_COLORS = [
    '#e6194b', '#f58231', '#ffe119', '#3cb44b', '#42d4f4',
    '#4363d8', '#911eb4', '#a9a9a9', '#f032e6', '#bfef45', '#fabed4', '#aaffc3'
]


def bearing_deg(lat1, lon1, lat2, lon2):
    """Rumbo geodésico p1→p2 en grados desde el norte (0=N, 90=E)."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def build_ancestor_geo_data(G, person_id: str, cache: dict, max_gen: int = 8,
                             compute_inbreeding_fn=None, ancestors_with_distance_fn=None):
    """
    Recopila datos geográficos de los ancestros de person_id.
    Retorna (mapped, unmapped) — listas de dicts.
      mapped:   [{'id','name','lat','lon','gen','F','place_name'}, ...]
      unmapped: [{'id','name','gen','place_name'}, ...]

    Requiere que se pasen las funciones compute_inbreeding y ancestors_with_distance
    del módulo consanguinidad para evitar dependencia circular.
    """
    mapped, unmapped = [], []
    anc_dist = ancestors_with_distance_fn(G, person_id, max_gen=max_gen)
    for anc_id, gen in anc_dist.items():
        node = G.nodes[anc_id]
        name = node.get('name', anc_id)
        place = node.get('place')
        lat = place.get('lat') if place else None
        lon = place.get('lon') if place else None
        place_name = place.get('name', '') if place else ''
        F_val = compute_inbreeding_fn(G, anc_id, cache, max_gen=max_gen)
        if lat is not None and lon is not None:
            mapped.append({'id': anc_id, 'name': name, 'lat': lat, 'lon': lon,
                           'gen': gen, 'F': F_val, 'place_name': place_name})
        else:
            unmapped.append({'id': anc_id, 'name': name, 'gen': gen, 'place_name': place_name})
    mapped.sort(key=lambda x: x['gen'])
    unmapped.sort(key=lambda x: x['gen'])
    return mapped, unmapped


def plot_geo_migration(mapped: list, show_lines: bool = True, G=None, person_id: str = None):
    """
    Genera figura Plotly con mapa OpenStreetMap usando go.Scattermap (Plotly >= 5.18).
    Con show_lines=True dibuja flechas reales padre→hijo con symbol='arrow' orientado
    según el rumbo geodésico del desplazamiento.
    """
    import plotly.graph_objects as go

    coord_map = {pt['id']: pt for pt in mapped}

    by_gen = defaultdict(list)
    for pt in mapped:
        by_gen[pt['gen']].append(pt)

    all_lats = [pt['lat'] for pt in mapped]
    all_lons = [pt['lon'] for pt in mapped]
    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    traces = []

    if show_lines and G is not None:
        anc_ids = set(coord_map.keys())
        if person_id:
            anc_ids.add(person_id)

        seen_edges = set()
        line_lons, line_lats = [], []
        arr_lons, arr_lats, arr_angles, arr_hovers = [], [], [], []

        for node_id in anc_ids:
            if node_id not in G.nodes():
                continue
            for child_id in G.successors(node_id):
                if (node_id, child_id) in seen_edges:
                    continue
                parent_pt = coord_map.get(node_id)
                child_pt = coord_map.get(child_id)
                if parent_pt is None or child_pt is None:
                    continue
                seen_edges.add((node_id, child_id))

                plon, plat = parent_pt['lon'], parent_pt['lat']
                clon, clat = child_pt['lon'], child_pt['lat']

                line_lons += [plon, clon, None]
                line_lats += [plat, clat, None]

                arr_lons.append((plon + clon) / 2)
                arr_lats.append((plat + clat) / 2)
                arr_angles.append(bearing_deg(plat, plon, clat, clon))
                arr_hovers.append(f"{parent_pt['name']} → {child_pt['name']}")

        if line_lons:
            traces.append(go.Scattermap(
                lon=line_lons,
                lat=line_lats,
                mode='lines',
                line=dict(width=1.5, color='rgba(50,50,50,0.4)'),
                showlegend=False,
                hoverinfo='skip',
            ))
            traces.append(go.Scattermap(
                lon=arr_lons,
                lat=arr_lats,
                mode='markers',
                marker=dict(
                    size=12,
                    symbol='arrow',
                    color='rgba(40,40,40,0.75)',
                    angle=arr_angles,
                    allowoverlap=True,
                ),
                text=arr_hovers,
                hoverinfo='text',
                showlegend=False,
            ))

    for gen in sorted(by_gen.keys()):
        pts = by_gen[gen]
        color = GEN_COLORS[(gen - 1) % len(GEN_COLORS)]
        sizes = [max(10, min(28, 10 + pt['F'] * 200)) for pt in pts]
        hover = [
            f"<b>{pt['name']}</b><br>Gen {pt['gen']}<br>F={pt['F']:.4f}<br>{pt['place_name']}"
            for pt in pts
        ]
        traces.append(go.Scattermap(
            lon=[pt['lon'] for pt in pts],
            lat=[pt['lat'] for pt in pts],
            mode='markers',
            marker=dict(size=sizes, color=color, opacity=0.9),
            text=hover,
            hoverinfo='text',
            name=f"Gen {gen}",
        ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style='open-street-map',
            center=dict(lat=center_lat, lon=center_lon),
            zoom=7,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(title="Generation", bgcolor='rgba(255,255,255,0.8)'),
        height=600,
    )
    return fig
