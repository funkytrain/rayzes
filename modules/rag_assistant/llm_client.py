from __future__ import annotations

import requests


def chat_completion(
    messages: list[dict],
    base_url: str,
    model: str,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: int = 120,
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
