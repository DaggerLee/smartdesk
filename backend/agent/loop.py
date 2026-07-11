"""
agent/loop.py — SmartDesk v2 agent main loop

Generator function that yields AgentEvent; the SSE layer is responsible for
serialising and streaming the events.

Self-healing mechanisms (W2):
  1. Tool error retry  — exceptions and unknown tool names feed an error
     functionResponse back to the LLM instead of raising; after
     _MAX_TOOL_FAILURES the model is told the tool is unavailable.
  2. Low retrieval relevance — retrieve returning relevance_ok=False injects a
     rewrite hint so the model tries a different query (capped at _MAX_REWRITES).
  3. Groundedness check — final answer is audited by LLM-as-judge; one revision
     pass is attempted if unsupported sentences are found.

Evidence protocol: Tool.run() returns a dict that may contain an "evidence" key
(list[{"text": str, "source": str}]).  The loop accumulates all evidence into
state.evidence for the groundedness check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generator

from agent.groundedness import check as _check_groundedness
from agent.state import AgentState
from agent.tools.retrieve import RetrieveTool
from agent.tools.web_search import WebSearchTool
from config import MAX_AGENT_TURNS
from llm.client import complete
from llm.trace import span as _trace_span, write as _trace_write


# ── Event dataclass ────────────────────────────────────────────────────────────

@dataclass
class AgentEvent:
    type: str   # "tool_call" | "tool_result" | "final"
    data: dict


# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the reasoning core of SmartDesk, a knowledge assistant that answers \
questions using a private knowledge base and, when necessary, the web.

## Tools
- retrieve: search the user's knowledge base. This is your primary source — \
try it first for any factual question about the user's documents or domain.
- web_search: search the public web. Use it only when the question requires \
current or external information, or when retrieve returned nothing relevant.

## How to work
1. Decide whether the question needs evidence at all. Greetings and questions \
about yourself need none — answer directly without calling tools.
2. For factual questions, call a tool before answering. Prefer one well-formed \
call over several speculative ones.
3. If a tool's results look irrelevant, rewrite the query with different terms \
and try once more before switching tools.
4. Stop searching as soon as you have enough to answer. Do not call tools to \
"double-check" information you already have.

## Answering
- Ground every claim in tool results. Never invent facts, names, numbers, or \
citations.
- If the evidence is insufficient, state exactly what you could not find \
instead of guessing.
- Answer in the user's language. Be concise: lead with the answer, then the \
supporting details.
"""

# ── Self-healing constants ─────────────────────────────────────────────────────

_MAX_TOOL_FAILURES = 2   # per tool name per run_agent() call
_MAX_REWRITES = 1        # rewrite attempts before giving up on low relevance

_TOOL_UNAVAILABLE_MSG = (
    "Tool '{name}' has failed {count} time(s) and is no longer available. "
    "Answer the user's question using only the evidence already gathered. "
    "If the evidence is insufficient, say so explicitly."
)

_REWRITE_HINT = (
    "The retrieval results have low relevance to the query. "
    "Rewrite the search query with different, more specific terms and try retrieve again."
)

_GROUNDEDNESS_REVISION_PREFIX = (
    "The following sentences in your answer could not be verified against the evidence:\n"
)
_GROUNDEDNESS_REVISION_SUFFIX = (
    "\n\nRevise the answer to remove or correct these claims, or explicitly state that "
    "the information was not found in the evidence. Do not call any tools."
)

# Appended to the last user-role message parts when max_turns is hit so the
# model is forced to answer without making more tool calls.
_WRAP_UP_INSTRUCTION = (
    "Based on the information gathered above, answer the user's question now. "
    "Do not call any more tools. If the evidence is insufficient, say so "
    "explicitly instead of guessing."
)


# ── Main loop ──────────────────────────────────────────────────────────────────

