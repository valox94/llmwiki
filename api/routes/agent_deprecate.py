"""Agent-driven source deprecation route — local mode only.

Symmetric counterpart to `agent_ingest`: streams Server-Sent Events as a
Cursor SDK agent updates the wiki to reflect the removal of a source, then
the source itself is deleted by a post-run hook in `services.agent_runner`.

Fast-path: if the source has no citing wiki pages, the runner skips the agent
entirely and deletes directly — the agent has nothing to reason about.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from config import settings
from deps import get_user_id
from services.agent_runner import event_to_sse, run_deprecate

router = APIRouter(tags=["agent"])


@router.post("/v1/agents/deprecate/{doc_id}")
async def deprecate_with_agent(
    doc_id: str,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    """Stream an agent-driven deprecation of `doc_id`.

    The route looks up the workspace KB slug, then delegates to `run_deprecate`
    which yields events in the same shape as the ingest stream. Terminal
    events: `finished` (the source is now deleted) or `error` (source intact;
    user can retry).
    """
    db = request.app.state.sqlite_db

    cursor = await db.execute(
        "SELECT name FROM workspace WHERE user_id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No workspace initialized")
    kb_slug = row[0]

    workspace = settings.WORKSPACE_PATH

    document_service = request.app.state.factory.document_service(user_id)

    async def event_stream():
        async for event in run_deprecate(db, doc_id, workspace, kb_slug, document_service):
            yield event_to_sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
