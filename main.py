import streamlit as st
import requests as _requests
from translations import t, set_lang, get_lang, SUPPORTED_LANGS

st.set_page_config(page_title="Rayzes — Genealogía", layout="wide")

if "_lang" not in st.session_state:
    st.session_state["_lang"] = "es"

_lang_display = st.sidebar.selectbox(
    t("lang_selector_label"),
    SUPPORTED_LANGS,
    index=SUPPORTED_LANGS.index("Español" if st.session_state["_lang"] == "es" else "English"),
    key="_lang_selector",
)
set_lang("es" if _lang_display == "Español" else "en")
st.sidebar.markdown("---")

SECTION_KEYS = ["general", "testigos", "consanguinidad", "adn", "migration", "family_completion", "investigacion", "export", "rag_assistant", "media_linker"]
SECTION_LABELS = [t("section_general"), t("section_testigos"), t("section_consanguinidad"), t("section_adn"), t("section_migration"), t("section_family_completion"), t("section_investigacion"), t("section_export"), t("section_rag_assistant"), t("section_media_linker")]

if "active_section_key" not in st.session_state:
    st.session_state["active_section_key"] = SECTION_KEYS[0]

_section_idx = SECTION_KEYS.index(st.session_state.get("active_section_key", SECTION_KEYS[0]))

active_section = st.session_state.get("active_section_key", SECTION_KEYS[0])

if active_section == "testigos":
    from modules.testigos import render_sidebar_upload, render_sidebar, render_page
elif active_section == "consanguinidad":
    from modules.consanguinidad import render_sidebar_upload, render_sidebar, render_page
elif active_section == "general":
    from modules.general import render_sidebar_upload, render_sidebar, render_page
elif active_section == "adn":
    from modules.adn import render_sidebar_upload, render_sidebar, render_page
elif active_section == "migration":
    from modules.migration import render_sidebar_upload, render_sidebar, render_page
elif active_section == "family_completion":
    from modules.family_completion import render_sidebar_upload, render_sidebar, render_page
elif active_section == "investigacion":
    from modules.investigacion import render_sidebar_upload, render_sidebar, render_page
elif active_section == "export":
    from modules.export import render_sidebar_upload, render_sidebar, render_page
elif active_section == "rag_assistant":
    from modules.rag_assistant import render_sidebar_upload, render_sidebar, render_page
elif active_section == "media_linker":
    from modules.media_linker import render_sidebar_upload, render_sidebar, render_page

# 1. File upload del módulo activo
render_sidebar_upload()

# ── 2. Gramps Web API — widget de conexión ───────────────────────────────────
from modules.shared.gramps_api_client import get_token as _get_token, fetch_gramps_db as _fetch_gramps_db

st.sidebar.markdown("---")
_is_connected = bool(st.session_state.get("gramps_web_connected"))
with st.sidebar.expander(t("gramps_web_expander_label"), expanded=False):
    if _is_connected:
        st.success(t("gramps_web_status_connected",
                     url=st.session_state.get("gramps_web_url_saved", st.session_state.get("gramps_web_url", ""))))
        _disconnect_clicked = st.button(t("gramps_web_disconnect_btn"),
                                        key="_gweb_disconnect", type="primary")
        _connect_clicked = False
        _pwd = ""
    else:
        st.text_input(t("gramps_web_url_label"), key="gramps_web_url",
                      placeholder="http://192.168.1.x:5000")
        st.text_input(t("gramps_web_user_label"), key="_gramps_web_username_input")
        _pwd = st.text_input(t("gramps_web_pwd_label"), type="password",
                             key="_gramps_web_pwd_input")
        _connect_clicked = st.button(t("gramps_web_connect_btn"),
                                     key="_gweb_connect", type="primary")
        _disconnect_clicked = False

    if _connect_clicked:
        _url = (st.session_state.get("gramps_web_url") or "").strip()
        _usr = (st.session_state.get("_gramps_web_username_input") or "").strip()
        _pw  = _pwd
        if _url and _usr and _pw:
            try:
                with st.spinner(t("gramps_web_connecting")):
                    _token = _get_token(_url, _usr, _pw)
                st.session_state["gramps_web_token"] = _token
                st.session_state["gramps_web_connected"] = True
                st.session_state["gramps_web_db_cache"] = None
                st.session_state["gramps_web_url_saved"] = _url
                if "_gramps_web_pwd_input" in st.session_state:
                    del st.session_state["_gramps_web_pwd_input"]
                st.rerun()
            except _requests.HTTPError as _e:
                st.error(t("gramps_web_auth_error", e=_e))
            except (_requests.ConnectionError, _requests.Timeout):
                st.error(t("gramps_web_conn_error"))
        else:
            st.warning(t("gramps_web_fill_all"))

    if _disconnect_clicked:
        for _k in ("gramps_web_token", "gramps_web_connected", "gramps_web_db_cache",
                   "_gramps_web_db_override", "tst_gramps_xml_path", "gramps_web_url_saved"):
            st.session_state.pop(_k, None)
        st.rerun()

