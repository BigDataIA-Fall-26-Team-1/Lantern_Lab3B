"""Part 2: the one place that talks to OpenAI (httpx, no extra package).

The API key is read from OPENAI_API_KEY at the moment of the call and goes ONLY into the
Authorization header of that one HTTP request. It is never part of the request body that the
replay cache fingerprints, never stored, never printed, and is removed from every error message.
"""
import os
import re

import httpx

_SK = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


class ProviderError(RuntimeError):
    """A provider call failed. The message never contains the key."""


def redact(text):
    text = str(text)
    key = os.environ.get("OPENAI_API_KEY") or ""
    if key:
        text = text.replace(key, "[REDACTED]")
    return _SK.sub("sk-[REDACTED]", text)


def chat(body, base_url, timeout=120):
    """POST /chat/completions with `body` (no secrets in it); returns the parsed JSON response."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ProviderError("OPENAI_API_KEY is not set in this environment")
    try:
        r = httpx.post(f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=timeout,
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise ProviderError(redact(f"{type(e).__name__}: {e}")) from None
    finally:
        del key
    if r.status_code >= 400:
        raise ProviderError(redact(f"HTTP {r.status_code} from the provider: {r.text[:600]}"))
    return r.json()
