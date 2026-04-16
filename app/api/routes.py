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

# v6: Questions are uploaded dynamically via POST /data/upload-questions
CUSTOMERS_BASE_DIR = "data/customers"

ALLOWED_DOC_EXTENSIONS = {".pdf", ".xlsx", ".docx"}


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


# ── New SOW-based models ──────────────────────────────────────────────────────

class SowQuestionResult(BaseModel):
    rank: int
    question_id: str
    category: str
    question: str
    score: float = Field(..., description="Hybrid score (direct SOW matches boosted 1.5×)")
    match_path: str = Field(..., description="'Direct SOW match' or 'SOW → SOP (capability)'")
    sow_context: str = Field(..., description="First 150 chars of the matching SOW chunk")
    sow_filename: str
    sop_context: Optional[str] = Field(None, description="First 150 chars of the matching SOP chunk")
    sop_capability: Optional[str] = Field(None, description="Capability label of the matched SOP")


class SowQueryResponse(BaseModel):
    total_results: int
    questions: List[SowQuestionResult]


# --- Endpoints ---

@router.get("/health")
def health():
    """Health check — returns status for v6 indexes (SOP chunks, question items, semantic mappings)."""
    import os, json

    # v6: New SOP chunks index
    sop_built = os.path.exists("data/sop_store.json")
    sop_chunks = 0
    if sop_built:
        with open("data/sop_store.json") as f:
            sop_chunks = len(json.load(f))

    # v6: Question items index
    q_built = os.path.exists("data/questions_store.json")
    q_count = 0
    if q_built:
        with open("data/questions_store.json") as f:
            q_count = len(json.load(f))

    return {
        "status": "ok",
        "version": "6.0",
        "sop_index_built": sop_built,
        "sop_chunks_indexed": sop_chunks,
        "questions_index_built": q_built,
        "psmart_questions_indexed": q_count,
    }

# v6: Legacy /index/questionnaires and /index/upload-questionnaire endpoints removed.
# Use POST /index/upload-questions instead to upload custom questions Excel files.




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


@router.post("/index/upload-sops")
async def upload_and_index_sops(
    files: List[UploadFile] = File(...),
    capabilities: List[str] = Form(...),
):
    """
    Upload one or more SOP files (.docx / .pdf / .xlsx) with capability labels
    and index them into the SOP chunks Azure index.

    Form fields:
      - files[]       — SOP file uploads
      - capabilities[] — one capability label per file (same order as files)
    """
    from app.core.indexer import build_sop_index
    import traceback
    
    logger.info(f"=== SOP INDEXING REQUEST STARTED ===")
    logger.info(f"Received {len(files)} files with {len(capabilities)} capabilities")

    if len(files) != len(capabilities):
        raise HTTPException(
            status_code=400,
            detail=f"Number of files ({len(files)}) must match number of capabilities ({len(capabilities)}).",
        )

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_DOC_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{f.filename}'. Allowed: .docx, .pdf, .xlsx",
            )

    tmp_dir = Path(tempfile.gettempdir()) / f"sops_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    saved: List[tuple] = []

    try:
        for upload, capability in zip(files, capabilities):
            dest = tmp_dir / Path(upload.filename).name
            dest.write_bytes(await upload.read())
            saved.append((str(dest), capability.strip()))
            logger.info(f"Saved SOP: {dest} (capability: {capability})")

        logger.info(f"Calling build_sop_index with {len(saved)} files...")
        total = build_sop_index(saved)
        logger.info(f"Indexed {total} SOP chunks")
    except Exception as e:
        logger.error(f"SOP indexing failed: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"message": f"Indexed {len(files)} SOP file(s) successfully", "chunks_indexed": total}


@router.post("/index/upload-questions")
async def upload_and_index_questions(
    file: UploadFile = File(...),
):
    """
    Upload a questions Excel file (.xlsx) and index it into the custom questions Azure index.

    Expected columns in the Excel file: 'category' and 'question' (case-insensitive).
    """
    from app.core.indexer import build_questions_index
    import traceback

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted for questions indexing.")

    tmp_path = Path(tempfile.gettempdir()) / f"questions_{uuid.uuid4().hex[:8]}.xlsx"
    try:
        tmp_path.write_bytes(await file.read())
        logger.info(f"Saved questions file to {tmp_path}")

        count = build_questions_index(str(tmp_path))
        logger.info(f"Indexed {count} questions")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Questions indexing failed: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"message": f"Indexed '{file.filename}' successfully", "questions_indexed": count}


@router.post("/query-sow", response_model=SowQueryResponse)
async def query_with_sow(
    files: List[UploadFile] = File(...),
    top_n: int = Form(default=20),
):
    """
    Upload one or more SOW files (.docx / .pdf / .xlsx) and retrieve the most
    relevant questions from the custom questions index.

    Combines two retrieval paths:
      1. Direct SOW → Questions (1.5× score boost)
      2. SOW → SOP → Questions (base score, capability-mediated)

    Returns top_n questions with supporting context showing which path matched.
    """
    from app.core.indexer import sop_index_is_built, questions_index_is_built
    from app.core.retriever import retrieve_for_sow
    import traceback

    if not questions_index_is_built():
        raise HTTPException(
            status_code=400,
            detail="Questions index not built yet. Upload a questions Excel file first via POST /index/upload-questions.",
        )

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_DOC_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{f.filename}'. Allowed: .docx, .pdf, .xlsx",
            )

    if top_n < 1:
        top_n = 1

    session_dir = Path(tempfile.gettempdir()) / f"sow_{uuid.uuid4().hex[:8]}"
    session_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: List[str] = []

    try:
        for upload in files:
            dest = session_dir / Path(upload.filename).name
            dest.write_bytes(await upload.read())
            saved_paths.append(str(dest))
            logger.info(f"Saved SOW: {dest}")

        result = retrieve_for_sow(sow_file_paths=saved_paths, top_n=top_n)
        logger.info(f"SOW query returned {result['total_results']} questions")
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"SOW query failed: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)

    return SowQueryResponse(**result)


@router.post("/upload-and-query", response_model=QueryResponse)
async def upload_and_query(
    files: List[UploadFile] = File(...),
    top_k: int = Form(default=20),
):
    """Upload customer documents (PDF/XLSX) and get ranked PSmart questions in one step."""
    from app.core.indexer import index_is_built
    from app.core.retriever import retrieve_for_customer

    if not index_is_built():
        raise HTTPException(
            status_code=400,
            detail="PSmart questions index not built yet. Upload questions first.",
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
