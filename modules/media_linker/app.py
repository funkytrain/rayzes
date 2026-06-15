"""
Módulo Rayzes: Vincular Imágenes
Asocia imágenes de archivos eclesiásticos a eventos/personas en GRAMPS.
Soporta modo archivo local (descarga .gramps modificado) y modo API (Gramps Web).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from translations import t
from modules.shared.gramps_parser import parse_gramps

from .linker_engine import scan_and_match, resolver_confirmados
from .media_store import MediaLinkerStore
from .xml_media_writer import apply_media_links_xml

_store = MediaLinkerStore()


# ── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar_upload():
    if not st.session_state.get("gramps_web_connected"):
        uploaded = st.sidebar.file_uploader(
            t("exp_upload_gramps") if "exp_upload_gramps" in _t_keys() else "Subir .gramps",
            type=["gramps"],
            key="ml_gramps_upload",
        )
        if uploaded:
            st.session_state["shared_gramps_bytes"] = uploaded.read()
            st.session_state["shared_gramps_name"]  = uploaded.name


def render_sidebar():
    pass


def _t_keys():
    try:
        from translations import TRANSLATIONS
        lang = st.session_state.get("_lang", "es")
        return TRANSLATIONS.get(lang, {}).keys()
    except Exception:
        return set()


# ── Helpers ──────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _cached_parse(content_bytes: bytes):
    return parse_gramps(content_bytes)


def _get_db_and_mode():
    """Devuelve (db, api_connected, content_bytes)."""
    api_connected  = bool(st.session_state.get("gramps_web_connected"))
    db             = st.session_state.get("_gramps_web_db_override")
    content_bytes  = st.session_state.get("shared_gramps_bytes")

    if db is None and content_bytes:
        db = _cached_parse(content_bytes)

    return db, api_connected, content_bytes


def _obj_type_for_handle(handle: str, db) -> str:
    """Determina el tipo de entidad GRAMPS a partir del handle."""
    if handle in db.events:
        return "events"
    if handle in db.families:
        return "families"
    return "people"


# ── Página principal ─────────────────────────────────────────────────────────

def render_page():
    st.title(t("section_media_linker"))

    db, api_connected, content_bytes = _get_db_and_mode()

    if db is None:
        st.info(t("ml_no_db"))
        return

    # ── Sección 1: selección de carpeta ─────────────────────────────────────
    carpeta = st.text_input(
        t("ml_folder_label"),
        key="ml_folder_path",
        placeholder=t("ml_folder_placeholder"),
    )

    col_scan, _ = st.columns([1, 4])
    with col_scan:
        scan_clicked = st.button(t("ml_scan_btn"), type="primary")

    if scan_clicked:
        if not carpeta:
            st.warning(t("ml_folder_not_found").format(path="(vacío)"))
        else:
            import os
            if not os.path.isdir(carpeta):
                st.error(t("ml_folder_not_found").format(path=carpeta))
            else:
                _store.load()
                ya_procesados = _store.get_procesados()
                with st.spinner(t("ml_scan_btn") + "..."):
                    resultado = scan_and_match(carpeta, db, ya_procesados)
                st.session_state["ml_scan_result"] = resultado

    resultado = st.session_state.get("ml_scan_result")

    if resultado is None:
        st.info(t("ml_no_scan_yet"))
        return

    # ── Sección 2: métricas ──────────────────────────────────────────────────
    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(t("ml_images_found"),   resultado["total"])
    c2.metric(t("ml_images_new"),     resultado["nuevas"])
    c3.metric(t("ml_images_already"), resultado["ya_procesadas"])
    c4.metric(t("ml_auto_title"),     len(resultado["auto"]))
    c5.metric(t("ml_images_no_parse"),len(resultado["sin_parsear"]))

    # ── Sección 3: matches automáticos ───────────────────────────────────────
    st.subheader(t("ml_auto_title"))
    auto = resultado["auto"]
    if not auto:
        st.info(t("ml_auto_empty"))
    else:
        df_auto = pd.DataFrame([{
            t("ml_col_archivo"): r["img_name"],
            t("ml_col_tipo"):    r["tipo"],
            t("ml_col_nombre"):  r["nombre"],
            t("ml_col_anyo"):    str(r["anyo"]),
            t("ml_col_destino"): r["destino"],
        } for r in auto])
        st.dataframe(df_auto, use_container_width=True, hide_index=True)

    # ── Sección 4: pendientes con sugerencias ────────────────────────────────
    st.subheader(t("ml_pending_title"))
    pendientes_orig = resultado["pendientes"]
    if not pendientes_orig:
        st.info(t("ml_pending_empty"))
    else:
        st.caption(t("ml_pending_help"))

        cols_editor = {
            t("ml_col_archivo"): "img_name",
            t("ml_col_tipo"):    "tipo",
            t("ml_col_nombre"):  "nombre",
            t("ml_col_anyo"):    "anyo",
            t("ml_col_sug").format(n=1, score=""): "sug_1_label",
            t("ml_col_sug").format(n=2, score=""): "sug_2_label",
            t("ml_col_sug").format(n=3, score=""): "sug_3_label",
            t("ml_col_confirmado"): "gramps_id_confirmado",
        }

        rows = []
        for p in pendientes_orig:
            def _sug_label(n):
                sid   = p.get(f"sug_{n}_id", "")
                snomb = p.get(f"sug_{n}_nombre", "")
                sscor = p.get(f"sug_{n}_score", "")
                if sid:
                    return f"{sid} — {snomb} ({sscor})"
                return ""

            rows.append({
                t("ml_col_archivo"):  p["img_name"],
                t("ml_col_tipo"):     p["tipo"],
                t("ml_col_nombre"):   p["nombre"],
                t("ml_col_anyo"):     str(p["anyo"]),
                t("ml_col_sug").format(n=1, score=""): _sug_label(1),
                t("ml_col_sug").format(n=2, score=""): _sug_label(2),
                t("ml_col_sug").format(n=3, score=""): _sug_label(3),
                t("ml_col_confirmado"): p.get("gramps_id_confirmado", ""),
            })

        col_confirmado = t("ml_col_confirmado")
        col_archivo    = t("ml_col_archivo")
        df_pend = pd.DataFrame(rows)

        edited = st.data_editor(
            df_pend,
            column_config={
                col_archivo: st.column_config.TextColumn(disabled=True),
                t("ml_col_tipo"):    st.column_config.TextColumn(disabled=True),
                t("ml_col_nombre"):  st.column_config.TextColumn(disabled=True),
                t("ml_col_anyo"):    st.column_config.TextColumn(disabled=True),
                t("ml_col_sug").format(n=1, score=""): st.column_config.TextColumn(disabled=True),
                t("ml_col_sug").format(n=2, score=""): st.column_config.TextColumn(disabled=True),
                t("ml_col_sug").format(n=3, score=""): st.column_config.TextColumn(disabled=True),
                col_confirmado: st.column_config.TextColumn(
                    help="ID de GRAMPS (ej. I0123). Deja vacío para descartar."
                ),
            },
            use_container_width=True,
            hide_index=True,
            key="ml_pend_editor",
        )

        # Sincronizar ediciones con pendientes_orig
        for i, row in edited.iterrows():
            pendientes_orig[i]["gramps_id_confirmado"] = str(
                row.get(col_confirmado, "")
            ).strip()

    # ── Sección 5: aplicar ───────────────────────────────────────────────────
    st.divider()
    hay_auto      = bool(auto)
    hay_confirmados = any(
        str(p.get("gramps_id_confirmado", "")).strip()
        for p in pendientes_orig
    ) if pendientes_orig else False

    if not hay_auto and not hay_confirmados:
        st.info(t("ml_no_apply_empty"))
        return

    if st.button(t("ml_apply_btn"), type="primary", key="ml_apply_btn"):
        # Resolver confirmados manuales
        links_confirmados, errores_id = resolver_confirmados(
            pendientes_orig,
            db.persons_by_gramps_id,
            db,
        )

        if errores_id:
            for gid in errores_id:
                st.warning(t("ml_invalid_id").format(gid=gid, archivo=""))

        todos_links = auto + links_confirmados

        if not todos_links:
            st.info(t("ml_no_apply_empty"))
            return

        if api_connected:
            _apply_api(todos_links, db)
        else:
            _apply_xml(todos_links, content_bytes)

        # Registrar procesados
        _store.load()
        _store.marcar_procesados(todos_links)
        # Limpiar resultado de sesión para forzar nuevo escaneo
        st.session_state.pop("ml_scan_result", None)
        st.rerun()


def _apply_api(links: list[dict], db):
    from modules.shared.gramps_api_client import GrampsWebWriter

    base_url = st.session_state.get("gramps_web_url_saved", st.session_state.get("gramps_web_url", ""))
    token    = st.session_state.get("gramps_web_token", "")
    writer   = GrampsWebWriter(base_url, token)

    progress = st.progress(0, text=t("ml_applying").format(current=0, total=len(links)))
    errores  = []

    for i, link in enumerate(links, 1):
        progress.progress(i / len(links),
                          text=t("ml_applying").format(current=i, total=len(links)))
        try:
            media_handle = writer.upload_media_object(link["img_path"], link["descripcion"])
            obj_type     = _obj_type_for_handle(link["elem_handle"], db)
            writer.add_objref_to_entity(obj_type, link["elem_handle"], media_handle)
        except Exception as exc:
            errores.append(t("ml_api_error").format(
                archivo=link["img_name"], error=str(exc)
            ))

    progress.progress(1.0)
    if errores:
        for e in errores:
            st.warning(e)
    n_ok = len(links) - len(errores)
    if n_ok > 0:
        st.success(t("ml_success").format(n=n_ok))


def _apply_xml(links: list[dict], content_bytes: bytes | None):
    if not content_bytes:
        st.error("No hay archivo .gramps cargado.")
        return

    progress = st.progress(0, text=t("ml_applying").format(current=0, total=len(links)))

    # Procesar en un solo paso (XmlEditor acumula todo antes de aplicar)
    progress.progress(0.5, text=t("ml_applying").format(current=1, total=1))
    modified_bytes = apply_media_links_xml(content_bytes, links)
    progress.progress(1.0)

    nombre_original = st.session_state.get("shared_gramps_name", "archivo.gramps")
    nombre_descarga = nombre_original.replace(".gramps", "_media.gramps")

    st.success(t("ml_success").format(n=len(links)))
    st.download_button(
        label=t("ml_download_btn"),
        data=modified_bytes,
        file_name=nombre_descarga,
        mime="application/gzip",
        help=t("ml_download_help"),
    )
