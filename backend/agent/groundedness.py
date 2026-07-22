"""agent/groundedness.py — LLM-as-judge citation groundedness check.

Single complete() call at temperature=0 audits whether every factual claim in
the answer is supported by at least one evidence passage.

Parsing is deliberately defensive: the judge prompt instructs "return ONLY JSON"
but real Gemini responses sometimes wrap the object in markdown code fences or
add a short preamble.  The parser strips fences, tries a direct json.loads, then
falls back to extracting the first {...} block.  On any failure it fails open
(supported=True) and records a parse_error in the trace so the failure is visible
without blocking answer delivery.
"""

from __future__ import annotations

import json
import re

from llm.client import complete
from llm.trace import span as _trace_span

_JUDGE_SYSTEM = """\
You are a factual grounding auditor. You will be given an ANSWER and a set of
EVIDENCE passages. Your job is to determine whether every factual claim in the
answer is supported by at least one evidence passage.

Rules:
- Ignore claims that are common knowledge or definitions (e.g. "Python is a language").
- Return ONLY a JSON object with this exact schema, no other text:
  {"supported": <bool>, "unsupported_sentences": [<string>, ...]}
- "supported" is true only when unsupported_sentences is empty.

## SUPPORTED — mark these as grounded, do NOT flag them

1. Paraphrase or synonym rewording of an evidence claim.
   Example — Evidence: "模块化：组件可独立替换（换搜索引擎/换模型），不同步骤甚至用不同LLM"
   Answer sentence: "多模型协同：多 Agent 允许在不同的步骤中甚至使用不同的 LLM。"
   → SUPPORTED (same claim, reworded).

2. Attribution/framing shift that keeps the underlying claim intact (e.g.
   restating "agentic 的优势" as "多 agent 的优势" when the evidence discusses
   the same property in that context) or a reasonable cross-evidence synthesis
   that combines two passages without introducing new facts.
   Example — Evidence: "并行化：多个web search/fetch可同时进行，比人类顺序处理快"
   Answer sentence: "单 Agent 通常只能顺序执行任务，而多 Agent 架构支持并行化。"
   → SUPPORTED (same claim, restated as a single-vs-multi comparison).

## UNSUPPORTED — flag these

1. A specific number, named entity, or model/product name that does not
   appear anywhere in the evidence, even if the surrounding claim sounds
   plausible.
   Example — Evidence mentions "小模型适合简单事实问答（PII脱敏案例：Llama 3.1 8B
   漏字段，GPT-4级别完整正确）" but never states a percentage.
   Answer sentence: "我们发现大模型提取错误占了 80% 以上。"
   → UNSUPPORTED ("80%" appears nowhere in evidence — fabricated statistic).

2. An argument or elaboration built from outside knowledge to support a
   claim, even when the claim's conclusion happens to be independently true.
   Flag the reasoning/elaboration sentence itself, not a plain conclusion
   sentence that itself matches the evidence.
   Example — Evidence only says "code-as-action效果最好（有论文数据）" with no
   named study.
   Answer sentence: "这一结论在众多业界研究（包括 Voyager 论文等）以及实际工程
   实践中都得到了数据支持。"
   → UNSUPPORTED (the paper name and the "众多业界研究" claim are outside
   knowledge, not present in evidence).
"""


def check(answer: str, evidence: list[dict]) -> dict:
    """Audit whether the answer is grounded in the provided evidence.

    Args:
        answer:   The answer text to audit.
        evidence: List of {"text": str, "source": str} dicts from state.evidence.

    Returns:
        {"supported": bool, "unsupported_sentences": list[str]}
        Fails open on any parse error (supported=True) and records parse_error
        in the trace entry so the failure is observable.
    """
    if not evidence:
        return {
            "supported": True,
            "unsupported_sentences": [],
            "verification_status": "not_applicable",
        }

    evidence_block = "\n\n".join(
        f"[{i + 1}] ({e.get('source', 'unknown')}) {e['text']}"
        for i, e in enumerate(evidence)
    )
    prompt = f"EVIDENCE:\n{evidence_block}\n\nANSWER:\n{answer}"

    with _trace_span({"type": "groundedness_judge"}) as _t:
        _t["answer_len"] = len(answer)
        _t["evidence_count"] = len(evidence)

        try:
            resp = complete(
                messages=[{"role": "user", "parts": [{"text": prompt}]}],
                tools=None,
                system=_JUDGE_SYSTEM,
                temperature=0,
            )
            raw = (resp.text or "").strip()

            # Strip markdown code fences: ```json\n...\n``` or ```\n...\n```
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
            raw = raw.strip()

            # Try direct parse first; if it fails extract the first {...} block.
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if not m:
                    raise
                parsed = json.loads(m.group())

            result = {
                "supported": bool(parsed.get("supported", True)),
                "unsupported_sentences": list(parsed.get("unsupported_sentences", [])),
            }
            result["verification_status"] = (
                "verified" if result["supported"] else "rejected"
            )
            _t["supported"] = result["supported"]
            _t["unsupported_sentences"] = result["unsupported_sentences"]
            _t["verification_status"] = result["verification_status"]
            return result

        except Exception as exc:
            _t["check_error"] = str(exc)
            if "resp" in locals():
                _t["raw_text_excerpt"] = (resp.text or "")[:300]
            return {
                "supported": True,
                "unsupported_sentences": [],
                "verification_status": "check_error",
            }
