"""
Retriever: hybrid search pipeline with Azure Cognitive Search.

Full pipeline for a single customer query:
  1. Load + parse customer SOP/SOW PDFs via ingestor.
  2. Summarize key security topics with Azure OpenAI gpt-4o (embedder).
  3. Embed summary as 1536-dim vector (Azure OpenAI ada-002).
  4. Run hybrid search on Azure CS PSmart questions index (vector + BM25 + semantic ranking).
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
    load_psmart_questions_store,
)
from app.core.ingestor import load_customer_docs, load_sow_file
from app.core.reranker import rerank_questions_with_gpt4o

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
    # the themes that map most directly onto PSmart question domains.
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
    # Step 4 — Hybrid search on Azure CS PSmart questions index
    # Azure CS handles both vector search (HNSW, cosine) and BM25 simultaneously,
    # with optional semantic ranking for relevance reranking.
    # ------------------------------------------------------------------
    logger.info("Running hybrid search on PSmart questions index (vector + BM25 + semantic ranking)...")
    try:
        search_result = azure_client.search_questions_hybrid(
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
SOW_DIRECT_BOOST = 1.2

# Boost for questions found via SOP matches (can compete with or override direct matches if more relevant)
SOW_VIA_SOP_BOOST = 1

# Maximum SOW chunks to process per file (keeps Azure Search call count bounded)
MAX_CHUNKS_PER_SOW = 15

# SOP matches per SOW chunk (used for the SOP-mediated path)
TOP_SOP_MATCHES = 5

# Questions to search per SOP chunk (via SOP path)
TOP_QUESTIONS_PER_SOP = 8

# Direct question matches per SOW chunk
TOP_DIRECT_QUESTIONS = 10

# Questions retrieved per SOP capability match
TOP_QUESTIONS_PER_CAPABILITY = 5


def retrieve_for_sow(
    sow_file_paths: List[str],
    top_n: int = 20,
    use_gpt4o_reranking: bool = True,  # NEW: Enable/disable GPT-4o re-ranking
) -> Dict:
    """
    Retrieve and rank custom questions for a set of SOW files.

    Two-stage retrieval strategy:
      1. **Vector Search** (SOW → Questions): Fast hybrid search to get 
         top 50 candidates using embeddings + BM25 + 1.2× SOW boost.
      2. **GPT-4o Re-ranking**: Deep semantic analysis to score top 50
         candidates based on actual relevance to SOW requirements.

    Also retrieves SOP chunks for additional context enrichment.

    Args:
        sow_file_paths:        list of paths to SOW files (.docx / .pdf / .xlsx).
        top_n:                 maximum number of questions to return.
        use_gpt4o_reranking:   if True, use GPT-4o for re-ranking (recommended).

    Returns:
        Dict with keys:
            total_results  — number of questions returned
            questions      — ranked list of question dicts, each containing:
                rank, question_id, category, question, score,
                match_path, sow_context, sow_filename,
                sop_context (nullable), explanation (if GPT-4o used)
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
    questions_store = load_psmart_questions_store()

    # Accumulator: question_id → best candidate
    candidates: Dict[str, Dict] = {}
    
    # Collect SOP contexts for enrichment
    all_sop_contexts: List[str] = []

    def _update_candidate(
        qid: str,
        score: float,
        path_label: str,
        sow_chunk: str,
        sow_filename: str,
        sop_chunk: Optional[str] = None,
    ) -> None:
        """Keep only the highest-score entry per question."""
        if qid not in candidates or score > candidates[qid]["score"]:
            candidates[qid] = {
                "score": score,
                "vector_score": score,  # Keep original vector score
                "match_path": path_label,
                "sow_context": sow_chunk[:400],  # Increased from 150
                "sow_filename": sow_filename,
                "sop_context": sop_chunk[:400] if sop_chunk else None,  # Increased from 150
                "full_sow_chunk": sow_chunk,  # Keep full text for GPT-4o
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
            )

        # ── Path 2 (SOW → SOP → Questions) ───────────────────────────────────
        sop_matches = azure_client.search_sop_hybrid(
            query_vector=embedding,
            query_text=chunk_text,
            top=TOP_SOP_MATCHES,
        )
        
        # Collect SOP chunks for embedding and question search
        sop_chunks_for_search = []
        for sop in sop_matches.get("results", []):
            sop_text = sop.get("chunk_text", "")
            sop_score = sop.get("score", 0.0)
            
            if not sop_text:
                continue
                
            # Collect SOP chunks for context enrichment
            if sop_text not in all_sop_contexts:
                all_sop_contexts.append(sop_text)
            
            sop_chunks_for_search.append({
                "text": sop_text,
                "score": sop_score,
            })
        
        # Embed all SOP chunks and search for questions via SOPs
        if sop_chunks_for_search:
            sop_texts = [s["text"] for s in sop_chunks_for_search]
            sop_embeddings = embed_texts(sop_texts)
            
            for sop_chunk, sop_embedding in zip(sop_chunks_for_search, sop_embeddings):
                sop_text = sop_chunk["text"]
                sop_score = sop_chunk["score"]
                
                # Use SOP chunk to find related questions
                sop_questions = azure_client.search_questions_hybrid(
                    query_vector=sop_embedding,
                    query_text=sop_text,
                    top=TOP_QUESTIONS_PER_SOP,
                )
                
                for q in sop_questions.get("results", []):
                    qid = q.get("question_id")
                    if not qid:
                        continue
                        
                    # Combined score: SOP relevance × Question match × Boost
                    # This allows SOP-based matches to compete with direct matches
                    combined_score = (sop_score * 0.5 + q["score"] * 0.5) * SOW_VIA_SOP_BOOST
                    
                    _update_candidate(
                        qid=qid,
                        score=combined_score,
                        path_label="Via SOP match",
                        sow_chunk=chunk_text,
                        sow_filename=filename,
                        sop_chunk=sop_text,
                    )

    logger.info(f"Vector search found {len(candidates)} unique question candidates")
    logger.info(f"Retrieved {len(all_sop_contexts)} SOP chunks for context")
    
    # Log match type breakdown
    direct_matches = sum(1 for c in candidates.values() if c["match_path"] == "Direct SOW match")
    via_sop_matches = sum(1 for c in candidates.values() if c["match_path"] == "Via SOP match")
    logger.info(f"  📊 Match breakdown: {direct_matches} Direct SOW matches, {via_sop_matches} Via SOP matches")

    # ── Step 4: GPT-4o Re-ranking (if enabled) ───────────────────────────────
    if use_gpt4o_reranking and candidates:
        logger.info("Starting GPT-4o re-ranking...")
        
        # Convert candidates dict to list format for reranker
        candidate_list = []
        for qid, info in candidates.items():
            q_meta = questions_store.get(qid, {})
            candidate_list.append({
                "question_id": qid,
                "question": q_meta.get("question_text", ""),
                "category": q_meta.get("category", ""),
                "score": info["score"],
                "vector_score": info["vector_score"],  # Keep original vector score
                "match_path": info["match_path"],
                "sow_context": info["sow_context"],
                "sow_filename": info["sow_filename"],
                "sop_context": info.get("sop_context"),
                "full_sow_chunk": info.get("full_sow_chunk", ""),
            })
        
        # Combine SOW contexts for GPT-4o
        combined_sow_context = "\n\n".join([c["chunk_text"] for c in all_chunks[:5]])
        
        # Re-rank with GPT-4o (gets top 50, scores them, returns top_n)
        try:
            reranked_results = rerank_questions_with_gpt4o(
                candidates=candidate_list[:50],  # Top 50 from vector search
                sow_context=combined_sow_context,
                sop_contexts=all_sop_contexts,
                top_n=top_n,
                batch_size=10
            )
            
            # Debug: Show what reranker returned
            if reranked_results:
                first_result = reranked_results[0]
                logger.info(f"First reranked result: qid={first_result.get('question_id')}, "
                           f"score={first_result.get('score')}, vector_score={first_result.get('vector_score')}, "
                           f"has_explanation={bool(first_result.get('explanation'))}, "
                           f"explanation_preview='{str(first_result.get('explanation', ''))[:80]}'")
            
            # Format final results
            results = []
            for item in reranked_results:
                result_obj = {
                    "rank": item["rank"],
                    "question_id": item["question_id"],
                    "category": item["category"],
                    "question": item["question"],
                    "score": round(item.get("score", 0), 2),  # GPT-4o score (0-10)
                    "vector_score": round(item.get("vector_score", 0), 3),  # Original vector score
                    "match_path": item["match_path"],
                    "sow_context": item["sow_context"],
                    "sow_filename": item["sow_filename"],
                    "sop_context": item.get("sop_context"),
                    "explanation": item.get("explanation", ""),  # NEW: GPT-4o explanation
                }
                results.append(result_obj)
                
                # Debug first result
                if len(results) == 1:
                    logger.info(f"Final result #1 being returned: score={result_obj['score']}, "
                               f"vector_score={result_obj['vector_score']}, "
                               f"explanation='{result_obj['explanation'][:100] if result_obj['explanation'] else 'EMPTY'}'")
            
            logger.info(f"GPT-4o re-ranking complete: {len(results)} questions")
            
        except Exception as e:
            logger.error(f"GPT-4o re-ranking failed: {e}. Falling back to vector scores.")
            use_gpt4o_reranking = False  # Fall through to vector-only ranking
    
    # ── Step 4 Alternative: Vector-only ranking (fallback) ───────────────────
    if not use_gpt4o_reranking:
        ranked = sorted(candidates.items(), key=lambda x: x[1]["score"], reverse=True)[:top_n]

        results = []
        for rank, (qid, info) in enumerate(ranked, start=1):
            q_meta = questions_store.get(qid, {})
            results.append({
                "rank": rank,
                "question_id": qid,
                "category": q_meta.get("category", ""),
                "question": q_meta.get("question_text", ""),
                "score": round(info["score"], 3),
                "vector_score": round(info["vector_score"], 3),
                "match_path": info["match_path"],
                "sow_context": info["sow_context"],
                "sow_filename": info["sow_filename"],
                "sop_context": info.get("sop_context"),
            })
        
        logger.info(f"Vector-only ranking: {len(results)} questions")

    logger.info(f"retrieve_for_sow: returning {len(results)} questions from {len(candidates)} candidates")

    return {
        "total_results": len(results),
        "questions": results,
    }
