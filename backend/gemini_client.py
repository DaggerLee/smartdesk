import logging
import os
from typing import List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("GEMINI_API_KEY", "")
_BASE = "https://generativelanguage.googleapis.com/v1beta"
_cached_model: Optional[str] = None


def _find_model() -> str:
    """查询此 API Key 实际可用的模型，返回第一个支持 generateContent 的模型全名。"""
    global _cached_model
    if _cached_model:
        return _cached_model

    resp = requests.get(f"{_BASE}/models?key={_API_KEY}", timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"ListModels 失败 ({resp.status_code}): {resp.text}")

    models = resp.json().get("models", [])
    names = [m["name"] for m in models]
    logging.info(f"[Gemini] 可用模型列表: {names}")

    for m in models:
        if "generateContent" in m.get("supportedGenerationMethods", []):
            _cached_model = m["name"]   # 形如 "models/gemini-1.5-flash"
            logging.info(f"[Gemini] 自动选用模型: {_cached_model}")
            return _cached_model

    raise RuntimeError(f"该 API Key 下没有支持 generateContent 的模型，可用列表: {names}")


def generate_answer(question: str, context: List[str]) -> str:
    model = _find_model()
    url = f"{_BASE}/{model}:generateContent?key={_API_KEY}"

    if context:
        context_text = "\n\n---\n\n".join(context)
        prompt = f"""You are a professional enterprise customer service assistant. Answer the user's question strictly based on the reference material below.
If the reference material does not contain relevant information, honestly tell the user that no relevant information was found in the knowledge base. Do not make up content. Always respond in English.

[Reference Material]
{context_text}

[User Question]
{question}

Please provide an accurate, professional, and concise answer:"""
    else:
        prompt = f"""You are a professional enterprise customer service assistant. There is currently no relevant material in the knowledge base.
Please inform the user that no relevant content was found, and suggest they upload related documents before asking again. Always respond in English.

User question: {question}"""

    resp = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
