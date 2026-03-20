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


def _build_prompt(question: str, context: List[str], history: list = None) -> str:
    """Build the prompt string from question, retrieved context chunks, and conversation history."""
    history_block = ""
    if history:
        lines = ["Previous conversation:"]
        for turn in history:
            lines.append(f"User: {turn.question}")
            lines.append(f"Assistant: {turn.answer}")
        history_block = "\n".join(lines) + "\n\n"

    if context:
        context_text = "\n\n---\n\n".join(context)
        return (
            "You are a professional enterprise customer service assistant. "
            "Answer the user's question using the reference material and conversation history below.\n"
            "If the reference material contains relevant information, use it to answer "
            "and append exactly [SOURCE_USED] at the very end of your response (no space before it).\n"
            "If the reference material does not contain relevant information, answer from general knowledge "
            "and do NOT append [SOURCE_USED].\n"
            "Do not make up content. Always respond in English.\n\n"
            f"{history_block}"
            f"Current question: {question}\n\n"
            f"Relevant context from documents:\n{context_text}\n\n"
            "Please answer based on the context above and the conversation history:"
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


def generate_answer(question: str, context: List[str], history: list = None) -> str:
    """Generate a complete answer (non-streaming)."""
    model = _find_model()
    url = f"{_BASE}/{model}:generateContent?key={_API_KEY}"
    prompt = _build_prompt(question, context, history)

    resp = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate_answer_stream(question: str, context: List[str], history: list = None) -> Generator[str, None, None]:
    """Generate an answer as a stream, yielding text chunks one at a time."""
    model = _find_model()
    url = f"{_BASE}/{model}:streamGenerateContent?alt=sse&key={_API_KEY}"
    prompt = _build_prompt(question, context, history)

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
