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

W5 T4: build_graph() now compiles with a checkpointer (SqliteSaver on
data/checkpoints.sqlite by default, injectable for tests) — the foundation
for HITL interrupt/resume. LangGraph checkpoints once per completed
superstep, not per node statement: a crash mid-node loses only that node's
in-flight work, and resume (_compiled_graph.stream(None, config)) re-executes
exactly the superstep that never committed, not anything before it. Every
.stream() call here passes durability="sync" explicitly — the default
("async") only guarantees the checkpoint is written before the *run*
finishes, not before the *next superstep* starts, which would undermine the
"already-committed supersteps never replay" guarantee this module's
idempotency reasoning (and resume_graph(), below) depends on.

thread_id is scoped one-per-turn (one query = one graph run = eventually one
Conversation row), not to kb_id or a user session: GraphState fields like
messages/evidence/turn/tool_fail_counts have no reducer (default overwrite),
and stream_graph()'s initial_state only sets query/kb_id/history. Reusing a
thread_id across turns would let a prior turn's leftover messages/turn/
evidence survive in the checkpoint and bleed into the next turn's initial
state (nothing overwrites those keys), corrupting turn counting and mixing
unrelated evidence. The existing multi-turn memory (history, loaded from the
Conversation table) already doesn't depend on graph-internal state surviving
across turns, so per-turn thread_id costs nothing. stream_graph()/run_graph()
accept an optional thread_id and auto-generate one (uuid4) when omitted, so
every existing caller is unaffected. Conversation gets no new column in this
task — no acceptance criterion exercises a persisted thread_id<->Conversation
mapping, and HITL (which would actually consume it) is separate future work;
adding it now would be speculative structure.

tool_node's per-tool-call trace_write() calls (mechanism 1's error logging)
were inline inside its for-loop; batched instead (see tool_node) — a crash
partway through the loop used to leave already-logged trace entries on disk
while the node's own checkpoint never committed, so resume re-ran the whole
loop and re-logged them, double-counting errors for W4-style error analysis.
Deferring every trace_write() to one point right before return means a
partial run persists nothing (nothing survives an interruption to duplicate)
and a completed run persists each event at most once — a crash in the
narrow window between the flush loop and the node's own checkpoint commit
is still possible in principle (the same residual race every other node's
single trace/span write already carries, see rewrite_node/groundedness_node
below), but the wide, easy-to-hit window (partway through the tool-call loop
itself) is what actually mattered here and is fully closed. Chosen over
giving trace entries a dedupable id because it fixes the duplication at the
source instead of pushing a reconciliation step onto every downstream trace
reader.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Generator, Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter, ValidationError

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
from agent.tools.write_note import WriteNoteTool
from agent.write_action import (
    ActionReceipt,
    ActionResolution,
    PendingAction,
    render_action_answer,
    to_action_evidence,
    validate_write_note_payload,
)
from agent.write_note_policy import (
    WRITE_NOTE_POLICY,
    classify_write_intent,
    is_hitl_write_note_enabled,
)
from config import CHECKPOINT_DB_PATH, MAX_AGENT_TURNS, WRITE_NOTE_ROOT
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
    history: list[dict[str, str]]  # Checkpoint-safe turns, oldest first

    route: str  # "direct" | "rag" | "agent"

    # agent path
    messages: list[dict]
    evidence: list[dict]
    grounded: Optional[bool]
    verification_status: str
    verification_source: str
    user_id: int
    thread_id: str
    write_intent: str
    pending_action: dict
    write_action_seen: bool
    invalid_write_round: bool

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
        rag_query = history[-1]["question"]
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


_WRITE_NOTE_DECLARATION = {
    "name": "write_note",
    "description": "Propose saving a Markdown note for explicit user approval.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["title", "content"],
    },
}


