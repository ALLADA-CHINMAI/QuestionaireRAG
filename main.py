"""
QuestionaireRAG — entry point.
Run with: uvicorn main:app --reload
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.api.routes import router

app = FastAPI(
    title="QuestionaireRAG",
    description="Retrieve ranked security questionnaire questions relevant to customer SOP/SOW docs.",
    version="2.0.0",
)

app.include_router(router)
