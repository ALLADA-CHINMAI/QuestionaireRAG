"""
Retriever: hybrid search pipeline with Reciprocal Rank Fusion (RRF).

Full pipeline for a single customer query:
  1. Load + parse customer SOP/SOW PDFs via ingestor.
  2. Summarize key security topics with Azure OpenAI gpt-4o (embedder).
  3. Run dense search — embed the summary, query ChromaDB by cosine similarity.
  4. Run sparse search — tokenize the summary, score all questions via BM25.
  5. Merge both ranked lists using Reciprocal Rank Fusion.
  6. Enrich top-K results with full question metadata and return.

RRF combines dense (semantic) and sparse (keyword) signals without requiring
score normalization, making it robust when the two retrievers use different
scoring scales.
"""

from typing import List, Dict

from app.core.embedder import embed_query, summarize_customer_context
from app.core.indexer import load_chroma_collection, load_bm25_index, load_questions_store
from app.core.ingestor import load_customer_docs


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# RRF smoothing constant — dampens the influence of very high ranks.
# k=60 is the standard value from the original RRF paper (Cormack et al. 2009).
# Higher k → more weight on top ranks; lower k → flatter score distribution.
RRF_K = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reciprocal_rank_fusion(
    dense_ids: List[str],
    sparse_ids: List[str],
    k: int = RRF_K,
) -> List[tuple]:
    """
    Merge two ranked result lists using Reciprocal Rank Fusion.

    For each question_id in either list, its RRF score is:
        score += 1 / (k + rank + 1)
    where rank is 0-based. Questions appearing in both lists get
    contributions from both, naturally boosting overlapping results.

    Args:
        dense_ids:  question_ids ordered by dense (semantic) rank.
        sparse_ids: question_ids ordered by sparse (BM25 keyword) rank.
        k:          RRF constant (default 60).

    Returns:
        List of (question_id, rrf_score) tuples sorted by score descending.
    """
    scores: Dict[str, float] = {}

    # Accumulate dense contribution — higher rank (lower index) = higher score
    for rank, qid in enumerate(dense_ids):
        scores[qid] = scores.get(qid, 0.0) + 1.0 / (k + rank + 1)

    # Accumulate sparse contribution on top of any existing dense score
    for rank, qid in enumerate(sparse_ids):
        scores[qid] = scores.get(qid, 0.0) + 1.0 / (k + rank + 1)

    # Sort by combined RRF score, highest first
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _tokenize(text: str) -> List[str]:
    """Lowercase whitespace tokenizer — must match the tokenizer used at index time."""
    return text.lower().split()


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------

def retrieve_for_customer(
    customer_id: str,
    customers_base_dir: str = "data/customers",
    top_k: int = 20,
    dense_candidates: int = 50,
    sparse_candidates: int = 50,
) -> Dict:
    """
    End-to-end retrieval pipeline for a single customer.

    Args:
        customer_id:        folder name under customers_base_dir (e.g. "customer_1").
        customers_base_dir: base directory containing all customer folders.
        top_k:              number of final ranked questions to return.
        dense_candidates:   how many top results to fetch from ChromaDB before fusion.
        sparse_candidates:  how many top results to fetch from BM25 before fusion.

    Returns:
        Dict with keys:
            customer_id      — echoed from input
            context_summary  — Azure OpenAI-generated summary used as the query
            total_results    — number of questions returned (= top_k)
            questions        — ranked list of question dicts with scores
    """
    customer_dir = f"{customers_base_dir}/{customer_id}"

    # ------------------------------------------------------------------
    # Step 1 — Load and combine all customer PDFs into one text block
    # ------------------------------------------------------------------
    print(f"Loading customer docs from {customer_dir}...")
    customer_text = load_customer_docs(customer_dir)

    # ------------------------------------------------------------------
    # Step 2 — Summarize with Azure OpenAI gpt-4o to produce a focused search query
    # The summary highlights security topics, compliance needs, and risk
    # areas — the themes that map most directly onto CAIQ domains.
    # ------------------------------------------------------------------
    print("Summarizing customer context with Azure OpenAI gpt-4o...")
    context_summary = summarize_customer_context(customer_text)
    print(f"  Summary: {context_summary[:200]}...")

    # ------------------------------------------------------------------
    # Step 3a — Dense retrieval via ChromaDB (semantic similarity)
    # Embed the summary and find the top dense_candidates nearest vectors
    # in the ChromaDB collection using cosine distance.
    # ------------------------------------------------------------------
    print("Running dense search...")
    query_vector = embed_query(context_summary)
    collection = load_chroma_collection()
    dense_results = collection.query(
        query_embeddings=[query_vector],
        n_results=dense_candidates,
    )
    # .query() returns a list-of-lists; [0] extracts results for our single query
    dense_ids = dense_results["ids"][0]

    # ------------------------------------------------------------------
    # Step 3b — Sparse retrieval via BM25 (keyword matching)
    # Tokenize the summary and score every question in the BM25 corpus.
    # Select the top sparse_candidates by raw BM25 score.
    # ------------------------------------------------------------------
    print("Running BM25 sparse search...")
    bm25_data = load_bm25_index()
    bm25 = bm25_data["bm25"]
    all_ids = bm25_data["ids"]   # ordered list of question_ids matching BM25 positions

    tokenized_query = _tokenize(context_summary)
    scores = bm25.get_scores(tokenized_query)  # returns a score per corpus document

    # Sort corpus indices by BM25 score and take the top N
    top_sparse_indices = sorted(
        range(len(scores)), key=lambda i: scores[i], reverse=True
    )[:sparse_candidates]
    sparse_ids = [all_ids[i] for i in top_sparse_indices]

    # ------------------------------------------------------------------
    # Step 4 — Reciprocal Rank Fusion: merge dense + sparse ranked lists
    # Questions that rank well in both lists get a higher combined score.
    # ------------------------------------------------------------------
    print("Applying Reciprocal Rank Fusion...")
    fused = _reciprocal_rank_fusion(dense_ids, sparse_ids)
    top_fused = fused[:top_k]  # keep only the final top_k results

    # ------------------------------------------------------------------
    # Step 5 — Enrich results with full question metadata
    # Look up each question_id in the pre-built questions store to attach
    # domain, full question text, and source file information.
    # ------------------------------------------------------------------
    questions_store = load_questions_store()
    results = []
    for rank, (qid, rrf_score) in enumerate(top_fused, start=1):
        q = questions_store.get(qid, {})
        results.append({
            "rank": rank,
            "question_id": qid,
            "domain": q.get("domain", ""),
            "question": q.get("question_text", ""),
            "rrf_score": round(rrf_score, 6),   # round for clean API output
            "source": q.get("source", ""),
        })

    return {
        "customer_id": customer_id,
        "context_summary": context_summary,
        "total_results": len(results),
        "questions": results,
    }
