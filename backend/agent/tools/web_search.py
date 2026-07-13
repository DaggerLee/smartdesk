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

        Raises on any failure (ImportError, network error, etc.) so the agent
        loop's mechanism-1 error path handles retries instead of silently
        returning empty results that look like "nothing found".

        Returns:
            {
                "results":  [{"title": str, "url": str, "snippet": str}, ...],
                "evidence": [{"text": str, "source": str}, ...]
            }
        """
        with _trace_span({"type": "tool_call", "tool": "web_search", "query_len": len(query)}) as _out:
            from ddgs import DDGS  # ImportError propagates — intentional
            items = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=5):
                    items.append({
                        "title": r.get("title", r.get("href", "")),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
            evidence = [
                {"text": r["snippet"], "source": r["url"]}
                for r in items
            ]
            _out["results_count"] = len(items)
            _out["evidence_count"] = len(evidence)
        return {"results": items, "evidence": evidence}
