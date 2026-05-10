from __future__ import annotations

from modules.rag_assistant.chunker import RagChunk

DEFAULT_SYSTEM_PROMPT = (
    "Eres un asistente experto en genealogía que responde preguntas sobre un árbol genealógico real. "
    "Dispones de fragmentos de información extraídos de la base de datos genealógica del usuario. "
    "Responde siempre en el mismo idioma que la pregunta. "
    "Cita los nombres y fechas que aparecen en los fragmentos. "
    "No inventes datos que no estén en el contexto proporcionado. "
    "Si la información es insuficiente para responder, dilo claramente."
)

_CHARS_PER_TOKEN = 4  # heuristic


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def build_messages(
    question: str,
    retrieved_chunks: list[tuple[RagChunk, float]],
    history: list[dict],
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_context_tokens: int = 3000,
    tree_stats: str = "",
) -> list[dict]:
    budget = max_context_tokens
    system_tokens = _estimate_tokens(system_prompt)
    budget -= system_tokens
    budget -= _estimate_tokens(question) + 10  # +10 for role overhead
    if tree_stats:
        budget -= _estimate_tokens(tree_stats)

    # Build context block from retrieved chunks
    context_lines: list[str] = ["--- CONTEXTO GENEALÓGICO ---"]
    context_tokens = _estimate_tokens(context_lines[0])

    for chunk, score in retrieved_chunks:
        header = f"\n[Fragmento — score: {score:.2f}]\nFuente: {chunk.source_label}"
        body = chunk.text
        needed = _estimate_tokens(header + "\n" + body)
        if context_tokens + needed > budget * 0.65:
            # try truncated
            max_body_chars = max(100, int((budget * 0.65 - context_tokens - _estimate_tokens(header)) * _CHARS_PER_TOKEN))
            body = body[:max_body_chars] + "…"
            needed = _estimate_tokens(header + "\n" + body)
        if context_tokens + needed > budget * 0.65:
            break
        context_lines.append(header + "\n" + body)
        context_tokens += needed

    context_lines.append("--- FIN CONTEXTO ---")
    context_block = "\n".join(context_lines)
    budget -= context_tokens

    # Fit history into remaining budget (drop oldest pairs first)
    history_pairs: list[tuple[dict, dict]] = []
    i = 0
    while i + 1 < len(history):
        if history[i]["role"] == "user" and history[i + 1]["role"] == "assistant":
            history_pairs.append((history[i], history[i + 1]))
            i += 2
        else:
            i += 1

    selected_history: list[dict] = []
    remaining = budget
    for user_msg, asst_msg in reversed(history_pairs):
        pair_tokens = _estimate_tokens(user_msg["content"]) + _estimate_tokens(asst_msg["content"]) + 10
        if pair_tokens <= remaining:
            selected_history = [user_msg, asst_msg] + selected_history
            remaining -= pair_tokens
        else:
            break

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if tree_stats:
        messages.append({"role": "system", "content": tree_stats})
    messages.append({"role": "system", "content": context_block})
    messages.extend(selected_history)
    messages.append({"role": "user", "content": question})

    return messages
