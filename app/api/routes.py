"""
FastAPI routes for QuestionaireRAG.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.indexer import build_index, index_is_built
from app.core.retriever import retrieve_for_customer


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


class QuestionResult(BaseModel):
    rank: int
    question_id: str
    domain: str
    question: str
    rrf_score: float
    source: str


class QueryResponse(BaseModel):
    customer_id: str
    context_summary: str
    total_results: int
    questions: list[QuestionResult]


# --- Endpoints ---

@router.get("/health")
def health():
    return {"status": "ok", "index_built": index_is_built()}


@router.post("/index/questionnaires", response_model=IndexResponse)
def index_questionnaires():
    """Parse CAIQ XLSX and build ChromaDB + BM25 indexes."""
    if not Path(CAIQ_XLSX_PATH).exists():
        raise HTTPException(status_code=404, detail=f"CAIQ file not found at {CAIQ_XLSX_PATH}")
    try:
        count = build_index(CAIQ_XLSX_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return IndexResponse(message="Index built successfully", questions_indexed=count)


@router.post("/query", response_model=QueryResponse)
def query_customer(request: QueryRequest):
    """Retrieve ranked questions for a customer based on their SOP/SOW docs."""
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
        result = retrieve_for_customer(
            customer_id=request.customer_id,
            customers_base_dir=CUSTOMERS_BASE_DIR,
            top_k=request.top_k,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return QueryResponse(**result)
