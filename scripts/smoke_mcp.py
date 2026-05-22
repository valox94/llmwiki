"""Smoke test the local llmwiki MCP server over stdio.

Run with the project venv:
    api\.venv\Scripts\python.exe scripts\smoke_mcp.py C:\SweProjects\llmwiki-demo

The repo's mcp/ directory shadows the pip mcp package when this file is
executed from the repo root, so we move sys.path so site-packages wins.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Run from the mcp/ directory so the pip-installed `mcp` package resolves.
os.chdir(REPO_ROOT / "mcp")
# Drop repo root from sys.path so the local namespace package doesn't shadow.
sys.path = [p for p in sys.path if Path(p).resolve() != REPO_ROOT]

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def _run(workspace: str) -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "llmwiki"), "mcp", workspace],
        env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"server={init.serverInfo.name} version={init.serverInfo.version}")
            tools = await session.list_tools()
            for tool in tools.tools:
                desc = (tool.description or "").splitlines()[0:1]
                print(f"  - {tool.name}: {desc[0] if desc else ''}")
            ping = await session.call_tool("ping", {})
            print(f"ping -> {ping.content[0].text if ping.content else 'no-content'}")
            guide = await session.call_tool("guide", {})
            text = guide.content[0].text if guide.content else ""
            tail = text.splitlines()[-3:] if text else []
            print("guide tail:")
            for line in tail:
                print(f"  | {line}")
    return 0


def main() -> int:
    workspace = sys.argv[1] if len(sys.argv) > 1 else str(REPO_ROOT.parent / "llmwiki-demo")
    return asyncio.run(_run(workspace))


if __name__ == "__main__":
    raise SystemExit(main())