def _write_protocol_failure(
    state: GraphState,
    messages: list[dict],
    resp,
) -> dict:
    tool_fail_counts = dict(state.get("tool_fail_counts") or {})
    failure_count = tool_fail_counts.get("write_protocol", 0) + 1
    tool_fail_counts["write_protocol"] = failure_count
    error = (
        "The write_note tool-call round was invalid. No tools were executed. "
        "Retry with exactly one write_note call containing only a valid title and content."
    )
    updated_messages = list(messages)
    updated_messages.append(model_turn(resp))
    updated_messages.append(
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": tc.name,
                        "response": {"error": error},
                    }
                }
                for tc in resp.tool_calls
            ],
        }
    )
    _trace_write(
        {
            "type": "tool_error",
            "tool": "write_protocol",
            "fail_count": failure_count,
            "unavailable": failure_count >= _MAX_TOOL_FAILURES,
        }
    )
    capped = failure_count >= _MAX_TOOL_FAILURES
    return {
        "messages": updated_messages,
        "pending_tool_calls": None,
        "tool_fail_counts": tool_fail_counts,
        "invalid_write_round": not capped,
        "wrap_up": capped,
        "answer": error if capped else "",
        "verification_status": "rejected" if capped else "pending",
        "verification_source": "llm_groundedness" if capped else None,
    }


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
        return {
            "messages": messages,
            "answer": resp.text or "",
            "grounded": None,
            "verification_status": "unchecked_max_turns",
            "wrap_up": True,
        }

    tools_registry = _build_tools_registry(state["kb_id"])
    declarations = [tool.declaration for tool in tools_registry.values()]
    write_enabled = (
        is_hitl_write_note_enabled()
        and state.get("write_intent") == "persist"
        and bool(state.get("user_id"))
        and not state.get("write_action_seen", False)
    )
    if write_enabled:
        declarations.append(_WRITE_NOTE_DECLARATION)
    system_prompt = SYSTEM_PROMPT + ("\n" + WRITE_NOTE_POLICY if write_enabled else "")
    resp = complete(messages, tools=declarations, system=system_prompt)

    if resp.tool_calls:
        write_calls = [tc for tc in resp.tool_calls if tc.name == "write_note"]
        if write_calls and not (len(resp.tool_calls) == 1 and len(write_calls) == 1):
            return _write_protocol_failure(state, messages, resp)
        if len(resp.tool_calls) == 1 and len(write_calls) == 1:
            call = write_calls[0]
            try:
                payload = validate_write_note_payload(**call.args)
            except (ValidationError, TypeError):
                return _write_protocol_failure(state, messages, resp)
            messages.append(model_turn(resp))
            pending = PendingAction(
                action_id=uuid.uuid4().hex,
                user_id=state["user_id"],
                original_payload=payload,
            )
            return {
                "messages": messages,
                "pending_tool_calls": None,
                "pending_action": pending.model_dump(),
                "write_action_seen": True,
                "invalid_write_round": False,
            }
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
        return {
            "messages": messages,
            "pending_tool_calls": resp.tool_calls,
            "invalid_write_round": False,
        }

    messages.append(model_turn(resp))
    return {
        "messages": messages,
        "answer": resp.text or "",
        "invalid_write_round": False,
    }


def _llm_route_edge(state: GraphState) -> str:
    """Route llm_node's output. The final leg (-> groundedness_node) is a
    fixed transition for every genuine final answer: no judge or LLM decides
    whether the check happens, only llm_node's own tool-calls-vs-text /
    wrap_up shape determines which branch is taken.
    """
    if (state.get("pending_action") or {}).get("status") == "proposed":
        return "approval_gate"
    if state.get("pending_tool_calls"):
        return "tool_node"
    if state.get("wrap_up"):
        return END
    if state.get("invalid_write_round"):
        return "llm_node"
    return "groundedness_node"


