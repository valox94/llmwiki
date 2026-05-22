"""Agent-driven wiki ingestion route — local mode only.

Exposes a single endpoint that streams Server-Sent Events as the Cursor
SDK agent reads a freshly-uploaded source and updates the wiki via the
local MCP server. The agent loop itself lives in `services.agent_runner`;
this module only handles HTTP concerns: authentication, doc lookup,
SSE framing, and translating the runner's event stream into the wire format.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from config import settings
from deps import get_user_id
from services.agent_runner import event_to_sse, run_ingest

router = APIRouter(tags=["agent"])


@router.post("/v1/agents/ingest/{doc_id}")
async def ingest_with_agent(
    doc_id: str,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    """Stream an agent-driven ingest of `doc_id` into the configured wiki.

    Returns text/event-stream. Each event is a JSON object with at minimum
    a `type` field; consumers should switch on `type` and tolerate unknown
    types. Terminal events: `finished` (success) or `error`.
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

    async def event_stream():
        async for event in run_ingest(db, doc_id, workspace, kb_slug):
            yield event_to_sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Disable proxy buffering for true streaming. Without this,
            # nginx/cloudflare-style intermediaries hold the response until
            # it grows past their buffer threshold and the UI sees nothing
            # for tens of seconds.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
