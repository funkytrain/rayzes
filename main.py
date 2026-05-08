import streamlit as st
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

SECTION_KEYS = ["testigos", "consanguinidad", "general", "adn"]
SECTION_LABELS = [t("section_testigos"), t("section_consanguinidad"), t("section_general"), t("section_adn")]

if "active_section_key" not in st.session_state:
    st.session_state["active_section_key"] = SECTION_KEYS[0]

_section_idx = SECTION_KEYS.index(st.session_state.get("active_section_key", SECTION_KEYS[0]))

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
active_section = SECTION_KEYS[SECTION_LABELS.index(selected_label)] if selected_label in SECTION_LABELS else SECTION_KEYS[_section_idx]
st.session_state["active_section_key"] = active_section
st.sidebar.markdown("---")

if active_section == "testigos":
    from modules.testigos import render_sidebar, render_page
elif active_section == "consanguinidad":
    from modules.consanguinidad import render_sidebar, render_page
elif active_section == "general":
    from modules.general import render_sidebar, render_page
elif active_section == "adn":
    from modules.adn import render_sidebar, render_page

render_sidebar()
render_page()
