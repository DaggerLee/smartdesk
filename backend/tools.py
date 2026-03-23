import logging
import re
import urllib.parse
from typing import List, Optional

import requests as http

logger = logging.getLogger(__name__)

# ChromaDB cosine distance threshold (range 0-2).
# In practice, even unrelated docs can score below 1.0 with the ONNX embedding model,
# so 0.8 is a more reliable cutoff for "actually relevant" content.
RELEVANCE_THRESHOLD = 0.8

# Detects weather-related queries
_WEATHER_RE = re.compile(
    r"\b(weather|forecast|temperature|rain|snow|sunny|cloudy|wind|humidity|cold|hot|warm)\b"
    r"|天气|气温|下雨|晴天|温度|降水|多云|风速|寒冷|炎热",
    re.IGNORECASE,
)

# ── RAG quality ───────────────────────────────────────────────────────────────

def assess_rag_quality(results: List[dict]) -> bool:
    """Return True if RAG results are relevant enough to answer without web search."""
    if not results:
        print("[RAG] No results returned from ChromaDB — triggering web search")
        return False

    distances = [r.get("distance", 2.0) for r in results]
    best = min(distances)
    print(f"[RAG] Retrieved {len(results)} chunks. Distances: {[round(d, 4) for d in distances]}")
    print(f"[RAG] Best distance: {round(best, 4)} | Threshold: {RELEVANCE_THRESHOLD}")

    if best >= RELEVANCE_THRESHOLD:
        print(f"[RAG] Quality insufficient (best {round(best, 4)} >= {RELEVANCE_THRESHOLD}) — triggering web search")
        return False

    print("[RAG] Quality OK — using document context")
    return True


# ── Weather tool ──────────────────────────────────────────────────────────────

def is_weather_query(message: str) -> bool:
    return bool(_WEATHER_RE.search(message))


def _extract_location(message: str) -> str:
    """Extract the location name from a weather query.

    Handles common Chinese and English patterns; falls back to the raw message
    so wttr.in can attempt its own parsing.
    """
    # Chinese: text before 天气 / 气温 / 温度, stripping time words
    m = re.search(r'(.+?)(?:的)?(?:天气|气温|温度|天气预报)', message)
    if m:
        loc = re.sub(r'(今天|明天|现在|最近|查询|如何|怎么样|怎样)', '', m.group(1)).strip()
        if loc:
            return loc

    # English: "weather in X", "X weather", "X forecast"
    m = re.search(r'weather\s+in\s+([a-zA-Z ,]+?)(?:\s+today|\s+forecast|\?|$)', message, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'([a-zA-Z ]{3,}?)\s+(?:weather|forecast)', message, re.I)
    if m:
        return m.group(1).strip()

    return message


def fetch_weather(message: str) -> Optional[str]:
    """Fetch real-time weather data from wttr.in (no API key required).

    Returns a formatted weather summary string, or None on failure.
    """
    location = _extract_location(message)
    print(f"[Weather] Fetching weather for: {location!r}")
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        resp = http.get(url, timeout=10, headers={"User-Agent": "SmartDesk/1.0"})
        if resp.status_code != 200:
            print(f"[Weather] wttr.in returned {resp.status_code}")
            return None
        data = resp.json()

        c = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        city    = area.get("areaName",  [{}])[0].get("value", location)
        country = area.get("country",   [{}])[0].get("value", "")
        desc     = c["weatherDesc"][0]["value"]
        temp_c   = c["temp_C"]
        temp_f   = c["temp_F"]
        feels_c  = c["FeelsLikeC"]
        humidity = c["humidity"]
        wind_kmh = c["windspeedKmph"]
        precip   = c.get("precipMM", "0")
        uv_index = c.get("uvIndex", "N/A")

        location_str = f"{city}, {country}" if country else city
        summary = (
            f"Current weather in {location_str}:\n"
            f"  Condition   : {desc}\n"
            f"  Temperature : {temp_c}°C / {temp_f}°F  (feels like {feels_c}°C)\n"
            f"  Humidity    : {humidity}%\n"
            f"  Wind        : {wind_kmh} km/h\n"
            f"  Precipitation: {precip} mm\n"
            f"  UV Index    : {uv_index}"
        )
        print(f"[Weather] Got data: {temp_c}°C, {desc}")
        return summary
    except Exception as e:
        print(f"[Weather] Failed: {e}")
        return None


# ── Web search ────────────────────────────────────────────────────────────────

def web_search(query: str, num_results: int = 5) -> List[dict]:
    """Search DuckDuckGo and return results as dicts with title, url, and snippet.

    Uses ddgs which requires no API key and is not rate-limited.
    Returns an empty list on any error so the caller can degrade gracefully.
    """
    print(f"[WebSearch] Searching for: {query!r}")
    try:
        from ddgs import DDGS
        items = []
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=num_results):
                items.append({
                    "title": result.get("title", result.get("href", "")),
                    "url": result.get("href", ""),
                    "snippet": result.get("body", ""),
                })
        print(f"[WebSearch] Got {len(items)} results")
        return items
    except Exception as e:
        print(f"[WebSearch] Failed: {e}")
        logger.warning(f"Web search failed: {e}")
        return []
