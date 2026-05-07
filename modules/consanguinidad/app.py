import streamlit as st
import pandas as pd
import networkx as nx
from pyvis.network import Network
import xml.etree.ElementTree as ET
import math
from collections import defaultdict, deque
import io

from translations import t, get_lang
from modules.shared.utils import strip_ns, safe_year as safe_int_year
from modules.shared.gramps_parser import parse_gramps as _parse_gramps_shared

# ----------------------------
# Utilities placeholder — TRANSLATIONS block removed (using shared translations.py)
# ----------------------------
# --- TRANSLATIONS_REMOVED_PLACEHOLDER ---
TRANSLATIONS = {
    "en": {
        "title": "ConsanguinityLab — Gramps Analysis",
        "upload_params": "Upload & Parameters",
        "upload_file": "Upload your .gramps file (XML exported from Gramps)",
        "max_gen": "Max ancestor generations to analyze",
        "f_threshold": "F threshold to mark (e.g., 0.01 = 1%)",
        "run_analysis": "Run Analysis",
        "adjust_params": "Adjust parameters and click 'Run Analysis' in the sidebar to execute the analysis.",
        "upload_warning": "Upload a .gramps (XML) file to start.",
        "package_error": "The file appears to be a package (.gpkg or zip). Export from Gramps as 'Gramps XML' (.gramps file).",
        "xml_error": "Error reading XML: {}",
        "no_people": "Could not read people from file. Check format.",
        "people": "People",
        "families": "Families",
        "nodes_graph": "Nodes in graph",
        "edges": "Edges (parent→child)",
        "results_summary": "Results Summary",
        "individuals_analyzed": "Individuals analyzed",
        "individuals_f_positive": "Individuals with F > 0",
        "top_10_f": "Top 10 by F",
        "download_csv": "Download CSV",
        "download_csv_summary": "Download CSV (summary)",
        "consanguineous_couples": "Detected consanguineous couples (families)",
        "couple_detection": "Consanguineous couple detection",
        "couple_min_phi": "Minimum Φ (kinship) threshold to report couples",
        "no_couples": "No consanguineous couples found with the selected threshold.",
        "family_id": "Family ID",
        "husband": "Husband",
        "wife": "Wife",
        "phi": "Φ",
        "r_coef": "R",
        "classification": "Classification",
        "n_children": "Children",
        "sample_ancestors": "Sample common ancestors",
        "download_couples_csv": "Download CSV of consanguineous couples",
        "individual_explorer": "Individual Explorer",
        "select_individual": "Select individual",
        "common_ancestors": "Common ancestors",
        "common_ancestors_summary": "Common ancestors (summary)",
        "none_found": "None found up to indicated depth.",
        "loop_details": "Loop / path details",
        "no_loops": "No consanguinity loops found for this individual with current parameters.",
        "common_ancestor": "Common ancestor",
        "dist_from_father": "Distance from father",
        "dist_from_mother": "Distance from mother",
        "path": "path",
        "relationship_two": "Relationship between two people",
        "select_a": "Select individual A",
        "select_b": "Select individual B",
        "calc_explain": "Calculate and explain relationship (text)",
        "auto_explanation": "Automatic explanation",
        "download_explanation": "Download explanation (TXT)",
        "visualize_relationship": "Visualize relationship A ↔ B graphically",
        "interactive_subtree": "Visualization: interactive subtree",
        "gens_up": "Generations upward to visualize",
        "download_full_csv": "Download full CSV (with paths)",
        "analysis_complete": "Analysis completed. Adjust 'Max generations' and click 'Run Analysis' again to recompute with different depth.",
        "pedigree_collapse_reports": "Pedigree Collapse + Reports",
        "gens_analyze_collapse": "Generations to analyze (collapse)",
        "calc_collapse": "Calculate pedigree collapse",
        "collapse_result": "Pedigree collapse result",
        "collapse_coeff": "Collapse coefficient",
        "generate_report": "Generate Report HTML / PDF",
        "format": "Format",
        "generate_full_report": "Generate complete report",
        "run_analysis_first": "First run the analysis and pedigree collapse.",
        "download_html": "Download HTML report",
        "download_pdf": "Download PDF",
        "advanced_viz": "Advanced Visualizations and Analysis",
        "radial_wheel": "Radial pedigree collapse wheel",
        "select_radial": "Select individual for radial wheel",
        "gens_show": "Generations to show",
        "generate_radial": "Generate radial wheel",
        "no_collapse_detected": "No detectable collapse in this generation range.",
        "global_f_histogram": "Global F histogram",
        "show_histogram": "Show histogram",
        "run_main_first": "First run the main analysis.",
        "enhanced_histogram": "Enhanced F histogram",
        "show_only_positive": "Show only F > 0",
        "log_y_scale": "Logarithmic Y scale",
        "show_kde": "Show KDE (if scipy installed)",
        "generate_enhanced_hist": "Generate enhanced histogram",
        "no_data_histogram": "No data to draw histogram with indicated parameters.",
        "mass_export": "Mass export of reports (HTML)",
        "gens_collapse_mass": "Generations for collapse in mass reports",
        "generate_mass": "Generate mass reports",
        "download_zip": "Download ZIP of reports",
        "global_collapse_exploration": "Global tree collapse exploration",
        "gens_global_analysis": "Generations for global analysis",
        "scan_global_collapse": "Scan global collapse",
        "heatmap_collapse_gen": "Heatmap: collapse by generation (individual)",
        "select_heatmap": "Select individual for heatmap",
        "max_gens_heatmap": "Max generations (heatmap)",
        "generate_heatmap": "Generate collapse heatmap",
        "no_repetitions": "No repetitions detected or insufficient data in that generation range.",
        "tree_colored_f": "Interactive tree colored by F",
        "select_center": "Select center (or leave empty for entire graph)",
        "entire_graph": "Entire graph",
        "gens_upward_show": "Generations (upward) to show",
        "generate_pyvis_f": "Generate Pyvis colored by F",
        "relationship_between": "Relationship between {} ({}) and {} ({})",
        "kinship": "Φ (kinship)",
        "r_relationship": "R (relationship coef.)",
        "approx_classification": "Approximate classification",
        "common_ancestors_found": "Found {} common ancestors (showing up to {})",
        "no_common_ancestors": "No common ancestors found within indicated generation range.",
        "ancestor": "Ancestor",
        "distances": "distances",
        "path_a_ancestor": "Path A → ancestor",
        "path_b_ancestor": "Path B → ancestor",
        "approx_contribution": "Approximate contribution of this ancestor to total Φ (approx)",
        "summed_contributions": "Sum of approximate contributions from shown ancestors yields ~{} (this is an approximation).",
        "note_approx": "NOTE: these contributions are approximate; for exact values in complex structures use gene-dropping.",
        "unrelated": "No detected relationship",
        "direct_relative": "Direct relative: parent/child or siblings (very high)",
        "grandparent_uncle": "Grandparent/grandchild, uncle/nephew, or similar relationship",
        "first_cousins": "Approximate first cousins",
        "second_cousins": "Second cousins / distant relatives",
        "very_distant": "Very distant relationship (possible historical collapse)",
        "very_remote": "Very remote relationship (very low)",
        "generation": "Generation",
        "repetitions": "repetitions",
        "ancestor_repetitions_by_gen": "Ancestor repetitions by generation (individual {})",
        "radial_pedigree_collapse": "Radial Pedigree Collapse",
        "ancestor_repetition_count": "Ancestor repetition count",
        "f_distribution": "F coefficient distribution",
        "f_only_positive": " (only F > 0)",
        "f_log_scale": " — Y log scale",
        "number_of_individuals": "Number of individuals",
        # Func 3 — relationship classifier
        "rel_parent": "Parent (father/mother)",
        "rel_child": "Child (son/daughter)",
        "rel_sibling_full": "Full sibling (same father and mother)",
        "rel_sibling_half": "Half sibling (one common parent)",
        "rel_grandparent": "Grandparent",
        "rel_grandchild": "Grandchild",
        "rel_uncle_aunt": "Uncle/aunt",
        "rel_nephew_niece": "Nephew/niece",
        "rel_great_uncle": "Great-uncle/great-aunt",
        "rel_great_nephew": "Grand-nephew/grand-niece",
        "rel_1c": "First cousin (1C)",
        "rel_1c1r": "First cousin once removed (1C1R)",
        "rel_1c2r": "First cousin twice removed (1C2R)",
        "rel_2c": "Second cousin (2C)",
        "rel_2c1r": "Second cousin once removed (2C1R)",
        "rel_3c": "Third cousin (3C)",
        "rel_bisabuelo": "Great-grandparent",
        "rel_bisnieto": "Great-grandchild",
        "rel_tatarabuelo": "Great-great-grandparent",
        "rel_distant_rel": "Distant relative (dist_a={}, dist_b={})",
        # Func 1 — geographic map
        "geo_lineage": "Geographic Ancestry & Migration Map",
        "geo_select_individual": "Select individual to map ancestors",
        "geo_max_gen": "Max generations to show",
        "geo_generate": "Generate geographic map",
        "geo_no_coords": "No ancestors with geographic coordinates found.",
        "geo_ancestors_no_coords": "Ancestors without coordinates (cannot be mapped):",
        "geo_plotly_missing": "Plotly is not installed. Add 'plotly>=5.0.0' to requirements.txt and restart.",
        "geo_migration_lines": "Show migration lines between generations",
        "geo_n_mapped": "{n} ancestors mapped, {m} without coordinates.",
        # Func 2 — historical inbreeding
        "inbreeding_timeline": "Historical Inbreeding Patterns",
        "inbreeding_period": "Group by",
        "inbreeding_period_decade": "Decade",
        "inbreeding_period_century": "Century",
        "inbreeding_period_25yr": "25-year period",
        "inbreeding_generate": "Generate historical analysis",
        "inbreeding_no_birth": "No individuals with known birth year found.",
        "inbreeding_n_with_birth": "{n} individuals with known birth year (out of {total}).",
        "inbreeding_period_label": "Period",
        "inbreeding_count": "Count",
        "inbreeding_mean_f": "Mean F",
        "inbreeding_max_f": "Max F",
        "inbreeding_median_f": "Median F",
        "inbreeding_peak_epoch": "Period with highest mean F: {} (mean F = {:.4f})",
        "inbreeding_chart_title": "Mean F by {} — Historical Inbreeding Trend",
        "inbreeding_table_title": "Summary table by period",
    },
    "es": {
        "title": "Consanguinidad — análisis desde Gramps (.gramps)",
        "upload_params": "Carga y parámetros",
        "upload_file": "Sube tu archivo .gramps (XML exportado desde Gramps)",
        "max_gen": "Máx generaciones (ancestros) a analizar",
        "f_threshold": "Umbral F para marcar (ej: 0.01 = 1%)",
        "run_analysis": "Correr análisis",
        "adjust_params": "Ajusta parámetros y pulsa 'Correr análisis' en la barra lateral para ejecutar el análisis.",
        "upload_warning": "Sube un archivo .gramps (XML) para empezar.",
        "package_error": "El archivo parece ser un paquete (.gpkg o zip). Exporta desde Gramps como 'Gramps XML' (archivo .gramps).",
        "xml_error": "Error leyendo XML: {}",
        "no_people": "No se pudieron leer personas del archivo. Revisa formato.",
        "people": "Personas",
        "families": "Familias",
        "nodes_graph": "Nodos en grafo",
        "edges": "Aristas (parent→child)",
        "results_summary": "Resumen de resultados",
        "individuals_analyzed": "Individuos analizados",
        "individuals_f_positive": "Individuos con F > 0",
        "top_10_f": "Top 10 por F",
        "download_csv": "Descargar CSV",
        "download_csv_summary": "Descargar CSV (resumen)",
        "consanguineous_couples": "Parejas consanguíneas detectadas (familias)",
        "couple_detection": "Detección de parejas consanguíneas",
        "couple_min_phi": "Umbral mínimo Φ (kinship) para reportar parejas",
        "no_couples": "No se encontraron parejas consanguíneas con el umbral seleccionado.",
        "family_id": "family_id",
        "husband": "husband",
        "wife": "wife",
        "phi": "phi",
        "r_coef": "R",
        "classification": "classification",
        "n_children": "n_children",
        "sample_ancestors": "sample_common_ancestors",
        "download_couples_csv": "Descargar CSV de parejas consanguíneas",
        "individual_explorer": "Explorador por individuo",
        "select_individual": "Selecciona individuo",
        "common_ancestors": "Ancestros comunes",
        "common_ancestors_summary": "Ancestros comunes (resumen)",
        "none_found": "Ninguno encontrado hasta la profundidad indicada.",
        "loop_details": "Detalles de bucles / rutas",
        "no_loops": "No se encontraron bucles de consanguinidad para este individuo con los parámetros actuales.",
        "common_ancestor": "Ancestro común",
        "dist_from_father": "Distancia desde padre",
        "dist_from_mother": "Distancia desde madre",
        "path": "ruta",
        "relationship_two": "Parentesco entre dos personas",
        "select_a": "Selecciona individuo A",
        "select_b": "Selecciona individuo B",
        "calc_explain": "Calcular y explicar parentesco (texto)",
        "auto_explanation": "Explicación automática",
        "download_explanation": "Descargar explicación (TXT)",
        "visualize_relationship": "Visualizar gráficamente relación A ↔ B",
        "interactive_subtree": "Visualización: subárbol interactivo",
        "gens_up": "Generaciones arriba para visualizar",
        "download_full_csv": "Descargar CSV completo (con rutas)",
        "analysis_complete": "Análisis completado. Ajusta 'Máx generaciones' y vuelve a pulsar 'Correr análisis' para recomputar con diferente profundidad.",
        "pedigree_collapse_reports": "Colapso de Pedigrí + Informes",
        "gens_analyze_collapse": "Generaciones a analizar (colapso)",
        "calc_collapse": "Calcular colapso de pedigrí",
        "collapse_result": "Resultado del colapso de pedigrí",
        "collapse_coeff": "Coeficiente de colapso",
        "generate_report": "Generar Informe HTML / PDF",
        "format": "Formato",
        "generate_full_report": "Generar informe completo",
        "run_analysis_first": "Primero ejecuta el análisis y el colapso de pedigrí.",
        "download_html": "Descargar informe HTML",
        "download_pdf": "Descargar PDF",
        "advanced_viz": "Visualizaciones y análisis avanzados",
        "radial_wheel": "Rueda radial del colapso de pedigrí",
        "select_radial": "Selecciona individuo para rueda radial",
        "gens_show": "Generaciones a mostrar",
        "generate_radial": "Generar rueda radial",
        "no_collapse_detected": "No hay colapso detectable en este rango de generaciones.",
        "global_f_histogram": "Histograma global de F",
        "show_histogram": "Mostrar histograma",
        "run_main_first": "Primero realiza el análisis principal.",
        "enhanced_histogram": "Histograma mejorado de F",
        "show_only_positive": "Mostrar solo F > 0",
        "log_y_scale": "Escala Y logarítmica",
        "show_kde": "Mostrar KDE (si scipy instalado)",
        "generate_enhanced_hist": "Generar histograma mejorado",
        "no_data_histogram": "No hay datos para dibujar el histograma con los parámetros indicados.",
        "mass_export": "Exportación masiva de informes (HTML)",
        "gens_collapse_mass": "Generaciones para colapso en informes masivos",
        "generate_mass": "Generar informes masivos",
        "download_zip": "Descargar ZIP de informes",
        "global_collapse_exploration": "Exploración global del colapso en el árbol",
        "gens_global_analysis": "Generaciones para análisis global",
        "scan_global_collapse": "Escanear colapso global",
        "heatmap_collapse_gen": "Heatmap: colapso por generación (individual)",
        "select_heatmap": "Selecciona individuo para heatmap",
        "max_gens_heatmap": "Máx generaciones (heatmap)",
        "generate_heatmap": "Generar heatmap de colapso",
        "no_repetitions": "No se detectaron repeticiones o no hay datos suficientes en ese rango de generaciones.",
        "tree_colored_f": "Árbol interactivo coloreado por F",
        "select_center": "Selecciona centro (o deja vacío para todo el grafo)",
        "entire_graph": "Todo el grafo",
        "gens_upward_show": "Generaciones (al subir) a mostrar",
        "generate_pyvis_f": "Generar Pyvis coloreado por F",
        "relationship_between": "Relación entre {} ({}) y {} ({})",
        "kinship": "Φ (kinship)",
        "r_relationship": "R (coef. relación)",
        "approx_classification": "Clasificación aproximada",
        "common_ancestors_found": "Se han encontrado {} ancestros comunes (se muestran hasta {})",
        "no_common_ancestors": "No se han encontrado ancestros comunes dentro del rango de generaciones indicado.",
        "ancestor": "Ancestro",
        "distances": "distancias",
        "path_a_ancestor": "Ruta A → ancestro",
        "path_b_ancestor": "Ruta B → ancestro",
        "approx_contribution": "Contribución aproximada de este ancestro a Φ total (aprox)",
        "summed_contributions": "Sumadas las contribuciones aproximadas de los ancestros mostrados se obtiene ~{} (esto es una aproximación).",
        "note_approx": "NOTA: estas contribuciones son aproximadas; para valores exactos en estructuras complejas use gene-dropping.",
        "unrelated": "No emparentados detectados",
        "direct_relative": "Pariente directo: padre/hijo o hermanos (muy alto)",
        "grandparent_uncle": "Abuelo/nieto, tío/sobrino, o parentesco similar",
        "first_cousins": "Primos hermanos aproximados",
        "second_cousins": "Primos segundos / parientes lejanos",
        "very_distant": "Parentesco muy lejano (posible colapso histórico)",
        "very_remote": "Parentesco muy remoto (muy bajo)",
        "generation": "Generación",
        "repetitions": "repeticiones",
        "ancestor_repetitions_by_gen": "Repeticiones de ancestros por generación (individuo {})",
        "radial_pedigree_collapse": "Colapso Radial de Pedigrí",
        "ancestor_repetition_count": "Número de repeticiones del ancestro",
        "f_distribution": "Distribución del coeficiente F",
        "f_only_positive": " (solo F > 0)",
        "f_log_scale": " — escala Y log",
        "number_of_individuals": "Número de individuos",
        # Func 3 — clasificador de relaciones
        "rel_parent": "Progenitor (padre/madre)",
        "rel_child": "Descendiente directo (hijo/hija)",
        "rel_sibling_full": "Hermano/hermana de padre y madre completos",
        "rel_sibling_half": "Medio hermano/hermana (un progenitor común)",
        "rel_grandparent": "Abuelo/abuela",
        "rel_grandchild": "Nieto/nieta",
        "rel_uncle_aunt": "Tío/tía",
        "rel_nephew_niece": "Sobrino/sobrina",
        "rel_great_uncle": "Tío abuelo/Tía abuela",
        "rel_great_nephew": "Sobrino nieto/Sobrina nieta",
        "rel_1c": "Primo hermano (1C)",
        "rel_1c1r": "Primo hermano una vez removido (1C1R)",
        "rel_1c2r": "Primo hermano dos veces removido (1C2R)",
        "rel_2c": "Primo segundo (2C)",
        "rel_2c1r": "Primo segundo una vez removido (2C1R)",
        "rel_3c": "Primo tercero (3C)",
        "rel_bisabuelo": "Bisabuelo/bisabuela",
        "rel_bisnieto": "Bisnieto/bisnieta",
        "rel_tatarabuelo": "Tatarabuelo/tatarabuela",
        "rel_distant_rel": "Pariente lejano (dist_a={}, dist_b={})",
        # Func 1 — mapa geográfico
        "geo_lineage": "Linaje Geográfico y Mapa de Migración",
        "geo_select_individual": "Selecciona individuo para mapear ancestros",
        "geo_max_gen": "Máx generaciones a mostrar",
        "geo_generate": "Generar mapa geográfico",
        "geo_no_coords": "No se encontraron ancestros con coordenadas geográficas.",
        "geo_ancestors_no_coords": "Ancestros sin coordenadas (no se pueden mapear):",
        "geo_plotly_missing": "Plotly no está instalado. Añade 'plotly>=5.0.0' a requirements.txt y reinicia.",
        "geo_migration_lines": "Mostrar líneas de migración entre generaciones",
        "geo_n_mapped": "{n} ancestros mapeados, {m} sin coordenadas.",
        # Func 2 — endogamia histórica
        "inbreeding_timeline": "Patrones de Endogamia Histórica",
        "inbreeding_period": "Agrupar por",
        "inbreeding_period_decade": "Década",
        "inbreeding_period_century": "Siglo",
        "inbreeding_period_25yr": "Período de 25 años",
        "inbreeding_generate": "Generar análisis histórico",
        "inbreeding_no_birth": "No se encontraron individuos con año de nacimiento conocido.",
        "inbreeding_n_with_birth": "{n} individuos con año de nacimiento conocido (de {total}).",
        "inbreeding_period_label": "Período",
        "inbreeding_count": "Nº individuos",
        "inbreeding_mean_f": "F media",
        "inbreeding_max_f": "F máxima",
        "inbreeding_median_f": "F mediana",
        "inbreeding_peak_epoch": "Período de mayor endogamia: {} (F media = {:.4f})",
        "inbreeding_chart_title": "F media por {} — Evolución histórica de endogamia",
        "inbreeding_table_title": "Tabla resumen por período",
    }
}
# --- END TRANSLATIONS_REMOVED_PLACEHOLDER (dict kept as dead code, t() from translations.py) ---

