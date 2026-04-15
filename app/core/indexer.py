"""
Indexer: builds and indexes the CAIQ dataset to Azure Cognitive Search.

Uses Azure Cognitive Search with hybrid indexing:
  1. Dense indexing — 1536-dim embeddings for semantic vector search (HNSW algorithm)
  2. Sparse indexing — BM25 keyword search built-in to Azure CS
  3. questions_store — JSON lookup table mapping question_id → full metadata,
                       used to enrich search results at query time.

The CAIQ questions are indexed to Azure Cognitive Search and persisted there.
Metadata is also cached locally in questions_store.json for quick enrichment.
"""

import json
import os
import logging
from pathlib import Path
from typing import List, Dict, Optional

from app.core.azure_search import AzureSearchClient
from app.core.embedder import embed_texts
from app.core.ingestor import load_caiq_questions, load_sop_file, load_questions_xlsx

logger = logging.getLogger(__name__)


# Configuration
# JSON file mapping question_id → full question dict (for result enrichment)
QUESTIONS_STORE_PATH = "data/questions_store.json"
SOP_STORE_PATH = "data/sop_store.json"
CUSTOM_QUESTIONS_STORE_PATH = "data/custom_questions_store.json"

# Azure Search configuration (loaded from environment)
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
AZURE_SEARCH_CAIQ_INDEX_NAME = os.getenv("AZURE_SEARCH_CAIQ_INDEX_NAME", "caiq_questions")
AZURE_SEARCH_SOP_INDEX_NAME = os.getenv("AZURE_SEARCH_SOP_INDEX_NAME", "sop_chunks")
AZURE_SEARCH_QUESTIONS_INDEX_NAME = os.getenv("AZURE_SEARCH_QUESTIONS_INDEX_NAME", "custom_questions")


def sanitize_azure_key(key: str) -> str:
    """
    Sanitize a key to make it Azure Cognitive Search compliant.
    Azure keys can only contain letters, digits, underscore (_), dash (-), or equal sign (=).
    
    Args:
        key: Original key that may contain invalid characters like & or spaces
        
    Returns:
        Sanitized key with invalid characters replaced by underscore
    """
    import re
    # Replace any character that's NOT a letter, digit, underscore, dash, or equal sign
    sanitized = re.sub(r'[^a-zA-Z0-9_\-=]', '_', key)
    return sanitized

