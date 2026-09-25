"""Per-item content analysis via OpenRouter, on the accepted models in config/ai/models.yaml.

Video items send the full video (visuals + audio) in one call and get a subtitle
transcript, content-flow breakdown, and summary. Image/carousel items (no audio)
send the image(s) instead and get an empty subtitle, per spec Assumptions and
research.md R9 — non-applicable analysis fields are left empty, not errored.
"""
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

import model_rotation
from extraction_models import ExtractionInvalid, validate_extraction

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Video analysis needs an audio+video-capable model. Image/carousel analysis uses
# a separate, cheaper vision model. Both come from config/ai/models.yaml (cases
# `video` and `image`), each an ordered list that rotates to the next model after
# repeated failures (model_rotation.py). OPENROUTER_MODEL / OPENROUTER_IMAGE_MODEL
# no longer choose anything; a warning at startup says so if they are still set.
#
# NOTE ON WHY: this split originally existed because the video model's providers
# did not reliably accept image input ("image-422 failures"). **That is no longer
# true** — measured 2026-08-08, one image sent to xiaomi/mimo-v2.5 was accepted on
# the first attempt (specs/007-structured-extraction/research.md R8, probe result).
#
# The split is kept for a different and now-primary reason: COST. Measured per
# item, the image model is ~4.5x cheaper ($0.00043 vs $0.00193). Keeping the
# capability rationale in this comment would have made a stale constraint look
# like a live one, and it is exactly the kind of inherited "we can't" that stops
# anyone re-testing it.
ROTATIONS = model_rotation.load()
VIDEO_MODELS = ROTATIONS["video"]
IMAGE_MODELS = ROTATIONS["image"]
for _var in ("OPENROUTER_MODEL", "OPENROUTER_IMAGE_MODEL"):
    if os.environ.get(_var):
        print(f"[models] {_var} is set but ignored: models come from {model_rotation.models_file()}", flush=True)

# OpenRouter provider routing: same model, deterministic providers. Xiaomi is the
# primary (it hosts MiMo directly); the rest are ordered fallbacks used only when
# Xiaomi is unavailable. Removes the provider-roulette behind empty-content and
# image-422 failures. Names are mapped to OpenRouter provider slugs below.
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
# roach's analysis spend can't be told apart from songbird's generation spend.
APP_TITLE = os.environ.get("OPENROUTER_APP_TITLE", "noktah-roach")
APP_REFERER = os.environ.get("OPENROUTER_APP_URL", "https://github.com/noktah/noktah-dashboard")

# Token budgets. Video needs headroom for a full transcript AND the structured
# fields that now share the same response (FR-011).
#
# Raised from 8000 when beats[] and attributes[] were added. The sizing evidence
# is deliberately conservative because it is RIGHT-CENSORED: the largest stored
# transcript is 4,812 chars (~1.6k tokens), but every response that was truncated
# before this feature got regex-salvaged into looking complete (research.md R4),
# so that maximum is a lower bound on the real one, not the real one. A beat array
# with descriptions runs a few hundred tokens more. 12000 leaves room for a
# transcript several times longer than any yet observed.
#
# Transcript completeness is NEVER traded away to make room for structure
# (FR-008/FR-011): where both cannot fit, the response is truncated and rejected
# as such. T036b's `truncated` counts, broken out by content type, are what let
# this be resized on honest evidence after a month of real truncation data.
VIDEO_MAX_TOKENS = int(os.environ.get("ANALYZE_VIDEO_MAX_TOKENS", "12000"))
IMAGE_MAX_TOKENS = int(os.environ.get("ANALYZE_IMAGE_MAX_TOKENS", "5000"))

# ---------------------------------------------------------------------------
# Versioned extraction config (feature 007)
# ---------------------------------------------------------------------------
# The schema and the vocabulary are read from config/extraction/, mounted
# read-only into this container and into the Prefect service. ONE declaration
# serving three consumers: this service's `response_format`, this service's
# client-side validation, and Prefect's storage-boundary re-validation. Declared
# three times they would drift silently (research.md R7).
_HERE = Path(__file__).resolve().parent
_CONFIG_CANDIDATES = (
    # Container: this file is /app/analyze.py, config mounted at /app/config/.
    _HERE / "config" / "extraction",
    # Host: this file is <repo>/service/roach/analyze.py, so parents[1] is <repo>.
    # A SLICE, not an index — in the container `_HERE` is `/app` and `parents` has
    # a single element, so `parents[1]` raises IndexError at import time and takes
    # the whole service down on startup. The slice yields () instead. Same guard,
    # same reason, as tasks/availability_tasks.py.
    *(p / "config" / "extraction" for p in _HERE.parents[1:2]),
)


