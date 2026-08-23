import chroma_client
from config import TOP_K, RELEVANCE_THRESHOLD
from llm.trace import span as _trace_span


_LEXICAL_RELEVANCE_MIN_SCORE = 3


class RetrieveTool:
    name = "retrieve"
    description = "Search the user's knowledge base for relevant document chunks."
    declaration = {
        "name": "retrieve",
        "description": "Search the user's knowledge base for relevant document chunks.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant chunks.",
                }
            },
            "required": ["query"],
        },
    }

    def __init__(self, kb_id: int) -> None:
        self.kb_id = kb_id

    def run(self, *, query: str) -> dict:
        """Query ChromaDB and return chunks + evidence list.

        Returns:
            {
                "chunks":   [str, ...],          # raw text, passed back to the LLM
                "evidence": [{"text": str, "source": str}, ...]  # for groundedness
            }
        """
        with _trace_span({"type": "tool_call", "tool": "retrieve", "query_len": len(query)}) as _out:
            results = chroma_client.query_documents(self.kb_id, query, n_results=TOP_K)
            evidence = [{"text": r["text"], "source": r["filename"]} for r in results]
            best_distance = min((r["distance"] for r in results), default=float("inf"))
            best_lexical_score = max((r.get("lexical_score", 0) for r in results), default=0)
            relevance_ok = (
                best_distance < RELEVANCE_THRESHOLD
                or best_lexical_score >= _LEXICAL_RELEVANCE_MIN_SCORE
            )
            _out["chunks_count"] = len(results)
            _out["best_distance"] = best_distance
            _out["top_distance"] = best_distance
            _out["best_lexical_score"] = best_lexical_score
            _out["relevance_ok"] = relevance_ok
        return {
            "chunks": [r["text"] for r in results],
            "evidence": evidence,
            "relevance_ok": relevance_ok,
        }
