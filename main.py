"""
QuestionaireRAG — entry point.
Run with: uvicorn main:app --reload
"""

import logging
from dotenv import load_dotenv
load_dotenv()

# Configure logging to show in console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

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
    logger.info("✓ Server ready - Azure Cognitive Search will be initialized on first use")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("QuestionaireRAG server shutting down...")
