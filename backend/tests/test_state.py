from agent.state import AgentState


def test_default_status_is_running():
    s = AgentState(query="hello")
    assert s.status == "running"


def test_default_turn_is_zero():
    s = AgentState(query="hello")
    assert s.turn == 0


def test_messages_and_evidence_default_to_empty_lists():
    s = AgentState(query="hello")
    assert s.messages == []
    assert s.evidence == []


def test_messages_are_independent_across_instances():
    # field(default_factory=list) must not share the same list object
    a = AgentState(query="a")
    b = AgentState(query="b")
    a.messages.append({"role": "user", "parts": [{"text": "hi"}]})
    assert b.messages == []


def test_status_transitions():
    s = AgentState(query="q")
    for status in ("running", "done", "max_turns", "failed"):
        s.status = status
        assert s.status == status