def run_agent(
    query: str,
    kb_id: int,
    *,
    max_turns: int = MAX_AGENT_TURNS,
) -> Generator[AgentEvent, None, None]:
    """Run the agent loop and yield a stream of AgentEvents.

    Args:
        query:     The raw user question.
        kb_id:     Knowledge base ID passed to RetrieveTool.
        max_turns: Maximum number of tool-call rounds (safety cap).
    """
    tools_registry: dict[str, Any] = {
        "retrieve":   RetrieveTool(kb_id=kb_id),
        "web_search": WebSearchTool(),
    }
    declarations = [tool.declaration for tool in tools_registry.values()]

    state = AgentState(
        query=query,
        messages=[{"role": "user", "parts": [{"text": query}]}],
    )

    _tool_fail_counts: dict[str, int] = {}  # { tool_name: failure_count }
    _rewrite_count = 0

    while state.turn < max_turns:

        resp = complete(state.messages, tools=declarations, system=SYSTEM_PROMPT)

        # ── Branch A: model requested tool calls ───────────────────────────────
        if resp.tool_calls:

            # Echo the model turn (all functionCall parts together) before results.
            state.messages.append({
                "role": "model",
                "parts": [
                    {"functionCall": {"name": tc.name, "args": tc.args}}
                    for tc in resp.tool_calls
                ],
            })

            # Gemini expects ONE user message whose parts contain a
            # functionResponse for EVERY functionCall in the model turn.
            # Collect all parts here and append once after the loop —
            # appending per-call would produce consecutive user messages.
            fr_parts: list[dict] = []

            for tc in resp.tool_calls:
                yield AgentEvent(type="tool_call", data={"name": tc.name, "args": tc.args})

                # Mechanism 1a: unknown tool name
                if tc.name not in tools_registry:
                    _tool_fail_counts[tc.name] = _tool_fail_counts.get(tc.name, 0) + 1
                    err_msg = f"Unknown tool: {tc.name!r}. Available: {list(tools_registry)}"
                    _trace_write({
                        "type": "tool_error",
                        "tool": tc.name,
                        "error": err_msg,
                        "fail_count": _tool_fail_counts[tc.name],
                        "unavailable": _tool_fail_counts[tc.name] >= _MAX_TOOL_FAILURES,
                    })
                    fr_parts.extend(_error_parts(tc.name, {"error": err_msg}, _tool_fail_counts))
                    yield AgentEvent(
                        type="tool_result",
                        data={"name": tc.name, "result_summary": "[ERROR] unknown tool", "failed": True},
                    )
                    continue

                # Mechanism 1b: tool execution exception
                try:
                    result: dict = tools_registry[tc.name].run(**tc.args)
                except Exception as exc:
                    _tool_fail_counts[tc.name] = _tool_fail_counts.get(tc.name, 0) + 1
                    _trace_write({
                        "type": "tool_error",
                        "tool": tc.name,
                        "error": str(exc),
                        "fail_count": _tool_fail_counts[tc.name],
                        "unavailable": _tool_fail_counts[tc.name] >= _MAX_TOOL_FAILURES,
                    })
                    fr_parts.extend(_error_parts(tc.name, {"error": str(exc)}, _tool_fail_counts))
                    yield AgentEvent(
                        type="tool_result",
                        data={"name": tc.name, "result_summary": f"[ERROR] {exc}", "failed": True},
                    )
                    continue

                state.evidence.extend(result.get("evidence", []))

                fr_parts.append({"functionResponse": {"name": tc.name, "response": result}})

                # Mechanism 2: low retrieval relevance → inject rewrite hint (capped)
                if tc.name == "retrieve" and not result.get("relevance_ok", True):
                    if _rewrite_count < _MAX_REWRITES:
                        _rewrite_count += 1
                        fr_parts.append({"text": _REWRITE_HINT})
                        _trace_write({
                            "type": "rewrite_hint",
                            "tool": "retrieve",
                            "rewrite_count": _rewrite_count,
                        })

                yield AgentEvent(
                    type="tool_result",
                    data={"name": tc.name, "result_summary": _summarize(result)},
                )

            state.messages.append({"role": "user", "parts": fr_parts})
            state.turn += 1
            continue

        # ── Branch B: model returned text — enough information to answer ───────
        state.status = "done"

        # Mechanism 3: groundedness check with one revision pass
        with _trace_span({"type": "groundedness_check"}) as _t:
            grounded = _check_groundedness(resp.text or "", state.evidence)
            _t["supported"] = grounded["supported"]
            _t["unsupported_count"] = len(grounded["unsupported_sentences"])
            _t["unsupported_sentences"] = grounded["unsupported_sentences"]

        if not grounded["supported"]:
            unsupported_list = "\n".join(f"- {s}" for s in grounded["unsupported_sentences"])
            revision_msg = (
                _GROUNDEDNESS_REVISION_PREFIX + unsupported_list + _GROUNDEDNESS_REVISION_SUFFIX
            )
            state.messages.append({"role": "user", "parts": [{"text": revision_msg}]})
            revised = complete(state.messages, tools=None, system=SYSTEM_PROMPT)

            with _trace_span({"type": "groundedness_recheck"}) as _t2:
                grounded2 = _check_groundedness(revised.text or "", state.evidence)
                _t2["supported"] = grounded2["supported"]
                _t2["unsupported_sentences"] = grounded2["unsupported_sentences"]
                _t2["grounded_final"] = grounded2["supported"]

            yield AgentEvent(
                type="final",
                data={
                    "text": revised.text,
                    "evidence": state.evidence,
                    "messages": state.messages,
                    "grounded": grounded2["supported"],
                },
            )
        else:
            yield AgentEvent(
                type="final",
                data={
                    "text": resp.text,
                    "evidence": state.evidence,
                    "messages": state.messages,
                    "grounded": True,
                },
            )
        return

    # ── Loop exited — max_turns safety cap reached ─────────────────────────────
    state.status = "max_turns"
    state.messages[-1]["parts"].append({"text": _WRAP_UP_INSTRUCTION})
    resp = complete(state.messages, tools=None, system=SYSTEM_PROMPT)
    yield AgentEvent(
        type="final",
        data={
            "text": resp.text,
            "evidence": state.evidence,
            "messages": state.messages,
            "grounded": None,   # groundedness not checked at max_turns exit
        },
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _error_parts(
    tool_name: str,
    error_payload: dict,
    fail_counts: dict[str, int],
) -> list[dict]:
    """Build error functionResponse parts; inject unavailability notice when threshold is hit."""
    parts: list[dict] = [{"functionResponse": {"name": tool_name, "response": error_payload}}]
    if fail_counts.get(tool_name, 0) >= _MAX_TOOL_FAILURES:
        parts.append({
            "text": _TOOL_UNAVAILABLE_MSG.format(
                name=tool_name, count=fail_counts[tool_name]
            )
        })
    return parts


def _summarize(result: dict, max_chars: int = 200) -> str:
    """Truncate a tool result to a readable summary for progress display."""
    text = str(result)
    return text[:max_chars] + "…" if len(text) > max_chars else text
