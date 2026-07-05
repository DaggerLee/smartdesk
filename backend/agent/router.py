"""
agent/router.py — SmartDesk v2 query router

One non-streaming LLM call classifies a query into one of three execution paths:
direct / rag / agent.  Stateless; does not depend on conversation history.
Parse failures always fall back to "rag" — it has the lowest failure cost of the
three paths (direct hallucinates, agent is most expensive, rag just adds one
extra retrieval).
"""

from __future__ import annotations

from llm.client import complete

SYSTEM_PROMPT = """\
You are a query router. Classify the user's request into exactly one of the \
following categories.

direct
- Greetings and casual conversation
- Questions about the assistant itself
- Simple interactions that require no retrieval

rag
- A single factual question
- The answer is expected to exist in the knowledge base
- One retrieval should usually be sufficient
- No comparison, planning, or multi-step reasoning required

agent
- Requires multiple retrieval steps
- Requires comparison, synthesis, or planning
- May require information beyond the knowledge base (e.g. web search)

Return ONLY one word: direct, rag, or agent.

Examples

User: Hi
Answer: direct

User: Who are you?
Answer: direct

User: What is LoRA?
Answer: rag

User: What is the Transformer attention mechanism?
Answer: rag

User: Compare LoRA and QLoRA, and explain when each should be used.
Answer: agent

User: Summarize the latest research on DeepSeek and compare it with recent \
LLM developments.
Answer: agent

Boundary examples

User: Has DeepSeek released a new model today?
Answer: agent
(Looks like a single factual question, but the answer cannot exist in the \
knowledge base — it requires current external information.)

User: Thanks! By the way, what did my uploaded doc say about refunds?
Answer: rag
(Starts like casual chat, but the real intent requires retrieval.)
"""

_VALID_LABELS = ("direct", "rag", "agent")


def route(query: str) -> str:
    """Classify a single query into one of three execution paths.

    Returns:
        "direct" | "rag" | "agent".  Falls back to "rag" on parse failure.
    """
    resp = complete(
        messages=[{"role": "user", "parts": [{"text": query}]}],
        tools=None,
        system=SYSTEM_PROMPT,
        temperature=0,  # deterministic output required for classification
    )
    label = resp.text.strip().lower()

    # Substring match handles verbose model output (e.g. "Category: rag").
    # Order direct → rag → agent: on ambiguity, prefer the cheaper/safer path.
    if "direct" in label:
        return "direct"
    if "rag" in label:
        return "rag"
    if "agent" in label:
        return "agent"
    return "rag"