# ----------------------------
# Utilities
# ----------------------------
# strip_ns y safe_int_year → modules.shared.utils

def text_of(elem):
    if elem is None:
        return None
    if elem.text and elem.text.strip():
        return elem.text.strip()
    return None

# ----------------------------
# Gramps XML parser — delegated to modules.shared.gramps_parser
# ----------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def _cached_parse_gramps(content_bytes: bytes):
    """Parse puro sin side-effects; cacheable por st.cache_data."""
    db = _parse_gramps_shared(content_bytes)
    return db.to_persons_dict(), db.to_families_dict()


def parse_gramps(content_bytes):
    """
    Parse Gramps XML (bytes). Returns:
      people: dict id -> {id,name,sex,birth_year,place:{'lat','lon','name'}}
      families: dict fid -> {id,husband,wife,children:list}
    Delegates to the unified GrampsDB parser.
    """
    if content_bytes[:4] == b'PK':
        st.error(t("package_error"))
        return {}, {}
    try:
        return _cached_parse_gramps(content_bytes)
    except ValueError as e:
        st.error(str(e))
        return {}, {}
    except Exception as e:
        st.error(t("xml_error").format(e))
        return {}, {}

# ----------------------------
# Build pedigree graph
# ----------------------------
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

# ----------------------------
# Ancestors (BFS up) and paths
# ----------------------------
def ancestors_with_distance(G, start_id, max_gen=10):
    dist = {}
    q = deque()
    for p in G.predecessors(start_id):
        dist[p] = 1
        q.append((p,1))
    while q:
        node, d = q.popleft()
        if d >= max_gen:
            continue
        for par in G.predecessors(node):
            if par not in dist or dist[par] > d+1:
                dist[par] = d+1
                q.append((par, d+1))
    return dist

