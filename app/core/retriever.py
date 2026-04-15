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
from typing import List, Dict, Optional

from app.core.embedder import embed_query, embed_texts, summarize_customer_context
from app.core.indexer import (
    get_azure_search_client,
    load_questions_store,
    load_custom_questions_store,
)
from app.core.ingestor import load_customer_docs, load_sow_file

logger = logging.getLogger(__name__)


# Search configuration
DEFAULT_TOP_K = 20
DEFAULT_DENSE_CANDIDATES = 50


def retrieve_for_customer(
    customer_id: str,
    customers_base_dir: str = "data/customers",
    top_k: int = DEFAULT_TOP_K,
) -> Dict:
    """
    End-to-end retrieval pipeline for a single customer using Azure Cognitive Search.

    Args:
        customer_id:        folder name under customers_base_dir (e.g. "customer_1").
        customers_base_dir: base directory containing all customer folders.
        top_k:              number of final ranked questions to return.

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
        
        results.append(result_dict)

    logger.info(f"Retrieved {len(results)} questions for customer {customer_id}")

    return {
        "customer_id": customer_id,
        "context_summary": context_summary,
        "total_results": len(results),
        "questions": results,
    }


# ---------------------------------------------------------------------------
# New SOW-based retrieval pipeline
# ---------------------------------------------------------------------------

# Priority boost applied to questions matched directly from the SOW
# (vs. questions reached via an SOP intermediary)
SOW_DIRECT_BOOST = 1.5

# Maximum SOW chunks to process per file (keeps Azure Search call count bounded)
MAX_CHUNKS_PER_SOW = 15

# SOP matches per SOW chunk (used for the SOP-mediated path)
TOP_SOP_MATCHES = 5

# Direct question matches per SOW chunk
TOP_DIRECT_QUESTIONS = 10

# Questions retrieved per SOP capability match
TOP_QUESTIONS_PER_CAPABILITY = 5


def retrieve_for_sow(
    sow_file_paths: List[str],
    top_n: int = 20,
) -> Dict:
    """
    Retrieve and rank custom questions for a set of SOW files.

    Two retrieval paths are combined:
      1. **Direct** (SOW → Questions): each SOW chunk is searched directly
         against the questions index.  Results get a 1.5× score boost to
         prioritise questions with strong SOW alignment.
      2. **SOP-mediated** (SOW → SOP → Questions): each SOW chunk is first
         matched to relevant SOP chunks; the SOP capability label is then used
         to bias the questions search toward that capability's domain.

    Duplicate questions (same question_id found via both paths) are resolved
    by keeping the higher score.

    Args:
        sow_file_paths: list of paths to SOW files (.docx / .pdf / .xlsx).
        top_n:          maximum number of questions to return.

    Returns:
        Dict with keys:
            total_results  — number of questions returned
            questions      — ranked list of question dicts, each containing:
                rank, question_id, category, question, score,
                match_path, sow_context, sow_filename,
                sop_context (nullable), sop_capability (nullable)
    """
    # ── Step 1: Parse + chunk all SOW files ──────────────────────────────────
    all_chunks: List[Dict] = []
    for path in sow_file_paths:
        chunks = load_sow_file(path)
        # Sample evenly if the file produces too many chunks
        if len(chunks) > MAX_CHUNKS_PER_SOW:
            step = max(1, len(chunks) // MAX_CHUNKS_PER_SOW)
            chunks = chunks[::step][:MAX_CHUNKS_PER_SOW]
        all_chunks.extend(chunks)
        logger.info(f"SOW '{path}' → {len(chunks)} chunks (after sampling)")

    if not all_chunks:
        logger.warning("No SOW chunks produced — returning empty results")
        return {"total_results": 0, "questions": []}

    # ── Step 2: Batch-embed all SOW chunks (single API call) ─────────────────
    logger.info(f"Embedding {len(all_chunks)} SOW chunks...")
    chunk_texts = [c["chunk_text"] for c in all_chunks]
    chunk_embeddings = embed_texts(chunk_texts)

    # ── Step 3: Search ────────────────────────────────────────────────────────
    azure_client = get_azure_search_client()
    questions_store = load_custom_questions_store()

    # Accumulator: question_id → best candidate
    candidates: Dict[str, Dict] = {}

    def _update_candidate(
        qid: str,
        score: float,
        path_label: str,
        sow_chunk: str,
        sow_filename: str,
        sop_chunk: Optional[str],
        sop_capability: Optional[str],
    ) -> None:
        """Keep only the highest-score entry per question."""
        if qid not in candidates or score > candidates[qid]["score"]:
            candidates[qid] = {
                "score": score,
                "match_path": path_label,
                "sow_context": sow_chunk[:150],
                "sow_filename": sow_filename,
                "sop_context": sop_chunk[:150] if sop_chunk else None,
                "sop_capability": sop_capability,
            }

    for chunk, embedding in zip(all_chunks, chunk_embeddings):
        chunk_text = chunk["chunk_text"]
        filename = chunk["filename"]

        # ── Path 1 (Direct SOW → Questions) ──────────────────────────────────
        direct = azure_client.search_questions_hybrid(
            query_vector=embedding,
            query_text=chunk_text,
            top=TOP_DIRECT_QUESTIONS,
        )
        for q in direct.get("results", []):
            qid = q.get("question_id")
            if not qid:
                continue
            _update_candidate(
                qid=qid,
                score=q["score"] * SOW_DIRECT_BOOST,
                path_label="Direct SOW match",
                sow_chunk=chunk_text,
                sow_filename=filename,
                sop_chunk=None,
                sop_capability=None,
            )

        # ── Path 2 (SOW → SOP → Questions) ───────────────────────────────────
        sop_matches = azure_client.search_sop_hybrid(
            query_vector=embedding,
            query_text=chunk_text,
            top=TOP_SOP_MATCHES,
        )
        seen_capabilities: set = set()
        for sop in sop_matches.get("results", []):
            capability = sop.get("capability", "").strip()
            if not capability or capability in seen_capabilities:
                continue
            seen_capabilities.add(capability)

            q_via_sop = azure_client.search_questions_hybrid(
                query_vector=embedding,
                query_text=f"{chunk_text[:300]} {capability}",
                top=TOP_QUESTIONS_PER_CAPABILITY,
            )
            for q in q_via_sop.get("results", []):
                qid = q.get("question_id")
                if not qid:
                    continue
                _update_candidate(
                    qid=qid,
                    score=q["score"],  # base score — no boost
                    path_label=f"SOW \u2192 SOP ({capability})",
                    sow_chunk=chunk_text,
                    sow_filename=filename,
                    sop_chunk=sop.get("chunk_text"),
                    sop_capability=capability,
                )

    # ── Step 4: Rank and return top_n ────────────────────────────────────────
    ranked = sorted(candidates.items(), key=lambda x: x[1]["score"], reverse=True)[:top_n]

    results = []
    for rank, (qid, info) in enumerate(ranked, start=1):
        q_meta = questions_store.get(qid, {})
        results.append({
            "rank": rank,
            "question_id": qid,
            "category": q_meta.get("category", ""),
            "question": q_meta.get("question_text", ""),
            "score": round(info["score"], 6),
            "match_path": info["match_path"],
            "sow_context": info["sow_context"],
            "sow_filename": info["sow_filename"],
            "sop_context": info.get("sop_context"),
            "sop_capability": info.get("sop_capability"),
        })

    logger.info(f"retrieve_for_sow: returning {len(results)} questions from {len(candidates)} candidates")

    return {
        "total_results": len(results),
        "questions": results,
    }
