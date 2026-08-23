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
- Source-bound single-fact, tightly related factual explanation, same-topic technical-difference, and corpus-bounded absence-check questions stay here, even when phrased as why/when/what/how/difference
- No planning, current/external lookup, open-ended synthesis across multiple independent sources, or multi-step tool reasoning required

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


User: 某份内部笔记里，短有效期凭据和续期凭据分别负责什么？
Answer: rag
(Two tightly related facts from one named note/source; not a comparison,
planning task, or external lookup.)

User: 某个远程协议的新旧传输方式，在连接状态处理上有什么核心差异？
Answer: rag
(A technical difference inside one source-bounded topic; one retrieval pass
should answer it.)

User: 这组项目笔记里有没有讲消息队列分区再均衡策略？
Answer: rag
(A corpus-bounded absence check across the knowledge base; retrieval should
confirm whether the notes contain it.)

User: Tina 的 traceability 复现文档和 Vibe 的 checkpoint 共同降低什么工程风险？
Answer: agent
(Requires synthesis across two named concepts/sources to identify a shared
problem; one single-source retrieval answer is insufficient.)

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
    _trace_write({"type": "router_decision", "raw_text": raw_text, "decision": decision})
    return decision
