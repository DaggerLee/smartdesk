"""Tests for Chroma retrieval result post-processing."""

import chroma_client


class _FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.requested_n_results = None

    def count(self):
        return len(self.rows)

    def query(self, *, query_texts, n_results, include):
        self.requested_n_results = n_results
        rows = self.rows[:n_results]
        return {
            "documents": [[row["text"] for row in rows]],
            "metadatas": [[{"filename": row["filename"], "chunk_index": row["chunk_index"]} for row in rows]],
            "distances": [[row["distance"] for row in rows]],
        }


def test_query_documents_promotes_lexically_exact_candidate_from_broad_pool(monkeypatch):
    rows = [
        {
            "text": f"generic agentic workflow filler {i}",
            "filename": "generic.html",
            "chunk_index": i,
            "distance": 0.10 + i * 0.01,
        }
        for i in range(5)
    ]
    rows.append(
        {
            "text": (
                "ACHIEVE 框架：Aiding human coordination；"
                "Cutting tedious tasks；Help provide safety net。"
            ),
            "filename": "target.html",
            "chunk_index": 42,
            "distance": 0.60,
        }
    )
    collection = _FakeCollection(rows)
    monkeypatch.setattr(chroma_client, "_get_or_create", lambda kb_id: collection)

    results = chroma_client.query_documents(
        1,
        "ACHIEVE 框架里，A、C、H 分别代表什么适用场景？",
        n_results=5,
    )

    assert collection.requested_n_results == 6
    assert len(results) == 5
    assert results[0]["filename"] == "target.html"
    assert results[0]["chunk_index"] == 42


def test_query_documents_ignores_generic_cjk_question_phrasing_when_reranking(monkeypatch):
    rows = [
        {
            "text": f"缓存是什么以及常见问题 filler {i}",
            "filename": "generic.html",
            "chunk_index": i,
            "distance": 0.90 + i * 0.01,
        }
        for i in range(5)
    ]
    rows.append(
        {
            "text": "MCP 远程 server 采用 OAuth 2.1 进行认证，access token 用于请求核验。",
            "filename": "mcp.html",
            "chunk_index": 9,
            "distance": 0.20,
        }
    )
    collection = _FakeCollection(rows)
    monkeypatch.setattr(chroma_client, "_get_or_create", lambda kb_id: collection)

    results = chroma_client.query_documents(1, "MCP 的认证方式是什么？", n_results=5)

    assert results[0]["filename"] == "mcp.html"
    assert all(row["filename"] != "generic.html" or row["lexical_score"] == 0 for row in results)


def test_query_documents_ignores_other_cjk_question_templates_when_reranking(monkeypatch):
    rows = [
        {
            "text": f"MCP 缓存有多少种常见模式 filler {i}",
            "filename": "generic.html",
            "chunk_index": i,
            "distance": 0.90 + i * 0.01,
        }
        for i in range(5)
    ]
    rows.append(
        {
            "text": "远程 server 采用 OAuth 2.1 进行认证，access token 用于请求核验。",
            "filename": "auth.html",
            "chunk_index": 9,
            "distance": 0.20,
        }
    )
    collection = _FakeCollection(rows)
    monkeypatch.setattr(chroma_client, "_get_or_create", lambda kb_id: collection)

    results = chroma_client.query_documents(1, "MCP 有多少种认证方式？", n_results=5)

    assert results[0]["filename"] == "auth.html"
    assert all(not row.get("strong_lexical_match", False) for row in results if row["filename"] == "generic.html")
