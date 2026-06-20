"""
AppContext — contrato explícito entre main.py y los módulos.

En lugar de que cada módulo lea st.session_state directamente para obtener
la fuente de datos GRAMPS y la configuración LLM, main.py construye un
AppContext una vez por render y lo pasa a render_page(ctx).

Los módulos que todavía no han migrado pueden leer las mismas claves de
session_state como siempre — AppContext no rompe nada, solo añade el seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMConfig:
    """Configuración del proveedor LLM, compartida por todos los módulos."""
    base_url: str = "http://127.0.0.1:9292/v1"
    model: str = "qwen3-14b"
    timeout: int = 300
    provider: str = "local"          # "local" | "claude" | "openai"
    api_key: Optional[str] = None


@dataclass
class GrampsSource:
    """Fuente de datos GRAMPS activa."""
    bytes_: Optional[bytes] = None   # contenido del archivo .gramps
    name: str = ""                   # nombre de archivo (para display)
    db: object = None                # GrampsDB ya parseado (desde API o caché)
    is_api: bool = False             # True si proviene de Gramps Web API
    api_url: str = ""
    api_token: str = ""


@dataclass
class AppContext:
    """
    Contrato explícito de dependencias cross-módulo.

    Construido una vez en main.py y pasado a render_page(ctx).
    Los módulos que lo adoptan dejan de leer session_state para estas claves.
    """
    gramps: GrampsSource = field(default_factory=GrampsSource)
    llm: LLMConfig = field(default_factory=LLMConfig)
    lang: str = "es"


def build_app_context() -> AppContext:
    """
    Construye AppContext desde st.session_state.
    Llamar una vez por ciclo de render, desde main.py.
    """
    import streamlit as st

    gramps = GrampsSource(
        bytes_=st.session_state.get("shared_gramps_bytes"),
        name=st.session_state.get("shared_gramps_name", ""),
        db=st.session_state.get("_gramps_web_db_override"),
        is_api=bool(st.session_state.get("gramps_web_connected")),
        api_url=st.session_state.get("gramps_web_url_saved", ""),
        api_token=st.session_state.get("gramps_web_token", ""),
    )

    llm = LLMConfig(
        base_url=st.session_state.get("rag_llm_base_url", "http://127.0.0.1:9292/v1"),
        model=st.session_state.get("rag_llm_model", "qwen3-14b"),
        timeout=int(st.session_state.get("rag_llm_timeout", 300)),
        provider=st.session_state.get("rag_llm_provider", "local"),
        api_key=st.session_state.get("rag_llm_api_key") or None,
    )

    from translations import get_lang
    return AppContext(gramps=gramps, llm=llm, lang=get_lang())
