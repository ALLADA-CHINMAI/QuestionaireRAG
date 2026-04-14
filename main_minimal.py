"""Minimal version to test routing without Azure imports."""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="QuestionaireRAG Minimal")

class HealthResponse(BaseModel):
    status: str
    index_built: bool

@app.get("/health", response_model=HealthResponse)
def health():
    return {"status": "ok", "index_built": False}

@app.get("/")
def root():
    return {"message": "Server is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
