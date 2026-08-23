import re
import unicodedata
from typing import List

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

import config

# Multilingual model (was DefaultEmbeddingFunction, English-only ONNX model —
# it embedded Chinese KB content poorly, see config.RELEVANCE_THRESHOLD comment).
_embedding_fn = SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)

# Local persistent ChromaDB with telemetry disabled
_client = chromadb.PersistentClient(
    path="./data/chroma_data",
    settings=Settings(anonymized_telemetry=False),
)


def _collection_name(kb_id: int) -> str:
    return f"kb_{str(kb_id)}"


def _get_or_create(kb_id: int):
    return _client.get_or_create_collection(
        name=_collection_name(kb_id),
        embedding_function=_embedding_fn,
    )


# ── Text Chunking ─────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP) -> List[str]:
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


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#_.-]*|[\u4e00-\u9fff]+", re.IGNORECASE)
_RERANK_MIN_CANDIDATES = 50
_RERANK_MULTIPLIER = 4
_CJK_QUESTION_TERMS = (
    "有没有",
    "有多少种",
    "有几种",
    "是什么",
    "为什么",
    "怎么",
    "如何",
    "什么",
    "多少",
    "几种",
    "哪些",
    "哪几",
    "哪三",
)
_CJK_GENERIC_MARKERS = ("什么", "多少", "几", "哪", "谁", "吗", "是否", "有没有")
_CJK_LOW_SIGNAL_TERMS = {"共同", "解决", "问题", "分别"}
_CJK_FILLER_CHARS = "的了在里和与及是有"


def _normalize_for_match(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _cjk_terms(token: str) -> set[str]:
    compact = token
    for question_term in _CJK_QUESTION_TERMS:
        compact = compact.replace(question_term, "")
    compact = "".join(ch for ch in compact if ch not in _CJK_FILLER_CHARS)
    if len(compact) < 2 or any(marker in compact for marker in _CJK_GENERIC_MARKERS):
        return set()

    terms = {compact}
    if len(compact) > 4:
        terms.update(compact[i : i + 2] for i in range(0, len(compact) - 1))
        terms.update(compact[i : i + 3] for i in range(0, len(compact) - 2))
    return {
        term
        for term in terms
        if term not in _CJK_LOW_SIGNAL_TERMS
        and not any(low_signal in term for low_signal in _CJK_LOW_SIGNAL_TERMS)
        and not any(marker in term for marker in _CJK_GENERIC_MARKERS)
    }


def _query_terms(query: str) -> set[str]:
    norm = _normalize_for_match(query)
    terms: set[str] = set()
    previous_latin: str | None = None
    for token in _TOKEN_RE.findall(norm):
        if _CJK_RE.fullmatch(token):
            terms.update(_cjk_terms(token))
            previous_latin = None
        elif len(token) >= 2:
            terms.add(token)
            if previous_latin:
                terms.add(f"{previous_latin} {token}")
            previous_latin = token
    return terms


def _term_in_document(term: str, document: str) -> bool:
    if _CJK_RE.search(term):
        return term in document
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", document) is not None


def _lexical_score(terms: set[str], document: str) -> int:
    doc = _normalize_for_match(document)
    return sum(1 for term in terms if _term_in_document(term, doc))


def _strong_lexical_match(terms: set[str], document: str) -> bool:
    doc = _normalize_for_match(document)
    cjk_hits = 0
    for term in terms:
        if _CJK_RE.search(term):
            if term in doc:
                if len(term) >= 4:
                    return True
                cjk_hits += 1
        elif len(term) >= 4 and _term_in_document(term, doc):
            return True
    return cjk_hits >= 2


def _candidate_count(requested: int, collection_count: int) -> int:
    broad = max(requested, _RERANK_MIN_CANDIDATES, requested * _RERANK_MULTIPLIER)
    return min(collection_count, broad)


# ── Core Operations ───────────────────────────────────────────────────────────

def add_documents(kb_id: int, texts: List[str], ids: List[str], metadatas: List[dict]) -> None:
    """Store text chunks in ChromaDB with metadata (auto-embedded by the local model)."""
    collection = _get_or_create(kb_id)
    collection.add(documents=texts, ids=ids, metadatas=metadatas)


def query_documents(kb_id: int, query: str, n_results: int = config.TOP_K) -> List[dict]:
    """Retrieve the most relevant document chunks for a query.

    Returns a list of dicts with keys: text, filename, chunk_index, distance,
    lexical_score, strong_lexical_match. distance is a cosine distance in [0, 2];
    lower means more relevant.
    """
    collection = _get_or_create(kb_id)
    count = collection.count()
    if count == 0:
        return []

    candidate_count = _candidate_count(n_results, count)
    results = collection.query(
        query_texts=[query],
        n_results=candidate_count,
        include=["documents", "metadatas", "distances"],
    )

    docs = results.get("documents", [[]])[0] or []
    metas = results.get("metadatas", [[]])[0] or []
    dists = results.get("distances", [[]])[0] or []

    query_terms = _query_terms(query)
    rows = []
    for i, doc in enumerate(docs):
        lexical_score = _lexical_score(query_terms, doc)
        strong_lexical_match = _strong_lexical_match(query_terms, doc)
        rows.append(
            {
                "text": doc,
                "filename": (metas[i] or {}).get("filename", "Unknown"),
                "chunk_index": (metas[i] or {}).get("chunk_index", i),
                "distance": dists[i] if i < len(dists) else 2.0,
                "lexical_score": lexical_score,
                "strong_lexical_match": strong_lexical_match,
            }
        )
    rows.sort(
        key=lambda row: (
            not row["strong_lexical_match"],
            -row["lexical_score"] if row["strong_lexical_match"] else 0,
            row["distance"],
        )
    )
    return rows[:n_results]


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
