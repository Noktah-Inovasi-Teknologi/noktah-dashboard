"""Slack notices, best-effort: a failed notice is logged and never blocks the save.

Approval notices go to the Noktah Brand's managers' channel (G-21); the webhook
is read from the env var the brand row names (`noktah_brands.slack_managerial_env`).
"""
import logging
from typing import Optional

import httpx

from .settings import env

logger = logging.getLogger(__name__)


async def post_slack(url: Optional[str], text: str) -> bool:
    if not url:
        logger.warning(f"Slack webhook not configured; notice not sent: {text[:120]}")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"text": text})
        if response.status_code != 200:
            logger.warning(f"Slack notice rejected: HTTP {response.status_code} {response.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001 - a notice must never break the save
        logger.warning(f"Slack notice failed: {e}")
        return False


async def approval_pending(webhook_env: Optional[str], client_name: str, field_label: str,
                           proposer: str, link: str) -> bool:
    """'Perubahan brand <Client> menunggu persetujuan: <link>' (G-21)."""
    return await post_slack(
        env(webhook_env) if webhook_env else None,
        f"🟡 Perubahan brand *{client_name}* ({field_label}) oleh {proposer} menunggu persetujuan: <{link}|buka>",
    )
