from __future__ import annotations

import hashlib

import streamlit as st

from translations import t


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gramps_hash(content_bytes: bytes) -> str:
    return hashlib.md5(content_bytes).hexdigest()


def _doc_uploads_key(uploads) -> list[str]:
    if not uploads:
        return []
    return sorted(f.name for f in uploads)


def _ensure_index(content_bytes: bytes, doc_uploads) -> None:
    from modules.rag_assistant.index_store import (
        get_stored_gramps_hash, get_stored_doc_filenames, load_index, save_index,
    )
    from modules.rag_assistant.chunker import build_all_chunks
    from modules.rag_assistant.retriever import build_index
    from modules.shared.gramps_parser import parse_gramps

    current_hash = _gramps_hash(content_bytes)
    current_doc_names = _doc_uploads_key(doc_uploads)
    force = st.session_state.pop("rag_force_rebuild", False)

    # Already in session and up to date
    if (
        not force
        and st.session_state.get("rag_index_obj") is not None
        and st.session_state.get("rag_index_hash") == current_hash
        and st.session_state.get("rag_index_doc_names") == current_doc_names
    ):
        return

    # Try loading from disk
    if not force:
        stored_hash = get_stored_gramps_hash()
        stored_docs = get_stored_doc_filenames()
        if stored_hash == current_hash and stored_docs == current_doc_names:
            index = load_index()
            if index is not None:
                st.session_state["rag_index_obj"] = index
                st.session_state["rag_index_hash"] = current_hash
                st.session_state["rag_index_doc_names"] = current_doc_names
                st.session_state["rag_index_meta"] = index.meta
                return

    # Rebuild
    base_url = st.session_state.get("rag_llm_base_url", "http://localhost:8080/v1")
    model = st.session_state.get("rag_llm_model", "qwen3-14b")

    with st.spinner(t("rag_index_building")):
        db = parse_gramps(content_bytes)

        doc_pairs: list[tuple[str, bytes]] = []
        if doc_uploads:
            for f in doc_uploads:
                f.seek(0)
                doc_pairs.append((f.name, f.read()))

        chunks = build_all_chunks(db, doc_pairs)

        index = build_index(
            chunks,
            base_url=base_url,
            model=model,
            gramps_hash=current_hash,
            doc_filenames=current_doc_names,
            db=db,
        )
        save_index(index)

    st.session_state["rag_index_obj"] = index
    st.session_state["rag_index_hash"] = current_hash
    st.session_state["rag_index_doc_names"] = current_doc_names
    st.session_state["rag_index_meta"] = index.meta
    st.success(t("rag_index_ready").format(n_chunks=len(chunks)))


