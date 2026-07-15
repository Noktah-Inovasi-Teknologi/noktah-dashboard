"""Per-item content analysis via OpenRouter (xiaomi/mimo-v2.5).

Video items send the full video (visuals + audio) in one call and get a subtitle
transcript, content-flow breakdown, and summary. Image/carousel items (no audio)
send the image(s) instead and get an empty subtitle, per spec Assumptions and
research.md R9 — non-applicable analysis fields are left empty, not errored.
"""
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Video analysis needs an audio+video-capable model (default mimo). Image/carousel
# analysis can use a different vision model — set OPENROUTER_IMAGE_MODEL if the
# video model's providers don't reliably accept image input on your account.
MODEL = os.environ.get("OPENROUTER_MODEL", "xiaomi/mimo-v2.5")
IMAGE_MODEL = os.environ.get("OPENROUTER_IMAGE_MODEL", MODEL)

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

# Token budgets. Video needs headroom for a full transcript; images don't.
VIDEO_MAX_TOKENS = int(os.environ.get("ANALYZE_VIDEO_MAX_TOKENS", "8000"))
IMAGE_MAX_TOKENS = int(os.environ.get("ANALYZE_IMAGE_MAX_TOKENS", "3000"))

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

VIDEO_PROMPT = """You are given a short-form social media video (TikTok/Instagram Reel), including its visuals and audio.

Respond with ONLY a JSON object (no markdown fences, no extra text) with exactly these keys:
- "subtitle": full verbatim transcript of every spoken word, in the original spoken language. Also transcribe any \
on-screen text (captions, overlays) that is not spoken, prefixed with [on-screen]. If there is no speech and no \
on-screen text, use an empty string.
- "flow": a short numbered breakdown of how the content is structured, e.g. hook, setup, main point, call to action \
(use whatever stages actually appear in this video). Account for on-screen text, visual cuts, and demonstrations, \
not just what is spoken.
- "summary": a concise 2-4 sentence summary of what the video is about and its key message.

Base every field only on what is actually present in the video. Do not invent, guess, or add content that is not there.
"""

IMAGE_PROMPT = """You are given one or more images from a social media post (image post, carousel, or story). \
There is no audio.

Respond with ONLY a JSON object (no markdown fences, no extra text) with exactly these keys:
- "flow": a short numbered breakdown of how the content is structured (e.g. what each image shows in sequence, \
any on-screen text, the overall narrative or message progression).
- "summary": a concise 2-4 sentence summary of what the post is about and its key message.

Base every field only on what is actually shown in the images. Do not invent, guess, or add content that is not there.
"""


MAX_ATTEMPTS = 4
# Back-off (seconds) for OpenRouter 429s. Kept bounded so the whole analyze
# call stays well under the caller's HTTP timeout (honors Retry-After too).
RATE_LIMIT_BACKOFF = [10, 20, 40]


