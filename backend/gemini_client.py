import json
import logging
import os
from typing import Generator, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("GEMINI_API_KEY", "")
_BASE = "https://generativelanguage.googleapis.com/v1beta"
_cached_model: Optional[str] = None


def _find_model() -> str:
    """Query available models for this API key and return the first one that supports generateContent."""
    global _cached_model
    if _cached_model:
        return _cached_model

    resp = requests.get(f"{_BASE}/models?key={_API_KEY}", timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"ListModels failed ({resp.status_code}): {resp.text}")

    models = resp.json().get("models", [])
    names = [m["name"] for m in models]
    logging.info(f"[Gemini] Available models: {names}")

    for m in models:
        if "generateContent" in m.get("supportedGenerationMethods", []):
            _cached_model = m["name"]
            logging.info(f"[Gemini] Selected model: {_cached_model}")
            return _cached_model

    raise RuntimeError(f"No model supporting generateContent found. Available: {names}")


def _build_prompt(
    question: str,
    context: List[str],
    history: list = None,
    web_results: list = None,
) -> str:
    """Build the RAG prompt from question, retrieved chunks, conversation history, and optional web results."""
    history_block = ""
    if history:
        lines = ["Previous conversation:"]
        for turn in history:
            lines.append(f"User: {turn.question}")
            lines.append(f"Assistant: {turn.answer}")
        history_block = "\n".join(lines) + "\n\n"

    web_block = ""
    if web_results:
        lines = ["Web search results (supplementary — use when documents lack sufficient information):"]
        for i, r in enumerate(web_results, 1):
            lines.append(f"{i}. {r.get('title', 'Untitled')}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append(f"   URL: {r.get('url', '')}")
        web_block = "\n".join(lines) + "\n\n"

    if context:
        context_text = "\n\n---\n\n".join(context)
        web_instruction = (
            "You may also reference the web search results below to fill any gaps. "
            "If you use web results, append exactly [WEB_USED] at the very end (after [SOURCE_USED] if present).\n"
            if web_results else ""
        )
        return (
            "You are a professional enterprise customer service assistant. "
            "Answer the user's question using the reference material and conversation history below.\n"
            "If the reference material contains relevant information, use it to answer "
            "and append exactly [SOURCE_USED] at the very end of your response (no space before it).\n"
            f"{web_instruction}"
            "If the reference material does not contain relevant information, answer from general knowledge "
            "and do NOT append [SOURCE_USED].\n"
            "Do not make up content. Always respond in English.\n\n"
            f"{history_block}"
            f"Current question: {question}\n\n"
            f"Relevant context from documents:\n{context_text}\n\n"
            f"{web_block}"
            "Please answer based on the context above:"
        )

    if web_results:
        # No document context available — rely on web results only
        return (
            "You are a professional enterprise customer service assistant. "
            "The knowledge base does not contain documents relevant to this question. "
            "Use the web search results below to answer if applicable. "
            "Append exactly [WEB_USED] at the very end of your response.\n"
            "Do not make up content. Always respond in English.\n\n"
            f"{history_block}"
            f"Current question: {question}\n\n"
            f"{web_block}"
            "Please answer based on the web search results above:"
        )

    return (
        "You are a professional enterprise customer service assistant. "
        "There is currently no relevant material in the knowledge base.\n"
        "Please inform the user that no relevant content was found, "
        "and suggest they upload related documents before asking again. "
        "Always respond in English.\n\n"
        f"{history_block}"
        f"User question: {question}"
    )


def generate_answer(
    question: str,
    context: List[str],
    history: list = None,
    web_results: list = None,
) -> str:
    """Generate a complete answer (non-streaming)."""
    model = _find_model()
    url = f"{_BASE}/{model}:generateContent?key={_API_KEY}"
    prompt = _build_prompt(question, context, history, web_results)

    resp = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate_answer_stream(
    question: str,
    context: List[str],
    history: list = None,
    web_results: list = None,
) -> Generator[str, None, None]:
    """Generate an answer as a stream, yielding text chunks one at a time."""
    model = _find_model()
    url = f"{_BASE}/{model}:streamGenerateContent?alt=sse&key={_API_KEY}"
    prompt = _build_prompt(question, context, history, web_results)

    with requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        stream=True,
        timeout=60,
    ) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                if text:
                    yield text
            except (KeyError, json.JSONDecodeError, IndexError):
                continue


def generate_summary(text: str) -> str:
    """Generate a 3-5 sentence document summary using the first 4000 characters of text.

    Returns an empty string on failure so the caller can degrade gracefully.
    """
    try:
        model = _find_model()
        url = f"{_BASE}/{model}:generateContent?key={_API_KEY}"
        excerpt = text[:4000]
        prompt = (
            "Summarize the following document in 3 to 5 sentences. "
            "Focus on the main topics, key information, and the purpose of the document. "
            "Be concise and informative. Always respond in English.\n\n"
            f"Document content:\n{excerpt}\n\n"
            "Summary:"
        )
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logging.warning(f"Summary generation failed: {e}")
        return ""
