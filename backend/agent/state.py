from dataclasses import dataclass, field


@dataclass
class AgentState:
    query: str
    messages: list[dict] = field(default_factory=list)   # Gemini-format content turns in the loop
    evidence: list[dict] = field(default_factory=list)   # accumulated chunks/search results with source metadata
    turn: int = 0
    status: str = "running"  # running | done | max_turns | failed
