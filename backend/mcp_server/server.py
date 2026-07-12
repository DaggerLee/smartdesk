"""SmartDesk MCP server — exposes the tool layer via FastMCP (stdio transport).

Tools
-----
retrieve(kb_id, query)
    Search a SmartDesk knowledge base using ChromaDB semantic search.
    Returns chunks + evidence list following the SmartDesk evidence protocol,
    plus relevance_ok indicating whether the top result clears the cosine
    distance threshold.

web_search(query, num_results=5)
    Search the public web via DuckDuckGo (no API key required).
    Returns results + evidence list.

Running
-------
    cd backend
    python3 -m mcp_server.server          # preferred (keeps cwd = backend/)
    python3 mcp_server/server.py          # also works

Registering (Claude Desktop / Claude Code)
------------------------------------------
See backend/mcp_server/README section in the project README.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend/ to sys.path so agent/tools, config, chroma_client, etc. resolve
# regardless of how this script is invoked (subprocess, -m, absolute path…).
_BACKEND = Path(__file__).parent.parent.resolve()
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# chroma_client and the SQLite DB use relative paths anchored to backend/.
# Set cwd here so the server works regardless of how it is invoked.
import os
os.chdir(_BACKEND)

from fastmcp import FastMCP

mcp = FastMCP(
    name="SmartDesk Knowledge Tools",
    instructions=(
        "Use 'retrieve' to search the user's private knowledge base. "
        "Use 'web_search' when the question requires current or external information."
    ),
)


@mcp.tool()
def retrieve(kb_id: int, query: str) -> dict:
    """Search a SmartDesk knowledge base for relevant document chunks.

    Args:
        kb_id:  ID of the knowledge base to search (integer).
        query:  Natural-language search query.

    Returns a dict with:
        chunks        — list of matching text strings
        evidence      — list of {"text": str, "source": str} for citation
        relevance_ok  — True when the top result is below the cosine distance
                        threshold (0.8); False signals low-confidence results.
    """
    from agent.tools.retrieve import RetrieveTool
    return RetrieveTool(kb_id=kb_id).run(query=query)


@mcp.tool()
def web_search(query: str, num_results: int = 5) -> dict:
    """Search the public web using DuckDuckGo (no API key required).

    Args:
        query:       Search query string.
        num_results: Maximum number of results to return (default 5).

    Returns a dict with:
        results  — list of {"title": str, "url": str, "snippet": str}
        evidence — list of {"text": str, "source": str} for citation
    """
    from agent.tools.web_search import WebSearchTool
    return WebSearchTool().run(query=query)


if __name__ == "__main__":
    mcp.run()
