import chroma_client
from config import TOP_K
from llm.trace import span as _trace_span


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
            _out["chunks_count"] = len(results)
            _out["evidence_count"] = len(evidence)
        return {
            "chunks": [r["text"] for r in results],
            "evidence": evidence,
        }
