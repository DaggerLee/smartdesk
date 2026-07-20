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

W5 T3: stream_graph() is the new streaming entry point — it drives
_compiled_graph.stream(..., stream_mode=["custom", "values"]) and yields
GraphEvent objects that routers/chat.py's langgraph branch translates into SSE
frames, one-to-one with what agent.loop.run_agent()'s AgentEvent stream and
chat.py's direct/rag chunk loops already produce for the legacy paths:
  - "tool_call"  — llm_node, one per pending tool call, emitted via
                   get_stream_writer() at the same point run_agent() yields
                   AgentEvent(type="tool_call", ...): before the call runs.
  - "chunk"      — direct_node/rag_node, one per text chunk from
                   llm_stream()/generate_answer_stream(), emitted live as the
                   node consumes the underlying stream. Legacy has no
                   AgentEvent for this because chat.py loops over those
                   generators itself; the graph unifies all three paths behind
                   one stream_graph() generator, so this event carries that
                   inline chunking across the graph boundary.
  - "final"      — the last "values" snapshot (the completed GraphState).
                   chat.py uses it to run the same two-stage finish the legacy
                   agent path uses (re-stream via llm_stream(final_messages) —
                   see agent/loop.py's run_agent() docstring / chat.py's
                   generate_agent(), which discards run_agent()'s own
                   groundedness-checked text and regenerates via a second
                   streaming call over the same message history; stream_graph()
                   preserves that exact behavior, not a replay of state["answer"])
                   and to build the rag path's sources SSE frame from
                   used_docs/used_web + doc_sources/web_results.
run_graph() keeps its pre-T3 synchronous contract (returns the final
GraphState dict) for existing callers — tests/test_graph_self_healing.py
subscripts the return value directly — by draining stream_graph() and
discarding the "tool_call"/"chunk" events; both entry points run the exact
same compiled graph, so there is one execution path, not two.

Fallback: agent/loop.py (run_agent(), unmodified), agent/router.py, and
routers/chat.py's inline RAG chain remain fully functional on their own and are
what routers/chat.py calls by default. The graph is only reached when
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
from dataclasses import dataclass
from typing import Generator, Optional, TypedDict

from langgraph.config import get_stream_writer
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


# ── Streaming event type ────────────────────────────────────────────────────
#
# Mirrors agent.loop.AgentEvent's role (a typed envelope the SSE layer
# switches on) but with the type vocabulary stream_graph() actually emits —
# see the module docstring's event-mapping list.

@dataclass
class GraphEvent:
    type: str   # "tool_call" | "chunk" | "final"
    data: dict


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
    used_docs: bool    # [SOURCE_USED] marker seen in the raw generated text
    used_web: bool     # [WEB_USED] marker seen in the raw generated text

    answer: str


# ── Nodes ─────────────────────────────────────────────────────────────────────

def classify_node(state: GraphState) -> dict:
    """Run the existing router LLM call and store its decision in State."""
    return {"route": route(state["query"])}


def _route_edge(state: GraphState) -> str:
    """Conditional edge: pure read of state['route'], no side effects."""
    return state["route"]


def direct_node(state: GraphState) -> dict:
    """Mirrors chat.py's direct path: plain streamed reply, no retrieval.

    Forwards each chunk through get_stream_writer() as it arrives (stream_mode
    "custom") so stream_graph() callers see the same live, one-stage token
    streaming chat.py's legacy generate_direct() gives the SSE layer — the
    node doesn't buffer the whole answer before the caller can start reading.
    """
    writer = get_stream_writer()
    msgs = [{"role": "user", "parts": [{"text": state["query"]}]}]
    chunks: list[str] = []
    for chunk in llm_stream(msgs):
        chunks.append(chunk)
        writer({"kind": "chunk", "text": chunk})
    return {"answer": "".join(chunks)}