def find_paths_to_ancestor(G, start, target, max_gen=12, multi=False) -> list | None:
    """
    Encuentra rutas de start → target subiendo por líneas parentales.
    Si multi=False -> devuelve solo la primera ruta encontrada.
    Si multi=True  -> devuelve *todas* las rutas hasta target (lista de listas).

    Optimizaciones vs. versión original:
    - deque + popleft() en lugar de list.pop(0) (O(1) vs O(n) por extracción).
    - multi=False usa parent_map: sin copiar rutas, O(1) por nodo.
    - multi=True usa tuples inmutables: 'path + (parent,)' es más rápido que
      'path + [parent]' porque las tuples no tienen sobre-asignación de buffer.
    """
    if start == target:
        return [[start]] if multi else [start]

    if not multi:
        # BFS con parent tracking: sin copias de ruta, O(1) por nodo.
        parent_map: dict = {start: None}
        q: deque = deque([(start, 0)])
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

    # multi=True: necesitamos todas las rutas — cada entrada de la cola
    # lleva su propia tupla de ruta para no mezclar caminos distintos.
    results = []
    q = deque([(start, (start,))])
    while q:
        current, path = q.popleft()
        if len(path) - 1 >= max_gen:
            continue
        for parent in G.predecessors(current):
            if parent in path:  # evitar ciclos dentro de esta ruta
                continue
            new_path = path + (parent,)
            if parent == target:
                results.append(list(new_path))
                continue
            q.append((parent, new_path))
    return results

def find_common_ancestors(G, a_id, b_id, max_gen=12):
    """
    Devuelve una lista de ancestros comunes entre a_id y b_id.
    Cada elemento es un dict: {'id','name','dist_a','dist_b'} ordenado por dist_a+dist_b asc.
    """
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
            'dist_b': anc_b[cid]
        })
    out.sort(key=lambda x: (x['dist_a'] + x['dist_b'], x['dist_a'], x['dist_b']))
    return out


def get_paths_to_common(G, a_id, b_id, commons_list, max_gen=12):
    """
    Dado commons_list (lista de dicts con 'id'), devuelve para cada ancestro
    las rutas desde A y desde B hasta ese ancestro.
    Resultado: dict anc_id -> {'ancestor_name', 'path_from_A', 'path_from_B'}
    """
    result = {}
    for c in commons_list:
        cid = c['id']
        pA = find_paths_to_ancestor(G, a_id, cid, max_gen=max_gen)
        pB = find_paths_to_ancestor(G, b_id, cid, max_gen=max_gen)
        result[cid] = {
            'ancestor_name': c.get('name', cid),
            'path_from_A': pA,
            'path_from_B': pB
        }
    return result

# ----------------------------
# Inbreeding F (Wright)
# ----------------------------
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

# ----------------------------
# Find consanguinity loops for person
# ----------------------------
def find_consanguinity_for_person(G, person_id, max_gen=8):
    parents = list(G.predecessors(person_id))
    if len(parents) < 2:
        return []
    father, mother = parents[0], parents[1]
    anc_f = ancestors_with_distance(G, father, max_gen=max_gen)
    anc_m = ancestors_with_distance(G, mother, max_gen=max_gen)
    commons = set(anc_f.keys()) & set(anc_m.keys())
    loops = []
    for a in commons:
        path1 = find_paths_to_ancestor(G, father, a, max_gen=max_gen)
        path2 = find_paths_to_ancestor(G, mother, a, max_gen=max_gen)
        loops.append({
            'ancestor': a,
            'ancestor_name': G.nodes[a].get('name', a),
            'dist_from_father': anc_f[a],
            'dist_from_mother': anc_m[a],
            'path_from_father': path1,
            'path_from_mother': path2,
        })
    return loops

def pretty_path(G, path):
    """
    Recibe una lista de IDs como ['I001','I002','I003']
    y devuelve un string legible como:
    'Nombre1 (I001) → Nombre2 (I002) → Nombre3 (I003)'
    """
    if not path:
        return "—"   # guion largo si no hay ruta

    parts = []
    for pid in path:
        name = G.nodes[pid].get("name", pid)
        parts.append(f"{name} ({pid})")

    return " → ".join(parts)

def describe_single_path(G, path):
    """Devuelve texto legible para una ruta [A, B, C, ...] (list of IDs)."""
    if not path:
        return "—"
    return " → ".join([f"{G.nodes[p].get('name', p)} ({p})" for p in path])

def explain_relationship(G, a_id, b_id, cacheF, max_gen=12, max_ancestors_show=5):
    """
    Devuelve (texto_human, summary_dict) explicando la relación entre a_id y b_id.
    Usa: kinship_coefficient, find_common_ancestors, get_paths_to_common, pretty_path.
    """
    out_lines = []
    summary = {}

    # Validación
    if a_id not in G.nodes() or b_id not in G.nodes():
        return "Uno o ambos individuos no están en el grafo.", {}

    # Kinship and classification
    phi = kinship_coefficient(G, a_id, b_id, cacheF, max_gen=max_gen)
    R = kinship_to_R(phi)
    # Obtener ancestros comunes primero para clasificación precisa
    commons = find_common_ancestors(G, a_id, b_id, max_gen=max_gen)
    label, _ = classify_relationship_smart(G, a_id, b_id, phi, commons)

    out_lines.append(t("relationship_between").format(G.nodes[a_id].get('name', a_id), a_id, G.nodes[b_id].get('name', b_id), b_id))
    out_lines.append(f"- {t('kinship')} = {phi:.6f}")
    out_lines.append(f"- {t('r_relationship')} = {R:.6f}")
    out_lines.append(f"- {t('approx_classification')}: {label}")
    out_lines.append("")

    # Ancestros comunes ya calculados arriba
    if not commons:
        out_lines.append(t("no_common_ancestors"))
        summary.update({'phi': phi, 'R': R, 'n_common': 0})
        return "\n".join(out_lines), summary

    out_lines.append(t("common_ancestors_found").format(len(commons), max_ancestors_show))
    # ordenar por suma de distancias (ya lo hace find_common_ancestors)
    commons = commons[:max_ancestors_show]

    # Obtener paths
    paths = get_paths_to_common(G, a_id, b_id, commons, max_gen=max_gen)

    # Para cada ancestro, describir
    contributions = []
    for c in commons:
        cid = c['id']
        cname = c['name']
        dist_a = c['dist_a']
        dist_b = c['dist_b']

        info = paths.get(cid, {})
        path_a = info.get('path_from_A') or find_paths_to_ancestor(G, a_id, cid, max_gen=max_gen)
        path_b = info.get('path_from_B') or find_paths_to_ancestor(G, b_id, cid, max_gen=max_gen)

        out_lines.append(f"\n{t('ancestor')}: {cname} ({cid}) — {t('distances')}: A={dist_a}, B={dist_b}")
        out_lines.append(f"  - {t('path_a_ancestor')}: {describe_single_path(G, path_a)}")
        out_lines.append(f"  - {t('path_b_ancestor')}: {describe_single_path(G, path_b)}")

        # Si el ancestro contribuye al F de uno de ellos (cuando es ancestro común entre los padres de un individuo),
        # estimamos contribución simple para el caso en que A sea ancestro común de padres (si corresponde).
        # calculamos contribución aproximada al parentesco entre A y B vía este ancestro:
        # aproximación: contrib = (1/2)^(dist_a + dist_b + 1) * (1 + F_ancestor)
        FA = compute_inbreeding(G, cid, cacheF, max_gen=max_gen)
        contrib = (0.5 ** (dist_a + dist_b + 1)) * (1.0 + FA)
        contributions.append({'ancestor': cid, 'name': cname, 'dist_a': dist_a, 'dist_b': dist_b, 'contrib': contrib})

        out_lines.append(f"  - {t('approx_contribution')}: {contrib:.6e}")

    # resumen de contribuciones
    contributions = sorted(contributions, key=lambda x: x['contrib'], reverse=True)
    total_contrib = sum([c['contrib'] for c in contributions])
    out_lines.append("")
    out_lines.append(t("summed_contributions").format(total_contrib))
    out_lines.append(t("note_approx"))

    # incluir paths y commons en summary para uso posterior (descarga, visualización)
    summary.update({
        'a_id': a_id, 'b_id': b_id,
        'phi': phi, 'R': R, 'label': label,
        'n_common': len(commons),
        'commons': commons,
        'paths': paths,
        'contributions': contributions
    })

    return "\n".join(out_lines), summary

