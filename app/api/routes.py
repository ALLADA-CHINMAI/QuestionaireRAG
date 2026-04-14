"""
FastAPI routes for QuestionaireRAG.

Now using Azure Cognitive Search with hybrid indexing (vector + BM25 + semantic ranking).
"""

from __future__ import annotations

from pathlib import Path
import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Lazy imports - imported only when endpoints are called, not at module load time
# This prevents blocking during FastAPI startup/docs generation

logger = logging.getLogger(__name__)


router = APIRouter()

CAIQ_XLSX_PATH = "data/questionnaires/CAIQv4.0.3_STAR-Security-Questionnaire_Generated-at_2023-09-26.xlsx"
CUSTOMERS_BASE_DIR = "data/customers"


# --- Request / Response models ---

class IndexResponse(BaseModel):
    message: str
    questions_indexed: int


class QueryRequest(BaseModel):
    customer_id: str = Field(..., example="customer_1")
    top_k: int = Field(default=20, ge=1, le=263)
    use_semantic_ranking: bool = Field(default=True, description="Enable semantic ranking for better relevance")


class QuestionResult(BaseModel):
    rank: int
    question_id: str
    domain: str
    question: str
    score: float = Field(..., description="Hybrid search score (vector + BM25)")
    semantic_score: Optional[float] = Field(default=None, description="Semantic ranking score (if enabled)")
    source: str


class QueryResponse(BaseModel):
    customer_id: str
    context_summary: str
    total_results: int
    questions: List[QuestionResult]


# --- Endpoints ---

@router.get("/health")
def health():
    """Quick health check - only checks if questions_store exists, doesn't check Azure connection."""
    import os
    questions_store_exists = os.path.exists("data/questions_store.json")
    return {"status": "ok", "index_built": questions_store_exists}


@router.post("/index/questionnaires", response_model=IndexResponse)
def index_questionnaires():
    """Parse CAIQ XLSX and build Azure Cognitive Search index (hybrid: vector + BM25 + semantic ranking)."""
    from app.core.indexer import build_index  # Lazy import
    import traceback
    
    if not Path(CAIQ_XLSX_PATH).exists():
        raise HTTPException(status_code=404, detail=f"CAIQ file not found at {CAIQ_XLSX_PATH}")
    try:
        logger.info("Starting CAIQ indexing to Azure Cognitive Search...")
        count = build_index(CAIQ_XLSX_PATH)
        logger.info(f"Successfully indexed {count} questions")
    except Exception as e:
        error_details = f"Indexing failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_details)
        print(f"\n\n=== ERROR IN INDEXING ===\n{error_details}\n=========================\n")
        raise HTTPException(status_code=500, detail=str(e))
    return IndexResponse(message="Index built successfully in Azure Cognitive Search", questions_indexed=count)


@router.post("/query", response_model=QueryResponse)
def query_customer(request: QueryRequest):
    """Retrieve ranked questions for a customer based on their SOP/SOW docs.
    
    Uses Azure Cognitive Search hybrid search (vector + BM25 + optional semantic ranking).
    """
    from app.core.indexer import index_is_built  # Lazy import
    from app.core.retriever import retrieve_for_customer  # Lazy import
    
    customer_dir = Path(CUSTOMERS_BASE_DIR) / request.customer_id
    if not customer_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Customer directory not found: {customer_dir}"
        )
    if not index_is_built():
        raise HTTPException(
            status_code=400,
            detail="Index not built yet. Call POST /index/questionnaires first."
        )
    try:
        logger.info(f"Retrieved query for customer {request.customer_id} with top_k={request.top_k}")
        result = retrieve_for_customer(
            customer_id=request.customer_id,
            customers_base_dir=CUSTOMERS_BASE_DIR,
            top_k=request.top_k,
            use_semantic_ranking=request.use_semantic_ranking,
        )
        logger.info(f"Successfully retrieved {result['total_results']} questions")
    except FileNotFoundError as e:
        logger.error(f"File not found: {str(e)}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    return QueryResponse(**result)
