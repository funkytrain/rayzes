"""
modules/export/app.py — Write-back to GRAMPS
UI Streamlit para generar un archivo .gramps enriquecido con notas y tags,
o sincronizar los cambios directamente con Gramps Web API.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from translations import t
from modules.shared.gramps_parser import parse_gramps, GrampsDB
from modules.export.gramps_writer import GrampsWriter

DATA_DIR = Path("data")
DISMISSED_FILE = DATA_DIR / "dismissed_inconsistencies.json"
RESULTS_FILE = DATA_DIR / "family_completion_results.json"
CONFIRMED_LINKS_FILE = DATA_DIR / "confirmed_links.json"
IDENTITY_RESULTS_FILE = DATA_DIR / "identity_resolution_results.json"
ARCHIVE_FINDINGS_FILE = DATA_DIR / "archive_findings.json"
RESEARCH_TASKS_FILE = DATA_DIR / "research_tasks.json"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de carga de datos
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _cached_parse(content_bytes: bytes) -> GrampsDB:
    return parse_gramps(content_bytes)


def _load_confirmed_links() -> dict:
    """Carga confirmed_links.json y devuelve gramps_links.confirmed."""
    try:
        if CONFIRMED_LINKS_FILE.exists():
            data = json.loads(CONFIRMED_LINKS_FILE.read_text(encoding='utf-8'))
            return data.get('gramps_links', {}).get('confirmed', {})
    except Exception:
        pass
    return {}


def _load_dismissed() -> set:
    try:
        if DISMISSED_FILE.exists():
            return set(json.loads(DISMISSED_FILE.read_text(encoding='utf-8')))
    except Exception:
        pass
    return set()


def _load_family_completion_results() -> list[dict]:
    try:
        if RESULTS_FILE.exists():
            return json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return []


def _load_all_inconsistencies(content_bytes: bytes | None, db: GrampsDB | None = None) -> list[dict]:
    """Recalcula todas las inconsistencias del árbol cargado.
    Acepta content_bytes (modo archivo) o db directamente (modo API).
    """
    try:
        from modules.general.app import (
            detect_inconsistencies,
            _dismissed_key,
            build_graph,
        )
        if content_bytes is not None:
            resolved_db = _cached_parse(content_bytes)
            from modules.general.app import _cached_stats
            stats, _ = _cached_stats(content_bytes)
        elif db is not None:
            resolved_db = db
            # En modo API no hay content_bytes; recalcular stats desde db directamente
            try:
                from modules.general.app import compute_file_statistics, compute_windowed_stats
                _pe = db.to_persons_ext()
                _fe = db.to_families_ext()
                stats = compute_file_statistics(_pe, _fe)
            except Exception:
                stats = {}
        else:
            return []

        people_ext = resolved_db.to_persons_ext()
        families_ext = resolved_db.to_families_ext()
        G = build_graph(people_ext, families_ext)
        all_issues = detect_inconsistencies(people_ext, families_ext, stats, G)

        dismissed = _load_dismissed()
        return [i for i in all_issues if _dismissed_key(i) not in dismissed]
    except Exception as e:
        st.warning(f"No se pudieron calcular inconsistencias: {e}")
        return []


def _load_identity_results() -> list[dict]:
    try:
        if IDENTITY_RESULTS_FILE.exists():
            return json.loads(IDENTITY_RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _load_archive_findings() -> dict:
    try:
        if ARCHIVE_FINDINGS_FILE.exists():
            data = json.loads(ARCHIVE_FINDINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _load_research_tasks() -> list[dict]:
    try:
        if RESEARCH_TASKS_FILE.exists():
            return json.loads(RESEARCH_TASKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


# ─────────────────────────────────────────────────────────────────────────────
# render_sidebar
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar_upload() -> None:
    st.sidebar.markdown(t("sidebar_gramps_header"))
    if st.session_state.get("gramps_web_connected"):
        st.sidebar.info(t("gramps_web_source_active"))
    else:
        uploaded = st.sidebar.file_uploader(
            t("sidebar_gramps_uploader"),
            type=["gramps"],
            key="exp_uploader",
        )
        if uploaded is not None:
            st.session_state["shared_gramps_bytes"] = uploaded.read()
            st.session_state["shared_gramps_name"] = uploaded.name

        if st.session_state.get("shared_gramps_name"):
            st.sidebar.success(t("sidebar_gramps_loaded", name=st.session_state["shared_gramps_name"]))


def render_sidebar() -> None:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# render_page
# ─────────────────────────────────────────────────────────────────────────────

def _render_changelog(changelog: list[dict]) -> None:
    """Renderiza el log de cambios en un expander."""
    if changelog:
        with st.expander(t("exp_log_header"), expanded=False):
            for entry in changelog:
                etype = entry.get("type", "")
                if etype == "confirmation_note":
                    st.markdown(
                        f"- **{t('exp_log_conf_note')}** "
                        f"`{entry.get('pid')}` — {entry.get('witness')} → {entry.get('name')}"
                    )
                elif etype == "inconsistency_tag":
                    st.markdown(
                        f"- **{t('exp_log_tag')}** "
                        f"`{entry.get('handle')}` ← `{entry.get('tag')}`"
                    )
                elif etype == "completion_candidate":
                    prob_pct = f"{entry.get('prob', 0):.0%}"
                    st.markdown(
                        f"- **{t('exp_log_candidate')}** "
                        f"Familia `{entry.get('marriage_fid')}`: "
                        f"{entry.get('orphan')} ← {entry.get('candidate')} ({prob_pct})"
                    )
                elif etype == "identity_note":
                    prob_pct = f"{entry.get('prob', 0):.0%}"
                    st.markdown(
                        f"- **{t('exp_log_identity_note')}** "
                        f"`{entry.get('pid')}` — {entry.get('witness')} ({prob_pct})"
                    )
                elif etype == "task_note":
                    st.markdown(
                        f"- **{t('exp_log_task_note')}** "
                        f"`{entry.get('person_id')}` — {entry.get('title')}"
                    )
                elif etype == "archive_citation":
                    st.markdown(
                        f"- **{t('exp_log_archive_citation')}** "
                        f"`{entry.get('person_handle')}` ← {entry.get('archive')}: {entry.get('title')}"
                    )
    else:
        st.info(t("exp_log_empty"))


def render_page(ctx=None) -> None:
    st.title(t("exp_title"))

    api_connected = bool((ctx.gramps.is_api if ctx is not None else None)
                         or st.session_state.get("gramps_web_connected"))
    db = (ctx.gramps.db if ctx is not None else None) \
         or st.session_state.get("_gramps_web_db_override")
    content_bytes: bytes | None = None

    if db is None:
        content_bytes = (ctx.gramps.bytes_ if ctx is not None else None) \
                        or st.session_state.get("shared_gramps_bytes")
        if not content_bytes:
            st.info(t("exp_no_file"))
            return
        db = _cached_parse(content_bytes)

    # ── Destino de exportación ────────────────────────────────────────────────
    dest_options = [t("exp_export_download")]
    if api_connected:
        dest_options.append(t("exp_export_api"))

    export_dest = st.radio(
        t("exp_export_mode_label"),
        dest_options,
        key="exp_export_dest",
        horizontal=True,
    )
    use_api = api_connected and export_dest == t("exp_export_api")

    if not api_connected and export_dest == t("exp_export_api"):
        st.warning(t("exp_api_not_connected"))
        return

    st.markdown("---")

    # ── Opciones — Grupo 1 ────────────────────────────────────────────────────
    st.markdown(f"### {t('exp_options_header')}")

    col1, col2, col3 = st.columns(3)
    with col1:
        include_confirmations = st.checkbox(
            t("exp_include_confirmations"), value=True, key="exp_chk_conf"
        )
    with col2:
        include_tags = st.checkbox(
            t("exp_include_tags"), value=True, key="exp_chk_tags"
        )
    with col3:
        include_candidates = st.checkbox(
            t("exp_include_candidates"), value=True, key="exp_chk_cand"
        )

    min_prob = 0.65
    if include_candidates:
        min_prob = st.slider(
            t("exp_min_prob_label"),
            min_value=0.0, max_value=1.0, value=0.65, step=0.05,
            key="exp_min_prob",
        )

    # ── Opciones — Grupo 2 (solo en modo API) ─────────────────────────────────
    include_archive = False
    include_identity = False
    include_tasks = False
    identity_threshold = 0.75

    if use_api:
        st.markdown("---")
        col4, col5, col6 = st.columns(3)
        with col4:
            include_archive = st.checkbox(
                t("exp_include_archive"), value=False, key="exp_chk_archive"
            )
        with col5:
            include_identity = st.checkbox(
                t("exp_include_identity"), value=False, key="exp_chk_identity"
            )
        with col6:
            include_tasks = st.checkbox(
                t("exp_include_tasks"), value=False, key="exp_chk_tasks"
            )
        if include_identity:
            identity_threshold = st.slider(
                t("exp_identity_threshold_label"),
                min_value=0.5, max_value=1.0, value=0.75, step=0.05,
                key="exp_identity_threshold",
            )

    st.markdown("---")

    # ── Cargar datos ──────────────────────────────────────────────────────────
    confirmed_links = _load_confirmed_links() if include_confirmations else {}
    active_issues = _load_all_inconsistencies(content_bytes, db) if include_tags else []
    batch_results = _load_family_completion_results() if include_candidates else []
    identity_results = _load_identity_results() if include_identity else []
    archive_findings = _load_archive_findings() if include_archive else {}
    research_tasks = _load_research_tasks() if include_tasks else []

    # Preferir batch de sesión si está disponible
    if include_candidates and "fce_batch" in st.session_state:
        session_batch = st.session_state["fce_batch"]
        if session_batch:
            batch_results = session_batch

    # ── Preview ───────────────────────────────────────────────────────────────
    st.markdown(f"### {t('exp_preview_header')}")

    n_conf = len(confirmed_links) if include_confirmations else 0
    n_issues = len(active_issues) if include_tags else 0
    n_cand = sum(
        1 for r in batch_results if (r.get("top_prob") or 0.0) >= min_prob
    ) if include_candidates else 0

    preview_cols = st.columns(3)
    with preview_cols[0]:
        st.metric(t("exp_metric_notes_conf"), n_conf)
    with preview_cols[1]:
        st.metric(t("exp_metric_tags"), n_issues)
    with preview_cols[2]:
        st.metric(t("exp_metric_candidates"), n_cand)

    if use_api:
        n_archive = sum(
            len([d for d in v.get("documents", []) if (d.get("relevance_score") or 0) > 0])
            for v in archive_findings.values()
            if isinstance(v, dict)
        ) if include_archive else 0
        n_identity = sum(
            1 for r in identity_results if (r.get("probability") or 0) >= identity_threshold
        ) if include_identity else 0
        n_tasks = sum(
            1 for t_ in research_tasks
            if t_.get("status") == "done" and (t_.get("found_source") or "").strip()
        ) if include_tasks else 0

        api_cols = st.columns(3)
        with api_cols[0]:
            st.metric(t("exp_metric_archive"), n_archive)
        with api_cols[1]:
            st.metric(t("exp_metric_identity"), n_identity)
        with api_cols[2]:
            st.metric(t("exp_metric_tasks"), n_tasks)

    total_items = n_conf + n_issues + n_cand
    if use_api:
        total_items += n_archive + n_identity + n_tasks  # type: ignore[possibly-undefined]
    if total_items == 0:
        st.info(t("exp_nothing_to_add"))

    # ── Advertencia de desajuste de árbol ────────────────────────────────────
    if batch_results:
        n_persons_db = len(db.persons)
        ratio = abs(n_persons_db - len(batch_results)) / max(n_persons_db, 1)
        if ratio > 0.05:
            st.warning(t("exp_mismatch_warning"))

    st.markdown("---")

    # ═════════════════════════════════════════════════════════════════════════
    # Modo A: Descargar .gramps
    # ═════════════════════════════════════════════════════════════════════════
    if not use_api:
        if st.button(t("exp_generate_gramps"), type="primary", key="exp_btn_generate"):
            # En modo API sin bytes locales, descargar el .gramps del servidor
            if content_bytes is None and api_connected:
                _api_url   = st.session_state.get("gramps_web_url_saved", st.session_state.get("gramps_web_url", "")).rstrip("/")
                _api_token = st.session_state.get("gramps_web_token", "")
                with st.spinner("Descargando archivo del servidor (puede tardar unos segundos)..."):
                    try:
                        from modules.shared.gramps_api_client import download_gramps_export
                        content_bytes = download_gramps_export(_api_url, _api_token)
                        st.session_state["shared_gramps_bytes"] = content_bytes
                        st.session_state["shared_gramps_name"] = "gramps_web.gramps"
                    except Exception as _e:
                        st.error(f"No se pudo descargar el archivo del servidor: {_e}")
                        return
            if content_bytes is None:
                st.error("No hay archivo .gramps cargado para generar la descarga.")
                return
            with st.spinner(t("exp_generating")):
                writer = GrampsWriter(content_bytes, db)

                n_added_conf = 0
                n_added_tags = 0
                n_added_cand = 0

                if include_confirmations and confirmed_links:
                    n_added_conf = writer.add_confirmation_notes(confirmed_links)
                    writer.add_witness_attribute_notes(confirmed_links)

                if include_tags and active_issues:
                    n_added_tags = writer.add_inconsistency_tags(active_issues)

                if include_candidates and batch_results:
                    n_added_cand = writer.add_completion_candidates(
                        batch_results, min_prob=min_prob
                    )

                result_bytes = writer.to_bytes()

            filename = st.session_state.get("shared_gramps_name", "export.gramps")
            base = filename.replace(".gramps", "")
            out_name = f"{base}_genhelper.gramps"

            st.success(
                t("exp_done_summary", conf=n_added_conf, tags=n_added_tags, cand=n_added_cand)
            )
            st.download_button(
                label=t("exp_download_label"),
                data=result_bytes,
                file_name=out_name,
                mime="application/gzip",
                key="exp_download",
            )
            _render_changelog(writer.changelog)

    # ═════════════════════════════════════════════════════════════════════════
    # Modo B: Sincronizar con Gramps Web API
    # ═════════════════════════════════════════════════════════════════════════
    else:
        with st.expander(t("exp_sync_strategy_label"), expanded=False):
            strategy = st.radio(
                t("exp_sync_strategy_label"),
                [t("exp_sync_strategy_transaction"), t("exp_sync_strategy_sequential")],
                key="exp_sync_strategy",
                label_visibility="collapsed",
            )
        sync_strategy = "sequential" if strategy == t("exp_sync_strategy_sequential") else "transaction"

        if st.button(t("exp_sync_api"), type="primary", key="exp_btn_sync"):
            from modules.export.gramps_api_writer import GrampsApiWriter

            base_url = st.session_state.get("gramps_web_url_saved", st.session_state.get("gramps_web_url", ""))
            token = st.session_state.get("gramps_web_token", "")

            with st.spinner(t("exp_syncing")):
                try:
                    api_writer = GrampsApiWriter(base_url, token, db)

                    if include_confirmations and confirmed_links:
                        api_writer.add_confirmation_notes(confirmed_links)
                        api_writer.add_witness_attribute_notes(confirmed_links)

                    if include_tags and active_issues:
                        api_writer.add_inconsistency_tags(active_issues)

                    if include_candidates and batch_results:
                        api_writer.add_completion_candidates(batch_results, min_prob=min_prob)

                    if include_identity and identity_results:
                        api_writer.add_identity_resolution_notes(
                            identity_results, threshold=identity_threshold
                        )

                    if include_tasks and research_tasks:
                        api_writer.add_research_task_notes(research_tasks)

                    if include_archive and archive_findings:
                        api_writer.add_archive_citations(archive_findings, confirmed_links)

                    result = api_writer.sync(strategy=sync_strategy)

                except Exception as e:
                    st.error(t("exp_sync_error", e=str(e)))
                    return

            if result.success:
                st.success(t("exp_sync_done", n=result.n_ops))
            elif result.n_ops > 0 and result.detail:
                st.warning(t("exp_sync_partial_warning", ok=result.n_ops, fail=len(result.detail)))
            else:
                st.error(t("exp_sync_error", e=result.error))

            _render_changelog(api_writer.changelog)


