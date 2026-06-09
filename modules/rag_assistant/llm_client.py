from __future__ import annotations

import requests


def _chat_completion_local(
    messages: list[dict],
    base_url: str,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"No se pudo conectar al servidor LLM en {base_url}. ¿Está llama-swap activo?")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"El servidor LLM tardó más de {timeout}s en responder.")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Error HTTP {e.response.status_code}: {e.response.text[:200]}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Respuesta inesperada del LLM: {e}")


def _chat_completion_claude(
    messages: list[dict],
    model: str,
    api_key: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("El paquete 'anthropic' no está instalado. Ejecuta: pip install anthropic")

    system_text = ""
    filtered = []
    for msg in messages:
        if msg.get("role") == "system":
            system_text = msg.get("content", "")
        else:
            filtered.append(msg)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": filtered,
    }
    if system_text:
        kwargs["system"] = system_text

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(**kwargs)
        return response.content[0].text or ""
    except anthropic.AuthenticationError:
        raise RuntimeError("API key de Anthropic inválida o expirada.")
    except anthropic.RateLimitError:
        raise RuntimeError("Límite de tasa de Anthropic alcanzado. Espera un momento y reintenta.")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Error de API Anthropic {e.status_code}: {e.message}")
    except Exception as e:
        raise RuntimeError(f"Error al llamar a Claude API: {e}")


def _chat_completion_openai_remote(
    messages: list[dict],
    model: str,
    api_key: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except requests.exceptions.ConnectionError:
        raise RuntimeError("No se pudo conectar a la API de OpenAI.")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"La API de OpenAI tardó más de {timeout}s en responder.")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        if status == 401:
            raise RuntimeError("API key de OpenAI inválida o expirada.")
        elif status == 429:
            raise RuntimeError("Límite de tasa de OpenAI alcanzado. Espera un momento y reintenta.")
        raise RuntimeError(f"Error HTTP {status}: {e.response.text[:200]}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Respuesta inesperada de OpenAI: {e}")


def chat_completion(
    messages: list[dict],
    base_url: str,
    model: str,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: int = 120,
    provider: str = "local",
    api_key: str | None = None,
) -> str:
    if provider == "claude":
        if not api_key:
            raise RuntimeError("Se requiere una API key para usar Claude API.")
        return _chat_completion_claude(messages, model, api_key, max_tokens, temperature, timeout)
    elif provider == "openai_remote":
        if not api_key:
            raise RuntimeError("Se requiere una API key para usar OpenAI API.")
        return _chat_completion_openai_remote(messages, model, api_key, max_tokens, temperature, timeout)
    else:
        return _chat_completion_local(messages, base_url, model, max_tokens, temperature, timeout)
