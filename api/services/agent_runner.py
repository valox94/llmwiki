"""Agent-driven wiki maintenance via the Cursor SDK.

Two symmetric entry points:

- `run_ingest`    — agent reads a freshly-uploaded source and produces wiki pages.
- `run_deprecate` — agent reads a source one last time, updates citing wiki
                    pages, then the source itself is deleted.

Both share the same SDK orchestration (`_run_agent`); they differ only in the
prompt and an optional post-run hook (e.g. delete the source after a successful
deprecation). Concurrency is per-workspace asyncio.Lock — wiki structural pages
(`overview.md`, `log.md`) are shared mutable state; serializing avoids races
that the MCP `edit` tool's single-match check would reject after the fact.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, AsyncIterator

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
class AgentEvent:
    """One event in an agent run stream. Maps to a single SSE `data:` line.

    The shape is intentionally generic — the route serializes `(type, data)`
    and the UI renders by `type`. Adding a new event kind is one-touch:
    yield it from the runner, render it in the UI; nothing in between
    needs to change.
    """
    type: str
    data: dict[str, Any]


# Back-compat alias for routes/tests that imported the old name.
IngestEvent = AgentEvent


# ── MCP server config ────────────────────────────────────────────────────

def _mcp_server_config(workspace: str):
    """Build the inline MCP server config for the agent.

    Mirrors the user's `.cursor/mcp.json` so the agent talks to the same
    local server (and same workspace) the user is debugging against.
    Computes paths from this file's location to avoid environment-specific
    hardcoding. Uses the SDK's typed `StdioMcpServerConfig` rather than a raw
    dict so any schema drift surfaces at the SDK boundary, not at runtime.
    """
    from cursor_sdk import StdioMcpServerConfig

    repo_root = Path(__file__).resolve().parent.parent.parent
    return {
        "llmwiki": StdioMcpServerConfig(
            command=sys.executable,
            args=[str(repo_root / "llmwiki"), "mcp", workspace],
        )
    }


# ── DB helpers ───────────────────────────────────────────────────────────

async def _lookup_doc(db, doc_id: str) -> dict | None:
    """Fetch the document row by id. Returns None if missing."""
    cursor = await db.execute(
        "SELECT id, filename, title, relative_path, path, file_type, page_count, status "
        "FROM documents WHERE id = ?",
        (doc_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


async def _get_backlinks(db, doc_id: str) -> list[dict]:
    """Wiki pages that cite the given document. Drives blast-radius display.

    Local-mode SQLite hard-deletes archived docs (no `archived` column), so
    filtering out archived rows is unnecessary — they're already gone. Mirrors
    the query shape in `mcp/vaultfs/sqlite.py:get_backlinks`.
    """
    cursor = await db.execute(
        "SELECT d.id, d.path, d.filename, d.title, dr.reference_type "
        "FROM document_references dr "
        "JOIN documents d ON dr.source_document_id = d.id "
        "WHERE dr.target_document_id = ? "
        "  AND d.status != 'failed' "
        "ORDER BY d.path, d.filename",
        (doc_id,),
    )
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


# ── Prompt builders ──────────────────────────────────────────────────────

def _build_ingest_prompt(doc: dict, kb_slug: str) -> str:
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
        "   Mermaid syntax rules (the renderer is strict — invalid blocks fail "
        "to render):\n"
        "   - Use only `flowchart`/`graph`, `sequenceDiagram`, `classDiagram`, "
        "`erDiagram`, `pie`, or `quadrantChart`. No themes, no `%%{init}%%` "
        "directives, no `classDef`/`style` lines.\n"
        "   - **Quote every node label** with double quotes: "
        "`A[\"My Label\"]`, never `A[My Label]`. This is mandatory whenever "
        "the label contains spaces, punctuation (`#`, `:`, `&`, `(`, `)`, `/`, "
        "`%`, `+`, quotes), or symbols — and harmless when it doesn't, so "
        "just always quote.\n"
        "   - Do not use backslash escapes (`\\\"`, `\\n`) inside labels. "
        "If you need a line break, use `<br/>`; if you need a quote, rephrase "
        "to avoid it; replace `\"` units like inches with `in` or `inch`.\n"
        "   - Keep each block under ~12 nodes. If you can't draw a clean diagram, "
        "use a markdown table instead.\n"
        "5. `edit` `/wiki/overview.md` to bump source counts, add key findings, "
        "and add a recent-updates entry.\n"
        "6. `append` an entry to `/wiki/log.md` formatted as the doctrine specifies.\n\n"
        f"Cite the source as `[^N]: {filename}, p.X` (use the FULL filename verbatim, "
        "include page numbers for PDFs)."
    )


def _build_deprecate_prompt(doc: dict, kb_slug: str, citing: list[dict]) -> str:
    filename = doc["filename"]
    title = doc.get("title") or filename
    relative = doc.get("relative_path") or filename
    citing_lines = "\n".join(
        f"- `{c['path']}{c['filename']}` ({c.get('title') or c['filename']}) — {c.get('reference_type', 'cites')}"
        for c in citing
    )

    return (
        f"A source is being removed from the `{kb_slug}` wiki:\n\n"
        f"- filename: `{filename}`\n"
        f"- title: {title}\n"
        f"- workspace path: `/{relative}`\n\n"
        f"This source is currently cited by {len(citing)} wiki page(s):\n"
        f"{citing_lines}\n\n"
        "Update the wiki to reflect the removal:\n\n"
        f"1. Call `guide` first to refresh on the doctrine.\n"
        f"2. If you need the source's content for context (to decide what to keep "
        f"in citing pages), call `read(knowledge_base=\"{kb_slug}\", path=\"/{relative}\")`.\n"
        "3. For each citing page, decide:\n"
        "   - **rewrite** if the page stands without this source: `edit` to remove the citation "
        "and any sentences that solely depended on it. Update the frontmatter `date`.\n"
        "   - **archive** if the page exists only because of this source: `delete` it.\n"
        "4. `edit` `/wiki/overview.md` to decrement source count and add a recent-updates entry "
        "noting the removal.\n"
        f"5. `append` a deprecate entry to `/wiki/log.md`: header `## [YYYY-MM-DD] deprecate | {filename}` "
        "with bullets describing what was rewritten, what was archived, and why.\n\n"
        "After your edits complete, the source itself will be deleted by the system. "
        "Do not call `delete` on the source — focus on the wiki side."
    )


# ── SDK message → AgentEvent mapping ─────────────────────────────────────

def _serialize_block(block: Any) -> dict[str, Any]:
    """Best-effort conversion of an SDK content block into a JSON-friendly dict.

    Common types (`text`, `tool_use`, `tool_result`) get typed projections;
    unknown shapes are stringified rather than dropped so server-side SDK
    additions don't require a UI deploy.
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


