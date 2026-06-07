"""
modules/export/app.py — Write-back to GRAMPS
UI Streamlit para generar un archivo .gramps enriquecido con notas y tags.
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


def _load_all_inconsistencies(content_bytes: bytes) -> list[dict]:
    """Recalcula todas las inconsistencias del árbol cargado."""
    try:
        from modules.general.app import (
            detect_inconsistencies,
            _dismissed_key,
        )
        db = _cached_parse(content_bytes)
        people_ext = db.to_persons_ext()
        families_ext = db.to_families_ext()

        from modules.general.app import _cached_stats, build_graph
        stats, _ = _cached_stats(content_bytes)
        G = build_graph(people_ext, families_ext)
        all_issues = detect_inconsistencies(people_ext, families_ext, stats, G)

        dismissed = _load_dismissed()
        active = [
            i for i in all_issues
            if _dismissed_key(i) not in dismissed
        ]
        return active
    except Exception as e:
        st.warning(f"No se pudieron calcular inconsistencias: {e}")
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

def render_page() -> None:
    st.title(t("exp_title"))

    db = st.session_state.get("_gramps_web_db_override")
    if db is None:
        content_bytes: bytes | None = st.session_state.get("shared_gramps_bytes")
        if not content_bytes:
            st.info(t("exp_no_file"))
            return
        db = _cached_parse(content_bytes)

    # ── Opciones ──────────────────────────────────────────────────────────────
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

    st.markdown("---")

    # ── Cargar datos ──────────────────────────────────────────────────────────
    confirmed_links = _load_confirmed_links() if include_confirmations else {}
    active_issues = _load_all_inconsistencies(content_bytes) if include_tags else []
    batch_results = _load_family_completion_results() if include_candidates else []

    # Filtrar batch por sesión (si hay análisis en curso en sesión, usarlo)
    if include_candidates and "fce_batch" in st.session_state:
        session_batch = st.session_state["fce_batch"]
        if session_batch:
            batch_results = session_batch

    # ── Preview ───────────────────────────────────────────────────────────────
    st.markdown(f"### {t('exp_preview_header')}")

    n_conf = len(confirmed_links) if include_confirmations else 0
    n_issues = len(active_issues) if include_tags else 0
    n_cand = sum(
        1 for r in batch_results
        if (r.get('top_prob') or 0.0) >= min_prob
    ) if include_candidates else 0

    preview_col1, preview_col2, preview_col3 = st.columns(3)
    with preview_col1:
        st.metric(t("exp_metric_notes_conf"), n_conf)
    with preview_col2:
        st.metric(t("exp_metric_tags"), n_issues)
    with preview_col3:
        st.metric(t("exp_metric_candidates"), n_cand)

    if n_conf == 0 and n_issues == 0 and n_cand == 0:
        st.info(t("exp_nothing_to_add"))

    # ── Advertencia de desajuste de árbol ────────────────────────────────────
    if batch_results:
        n_persons_db = len(db.persons)
        n_persons_batch = len({r.get('orphan_pid') for r in batch_results})
        if n_persons_batch > 0:
            ratio = abs(n_persons_db - len(batch_results)) / max(n_persons_db, 1)
            if ratio > 0.05:
                st.warning(t("exp_mismatch_warning"))

    # ── Botón generar ─────────────────────────────────────────────────────────
    st.markdown("---")
    if st.button(t("exp_generate_gramps"), type="primary", key="exp_btn_generate"):
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

        # Download button
        filename = st.session_state.get("shared_gramps_name", "export.gramps")
        base = filename.replace(".gramps", "")
        out_name = f"{base}_genhelper.gramps"

        st.success(
            t("exp_done_summary",
              conf=n_added_conf, tags=n_added_tags, cand=n_added_cand)
        )

        st.download_button(
            label=t("exp_download_label"),
            data=result_bytes,
            file_name=out_name,
            mime="application/gzip",
            key="exp_download",
        )

        # ── Log expandible ────────────────────────────────────────────────────
        if writer.changelog:
            with st.expander(t("exp_log_header"), expanded=False):
                for entry in writer.changelog:
                    etype = entry.get('type', '')
                    if etype == 'confirmation_note':
                        st.markdown(
                            f"- **{t('exp_log_conf_note')}** "
                            f"`{entry.get('pid')}` — {entry.get('witness')} → {entry.get('name')}"
                        )
                    elif etype == 'inconsistency_tag':
                        st.markdown(
                            f"- **{t('exp_log_tag')}** "
                            f"`{entry.get('handle')}` ← `{entry.get('tag')}`"
                        )
                    elif etype == 'completion_candidate':
                        prob_pct = f"{entry.get('prob', 0):.0%}"
                        st.markdown(
                            f"- **{t('exp_log_candidate')}** "
                            f"Familia `{entry.get('marriage_fid')}`: "
                            f"{entry.get('orphan')} ← {entry.get('candidate')} ({prob_pct})"
                        )
        else:
            st.info(t("exp_log_empty"))
