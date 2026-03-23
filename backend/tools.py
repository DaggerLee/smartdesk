import logging
from typing import List

logger = logging.getLogger(__name__)

# ChromaDB cosine distance threshold (range 0-2).
# Distances above this mean the retrieved chunks are not very relevant.
RELEVANCE_THRESHOLD = 1.0


def assess_rag_quality(results: List[dict]) -> bool:
    """Return True if RAG results are relevant enough to answer without web search."""
    if not results:
        return False
    best_distance = min(r.get("distance", 2.0) for r in results)
    return best_distance < RELEVANCE_THRESHOLD


def web_search(query: str, num_results: int = 5) -> List[dict]:
    """Search Google and return results as dicts with title, url, and snippet.

    Uses googlesearch-python which requires no API key.
    Returns an empty list on any error so the caller can degrade gracefully.
    """
    try:
        from googlesearch import search
        items = []
        for result in search(query, num_results=num_results, advanced=True):
            items.append({
                "title": result.title or result.url,
                "url": result.url,
                "snippet": result.description or "",
            })
        return items
    except Exception as e:
        logger.warning(f"Web search failed: {e}")
        return []
