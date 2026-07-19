"""Import smoke tests — every FastAPI entry module and the MCP server entry
point must import cleanly. This is a regression test for the fastapi/starlette
version-drift incident (2026-07-18): starlette silently got bumped to an
incompatible version by a transitive dependency, and no existing test caught
it because nothing in the suite imported main.py or routers/*.
"""

import importlib


def test_import_main():
    importlib.import_module("main")


def test_import_routers_auth():
    importlib.import_module("routers.auth")


def test_import_routers_chat():
    importlib.import_module("routers.chat")


def test_import_routers_knowledge_base():
    importlib.import_module("routers.knowledge_base")


def test_import_agent_graph():
    importlib.import_module("agent.graph")


def test_import_mcp_server():
    importlib.import_module("mcp_server.server")
