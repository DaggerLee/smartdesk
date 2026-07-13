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
- A claim is UNSUPPORTED if it cannot be verified from the evidence passages alone.
- Return ONLY a JSON object with this exact schema, no other text:
  {"supported": <bool>, "unsupported_sentences": [<string>, ...]}
- "supported" is true only when unsupported_sentences is empty.
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
        return {"supported": True, "unsupported_sentences": []}

    evidence_block = "\n\n".join(
        f"[{i + 1}] ({e.get('source', 'unknown')}) {e['text']}"
        for i, e in enumerate(evidence)
    )
    prompt = f"EVIDENCE:\n{evidence_block}\n\nANSWER:\n{answer}"

    with _trace_span({"type": "groundedness_judge"}) as _t:
        resp = complete(
            messages=[{"role": "user", "parts": [{"text": prompt}]}],
            tools=None,
            system=_JUDGE_SYSTEM,
            temperature=0,
        )
        _t["answer_len"] = len(answer)
        _t["evidence_count"] = len(evidence)

        try:
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
            _t["supported"] = result["supported"]
            _t["unsupported_sentences"] = result["unsupported_sentences"]
            return result

        except Exception as exc:
            _t["parse_error"] = str(exc)
            _t["raw_text_excerpt"] = (resp.text or "")[:300]
            return {"supported": True, "unsupported_sentences": []}
