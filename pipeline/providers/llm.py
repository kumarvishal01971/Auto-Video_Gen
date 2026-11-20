"""Text AI. 'openai_compat' works with any OpenAI-style API:
Gemini (free key from aistudio.google.com), Groq, DeepSeek, OpenAI, OpenRouter.
'mock' needs no key and returns a template storyboard (for plumbing tests)."""
import base64
import json
import re

import httpx

import config


class ProviderError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def _chat_json_with_provider(
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    images: list[bytes] | None = None,
) -> dict:
    if not api_key:
        raise ProviderError(f"{provider} API key is not set in .env")
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
            if r.status_code == 400 and "response_format" in r.text:
                body.pop("response_format", None)
                continue
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise ValueError("response content is not text")
            return _extract_json(text)
        except httpx.HTTPError as e:
            raise ProviderError(f"{provider} request failed: {e}") from e
        except (ValueError, KeyError, IndexError, TypeError) as e:
            if attempt == 2:
                raise ProviderError(f"{provider} did not return valid JSON: {e}") from e
    raise ProviderError(f"{provider} request failed after retrying without JSON mode")


def chat_json(system: str, user: str, images: list[bytes] | None = None) -> dict:
    if config.LLM_PROVIDER == "mock":
        raise NotImplementedError("mock provider is handled by the caller")
    try:
        return _chat_json_with_provider(
            "Primary LLM", config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY,
            system, user, images,
        )
    except ProviderError as primary_error:
        if not config.GROQ_API_KEY:
            raise
        try:
            return _chat_json_with_provider(
                "Groq fallback", config.GROQ_BASE_URL, config.GROQ_MODEL, config.GROQ_API_KEY,
                system, user, images,
            )
        except ProviderError as fallback_error:
            raise ProviderError(
                f"Primary LLM failed ({primary_error}); Groq fallback failed ({fallback_error})"
            ) from fallback_error
