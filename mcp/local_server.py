"""Local MCP server for stdio (Claude Desktop / Claude Code / Cursor).

One workspace = one MCP server. Filesystem is truth. SQLite is the index.

Usage:
    python -m local_server --workspace ~/research
    python -m local_server ~/research
"""

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

# Force UTF-8 on stdio. On Windows, the default stderr encoding is the system
# code page (cp1252 on US installs), which silently mangles non-ASCII log output
# and, more importantly, removes an entire class of latent encoding bugs from
# the JSON-RPC stdout stream.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        # Some test runners replace stdio with non-TextIOWrapper objects.
        pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("llmwiki.local")

_LOCAL_USER_ID = os.environ.get("LLMWIKI_USER_ID", str(uuid.uuid5(uuid.NAMESPACE_DNS, "local")))
os.environ["SUPAVAULT_USER_ID"] = _LOCAL_USER_ID


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Wiki local MCP server")
    parser.add_argument("workspace", nargs="?", default=".", help="Path to workspace folder")
    parser.add_argument("--workspace", dest="workspace_flag", default=None, help="Path to workspace folder")
    return parser.parse_args()


async def _init_workspace(workspace_path: str) -> None:
    """Initialize workspace: create dirs, SQLite, default workspace row, scaffold wiki files."""
    ws = Path(workspace_path).resolve()

    (ws / "wiki").mkdir(parents=True, exist_ok=True)
    (ws / ".llmwiki").mkdir(parents=True, exist_ok=True)
    (ws / ".llmwiki" / "cache").mkdir(parents=True, exist_ok=True)

    from vaultfs import SqliteVaultFS
    await SqliteVaultFS.init(str(ws))

    fs = SqliteVaultFS(_LOCAL_USER_ID)
    existing = await fs.get_workspace()
    if not existing:
        ws_name = ws.name
        ws_id = await fs.ensure_workspace(ws_name)

        await fs.create_document(
            ws_id, "overview.md", "Overview", "/wiki/", "md",
            f"This wiki tracks research on {ws_name}.\n\n## Key Findings\n\nNo sources ingested yet.\n\n## Recent Updates\n\nNo activity yet.",
            ["overview"],
        )
        await fs.create_document(
            ws_id, "log.md", "Log", "/wiki/", "md",
            "Chronological record of ingests, queries, and maintenance passes.",
            ["log"],
        )

        overview_path = ws / "wiki" / "overview.md"
        if not overview_path.exists():
            overview_path.write_text(
                f"This wiki tracks research on {ws_name}.\n\n## Key Findings\n\n"
                "No sources ingested yet.\n\n## Recent Updates\n\nNo activity yet.\n"
            )
        log_path = ws / "wiki" / "log.md"
        if not log_path.exists():
            log_path.write_text("Chronological record of ingests, queries, and maintenance passes.\n")

        logger.info("Initialized workspace: %s", ws)
    else:
        logger.info("Workspace ready: %s", ws)


async def _serve(workspace: str) -> None:
    """Initialize the workspace and run the stdio server on the same event loop.

    aiosqlite binds the worker thread / completion callbacks to the loop that
    is current when `connect()` is awaited; running init on a separate loop and
    then handing off to `run_stdio_async` strands those callbacks on a dead
    loop, which on Windows manifests as a handshake that never completes.
    """
    await _init_workspace(workspace)

    from mcp.server.fastmcp import FastMCP
    from tools import register
    from vaultfs import SqliteVaultFS

    mcp = FastMCP(
        name="LLM Wiki",
        instructions=(
            "You are connected to an LLM Wiki workspace. The user has uploaded files, notes, "
            "and documents that you can read, search, edit, and organize. "
            "Call the `guide` tool first to see available knowledge bases and learn the full workflow."
        ),
    )

    def _get_user_id(ctx):
        return _LOCAL_USER_ID

    register(mcp, _get_user_id, lambda user_id: SqliteVaultFS(user_id))

    @mcp.tool(name="ping", description="Test connectivity")
    async def ping() -> str:
        return "pong"

    logger.info("Local MCP server ready - workspace: %s", workspace)
    await mcp.run_stdio_async()


def main():
    args = _parse_args()
    workspace = args.workspace_flag or args.workspace
    workspace = str(Path(workspace).resolve())

    sys.modules["local_server"] = sys.modules[__name__]

    asyncio.run(_serve(workspace))


if __name__ == "__main__":
    main()