def rag_node(state: GraphState) -> dict:
    """Mirrors chat.py's inline v1 RAG chain (classify -> retrieve -> optional
    web/weather fallback -> generate_answer_stream), calling the same
    underlying functions chat.py calls.

    Streams each raw chunk through get_stream_writer() as it arrives, same as
    direct_node — one-stage streaming, no graph-side buffering. [SOURCE_USED]/
    [WEB_USED] marker detection (used_docs/used_web) happens here, on the raw
    joined text, before stripping — mirroring where chat.py's legacy generate()
    does the same detection+strip on its own joined chunks. Doing it in the
    node keeps that business logic where the rest of the RAG chain logic
    already lives (see module docstring's "known duplication" note); the SSE
    layer only reads used_docs/used_web + doc_sources/web_results to decide
    whether to emit a sources frame, it doesn't re-derive them.
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

    writer = get_stream_writer()
    raw_chunks: list[str] = []
    for chunk in generate_answer_stream(query, context_texts, history, web_results or None, msg_type):
        raw_chunks.append(chunk)
        writer({"kind": "chunk", "text": chunk})
    raw_answer = "".join(raw_chunks)

    used_docs = "[SOURCE_USED]" in raw_answer
    used_web = "[WEB_USED]" in raw_answer
    clean_answer = raw_answer.replace("[SOURCE_USED]", "").replace("[WEB_USED]", "").rstrip()

    return {
        "msg_type": msg_type,
        "context_texts": context_texts,
        "doc_sources": doc_sources,
        "web_results": web_results,
        "used_docs": used_docs,
        "used_web": used_web,
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
        # Emit one "tool_call" custom event per call, before any of them run —
        # same timing as run_agent()'s yield AgentEvent(type="tool_call", ...),
        # which happens per-tc before that tc's tool.run(). tool_node executes
        # the calls in the next graph step; legacy has no SSE-visible
        # "tool_result" equivalent either (chat.py's generate_agent() only
        # switches on "tool_call"), so tool_node doesn't need to emit anything.
        writer = get_stream_writer()
        for tc in resp.tool_calls:
            writer({"kind": "tool_call", "name": tc.name, "args": tc.args})

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


def stream_graph(
    query: str, kb_id: int, history: Optional[list] = None
) -> Generator[GraphEvent, None, None]:
    """Streaming entry point: run the full router -> path graph for one query,
    yielding GraphEvents as nodes produce them (see module docstring's
    event-mapping list) instead of blocking until the graph completes.

    stream_mode=["custom", "values"]: "custom" carries the fine-grained
    tool_call/chunk events nodes push via get_stream_writer() — the graph
    equivalent of run_agent()'s hand-written `yield AgentEvent(...)` points.
    "values" carries the full GraphState after every superstep; only the last
    one (the completed state) is used, as the "final" event. stream_mode
    "messages" isn't an option here — it taps token events from LangChain
    BaseChatModel invocations, and no node calls one (all LLM calls go through
    llm/client.py's plain Gemini REST wrapper, per the single-client-module
    constraint in CLAUDE.md). "updates" was also considered and rejected: it
    would hand back each node's partial-state diff separately, requiring the
    caller to manually re-merge ~7 nodes' worth of diffs to reconstruct what
    "values" already assembles for free.
    """
    initial_state: GraphState = {"query": query, "kb_id": kb_id, "history": history or []}
    final_state: GraphState = dict(initial_state)  # type: ignore[assignment]

    for mode, chunk in _compiled_graph.stream(initial_state, stream_mode=["custom", "values"]):
        if mode == "custom":
            kind = chunk["kind"]
            yield GraphEvent(type=kind, data={k: v for k, v in chunk.items() if k != "kind"})
        else:  # mode == "values"
            final_state.update(chunk)

    yield GraphEvent(type="final", data=final_state)


def run_graph(query: str, kb_id: int, history: Optional[list] = None) -> GraphState:
    """Alternate entry point: run the full router -> path graph for one query,
    blocking until it completes.

    Returns the final GraphState (includes "route" and "answer" plus whatever
    fields the chosen path populated). route()/run_agent()/chat.py's RAG chain
    remain the fallback — nothing here deletes or alters them.

    Implemented on top of stream_graph() (draining it and keeping only the
    "final" event) rather than a separate _compiled_graph.invoke() call, so
    there is exactly one graph-execution code path behind both entry points —
    kept for existing callers (tests/test_graph_self_healing.py) that expect a
    synchronous dict return rather than a generator.
    """
    final_state: GraphState = {}
    for event in stream_graph(query, kb_id, history=history):
        if event.type == "final":
            final_state = event.data  # type: ignore[assignment]
    return final_state
