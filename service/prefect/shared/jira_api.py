"""
Jira REST helpers shared across workflows.

Wraps the ``POST /rest/api/3/issue/bulk`` endpoint with batching (Jira Cloud
caps a bulk request at 50 issues), per-call rate limiting, and ``Retry-After``
aware exponential backoff. This replaces the previous inline ``requests.post``
that swallowed HTTP errors -- the cause of bulk creation silently stopping after
Jira Cloud began rate-limiting (~10 clients).
"""
import base64
import logging
from typing import Any, Dict, List, Optional

from .batching import chunked
from .http import async_request
from .rate_limit import AsyncRateLimiter
from .retry import RetryableError, TerminalError, run_with_backoff

logger = logging.getLogger(__name__)

# Jira Cloud hard limit for the bulk-create endpoint.
JIRA_BULK_MAX = 50

# Shared limiter for all Jira calls in this process. Jira Cloud rate limits are
# cost-based; ~1s baseline keeps bursts well under typical thresholds and widens
# automatically on 429.
JIRA_RATE_LIMITER = AsyncRateLimiter(min_interval=1.0, max_interval=30.0)


def build_basic_auth_header(username: str, token: str) -> str:
    """
    Build an HTTP Basic ``Authorization`` header value from email + API token.

    Args:
        username: Jira account email.
        token: Jira API token.

    Returns:
        e.g. ``"Basic dXNlcjp0b2tlbg=="``.
    """
    raw = f"{username}:{token}".encode("ascii")
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


async def _post_bulk_batch(
    base_url: str,
    auth_header: str,
    issues: List[Dict[str, Any]],
    timeout: float,
) -> Dict[str, Any]:
    """POST a single batch (<= JIRA_BULK_MAX issues) with backoff, returning parsed JSON."""
    url = f"{base_url.rstrip('/')}/rest/api/3/issue/bulk"
    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async def _attempt() -> Dict[str, Any]:
        response = await async_request(
            "POST",
            url,
            headers=headers,
            json_body={"issueUpdates": issues},
            timeout=timeout,
            limiter=JIRA_RATE_LIMITER,
        )
        return response.json() or {}

    return await run_with_backoff(_attempt, max_attempts=5, base_delay=2.0, cap=60.0)


async def bulk_create_issues(
    base_url: str,
    auth_header: str,
    issues: List[Dict[str, Any]],
    batch_size: int = JIRA_BULK_MAX,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """
    Create Jira issues in bulk, batching and rate-limiting to survive throttling.

    Args:
        base_url: Jira base URL (e.g. ``https://x.atlassian.net``).
        auth_header: Value for the ``Authorization`` header (see ``build_basic_auth_header``).
        issues: List of issue-update objects (each with a ``fields`` property).
        batch_size: Issues per request (clamped to <= ``JIRA_BULK_MAX``).
        timeout: Per-request timeout in seconds.

    Returns:
        Aggregated result dict::

            {
              "status": "success" | "partial" | "error",
              "total_requested": int,
              "total_created": int,
              "total_errors": int,
              "created_issues": [...],
              "errors": [...],
            }

        On terminal (non-retryable) failure the status is "error" and the
        exception message is included; retryable failures are retried first and
        only surface here if all attempts are exhausted.
    """
    effective_batch = max(1, min(batch_size, JIRA_BULK_MAX))
    batches = chunked(issues, effective_batch)

    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    failure: Optional[str] = None

    for index, batch in enumerate(batches):
        try:
            result = await _post_bulk_batch(base_url, auth_header, batch, timeout)
            created.extend(result.get("issues", []))
            errors.extend(result.get("errors", []))
            logger.info(
                f"Bulk batch {index + 1}/{len(batches)}: "
                f"created={len(result.get('issues', []))}, "
                f"errors={len(result.get('errors', []))}"
            )
        except (RetryableError, TerminalError) as exc:
            # Record and keep going so one bad batch doesn't lose the others.
            failure = str(exc)
            errors.append({"batch_index": index, "error": str(exc)})
            logger.error(f"Bulk batch {index + 1}/{len(batches)} failed: {exc}")

    total_created = len(created)
    total_errors = len(errors)
    if total_created and total_errors:
        status = "partial"
    elif total_created:
        status = "success"
    else:
        status = "error"

    result: Dict[str, Any] = {
        "status": status,
        "total_requested": len(issues),
        "total_created": total_created,
        "total_errors": total_errors,
        "created_issues": created,
        "errors": errors,
    }
    if failure and status == "error":
        result["error"] = failure
    return result
