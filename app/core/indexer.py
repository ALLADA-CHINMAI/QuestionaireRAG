"""
Indexer: builds and persists the hybrid search indexes from the CAIQ dataset.

Two indexes are created and saved to disk:
  1. ChromaDB (dense)  — cosine-similarity vector store for semantic search.
  2. BM25 (sparse)     — keyword-frequency index for exact/term-based search.
  3. questions_store   — JSON lookup table mapping question_id → full metadata,
                         used to enrich search results at query time.

All artifacts are written under the data/ directory so they survive
server restarts without re-indexing.
"""

import json
import pickle
from pathlib import Path
from typing import List, Dict

import chromadb
from rank_bm25 import BM25Okapi

from app.core.embedder import embed_texts
from app.core.ingestor import load_caiq_questions


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

# Directory where ChromaDB persists its HNSW vector index
CHROMA_PERSIST_DIR = "data/chroma_db"

# Pickle file storing the BM25 model and the ordered list of question IDs
BM25_INDEX_PATH = "data/bm25_index.pkl"

# JSON file mapping question_id → full question dict (for result enrichment)
QUESTIONS_STORE_PATH = "data/questions_store.json"

# Name of the ChromaDB collection
COLLECTION_NAME = "caiq_questions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """
    Minimal tokenizer for BM25: lowercase + whitespace split.

    BM25 operates on token lists. This keeps tokenization simple and
    consistent between index-time and query-time.
    """
    return text.lower().split()


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

def build_index(xlsx_path: str) -> int:
    """
    Full index build pipeline:
      1. Parse CAIQ XLSX → list of question dicts.
      2. Embed all question texts → dense vectors (OpenAI).
      3. Store vectors + metadata in a ChromaDB persistent collection.
      4. Build a BM25 sparse index over the same question texts.
      5. Persist BM25 + question store to disk.

    Existing indexes are dropped and rebuilt on each call so the index
    always reflects the current CAIQ file.

    Args:
        xlsx_path: path to the CAIQ Excel file.

    Returns:
        Number of questions successfully indexed.
    """
    # Step 1 — Parse CAIQ XLSX into structured question dicts
    print("Loading questions from CAIQ...")
    questions = load_caiq_questions(xlsx_path)
    print(f"  Found {len(questions)} questions")

    # Extract parallel lists of texts and IDs for indexing
    texts = [q["question_text"] for q in questions]
    ids = [q["question_id"] for q in questions]

    # ------------------------------------------------------------------
    # Step 2 & 3 — Dense index via ChromaDB
    # ------------------------------------------------------------------
    print("Embedding questions (dense)...")
    # Call OpenAI embeddings API in batches — returns 1536-dim vectors
    embeddings = embed_texts(texts)

    # Use PersistentClient so the index survives restarts
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)

    # Drop the old collection if it exists so we start clean
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # Collection didn't exist yet — that's fine

    # Create collection with cosine distance space (suited for text similarity)
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Attach domain and source as metadata for optional filtered queries later
    metadatas = [
        {"domain": q["domain"], "source": q["source"]}
        for q in questions
    ]

    # Add all vectors, raw text documents, and metadata in one batch call
    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    print(f"  ChromaDB collection built: {len(questions)} vectors")

    # ------------------------------------------------------------------
    # Step 4 & 5 — Sparse index via BM25
    # ------------------------------------------------------------------
    print("Building BM25 index (sparse)...")

    # Tokenize every question text for BM25 training
    tokenized = [_tokenize(t) for t in texts]

    # BM25Okapi trains on the corpus at construction time
    bm25 = BM25Okapi(tokenized)

    # Persist the BM25 model and the ordered ID list together so we can
    # map BM25 score positions back to question_ids at query time
    Path(BM25_INDEX_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(BM25_INDEX_PATH, "wb") as f:
        pickle.dump({"bm25": bm25, "ids": ids}, f)
    print(f"  BM25 index saved to {BM25_INDEX_PATH}")

    # ------------------------------------------------------------------
    # Save full question metadata store (used for result enrichment)
    # ------------------------------------------------------------------
    with open(QUESTIONS_STORE_PATH, "w") as f:
        json.dump({q["question_id"]: q for q in questions}, f, indent=2)
    print(f"  Question store saved to {QUESTIONS_STORE_PATH}")

    return len(questions)


# ---------------------------------------------------------------------------
# Index loaders (called at query time)
# ---------------------------------------------------------------------------

def load_chroma_collection():
    """
    Load the persisted ChromaDB collection for querying.

    Returns the ChromaDB Collection object ready for .query() calls.
    """
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    return client.get_collection(COLLECTION_NAME)


def load_bm25_index() -> Dict:
    """
    Load the persisted BM25 index from disk.

    Returns:
        Dict with two keys:
            'bm25' — trained BM25Okapi model
            'ids'  — ordered list of question_ids matching BM25 corpus positions
    """
    with open(BM25_INDEX_PATH, "rb") as f:
        return pickle.load(f)


def load_questions_store() -> Dict:
    """
    Load the full question metadata store from disk.

    Returns:
        Dict mapping question_id (str) → question dict with all fields.
    """
    with open(QUESTIONS_STORE_PATH, "r") as f:
        return json.load(f)


def index_is_built() -> bool:
    """
    Check whether all index artifacts exist on disk.

    Used by the API to guard /query calls before /index has been run.
    """
    return (
        Path(BM25_INDEX_PATH).exists()
        and Path(QUESTIONS_STORE_PATH).exists()
        and Path(CHROMA_PERSIST_DIR).exists()
    )
