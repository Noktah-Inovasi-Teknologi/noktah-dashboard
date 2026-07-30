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

# OpenRouter attributes spend on its own activity dashboard by these two headers.
# Without them every call from this stack arrives as one undifferentiated app, so
# songbird's generation spend can't be told apart from roach's analysis spend.
APP_TITLE = os.environ.get("OPENROUTER_APP_TITLE", "noktah-prefect")
APP_REFERER = os.environ.get("OPENROUTER_APP_URL", "https://github.com/noktah/noktah-dashboard")

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
        # Usage accounting: opting in adds the resolved USD `cost` to the usage
        # object alongside the token counts, so spend is read off the response
        # rather than estimated from a price list.
        "usage": {"include": True},
        "messages": messages,
    }
    if response_format:
        payload["response_format"] = response_format
    if PROVIDER_ORDER:
        payload["provider"] = {"order": PROVIDER_ORDER, "allow_fallbacks": ALLOW_FALLBACKS}
    return payload


def _run_logger():
    """The Prefect run logger when inside a run, else the module logger.

    Token usage is only useful if it lands somewhere someone reads — the Prefect
    UI for a flow run, stderr for a standalone script.
    """
    try:
        from prefect import get_run_logger

        return get_run_logger()
    except Exception:
        return logger


def _log_usage(body: Dict[str, Any], call_site: str, model: str, client: Optional[str], log) -> None:
    """Record the token usage OpenRouter returns on every call.

    The counts (and, with usage accounting on, the cost) ride on every response
    and used to be discarded with the rest of the envelope — which left
    generation spend unmeasurable. Best-effort: never let accounting break a run.
    """
    try:
        usage = body.get("usage") or {}
        if not usage:
            return
        cost = usage.get("cost")
        log.info(
            f"[openrouter] usage call_site={call_site} model={body.get('model') or model} "
            f"client={client or '-'} prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} "
            f"total_tokens={usage.get('total_tokens')}"
            + (f" cost_usd={cost}" if cost is not None else "")
        )
    except Exception:
        pass


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
    call_site: str = "unknown",
    client: Optional[str] = None,
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
        call_site: Label for which code path spent the tokens (usage logging).
        client: Client the spend belongs to, when the caller knows it.

    Returns:
        Parsed JSON (dict/list) when response_format is given, else the raw
        string content.

    Raises:
        RuntimeError on non-retryable HTTP errors or exhausted attempts.
    """
    # docker-compose passes the var through as an empty string when it is missing from
    # .env, so `os.environ[...]` succeeds and the empty key only surfaces deep in httpx
    # as "Illegal header value b'Bearer '". Fail here with the actual cause instead.
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set for this service. Add it to the project-root "
            ".env (docker-compose passes it to prefect + prefect-worker), then recreate "
            "the containers: docker-compose up -d prefect prefect-worker"
        )
    model = model or DEFAULT_MODEL
    payload = _build_payload(system, user, model, max_tokens, response_format, temperature)
    log = _run_logger()

    last_error: Exception = RuntimeError("unreachable")
    async with httpx.AsyncClient(timeout=180.0) as http_client:
        for attempt in range(MAX_ATTEMPTS):
            resp = await http_client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-Title": APP_TITLE,
                    "HTTP-Referer": APP_REFERER,
                },
                json=payload,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() \
                    else RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)]
                last_error = RuntimeError(f"429 rate limited on attempt {attempt + 1}; backing off {delay}s")
                log.warning(str(last_error))
                import asyncio
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 400:
                # Other 4xx/5xx won't improve on retry — surface the provider body.
                raise RuntimeError(f"OpenRouter {model} HTTP {resp.status_code}: {resp.text[:400]}")
            body = resp.json()
            # Log usage before anything below can raise/continue — the tokens were
            # spent whether or not the content turns out to be usable.
            _log_usage(body, call_site, model, client, log)
            choice = body["choices"][0]
            # A response cut off at max_tokens still parses — `_extract_json`'s regex
            # fallback salvages the truncated array — so items silently go missing.
            # Surface it rather than letting the caller wonder where the rest went.
            if choice.get("finish_reason") == "length":
                log.warning(
                    f"OpenRouter {model} hit the {max_tokens}-token output limit; the "
                    f"response was truncated and some items may be missing"
                )
            content = choice["message"]["content"]
            if not content:
                last_error = RuntimeError(f"empty content on attempt {attempt + 1}")
                log.warning(str(last_error))
                continue
            if response_format is None:
                return content
            try:
                return _extract_json(content)
            except json.JSONDecodeError as e:
                last_error = e
                log.warning(f"JSON parse failed on attempt {attempt + 1}: {e}")
    raise last_error
