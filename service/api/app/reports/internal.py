"""/internal/jira/*: the hub-jira-sync flow's side of the Jira copy (spec 009 R6)."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import db
from ..incentive import events
from . import jira_store

router = APIRouter()


class IngestIn(BaseModel):
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    done: bool = False
    started_at: Optional[str] = None  # when this sync began; becomes the next cursor (minus overlap)
    error: Optional[str] = None


class CommentResults(BaseModel):
    results: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/jira/sync-state")
async def sync_state() -> dict:
    async with db.pool().acquire() as conn:
        return await jira_store.sync_state(conn)


@router.post("/jira/ingest")
async def ingest(body: IngestIn) -> dict:
    """One page of issues. Returns the Event point comments to post (G-45)."""
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            new_changes = await jira_store.upsert_issues(conn, body.issues)
            event_keys = [i["key"] for i in body.issues if i.get("type") == "Event"]
            comments = await events.queue_comments(conn, event_keys) if event_keys or body.done else []
            if body.done:
                await jira_store.finish_sync(conn, body.started_at, body.error)
    return {"stored": len(body.issues), "new_changes": new_changes, "comments": comments}


@router.post("/jira/comments")
async def comments(body: CommentResults) -> dict:
    async with db.pool().acquire() as conn:
        await events.record_comments(conn, body.results)
    return {}