def tool_node(state: GraphState) -> dict:
    """Execute the pending tool calls and fold results back into one
    functionResponse turn (Gemini requires every functionResponse from a
    parallel-call round to land in a single user message).

    Mechanism 1 (tool error retry): unknown tool names and exceptions become
    an error functionResponse instead of raising, so the graph routes back to
    llm_node rather than aborting; after _MAX_TOOL_FAILURES an unavailability
    notice is injected too.

    W5 T4 idempotency fix: trace_write() calls are collected in trace_events
    and flushed in one batch right before return, instead of firing inline
    per tool call inside the loop below. Checkpointing commits once per
    completed superstep — if the process is interrupted partway through this
    loop, this node's own checkpoint never commits and resume re-runs the
    whole loop from scratch. Inline writes would have already persisted for
    tool calls processed before the interruption, so the re-run would persist
    them a second time (double-counting tool errors for W4-style error
    analysis). Batching to one point means a partial run persists nothing
    (nothing survives the interruption to duplicate) and a completed run
    persists each event at most once — the event *content* is unchanged,
    only the timing of the side effect moves.
    """
    messages = list(state["messages"])
    evidence = list(state.get("evidence") or [])
    tool_fail_counts = dict(state.get("tool_fail_counts") or {})
    rewrite_count = state.get("rewrite_count", 0)

    tools_registry = _build_tools_registry(state["kb_id"])
    pending_rewrite = False
    fr_parts: list[dict] = []
    trace_events: list[dict] = []

    for tc in state.get("pending_tool_calls") or []:
        if tc.name not in tools_registry:
            tool_fail_counts[tc.name] = tool_fail_counts.get(tc.name, 0) + 1
            err_msg = f"Unknown tool: {tc.name!r}. Available: {list(tools_registry)}"
            trace_events.append({
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
            trace_events.append({
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
    for event in trace_events:
        _trace_write(event)

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


def approval_gate(state: GraphState) -> dict:
    pending = state["pending_action"]
    raw_resolution = interrupt(
        {
            "action_id": pending["action_id"],
            "tool": pending["tool"],
            "title": pending["original_payload"]["title"],
            "content": pending["original_payload"]["content"],
        }
    )
    resolution = TypeAdapter(ActionResolution).validate_python(raw_resolution)
    if resolution.action_id != pending["action_id"]:
        raise ValueError("resolution action_id does not match pending action")

    updated = dict(pending)
    updated["decision"] = resolution.decision
    if resolution.decision == "reject":
        receipt = ActionReceipt(action_id=pending["action_id"], result="rejected")
        updated.update(
            status="rejected",
            reject_reason=resolution.reason,
            approved_payload=None,
            receipt=receipt.model_dump(),
        )
        return {
            "pending_action": updated,
            "messages": _append_action_response(state["messages"], receipt),
        }

    if resolution.decision == "approve":
        approved = validate_write_note_payload(**pending["original_payload"])
    else:
        approved = validate_write_note_payload(
            title=resolution.title,
            content=resolution.content,
        )
    updated.update(
        status="approved",
        approved_payload=approved.model_copy(deep=True).model_dump(),
        reject_reason=None,
    )
    return {"pending_action": updated}


def _append_action_response(messages: list[dict], receipt: ActionReceipt) -> list[dict]:
    updated = list(messages)
    updated.append(
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "write_note",
                        "response": receipt.model_dump(),
                    }
                }
            ],
        }
    )
    return updated


def _approval_route_edge(state: GraphState) -> str:
    status = state["pending_action"]["status"]
    return "action_finalize_node" if status == "rejected" else "write_action_node"


def write_action_node(state: GraphState) -> dict:
    pending = dict(state["pending_action"])
    approved = pending["approved_payload"]
    receipt = WriteNoteTool(
        user_id=pending["user_id"],
        action_id=pending["action_id"],
        notes_root=WRITE_NOTE_ROOT,
    ).run(title=approved["title"], content=approved["content"])
    pending.update(status=receipt.result, receipt=receipt.model_dump())
    return {
        "pending_action": pending,
        "messages": _append_action_response(state["messages"], receipt),
    }


