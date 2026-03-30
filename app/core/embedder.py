"""
Embedder: generates dense vectors and customer context summaries.

Two responsibilities:
  1. Embedding (HuggingFace sentence-transformers — local, free, no API key):
       - embed_texts()  — batch-embed a list of strings into float vectors.
       - embed_query()  — embed a single query string.
       Model: all-MiniLM-L6-v2 (384-dim, ~90MB, downloads once on first run)

  2. Summarization (Groq free API — llama-3.1-8b-instant):
       - summarize_customer_context() — condense customer SOP/SOW text into
         a focused, security-themed summary used as the RAG query.

Both clients are lazily initialized on first use and reused across calls.
"""

import os
from typing import List

from groq import Groq
from sentence_transformers import SentenceTransformer


# ---------------------------------------------------------------------------
# Lazy singletons — initialized on first call
# ---------------------------------------------------------------------------

_embedding_model = None
_groq_client = None


def _get_embedding_model() -> SentenceTransformer:
    """
    Return (or load) the local sentence-transformer model.
    Downloads ~90MB on first run, then cached locally by HuggingFace.
    """
    global _embedding_model
    if _embedding_model is None:
        print("Loading embedding model (all-MiniLM-L6-v2)...")
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("  Embedding model ready.")
    return _embedding_model


def _get_groq() -> Groq:
    """Return (or create) the shared Groq client."""
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


# ---------------------------------------------------------------------------
# Embedding functions
# ---------------------------------------------------------------------------

def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a list of strings using the local all-MiniLM-L6-v2 model.

    Runs entirely on CPU — no API call, no cost.
    Produces 384-dimensional float vectors.

    Args:
        texts: list of strings to embed.

    Returns:
        List of float vectors (one per input string), each 384-dimensional.
    """
    model = _get_embedding_model()

    # encode() returns a numpy array; convert to plain Python list for ChromaDB
    embeddings = model.encode(texts, show_progress_bar=False)
    return embeddings.tolist()


def embed_query(text: str) -> List[float]:
    """
    Embed a single query string.

    Convenience wrapper around embed_texts() for the common single-query case.

    Args:
        text: the query string to embed.

    Returns:
        A single 384-dimensional float vector.
    """
    return embed_texts([text])[0]


# ---------------------------------------------------------------------------
# Customer context summarization
# ---------------------------------------------------------------------------

def summarize_customer_context(customer_text: str) -> str:
    """
    Use Groq (llama-3.1-8b-instant) to extract a focused security summary
    from customer SOP/SOW docs.

    The summary concentrates on security topics, compliance requirements,
    risk areas, and technologies — exactly the themes that map well onto
    CAIQ question domains. This summary becomes the search query fed into
    both the dense and sparse retrieval stages.

    Only the first 8000 characters of customer_text are sent to keep
    latency low and stay within the free tier context limits.

    Args:
        customer_text: combined raw text from all customer PDFs.

    Returns:
        A security-focused summary string (≤ 300 words).
    """
    client = _get_groq()

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",   # free tier model on Groq
        max_tokens=400,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a security compliance expert. "
                    "Extract and summarize key security topics, compliance requirements, "
                    "risk areas, technologies, and operational themes from customer documents. "
                    "Be specific and use technical security terminology. Keep it under 300 words."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Summarize the key security topics and requirements from these customer documents:\n\n"
                    f"{customer_text[:8000]}"
                ),
            },
        ],
    )
    return response.choices[0].message.content.strip()
