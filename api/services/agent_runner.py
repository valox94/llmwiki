"""Agent-driven wiki ingestion via the Cursor SDK.

Spawns a local Cursor agent that connects to the workspace's MCP server and
runs the LLM-Wiki ingest workflow (call `guide`, read the new source, update
concepts/entities/overview/log) on a single document.

Concurrency: per-workspace asyncio.Lock — the wiki's structural pages
(`overview.md`, `log.md`) are shared mutable state; serializing avoids races
that the MCP `edit` tool's single-match check would otherwise reject after the
fact. One agent run per workspace at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from config import settings

logger = logging.getLogger(__name__)


# ── Per-workspace serialization ──────────────────────────────────────────

_locks: dict[str, asyncio.Lock] = {}


def _lock_for(workspace: str) -> asyncio.Lock:
    """Get-or-create an asyncio.Lock keyed by absolute workspace path."""
    if workspace not in _locks:
        _locks[workspace] = asyncio.Lock()
    return _locks[workspace]


# ── Event shape exposed to the route layer ───────────────────────────────

@dataclass
class IngestEvent:
    """One event in the ingest stream. Maps to a single SSE `data:` line.

    The shape is intentionally generic — the route serializes `(type, data)`
    and the UI renders by `type`. Adding a new event kind is one-touch:
    yield it from `run_ingest`, render it in the UI; nothing in between
    needs to change.
    """
    type: str
    data: dict[str, Any]


# ── Prompt + MCP server config ───────────────────────────────────────────

def _build_prompt(doc: dict, kb_slug: str) -> str:
    """Compose the ingest prompt from the document row + KB slug.

    The prompt instructs the agent to follow `GUIDE_TEXT`'s ingest workflow
    and seeds the citation format using the source's exact filename so the
    citation parser has a chance to link them.
    """
    filename = doc["filename"]
    title = doc.get("title") or filename
    relative = doc.get("relative_path") or filename
    file_type = doc.get("file_type") or ""
    page_count = doc.get("page_count") or 0
    pages_hint = f" ({page_count} pages)" if page_count else ""

    return (
        f"A new source document was just uploaded to the `{kb_slug}` wiki:\n\n"
        f"- filename: `{filename}`\n"
        f"- title: {title}\n"
        f"- workspace path: `/{relative}`\n"
        f"- type: {file_type}{pages_hint}\n\n"
        "Ingest it into the wiki by following the LLM-Wiki workflow exactly:\n\n"
        "1. Call `guide` first to refresh on the doctrine and confirm the KB slug.\n"
        f"2. Call `read(knowledge_base=\"{kb_slug}\", path=\"/{relative}\")` "
        "(use the `pages` parameter for multi-page documents).\n"
        "3. Identify the concepts (abstract ideas) and entities (concrete things) "
        "this source introduces or expands. Be deliberate.\n"
        "4. Use `create` (or `edit` if a relevant page exists) to add pages under "
        "`/wiki/concepts/` and `/wiki/entities/`. Each page must have YAML "
        "frontmatter with title, description, date, and >=2 tags, and at least "
        "one visual element (mermaid diagram or table) per the doctrine.\n"
        "5. `edit` `/wiki/overview.md` to bump source counts, add key findings, "
        "and add a recent-updates entry.\n"
        "6. `append` an entry to `/wiki/log.md` formatted as the doctrine specifies.\n\n"
        f"Cite the source as `[^N]: {filename}, p.X` (use the FULL filename verbatim, "
        "include page numbers for PDFs)."
    )


def _mcp_server_config(workspace: str) -> dict[str, dict[str, Any]]:
    """Build the inline MCP server config for the agent.

    Mirrors the user's `.cursor/mcp.json` so the agent talks to the same
    local server (and same workspace) the user is debugging against.
    Computes paths from this file's location to avoid environment-specific
    hardcoding.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    return {
        "llmwiki": {
            "command": sys.executable,
            "args": [str(repo_root / "llmwiki"), "mcp", workspace],
        }
    }


# ── SDK message → IngestEvent mapping ────────────────────────────────────

def _serialize_block(block: Any) -> dict[str, Any]:
    """Best-effort conversion of an SDK content block into a JSON-friendly dict.

    SDK message shapes are stable for the common types (`text`, `tool_use`,
    `tool_result`) but the safest assumption for a UI-streaming layer is
    that new types will appear. Unknown shapes are stringified rather than
    dropped.
    """
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", None),
            "id": getattr(block, "id", None),
        }
    if btype == "tool_result":
        content = getattr(block, "content", None)
        if isinstance(content, list):
            content = [_serialize_block(c) for c in content]
        return {
            "type": "tool_result",
            "tool_use_id": getattr(block, "tool_use_id", None),
            "content": content,
            "is_error": getattr(block, "is_error", False),
        }
    return {"type": btype or "unknown", "repr": repr(block)[:500]}


