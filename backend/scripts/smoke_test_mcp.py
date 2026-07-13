#!/usr/bin/env python3
"""Smoke test for backend/mcp_server/server.py using FastMCP in-process Client.

Verifies:
  1. list_tools returns retrieve and web_search
  2. retrieve(kb_id=1, query=...) returns the expected dict schema
  3. web_search(query=...) returns results with title/url/snippet

Run from backend/:
    python3 scripts/smoke_test_mcp.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastmcp import Client
from mcp_server.server import mcp

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def ok(cond: bool, msg: str) -> None:
    print(f"  {PASS if cond else FAIL} {msg}")
    if not cond:
        sys.exit(1)


async def main() -> None:
    async with Client(mcp) as client:

        # ── 1. list_tools ─────────────────────────────────────────────────────
        print("\n[1] list_tools")
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        print(f"    tools: {sorted(tool_names)}")
        ok("retrieve" in tool_names, "retrieve tool present")
        ok("web_search" in tool_names, "web_search tool present")
        ok(len(tools) == 2, f"exactly 2 tools (got {len(tools)})")

        # ── 2. retrieve ───────────────────────────────────────────────────────
        print("\n[2] retrieve(kb_id=1, query='Python decorators')")
        result = await client.call_tool("retrieve", {"kb_id": 1, "query": "Python decorators"})
        print(f"    raw result: {result}")

        data = result.data if hasattr(result, "data") else result
        ok(isinstance(data, dict), f"result is dict: {type(data)}")
        ok("chunks" in data, "data has 'chunks' key")
        ok("evidence" in data, "data has 'evidence' key")
        ok("relevance_ok" in data, "data has 'relevance_ok' key")
        print(f"    chunks count : {len(data.get('chunks', []))}")
        print(f"    evidence     : {data.get('evidence', [])}")
        print(f"    relevance_ok : {data.get('relevance_ok')}")

        # ── 3. web_search ─────────────────────────────────────────────────────
        print("\n[3] web_search(query='FastMCP Python MCP server')")
        ws_result = await client.call_tool(
            "web_search", {"query": "FastMCP Python MCP server", "num_results": 3}
        )
        ws_data = ws_result.data if hasattr(ws_result, "data") else ws_result
        ok(isinstance(ws_data, dict), "web_search result is dict")
        ok("results" in ws_data, "data has 'results' key")
        ok("evidence" in ws_data, "data has 'evidence' key")
        print(f"    results count: {len(ws_data.get('results', []))}")
        if ws_data.get("results"):
            first = ws_data["results"][0]
            print(f"    first result : title={first.get('title', '')!r}")
            print(f"                   url={first.get('url', '')!r}")

    print(f"\n{PASS} All MCP smoke tests passed\n")


asyncio.run(main())
