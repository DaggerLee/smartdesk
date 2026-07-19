"""agent/graph.py — LangGraph skeleton for the router → {direct, rag, agent} dispatch.

W4 migration, skeleton stage only: this module wires the existing router and
path implementations into a StateGraph. It does not change what any path
does — it calls the same functions chat.py already calls (route(), run_agent(),
chroma_client.query_documents(), generate_answer_stream(), ...) and only adds
the graph-level plumbing around them.

Fallback: agent/loop.py, agent/router.py, and routers/chat.py's inline RAG
chain are unmodified and remain fully functional on their own. run_graph()
here is an alternate entry point, not a replacement.

Known duplication (intentional, flagged for a later cleanup pass): the RAG
branch logic in rag_node() mirrors routers/chat.py's inline chain rather than
calling a shared function, because that chain isn't factored out of chat.py
yet and this session is scoped to the graph skeleton, not to refactoring
chat.py. For the same reason, _classify() below is a copy of
routers/chat.py's _classify rather than an import from it — importing across
into the router layer would pull FastAPI/SQLAlchemy/auth into the agent
layer's import graph merely for a regex helper, and this environment's
FastAPI/Starlette combo currently fails at import time regardless
(unrelated pre-existing version mismatch, not part of this migration).
"""

from __future__ import annotations

import re
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

import chroma_client
from agent.loop import run_agent
from agent.router import route
from gemini_client import generate_answer_stream
from llm.client import stream as llm_stream
from tools import assess_rag_quality, fetch_weather, is_weather_query, web_search

# Copy of routers/chat.py's _classify — see module docstring for why this
# isn't an import.
_CONVERSATIONAL_RE = re.compile(
    r"^(thanks?|thank you|thx|ok|okay|got it|understood|makes sense|great|cool|nice|"
    r"perfect|awesome|sure|alright|yep|yup|nope|"
    r"hi|hello|hey|bye|goodbye|"
    r"谢谢|谢了|好的|好|明白|了解|嗯|知道了|收到|没问题|可以|行|对|是的|"
    r"你好|哈喽|再见|👍|👌)[\s!?.。！？]*$",
    re.IGNORECASE,
)

_FOLLOWUP_RE = re.compile(
    r"\b(that|it|this|those|them|above|the previous|the last|"
    r"tell me more|elaborate|more detail|more about|expand on|explain more|"
    r"can you explain|what do you mean|what does that mean)\b"
    r"|继续|更多|详细|展开|解释|说说|刚才|上面|再说|能不能再",
    re.IGNORECASE,
)

_FORMAT_RE = re.compile(
    r"\b(shorter|longer|simpler|summarize|summary|bullet|table|list|rewrite|rephrase|"
    r"more concise|step by step|in points|"
    r"reply in|answer in|respond in|switch to|change.*language)\b"
    r"|用.{1,4}[语文]|换.{0,3}语言|切换.{0,3}语言"
    r"|简短|总结|列表|表格|重写|换一种|分点|分步|简洁",
    re.IGNORECASE,
)


def _classify(message: str) -> str:
    m = message.strip()
    if _CONVERSATIONAL_RE.match(m):
        return "conversational"
    if _FORMAT_RE.search(m):
        return "meta"
    if _FOLLOWUP_RE.search(m):
        return "followup"
    return "question"


# ── State schema ────────────────────────────────────────────────────────────
#
# Fields map 1:1 onto what already flows through the three existing paths:
#   - messages/evidence/grounded  <- agent.state.AgentState (agent path)
#   - msg_type/context_texts/doc_sources/web_results <- chat.py's inline RAG
#     chain locals (rag path)
#   - route  <- router.route() decision, read by the conditional edge
#   - answer <- convergence point all three path nodes write to

class GraphState(TypedDict, total=False):
    query: str
    kb_id: int
    history: list  # Conversation ORM rows (rag path only), oldest first

    route: str  # "direct" | "rag" | "agent"

    # agent path
    messages: list[dict]
    evidence: list[dict]
    grounded: Optional[bool]

    # rag path
    msg_type: str
    context_texts: list[str]
    doc_sources: list[dict]
    web_results: list[dict]

    answer: str


