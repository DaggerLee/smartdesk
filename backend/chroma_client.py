import re
from typing import List

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

# Uses ChromaDB's built-in ONNX embedding model — no PyTorch required
_embedding_fn = DefaultEmbeddingFunction()

# Local persistent ChromaDB with telemetry disabled
_client = chromadb.PersistentClient(
    path="./data/chroma_data",
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

def add_documents(kb_id: int, texts: List[str], ids: List[str], metadatas: List[dict]) -> None:
    """Store text chunks in ChromaDB with metadata (auto-embedded by the local model)."""
    collection = _get_or_create(kb_id)
    collection.add(documents=texts, ids=ids, metadatas=metadatas)


def query_documents(kb_id: int, query: str, n_results: int = 5) -> List[dict]:
    """Retrieve the most relevant document chunks for a query.

    Returns a list of dicts with keys: text, filename, chunk_index, distance.
    distance is a cosine distance in [0, 2]; lower means more relevant.
    """
    collection = _get_or_create(kb_id)
    count = collection.count()
    if count == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, count),
        include=["documents", "metadatas", "distances"],
    )

    docs = results.get("documents", [[]])[0] or []
    metas = results.get("metadatas", [[]])[0] or []
    dists = results.get("distances", [[]])[0] or []

    return [
        {
            "text": doc,
            "filename": (metas[i] or {}).get("filename", "Unknown"),
            "chunk_index": (metas[i] or {}).get("chunk_index", i),
            "distance": dists[i] if i < len(dists) else 2.0,
        }
        for i, doc in enumerate(docs)
    ]


def delete_documents_by_filename(kb_id: int, filename: str) -> None:
    """Delete all chunks that belong to a specific file within a knowledge base."""
    try:
        collection = _get_or_create(kb_id)
        results = collection.get(where={"filename": filename}, include=[])
        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
    except Exception:
        pass


def delete_collection(kb_id: int) -> None:
    """Delete all vector data for a knowledge base."""
    try:
        _client.delete_collection(_collection_name(kb_id))
    except Exception:
        pass