def action_finalize_node(state: GraphState) -> dict:
    pending = state["pending_action"]
    receipt = ActionReceipt.model_validate(pending["receipt"])
    if receipt.result in {"succeeded", "replayed"} and not (
        receipt.read_back_verified
        and receipt.relative_path
        and receipt.content_hash
        and receipt.byte_count is not None
    ):
        raise ValueError("write receipt is not deterministically verified")
    evidence = to_action_evidence(receipt)
    _trace_write(evidence)
    language = "zh" if re.search(r"[\u4e00-\u9fff]", state["query"]) else "en"
    return {
        "answer": render_action_answer(receipt, language=language),
        "evidence": [evidence],
        "verification_status": "verified",
        "verification_source": "action_receipt",
        "grounded": True,
    }


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
        status = grounded.get("verification_status")
        if status is None:
            status = "verified" if grounded["supported"] else "rejected"
        _t["supported"] = grounded["supported"]
        _t["unsupported_count"] = len(grounded["unsupported_sentences"])
        _t["unsupported_sentences"] = grounded["unsupported_sentences"]
        _t["verification_status"] = status

    if status != "rejected" or revision_count >= _MAX_REVISIONS:
        return {
            "grounded": grounded["supported"],
            "verification_status": status,
            "verification_source": "llm_groundedness",
        }

    messages = list(state["messages"])
    unsupported_list = "\n".join(f"- {s}" for s in grounded["unsupported_sentences"])
    revision_msg = _GROUNDEDNESS_REVISION_PREFIX + unsupported_list + _GROUNDEDNESS_REVISION_SUFFIX
    messages.append({"role": "user", "parts": [{"text": revision_msg}]})

    return {
        "messages": messages,
        "revision_count": revision_count + 1,
        "pending_revision": True,
        "verification_status": status,
        "verification_source": "llm_groundedness",
    }


def _groundedness_route_edge(state: GraphState) -> str:
    return "llm_node" if state.get("pending_revision") else END


# ── Graph assembly ────────────────────────────────────────────────────────────

def _default_checkpointer() -> SqliteSaver:
    """Production checkpointer: a real sqlite file under data/, separate from
    the business DB (database.py's smartdesk.db). Tests build their own graph
    via build_graph(checkpointer=...) instead of touching this file — see
    tests/conftest.py, which also redirects CHECKPOINT_DB_PATH itself so the
    module-level singleton below never writes into the production file
    during a test run.
    """
    checkpoint_dir = os.path.dirname(CHECKPOINT_DB_PATH)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
    conn = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
    return SqliteSaver(conn)


