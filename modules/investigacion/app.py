"""
Módulo Investigación — Agenda de tareas genealógicas unificada.

Agrega hallazgos accionables de todos los módulos (General, Family Completion,
Testigos, Resolución árbol–testigos) en una lista priorizada de tareas con
estado persistente entre sesiones.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import streamlit as st

from translations import t
from modules.investigacion.task_engine import (
    ResearchTask,
    generate_tasks_from_general,
    generate_tasks_from_family_completion,
    generate_tasks_from_testigos,
    generate_tasks_from_identity_resolution,
    merge_with_stored,
    load_tasks,
    save_tasks,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).resolve().parents[2]
DATA_DIR   = BASE_DIR / "data"
TASKS_FILE = DATA_DIR / "research_tasks.json"
DATA_DIR.mkdir(exist_ok=True)

IDENTITY_RESOLUTION_FILE = DATA_DIR / "identity_resolution_results.json"
FAMILY_COMPLETION_FILE   = DATA_DIR / "family_completion_results.json"
DISMISSED_FILE           = DATA_DIR / "dismissed_inconsistencies.json"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de carga de datos auxiliares
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _cached_parse(content_bytes: bytes):
    from modules.shared.gramps_parser import parse_gramps
    return parse_gramps(content_bytes)


def _load_dismissed_keys() -> set:
    try:
        if DISMISSED_FILE.exists():
            return set(json.loads(DISMISSED_FILE.read_text(encoding='utf-8')))
    except Exception:
        pass
    return set()


def _load_family_completion_results() -> list:
    try:
        if FAMILY_COMPLETION_FILE.exists():
            return json.loads(FAMILY_COMPLETION_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return []


def _load_identity_resolution_results() -> list:
    try:
        if IDENTITY_RESOLUTION_FILE.exists():
            return json.loads(IDENTITY_RESOLUTION_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return []


def _load_confirmed_pids() -> set:
    """Obtiene los orphan_pid ya confirmados en family_completion_results.json."""
    results = _load_family_completion_results()
    return {r.get('orphan_pid', '') for r in results if r.get('confirmed')}


def _build_pending_clusters(df, conf_data: dict, threshold: int = 78) -> list:
    """Construye clusters de testigos pendientes de confirmar."""
    try:
        from modules.testigos.analysis import build_similarity_clusters
        import pandas as _pd

        if df is None or df.empty:
            return []

        unique_raws = df['witness_raw'].dropna().unique().tolist() if 'witness_raw' in df.columns else []
        if not unique_raws:
            return []

        clusters = build_similarity_clusters(unique_raws, threshold=threshold)
        status_map = conf_data.get('status', {})
        event_groups = conf_data.get('event_groups', {})

        result = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            events_in_cluster = []
            for raw in cluster:
                canon_events = df[df['witness_raw'] == raw] if 'witness_raw' in df.columns else _pd.DataFrame()
                events_in_cluster.extend(canon_events['event_id'].tolist() if 'event_id' in canon_events.columns else [])

            n_eventos = len(events_in_cluster)
            pending_events = [e for e in events_in_cluster if status_map.get(e, {}).get('state') not in ('same', 'different', 'reviewed')]
            n_pendientes = len(pending_events)

            if n_pendientes == 0:
                continue

            # Rango de fechas
            dates = []
            for raw in cluster:
                evs = df[df['witness_raw'] == raw] if 'witness_raw' in df.columns else _pd.DataFrame()
                if 'date_iso' in evs.columns:
                    for d in evs['date_iso'].dropna():
                        try:
                            y = int(str(d)[:4])
                            if 1000 < y < 2100:
                                dates.append(y)
                        except Exception:
                            pass
            rango = f"{min(dates)}–{max(dates)}" if dates else ''

            # Lugares
            lugares = []
            for raw in cluster:
                evs = df[df['witness_raw'] == raw] if 'witness_raw' in df.columns else _pd.DataFrame()
                if 'place_name' in evs.columns:
                    lugares.extend(evs['place_name'].dropna().unique().tolist())
            lugares_str = '; '.join(list(dict.fromkeys(lugares))[:5])

            prioridad = n_eventos * (n_pendientes / max(n_eventos, 1))

            result.append({
                'variantes':           ' / '.join(cluster),
                'n_variantes':         len(cluster),
                'n_eventos':           n_eventos,
                'n_pendientes':        n_pendientes,
                'rango_fechas':        rango,
                'lugares':             lugares_str,
                'incertidumbre_bayes': n_pendientes / max(n_eventos, 1),
                'prioridad':           prioridad,
            })
        return result
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Regeneración de tareas
# ─────────────────────────────────────────────────────────────────────────────

def _regenerate_tasks(content_bytes: bytes) -> None:
    """Genera tareas de todos los módulos, hace merge con estado guardado."""
    all_tasks: list[ResearchTask] = []

    # ── General ───────────────────────────────────────────────────────────────
    try:
        import networkx as nx
        from modules.general.app import (
            compute_file_statistics,
            compute_windowed_stats,
        )
        db = _cached_parse(content_bytes)
        people_ext  = db.to_persons_ext()
        families_ext= db.to_families_ext()
        stats       = compute_file_statistics(people_ext, families_ext)
        windowed    = compute_windowed_stats(people_ext, families_ext)

        # Build graph
        from modules.general.app import build_graph
        G = build_graph(people_ext, families_ext)

        # Record dates
        record_dates_file = DATA_DIR / "gen_record_dates.json"
        record_dates = {}
        if record_dates_file.exists():
            try:
                record_dates = json.loads(record_dates_file.read_text(encoding='utf-8'))
            except Exception:
                pass

        dismissed = _load_dismissed_keys()
        general_tasks = generate_tasks_from_general(
            people_ext    = people_ext,
            families_ext  = families_ext,
            stats         = stats,
            graph         = G,
            dismissed_keys= dismissed,
            windowed_stats= windowed,
            record_dates  = record_dates,
        )
        all_tasks.extend(general_tasks)
    except Exception:
        pass

    # ── Family Completion ─────────────────────────────────────────────────────
    try:
        fc_results      = _load_family_completion_results()
        confirmed_pids  = _load_confirmed_pids()
        fc_tasks = generate_tasks_from_family_completion(
            results_json  = fc_results,
            confirmed_pids= confirmed_pids,
        )
        all_tasks.extend(fc_tasks)
    except Exception:
        pass

    # ── Testigos (clusters pendientes) ───────────────────────────────────────
    try:
        tst_df   = st.session_state.get('tst_df_global') or st.session_state.get('df')
        tst_conf = st.session_state.get('tst_conf', {})
        if tst_df is not None and not tst_df.empty:
            clusters = _build_pending_clusters(tst_df, tst_conf)
            wit_tasks = generate_tasks_from_testigos(clusters)
            all_tasks.extend(wit_tasks)
    except Exception:
        pass

    # ── Identity Resolution ───────────────────────────────────────────────────
    try:
        ir_results = _load_identity_resolution_results()
        ir_tasks   = generate_tasks_from_identity_resolution(ir_results)
        all_tasks.extend(ir_tasks)
    except Exception:
        pass

    # ── Merge con estado guardado ─────────────────────────────────────────────
    stored   = load_tasks(TASKS_FILE)
    merged   = merge_with_stored(all_tasks, stored)
    st.session_state['inv_tasks'] = [dataclasses.asdict(t_) for t_ in merged]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de UI
# ─────────────────────────────────────────────────────────────────────────────

_PRIORITY_LABELS = {1: "🔴 Crítica", 2: "🟡 Alta", 3: "🟢 Media"}
_TYPE_ICONS = {
    'find_parents':             "👨‍👩‍👧",
    'confirm_parent_candidate': "🔎",
    'resolve_inconsistency':    "⚠️",
    'confirm_witness_identity': "👥",
    'resolve_tree_link':        "🌳",
}
_STATUS_OPTIONS = ['pending', 'in_progress', 'done', 'discarded']
_STATUS_LABELS  = {
    'pending':     t("inv_status_pending"),
    'in_progress': t("inv_status_in_progress"),
    'done':        t("inv_status_done"),
    'discarded':   t("inv_status_discarded"),
}


def _render_summary_metrics(tasks: list[dict]) -> None:
    pending  = sum(1 for t_ in tasks if t_.get('status') == 'pending')
    critical = sum(1 for t_ in tasks if t_.get('status') == 'pending' and t_.get('priority') == 1)
    done_sess= st.session_state.get('inv_session_done_count', 0)
    c1, c2, c3 = st.columns(3)
    c1.metric(t("inv_metric_pending"),      pending)
    c2.metric(t("inv_metric_critical"),     critical)
    c3.metric(t("inv_metric_done_session"), done_sess)


def _apply_filters(tasks: list[dict]) -> list[dict]:
    status_f  = st.session_state.get('inv_filter_status', 'all')
    module_f  = st.session_state.get('inv_filter_module', 'all')
    priority_f= st.session_state.get('inv_filter_priority', 0)
    search    = (st.session_state.get('inv_search', '') or '').lower().strip()

    result = tasks
    if status_f != 'all':
        result = [t_ for t_ in result if t_.get('status') == status_f]
    if module_f != 'all':
        result = [t_ for t_ in result if t_.get('source_module') == module_f]
    if priority_f:
        result = [t_ for t_ in result if t_.get('priority') == priority_f]
    if search:
        result = [
            t_ for t_ in result
            if search in (t_.get('person_name') or '').lower()
            or search in (t_.get('title') or '').lower()
        ]
    return result


def _render_task_table(filtered: list[dict]) -> None:
    import pandas as _pd

    if not filtered:
        st.info(t("inv_no_tasks"))
        return

    rows = []
    for i, task in enumerate(filtered):
        prio = task.get('priority', 3)
        rows.append({
            '_idx':                 i,
            t("inv_col_priority"):  _PRIORITY_LABELS.get(prio, str(prio)),
            t("inv_col_type"):      _TYPE_ICONS.get(task.get('task_type', ''), '') + ' ' + task.get('task_type', ''),
            t("inv_col_title"):     task.get('title', ''),
            t("inv_col_person"):    task.get('person_name', ''),
            t("inv_col_module"):    task.get('source_module', ''),
            t("inv_col_status"):    _STATUS_LABELS.get(task.get('status', 'pending'), task.get('status', '')),
        })
    df_table = _pd.DataFrame(rows)

    selected = st.dataframe(
        df_table.drop(columns=['_idx']),
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="inv_table",
    )
    sel_indices = []
    if hasattr(selected, 'selection'):
        sel_indices = selected.selection.get('rows', [])
    if sel_indices:
        st.session_state['inv_selected_task_id'] = filtered[rows[sel_indices[0]]['_idx']].get('task_id')


def _render_task_detail(tasks: list[dict], content_bytes: bytes) -> None:
    task_id = st.session_state.get('inv_selected_task_id')
    if not task_id:
        return

    task = next((t_ for t_ in tasks if t_.get('task_id') == task_id), None)
    if not task:
        return

    st.markdown("---")
    prio = task.get('priority', 3)
    st.subheader(f"{_PRIORITY_LABELS.get(prio, '')}  {task.get('title', '')}")
    st.write(task.get('detail', ''))

    pid = task.get('person_id', '')
    if pid:
        st.caption(f"GRAMPS ID: {pid} — {task.get('person_name', '')}")

    # Notas y fuente
    notes_key  = f"inv_notes_{task_id}"
    source_key = f"inv_src_{task_id}"
    new_notes  = st.text_area(t("inv_notes_label"),  value=task.get('notes', ''),       key=notes_key, height=80)
    new_source = st.text_input(t("inv_source_label"), value=task.get('found_source', ''), key=source_key)

    # Botones de estado
    current_status = task.get('status', 'pending')
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if st.button(t("inv_btn_in_progress"), key=f"inv_inp_{task_id}", disabled=(current_status == 'in_progress')):
            _update_task(tasks, task_id, status='in_progress', notes=new_notes, found_source=new_source)
            st.rerun()
    with b2:
        if st.button(t("inv_btn_done"), key=f"inv_done_{task_id}", type="primary", disabled=(current_status == 'done')):
            _update_task(tasks, task_id, status='done', notes=new_notes, found_source=new_source)
            st.session_state['inv_session_done_count'] = st.session_state.get('inv_session_done_count', 0) + 1
            st.rerun()
    with b3:
        if st.button(t("inv_btn_discard"), key=f"inv_disc_{task_id}", disabled=(current_status == 'discarded')):
            _update_task(tasks, task_id, status='discarded', notes=new_notes, found_source=new_source)
            st.rerun()
    with b4:
        if st.button(t("inv_btn_pending"), key=f"inv_pend_{task_id}", disabled=(current_status == 'pending')):
            _update_task(tasks, task_id, status='pending', notes=new_notes, found_source=new_source)
            st.rerun()

    # Botón IA opcional
    if st.button(t("inv_ask_ai_btn"), key=f"inv_ai_{task_id}"):
        rag_index = st.session_state.get('rag_index_obj')
        if rag_index and content_bytes:
            try:
                from modules.rag_assistant.app import answer_oneshot
                with st.spinner(t("inv_ai_thinking")):
                    answer, error = answer_oneshot(task.get('detail', ''), content_bytes)
                if error:
                    st.error(error)
                elif answer:
                    st.info(answer)
            except Exception as e:
                st.warning(f"IA no disponible: {e}")
        else:
            st.info("El asistente IA no está disponible. Actívalo en la sección 'Asistente IA'.")


def _update_task(tasks: list[dict], task_id: str, **kwargs) -> None:
    from datetime import datetime, timezone
    for task in tasks:
        if task.get('task_id') == task_id:
            task.update(kwargs)
            task['updated_at'] = datetime.now(timezone.utc).isoformat()
            break
    st.session_state['inv_tasks'] = tasks


# ─────────────────────────────────────────────────────────────────────────────
# Render público
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    st.sidebar.markdown(t("sidebar_gramps_header"))
    shared_bytes = st.session_state.get("shared_gramps_bytes")
    shared_name  = st.session_state.get("shared_gramps_name", "")

    if shared_bytes:
        st.sidebar.success(f"📂 {shared_name}")
    else:
        uploaded = st.sidebar.file_uploader(
            t("sidebar_gramps_uploader"),
            type=["gramps"],
            key="inv_uploader",
        )
        if uploaded:
            content = uploaded.read()
            st.session_state["shared_gramps_bytes"] = content
            st.session_state["shared_gramps_name"]  = uploaded.name

    st.sidebar.markdown("---")
    st.sidebar.selectbox(
        t("inv_filter_status"),
        ['all', 'pending', 'in_progress', 'done', 'discarded'],
        format_func=lambda x: t("inv_filter_all") if x == 'all' else _STATUS_LABELS.get(x, x),
        key="inv_filter_status",
    )
    st.sidebar.selectbox(
        t("inv_filter_module"),
        ['all', 'general', 'family_completion', 'testigos', 'identity_resolution'],
        format_func=lambda x: t("inv_filter_all") if x == 'all' else x,
        key="inv_filter_module",
    )
    st.sidebar.selectbox(
        t("inv_filter_priority"),
        [0, 1, 2, 3],
        format_func=lambda x: t("inv_filter_all") if x == 0 else _PRIORITY_LABELS.get(x, str(x)),
        key="inv_filter_priority",
    )
    st.sidebar.text_input(t("inv_search_placeholder"), key="inv_search")


def render_page() -> None:
    st.title(t("inv_title"))
    st.caption(t("inv_description"))

    content_bytes = st.session_state.get("shared_gramps_bytes")
    if not content_bytes:
        st.info(t("inv_no_file"))
        return

    col_regen, col_save = st.columns([2, 1])
    with col_regen:
        if st.button(t("inv_regenerate_btn"), type="primary"):
            with st.spinner(t("inv_regenerating")):
                _regenerate_tasks(content_bytes)
            st.rerun()
    with col_save:
        if st.button(t("inv_save_btn")):
            tasks_raw = st.session_state.get('inv_tasks', [])
            if tasks_raw:
                task_objs = [ResearchTask(**{
                    k: v for k, v in td.items()
                    if k in ResearchTask.__dataclass_fields__
                }) for td in tasks_raw]
                ok = save_tasks(task_objs, TASKS_FILE)
                st.success(t("inv_saved_ok")) if ok else st.error(t("inv_saved_error"))

    # Inicializar tareas si no existen en session_state
    if 'inv_tasks' not in st.session_state:
        stored = load_tasks(TASKS_FILE)
        if stored:
            st.session_state['inv_tasks'] = stored
        else:
            with st.spinner(t("inv_regenerating")):
                _regenerate_tasks(content_bytes)

    tasks = st.session_state.get('inv_tasks', [])

    _render_summary_metrics(tasks)
    st.markdown("---")

    filtered = _apply_filters(tasks)
    _render_task_table(filtered)
    _render_task_detail(tasks, content_bytes)
