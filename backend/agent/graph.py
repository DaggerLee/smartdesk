"""agent/graph.py — LangGraph skeleton for the router → {direct, rag, agent} dispatch.

W4 migration, skeleton stage only: this module wires the existing router and
path implementations into a StateGraph. It does not change what any path
does — it calls the same functions chat.py already calls (route(), run_agent(),
chroma_client.query_documents(), generate_answer_stream(), ...) and only adds
the graph-level plumbing around them.

W5 T2: the agent path is further split from a single coarse-grained agent_node
(which wrapped run_agent() wholesale) into llm_node / tool_node / rewrite_node /
groundedness_node, expressing the three W2 self-healing mechanisms as graph
nodes and edges instead of an opaque function call. The prompts and tuning
constants (SYSTEM_PROMPT, _MAX_TOOL_FAILURES, _MAX_REWRITES, hint/notice text)
are imported from agent.loop — the single source of truth — not copied, so the
graph and legacy paths can never drift on these values.

Fallback: agent/loop.py (run_agent(), unmodified), agent/router.py, and
routers/chat.py's inline RAG chain remain fully functional on their own and are
what routers/chat.py calls by default. run_graph() is only reached when
SMARTDESK_AGENT_BACKEND=langgraph is set (see routers/chat.py) — production
traffic is on the legacy path unless that switch is flipped.

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
from agent.groundedness import check as _check_groundedness
from agent.loop import (
    SYSTEM_PROMPT,
    _error_parts,
    _GROUNDEDNESS_REVISION_PREFIX,
    _GROUNDEDNESS_REVISION_SUFFIX,
    _MAX_REWRITES,
    _MAX_TOOL_FAILURES,
    _REWRITE_HINT,
    _WRAP_UP_INSTRUCTION,
)
from agent.router import route
from agent.tools.retrieve import RetrieveTool
from agent.tools.web_search import WebSearchTool
from config import MAX_AGENT_TURNS
from gemini_client import generate_answer_stream
from llm.client import complete, model_turn, stream as llm_stream
from llm.trace import span as _trace_span, write as _trace_write
from tools import assess_rag_quality, fetch_weather, is_weather_query, web_search

# Mirrors agent/loop.py's groundedness check, which structurally allows exactly
# one revision pass (no named constant there — the "cap" is implicit in the
# code shape: revise once, accept whatever the recheck says). Named here
# because the graph needs an explicit State counter to enforce it across node
# visits; the value is copied as-is, not retuned.
_MAX_REVISIONS = 1

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

    # agent path — self-healing mechanism state (W5 T2)
    turn: int                       # tool-call round counter, mirrors legacy AgentState.turn
    pending_tool_calls: Optional[list]   # ToolCall list awaiting tool_node execution
    tool_fail_counts: dict[str, int]     # mechanism 1: per-tool-name failure count, capped at _MAX_TOOL_FAILURES
    rewrite_count: int                   # mechanism 2: low-relevance rewrites issued, capped at _MAX_REWRITES
    pending_rewrite: bool                # transient: tool_node found a low-relevance retrieve eligible for rewrite
    revision_count: int                  # mechanism 3: groundedness revisions issued, capped at _MAX_REVISIONS
    pending_revision: bool               # transient: next llm_node call is a groundedness revision (tools=None)
    wrap_up: bool                        # transient: llm_node took the max_turns forced-answer branch

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


def _build_tools_registry(kb_id: int) -> dict:
    return {"retrieve": RetrieveTool(kb_id=kb_id), "web_search": WebSearchTool()}


def llm_node(state: GraphState) -> dict:
    """Gemini call + function-calling decision — the reasoning step of the
    agent path. Also owns the two forced, tool-free completions that don't
    involve a fresh model decision: the groundedness revision continuation
    (mechanism 3) and the max_turns wrap-up answer.
    """
    messages = list(state.get("messages") or [{"role": "user", "parts": [{"text": state["query"]}]}])
    turn = state.get("turn", 0)

    # Mechanism 3 continuation: groundedness_node routed back here after
    # appending a revision prompt. No tool calling on a revision pass.
    if state.get("pending_revision"):
        resp = complete(messages, tools=None, system=SYSTEM_PROMPT)
        return {"answer": resp.text or "", "pending_revision": False}

    # Safety cap reached: force a tool-free wrap-up answer and skip
    # groundedness entirely (mirrors legacy's grounded=None at max_turns exit).
    if turn >= MAX_AGENT_TURNS:
        last = dict(messages[-1])
        last["parts"] = [*last["parts"], {"text": _WRAP_UP_INSTRUCTION}]
        messages[-1] = last
        resp = complete(messages, tools=None, system=SYSTEM_PROMPT)
        return {"messages": messages, "answer": resp.text or "", "grounded": None, "wrap_up": True}

    tools_registry = _build_tools_registry(state["kb_id"])
    declarations = [tool.declaration for tool in tools_registry.values()]
    resp = complete(messages, tools=declarations, system=SYSTEM_PROMPT)

    if resp.tool_calls:
        # Echo the model turn verbatim (raw parts keep the thoughtSignature
        # that gemini-3.5+ requires on replayed functionCall history).
        messages.append(model_turn(resp))
        return {"messages": messages, "pending_tool_calls": resp.tool_calls}

    return {"answer": resp.text or ""}


def _llm_route_edge(state: GraphState) -> str:
    """Route llm_node's output. The final leg (-> groundedness_node) is a
    fixed transition for every genuine final answer: no judge or LLM decides
    whether the check happens, only llm_node's own tool-calls-vs-text /
    wrap_up shape determines which branch is taken.
    """
    if state.get("pending_tool_calls"):
        return "tool_node"
    if state.get("wrap_up"):
        return END
    return "groundedness_node"


def tool_node(state: GraphState) -> dict:
    """Execute the pending tool calls and fold results back into one
    functionResponse turn (Gemini requires every functionResponse from a
    parallel-call round to land in a single user message).

    Mechanism 1 (tool error retry): unknown tool names and exceptions become
    an error functionResponse instead of raising, so the graph routes back to
    llm_node rather than aborting; after _MAX_TOOL_FAILURES an unavailability
    notice is injected too.
    """
    messages = list(state["messages"])
    evidence = list(state.get("evidence") or [])
    tool_fail_counts = dict(state.get("tool_fail_counts") or {})
    rewrite_count = state.get("rewrite_count", 0)

    tools_registry = _build_tools_registry(state["kb_id"])
    pending_rewrite = False
    fr_parts: list[dict] = []

    for tc in state.get("pending_tool_calls") or []:
        if tc.name not in tools_registry:
            tool_fail_counts[tc.name] = tool_fail_counts.get(tc.name, 0) + 1
            err_msg = f"Unknown tool: {tc.name!r}. Available: {list(tools_registry)}"
            _trace_write({
                "type": "tool_error",
                "tool": tc.name,
                "error": err_msg,
                "fail_count": tool_fail_counts[tc.name],
                "unavailable": tool_fail_counts[tc.name] >= _MAX_TOOL_FAILURES,
            })
            fr_parts.extend(_error_parts(tc.name, {"error": err_msg}, tool_fail_counts))
            continue

        try:
            result: dict = tools_registry[tc.name].run(**tc.args)
        except Exception as exc:
            tool_fail_counts[tc.name] = tool_fail_counts.get(tc.name, 0) + 1
            _trace_write({
                "type": "tool_error",
                "tool": tc.name,
                "error": str(exc),
                "fail_count": tool_fail_counts[tc.name],
                "unavailable": tool_fail_counts[tc.name] >= _MAX_TOOL_FAILURES,
            })
            fr_parts.extend(_error_parts(tc.name, {"error": str(exc)}, tool_fail_counts))
            continue

        evidence.extend(result.get("evidence", []))
        fr_parts.append({"functionResponse": {"name": tc.name, "response": result}})

        # Mechanism 2 trigger check (the hint itself is injected by
        # rewrite_node, which can mutate this same turn without starting a
        # new one — see its docstring).
        if tc.name == "retrieve" and not result.get("relevance_ok", True) and rewrite_count < _MAX_REWRITES:
            pending_rewrite = True

    messages.append({"role": "user", "parts": fr_parts})

    return {
        "messages": messages,
        "evidence": evidence,
        "tool_fail_counts": tool_fail_counts,
        "turn": state.get("turn", 0) + 1,
        "pending_tool_calls": None,
        "pending_rewrite": pending_rewrite,
    }


def _tool_route_edge(state: GraphState) -> str:
    return "rewrite_node" if state.get("pending_rewrite") else "llm_node"


def rewrite_node(state: GraphState) -> dict:
    """Mechanism 2: low-relevance retrieval → rewrite hint (capped at
    _MAX_REWRITES). Appends the hint to the functionResponse turn tool_node
    just built by mutating a copy of that message, not by starting a new
    "user" turn — Gemini's contract requires every functionResponse from one
    model turn to land in a single following user message, and a second
    consecutive user-role message would violate that (the exact bug fixed in
    the legacy loop on 2026-07-10 for parallel tool calls).

    Always falls through to llm_node: the actual re-retrieval is LLM-driven
    (the model reads the hint and decides the new query itself), so this node
    cannot "re-search" directly — only llm_node -> tool_node can.
    """
    messages = list(state["messages"])
    rewrite_count = state.get("rewrite_count", 0) + 1

    last = dict(messages[-1])
    last["parts"] = [*last["parts"], {"text": _REWRITE_HINT}]
    messages[-1] = last

    _trace_write({"type": "rewrite_hint", "tool": "retrieve", "rewrite_count": rewrite_count})

    return {"messages": messages, "rewrite_count": rewrite_count, "pending_rewrite": False}


def groundedness_node(state: GraphState) -> dict:
    """Mechanism 3: LLM-as-judge citation groundedness check, with one
    revision pass (capped at _MAX_REVISIONS) if unsupported claims are found.
    Reached unconditionally for every real final answer (see
    _llm_route_edge) — this node decides what happens *after* the check runs,
    never whether it runs at all.
    """
    answer = state.get("answer") or ""
    evidence = state.get("evidence") or []
    revision_count = state.get("revision_count", 0)

    with _trace_span({"type": "groundedness_check"}) as _t:
        grounded = _check_groundedness(answer, evidence)
        _t["supported"] = grounded["supported"]
        _t["unsupported_count"] = len(grounded["unsupported_sentences"])
        _t["unsupported_sentences"] = grounded["unsupported_sentences"]

    if grounded["supported"] or revision_count >= _MAX_REVISIONS:
        return {"grounded": grounded["supported"]}

    messages = list(state["messages"])
    unsupported_list = "\n".join(f"- {s}" for s in grounded["unsupported_sentences"])
    revision_msg = _GROUNDEDNESS_REVISION_PREFIX + unsupported_list + _GROUNDEDNESS_REVISION_SUFFIX
    messages.append({"role": "user", "parts": [{"text": revision_msg}]})

    return {
        "messages": messages,
        "revision_count": revision_count + 1,
        "pending_revision": True,
    }


def _groundedness_route_edge(state: GraphState) -> str:
    return "llm_node" if state.get("pending_revision") else END


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("classify", classify_node)
    graph.add_node("direct", direct_node)
    graph.add_node("rag", rag_node)
    graph.add_node("llm_node", llm_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("rewrite_node", rewrite_node)
    graph.add_node("groundedness_node", groundedness_node)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        _route_edge,
        {"direct": "direct", "rag": "rag", "agent": "llm_node"},
    )
    graph.add_edge("direct", END)
    graph.add_edge("rag", END)

    graph.add_conditional_edges(
        "llm_node",
        _llm_route_edge,
        {"tool_node": "tool_node", "groundedness_node": "groundedness_node", END: END},
    )
    graph.add_conditional_edges(
        "tool_node",
        _tool_route_edge,
        {"rewrite_node": "rewrite_node", "llm_node": "llm_node"},
    )
    graph.add_edge("rewrite_node", "llm_node")
    graph.add_conditional_edges(
        "groundedness_node",
        _groundedness_route_edge,
        {"llm_node": "llm_node", END: END},
    )

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