def build_graph(checkpointer=None):
    graph = StateGraph(GraphState)
    graph.add_node("classify", classify_node)
    graph.add_node("direct", direct_node)
    graph.add_node("rag", rag_node)
    graph.add_node("llm_node", llm_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("rewrite_node", rewrite_node)
    graph.add_node("groundedness_node", groundedness_node)
    graph.add_node("write_action_node", write_action_node)
    graph.add_node("action_finalize_node", action_finalize_node)

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
        {
            "approval_gate": "approval_gate",
            "llm_node": "llm_node",
            "tool_node": "tool_node",
            "groundedness_node": "groundedness_node",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "approval_gate",
        _approval_route_edge,
        {"write_action_node": "write_action_node", "action_finalize_node": "action_finalize_node"},
    )
    graph.add_edge("write_action_node", "action_finalize_node")
    graph.add_edge("action_finalize_node", END)
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

    return graph.compile(checkpointer=checkpointer or _default_checkpointer())


_compiled_graph = build_graph()


def _serialize_history(history: list) -> list[dict[str, str]]:
    """Convert database/history objects into checkpoint-safe plain data."""
    serialized = []
    for turn in history:
        if isinstance(turn, dict):
            question = turn["question"]
            answer = turn["answer"]
        else:
            question = turn.question
            answer = turn.answer
        serialized.append({"question": question, "answer": answer})
    return serialized


def stream_graph(
    query: str,
    kb_id: int,
    history: Optional[list] = None,
    thread_id: Optional[str] = None,
    user_id: Optional[int] = None,
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

    thread_id: one graph run = one thread (see module docstring's "thread_id
    is scoped one-per-turn" note). Auto-generated when omitted so every
    existing caller is unaffected; callers that need to resume a specific run
    later (resume_graph()) must generate and hold onto their own id instead of
    relying on the default. durability="sync" makes every checkpoint durable
    before the next superstep starts — required for the "already-committed
    supersteps never replay" guarantee the idempotency audit depends on (see
    module docstring).
    """
    resolved_thread_id = thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": resolved_thread_id}}
    initial_state: GraphState = {
        "query": query,
        "kb_id": kb_id,
        "history": _serialize_history(history or []),
        "thread_id": resolved_thread_id,
        "write_intent": classify_write_intent(query),
    }
    final_state: GraphState = dict(initial_state)  # type: ignore[assignment]
    if user_id is not None:
        initial_state["user_id"] = user_id

    for mode, chunk in _compiled_graph.stream(
        initial_state, config=config, stream_mode=["custom", "values"], durability="sync"
    ):
        if mode == "custom":
            kind = chunk["kind"]
            yield GraphEvent(type=kind, data={k: v for k, v in chunk.items() if k != "kind"})
        else:  # mode == "values"
            final_state.update(chunk)

    snapshot = _compiled_graph.get_state(config)
    pending = snapshot.values.get("pending_action")
    if snapshot.next == ("approval_gate",) and pending:
        proposal = pending["original_payload"]
        yield GraphEvent(
            type="confirmation_required",
            data={
                "action_id": pending["action_id"],
                "tool": pending["tool"],
                "title": proposal["title"],
                "content": proposal["content"],
            },
        )
        return
    yield GraphEvent(type="final", data=final_state)


def run_graph(
    query: str,
    kb_id: int,
    history: Optional[list] = None,
    thread_id: Optional[str] = None,
) -> GraphState:
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
    for event in stream_graph(query, kb_id, history=history, thread_id=thread_id):
        if event.type == "final":
            final_state = event.data  # type: ignore[assignment]
    return final_state


def resume_graph(thread_id: str) -> GraphState:
    """Resume a graph run that was interrupted (crash, or — the point of this
    task — a future HITL pause) before it reached END, continuing from its
    last committed checkpoint rather than restarting from classify_node.

    Passing None as input is LangGraph's own signal to continue a thread's
    pending tasks instead of starting a fresh run (see
    langgraph.pregel._loop.Loop._first(): "None input: resume after
    interrupt"). Only the superstep that never committed re-executes; every
    already-committed superstep (and its side effects) is not replayed — see
    the module docstring's per-node idempotency audit for which nodes that is
    safe for. Not layered on stream_graph()/run_graph() (unlike run_graph on
    stream_graph) because there is no live SSE caller to feed "custom" events
    to during a resume in this task's scope — chat.py isn't wired to call
    this yet; that wiring is future HITL-endpoint work.
    """
    config = {"configurable": {"thread_id": thread_id}}
    final_state: GraphState = {}
    for chunk in _compiled_graph.stream(None, config=config, stream_mode="values", durability="sync"):
        final_state = chunk  # type: ignore[assignment]
    return final_state


def resume_graph_action(
    thread_id: str,
    resolution: ActionResolution | dict,
) -> Generator[GraphEvent, None, None]:
    validated = TypeAdapter(ActionResolution).validate_python(resolution)
    config = {"configurable": {"thread_id": thread_id}}
    final_state: GraphState = {}
    for chunk in _compiled_graph.stream(
        Command(resume=validated.model_dump()),
        config=config,
        stream_mode="values",
        durability="sync",
    ):
        final_state = chunk  # type: ignore[assignment]

    snapshot = _compiled_graph.get_state(config)
    pending = snapshot.values.get("pending_action")
    if not pending or not pending.get("receipt"):
        raise RuntimeError("action resolution did not produce a committed receipt")
    if snapshot.next:
        raise RuntimeError("action graph did not reach a terminal state")
    receipt = ActionReceipt.model_validate(pending["receipt"])
    yield GraphEvent(type="action_result", data=receipt.model_dump())
    yield GraphEvent(type="final", data=dict(snapshot.values))


def get_graph_snapshot(thread_id: str) -> dict | None:
    """Return the latest durable state for one graph thread, if it exists."""
    snapshot = _compiled_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    if not snapshot.values:
        return None
    return dict(snapshot.values)
