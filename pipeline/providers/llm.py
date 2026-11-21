"""Text AI. 'openai_compat' works with any OpenAI-style API:
Gemini (free key from aistudio.google.com), Groq, DeepSeek, OpenAI, OpenRouter.
'mock' needs no key and returns a template storyboard (for plumbing tests)."""
import base64
import json
import re

import httpx

import config


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
    if not config.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set in .env")
    content = [{"type": "text", "text": user}]
    for img in images or []:
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content if images else user},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    for attempt in range(3):
        r = httpx.post(url, json=body, headers=headers, timeout=120)
        if r.status_code == 400 and "response_format" in r.text:
            body.pop("response_format", None)  # some providers don't support JSON mode
            continue
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        try:
            return _extract_json(text)
        except Exception:
            if attempt == 2:
                raise ValueError(f"AI did not return valid JSON: {text[:300]}")
    raise RuntimeError("LLM call failed")