# ── Nodes ─────────────────────────────────────────────────────────────────────

def classify_node(state: GraphState) -> dict:
    """Run the existing router LLM call and store its decision in State."""
    return {"route": route(state["query"])}


def _route_edge(state: GraphState) -> str:
    """Conditional edge: pure read of state['route'], no side effects."""
    return state["route"]


def direct_node(state: GraphState) -> dict:
    """Mirrors chat.py's direct path: plain streamed reply, no retrieval."""
    msgs = [{"role": "user", "parts": [{"text": state["query"]}]}]
    answer = "".join(llm_stream(msgs))
    return {"answer": answer}


def rag_node(state: GraphState) -> dict:
    """Mirrors chat.py's inline v1 RAG chain (classify -> retrieve -> optional
    web/weather fallback -> generate_answer_stream), calling the same
    underlying functions chat.py calls.
    """
    query = state["query"]
    kb_id = state["kb_id"]
    history = state.get("history") or []

    msg_type = _classify(query)

    context_texts: list[str] = []
    doc_sources: list[dict] = []
    web_results: list[dict] = []

    if msg_type == "conversational":
        pass

    elif msg_type in ("meta", "followup") and history:
        rag_query = history[-1].question
        results = chroma_client.query_documents(kb_id, rag_query, n_results=5)
        context_texts = [r["text"] for r in results]
        seen_files: set = set()
        for r in results:
            fname = r["filename"]
            if fname != "Unknown" and fname not in seen_files:
                seen_files.add(fname)
                doc_sources.append({
                    "type": "document",
                    "filename": fname,
                    "preview": r["text"][:80].replace("\n", " "),
                })

    else:
        results = chroma_client.query_documents(kb_id, query, n_results=5)
        context_texts = [r["text"] for r in results]
        seen_files = set()
        for r in results:
            fname = r["filename"]
            if fname != "Unknown" and fname not in seen_files:
                seen_files.add(fname)
                doc_sources.append({
                    "type": "document",
                    "filename": fname,
                    "preview": r["text"][:80].replace("\n", " "),
                })

        if not assess_rag_quality(results):
            if is_weather_query(query):
                weather_summary = fetch_weather(query)
                if weather_summary:
                    web_results = [{"title": "Real-time weather data", "url": "", "snippet": weather_summary}]
                else:
                    web_results = web_search(query)
            else:
                web_results = web_search(query)

    raw_answer = "".join(
        generate_answer_stream(query, context_texts, history, web_results or None, msg_type)
    )
    clean_answer = raw_answer.replace("[SOURCE_USED]", "").replace("[WEB_USED]", "").rstrip()

    return {
        "msg_type": msg_type,
        "context_texts": context_texts,
        "doc_sources": doc_sources,
        "web_results": web_results,
        "answer": clean_answer,
    }


def agent_node(state: GraphState) -> dict:
    """Calls run_agent() (existing loop, unmodified) and takes its final event."""
    final_data: Optional[dict] = None
    for event in run_agent(state["query"], state["kb_id"]):
        if event.type == "final":
            final_data = event.data

    assert final_data is not None, "run_agent() ended without yielding a final event"
    return {
        "messages": final_data["messages"],
        "evidence": final_data["evidence"],
        "grounded": final_data["grounded"],
        "answer": final_data["text"],
    }


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("classify", classify_node)
    graph.add_node("direct", direct_node)
    graph.add_node("rag", rag_node)
    graph.add_node("agent", agent_node)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        _route_edge,
        {"direct": "direct", "rag": "rag", "agent": "agent"},
    )
    graph.add_edge("direct", END)
    graph.add_edge("rag", END)
    graph.add_edge("agent", END)

    return graph.compile()


_compiled_graph = build_graph()


def run_graph(query: str, kb_id: int, history: Optional[list] = None) -> GraphState:
    """Alternate entry point: run the full router -> path graph for one query.

    Returns the final GraphState (includes "route" and "answer" plus whatever
    fields the chosen path populated). route()/run_agent()/chat.py's RAG chain
    remain the fallback — nothing here deletes or alters them.
    """
    initial_state: GraphState = {"query": query, "kb_id": kb_id, "history": history or []}
    return _compiled_graph.invoke(initial_state)
