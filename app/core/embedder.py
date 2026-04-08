"""
Embedder: generates dense vectors and customer context summaries.

Two responsibilities:
  1. Embedding (Azure OpenAI text-embedding-ada-002):
       - embed_texts()  — batch-embed a list of strings into float vectors.
       - embed_query()  — embed a single query string.
       Model: text-embedding-ada-002 (1536-dim)

  2. Summarization (Azure OpenAI gpt-4o):
       - summarize_customer_context() — condense customer SOP/SOW text into
         a focused, security-themed summary used as the RAG query.

Uses Azure AD authentication with client credentials flow.
"""

import os
from typing import List
from openai import AzureOpenAI
from azure.identity import ClientSecretCredential
import time


# ---------------------------------------------------------------------------
# Lazy singletons — initialized on first call
# ---------------------------------------------------------------------------

_openai_client = None
_credential = None
_token_cache = {"token": None, "expires_at": 0}


def _get_credential() -> ClientSecretCredential:
    """Get or create Azure AD credential."""
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=os.environ["AUTH_TENANT_ID"],
            client_id=os.environ["AUTH_CLIENT_ID"],
            client_secret=os.environ["AUTH_CLIENT_SECRET"]
        )
    return _credential


def _get_token() -> str:
    """Get a valid Azure AD token, refreshing if necessary."""
    global _token_cache
    
    # Check if we have a cached token that's still valid (with 5 min buffer)
    if _token_cache["token"] and time.time() < (_token_cache["expires_at"] - 300):
        return _token_cache["token"]
    
    # Get a new token
    credential = _get_credential()
    token_result = credential.get_token(os.environ["AUTH_SCOPE"])
    
    _token_cache["token"] = token_result.token
    _token_cache["expires_at"] = token_result.expires_on
    
    return token_result.token


def _get_openai_client() -> AzureOpenAI:
    """
    Return (or create) the shared Azure OpenAI client.
    Note: Token is refreshed on each request via azure_ad_token_provider.
    """
    global _openai_client
    if _openai_client is None:
        print("Initializing Azure OpenAI client with Azure AD authentication...")
        _openai_client = AzureOpenAI(
            azure_endpoint=os.environ["OPENAI_ENDPOINT"],
            azure_ad_token_provider=_get_token,
            api_version="2024-02-01"
        )
        print("  Azure OpenAI client ready.")
    return _openai_client


# ---------------------------------------------------------------------------
# Embedding functions
# ---------------------------------------------------------------------------

def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a list of strings using Azure OpenAI text-embedding-ada-002.

    Produces 1536-dimensional float vectors via Azure OpenAI API.

    Args:
        texts: list of strings to embed.

    Returns:
        List of float vectors (one per input string), each 1536-dimensional.
    """
    client = _get_openai_client()
    
    response = client.embeddings.create(
        model=os.environ["EMBEDINGS_OPENAI_DEPLOYMENT_NAME"],
        input=texts
    )
    
    return [data.embedding for data in response.data]


def embed_query(text: str) -> List[float]:
    """
    Embed a single query string.

    Convenience wrapper around embed_texts() for the common single-query case.

    Args:
        text: the query string to embed.

    Returns:
        A single 1536-dimensional float vector.
    """
    return embed_texts([text])[0]


# ---------------------------------------------------------------------------
# Customer context summarization
# ---------------------------------------------------------------------------

def summarize_customer_context(customer_text: str) -> str:
    """
    Use Azure OpenAI (gpt-4o) to extract a focused security summary
    from customer SOP/SOW docs.

    The summary concentrates on security topics, compliance requirements,
    risk areas, and technologies — exactly the themes that map well onto
    CAIQ question domains. This summary becomes the search query fed into
    both the dense and sparse retrieval stages.

    Only the first 8000 characters of customer_text are sent to keep
    latency low and stay within context limits.

    Args:
        customer_text: combined raw text from all customer PDFs.

    Returns:
        A security-focused summary string (≤ 300 words).
    """
    client = _get_openai_client()

    response = client.chat.completions.create(
        model=os.environ["OPENAI_DEPLOYMENT_NAME"],
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