def build_index(xlsx_path: str) -> int:
    """
    Full index build pipeline with Azure Cognitive Search:
      1. Parse CAIQ XLSX → list of question dicts.
      2. Embed all question texts → dense vectors (OpenAI, 1536-dim).
      3. Format and upload to Azure Cognitive Search (hybrid index with HNSW + BM25).
      4. Cache question metadata locally in questions_store.json.

    Existing documents in Azure CS are replaced on each call so the index
    always reflects the current CAIQ file.

    Args:
        xlsx_path: path to the CAIQ Excel file.

    Returns:
        Number of questions successfully indexed.
    """
    # Verify Azure Search credentials are available
    if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_API_KEY:
        raise ValueError(
            "Azure Cognitive Search credentials not found in environment. "
            "Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY in .env"
        )
    
    # Step 1 — Parse CAIQ XLSX into structured question dicts
    logger.info("Loading questions from CAIQ...")
    questions = load_caiq_questions(xlsx_path)
    logger.info(f"  Found {len(questions)} questions")

    # Extract parallel lists of texts and IDs for indexing
    texts = [q["question_text"] for q in questions]
    ids = [q["question_id"] for q in questions]

    # Step 2 — Embed all questions (dense vectors, 1536-dim via Azure OpenAI)
    logger.info("Embedding questions (dense, 1536-dim)...")
    embeddings = embed_texts(texts)
    logger.info(f"  Generated {len(embeddings)} embeddings")

    # Step 3 — Initialize Azure Cognitive Search client
    logger.info("Initializing Azure Cognitive Search client...")
    client = AzureSearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        api_key=AZURE_SEARCH_API_KEY,
        caiq_index_name=AZURE_SEARCH_CAIQ_INDEX_NAME
    )
    
    # Verify connection
    if not client.health_check():
        raise ConnectionError("Failed to connect to Azure Cognitive Search service")
    
    logger.info("Azure Cognitive Search connection verified")

    # Step 4 — Format documents for Azure CS
    # Azure CS will handle BM25 indexing automatically (no explicit BM25 training needed)
    logger.info("Formatting documents for Azure Cognitive Search...")
    documents = []
    for i, question_id in enumerate(ids):
        q = questions[i]
        doc = {
            "id": sanitize_azure_key(question_id),  # Sanitize ID (replace & and other invalid chars with _)
            "question_id": question_id,  # Keep original ID for reference
            "domain": q.get("domain", ""),
            "question_text": q.get("question_text", ""),
            "source": q.get("source", "CAIQ"),
            "vector": embeddings[i],  # 1536-dim embedding for hybrid search
        }
        documents.append(doc)

    # Step 5 — Upload to Azure Cognitive Search
    logger.info(f"Uploading {len(documents)} documents to Azure Cognitive Search...")
    result = client.index_caiq(documents)
    
    succeeded = result.get("succeeded", 0)
    failed = result.get("failed", 0)
    
    if failed > 0:
        logger.warning(f"  {failed} documents failed to index")
        errors = result.get("errors", [])
        if errors:
            logger.warning(f"  Sample errors: {errors[:3]}")
    
    logger.info(f"  Successfully indexed {succeeded} documents to Azure CS")

    # Step 6 — Cache question metadata locally for rapid enrichment at query time
    logger.info(f"Caching question metadata to {QUESTIONS_STORE_PATH}...")
    Path(QUESTIONS_STORE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(QUESTIONS_STORE_PATH, "w") as f:
        json.dump({q["question_id"]: q for q in questions}, f, indent=2)
    logger.info(f"  Question store saved")

    return succeeded


# ---------------------------------------------------------------------------
# Index loaders (called at query time)
# ---------------------------------------------------------------------------

def build_sop_index(sop_files: List[tuple]) -> int:
    """
    Parse, embed, and index SOP files into the SOP chunks Azure index.

    Args:
        sop_files: list of (file_path, capability) tuples.

    Returns:
        Number of chunks successfully indexed.
    """
    if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_API_KEY:
        raise ValueError(
            "Azure Cognitive Search credentials not found. "
            "Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY in .env"
        )

    # Step 1 — Parse all SOP files into chunks
    logger.info(f"Parsing {len(sop_files)} SOP file(s)...")
    all_chunks: List[Dict] = []
    for file_path, capability in sop_files:
        chunks = load_sop_file(file_path, capability)
        all_chunks.extend(chunks)
        logger.info(f"  {file_path} → {len(chunks)} chunks (capability: {capability})")

    if not all_chunks:
        logger.warning("No SOP chunks produced — nothing to index")
        return 0

    # Step 2 — Batch embed all chunks
    logger.info(f"Embedding {len(all_chunks)} SOP chunks...")
    texts = [c["chunk_text"] for c in all_chunks]
    embeddings = embed_texts(texts)

    # Step 3 — Build Azure documents
    documents = []
    for i, chunk in enumerate(all_chunks):
        doc_id = sanitize_azure_key(chunk["chunk_id"])
        documents.append({
            "id": doc_id,
            "chunk_id": chunk["chunk_id"],
            "filename": chunk["filename"],
            "capability": chunk["capability"],
            "chunk_text": chunk["chunk_text"],
            "chunk_index": chunk["chunk_index"],
            "vector": embeddings[i],
        })

    # Step 4 — Upload to Azure
    logger.info(f"Uploading {len(documents)} SOP chunks to Azure Cognitive Search...")
    client = AzureSearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        api_key=AZURE_SEARCH_API_KEY,
        caiq_index_name=AZURE_SEARCH_CAIQ_INDEX_NAME,
        sop_index_name=AZURE_SEARCH_SOP_INDEX_NAME,
        questions_index_name=AZURE_SEARCH_QUESTIONS_INDEX_NAME,
    )
    result = client.index_sop_chunks(documents)
    succeeded = result.get("succeeded", 0)
    failed = result.get("failed", 0)
    if failed:
        logger.warning(f"  {failed} SOP chunks failed to index")

    # Step 5 — Cache SOP chunk metadata locally
    logger.info(f"Caching SOP chunk metadata to {SOP_STORE_PATH}...")
    Path(SOP_STORE_PATH).parent.mkdir(parents=True, exist_ok=True)
    sop_store = {c["chunk_id"]: c for c in all_chunks}
    with open(SOP_STORE_PATH, "w") as f:
        json.dump(sop_store, f, indent=2)

    logger.info(f"  Successfully indexed {succeeded} SOP chunks")
    return succeeded