def _message_to_event(message: Any) -> AgentEvent | None:
    """Convert an SDK SDKMessage into an AgentEvent, or None to drop it.

    Surfaces only assistant messages (text + tool calls); system / user-echo
    messages are noise for a progress panel.
    """
    mtype = getattr(message, "type", None)
    if mtype != "assistant":
        return None
    inner = getattr(message, "message", None)
    if inner is None:
        return None
    blocks_raw = getattr(inner, "content", []) or []
    blocks = [_serialize_block(b) for b in blocks_raw]
    return AgentEvent("assistant", {"blocks": blocks})


# ── Generic agent runner ─────────────────────────────────────────────────

async def _run_agent(
    *,
    workspace: str,
    doc_id: str,
    filename: str,
    prompt: str,
    post_run_hook: Callable[[bool], Awaitable[None]] | None = None,
) -> AsyncIterator[AgentEvent]:
    """Drive one Cursor SDK agent run, yielding events as they arrive.

    `post_run_hook(success)` runs once after the SDK contexts close — `success`
    is True when the run's terminal status is `finished`. Hook failures are
    surfaced as `error` events but never propagate; the agent itself is
    already disposed by the time the hook runs.
    """
    try:
        from cursor_sdk import (
            AsyncClient, AsyncAgent, AgentOptions, LocalAgentOptions, CursorAgentError,
        )
    except ImportError as e:
        yield AgentEvent("error", {
            "phase": "import",
            "message": f"cursor-sdk not installed: {e}. Run `pip install cursor-sdk`.",
        })
        return

    lock = _lock_for(workspace)
    if lock.locked():
        yield AgentEvent("queued", {"message": "Another agent run is in flight for this workspace; waiting..."})

    mcp_servers = _mcp_server_config(workspace)

    async with lock:
        yield AgentEvent("starting", {"workspace": workspace, "doc_id": doc_id, "filename": filename})

        terminal_status: str | None = None
        try:
            async with await AsyncClient.launch_bridge(workspace=workspace) as client:
                options = AgentOptions(
                    model=settings.AGENT_MODEL,
                    api_key=settings.CURSOR_API_KEY,
                    local=LocalAgentOptions(cwd=workspace),
                    mcp_servers=mcp_servers,
                )
                async with await AsyncAgent.create(options, client=client) as agent:
                    agent_id = getattr(agent, "agent_id", None)
                    yield AgentEvent("agent_created", {"agent_id": agent_id})

                    run = await agent.send(prompt)
                    run_id = getattr(run, "run_id", None) or getattr(run, "id", None)
                    logger.info("Agent run started: agent_id=%s run_id=%s doc=%s", agent_id, run_id, doc_id)
                    yield AgentEvent("run_started", {"run_id": run_id})

                    async for message in run.messages():
                        event = _message_to_event(message)
                        if event is not None:
                            yield event

                    result = await run.wait()
                    terminal_status = getattr(result, "status", "unknown")
                    yield AgentEvent("finished", {
                        "status": terminal_status,
                        "run_id": run_id,
                        "agent_id": agent_id,
                    })

        except CursorAgentError as e:
            logger.exception("Agent failed to start")
            yield AgentEvent("error", {
                "phase": "startup",
                "message": str(e),
                "retryable": getattr(e, "is_retryable", False),
            })
        except Exception as e:
            logger.exception("Agent crashed mid-run")
            yield AgentEvent("error", {
                "phase": "runtime",
                "message": f"{type(e).__name__}: {e}",
            })

        if post_run_hook is not None:
            success = terminal_status == "finished"
            try:
                await post_run_hook(success)
            except Exception as e:
                logger.exception("Post-run hook failed")
                yield AgentEvent("error", {
                    "phase": "post_run",
                    "message": f"Post-run cleanup failed: {type(e).__name__}: {e}",
                })