# ── Cargar GrampsDB desde API si hay conexión activa ─────────────────────────
if st.session_state.get("gramps_web_connected"):
    _cached_db = st.session_state.get("gramps_web_db_cache")
    if _cached_db is None:
        _api_url   = st.session_state.get("gramps_web_url_saved", st.session_state.get("gramps_web_url", ""))
        _api_token = st.session_state.get("gramps_web_token", "")
        if _api_url and _api_token:
            try:
                with st.spinner(t("gramps_web_connecting")):
                    _cached_db = _fetch_gramps_db(_api_url, _api_token)
                st.session_state["gramps_web_db_cache"] = _cached_db
            except Exception as _fetch_err:
                st.error(t("gramps_web_fetch_error"))
                st.exception(_fetch_err)
    if _cached_db is not None:
        st.session_state["_gramps_web_db_override"] = _cached_db
else:
    st.session_state.pop("_gramps_web_db_override", None)

# ── 3. Selector de sección ────────────────────────────────────────────────────
st.sidebar.markdown("---")

# Solo corregir el valor del radio si el texto guardado no pertenece a la lista actual
# (ocurre tras cambio de idioma). En navegación normal no tocamos el session_state del widget.
_current_radio_val = st.session_state.get("_section_radio", "")
if _current_radio_val not in SECTION_LABELS:
    st.session_state["_section_radio"] = SECTION_LABELS[_section_idx]

selected_label = st.sidebar.radio(
    t("sidebar_section_label"),
    SECTION_LABELS,
    index=_section_idx,
    key="_section_radio",
)
_new_active = SECTION_KEYS[SECTION_LABELS.index(selected_label)] if selected_label in SECTION_LABELS else SECTION_KEYS[_section_idx]
if _new_active != active_section:
    st.session_state["active_section_key"] = _new_active
    st.rerun()

# ── 4. Controles de subsección del módulo activo ──────────────────────────────
render_sidebar()

# ── 5. Backup / Restore de datos de usuario ──────────────────────────────────
import io as _io
import zipfile as _zipfile
from pathlib import Path as _Path

_DATA_DIR = _Path(__file__).parent / "data"
_BACKUP_FILES = [
    "confirmed_links.json",
    "note_category_overrides.json",
    "archive_findings.json",
    "family_completion_results.json",
    "identity_resolution_results.json",
    "gen_record_dates.json",
]

st.sidebar.markdown("---")
with st.sidebar.expander("💾 Backup / Restore", expanded=False):
    # ── Exportar ──────────────────────────────────────────────────────────────
    def _build_backup_zip() -> bytes:
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
            for name in _BACKUP_FILES:
                p = _DATA_DIR / name
                if p.exists():
                    zf.write(p, name)
        return buf.getvalue()

    st.caption(t("backup_export_caption") if "backup_export_caption" in dir() else "Descarga un ZIP con todos los datos guardados.")
    st.download_button(
        label="⬇️ Exportar backup",
        data=_build_backup_zip(),
        file_name="rayzes_backup.zip",
        mime="application/zip",
        key="_backup_download",
    )

    st.markdown("---")

    # ── Importar ──────────────────────────────────────────────────────────────
    st.caption("Sube un backup para restaurar los datos. Los archivos existentes serán sobreescritos.")
    _uploaded_backup = st.file_uploader(
        "Selecciona rayzes_backup.zip",
        type=["zip"],
        key="_backup_upload",
    )
    if _uploaded_backup is not None:
        if st.button("✅ Restaurar backup", key="_backup_restore", type="primary"):
            try:
                with _zipfile.ZipFile(_io.BytesIO(_uploaded_backup.read())) as zf:
                    _restored = []
                    _skipped = []
                    for name in zf.namelist():
                        if name in _BACKUP_FILES:
                            _DATA_DIR.mkdir(exist_ok=True)
                            (_DATA_DIR / name).write_bytes(zf.read(name))
                            _restored.append(name)
                        else:
                            _skipped.append(name)
                if _restored:
                    st.success(f"Restaurados: {', '.join(_restored)}")
                if _skipped:
                    st.warning(f"Ignorados (no reconocidos): {', '.join(_skipped)}")
                st.rerun()
            except Exception as _e:
                st.error(f"Error al restaurar: {_e}")

from modules.shared.app_context import build_app_context
_ctx = build_app_context()
render_page(_ctx)