def build_questions_index(xlsx_path: str) -> int:
    """
    Parse, embed, and index a questions Excel file into the custom questions Azure index.

    Expected Excel columns: 'category' and 'question' (case-insensitive header detection).

    Args:
        xlsx_path: path to the questions Excel file.

    Returns:
        Number of questions successfully indexed.
    """
    if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_API_KEY:
        raise ValueError(
            "Azure Cognitive Search credentials not found. "
            "Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY in .env"
        )

    # Step 1 — Parse questions
    logger.info("Parsing questions from Excel...")
    questions = load_questions_xlsx(xlsx_path)
    logger.info(f"  Found {len(questions)} questions")

    if not questions:
        raise ValueError("No questions found in the uploaded Excel file. "
                         "Ensure it has 'category' and 'question' columns.")

    # Step 2 — Batch embed
    logger.info("Embedding questions...")
    texts = [q["question_text"] for q in questions]
    embeddings = embed_texts(texts)

    # Step 3 — Build Azure documents
    documents = []
    for i, q in enumerate(questions):
        doc_id = sanitize_azure_key(q["question_id"])
        documents.append({
            "id": doc_id,
            "question_id": q["question_id"],
            "category": q["category"],
            "question_text": q["question_text"],
            "vector": embeddings[i],
        })

    # Step 4 — Upload to Azure
    logger.info(f"Uploading {len(documents)} questions to Azure Cognitive Search...")
    client = AzureSearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        api_key=AZURE_SEARCH_API_KEY,
        caiq_index_name=AZURE_SEARCH_CAIQ_INDEX_NAME,
        sop_index_name=AZURE_SEARCH_SOP_INDEX_NAME,
        questions_index_name=AZURE_SEARCH_QUESTIONS_INDEX_NAME,
    )
    result = client.index_questions(documents)
    succeeded = result.get("succeeded", 0)
    failed = result.get("failed", 0)
    if failed:
        logger.warning(f"  {failed} questions failed to index")

    # Step 5 — Cache question metadata locally
    logger.info(f"Caching question metadata to {CUSTOM_QUESTIONS_STORE_PATH}...")
    Path(CUSTOM_QUESTIONS_STORE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(CUSTOM_QUESTIONS_STORE_PATH, "w") as f:
        json.dump({q["question_id"]: q for q in questions}, f, indent=2)

    logger.info(f"  Successfully indexed {succeeded} questions")
    return succeeded


def get_azure_search_client() -> AzureSearchClient:
    """
    Get initialized Azure Cognitive Search client (all indexes configured).

    Returns:
        AzureSearchClient instance configured from environment variables.

    Raises:
        ValueError if credentials are not available.
    """
    if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_API_KEY:
        raise ValueError(
            "Azure Cognitive Search credentials not found. "
            "Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY in .env"
        )

    return AzureSearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        api_key=AZURE_SEARCH_API_KEY,
        caiq_index_name=AZURE_SEARCH_CAIQ_INDEX_NAME,
        sop_index_name=AZURE_SEARCH_SOP_INDEX_NAME,
        questions_index_name=AZURE_SEARCH_QUESTIONS_INDEX_NAME,
    )


def load_chroma_collection():
    """
    [DEPRECATED] Load the persisted ChromaDB collection for querying.
    
    This function is kept for backwards compatibility but should not be used.
    Use get_azure_search_client() instead for Azure Cognitive Search.
    """
    raise DeprecationWarning(
        "Chroma DB is deprecated. Use get_azure_search_client() for Azure Cognitive Search."
    )


def load_bm25_index() -> Dict:
    """
    [DEPRECATED] Load the persisted BM25 index from disk.
    
    This function is kept for backwards compatibility but should not be used.
    Azure Cognitive Search handles BM25 indexing natively.
    """
    raise DeprecationWarning(
        "BM25 is deprecated. Azure Cognitive Search handles hybrid (BM25 + vector) search natively."
    )


def load_questions_store() -> Dict:
    """
    Load the full question metadata store from disk.

    Returns:
        Dict mapping question_id (str) → question dict with all fields.
    """
    if not os.path.exists(QUESTIONS_STORE_PATH):
        raise FileNotFoundError(f"Questions store not found at {QUESTIONS_STORE_PATH}")

    with open(QUESTIONS_STORE_PATH, "r") as f:
        return json.load(f)


def load_custom_questions_store() -> Dict:
    """
    Load the custom questions metadata store from disk (new flow).

    Returns:
        Dict mapping question_id (str) → {question_id, category, question_text}.
    """
    if not os.path.exists(CUSTOM_QUESTIONS_STORE_PATH):
        raise FileNotFoundError(
            f"Custom questions store not found at {CUSTOM_QUESTIONS_STORE_PATH}. "
            "Upload and index a questions Excel file first."
        )

    with open(CUSTOM_QUESTIONS_STORE_PATH, "r") as f:
        return json.load(f)


def index_is_built() -> bool:
    """
    Check whether the CAIQ index is built in Azure Cognitive Search (legacy).

    Returns:
        True if questions_store.json exists and Azure CS is reachable.
    """
    if not os.path.exists(QUESTIONS_STORE_PATH):
        return False

    try:
        client = get_azure_search_client()
        return client.health_check()
    except Exception as e:
        logger.warning(f"Index health check failed: {str(e)}")
        return False


def sop_index_is_built() -> bool:
    """
    Check whether the SOP chunks index has been built.

    Returns:
        True if sop_store.json exists on disk.
    """
    return os.path.exists(SOP_STORE_PATH)


def questions_index_is_built() -> bool:
    """
    Check whether the custom questions index has been built.

    Returns:
        True if custom_questions_store.json exists on disk.
    """
    return os.path.exists(CUSTOM_QUESTIONS_STORE_PATH)
