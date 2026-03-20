import re
from typing import List

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

# Uses ChromaDB's built-in ONNX embedding model — no PyTorch required
_embedding_fn = DefaultEmbeddingFunction()

# Local persistent ChromaDB with telemetry disabled
_client = chromadb.PersistentClient(
    path="./chroma_data",
    settings=Settings(anonymized_telemetry=False),
)


def _collection_name(kb_id: int) -> str:
    return f"kb_{kb_id}"


def _get_or_create(kb_id: int):
    return _client.get_or_create_collection(
        name=_collection_name(kb_id),
        embedding_function=_embedding_fn,
    )


# ── Text Chunking ─────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
    """Split long text into overlapping chunks, preferring paragraph/sentence boundaries."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    chunks: List[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + chunk_size, n)

        if end < n:
            para = text.rfind("\n\n", start, end)
            if para > start + chunk_size // 2:
                end = para
            else:
                sent = max(
                    text.rfind("。", start, end),
                    text.rfind(". ", start, end),
                    text.rfind("！", start, end),
                    text.rfind("？", start, end),
                )
                if sent > start + chunk_size // 2:
                    end = sent + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap if end < n else n

    return chunks


# ── Core Operations ───────────────────────────────────────────────────────────

def add_documents(kb_id: int, texts: List[str], ids: List[str]) -> None:
    """Store text chunks in ChromaDB (auto-embedded by the local model)."""
    collection = _get_or_create(kb_id)
    collection.add(documents=texts, ids=ids)


def query_documents(kb_id: int, query: str, n_results: int = 5) -> List[str]:
    """Retrieve the most relevant document chunks for a query."""
    collection = _get_or_create(kb_id)
    count = collection.count()
    if count == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, count),
    )

    docs = results.get("documents", [[]])[0]
    return docs if docs else []


def delete_collection(kb_id: int) -> None:
    """Delete all vector data for a knowledge base."""
    try:
        _client.delete_collection(_collection_name(kb_id))
    except Exception:
        pass
