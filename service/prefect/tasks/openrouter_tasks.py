"""
OpenRouter chat-completion task for Prefect workflows.

A thin, text-only Prefect wrapper around the OpenRouter chat-completions API,
porting the provider-routing + structured-JSON + 429 back-off pattern proven in
the roach service (service/roach/analyze.py) so songbird (and any future flow)
gets the same deterministic, parse-safe LLM behaviour without roach's media
machinery.

Single responsibility, raises on failure so Prefect's retry mechanism handles
transient errors (constitution I). Requires OPENROUTER_API_KEY in the env.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from prefect import task

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Same house default as roach; override per-call or via env.
DEFAULT_MODEL = os.environ.get("SONGBIRD_OPENROUTER_MODEL", os.environ.get("OPENROUTER_MODEL", "xiaomi/mimo-v2.5"))

# Deterministic provider routing (mirrors roach). Names map to OpenRouter slugs.
_PROVIDER_SLUGS = {
    "xiaomi": "xiaomi",
    "digitalocean": "digitalocean",
    "novitaai": "novita",
    "novita": "novita",
    "parasail": "parasail",
}
_PROVIDER_ORDER_RAW = os.environ.get("OPENROUTER_PROVIDER_ORDER", "Xiaomi,DigitalOcean,NovitaAI,Parasail")
PROVIDER_ORDER = [
    _PROVIDER_SLUGS.get(p.strip().lower(), p.strip().lower())
    for p in _PROVIDER_ORDER_RAW.split(",")
    if p.strip()
]
ALLOW_FALLBACKS = os.environ.get("OPENROUTER_ALLOW_FALLBACKS", "true").strip().lower() not in {"0", "false", "no"}

MAX_ATTEMPTS = 4
RATE_LIMIT_BACKOFF = [10, 20, 40]  # seconds, honors Retry-After when provided


def _extract_json(content: str) -> Any:
    """Parse a JSON value from model output, tolerating fences and stray prose."""
    text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} or [...] block anywhere in the text.
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise json.JSONDecodeError("no JSON value found", text, 0)


def _build_payload(
    system: Optional[str], user: str, model: str, max_tokens: int,
    response_format: Optional[Dict[str, Any]], temperature: float,
) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Suppress internal reasoning so a reasoning model doesn't exhaust the
        # token budget before writing the answer (roach's empty-content guard).
        "reasoning": {"enabled": False},
        "messages": messages,
    }
    if response_format:
        payload["response_format"] = response_format
    if PROVIDER_ORDER:
        payload["provider"] = {"order": PROVIDER_ORDER, "allow_fallbacks": ALLOW_FALLBACKS}
    return payload


def object_schema(name: str, keys: List[str]) -> Dict[str, Any]:
    """Build a strict json_schema response_format for an object of string keys."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in keys},
                "required": keys,
                "additionalProperties": False,
            },
        },
    }


def array_schema(name: str, item_keys: List[str]) -> Dict[str, Any]:
    """Build a strict json_schema response_format for an array of item objects.

    OpenRouter/OpenAI json_schema roots must be objects, so the array is wrapped
    under an `items` property — callers read result["items"].
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {k: {"type": "string"} for k in item_keys},
                            "required": item_keys,
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        },
    }


@task(name="openrouter.chat.complete", retries=2, retry_delay_seconds=30)
async def openrouter_chat(
    user: str,
    system: Optional[str] = None,
    response_format: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    max_tokens: int = 4000,
    temperature: float = 0.8,
) -> Any:
    """
    Call OpenRouter chat-completions and return the model's answer.

    Args:
        user: User-turn prompt text.
        system: Optional system-turn prompt (persona / rules).
        response_format: Optional OpenRouter response_format (use object_schema /
            array_schema helpers). When set, the return value is the parsed JSON;
            otherwise the raw string content is returned.
        model: Model id (defaults to the house model).
        max_tokens: Output token budget.
        temperature: Sampling temperature (generation wants some variety).

    Returns:
        Parsed JSON (dict/list) when response_format is given, else the raw
        string content.

    Raises:
        RuntimeError on non-retryable HTTP errors or exhausted attempts.
    """
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = model or DEFAULT_MODEL
    payload = _build_payload(system, user, model, max_tokens, response_format, temperature)

    last_error: Exception = RuntimeError("unreachable")
    async with httpx.AsyncClient(timeout=180.0) as client:
        for attempt in range(MAX_ATTEMPTS):
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() \
                    else RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)]
                last_error = RuntimeError(f"429 rate limited on attempt {attempt + 1}; backing off {delay}s")
                logger.warning(str(last_error))
                import asyncio
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 400:
                # Other 4xx/5xx won't improve on retry — surface the provider body.
                raise RuntimeError(f"OpenRouter {model} HTTP {resp.status_code}: {resp.text[:400]}")
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                last_error = RuntimeError(f"empty content on attempt {attempt + 1}")
                logger.warning(str(last_error))
                continue
            if response_format is None:
                return content
            try:
                return _extract_json(content)
            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(f"JSON parse failed on attempt {attempt + 1}: {e}")
    raise last_error