# ── Public entry points ──────────────────────────────────────────────────

async def run_ingest(
    db,
    doc_id: str,
    workspace: str,
    kb_slug: str,
) -> AsyncIterator[AgentEvent]:
    """Agent-driven ingest of a freshly-uploaded source into the wiki."""
    if not settings.CURSOR_API_KEY:
        yield AgentEvent("error", {
            "phase": "config",
            "message": "CURSOR_API_KEY is not set. Add it to .env to enable the agent.",
        })
        return

    doc = await _lookup_doc(db, doc_id)
    if not doc:
        yield AgentEvent("error", {"phase": "lookup", "message": f"Document {doc_id} not found"})
        return

    if doc.get("status") not in ("ready", None):
        yield AgentEvent("error", {
            "phase": "lookup",
            "message": (
                f"Document is `{doc.get('status')}`. Wait for processing to finish "
                "before ingesting with the agent."
            ),
        })
        return

    prompt = _build_ingest_prompt(doc, kb_slug)
    async for event in _run_agent(
        workspace=workspace,
        doc_id=doc_id,
        filename=doc["filename"],
        prompt=prompt,
    ):
        yield event


async def run_deprecate(
    db,
    doc_id: str,
    workspace: str,
    kb_slug: str,
    document_service,
) -> AsyncIterator[AgentEvent]:
    """Agent-driven removal of a source: update citing wiki pages, then delete.

    Fast-path: if the source has no citing wiki pages, skip the agent entirely
    and delete directly. The agent is doing real cognitive work only when the
    wiki actually depends on this source.
    """
    if not settings.CURSOR_API_KEY:
        yield AgentEvent("error", {
            "phase": "config",
            "message": "CURSOR_API_KEY is not set. Add it to .env to enable the agent.",
        })
        return

    doc = await _lookup_doc(db, doc_id)
    if not doc:
        yield AgentEvent("error", {"phase": "lookup", "message": f"Document {doc_id} not found"})
        return

    if doc.get("path", "/").startswith("/wiki/"):
        yield AgentEvent("error", {
            "phase": "lookup",
            "message": "This is a wiki page, not a source. Use the wiki UI to edit or delete it.",
        })
        return

    citing = await _get_backlinks(db, doc_id)
    yield AgentEvent("blast_radius", {
        "citing_count": len(citing),
        "citing": [
            {"id": c["id"], "path": c["path"], "filename": c["filename"], "title": c.get("title")}
            for c in citing
        ],
    })

    # Fast-path: nothing in the wiki depends on this source. Skip the agent and
    # delete directly. The user explicitly accepted this asymmetry — the agent
    # has nothing to think about and the wait would be pure overhead.
    if not citing:
        yield AgentEvent("starting", {
            "workspace": workspace, "doc_id": doc_id, "filename": doc["filename"],
            "fast_path": True,
        })
        try:
            await document_service.delete(doc_id)
        except Exception as e:
            logger.exception("Fast-path delete failed")
            yield AgentEvent("error", {
                "phase": "delete",
                "message": f"Failed to delete source: {type(e).__name__}: {e}",
            })
            return
        yield AgentEvent("finished", {"status": "finished", "fast_path": True})
        return

    prompt = _build_deprecate_prompt(doc, kb_slug, citing)

    async def _post_run(success: bool) -> None:
        if not success:
            logger.warning(
                "Skipping source delete because agent run did not finish cleanly (doc_id=%s)",
                doc_id,
            )
            return
        await document_service.delete(doc_id)

    async for event in _run_agent(
        workspace=workspace,
        doc_id=doc_id,
        filename=doc["filename"],
        prompt=prompt,
        post_run_hook=_post_run,
    ):
        yield event


# ── SSE serialization ────────────────────────────────────────────────────

def event_to_sse(event: AgentEvent) -> str:
    """Serialize one AgentEvent into an SSE `data:` line.

    Kept here next to the producer so any future event-shape change is
    visible at one site rather than scattered between the runner and the
    route.
    """
    payload = {"type": event.type, **event.data}
    return f"data: {json.dumps(payload)}\n\n"