def _message_to_event(message: Any) -> IngestEvent | None:
    """Convert an SDK SDKMessage into an IngestEvent, or None to drop it.

    We surface assistant messages (text + tool calls) and skip system /
    user-echo messages — those are noise for a progress panel.
    """
    mtype = getattr(message, "type", None)
    if mtype != "assistant":
        return None
    inner = getattr(message, "message", None)
    if inner is None:
        return None
    blocks_raw = getattr(inner, "content", []) or []
    blocks = [_serialize_block(b) for b in blocks_raw]
    return IngestEvent("assistant", {"blocks": blocks})


# ── Public entry point ───────────────────────────────────────────────────

async def run_ingest(
    db,
    doc_id: str,
    workspace: str,
    kb_slug: str,
) -> AsyncIterator[IngestEvent]:
    """Drive the ingest agent for one document, yielding events as they arrive.

    Failure modes are surfaced as terminal `error` events rather than thrown:
    the route layer streams events to the UI and an unhandled exception mid-
    stream would just truncate the SSE without the user seeing why.
    """
    if not settings.CURSOR_API_KEY:
        yield IngestEvent("error", {
            "phase": "config",
            "message": "CURSOR_API_KEY is not set. Add it to .env to enable the ingest agent.",
        })
        return

    cursor = await db.execute(
        "SELECT id, filename, title, relative_path, file_type, page_count, status "
        "FROM documents WHERE id = ?",
        (doc_id,),
    )
    row = await cursor.fetchone()
    if not row:
        yield IngestEvent("error", {"phase": "lookup", "message": f"Document {doc_id} not found"})
        return

    cols = [d[0] for d in cursor.description]
    doc = dict(zip(cols, row))

    if doc.get("status") not in ("ready", None):
        yield IngestEvent("error", {
            "phase": "lookup",
            "message": (
                f"Document is `{doc.get('status')}`. Wait for processing to finish "
                "before ingesting with the agent."
            ),
        })
        return

    try:
        from cursor_sdk import AsyncClient, LocalAgentOptions, CursorAgentError
    except ImportError as e:
        yield IngestEvent("error", {
            "phase": "import",
            "message": f"cursor-sdk not installed: {e}. Run `pip install cursor-sdk`.",
        })
        return

    lock = _lock_for(workspace)
    if lock.locked():
        yield IngestEvent("queued", {"message": "Another ingest is in flight for this workspace; waiting..."})

    async with lock:
        prompt = _build_prompt(doc, kb_slug)
        mcp_servers = _mcp_server_config(workspace)
        yield IngestEvent("starting", {"workspace": workspace, "doc_id": doc_id, "filename": doc["filename"]})

        try:
            async with await AsyncClient.launch_bridge(workspace=workspace) as client:
                async with await client.agents.create(
                    model=settings.AGENT_MODEL,
                    api_key=settings.CURSOR_API_KEY,
                    local=LocalAgentOptions(cwd=workspace),
                    mcp_servers=mcp_servers,
                ) as agent:
                    agent_id = getattr(agent, "agent_id", None)
                    yield IngestEvent("agent_created", {"agent_id": agent_id})

                    run = await agent.send(prompt)
                    run_id = getattr(run, "id", None)
                    logger.info("Ingest run started: agent_id=%s run_id=%s doc=%s", agent_id, run_id, doc_id)
                    yield IngestEvent("run_started", {"run_id": run_id})

                    async for message in run.messages():
                        event = _message_to_event(message)
                        if event is not None:
                            yield event

                    result = await run.wait()
                    status = getattr(result, "status", "unknown")
                    yield IngestEvent("finished", {
                        "status": status,
                        "run_id": run_id,
                        "agent_id": agent_id,
                    })

        except CursorAgentError as e:
            logger.exception("Ingest agent failed to start")
            yield IngestEvent("error", {
                "phase": "startup",
                "message": str(e),
                "retryable": getattr(e, "is_retryable", False),
            })
        except Exception as e:
            logger.exception("Ingest agent crashed mid-run")
            yield IngestEvent("error", {
                "phase": "runtime",
                "message": f"{type(e).__name__}: {e}",
            })


def event_to_sse(event: IngestEvent) -> str:
    """Serialize one IngestEvent into an SSE `data:` line.

    Kept here next to the producer so any future event-shape change is
    visible at one site rather than scattered between the runner and the
    route.
    """
    payload = {"type": event.type, **event.data}
    return f"data: {json.dumps(payload)}\n\n"
