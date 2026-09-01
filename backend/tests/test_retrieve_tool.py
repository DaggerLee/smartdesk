"""Tests for RetrieveTool result metadata."""

from agent.tools.retrieve import RetrieveTool


def test_retrieve_relevance_uses_best_returned_distance(monkeypatch):
    rows = [
        {
            "text": "lexically exact but vector-far chunk",
            "filename": "exact.html",
            "chunk_index": 7,
            "distance": 0.90,
        },
        {
            "text": "semantically close chunk",
            "filename": "near.html",
            "chunk_index": 1,
            "distance": 0.20,
        },
    ]
    monkeypatch.setattr("agent.tools.retrieve.chroma_client.query_documents", lambda *args, **kwargs: rows)

    result = RetrieveTool(kb_id=1).run(query="anything")

    assert result["relevance_ok"] is True
    assert result["evidence"][0]["source"] == "exact.html"



def test_retrieve_relevance_accepts_strong_lexical_match(monkeypatch):
    rows = [
        {
            "text": "vector-far but exact lexical match",
            "filename": "exact.html",
            "chunk_index": 2,
            "distance": 0.90,
            "strong_lexical_match": True,
        }
    ]
    monkeypatch.setattr("agent.tools.retrieve.chroma_client.query_documents", lambda *args, **kwargs: rows)

    result = RetrieveTool(kb_id=1).run(query="anything")

    assert result["relevance_ok"] is True


def test_retrieve_relevance_rejects_weak_lexical_only_match(monkeypatch):
    rows = [
        {
            "text": "only generic question phrasing matched",
            "filename": "generic.html",
            "chunk_index": 1,
            "distance": 0.90,
            "lexical_score": 5,
            "strong_lexical_match": False,
        }
    ]
    monkeypatch.setattr("agent.tools.retrieve.chroma_client.query_documents", lambda *args, **kwargs: rows)

    result = RetrieveTool(kb_id=1).run(query="anything")

    assert result["relevance_ok"] is False