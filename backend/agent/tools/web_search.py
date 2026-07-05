from tools import web_search as _ddgs_search
from llm.trace import span as _trace_span


class WebSearchTool:
    name = "web_search"
    description = "Search the public web for current or external information."
    declaration = {
        "name": "web_search",
        "description": "Search the public web for current or external information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                }
            },
            "required": ["query"],
        },
    }

    def run(self, *, query: str) -> dict:
        """Run a DuckDuckGo web search and return results + evidence list.

        Returns:
            {
                "results":  [{"title": str, "url": str, "snippet": str}, ...],
                "evidence": [{"text": str, "source": str}, ...]
            }
        """
        with _trace_span({"type": "tool_call", "tool": "web_search", "query_len": len(query)}) as _out:
            results = _ddgs_search(query)
            evidence = [
                {"text": r.get("snippet", ""), "source": r.get("url", "")}
                for r in results
            ]
            _out["results_count"] = len(results)
            _out["evidence_count"] = len(evidence)
        return {"results": results, "evidence": evidence}
