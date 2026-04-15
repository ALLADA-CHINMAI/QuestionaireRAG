"""
FastAPI routes for QuestionaireRAG.

Now using Azure Cognitive Search with hybrid indexing (vector + BM25 + semantic ranking).
"""

from __future__ import annotations

from pathlib import Path
import logging
import shutil
import uuid
import tempfile
import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
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


class QuestionResult(BaseModel):
    rank: int
    question_id: str
    domain: str
    question: str
    score: float = Field(..., description="Hybrid search score (vector + BM25)")
    source: str


class QueryResponse(BaseModel):
    customer_id: str
    context_summary: str
    total_results: int
    questions: List[QuestionResult]


# --- Endpoints ---

@router.get("/health")
def health():
    """Quick health check — returns index status and question count."""
    import os, json
    store_path = "data/questions_store.json"
    if os.path.exists(store_path):
        with open(store_path) as f:
            count = len(json.load(f))
        return {"status": "ok", "index_built": True, "questions_indexed": count}
    return {"status": "ok", "index_built": False, "questions_indexed": 0}


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


@router.post("/index/upload-questionnaire", response_model=IndexResponse)
async def upload_and_index_questionnaire(
    file: UploadFile = File(...),
):
    """Upload a CAIQ XLSX file and build the Azure Cognitive Search index with fresh embeddings."""
    from app.core.indexer import build_index
    import traceback

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted for questionnaire indexing.")

    tmp_dir = tempfile.gettempdir()
    tmp_path = Path(tmp_dir) / f"caiq_upload_{uuid.uuid4().hex}.xlsx"
    try:
        tmp_path.write_bytes(await file.read())
        logger.info(f"Saved uploaded questionnaire to {tmp_path}")

        count = build_index(str(tmp_path))
        logger.info(f"Successfully indexed {count} questions from uploaded file")
    except Exception as e:
        error_details = f"Indexing failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_details)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return IndexResponse(message=f"Indexed '{file.filename}' successfully", questions_indexed=count)


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
        )
        logger.info(f"Successfully retrieved {result['total_results']} questions")
    except FileNotFoundError as e:
        logger.error(f"File not found: {str(e)}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    return QueryResponse(**result)


ALLOWED_EXTENSIONS = {".pdf", ".xlsx"}


@router.post("/upload-and-query", response_model=QueryResponse)
async def upload_and_query(
    files: List[UploadFile] = File(...),
    top_k: int = Form(default=20),
):
    """Upload customer documents (PDF/XLSX) and get ranked CAIQ questions in one step."""
    from app.core.indexer import index_is_built
    from app.core.retriever import retrieve_for_customer

    if not index_is_built():
        raise HTTPException(
            status_code=400,
            detail="CAIQ index not built yet. Call POST /index/questionnaires first.",
        )

    # Validate file types
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{f.filename}'. Only PDF and XLSX are allowed.",
            )

    session_id = f"upload_{uuid.uuid4().hex[:8]}"
    customer_dir = Path(CUSTOMERS_BASE_DIR) / session_id
    customer_dir.mkdir(parents=True, exist_ok=True)

    try:
        for upload in files:
            dest = customer_dir / Path(upload.filename).name
            content = await upload.read()
            dest.write_bytes(content)
            logger.info(f"Saved uploaded file: {dest}")

        result = retrieve_for_customer(
            customer_id=session_id,
            customers_base_dir=CUSTOMERS_BASE_DIR,
            top_k=top_k,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Upload-and-query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(customer_dir, ignore_errors=True)

    return QueryResponse(**result)
