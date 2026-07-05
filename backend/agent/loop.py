"""
agent/loop.py — SmartDesk v2 agent main loop

Generator function that yields AgentEvent; the SSE layer is responsible for
serialising and streaming the events.
W1: tool exceptions propagate directly (no try/catch); unknown tool names raise
immediately (W2 will turn this into a self-healing trigger point).

Evidence protocol: Tool.run() returns a dict that may contain an "evidence" key
(list[{"text": str, "source": str}]).  The loop accumulates all evidence into
state.evidence for the W3 groundedness check.  Both retrieve and web_search
follow this convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generator

from agent.state import AgentState
from agent.tools.retrieve import RetrieveTool
from agent.tools.web_search import WebSearchTool
from llm.client import complete
from config import MAX_AGENT_TURNS


# ──────────────────────────────────────────────
# Event dataclass
# ──────────────────────────────────────────────

@dataclass
class AgentEvent:
    type: str   # "tool_call" | "tool_result" | "final"
    data: dict


# ──────────────────────────────────────────────
# System prompt (passed via systemInstruction in llm/client.py; does not
# consume a conversation turn)
# ──────────────────────────────────────────────

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

# Appended to the last message's parts when max_turns is hit, forcing a final
# answer without further tool calls.
_WRAP_UP_INSTRUCTION = (
    "Based on the information gathered above, answer the user's question now. "
    "Do not call any more tools. If the evidence is insufficient, say so "
    "explicitly instead of guessing."
)


# ──────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────

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
        max_turns: Maximum number of tool-call rounds (safety cap, not the
                   loop driver).
    """

    # ── Tool registry ──
    tools_registry: dict[str, Any] = {
        "retrieve":   RetrieveTool(kb_id=kb_id),
        "web_search": WebSearchTool(),
    }
    declarations = [tool.declaration for tool in tools_registry.values()]

    # ── Initialise state; system prompt goes via systemInstruction, not as a
    #    fake conversation turn ──
    state = AgentState(
        query=query,
        messages=[{"role": "user", "parts": [{"text": query}]}],
    )

    # ── Main loop ──
    while state.turn < max_turns:

        resp = complete(state.messages, tools=declarations, system=SYSTEM_PROMPT)

        # ── Branch A: model requested a tool call ──
        if resp.tool_calls:

            # The model turn containing the functionCall must be echoed back
            # before appending the tool result.
            state.messages.append({
                "role": "model",
                "parts": [
                    {"functionCall": {"name": tc.name, "args": tc.args}}
                    for tc in resp.tool_calls
                ],
            })

            for tc in resp.tool_calls:
                yield AgentEvent(
                    type="tool_call",
                    data={"name": tc.name, "args": tc.args},
                )

                # W1: unknown tool raises immediately; W2 will self-heal here.
                if tc.name not in tools_registry:
                    raise KeyError(
                        f"[loop] Unknown tool: {tc.name!r}. "
                        f"Registered tools: {list(tools_registry)}"
                    )

                result: dict = tools_registry[tc.name].run(**tc.args)  # W1: exceptions propagate

                # Accumulate evidence from all tools (including web_search).
                state.evidence.extend(result.get("evidence", []))

                # functionResponse.response must be a JSON-serialisable dict.
                state.messages.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": tc.name,
                            "response": result,
                        }
                    }],
                })

                yield AgentEvent(
                    type="tool_result",
                    data={"name": tc.name, "result_summary": _summarize(result)},
                )

            state.turn += 1
            continue

        # ── Branch B: model returned text — enough information to answer ──
        state.status = "done"
        yield AgentEvent(
            type="final",
            data={"text": resp.text, "evidence": state.evidence, "messages": state.messages},
        )
        return

    # ── Loop exited — max_turns safety cap reached ──
    state.status = "max_turns"
    # At this point the last message is always user-role (either a
    # functionResponse or the original query when max_turns=0).  Append the
    # wrap-up instruction to its parts list rather than adding a new message,
    # to avoid consecutive user-role messages which cause a Gemini 400.
    state.messages[-1]["parts"].append({"text": _WRAP_UP_INSTRUCTION})
    resp = complete(state.messages, tools=None, system=SYSTEM_PROMPT)  # no tools — force answer
    yield AgentEvent(
        type="final",
        data={"text": resp.text, "evidence": state.evidence, "messages": state.messages},
    )


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _summarize(result: dict, max_chars: int = 200) -> str:
    """Truncate a tool result to a readable summary for progress display."""
    text = str(result)
    return text[:max_chars] + "…" if len(text) > max_chars else text