# ----------------------------
# Kinship and relationship classification
# ----------------------------
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

def classify_relationship_from_phi(phi):
    R = kinship_to_R(phi)
    if R <= 0:
        return t("unrelated"), R
    if R >= 0.45:
        return t("direct_relative"), R
    if R >= 0.22:
        return t("grandparent_uncle"), R
    if R >= 0.11:
        return t("first_cousins"), R
    if R >= 0.04:
        return t("second_cousins"), R
    if R >= 0.01:
        return t("very_distant"), R
    return t("very_remote"), R

def classify_relationship_from_distances(dist_a: int, dist_b: int, n_shared_parents: int = 0) -> str:
    """Clasifica relación por distancias al ancestro común más cercano. Retorna clave de TRANSLATIONS."""
    da, db = sorted([dist_a, dist_b])
    if (da, db) == (1, 1):
        return "rel_sibling_full" if n_shared_parents == 2 else "rel_sibling_half"
    if (da, db) == (1, 2): return "rel_uncle_aunt"
    if (da, db) == (1, 3): return "rel_great_uncle"
    if (da, db) == (1, 4): return "rel_tatarabuelo"
    if (da, db) == (2, 2): return "rel_1c"
    if (da, db) == (2, 3): return "rel_1c1r"
    if (da, db) == (2, 4): return "rel_1c2r"
    if (da, db) == (3, 3): return "rel_2c"
    if (da, db) == (3, 4): return "rel_2c1r"
    if (da, db) == (4, 4): return "rel_3c"
    if da == 1: return "rel_great_uncle"
    return "rel_distant_rel"

def classify_relationship_smart(G, a_id, b_id, phi: float, commons_list: list):
    """
    Clasificación mejorada con lenguaje natural.
    Usa distancias del ancestro común más cercano si commons_list está disponible.
    Mismo contrato de retorno que classify_relationship_from_phi: (label_str, R_float).
    Fallback a classify_relationship_from_phi si no hay información de distancias.
    """
    R = kinship_to_R(phi)
    # Verificar si uno es ancestro directo del otro (hasta 4 generaciones)
    anc_a = ancestors_with_distance(G, a_id, max_gen=4)
    anc_b = ancestors_with_distance(G, b_id, max_gen=4)
    DIRECT_MAP = {1: "rel_parent", 2: "rel_grandparent", 3: "rel_bisabuelo", 4: "rel_tatarabuelo"}
    DIRECT_MAP_INV = {1: "rel_child", 2: "rel_grandchild", 3: "rel_bisnieto"}
    if b_id in anc_a and anc_a[b_id] in DIRECT_MAP:
        return t(DIRECT_MAP[anc_a[b_id]]), R
    if a_id in anc_b and anc_b[a_id] in DIRECT_MAP_INV:
        return t(DIRECT_MAP_INV[anc_b[a_id]]), R
    if not commons_list:
        return classify_relationship_from_phi(phi)
    closest = commons_list[0]
    parents_a = set(G.predecessors(a_id))
    parents_b = set(G.predecessors(b_id))
    n_shared = len(parents_a & parents_b)
    key = classify_relationship_from_distances(closest['dist_a'], closest['dist_b'], n_shared)
    if key == "rel_distant_rel":
        label = t("rel_distant_rel").format(closest['dist_a'], closest['dist_b'])
    else:
        label = t(key)
    return label, R

# ----------------------------
# Find consanguineous couples
# ----------------------------

def normalize_family_id(fdata):
    """
    Normaliza el ID de familia:
    - Si tiene ID corto (Fxxxx), lo devuelve.
    - Si no, genera uno estable basado en el handle.
    """
    fam_id = fdata.get("id")
    if fam_id:
        return fam_id  # el ID que quieres ver en tabla

    handle = fdata.get("handle", "")
    if handle.startswith("_"):
        # generar un ID corto reproducible
        return "F_" + handle[1:7]

    return handle or "F_UNKNOWN"


def find_consanguineous_couples(G, families, cacheF, max_gen=12, min_phi_threshold=0.0):
    couples = []

    for fid, fdata in families.items():

        # normalizar ID de familia (ELIMINA duplicados de handle)
        norm_fid = normalize_family_id(fdata)

        h = fdata.get('husband')
        w = fdata.get('wife')
        if not h or not w:
            continue
        if h not in G.nodes() or w not in G.nodes():
            continue

        phi = kinship_coefficient(G, h, w, cacheF, max_gen=max_gen)
        if phi <= min_phi_threshold:
            continue

        label, R = classify_relationship_from_phi(phi)

        # ancestros
        anc_h = ancestors_with_distance(G, h, max_gen=max_gen)
        anc_w = ancestors_with_distance(G, w, max_gen=max_gen)
        commons = list(set(anc_h.keys()) & set(anc_w.keys()))

        # muestreo de ancestros comunes
        sample_anc = []
        for a in commons[:6]:
            sample_anc.append({
                'id': a,
                'name': G.nodes[a].get('name', a),
                'dist_h': anc_h[a],
                'dist_w': anc_w[a],
            })

        couples.append({
            'family_id': norm_fid,
            'husband': h,
            'husband_name': G.nodes[h].get('name', h),
            'wife': w,
            'wife_name': G.nodes[w].get('name', w),
            'phi': phi,
            'R': R,
            'relationship_label': label,
            'n_children': len(fdata.get('children', [])),
            'common_ancestors_sample': sample_anc
        })

    # ELIMINAR duplicados por pareja (importante)
    dedup = {}
    for c in couples:
        key = (c['family_id'], c['husband'], c['wife'])
        dedup[key] = c

    couples = list(dedup.values())
    couples = sorted(couples, key=lambda x: x['phi'], reverse=True)

    return couples

# ----------------------------
# Visualization helpers (pyvis)
# ----------------------------
def render_pyvis_subtree(G, center_id, gens=4, highlight_ancestors=None, notebook=False):
    net = Network(height='650px', width='100%', directed=True, notebook=notebook)
    nodes_to_show = set()
    edges_to_show = set()
    q = deque()
    nodes_to_show.add(center_id)
    q.append((center_id, 0))
    while q:
        node, depth = q.popleft()
        if depth >= gens:
            continue
        for p in G.predecessors(node):
            nodes_to_show.add(p)
            edges_to_show.add((p, node))
            q.append((p, depth+1))
    # include immediate children for context
    for child in G.successors(center_id):
        nodes_to_show.add(child)
        edges_to_show.add((center_id, child))
    for nid in nodes_to_show:
        label = G.nodes[nid].get('name', nid)
        title = f"{label} ({nid})"
        net.add_node(nid, label=label, title=title)
    for u, v in edges_to_show:
        net.add_edge(u, v)
    if highlight_ancestors:
        for a in highlight_ancestors:
            if a in nodes_to_show:
                try:
                    net.get_node(a)['color'] = 'red'
                    net.get_node(a)['size'] = 25
                except Exception:
                    pass
    net.set_options("""
    var options = {
      "physics": {"barnesHut": {"gravitationalConstant": -8000}},
      "edges": {"arrows": {"to": {"enabled": true}}}
    }
    """)
    return net.generate_html()

def render_pyvis_relationship(G, a_id, b_id, commons, paths, gens=6):
    net = Network(height='650px', width='100%', directed=True)
    nodes = set()
    edges = set()
    nodes.add(a_id)
    nodes.add(b_id)
    colorA = "#3b82f6"
    colorB = "#22c55e"
    colorC = "#ef4444"
    for cid, info in paths.items():
        pA = info['path_from_A'] or []
        pB = info['path_from_B'] or []
        for i in range(len(pA) - 1):
            edges.add((pA[i], pA[i+1])); nodes.add(pA[i]); nodes.add(pA[i+1])
        for i in range(len(pB) - 1):
            edges.add((pB[i], pB[i+1])); nodes.add(pB[i]); nodes.add(pB[i+1])
    commons_ids = {c['id'] for c in commons}
    for n in nodes:
        label = G.nodes[n].get('name', n)
        if n == a_id:
            net.add_node(n, label=label, color=colorA, size=30)
        elif n == b_id:
            net.add_node(n, label=label, color=colorB, size=30)
        elif n in commons_ids:
            net.add_node(n, label=label, color=colorC, size=25)
        else:
            net.add_node(n, label=label)
    for u, v in edges:
        net.add_edge(u, v)
    net.set_options("""
    var options = {
        "physics": {"barnesHut": {"gravitationalConstant": -8000}},
        "edges": {"arrows": {"to": {"enabled": true}}},
        "nodes": {"font": {"size": 14}}
    }
    """)
    return net.generate_html()

# ----------------------------
# 4) Render Pyvis coloreado por F (usa st.session_state['analysis_df'] para map id->F)
# ----------------------------
def render_pyvis_colormap_by_F(G, df_analysis, center_id=None, gens=4):
    """
    Genera HTML Pyvis con nodos coloreados según F (valor entre 0..1).
    df_analysis: DataFrame con columna 'id' y 'F'.
    """
    # build map id -> F
    f_map = {}
    if df_analysis is not None:
        for _, r in df_analysis.iterrows():
            f_map[r['id']] = r['F'] if not pd.isna(r['F']) else 0.0

    net = Network(height='650px', width='100%', directed=True)
    nodes_to_show = set()
    edges_to_show = set()

    if center_id is None:
        # show entire graph (careful with very large graphs)
        nodes_to_show = set(G.nodes())
        for u, v in G.edges():
            edges_to_show.add((u, v))
    else:
        # BFS up to gens and immediate children
        q = deque()
        nodes_to_show.add(center_id)
        q.append((center_id, 0))
        while q:
            node, depth = q.popleft()
            if depth >= gens:
                continue
            for p in G.predecessors(node):
                nodes_to_show.add(p)
                edges_to_show.add((p, node))
                q.append((p, depth+1))
        for child in G.successors(center_id):
            nodes_to_show.add(child)
            edges_to_show.add((center_id, child))

    # define color ramp from low->high (white -> red)
    def f_to_color(f):
        # clamp 0..0.25 (typical F ranges) but allow up to 1
        val = min(max(f, 0.0), 1.0)
        # map to red ramp
        # simple linear gradient to red
        r = int(255 * val)
        g = int(255 * (1 - val))
        b = int(255 * (1 - val))
        return f"#{r:02x}{g:02x}{b:02x}"

    for nid in nodes_to_show:
        label = G.nodes[nid].get('name', nid)
        fval = f_map.get(nid, 0.0)
        color = f_to_color(fval)
        size = 15 + int(40 * min(fval, 1.0))
        net.add_node(nid, label=label, title=f"{label} ({nid})\nF={fval:.6f}", color=color, size=size)

    for u, v in edges_to_show:
        net.add_edge(u, v)

    net.set_options("""
    var options = {
      "physics": {"barnesHut": {"gravitationalConstant": -8000}},
      "edges": {"arrows": {"to": {"enabled": true}}}
    }
    """)
    return net.generate_html()