def _answer_question(question: str, index, history: list[dict]) -> tuple[str, list]:
    from modules.rag_assistant.retriever import retrieve
    from modules.rag_assistant.prompt_builder import build_messages, DEFAULT_SYSTEM_PROMPT
    from modules.rag_assistant.llm_client import chat_completion

    top_k = st.session_state.get("rag_top_k", 5)
    max_tokens = st.session_state.get("rag_max_ctx_tokens", 3000)
    base_url = st.session_state.get("rag_llm_base_url", "http://localhost:8080/v1")
    model = st.session_state.get("rag_llm_model", "qwen3-14b")

    retrieved = retrieve(question, index, top_k=top_k, base_url=base_url, model=model)

    if not retrieved:
        return t("rag_no_context"), []

    system_prompt = st.session_state.get("rag_system_prompt") or DEFAULT_SYSTEM_PROMPT
    messages = build_messages(
        question, retrieved, history,
        system_prompt=system_prompt,
        max_context_tokens=max_tokens,
        tree_stats=index.tree_stats,
    )

    try:
        answer = chat_completion(
            messages,
            base_url=base_url,
            model=model,
            max_tokens=min(max_tokens // 2, 2000),
        )
    except RuntimeError as e:
        return t("rag_llm_error").format(error=str(e)), retrieved

    return answer, retrieved


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit render functions
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    st.sidebar.markdown(t("rag_sidebar_header"))

    # GRAMPS file indicator
    shared_bytes = st.session_state.get("shared_gramps_bytes")
    shared_name = st.session_state.get("shared_gramps_name", "")
    if shared_bytes:
        st.sidebar.success(f"📂 {shared_name}")
    else:
        uploaded = st.sidebar.file_uploader(
            "Archivo .gramps",
            type=["gramps", "xml"],
            key="rag_gramps_uploader",
        )
        if uploaded:
            uploaded.seek(0)
            st.session_state["shared_gramps_bytes"] = uploaded.read()
            st.session_state["shared_gramps_name"] = uploaded.name

    st.sidebar.markdown("---")

    # LLM config
    st.sidebar.text_input(
        t("rag_sidebar_llm_url"),
        value=st.session_state.get("rag_llm_base_url", "http://127.0.0.1:9292/v1"),
        key="rag_llm_base_url",
    )
    st.sidebar.text_input(
        t("rag_sidebar_model"),
        value=st.session_state.get("rag_llm_model", "qwen3-14b"),
        key="rag_llm_model",
    )
    st.sidebar.slider(t("rag_sidebar_topk"), 1, 20, 5, key="rag_top_k")
    st.sidebar.slider(
        t("rag_sidebar_max_tokens"), 512, 8192, 3000, step=256, key="rag_max_ctx_tokens"
    )

    st.sidebar.markdown("---")

    # Additional documents
    st.sidebar.file_uploader(
        t("rag_sidebar_upload_docs"),
        type=["pdf", "txt"],
        accept_multiple_files=True,
        key="rag_doc_uploads",
    )

    # Rebuild button
    if st.sidebar.button(t("rag_sidebar_rebuild_btn"), key="rag_rebuild_btn"):
        st.session_state["rag_force_rebuild"] = True

    # Index status
    meta = st.session_state.get("rag_index_meta")
    if meta:
        strategy_label = (
            t("rag_strategy_embeddings")
            if meta.get("strategy") == "embeddings"
            else t("rag_strategy_tfidf")
        )
        st.sidebar.caption(
            t("rag_index_info").format(n=meta.get("n_chunks", 0), strategy=strategy_label)
        )
        n_docs = meta.get("n_chunks_docs", 0)
        if n_docs:
            st.sidebar.caption(t("rag_index_chunks_docs").format(n=n_docs))


def render_page() -> None:
    st.title(t("rag_page_title"))
    st.caption(t("rag_page_caption"))

    content_bytes = st.session_state.get("shared_gramps_bytes")
    if not content_bytes:
        st.info(t("rag_no_gramps_file"))
        return

    doc_uploads = st.session_state.get("rag_doc_uploads") or []

    _ensure_index(content_bytes, doc_uploads)

    rag_index = st.session_state.get("rag_index_obj")
    if rag_index is None:
        st.warning(t("rag_index_building"))
        return

    # ── Layout ───────────────────────────────────────────────────────────────
    col_chat, col_sources = st.columns([3, 1])

    with col_chat:
        if st.button(t("rag_clear_chat"), key="rag_clear_chat_btn"):
            st.session_state["rag_chat_history"] = []
            st.session_state["rag_last_sources"] = []

        history: list[dict] = st.session_state.setdefault("rag_chat_history", [])

        # Render existing messages
        for msg in history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # New input
        if question := st.chat_input(t("rag_chat_placeholder"), key="rag_chat_input"):
            with st.chat_message("user"):
                st.markdown(question)
            history.append({"role": "user", "content": question})

            with st.chat_message("assistant"):
                with st.spinner(t("rag_thinking")):
                    answer, sources = _answer_question(question, rag_index, history[:-1])
                st.markdown(answer)
            history.append({"role": "assistant", "content": answer})
            st.session_state["rag_last_sources"] = sources
            st.rerun()

    with col_sources:
        st.markdown(f"**{t('rag_sources_header')}**")
        sources = st.session_state.get("rag_last_sources") or []
        if not sources:
            st.caption("—")
        for chunk, score in sources:
            with st.expander(f"{chunk.source_label} ({score:.2f})", expanded=False):
                st.caption(chunk.chunk_id)
                preview = chunk.text[:400] + ("…" if len(chunk.text) > 400 else "")
                st.text(preview)
