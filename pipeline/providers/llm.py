"""Text AI. 'openai_compat' works with any OpenAI-style API:
Gemini (free key from aistudio.google.com), Groq, DeepSeek, OpenAI, OpenRouter.
'mock' needs no key and returns a template storyboard (for plumbing tests)."""
import base64
import json
import re
import time

import httpx

import config

_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """An AI provider request failed after its retry policy."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def chat_json(system: str, user: str, images: list[bytes] | None = None) -> dict:
    if config.LLM_PROVIDER == "mock":
        raise NotImplementedError("mock provider is handled by the caller")

    try:
        return _chat_json_with_provider(
            "Primary provider", config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY,
            system, user, images,
        )
    except ProviderError as primary_error:
        if not config.GROQ_API_KEY or config.LLM_BASE_URL.rstrip("/") == config.GROQ_BASE_URL.rstrip("/"):
            raise
        try:
            return _chat_json_with_provider(
                "Groq fallback", config.GROQ_BASE_URL, config.GROQ_MODEL, config.GROQ_API_KEY,
                system, user, images,
            )
        except ProviderError as fallback_error:
            raise ProviderError(
                f"{primary_error}; Groq fallback also failed: {fallback_error}"
            ) from fallback_error


def _chat_json_with_provider(
    provider_name: str,
    base_url: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    images: list[bytes] | None = None,
) -> dict:
    if not api_key:
        raise ProviderError(f"{provider_name} API key is not configured")
    content = [{"type": "text", "text": user}]
    for img in images or []:
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content if images else user},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    for attempt in range(3):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=120)
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"{provider_name} request failed: {e}") from e
        except httpx.ReadTimeout as e:
            raise ProviderError(f"{provider_name} read timed out") from e
        except httpx.RequestError as e:
            if attempt == 2:
                raise ProviderError(f"{provider_name} request failed: {e}") from e
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 400 and "response_format" in r.text:
            body.pop("response_format", None)  # some providers don't support JSON mode
            continue
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _RETRYABLE_STATUS_CODES or attempt == 2:
                raise ProviderError(f"{provider_name} request failed: {e}") from e
            retry_after = e.response.headers.get("Retry-After")
            try:
                delay = max(0, min(int(retry_after), 60)) if retry_after is not None else 2 ** attempt
            except ValueError:
                delay = 2 ** attempt
            time.sleep(delay)
            continue
        try:
            text = r.json()["choices"][0]["message"]["content"]
            return _extract_json(text)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            if attempt == 2:
                raise ProviderError(f"{provider_name} did not return valid JSON: {str(e)}") from e
    raise ProviderError("LLM call failed")
