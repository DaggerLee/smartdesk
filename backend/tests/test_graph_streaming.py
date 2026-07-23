"""tests/test_graph_streaming.py — stream_graph() event-sequence tests (W5 T3).

Covers the streaming entry point added on top of the T1/T2 graph:
  - direct_node/rag_node forward "chunk" events live (via get_stream_writer),
    not just a buffered final blob — one event per chunk yielded by
    llm_stream()/generate_answer_stream().
  - llm_node emits one "tool_call" event per pending call before dispatch,
    the same timing agent.loop.run_agent() uses for its AgentEvent yields.
  - The "final" event carries what chat.py's SSE layer reads per route:
    "messages" (agent's two-stage re-stream), "doc_sources"/"web_results"/
    "used_docs"/"used_web" (rag's sources-frame gate).

run_graph()'s own contract (blocking, dict return) is already covered by
tests/test_graph_self_healing.py and is unaffected by this file.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import agent.graph as graph_module
from agent.graph import stream_graph
from gemini_client import _build_prompt
from llm.client import LLMResponse, ToolCall


def _text(t: str) -> LLMResponse:
    return LLMResponse(text=t, tool_calls=[], raw={})


def _tool(name: str, **args) -> LLMResponse:
    return LLMResponse(text=None, tool_calls=[ToolCall(name=name, args=args)], raw={})


# ── direct path ────────────────────────────────────────────────────────────

def test_direct_path_streams_chunks_live():
    with patch("agent.graph.route", return_value="direct"), \
         patch("agent.graph.llm_stream", return_value=iter(["Hel", "lo!"])):
        events = list(stream_graph("Hi", kb_id=1))

    assert [e.type for e in events] == ["chunk", "chunk", "final"]
    assert events[0].data == {"text": "Hel"}
    assert events[1].data == {"text": "lo!"}
    final = events[-1].data
    assert final["route"] == "direct"
    assert final["answer"] == "Hello!"


# ── rag path ──────────────────────────────────────────────────────────────

def test_rag_path_streams_chunks_and_reports_sources_gate():
    chroma_result = [{"text": "chunk text", "filename": "doc.pdf", "distance": 0.1}]
    with patch("agent.graph.route", return_value="rag"), \
         patch("chroma_client.query_documents", return_value=chroma_result), \
         patch("agent.graph.assess_rag_quality", return_value=True), \
         patch("agent.graph.generate_answer_stream",
               return_value=iter(["The answer", "[SOURCE_USED]"])):
        events = list(stream_graph("What is X?", kb_id=1))

    assert [e.type for e in events] == ["chunk", "final"]
    assert events[0].data == {"text": "The answer"}
    final = events[-1].data
    assert final["route"] == "rag"
    assert final["answer"] == "The answer"       # marker stripped for DB/display
    assert final["used_docs"] is True             # marker was present pre-strip
    assert final["used_web"] is False
    assert final["doc_sources"][0]["filename"] == "doc.pdf"


# ── agent path ────────────────────────────────────────────────────────────

def test_agent_path_emits_tool_call_before_final():
    with patch("agent.graph.route", return_value="agent"), \
         patch("agent.graph.complete", side_effect=[
             _tool("retrieve", query="q"),
             _text("Well-grounded answer."),
         ]), \
         patch("agent.tools.retrieve.RetrieveTool.run", return_value={
             "chunks": ["c"],
             "evidence": [{"text": "c", "source": "doc.pdf"}],
             "relevance_ok": True,
         }), \
         patch("agent.graph._check_groundedness",
               return_value={"supported": True, "unsupported_sentences": []}):
        events = list(stream_graph("q", kb_id=1))

    assert [e.type for e in events] == ["tool_call", "final"]
    assert events[0].data == {"name": "retrieve", "args": {"query": "q"}}
    final = events[-1].data
    assert final["route"] == "agent"
    assert final["answer"] == "Well-grounded answer."
    assert final["messages"]  # chat.py's two-stage re-stream needs this


def test_stream_graph_normalizes_history_before_checkpointing():
    captured = {}
    history = [SimpleNamespace(question="previous question", answer="previous answer")]

    def capture_stream(initial_state, **kwargs):
        captured["history"] = initial_state["history"]
        yield "values", initial_state

    with patch.object(graph_module._compiled_graph, "stream", side_effect=capture_stream):
        list(stream_graph("question", kb_id=1, history=history))

    assert captured["history"] == [
        {"question": "previous question", "answer": "previous answer"},
    ]


def test_rag_followup_accepts_serialized_history():
    history = [{"question": "previous question", "answer": "previous answer"}]
    with patch("agent.graph.route", return_value="rag"), \
         patch("agent.graph._classify", return_value="followup"), \
         patch("chroma_client.query_documents", return_value=[]), \
         patch("agent.graph.generate_answer_stream", return_value=iter(["answer"])):
        events = list(stream_graph("tell me more", kb_id=1, history=history))

    assert events[-1].data["answer"] == "answer"


def test_rag_prompt_accepts_serialized_history():
    prompt = _build_prompt(
        "tell me more",
        context=[],
        history=[{"question": "previous question", "answer": "previous answer"}],
        msg_type="followup",
    )

    assert "User: previous question" in prompt
    assert "Assistant: previous answer" in prompt