def _response_format(keys: list[str]) -> dict:
    """OpenRouter/OpenAI json_schema response_format for the required keys.

    Compliant providers return guaranteed-valid JSON with exactly these keys,
    eliminating fence-stripping and parse failures.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "analysis",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in keys},
                "required": keys,
                "additionalProperties": False,
            },
        },
    }


def _build_payload(prompt: str, content_parts: list[dict], model: str, keys: list[str], max_tokens: int) -> dict:
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        # Suppress internal reasoning so a reasoning model doesn't exhaust the
        # token budget before writing the answer (the empty-content failure).
        "reasoning": {"enabled": False},
        "response_format": _response_format(keys),
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, *content_parts]}],
    }
    if PROVIDER_ORDER:
        payload["provider"] = {"order": PROVIDER_ORDER, "allow_fallbacks": ALLOW_FALLBACKS}
    return payload


def _extract_json(content: str) -> dict:
    """Parse a JSON object from model output, tolerating fences and stray prose."""
    text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} block anywhere in the text.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise json.JSONDecodeError("no JSON object found", text, 0)


def _call_model(prompt: str, content_parts: list[dict], model: str, keys: list[str], max_tokens: int) -> dict:
    api_key = os.environ["OPENROUTER_API_KEY"]

    last_error: Exception = RuntimeError("unreachable")
    for attempt in range(MAX_ATTEMPTS):
        resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=_build_payload(prompt, content_parts, model, keys, max_tokens),
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
        content = resp.json()["choices"][0]["message"]["content"]
        if not content:
            # Reasoning disabled above, but guard the empty-content case anyway.
            last_error = RuntimeError(f"empty content on attempt {attempt + 1}")
            continue
        try:
            return _extract_json(content)
        except json.JSONDecodeError as e:
            last_error = e
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


def analyze_video(video_path: Path) -> dict:
    clip, is_temp = _compress_video(video_path)
    try:
        video_b64 = base64.b64encode(clip.read_bytes()).decode()
        video_data_url = f"data:video/mp4;base64,{video_b64}"
        return _call_model(
            VIDEO_PROMPT,
            [{"type": "video_url", "video_url": {"url": video_data_url}}],
            MODEL,
            keys=["subtitle", "flow", "summary"],
            max_tokens=VIDEO_MAX_TOKENS,
        )
    finally:
        if is_temp:
            clip.unlink(missing_ok=True)


def analyze_images(image_paths: list[Path]) -> dict:
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
        result = _call_model(IMAGE_PROMPT, parts, IMAGE_MODEL, keys=["flow", "summary"], max_tokens=IMAGE_MAX_TOKENS)
        result.setdefault("subtitle", "")
        return result
    finally:
        for t in temps:
            t.unlink(missing_ok=True)


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}


def analyze_item(local_paths: list[str], content_type: str) -> dict:
    """Analyze one downloaded item, branching by the *actual* files on disk.

    The branch is decided by file extension, not just the content_type label:
    sending an .mp4 to the image model (which happens if a Reel is mislabeled)
    produces a provider error, so any video file always goes to analyze_video.

    Returns {subtitle, flow, summary, status, error}. On model failure the
    download is NOT touched — this returns status="failed" with the error
    detail instead of raising, so callers can still record the item (edge case:
    analysis fails but the content is retained).
    """
    paths = [Path(p) for p in local_paths]
    videos = [p for p in paths if p.suffix.lower() in VIDEO_EXTS]
    # Only real images — exclude audio soundtracks (TikTok photo posts) and any
    # other non-image files, which the image model can't accept.
    images = [p for p in paths if p.suffix.lower() in IMAGE_EXTS]
    try:
        if videos and not images:
            # Single video or all-video carousel — analyze the (first) video.
            result = analyze_video(videos[0])
            result.setdefault("subtitle", "")
        elif images and not videos:
            result = analyze_images(images)
        elif videos and images:
            # Mixed carousel: analyze the image(s); the video model only takes one.
            result = analyze_images(images)
        elif content_type == "video":
            result = analyze_video(paths[0])
            result.setdefault("subtitle", "")
        else:
            result = analyze_images(paths)
        # The model may return flow/subtitle as a JSON array; flatten to text
        # so downstream consumers (e.g. Google Sheets cells) get scalar strings.
        return {
            "subtitle": _to_text(result.get("subtitle", "")),
            "flow": _to_text(result.get("flow", "")),
            "summary": _to_text(result.get("summary", "")),
            "status": "success",
            "error": None,
        }
    except Exception as e:
        return {"subtitle": "", "flow": "", "summary": "", "status": "failed", "error": str(e)}


def _to_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value) if value is not None else ""


def build_report(data_dir: Path, video_paths: list[Path] | None = None) -> Path:
    if video_paths is None:
        video_paths = sorted(data_dir.glob("*.mp4"))
    lines = ["# Content Analysis Report", ""]

    for video_path in video_paths:
        video_id = video_path.stem
        print(f"Analyzing {video_id}...")
        try:
            result = analyze_video(video_path)
        except Exception as e:
            lines += [f"## {video_id}", "", f"**Error:** {e}", ""]
            continue

        lines += [
            f"## {video_id}",
            "",
            "### Subtitle", "",
            _to_text(result.get("subtitle")), "",
            "### Content Flow", "",
            _to_text(result.get("flow")), "",
            "### Summary", "",
            _to_text(result.get("summary")), "",
        ]

    report_path = data_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
