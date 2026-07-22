"""Deterministic delivery policy for finalized LangGraph agent answers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

VerificationStatus = Literal[
    "verified",
    "not_applicable",
    "unchecked_max_turns",
    "check_error",
    "rejected",
]

ALLOWED_VERIFICATION_STATUSES = frozenset({
    "verified",
    "not_applicable",
    "unchecked_max_turns",
    "check_error",
    "rejected",
})

RETRYABLE_VERIFICATION_NOTICE = (
    "I couldn't complete a verifiable answer this time. Please try again."
)
UNSUPPORTED_ANSWER_NOTICE = (
    "I couldn't provide an answer that was sufficiently supported by the available evidence."
)

# Released notice literals are immutable. If wording changes, add the new
# literal and retain every old one so persisted notices never re-enter context.
NON_CONTEXT_ANSWERS = frozenset({
    RETRYABLE_VERIFICATION_NOTICE,
    UNSUPPORTED_ANSWER_NOTICE,
})


@dataclass(frozen=True)
class DeliveryDecision:
    payload: str
    kind: Literal["graph_answer", "retryable_notice", "unsupported_notice"]


def select_delivery(graph_answer: str, status: Optional[str]) -> DeliveryDecision:
    """Select the only payload the enabled route may persist and emit."""
    if status in {"verified", "not_applicable"}:
        return DeliveryDecision(graph_answer, "graph_answer")
    if status in {"check_error", "unchecked_max_turns"}:
        return DeliveryDecision(RETRYABLE_VERIFICATION_NOTICE, "retryable_notice")
    return DeliveryDecision(UNSUPPORTED_ANSWER_NOTICE, "unsupported_notice")


def is_verified_delivery_enabled() -> bool:
    """Read the rollout flag at request time; default to the legacy path."""
    return os.getenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
