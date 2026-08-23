"""
agent/router.py — SmartDesk v2 query router

One non-streaming LLM call classifies a query into one of three execution paths:
direct / rag / agent.  Stateless; does not depend on conversation history.
Parse failures always fall back to "rag" — it has the lowest failure cost of the
three paths (direct hallucinates, agent is most expensive, rag just adds one
extra retrieval).
"""

from __future__ import annotations

from agent.write_note_policy import classify_write_intent
from llm.client import LLMProtocolError, complete
from llm.trace import write as _trace_write

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

_AGENT_REQUIRED_MARKERS = (
    "compare",
    "comparison",
    "latest",
    "today",
    "web search",
    "multi-agent",
    "multi agent",
    "planning",
    "reflection",
    "比较",
    "对比",
    "共同",
    "放在一起",
    "综合",
    "规划",
    "计划",
    "最新",
    "今天",
    "当前",
    "搜索",
    "查一下",
    "同时",
    "分别查",
    "多工具",
    "多步骤",
    "比",
)

_SOURCE_BOUND_MARKERS = (
    "笔记",
    "知识库",
    "文档",
    "资料",
    "notes",
)

_SINGLE_FACT_MARKERS = (
    "是什么",
    "为什么",
    "怎么",
    "如何",
    "什么时候",
)


def _should_demote_agent_to_rag(query: str) -> bool:
    """Keep single knowledge-base fact questions on the cheaper RAG path.

    The LLM router sometimes treats a hard two-part factual question as
    "agent" because it sees synthesis work. SmartDesk's eval contract keeps
    single-source factual explanation on RAG; agent is reserved for comparison,
    planning, current/external lookup, or multi-source synthesis.
    """
    q = query.strip().lower()
    if any(marker in q for marker in _AGENT_REQUIRED_MARKERS):
        return False
    if not any(marker in q for marker in _SOURCE_BOUND_MARKERS):
        return False
    return any(marker in query for marker in _SINGLE_FACT_MARKERS)



def route(query: str) -> str:
    """Classify a single query into one of three execution paths.

    Returns:
        "direct" | "rag" | "agent".  Falls back to "rag" on parse failure.
    """
    write_intent = classify_write_intent(query)
    try:
        resp = complete(
            messages=[{"role": "user", "parts": [{"text": query}]}],
            tools=None,
            system=SYSTEM_PROMPT,
            temperature=0,  # deterministic output required for classification
        )
        raw_text = resp.text or ""
    except LLMProtocolError:
        if write_intent != "persist":
            raise
        raw_text = "<protocol_error>"
    label = raw_text.strip().lower()

    # Substring match handles verbose model output (e.g. "Category: rag").
    # Order direct → rag → agent: on ambiguity, prefer the cheaper/safer path.
    if "direct" in label:
        decision = "direct"
    elif "rag" in label:
        decision = "rag"
    elif "agent" in label:
        decision = "agent"
    else:
        decision = "rag"

    if write_intent == "persist":
        decision = "agent"
    elif decision == "agent" and _should_demote_agent_to_rag(query):
        decision = "rag"
    _trace_write({"type": "router_decision", "raw_text": raw_text, "decision": decision})
    return decision
