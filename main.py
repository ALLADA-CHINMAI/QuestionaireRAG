"""
QuestionaireRAG — entry point.
Run with: uvicorn main:app --reload
"""

import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.api.routes import router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="QuestionaireRAG",
    description="Retrieve ranked security questionnaire questions relevant to customer SOP/SOW docs using Azure Cognitive Search.",
    version="3.0.0",
)

app.include_router(router)


@app.on_event("startup")
async def startup_event():
    """Initialize and verify Azure Cognitive Search connection on startup."""
    logger.info("QuestionaireRAG server starting up...")
    try:
        from app.core.indexer import get_azure_search_client
        client = get_azure_search_client()
        if client.health_check():
            logger.info("✓ Azure Cognitive Search connection verified")
        else:
            logger.warning("⚠ Azure Cognitive Search health check returned False")
    except ValueError as e:
        logger.warning(f"Azure Cognitive Search not configured: {str(e)}")
    except Exception as e:
        logger.warning(f"Azure Cognitive Search startup check failed: {str(e)}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("QuestionaireRAG server shutting down...")