def render_sidebar():
    """Controles de sidebar de Consanguinidad: uploader + parámetros + botón de análisis."""
    if "cng_analysis_done" not in st.session_state:
        st.session_state["cng_analysis_done"] = False

    st.sidebar.header(t("upload_params"))
    # Mostrar nombre del archivo ya cargado (desde Testigos u otra sección)
    shared_name = st.session_state.get("shared_gramps_name")
    if shared_name and "cng_uploaded_bytes" not in st.session_state:
        st.sidebar.info(f"Usando archivo ya cargado: **{shared_name}**")
    uploaded = st.sidebar.file_uploader(t("upload_file"), type=["gramps", "xml"])
    if uploaded is not None:
        file_bytes = uploaded.read()
        st.session_state["cng_uploaded_bytes"] = file_bytes
        # Compartir con otros módulos
        st.session_state["shared_gramps_bytes"] = file_bytes
        st.session_state["shared_gramps_name"] = uploaded.name
    elif "cng_uploaded_bytes" not in st.session_state and st.session_state.get("shared_gramps_bytes"):
        st.session_state["cng_uploaded_bytes"] = st.session_state["shared_gramps_bytes"]
    st.sidebar.slider(t("max_gen"), min_value=3, max_value=12, value=8, step=1, key="cng_max_gen")
    st.sidebar.number_input(t("f_threshold"), min_value=0.0, max_value=1.0,
                            value=0.0, step=0.0001, format="%.6f", key="cng_f_threshold")
    if st.sidebar.button(t("run_analysis"), key="cng_run_analysis"):
        st.session_state["cng_analysis_done"] = True


@st.cache_data(show_spinner=False, ttl=3600)
def _cached_inbreeding_results(content_bytes: bytes, max_gen: int):
    """BFS de consanguinidad cacheado. Devuelve solo tipos serializables (sin NetworkX).
    El grafo G se reconstruye en render_page porque el pickle de NetworkX
    no garantiza que G.predecessors() funcione correctamente tras deserializar.
    """
    try:
        people, families = _cached_parse_gramps(content_bytes)
    except Exception:
        return [], {}, {}
    if not people:
        return [], {}, {}
    G = build_graph(people, families)
    cache: dict = {}
    results = []
    for pid in G.nodes():
        Fval = compute_inbreeding(G, pid, cache, max_gen=max_gen)
        loops = find_consanguinity_for_person(G, pid, max_gen=max_gen)
        results.append({
            "id": pid,
            "name": G.nodes[pid].get("name", pid),
            "F": Fval,
            "n_common_ancestors": len(loops),
            "common_ancestors": "; ".join(
                [f"{l['ancestor_name']} ({l['ancestor']})" for l in loops]
            ),
            "loops_details": loops,
        })
    return results, cache, families


def render_page():
    """Renderiza la interfaz principal de Consanguinidad."""
    st.title(t("title"))
    if not st.session_state.get("cng_analysis_done", False):
        st.info(t("adjust_params"))
        return
    content = st.session_state.get("cng_uploaded_bytes")
    if content is None:
        st.warning(t("upload_warning"))
        return
    max_gen = st.session_state.get("cng_max_gen", 8)
    f_threshold = st.session_state.get("cng_f_threshold", 0.0)

    with st.spinner("Analizando..."):
        results, cache, families = _cached_inbreeding_results(content, max_gen)

    if not results:
        st.error(t("no_people"))
        return

    # Rebuild graph from parsed data (fast: just node/edge insertion)
    people, fams = _cached_parse_gramps(content)
    G = build_graph(people, fams)

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(by="F", ascending=False)

    st.sidebar.markdown(
        f"**{t('people')}:** {G.number_of_nodes()}  \n"
        f"**{t('families')}:** {len(families)}  \n"
        f"**{t('nodes_graph')}:** {G.number_of_nodes()}  \n"
        f"**{t('edges')}:** {G.number_of_edges()}"
    )

    st.session_state["cng_analysis_df"] = df
    _render_main_content(G, df, cache, max_gen, f_threshold, families)


