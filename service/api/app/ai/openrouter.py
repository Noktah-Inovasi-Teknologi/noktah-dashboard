"""
OpenRouter chat call for the Hub's AI (Intake, Summary). Structured output only.

Validation discipline copied from roach feature 007 (constitution II/XI):
  - `finish_reason == "length"` is checked BEFORE parsing: a truncated array is
    still valid JSON and would pass the schema;
  - the response must parse as JSON as-is (code fences stripped, nothing else).
    There is no salvage regex: a half-answer must not become a Proposal;
  - schema/semantic validation runs in `validate(parsed)`; on failure the call is
    retried EXACTLY ONCE with the validator's own error quoted back as a
    follow-up turn (media is not re-sent: it is already in the conversation);
  - a second failure raises AiFailure('validation_failed').

Usage and cost are read off the response (`usage: {include: true}`), and
`model`/`provider` are what actually answered, since routing allows fallbacks.

JSON mode, not provider-side JSON schema. The schema goes into the system message
and the provider is asked only for `json_object`. Measured 2026-09-25: asking for
`json_schema` limited xiaomi/mimo-v2.5 to the three providers that support it, so
every call went to DeepInfra, whose shared OpenRouter pool answered 7 of 10 calls
with "temporarily rate-limited upstream". Xiaomi's own endpoint (98% uptime) and
Novita support JSON mode. Our validator was always the authority, so nothing
weaker gets through.
"""
import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Providers serving xiaomi/mimo-v2.5 with JSON mode, checked 2026-09-25 against
# openrouter.ai/api/v1/models/xiaomi/mimo-v2.5/endpoints (DigitalOcean and Parasail no
# longer serve it). DeepInfra last: its shared pool is the one that throttles.
PROVIDER_ORDER = ["xiaomi", "novita", "streamlake", "deepinfra"]
RATE_LIMIT_BACKOFF = [5, 15]


class AiFailure(Exception):
    """A call that produced nothing usable. `reason` matches intakes.failure_reason."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        # What the failed call still cost (a truncated or twice-invalid answer is billed).
        self.model: Optional[str] = None
        self.provider: Optional[str] = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0

    def spent(self, model: Optional[str], provider: Optional[str], totals: Dict[str, Any]) -> "AiFailure":
        self.model, self.provider = model, provider
        self.prompt_tokens, self.completion_tokens = totals["prompt"], totals["completion"]
        self.cost_usd = round(totals["cost"], 6)
        return self


@dataclass
class AiResult:
    value: Any
    model: Optional[str]
    provider: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    attempts: int


def with_schema(messages: List[Dict[str, Any]], schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Append the answer's JSON schema to the system message (JSON mode carries no schema)."""
    note = ("\n\nJawab dengan SATU objek JSON yang sesuai skema berikut, tanpa teks lain:\n"
            + json.dumps(schema, ensure_ascii=False))
    out = [dict(m) for m in messages]
    if out and out[0].get("role") == "system" and isinstance(out[0].get("content"), str):
        out[0]["content"] += note
    else:
        out.insert(0, {"role": "system", "content": note.strip()})
    return out


def _parse(content: str) -> Any:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


async def chat_json(
    *,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    schema: Dict[str, Any],
    validate: Callable[[Any], None],
    call_site: str,
    max_tokens: int = 6000,
    timeout: float = 90.0,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> AiResult:
    """One structured call with one validation retry. Raises AiFailure."""
    if not api_key:
        raise AiFailure("provider_error", "OPENROUTER_API_KEY is not set")
    convo = with_schema(messages, schema)
    totals = {"prompt": 0, "completion": 0, "cost": 0.0}
    served_model: Optional[str] = None
    provider: Optional[str] = None

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        for attempt in (1, 2):
            payload = {
                "model": model,
                "messages": convo,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "reasoning": {"enabled": False},
                "usage": {"include": True},
                "response_format": {"type": "json_object"},
                "provider": {"order": PROVIDER_ORDER, "allow_fallbacks": True},
            }
            body = await _post(client, api_key, payload, call_site)
            usage = body.get("usage") or {}
            totals["prompt"] += int(usage.get("prompt_tokens") or 0)
            totals["completion"] += int(usage.get("completion_tokens") or 0)
            totals["cost"] += float(usage.get("cost") or 0.0)
            served_model = body.get("model") or served_model
            provider = body.get("provider") or provider

            choice = (body.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise AiFailure("truncated", f"max_tokens={max_tokens}").spent(served_model, provider, totals)
            content = (choice.get("message") or {}).get("content") or ""
            try:
                parsed = _parse(content)
                validate(parsed)
            except (json.JSONDecodeError, ValueError) as e:
                if attempt == 2:
                    raise AiFailure("validation_failed", str(e)).spent(served_model, provider, totals) from e
                convo = convo + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content":
                        "Jawaban tidak valid. Pesan validator (dikutip apa adanya): "
                        f"\"{e}\". Kirim ulang SELURUH jawaban sebagai JSON yang sesuai skema."},
                ]
                continue
            return AiResult(parsed, served_model, provider, totals["prompt"], totals["completion"],
                            round(totals["cost"], 6), attempt)
    raise AiFailure("validation_failed", "no valid answer")  # unreachable


async def _post(client: httpx.AsyncClient, api_key: str, payload: Dict[str, Any], call_site: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter attributes spend on its dashboard by these.
        "X-Title": f"Noktah Hub ({call_site})",
        "HTTP-Referer": "https://hub.noktah.co",
    }
    for attempt, backoff in enumerate([*RATE_LIMIT_BACKOFF, None]):
        try:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise AiFailure("timeout", str(e)) from e
        except httpx.HTTPError as e:
            raise AiFailure("provider_error", str(e)) from e
        if response.status_code == 429 and backoff is not None:
            await asyncio.sleep(backoff)
            continue
        if response.status_code >= 400:
            raise AiFailure("provider_error", f"HTTP {response.status_code}: {response.text[:300]}")
        body = response.json()
        if body.get("error"):
            raise AiFailure("provider_error", str(body["error"])[:300])
        return body
    raise AiFailure("provider_error", "rate limited")
