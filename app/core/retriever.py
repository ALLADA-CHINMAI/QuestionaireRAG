"""
Retriever: hybrid search pipeline with Azure Cognitive Search.

Full pipeline for a single customer query:
  1. Load + parse customer SOP/SOW PDFs via ingestor.
  2. Summarize key security topics with Azure OpenAI gpt-4o (embedder).
  3. Embed summary as 1536-dim vector (Azure OpenAI ada-002).
  4. Run hybrid search on Azure CS CAIQ index (vector + BM25 + semantic ranking).
  5. (Optional) Index customer docs and search them as well.
  6. Merge results and enrich with full question metadata.
  7. Return top-K ranked results.

Azure Cognitive Search handles hybrid search natively:
  - Vector search (HNSW, 1536-dim, cosine similarity)
  - BM25 sparse search
  - Semantic ranking for relevance reranking
"""

import logging
from typing import List, Dict

from app.core.embedder import embed_query, summarize_customer_context
from app.core.indexer import get_azure_search_client, load_questions_store
from app.core.ingestor import load_customer_docs

logger = logging.getLogger(__name__)


# Search configuration
DEFAULT_TOP_K = 20
DEFAULT_DENSE_CANDIDATES = 50


def retrieve_for_customer(
    customer_id: str,
    customers_base_dir: str = "data/customers",
    top_k: int = DEFAULT_TOP_K,
    use_semantic_ranking: bool = True,
) -> Dict:
    """
    End-to-end retrieval pipeline for a single customer using Azure Cognitive Search.

    Args:
        customer_id:        folder name under customers_base_dir (e.g. "customer_1").
        customers_base_dir: base directory containing all customer folders.
        top_k:              number of final ranked questions to return.
        use_semantic_ranking: enable semantic ranking for better relevance (recommended).

    Returns:
        Dict with keys:
            customer_id      — echoed from input
            context_summary  — Azure OpenAI-generated summary used as the query
            total_results    — number of questions returned (≤ top_k)
            questions        — ranked list of question dicts with Azure CS scores
    """
    customer_dir = f"{customers_base_dir}/{customer_id}"
    
    # Get Azure Search client
    try:
        azure_client = get_azure_search_client()
    except ValueError as e:
        logger.error(f"Failed to initialize Azure Search client: {str(e)}")
        raise

    # ------------------------------------------------------------------
    # Step 1 — Load and combine all customer PDFs into one text block
    # ------------------------------------------------------------------
    logger.info(f"Loading customer docs from {customer_dir}...")
    customer_text = load_customer_docs(customer_dir)

    # ------------------------------------------------------------------
    # Step 2 — Summarize with Azure OpenAI gpt-4o to produce a focused search query
    # The summary highlights security topics, compliance needs, and risk areas —
    # the themes that map most directly onto CAIQ domains.
    # ------------------------------------------------------------------
    logger.info("Summarizing customer context with Azure OpenAI gpt-4o...")
    context_summary = summarize_customer_context(customer_text)
    logger.info(f"  Summary: {context_summary[:200]}...")

    # ------------------------------------------------------------------
    # Step 3 — Embed the summary query (1536-dim vector)
    # ------------------------------------------------------------------
    logger.info("Embedding query...")
    query_vector = embed_query(context_summary)

    # ------------------------------------------------------------------
    # Step 4 — Hybrid search on Azure CS CAIQ index
    # Azure CS handles both vector search (HNSW, cosine) and BM25 simultaneously,
    # with optional semantic ranking for relevance reranking.
    # ------------------------------------------------------------------
    logger.info("Running hybrid search on CAIQ index (vector + BM25 + semantic ranking)...")
    try:
        search_result = azure_client.search_caiq_hybrid(
            query_vector=query_vector,
            query_text=context_summary,
            top=top_k,
            use_semantic_ranking=use_semantic_ranking,
        )
    except Exception as e:
        logger.error(f"Hybrid search failed: {str(e)}")
        raise

    # ------------------------------------------------------------------
    # Step 5 — Enrich results with full question metadata
    # Look up each question_id in the pre-built questions store to attach
    # domain, full question text, and source file information.
    # ------------------------------------------------------------------
    logger.info("Enriching results with full metadata...")
    questions_store = load_questions_store()
    
    results = []
    for rank, azure_result in enumerate(search_result.get("results", []), start=1):
        qid = azure_result.get("question_id")
        q = questions_store.get(qid, {})
        
        result_dict = {
            "rank": rank,
            "question_id": qid,
            "domain": q.get("domain", azure_result.get("domain", "")),
            "question": q.get("question_text", azure_result.get("question_text", "")),
            "source": q.get("source", azure_result.get("source", "")),
            "score": round(azure_result.get("score", 0.0), 6),  # Hybrid score from Azure
        }
        
        # Optionally include semantic ranking score if available
        if use_semantic_ranking and "semantic_score" in azure_result:
            result_dict["semantic_score"] = round(azure_result["semantic_score"], 6)
        
        results.append(result_dict)

    logger.info(f"Retrieved {len(results)} questions for customer {customer_id}")

    return {
        "customer_id": customer_id,
        "context_summary": context_summary,
        "total_results": len(results),
        "questions": results,
    }