def _config_dir() -> Path:
    explicit = os.environ.get("EXTRACTION_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    return next((c for c in _CONFIG_CANDIDATES if c.exists()), _CONFIG_CANDIDATES[0])


# Bumped on ANY edit to the prompt text, including a whitespace-only one. A
# prompt change that is not recorded makes FR-028 false and silently mixes two
# populations in every distribution computed afterwards.
PROMPT_VERSION = os.environ.get("EXTRACTION_PROMPT_VERSION", "v1")
SCHEMA_VERSION = os.environ.get("EXTRACTION_SCHEMA_VERSION", "v1")


def load_vocabulary(config_dir: Path | None = None) -> dict:
    """Read the versioned vocabulary the prompt and the validator both use.

    Returns {version, beat_functions: {term: description}, residual,
    attribute_terms: {dimension: {term: description}}}.

    Roach reads the FILE, not the table — it is stateless by design and holds no
    database connection. The file is the write path both sides derive from, so
    this cannot disagree with what `extraction-vocabulary-sync` put in the table
    unless the mount is stale.
    """
    path = (config_dir or _config_dir()) / "vocabulary_v1.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = str(doc.get("version") or "").strip()
    beat_functions: dict[str, str] = {}
    attribute_terms: dict[str, dict[str, str]] = {}
    residual = None
    for block in doc.get("dimensions") or []:
        dimension = str(block.get("dimension") or "").strip()
        terms = {
            str(t["term"]): str(t.get("description") or "")
            for t in (block.get("terms") or [])
        }
        if dimension == "beat_function":
            beat_functions = terms
            residual = next(
                (str(t["term"]) for t in (block.get("terms") or []) if t.get("is_residual")), None)
        else:
            attribute_terms[dimension] = terms
    return {
        "version": version,
        "beat_functions": beat_functions,
        "residual": residual,
        "attribute_terms": attribute_terms,
    }


def load_schema(config_dir: Path | None = None, vocabulary: dict | None = None) -> dict:
    """Read the JSON schema, with the beat-function enum GENERATED from the vocabulary.

    The enum in the file is a placeholder. Substituting it here is what stops the
    schema and the vocabulary becoming two sources of truth for one closed list —
    the failure mode being that a term added to the vocabulary is silently
    rejected by a stale enum, or worse, the reverse.
    """
    path = (config_dir or _config_dir()) / "schema_v1.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    vocab = vocabulary or load_vocabulary(config_dir)
    terms = sorted(vocab["beat_functions"])
    if terms:
        schema["properties"]["beats"]["items"]["properties"]["function"]["enum"] = terms
    # `$comment` is legal JSON Schema but some providers reject unknown keys in
    # strict mode; it exists for the reader of the file, not for the wire.
    schema.pop("$comment", None)
    return schema

# Video compression before analysis (ffmpeg, already in the image). Shrinks the
# base64 payload ~5-10x with no meaningful accuracy loss — audio is kept intact
# for the transcript. All env-tunable; dial height/CRF up if a spot-check regresses.
VIDEO_MAX_HEIGHT = int(os.environ.get("ANALYZE_VIDEO_MAX_HEIGHT", "480"))
VIDEO_FPS = int(os.environ.get("ANALYZE_VIDEO_FPS", "15"))
VIDEO_CRF = int(os.environ.get("ANALYZE_VIDEO_CRF", "30"))
VIDEO_MAX_SECONDS = int(os.environ.get("ANALYZE_VIDEO_MAX_SECONDS", "0"))  # 0 = no trim
# Cap images sent for a carousel and downscale each — big saver on multi-image posts.
MAX_IMAGES = int(os.environ.get("ANALYZE_MAX_IMAGES", "8"))
IMAGE_MAX_DIM = int(os.environ.get("ANALYZE_IMAGE_MAX_DIM", "1024"))

def _beat_vocabulary_block(vocab: dict) -> str:
    """Render the vocabulary into the prompt, descriptions and all.

    Built FROM the vocabulary rather than restated in prose, so the prompt and
    the validator cannot disagree about what a term means. If they could, every
    disagreement would surface as a retry the model had no way to satisfy.
    """
    lines = [f'  - "{term}": {desc}' for term, desc in vocab["beat_functions"].items()]
    return "\n".join(lines)


def _prompt_for(media: str, vocab: dict) -> str:
    """Build the extraction prompt for one media class.

    Both paths request the IDENTICAL key set (FR-023). The only difference is the
    guidance on `subtitle`, because an image post has nothing to transcribe —
    a fact about the media, not a difference in the contract. The old prompts
    asked for different keys entirely (["subtitle","flow","summary"] vs
    ["flow","summary"]) and back-filled subtitle client-side, which is what made
    the two paths' output non-comparable in the first place.
    """
    residual = vocab["residual"] or "unclassified"

    if media == "video":
        opening = ("You are given a short-form social media video (TikTok/Instagram Reel), "
                   "including its visuals and audio.")
        subtitle_rule = (
            '- "subtitle": the full VERBATIM transcript of every spoken word, in the original '
            'spoken language. Also transcribe any on-screen text (captions, overlays) that is not '
            'spoken, prefixed with [on-screen]. Use null if there is no speech and no on-screen '
            'text at all.\n'
            '- "subtitle_absence": null when you produced a transcript. When "subtitle" is null, '
            'set this to "not_applicable_no_audio" if the video genuinely carries no speech and no '
            'on-screen text, or "attempted_none_found" if you tried to transcribe and could not '
            'make anything out.'
        )
    else:
        opening = ("You are given one or more images from a social media post (image post, "
                   "carousel, or story). There is no audio.")
        subtitle_rule = (
            '- "subtitle": transcribe any on-screen text visible in the images, prefixed with '
            '[on-screen]. Use null if the images carry no text at all.\n'
            '- "subtitle_absence": null when you produced a transcript. When "subtitle" is null, '
            'set this to "not_applicable_no_audio" (these images carry no text to transcribe) or '
            '"attempted_none_found" if text is present but illegible.'
        )

    return f"""{opening}

Respond with ONLY a JSON object (no markdown fences, no extra text) with exactly these keys:
{subtitle_rule}
- "beats": an ordered array describing how the content is structured. Each beat is an object with:
    "position"    — 1, 2, 3, ... contiguous, starting at 1, in the order the beats occur.
    "function"    — EXACTLY one of the terms listed below. Nothing else.
    "description" — what actually happens in this beat.
- "attributes": an array of derived attributes. Leave it EMPTY ([]) — no attribute dimensions are
  published at this vocabulary version.
- "summary": a concise 2-4 sentence summary of what the content is about and its key message.

The only permitted values for "function" are:
{_beat_vocabulary_block(vocab)}

Three rules that will otherwise cause your response to be rejected:
1. Every beat's "function" MUST be one of the terms listed above. If none of them fits, use
   "{residual}" — do NOT invent a new term, and do not use a synonym.
2. Emit AT LEAST ONE beat. Content with no discernible structure is a single beat with function
   "{residual}" covering the whole item — never an empty array.
3. The transcript is verbatim and COMPLETE. Do not summarise, trim, abbreviate or paraphrase it to
   save room, and do not stop early. Length is not a problem; an incomplete transcript is.

Base every field only on what is actually present in the media. Do not invent, guess, or add content
that is not there. Do not comment on how the content performed or how many people saw it.
"""


MAX_ATTEMPTS = 4
# Back-off (seconds) for OpenRouter 429s. Kept bounded so the whole analyze
# call stays well under the caller's HTTP timeout (honors Retry-After too).
RATE_LIMIT_BACKOFF = [10, 20, 40]

# A bug inside roach is not an analysis failure, and must not be reported as
# one. `analyze_item`'s catch-all turns every exception into
# `{status: "failed"}` — a well-formed result the harvest flow records against
# the item and moves on from. A TypeError from a signature drift therefore
# looks exactly like the model declining to answer: the item is marked
# `analysis_status = 'failed'`, the run stays green, and nothing surfaces. The
# 18 failed items in the audit window cannot be told apart from roach bugs for
# this reason. These propagate instead, so they reach FastAPI as an unhandled
# 500 with a traceback in the roach log.
#
# Same tuple, same rationale as api.py's guard on the /list and /download paths
# (imported there from here, so there is one definition).
INTERNAL_BUG_ERRORS = (TypeError, AttributeError, NameError, ImportError)


def _response_format(schema: dict) -> dict:
    """OpenRouter/OpenAI json_schema response_format, from the versioned schema file.

    `strict: True` is provider-side enforcement and is kept — but it is NOT the
    check this feature relies on. Only providers that implement it honour it, and
    routing here runs with allow_fallbacks across four of them. FR-016 requires
    the system validate the response itself, which `extraction_models` does on
    every path with no exceptions.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "extraction",
            "strict": True,
            "schema": schema,
        },
    }


def _build_payload(prompt: str, content_parts: list[dict], model: str, schema: dict,
                   max_tokens: int, history: list[dict] | None = None) -> dict:
    """Assemble one OpenRouter request.

    `history` carries the retry turns (FR-018): the invalid response and the
    validator's own error text, appended after the original user message. The
    MEDIA IS NOT RE-SENT — it is already in that first message, and re-uploading
    base64 video to restate a JSON complaint would roughly double the input cost
    of every retry.
    """
    messages: list[dict] = [
        {"role": "user", "content": [{"type": "text", "text": prompt}, *content_parts]}
    ]
    messages.extend(history or [])
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        # Suppress internal reasoning so a reasoning model doesn't exhaust the
        # token budget before writing the answer (the empty-content failure).
        "reasoning": {"enabled": False},
        "response_format": _response_format(schema),
        # Usage accounting: opting in adds the resolved USD `cost` to the usage
        # object alongside the token counts, so spend is read off the response
        # rather than estimated from a price list.
        "usage": {"include": True},
        "messages": messages,
    }
    if PROVIDER_ORDER:
        payload["provider"] = {"order": PROVIDER_ORDER, "allow_fallbacks": ALLOW_FALLBACKS}
    return payload


@dataclass
class ModelCall:
    """One model response, with everything needed to attribute and cost it.

    Before this existed, `_call_model` returned only the parsed JSON body and
    threw the rest of the envelope away — including the usage object it had just
    finished printing to stdout. That is why no cost baseline exists anywhere in
    this system (research.md R3): the numbers were observed, logged, and
    discarded in the same breath, so nothing could ever be summed.

    `model_served` and `provider` are read off the RESPONSE, never assumed from
    the request. Provider routing runs with allow_fallbacks: true, so "what was
    asked for" and "what answered" are genuinely different questions (FR-024).
    """
    parsed: dict | None
    raw_text: str
    usage: dict
    model_requested: str
    model_served: str
    provider: str | None
    finish_reason: str | None
    # 1, or 2 when the error-fed-back retry succeeded (FR-018). Stored so a
    # rising retry rate is visible as its own signal rather than hidden inside a
    # success count.
    attempts: int = 1
    # The first attempt's validation errors, kept even on eventual success — a
    # response that only validated on the retry is worth being able to find.
    attempt_errors: list[dict] = field(default_factory=list)


def _usage_from(body: dict) -> dict:
    """Normalise OpenRouter's usage object into the three fields we store.

    `cost` is the provider's own resolved USD figure, present because
    `_build_payload` opts in with `usage: {include: true}`. It is a MEASUREMENT
    and is stored as one — never reconstructed from a price list, which would put
    an estimate in the same column as an observation (Constitution VI, FR-038).
    """
    usage = body.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost_usd": usage.get("cost"),
    }


def _log_usage(body: dict, call_site: str, model: str, client: str | None) -> None:
    """Record the token usage OpenRouter returns on every call.

    Kept as a log line as well as a returned value: the log is what makes a
    single run diagnosable in the roach container, the returned value is what
    makes spend summable across runs. Best-effort — never let accounting break
    an analysis.
    """
    try:
        usage = body.get("usage") or {}
        if not usage:
            return
        cost = usage.get("cost")
        print(
            f"[openrouter] usage call_site={call_site} model={body.get('model') or model} "
            f"client={client or '-'} prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} "
            f"total_tokens={usage.get('total_tokens')}"
            + (f" cost_usd={cost}" if cost is not None else ""),
            flush=True,
        )
    except Exception:
        pass


def _extract_json(content: str) -> dict:
    """Parse a JSON object from model output. STRICT — no salvage.

    Fence-stripping is retained: a ```json wrapper is a formatting artifact of a
    complete response, not evidence of damage.

    The regex fallback that used to live here is DELETED, not disabled. It
    searched the raw text for the first balanced {...} block, which is precisely
    the mechanism that made a response truncated at max_tokens parse successfully
    with content silently missing (FR-017). A response that does not parse
    cleanly is invalid and gets the retry; if the retry fails too it is
    quarantined with its raw text intact, which is strictly more useful than a
    salvaged fragment nobody can tell apart from a complete answer.

    Do not reintroduce it. tests/test_analyze_validation.py greps this module to
    assert it is absent, because a test that merely stops calling it would leave
    SC-006 unverifiable the moment someone adds the path back.
    """
    text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def _call_model(
    prompt: str, content_parts: list[dict], model: str, schema: dict, max_tokens: int,
    call_site: str = "analyze", client: str | None = None,
    history: list[dict] | None = None,
) -> ModelCall:
    """Call the model and return the FULL result, not just the parsed body.

    Returns a ModelCall carrying the parsed JSON, the raw text, the measured
    usage, the model that actually served the request, and `finish_reason`.
    Every one of those is needed downstream and every one of them used to be
    discarded here.
    """
    api_key = os.environ["OPENROUTER_API_KEY"]

    last_error: Exception = RuntimeError("unreachable")
    # The last response that arrived with no content. Kept so an all-empty run
    # can be classified as `empty_content` AND carry its measured cost, rather
    # than dying as a bare RuntimeError the caller files under `provider_error`.
    last_empty: ModelCall | None = None
    for attempt in range(MAX_ATTEMPTS):
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": APP_TITLE,
                "HTTP-Referer": APP_REFERER,
            },
            json=_build_payload(prompt, content_parts, model, schema, max_tokens, history),
            timeout=180,
        )
        # OpenRouter rate limits (429) are transient — honor Retry-After if
        # given, otherwise use an escalating back-off, then retry.
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = int(retry_after)
            else:
                delay = RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)]
            last_error = RuntimeError(f"429 rate limited on attempt {attempt + 1}; backing off {delay}s")
            time.sleep(delay)
            continue
        if resp.status_code >= 400:
            # Other 4xx/5xx (e.g. provider 422/502) won't improve on retry —
            # surface the provider's error body so the failure is diagnosable.
            raise RuntimeError(f"OpenRouter {model} HTTP {resp.status_code}: {resp.text[:400]}")
        body = resp.json()
        _log_usage(body, call_site, model, client)
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        call = ModelCall(
            parsed=None,
            raw_text=content or "",
            usage=_usage_from(body),
            model_requested=model,
            # Read off the response body. `body["model"]` is what OpenRouter
            # actually routed to; falling back to the request only when the
            # provider omits it, which would otherwise silently assert that the
            # requested model served the call (FR-024).
            model_served=body.get("model") or model,
            provider=body.get("provider"),
            finish_reason=choice.get("finish_reason"),
        )
        # TRUNCATION IS CHECKED BEFORE PARSING, and this ordering is the single
        # most important line in the feature.
        #
        # A beat array cut off after three complete beats is VALID JSON THAT
        # SATISFIES THE SCHEMA. It describes a story that stops half way and
        # there is nothing structurally wrong with it — no parser, no schema
        # validator, and no amount of provider-side `strict` can tell it from a
        # complete answer. Only finish_reason can. Parse first and the fragment
        # gets its chance to look valid (FR-017).
        if call.finish_reason == "length":
            invalid = ExtractionInvalid(
                "truncated",
                [f"the response was cut off at the token limit (max_tokens={max_tokens}); "
                 f"it is incomplete regardless of whether the fragment parses"],
                raw=content or "",
            )
            # ATTACH THE CALL, so its measured usage survives the exception.
            #
            # A truncated response is the MOST expensive kind of failure — it ran
            # to the token ceiling, so it burned the full completion budget. Losing
            # its cost here would bias the measured baseline low in exactly the
            # direction that matters, and make a pathological item that truncates
            # on every attempt look free (data-model.md, extraction_quarantine).
            # Caught in production: the first truncation this feature detected was
            # stored with a NULL cost_usd because of this omission.
            invalid.call = call
            raise invalid
        if not content:
            # Reasoning disabled above, but guard the empty-content case anyway.
            last_error = RuntimeError(f"empty content on attempt {attempt + 1}")
            last_empty = call
            continue
        try:
            call.parsed = _extract_json(content)
            return call
        except json.JSONDecodeError as e:
            # Strict parse only — the salvage regex is gone. A response that does
            # not parse is invalid and goes to the retry, which at least tells the
            # model what was wrong.
            invalid = ExtractionInvalid(
                "schema_invalid",
                [f"the response is not valid JSON: {e}"],
                raw=content,
            )
            invalid.call = call  # tokens were spent; the cost must survive (FR-038)
            raise invalid from e

    # Exhausted MAX_ATTEMPTS. If every attempt came back with no content at all,
    # that is `empty_content` — its OWN value in the closed vocabulary (FR-021),
    # not a provider error.
    #
    # Collapsing it into `provider_error` is exactly the generic-bucket failure
    # FR-021 forbids, and it misleads in a specific way: a provider error suggests
    # the provider is unhealthy and the item should be retried later, whereas
    # repeated empty content is a property of THIS item and this model. Measured
    # on the first full backfill: 13 of 824 items (1.6%) return empty content on
    # every attempt, and every one of them was filed as a provider fault.
    if last_empty is not None:
        invalid = ExtractionInvalid(
            "empty_content",
            [f"the model returned no content on any of {MAX_ATTEMPTS} attempts"],
            raw="",
        )
        # Empty content is NOT free — the prompt and media were uploaded and
        # billed on every one of those attempts (FR-038).
        invalid.call = last_empty
        raise invalid
    raise last_error


def _compress_video(src: Path) -> tuple[Path, bool]:
    """Downscale/re-encode a video with ffmpeg for a smaller analysis payload.

    Keeps audio intact (AAC 64k) so the transcript is unaffected. Returns
    (path, is_temp): on any ffmpeg failure it returns the original path so
    analysis never fails over compression.
    """
    if not shutil.which("ffmpeg"):
        return src, False
    fd, tmp_name = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    tmp = Path(tmp_name)
    vf = f"scale=-2:'min({VIDEO_MAX_HEIGHT},ih)',fps={VIDEO_FPS}"
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if VIDEO_MAX_SECONDS > 0:
        cmd += ["-t", str(VIDEO_MAX_SECONDS)]
    cmd += [
        "-vf", vf,
        "-c:v", "libx264", "-crf", str(VIDEO_CRF), "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            return tmp, True
    except (subprocess.SubprocessError, OSError):
        pass
    tmp.unlink(missing_ok=True)
    return src, False


def _compress_image(src: Path) -> tuple[Path, bool]:
    """Downscale an image with ffmpeg to IMAGE_MAX_DIM on its longest side."""
    if not shutil.which("ffmpeg"):
        return src, False
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp = Path(tmp_name)
    # Scale down only if larger than the cap (never upscale).
    vf = f"scale='min({IMAGE_MAX_DIM},iw)':'min({IMAGE_MAX_DIM},ih)':force_original_aspect_ratio=decrease"
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-q:v", "3", str(tmp)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            return tmp, True
    except (subprocess.SubprocessError, OSError):
        pass
    tmp.unlink(missing_ok=True)
    return src, False


def _validated_call(
    prompt: str, parts: list[dict], model: str, max_tokens: int,
    call_site: str, client: str | None, vocab: dict, schema: dict,
) -> tuple[ModelCall, object]:
    """One model call, validated, with EXACTLY ONE error-fed-back retry (FR-018).

    Not two, and not a configurable count. A second correction attempt on a model
    that has already ignored a quoted error is spend without evidence it helps,
    and the quarantine row is more useful than a third roll.

    The retry is a CORRECTION, not a re-roll: it appends the invalid output and
    the validator's own error text as follow-up turns. The previous code re-sent
    the identical payload and hoped for different dice.
    """
    attempts: list[dict] = []
    history: list[dict] = []
    last_call: ModelCall | None = None

    for attempt in (1, 2):
        try:
            call = _call_model(
                prompt, parts, model, schema, max_tokens,
                call_site=call_site, client=client, history=history or None,
            )
            last_call = call
            result = validate_extraction(
                call.parsed,
                beat_functions=vocab["beat_functions"],
                attribute_terms={d: set(t) for d, t in vocab["attribute_terms"].items()},
            )
            call.attempts = attempt
            call.attempt_errors = attempts
            return call, result
        except ExtractionInvalid as invalid:
            attempts.append({
                "attempt": attempt,
                "failure_kind": invalid.failure_kind,
                "errors": invalid.errors,
            })
            # `_call_model` attaches the ModelCall when it raises (truncation,
            # unparseable JSON) — those paths never return, so `last_call` is
            # still None from THIS attempt and would otherwise erase the usage
            # that came with the failure.
            if invalid.call is not None:
                last_call = invalid.call
            if last_call is not None:
                invalid.raw = invalid.raw or last_call.raw_text
            if attempt == 2:
                invalid.attempts = attempts
                # Prefer the call attached to this failure; fall back to the last
                # one seen. Either way the quarantine row carries measured cost.
                invalid.call = invalid.call or last_call
                raise
            # The model's own words back to it, then the validator's own error
            # text QUOTED — not paraphrased. A paraphrase would be a second
            # place the rules are stated, and it would drift from the validator.
            history.append({"role": "assistant", "content": invalid.raw or "(no content)"})
            history.append({"role": "user", "content": (
                "That response was rejected by schema validation:\n"
                + "\n".join(f"- {e}" for e in invalid.errors)
                + "\n\nReturn corrected JSON only, with the same keys. "
                  "Do not apologise or explain — return the JSON object."
            )})

    raise AssertionError("unreachable")  # pragma: no cover


def _rotated_call(models: model_rotation.Rotation, model_override: str | None, prompt: str, parts: list[dict],
                  max_tokens: int, call_site: str, client: str | None, vocab: dict, schema: dict):
    """`_validated_call` on the case's current model, reporting the outcome to its rotation.

    A named model (`model_override`, the calibration comparison) bypasses rotation:
    it must get exactly the model it asked for, and its failures say nothing about
    the harvest's routing. Only the model call is counted, so a broken media file
    (which fails before this) never rotates a healthy model away.
    """
    model = model_override or models.current()
    try:
        out = _validated_call(prompt, parts, model, max_tokens, call_site, client, vocab, schema)
    except INTERNAL_BUG_ERRORS:
        raise
    except Exception as e:
        if not model_override:
            models.failure(model)
        e.model_requested = model  # for provenance: the rotation may have moved on already
        raise
    if not model_override:
        models.success(model)
    return out


def analyze_video(video_path: Path, client: str | None = None, vocab: dict | None = None,
                  schema: dict | None = None, model_override: str | None = None):
    vocab = vocab or load_vocabulary()
    schema = schema or load_schema(vocabulary=vocab)
    clip, is_temp = _compress_video(video_path)
    try:
        video_b64 = base64.b64encode(clip.read_bytes()).decode()
        video_data_url = f"data:video/mp4;base64,{video_b64}"
        return _rotated_call(
            VIDEO_MODELS, model_override, _prompt_for("video", vocab),
            [{"type": "video_url", "video_url": {"url": video_data_url}}],
            VIDEO_MAX_TOKENS, "analyze.video", client, vocab, schema,
        )
    finally:
        if is_temp:
            clip.unlink(missing_ok=True)


def analyze_images(image_paths: list[Path], client: str | None = None, vocab: dict | None = None,
                   schema: dict | None = None, model_override: str | None = None):
    vocab = vocab or load_vocabulary()
    schema = schema or load_schema(vocabulary=vocab)
    parts = []
    temps: list[Path] = []
    try:
        for path in image_paths[:MAX_IMAGES]:
            small, is_temp = _compress_image(path)
            if is_temp:
                temps.append(small)
            mime_type = "image/jpeg" if is_temp else (mimetypes.guess_type(str(small))[0] or "image/jpeg")
            image_b64 = base64.b64encode(small.read_bytes()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}})
        # NOTE: no client-side `setdefault("subtitle", "")` any more. The image
        # path used to back-fill an empty string here, which is exactly what made
        # "this post has no audio" indistinguishable from "transcription failed"
        # and from "extraction never ran" (FR-010). The prompt now asks for an
        # explicit `subtitle_absence` and the validator requires one.
        return _rotated_call(
            IMAGE_MODELS, model_override, _prompt_for("image", vocab), parts,
            IMAGE_MAX_TOKENS, "analyze.images", client, vocab, schema,
        )
    finally:
        for t in temps:
            t.unlink(missing_ok=True)


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}


def analyze_item(local_paths: list[str], content_type: str, client: str | None = None,
                 model_override: str | None = None) -> dict:
    """Analyze one downloaded item, branching by the *actual* files on disk.

    The branch is decided by file extension, not just the content_type label:
    sending an .mp4 to the image model (which happens if a Reel is mislabeled)
    produces a provider error, so any video file always goes to analyze_video.

    `client` is an attribution label only (roach is stateless); it is echoed
    into the token-usage log so spend can be split per client.

    Returns one of THREE shapes, all of them non-raising:

      status="success"     — validated. Carries subtitle/subtitle_absence,
                             beats[], attributes[], summary, plus `flow` as a
                             readable rendering for existing consumers.
      status="quarantined" — the model failed validation twice (FR-019). Carries
                             the raw output and BOTH attempts' errors. Nothing is
                             stored as an extraction.
      status="failed"      — the model, the provider, or the media failed before
                             any validatable response existed.

    All three are `200 OK` at the API layer. A quarantine is NOT an HTTP error:
    the `{ok: false, code}` envelope means *platform* failure and the harvest
    flow branches on it — 429 backs off and rotates egress, 404 skips the
    profile. A model writing bad JSON would make a healthy profile look blocked.

    It does NOT cover roach's own bugs: INTERNAL_BUG_ERRORS propagate, because a
    caller that cannot tell a typo from a model refusal will record the typo as a
    fact about the content.

    `model_override` forces a specific model regardless of media routing. It
    exists for ONE caller — the controlled agreement comparison, which must send
    IDENTICAL input to two named models (FR-027). Without it the comparison
    silently measures nothing: routing picks the model from the media type, so
    both "sides" of every pair resolve to the same model and the result is a
    100% agreement that describes the router rather than the models. Measured:
    a 40-item calibration produced 39 pairs where both halves were served by
    `gemini-2.5-flash-lite`, and only the `model_served` mismatch check stopped
    it being reported as a finding.

    Normal harvest and backfill calls leave it None and keep media-based routing.
    """
    paths = [Path(p) for p in local_paths]
    videos = [p for p in paths if p.suffix.lower() in VIDEO_EXTS]
    # Only real images — exclude audio soundtracks (TikTok photo posts) and any
    # other non-image files, which the image model can't accept.
    images = [p for p in paths if p.suffix.lower() in IMAGE_EXTS]
    # The path ACTUALLY taken, decided by the files on disk. Recorded rather than
    # inferred from `content_type`: a mixed carousel routes to the image path
    # regardless of its label, and 6 rows in the live corpus are carousels
    # holding real transcripts because of exactly that (FR-024, research.md R1).
    media_path = "image"
    vocab = load_vocabulary()
    schema = load_schema(vocabulary=vocab)

    def _provenance(call=None, error=None) -> dict:
        return {
            "model_requested": getattr(call, "model_requested", None) or getattr(error, "model_requested", None)
            or model_override or (VIDEO_MODELS if media_path == "video" else IMAGE_MODELS).current(),
            "model_served": getattr(call, "model_served", None),
            "provider": getattr(call, "provider", None),
            "media_path": media_path,
            "finish_reason": getattr(call, "finish_reason", None),
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "vocabulary_version": vocab["version"],
            "attempts": getattr(call, "attempts", 1),
        }

    try:
        if videos and not images:
            # Single video or all-video carousel — analyze the (first) video.
            media_path = "video"
            call, result = analyze_video(videos[0], client, vocab, schema, model_override)
        elif images and not videos:
            call, result = analyze_images(images, client, vocab, schema, model_override)
        elif videos and images:
            # Mixed carousel: analyze the image(s); the video model only takes one.
            call, result = analyze_images(images, client, vocab, schema, model_override)
        elif content_type == "video":
            media_path = "video"
            call, result = analyze_video(paths[0], client, vocab, schema, model_override)
        else:
            call, result = analyze_images(paths, client, vocab, schema, model_override)

        beats = [b.model_dump() for b in result.beats]
        return {
            "status": "success",
            "error": None,
            "subtitle": result.subtitle,
            "subtitle_absence": result.subtitle_absence,
            "beats": beats,
            "attributes": [a.model_dump() for a in result.attributes],
            "summary": result.summary,
            # A readable rendering of the beats, for consumers that still expect
            # prose (the reviewer sheet's content_flow column, FR-041). Derived
            # deterministically from the structured data — no second model call,
            # and it cannot disagree with the beats it was rendered from.
            "flow": render_beats(beats),
            # Measured, from the provider's own reported usage (FR-038). This is
            # the row that makes a cost baseline possible at all.
            "usage": call.usage,
            "provenance": _provenance(call),
            "retry_errors": call.attempt_errors,
        }
    except ExtractionInvalid as invalid:
        # Twice-invalid. Retained in full and NEVER stored as an extraction.
        call = invalid.call
        return {
            "status": "quarantined",
            "error": str(invalid),
            "failure_kind": invalid.failure_kind,
            "raw_output": invalid.raw or getattr(call, "raw_text", "") or None,
            "attempts": invalid.attempts,
            "subtitle": None, "subtitle_absence": None,
            "beats": [], "attributes": [], "summary": None, "flow": "",
            # Tokens were spent whether or not the output was usable. Omitting
            # this would bias the measured baseline low and make a pathological
            # item that quarantines every time look free.
            "usage": getattr(call, "usage", None),
            "provenance": _provenance(call, invalid),
        }
    except INTERNAL_BUG_ERRORS:
        raise
    except Exception as e:
        return {
            "status": "failed", "error": str(e),
            "failure_kind": "provider_error",
            "subtitle": None, "subtitle_absence": None,
            "beats": [], "attributes": [], "summary": None, "flow": "",
            # A failed call may still have burned tokens, but this path cannot
            # see them: the exception escaped before a ModelCall was built.
            # Reported as absent rather than as zero — a zero here would be an
            # assertion that nothing was spent (Constitution VI).
            "usage": None,
            "provenance": _provenance(None, e),
        }


def render_beats(beats: list[dict]) -> str:
    """Beats -> readable numbered text, for consumers that expect prose.

    Deterministic and model-free. FR-041 requires the reviewer-facing sheet keep
    receiving a readable content flow, and Constitution XI requires anything a
    rule can produce not be produced by a model call.

    Defined in roach as well as in the Prefect task because both sides need it —
    roach to fill `flow` on the response for existing consumers, Prefect to fill
    the sheet from stored beats during a backfill, where no roach response is in
    hand.
    """
    return "\n".join(
        f"{b['position']}. [{b['function']}] {b['description']}"
        for b in sorted(beats, key=lambda b: b["position"])
    )


def build_report(data_dir: Path, video_paths: list[Path] | None = None) -> Path:
    if video_paths is None:
        video_paths = sorted(data_dir.glob("*.mp4"))
    lines = ["# Content Analysis Report", ""]

    for video_path in video_paths:
        video_id = video_path.stem
        print(f"Analyzing {video_id}...")
        try:
            _call, result = analyze_video(video_path)
        except Exception as e:
            lines += [f"## {video_id}", "", f"**Error:** {e}", ""]
            continue

        lines += [
            f"## {video_id}",
            "",
            "### Subtitle", "",
            result.subtitle or f"_(absent: {result.subtitle_absence})_", "",
            "### Content Flow", "",
            render_beats([b.model_dump() for b in result.beats]), "",
            "### Summary", "",
            result.summary, "",
        ]

    report_path = data_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