def _render_main_content(G, df, cache, max_gen, f_threshold, families):
    """Contenido principal de la pantalla de consanguinidad (después del análisis)."""
    # Main layout - summary
    st.header(t("results_summary"))
    st.caption(t("cng_caption_f_summary"))
    col1, col2 = st.columns([2,1])
    with col1:
        st.metric(t("individuals_analyzed"), len(df))
        num_with_F = (df['F'] > 0).sum()
        st.metric(t("individuals_f_positive"), int(num_with_F))
        st.markdown(t("top_10_f"))
        st.dataframe(df[['id','name','F','n_common_ancestors']].head(10).style.format({"F":"{:.6f}"}), height=300)
    with col2:
        st.markdown(t("download_csv"))
        csv = df[['id','name','F','n_common_ancestors','common_ancestors']].to_csv(index=False)
        st.download_button(t("download_csv_summary"), data=csv, file_name="consanguinity_summary.csv", mime="text/csv")

    # ----------------------------
    # Parejas consanguíneas (expander)
    # ----------------------------
    st.markdown("---")
    with st.expander(t("consanguineous_couples"), expanded=False):
        st.caption(t("cng_caption_couples"))

        st.sidebar.markdown(f"### {t('couple_detection')}")
        couple_min_phi = st.sidebar.number_input(
            t("couple_min_phi"),
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.0001,
            format="%.6f"
        )

        couples = find_consanguineous_couples(G, families, cache, max_gen=max_gen, min_phi_threshold=couple_min_phi)

        if not couples:
            st.info(t("no_couples"))
        else:
            rows = []
            for c in couples:
                rows.append({
                    'family_id': c['family_id'],
                    'husband': f"{c['husband_name']} ({c['husband']})",
                    'wife': f"{c['wife_name']} ({c['wife']})",
                    'phi': c['phi'],
                    'R': c['R'],
                    'classification': c['relationship_label'],
                    'n_children': c['n_children'],
                    'sample_common_ancestors': "; ".join([
                        f"{a['name']}({a['id']}) dH={a['dist_h']} dW={a['dist_w']}"
                        for a in c['common_ancestors_sample']
                    ])
                })
            couples_df = pd.DataFrame(rows)
            couples_df['phi_fmt'] = couples_df['phi'].map(lambda x: f"{x:.6f}")
            couples_df['R_fmt'] = couples_df['R'].map(lambda x: f"{x:.6f}")
            st.dataframe(
                couples_df[
                    [
                        'family_id','husband','wife','phi_fmt','R_fmt','classification','n_children','sample_common_ancestors'
                    ]
                ].rename(columns={'phi_fmt':t('phi'),'R_fmt':t('r_coef')}),
                height=350
            )
            csv_couples = couples_df.to_csv(index=False)
            st.download_button(t("download_couples_csv"), data=csv_couples, file_name="parejas_consanguineas.csv", mime="text/csv")

    # ----------------------------
    # Explorador por individuo (expander)
    # ----------------------------
    st.markdown("---")
    with st.expander(t("individual_explorer"), expanded=False):
        st.caption(t("cng_caption_individual"))

        sel = st.selectbox(
            t("select_individual"),
            options=df['id'].tolist(),
            format_func=lambda x: f"{G.nodes[x].get('name', x)} ({x})"
        )

        ind_row = df[df['id'] == sel].iloc[0]

        st.subheader(
            f"{ind_row['name']} — F = {ind_row['F']:.6f} — {t('common_ancestors')}: {ind_row['n_common_ancestors']}"
        )
        st.write(f"{t('common_ancestors_summary')}:")
        st.write(ind_row['common_ancestors'] if ind_row['common_ancestors'] else t("none_found"))

        st.markdown(f"**{t('loop_details')}**")
        loops = ind_row['loops_details']

        if not loops:
            st.info(t("no_loops"))
        else:
            for loop in loops:
                st.markdown(f"- **{t('common_ancestor')}:** {loop['ancestor_name']} ({loop['ancestor']})")
                st.markdown(
                    f"  - {t('dist_from_father')}: {loop['dist_from_father']}, {t('path')}: {pretty_path(G, loop['path_from_father'])}"
                )
                st.markdown(
                    f"  - {t('dist_from_mother')}: {loop['dist_from_mother']}, {t('path')}: {pretty_path(G, loop['path_from_mother'])}"
                )

    # ----------------------------
    # Parentesco entre dos individuos (expander)
    # ----------------------------
    st.markdown("---")
    with st.expander(t("relationship_two"), expanded=False):
        st.caption(t("cng_caption_relationship"))

        colA, colB = st.columns(2)

        with colA:
            indivA = st.selectbox(
                t("select_a"),
                options=list(G.nodes()),
                format_func=lambda x: G.nodes[x].get('name', x)
            )

        with colB:
            indivB = st.selectbox(
                t("select_b"),
                options=list(G.nodes()),
                format_func=lambda x: G.nodes[x].get('name', x)
            )

        # dentro del expander "Parentesco entre dos personas" — después de seleccionar indivA/indivB

        if st.button(t("calc_explain"), key="calc_explain_ab"):
            text, summary = explain_relationship(G, indivA, indivB, cache, max_gen=max_gen, max_ancestors_show=10)
            st.text_area(t("auto_explanation"), value=text, height=420)
            # guardar para visualización/descarga
            st.session_state['cng_last_rel_text'] = text
            st.session_state['cng_last_rel_summary'] = summary

            # ofrecer descarga txt
            st.download_button(t("download_explanation"), data=text, file_name=f"rel_{indivA}_{indivB}.txt",
                               mime="text/plain")

        if st.button(t("visualize_relationship"), key="viz_rel_ab_explain"):
            # Use cached summary only if it belongs to the currently selected pair
            summ = st.session_state.get('cng_last_rel_summary')
            if not summ or summ.get('a_id') != indivA or summ.get('b_id') != indivB:
                _, summ = explain_relationship(G, indivA, indivB, cache, max_gen=max_gen, max_ancestors_show=10)
            commons = summ.get('commons', [])
            paths = summ.get('paths', {})
            html_rel = render_pyvis_relationship(G, indivA, indivB, commons[:5], paths)
            st.components.v1.html(html_rel, height=700, scrolling=True)

    # ----------------------------
    # Visualización subárbol interactivo (expander)
    # ----------------------------
    st.markdown("---")
    with st.expander(t("interactive_subtree"), expanded=False):
        st.caption(t("cng_caption_subtree"))

        gens_vis = st.slider(
            t("gens_up"),
            min_value=2,
            max_value=10,
            value=6
        )

        highlight = set([l['ancestor'] for l in loops]) if 'loops' in locals() else set()
        html_str = render_pyvis_subtree(G, sel, gens=gens_vis, highlight_ancestors=highlight)
        st.components.v1.html(html_str, height=700, scrolling=True)

        def make_full_csv(df, G):
            rows = []
            for _, r in df.iterrows():
                details = []
                for l in r['loops_details']:
                    p1 = pretty_path(G, l.get('path_from_father'))
                    p2 = pretty_path(G, l.get('path_from_mother'))
                    details.append(
                        f"Ancestor:{l['ancestor_name']} ({l['ancestor']}) | p1:{p1} | p2:{p2}"
                    )
                rows.append({
                    'id': r['id'],
                    'name': r['name'],
                    'F': r['F'],
                    'n_common_ancestors': r['n_common_ancestors'],
                    'common_ancestors': r['common_ancestors'],
                    'loops_full': " || ".join(details)
                })
            return pd.DataFrame(rows)

        full_df = make_full_csv(df, G)
        st.download_button(
            t("download_full_csv"),
            data=full_df.to_csv(index=False),
            file_name="consanguinity_full.csv",
            mime="text/csv"
        )

        st.success(t("analysis_complete"))

    # ----------------------------
    # 2) Safe filename helper (añade nombre al export masivo)
    # ----------------------------
    import re
    def safe_filename(pid, name):
        """
        Construye un nombre seguro: ID_Nombre_Apellido.html
        Reemplaza espacios y caracteres problemáticos.
        """
        if not name:
            name = ""
        s = f"{pid}_{name}"
        # keep basic ascii, replace spaces and slashes
        s = s.replace("/", "-").replace("\\", "-")
        s = re.sub(r'\s+', '_', s)
        s = re.sub(r'[^A-Za-z0-9_\-\.]', '', s)
        return s


    # ----------------------------
    # 3) Heatmap (colapso por generación) para un individuo
    # ----------------------------
    import seaborn as sns  # optional but nicer; if not available matplotlib works
    def plot_collapse_heatmap(G, person_id, max_gen=8):
        """
        Devuelve PNG base64 de heatmap: eje X = generación (1..max_gen),
        valor = nº de ancestros repetidos en esa generación (apariciones-1 sumadas).
        """
        # obtener todas rutas hacia ancestros con multi=True
        anc = ancestors_with_distance(G, person_id, max_gen=max_gen)
        if not anc:
            return None

        # Conteo por generación de repeticiones
        gen_counts = np.zeros(max_gen+1, dtype=int)  # index by generation (0 unused)
        for ancestor_id in anc.keys():
            # todas las rutas desde person -> ancestor (multi)
            paths = find_paths_to_ancestor(G, person_id, ancestor_id, max_gen=max_gen, multi=True)
            if not paths:
                continue
            # paths is a list of lists; each path length-1 = generation
            for p in paths:
                gen = len(p) - 1
                if gen <= max_gen:
                    gen_counts[gen] += 1

        # ahora convertimos a "repeticiones" por generación: si en teoría hay 2^g ancestros,
        # y hemos contado N apariciones (incluyendo únicas), las repeticiones = N - unique_count
        # Unique_count estimation: number of distinct ancestor IDs appearing at generation g:
        unique_by_gen = np.zeros(max_gen+1, dtype=int)
        for ancestor_id in anc.keys():
            # determine min generation for that ancestor
            paths = find_paths_to_ancestor(G, person_id, ancestor_id, max_gen=max_gen, multi=True)
            gens = set(len(p)-1 for p in paths if p)
            if gens:
                min_g = min(gens)
                if min_g <= max_gen:
                    unique_by_gen[min_g] += 1

        # repetitions per generation: total aparitions at gen - unique_by_gen
        repetitions = np.zeros(max_gen+1, dtype=int)
        for g in range(1, max_gen+1):
            repetitions[g] = gen_counts[g] - unique_by_gen[g]
            if repetitions[g] < 0:
                repetitions[g] = 0

        # Prepare heatmap matrix (1 row: generations)
        data = repetitions[1:max_gen+1].reshape(1, -1)

        fig, ax = plt.subplots(figsize=(max(6, max_gen*0.6), 2.5))
        sns.heatmap(data, annot=True, fmt="d", cmap="Reds", cbar=True,
                    xticklabels=list(range(1, max_gen+1)), yticklabels=[t("repetitions")])
        ax.set_xlabel(t("generation"))
        ax.set_title(t("ancestor_repetitions_by_gen").format(person_id))

        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode()

    # ============================================================
    # (F) COLAPSO DE PEDIGRÍ
    # ============================================================

    def compute_pedigree_collapse(G, person_id, max_gen=6):
        """
        Calcula el colapso de pedigrí para un individuo:
        - ancestros teóricos = 2^n
        - ancestros reales únicos
        - detección de ancestros repetidos y cuántas veces aparecen
        """
        # map: ancestro -> dist (mínimo) y count de apariciones
        ancestors = ancestors_with_distance(G, person_id, max_gen=max_gen)

        # Para detectar repetición necesitamos buscar TODAS rutas posibles
        all_paths = defaultdict(list)

        # Extra todas las rutas a cada ancestro encontrado
        for anc, dist in ancestors.items():
            p = find_paths_to_ancestor(G, person_id, anc, max_gen=max_gen, multi=True)
            for path in p:
                all_paths[anc].append(path)

        # Contar repeticiones
        collapse_info = []
        for anc, paths in all_paths.items():
            name = G.nodes[anc].get("name", anc)
            count = len(paths)
            min_gen = min(len(p) for p in paths) - 1  # profundidad mínima
            collapse_info.append({
                "id": anc,
                "name": name,
                "appearances": count,
                "min_generation": min_gen
            })

        # ancestros teóricos
        theoretical = 2 ** max_gen
        real_unique = len(all_paths)

        collapse_coeff = 1 - (real_unique / theoretical)

        # ordenar por apariciones y generación
        collapse_info.sort(key=lambda x: (-x["appearances"], x["min_generation"]))

        return collapse_coeff, collapse_info


    # ============================================================
    # (G) GENERACIÓN DE INFORMES HTML y PDF
    # ============================================================
    import base64
    from jinja2 import Template

    def render_html_report(person, G, F_value, loops, collapse_coeff, collapse_info):
        """
        Devuelve un HTML completo con todos los datos relevantes.
        Puedes personalizar la plantilla fácilmente.
        """

        template_str = """
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                h1 { color: #333; }
                h2 { color: #555; border-bottom: 1px solid #ccc; padding-bottom: 4px; }
                table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                th, td { border: 1px solid #ccc; padding: 6px; text-align: left; }
                th { background: #eee; }
                .small { font-size: 0.9em; color: #777; }
            </style>
        </head>
        <body>

        <h1>Informe Genealógico</h1>
        <h2>Información básica</h2>
        <p><b>Individuo:</b> {{ person.name }} ({{ person.id }})</p>

        <h2>Coeficiente de Inbreeding (F)</h2>
        <p><b>F = {{ F_value }}</b></p>

        <h2>Colapso de Pedigrí</h2>
        <p><b>Coeficiente de colapso:</b> {{ collapse_coeff }}</p>

        <table>
            <tr><th>Ancestro</th><th>ID</th><th>Apariciones</th><th>Generación mínima</th></tr>
            {% for row in collapse_info %}
            <tr>
                <td>{{ row.name }}</td>
                <td>{{ row.id }}</td>
                <td>{{ row.appearances }}</td>
                <td>{{ row.min_generation }}</td>
            </tr>
            {% endfor %}
        </table>

        <h2>Bucles de Consanguinidad</h2>
        {% if loops %}
            <ul>
            {% for l in loops %}
                <li>
                    Ancestro común: <b>{{ l.ancestor_name }}</b>
                    ({{ l.ancestor }})<br>
                    Vía padre: {{ l.path_from_father }}<br>
                    Vía madre: {{ l.path_from_mother }}
                </li>
            {% endfor %}
            </ul>
        {% else %}
            <p>No hay bucles detectados.</p>
        {% endif %}

        </body>
        </html>
        """

        template = Template(template_str)
        return template.render(
            person=person,
            F_value=F_value,
            loops=loops,
            collapse_coeff=round(collapse_coeff, 6),
            collapse_info=collapse_info,
        )


    def html_to_pdf_bytes(html):
        import pdfkit

        # CONFIGURACIÓN DEL EJECUTABLE WKHTMLTOPDF
        config = pdfkit.configuration(
            wkhtmltopdf=r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
        )

        return pdfkit.from_string(html, False, configuration=config)


    # ============================================================
    # UI PARA (F) + (G)
    # ============================================================
    st.markdown("---")
    with st.expander(t("pedigree_collapse_reports"), expanded=False):
        st.caption(t("cng_caption_collapse"))

        person_id = st.selectbox(
            t("select_individual"),
            options=list(G.nodes()),
            format_func=lambda x: G.nodes[x].get('name', x),
            key="collapse_person_select"
        )

        max_gen_collapse = st.slider(
            t("gens_analyze_collapse"),
            min_value=3, max_value=10, value=6
        )

        if st.button(t("calc_collapse")):

            collapse_coeff, collapse_info = compute_pedigree_collapse(G, person_id, max_gen=max_gen_collapse)

            st.subheader(t("collapse_result"))
            st.write(f"**{t('collapse_coeff')}:** {collapse_coeff:.6f}")

            st.dataframe(pd.DataFrame(collapse_info))

            # Guardar en sesión para informes
            st.session_state["cng_collapse_result"] = {
                "coeff": collapse_coeff,
                "info": collapse_info
            }

        st.markdown("---")
        st.subheader(t("generate_report"))

        report_type = st.radio(t("format"), ["HTML", "PDF"])

        if st.button(t("generate_full_report")):

            if "collapse_result" not in st.session_state or "analysis_df" not in st.session_state:
                st.error(t("run_analysis_first"))
            else:
                # Obtener datos necesarios del individuo
                row = st.session_state["cng_analysis_df"][st.session_state["cng_analysis_df"]["id"] == person_id].iloc[0]

                loops = row["loops_details"]

                collapse_coeff = st.session_state["cng_collapse_result"]["coeff"]
                collapse_info = st.session_state["cng_collapse_result"]["info"]

                html = render_html_report(
                    person=row,
                    G=G,
                    F_value=row["F"],
                    loops=loops,
                    collapse_coeff=collapse_coeff,
                    collapse_info=collapse_info
                )

                if report_type == "HTML":
                    st.download_button(
                        t("download_html"),
                        data=html,
                        file_name=f"informe_{person_id}.html"
                    )

                else:
                    pdf_bytes = html_to_pdf_bytes(html)
                    st.download_button(
                        t("download_pdf"),
                        data=pdf_bytes,
                        file_name=f"informe_{person_id}.pdf",
                        mime="application/pdf"
                    )
    # ============================================================
    # (H) VISUALIZACIÓN RADIAL DEL COLAPSO DE PEDIGRÍ
    # ============================================================
    import matplotlib.pyplot as plt
    import io
    import base64
    import numpy as np

    def plot_radial_pedigree_wheel(collapse_info, max_gen):
        """
        Dibuja una rueda radial donde cada ancestro ocupa un punto en un anillo
        según su generación mínima. Ancestros repetidos se colorean intensamente.
        """
        if not collapse_info:
            return None

        gens = [row["min_generation"] for row in collapse_info]
        counts = [row["appearances"] for row in collapse_info]
        names = [row["name"] for row in collapse_info]

        fig = plt.figure(figsize=(6,6))
        ax = fig.add_subplot(111, polar=True)

        # separa el círculo en n puntos
        theta = np.linspace(0, 2*np.pi, len(gens), endpoint=False)

        # radios = generación → más generación → más lejos del centro
        radii = np.array(gens) + 1

        # color por nº de repeticiones
        colors = np.array(counts)

        scatter = ax.scatter(theta, radii, c=colors, cmap="Reds", s=120, alpha=0.9)

        ax.set_yticklabels([])
        ax.set_xticklabels([])
        ax.set_title(t("radial_pedigree_collapse"), fontsize=14, pad=20)

        # Añadir barra de color
        cbar = fig.colorbar(scatter, ax=ax, pad=0.1)
        cbar.set_label(t("ancestor_repetition_count"))

        # Convertir a imagen base64 para Streamlit
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)

        return base64.b64encode(buf.getvalue()).decode()


    # ----------------------------
    # 1) Histograma mejorado (F>0, log-y opcional, KDE opcional)
    # ----------------------------
    import matplotlib.pyplot as plt
    import numpy as np
    import io
    import base64

    def plot_histogram_F_enhanced(df, only_positive=True, log_y=False, bins=20, show_kde=False):
        """
        Devuelve PNG base64 del histograma mejorado de F.
        - only_positive: si True, muestra solo individuos con F>0
        - log_y: si True, eje Y en escala logarítmica
        - show_kde: si True, dibuja una curva KDE sobre el histograma (simple)
        """
        if df is None or df.shape[0] == 0:
            return None

        if only_positive:
            data = df[df["F"] > 0]["F"].values
        else:
            data = df["F"].values

        if len(data) == 0:
            return None

        fig, ax = plt.subplots(figsize=(8,4))
        ax.hist(data, bins=bins, edgecolor='black', alpha=0.75)

        if log_y:
            ax.set_yscale('log')

        ax.set_xlabel("F")
        ax.set_ylabel(t("number_of_individuals"))
        title = t("f_distribution")
        if only_positive:
            title += t("f_only_positive")
        if log_y:
            title += t("f_log_scale")
        ax.set_title(title)

        # simple KDE aproximada (gaussian smoothing) si se pide
        if show_kde and len(data) > 3:
            from scipy.stats import gaussian_kde
            kde = gaussian_kde(data)
            xs = np.linspace(min(data), max(data), 200)
            ys = kde(xs) * len(data) * (xs[1]-xs[0]) * (bins/ (max(data)-min(data)+1e-12))
            ax.plot(xs, ys, color='red', linewidth=1.5, label='KDE')
            ax.legend()

        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode()


    # ============================================================
    # (J) LINAJE GEOGRÁFICO Y MIGRACIÓN
    # ============================================================

    def build_ancestor_geo_data(G, person_id: str, cache: dict, max_gen: int = 8):
        """
        Recopila datos geográficos de los ancestros de person_id.
        Retorna (mapped, unmapped) — listas de dicts.
          mapped:   [{'id','name','lat','lon','gen','F','place_name'}, ...]
          unmapped: [{'id','name','gen','place_name'}, ...]
        """
        mapped, unmapped = [], []
        anc_dist = ancestors_with_distance(G, person_id, max_gen=max_gen)
        for anc_id, gen in anc_dist.items():
            node = G.nodes[anc_id]
            name = node.get('name', anc_id)
            place = node.get('place')
            lat = place.get('lat') if place else None
            lon = place.get('lon') if place else None
            place_name = place.get('name', '') if place else ''
            F_val = compute_inbreeding(G, anc_id, cache, max_gen=max_gen)
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
        import math
        from collections import defaultdict as _dd

        GEN_COLORS = [
            '#e6194b', '#f58231', '#ffe119', '#3cb44b', '#42d4f4',
            '#4363d8', '#911eb4', '#a9a9a9', '#f032e6', '#bfef45', '#fabed4', '#aaffc3'
        ]

        coord_map = {pt['id']: pt for pt in mapped}

        by_gen = _dd(list)
        for pt in mapped:
            by_gen[pt['gen']].append(pt)

        all_lats = [pt['lat'] for pt in mapped]
        all_lons = [pt['lon'] for pt in mapped]
        center_lat = sum(all_lats) / len(all_lats)
        center_lon = sum(all_lons) / len(all_lons)

        def bearing_deg(lat1, lon1, lat2, lon2):
            """Rumbo geodésico p1→p2 en grados desde el norte (0=N, 90=E)."""
            lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
            dlon = lon2 - lon1
            x = math.sin(dlon) * math.cos(lat2)
            y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
            return (math.degrees(math.atan2(x, y)) + 360) % 360

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

                    # Segmentos de línea separados con None
                    line_lons += [plon, clon, None]
                    line_lats += [plat, clat, None]

                    # Flecha en el punto medio, orientada según el rumbo
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
                # symbol='arrow' con angle[] funciona en go.Scattermap (Plotly 6+)
                # con cualquier estilo de mapa, incluido open-street-map
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

        # Puntos de ancestros por generación (encima de flechas)
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


    # ============================================================
    # (K) ENDOGAMIA HISTÓRICA
    # ============================================================

    def compute_inbreeding_timeline(G, cache: dict, max_gen: int, period: str = 'decade') -> pd.DataFrame:
        """
        Agrupa individuos por período de nacimiento y calcula estadísticas de F.
        Retorna DataFrame vacío si nadie tiene birth conocido.
        Columnas: period_label, period_start, count, mean_F, max_F, median_F
        """
        import math as _math

        def get_period(year, period):
            if period == 'decade':
                s = (year // 10) * 10
                return f"{s}s", s
            elif period == 'century':
                cn = _math.ceil(year / 100) if year > 0 else _math.floor(year / 100)
                s = (cn - 1) * 100 + 1
                e = cn * 100
                return f"{s}–{e}", s
            else:  # 25yr
                s = (year // 25) * 25
                return f"{s}–{s + 24}", s

        rows = []
        for pid in G.nodes():
            birth = G.nodes[pid].get('birth')
            if birth is None:
                continue
            birth = safe_int_year(birth) if not isinstance(birth, int) else birth
            if birth is None:
                continue
            F_val = compute_inbreeding(G, pid, cache, max_gen=max_gen)
            lbl, start = get_period(birth, period)
            rows.append({'period_label': lbl, 'period_start': start, 'F': F_val})

        if not rows:
            return pd.DataFrame()

        raw = pd.DataFrame(rows)
        grp = raw.groupby(['period_label', 'period_start'])['F'].agg(
            count='count', mean_F='mean', max_F='max', median_F='median'
        ).reset_index()
        return grp.sort_values('period_start')


    def plot_inbreeding_timeline(timeline_df: pd.DataFrame, period_name: str):
        """
        Figura Plotly de línea+área con evolución histórica del F.
        Lanza ImportError si plotly no está instalado.
        """
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=timeline_df['period_label'], y=timeline_df['mean_F'],
            mode='lines+markers', name='Mean F',
            line=dict(color='#d62728', width=2), marker=dict(size=8),
            fill='tozeroy', fillcolor='rgba(214,39,40,0.15)',
        ))
        fig.add_trace(go.Scatter(
            x=timeline_df['period_label'], y=timeline_df['max_F'],
            mode='lines', name='Max F',
            line=dict(color='#ff7f0e', width=1.5, dash='dot'),
        ))
        fig.add_trace(go.Scatter(
            x=timeline_df['period_label'], y=timeline_df['median_F'],
            mode='lines', name='Median F',
            line=dict(color='#1f77b4', width=1.5, dash='dash'),
        ))
        fig.update_layout(
            title=t("inbreeding_chart_title").format(period_name),
            xaxis_title=t("inbreeding_period_label"),
            yaxis_title="F",
            xaxis=dict(tickangle=-45),
            height=420,
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
            margin=dict(l=50, r=20, t=60, b=100),
        )
        return fig


    # ============================================================
    # (L) EXPORTACIÓN MASIVA DE INFORMES
    # ============================================================

    def generate_mass_reports(G, df, cache, max_gen, collapse_gen):
        """
        Devuelve un diccionario id → HTML report listo para descargar en ZIP.
        NO genera PDF para evitar tamaños demasiado grandes.
        """
        reports = {}

        for _, row in df.iterrows():
            pid = row["id"]

            # colapso del pedigrí individual
            collapse_coeff, collapse_info = compute_pedigree_collapse(G, pid, max_gen=collapse_gen)

            html = render_html_report(
                person=row,
                G=G,
                F_value=row["F"],
                loops=row["loops_details"],
                collapse_coeff=collapse_coeff,
                collapse_info=collapse_info
            )

            reports[pid] = html

        return reports


    # ============================================================
    # (K) ANÁLISIS GLOBAL DE COLAPSO EN TODO EL ÁRBOL
    # ============================================================

    def global_collapse_scan(G, ids, max_gen):
        """
        Calcula el colapso de pedigrí para TODOS los individuos.
        Devuelve una tabla ordenada por mayor colapso.
        """
        rows = []

        for pid in ids:
            coeff, info = compute_pedigree_collapse(G, pid, max_gen=max_gen)
            name = G.nodes[pid].get("name", pid)
            rows.append({
                "id": pid,
                "name": name,
                "collapse": coeff,
                "unique_ancestors": len(info),
                "top_ancestor": info[0]["name"] if info else None,
                "top_ancestor_reps": info[0]["appearances"] if info else None
            })

        df = pd.DataFrame(rows)
        df = df.sort_values(by="collapse", ascending=False)
        return df


    # ============================================================
    # ============================================================
    # UI LINAJE GEOGRÁFICO
    # ============================================================
    st.markdown("---")
    with st.expander(t("geo_lineage"), expanded=False):
        st.caption(t("cng_caption_geo"))
        geo_ind = st.selectbox(
            t("geo_select_individual"),
            options=list(G.nodes()),
            format_func=lambda x: G.nodes[x].get('name', x),
            key="geo_ind_select"
        )
        geo_max_gen = st.slider(t("geo_max_gen"), 2, 12, 6, key="geo_gen_slider")
        show_mig_lines = st.checkbox(t("geo_migration_lines"), value=True, key="geo_show_lines")

        if st.button(t("geo_generate"), key="geo_generate_btn"):
            try:
                import plotly
            except ImportError:
                st.error(t("geo_plotly_missing"))
                return
            mapped, unmapped = build_ancestor_geo_data(G, geo_ind, cache, max_gen=geo_max_gen)
            st.info(t("geo_n_mapped").format(n=len(mapped), m=len(unmapped)))
            if mapped:
                fig_geo = plot_geo_migration(mapped, show_lines=show_mig_lines, G=G, person_id=geo_ind)
                st.plotly_chart(fig_geo, use_container_width=True)
            else:
                st.warning(t("geo_no_coords"))
            if unmapped:
                st.markdown(f"**{t('geo_ancestors_no_coords')}**")
                st.dataframe(
                    pd.DataFrame(unmapped)[['name', 'gen', 'place_name', 'id']],
                    height=200
                )

    # ============================================================
    # UI COMPLETA DE H + I + J + K
    # ============================================================
    st.markdown("---")
    with st.expander(t("advanced_viz"), expanded=False):
        st.caption(t("cng_caption_advanced"))

        st.markdown(f"### {t('radial_wheel')}")
        pid_radial = st.selectbox(
            t("select_radial"),
            options=list(G.nodes()),
            format_func=lambda x: G.nodes[x].get('name', x),
            key="radial_select"
        )
        gen_radial = st.slider(t("gens_show"), 3, 10, 6)

        if st.button(t("generate_radial")):
            coeff, info = compute_pedigree_collapse(G, pid_radial, max_gen=gen_radial)
            img64 = plot_radial_pedigree_wheel(info, gen_radial)
            if img64:
                st.image(base64.b64decode(img64))
            else:
                st.info(t("no_collapse_detected"))

        st.markdown("---")
        st.markdown(f"### {t('global_f_histogram')}")
        if st.button(t("show_histogram")):
            if "analysis_df" in st.session_state:
                img64 = plot_histogram_F_enhanced(st.session_state["cng_analysis_df"])
                st.image(base64.b64decode(img64))
            else:
                st.error(t("run_main_first"))

        # --- Histograma mejorado controls ---
        st.markdown("---")
        st.markdown(f"### {t('enhanced_histogram')}")
        col_h1, col_h2, col_h3 = st.columns([2, 2, 1])
        with col_h1:
            only_pos = st.checkbox(t("show_only_positive"), value=True, key="hist_only_pos")
        with col_h2:
            use_log = st.checkbox(t("log_y_scale"), value=False, key="hist_logy")
        with col_h3:
            show_kde = st.checkbox(t("show_kde"), value=False, key="hist_kde")

        if st.button(t("generate_enhanced_hist")):
            if "analysis_df" in st.session_state:
                img64 = plot_histogram_F_enhanced(st.session_state["cng_analysis_df"], only_positive=only_pos, log_y=use_log,
                                                  bins=30, show_kde=show_kde)
                if img64:
                    st.image(base64.b64decode(img64))
                else:
                    st.info(t("no_data_histogram"))
            else:
                st.error(t("run_main_first"))

        st.markdown("---")
        st.markdown(f"### {t('inbreeding_timeline')}")
        period_options = {
            t("inbreeding_period_decade"): 'decade',
            t("inbreeding_period_century"): 'century',
            t("inbreeding_period_25yr"): '25yr',
        }
        period_label_sel = st.selectbox(
            t("inbreeding_period"),
            options=list(period_options.keys()),
            key="ib_period_select"
        )
        period_sel = period_options[period_label_sel]

        if st.button(t("inbreeding_generate"), key="ib_generate_btn"):
            if "analysis_df" not in st.session_state:
                st.error(t("run_main_first"))
            else:
                try:
                    import plotly
                except ImportError:
                    st.error(t("geo_plotly_missing"))
                    return
                total = G.number_of_nodes()
                n_with_birth = sum(1 for pid in G.nodes() if G.nodes[pid].get('birth') is not None)
                if n_with_birth == 0:
                    st.warning(t("inbreeding_no_birth"))
                else:
                    st.info(t("inbreeding_n_with_birth").format(n=n_with_birth, total=total))
                    timeline_df = compute_inbreeding_timeline(G, cache, max_gen=max_gen, period=period_sel)
                    if timeline_df.empty:
                        st.warning(t("inbreeding_no_birth"))
                    else:
                        peak = timeline_df.loc[timeline_df['mean_F'].idxmax()]
                        st.success(t("inbreeding_peak_epoch").format(peak['period_label'], peak['mean_F']))
                        st.plotly_chart(
                            plot_inbreeding_timeline(timeline_df, period_label_sel),
                            use_container_width=True
                        )
                        st.markdown(f"**{t('inbreeding_table_title')}**")
                        disp = timeline_df[['period_label', 'count', 'mean_F', 'max_F', 'median_F']].copy()
                        disp.columns = [
                            t("inbreeding_period_label"), t("inbreeding_count"),
                            t("inbreeding_mean_f"), t("inbreeding_max_f"), t("inbreeding_median_f")
                        ]
                        st.dataframe(
                            disp.style.format({
                                t("inbreeding_mean_f"): "{:.6f}",
                                t("inbreeding_max_f"):  "{:.6f}",
                                t("inbreeding_median_f"): "{:.6f}",
                            }),
                            height=300
                        )
                        st.download_button(
                            t("download_csv"),
                            data=timeline_df.to_csv(index=False),
                            file_name="inbreeding_timeline.csv",
                            mime="text/csv",
                            key="ib_dl_csv"
                        )

        st.markdown("---")
        st.markdown(f"### {t('mass_export')}")
        collapse_gen_mass = st.slider(t("gens_collapse_mass"), 3, 10, 6, key="mass_gen_slider")
        if st.button(t("generate_mass")):
            if "analysis_df" not in st.session_state:
                st.error(t("run_main_first"))
            else:
                mass = generate_mass_reports(
                    G,
                    st.session_state["cng_analysis_df"],
                    cache,
                    max_gen=max_gen,
                    collapse_gen=collapse_gen_mass
                )

                # generar ZIP
                import zipfile
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zf:
                    for pid, html in mass.items():
                        # get name from df if available
                        row = st.session_state["cng_analysis_df"]
                        name = ""
                        try:
                            # attempt to get name from df
                            r = row[row['id'] == pid]
                            if not r.empty:
                                name = r.iloc[0]['name']
                        except Exception:
                            name = ""
                        filename = safe_filename(pid, name) + ".html"
                        zf.writestr(filename, html)

                st.download_button(
                    t("download_zip"),
                    data=zip_buffer.getvalue(),
                    file_name="informes_masivos.zip",
                    mime="application/zip"
                )

        st.markdown("---")
        st.markdown(f"### {t('global_collapse_exploration')}")
        gen_global = st.slider(t("gens_global_analysis"), 3, 10, 6, key="global_gen_slider")
        if st.button(t("scan_global_collapse")):
            ids = list(G.nodes())
            df_global = global_collapse_scan(G, ids, gen_global)
            st.dataframe(df_global)
            csv = df_global.to_csv(index=False)
            st.download_button(t("download_csv"), data=csv, file_name="colapso_global.csv")

        st.markdown("---")

        # --- Heatmap colapso por generación (por individuo) ---
        st.markdown(f"### {t('heatmap_collapse_gen')}")
        pid_for_heat = st.selectbox(t("select_heatmap"), options=list(G.nodes()), format_func=lambda x: G.nodes[x].get('name', x), key="heatmap_select")
        gen_for_heat = st.slider(t("max_gens_heatmap"), 3, 12, 8, key="heatmap_gen_slider")

        if st.button(t("generate_heatmap")):
            img64 = plot_collapse_heatmap(G, pid_for_heat, max_gen=gen_for_heat)
            if img64:
                st.image(base64.b64decode(img64))
            else:
                st.info(t("no_repetitions"))

        st.markdown("---")

        # --- Pyvis coloreado por F ---
        st.markdown(f"### {t('tree_colored_f')}")
        center_for_colormap = st.selectbox(t("select_center"), options=[None] + list(G.nodes()), format_func=lambda x: t("entire_graph") if x is None else G.nodes[x].get('name', x), key="colormap_select")
        gens_for_colormap = st.slider(t("gens_upward_show"), 2, 8, 4, key="colormap_gen_slider")

        if st.button(t("generate_pyvis_f")):
            df_anal = st.session_state.get("cng_analysis_df", None)
            if df_anal is None:
                st.error(t("run_main_first"))
            else:
                # center_for_colormap can be None
                html = render_pyvis_colormap_by_F(G, df_anal, center_id=center_for_colormap, gens=gens_for_colormap)
                st.components.v1.html(html, height=700, scrolling=True)
