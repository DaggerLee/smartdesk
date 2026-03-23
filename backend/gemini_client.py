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
    msg_type: str = "question",
) -> str:
    """Build the RAG prompt from question, retrieved chunks, conversation history, and optional web results.

    msg_type controls which prompt branch is used:
      'conversational' — greeting / acknowledgment
      'meta'           — format or language instruction
      'followup'       — follow-up referencing previous context
      'question'       — normal new question (default)
    """
    language_rule = (
        "Respond in the same language the user used in their current message."
    )

    history_block = ""
    if history:
        lines = ["Previous conversation:"]
        for turn in history:
            lines.append(f"User: {turn.question}")
            lines.append(f"Assistant: {turn.answer}")
        history_block = "\n".join(lines) + "\n\n"

    web_block = ""
    if web_results:
        lines = ["Web search results (supplementary):"]
        for i, r in enumerate(web_results, 1):
            lines.append(f"{i}. {r.get('title', 'Untitled')}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append(f"   URL: {r.get('url', '')}")
        web_block = "\n".join(lines) + "\n\n"

    base = "You are a professional enterprise customer service assistant. "

    # ── Conversational: greeting / acknowledgment ─────────────────────────────
    if msg_type == "conversational":
        return (
            f"{base}"
            "The user sent a short conversational message (greeting, acknowledgment, etc.). "
            "Respond briefly and naturally — one or two sentences at most. "
            f"Do not search for information or reference documents. {language_rule}\n\n"
            f"{history_block}"
            f"User: {question}"
        )

    # ── Meta: format / language instruction ───────────────────────────────────
    if msg_type == "meta":
        return (
            f"{base}"
            "The user is giving a format or language instruction, not asking a new question. "
            "Apply the instruction to your most recent answer in the conversation history above. "
            "Do not treat the instruction itself as a question to answer. "
            "Do not produce translations or word lists. "
            f"{language_rule}\n\n"
            f"{history_block}"
            f"Instruction: {question}"
        )

    # ── Follow-up: references previous context ────────────────────────────────
    if msg_type == "followup":
        context_block = ""
        if context:
            context_block = "Relevant document context:\n" + "\n\n---\n\n".join(context) + "\n\n"
        return (
            f"{base}"
            "The user is following up on the previous conversation. "
            "Use the conversation history and any document context below to give a thorough answer. "
            f"Do not make up content. {language_rule}\n\n"
            f"{history_block}"
            f"{context_block}"
            f"Follow-up: {question}"
        )

    # ── Normal question ───────────────────────────────────────────────────────
    if context:
        context_text = "\n\n---\n\n".join(context)
        web_instruction = (
            "Additional background information is also provided below. "
            "Incorporate it naturally if it helps.\n"
            "If you use any of this background information, append exactly [WEB_USED] at the "
            "very end of your response (after [SOURCE_USED] if present).\n"
            if web_results else ""
        )
        return (
            f"{base}"
            "Use the background material below to answer the user's question. "
            "Write a natural, direct answer — do NOT mention 'the documents', 'search results', "
            "'reference material', or any other source names in your response. "
            "Just answer as if you already know the information.\n"
            "If you used the document context, append exactly [SOURCE_USED] at the very end "
            "(no space before it, not visible to the user).\n"
            f"{web_instruction}"
            "If the material is not relevant, answer from general knowledge without any marker.\n"
            f"Do not make up content. {language_rule}\n\n"
            f"{history_block}"
            f"Question: {question}\n\n"
            f"Background material:\n{context_text}\n\n"
            f"{web_block}"
            "Answer:"
        )

    if web_results:
        return (
            f"{base}"
            "Use the background information below to answer the user's question. "
            "Write a natural, direct answer — do NOT say 'according to search results', "
            "'based on web results', or anything similar. Just answer directly.\n"
            "Append exactly [WEB_USED] at the very end of your response.\n"
            f"Do not make up content. {language_rule}\n\n"
            f"{history_block}"
            f"Question: {question}\n\n"
            f"{web_block}"
            "Answer:"
        )

    return (
        f"{base}"
        "There is currently no relevant material in the knowledge base. "
        "Inform the user that no relevant content was found and suggest uploading related documents. "
        f"{language_rule}\n\n"
        f"{history_block}"
        f"Question: {question}"
    )


def generate_answer(
    question: str,
    context: List[str],
    history: list = None,
    web_results: list = None,
    msg_type: str = "question",
) -> str:
    """Generate a complete answer (non-streaming)."""
    model = _find_model()
    url = f"{_BASE}/{model}:generateContent?key={_API_KEY}"
    prompt = _build_prompt(question, context, history, web_results, msg_type)

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
    msg_type: str = "question",
) -> Generator[str, None, None]:
    """Generate an answer as a stream, yielding text chunks one at a time."""
    model = _find_model()
    url = f"{_BASE}/{model}:streamGenerateContent?alt=sse&key={_API_KEY}"
    prompt = _build_prompt(question, context, history, web_results, msg_type)

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
