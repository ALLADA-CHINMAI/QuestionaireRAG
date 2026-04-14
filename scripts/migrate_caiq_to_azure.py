"""
Index CAIQ questions into Azure Cognitive Search with fresh embeddings.

This script:
1. Parses the CAIQ XLSX file directly
2. Generates fresh embeddings via Azure OpenAI (text-embedding-ada-002)
3. Uploads to Azure Cognitive Search (index must already exist in portal)
4. Validates the upload with a test search

Usage:
    python scripts/migrate_caiq_to_azure.py

Prerequisites:
    - Azure Cognitive Search index (caiq_questions) created in the portal
    - Credentials in .env: AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY,
      OPENAI_ENDPOINT, OPENAI_API_KEY, EMBEDINGS_OPENAI_DEPLOYMENT_NAME
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.indexer import build_index
from app.core.azure_search import AzureSearchClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

CAIQ_XLSX_PATH = "data/questionnaires/CAIQv4.0.3_STAR-Security-Questionnaire_Generated-at_2023-09-26.xlsx"


def validate(client: AzureSearchClient) -> bool:
    """Run a test search to confirm documents are indexed."""
    logger.info("Validating upload...")
    try:
        result = client.search_caiq_hybrid(
            query_vector=[0.0] * 1536,
            query_text="security",
            top=5,
            use_semantic_ranking=False,
        )
        count = result.get("total_count", 0)
        if count > 0:
            logger.info(f"Validation passed — test search returned {count} results")
            return True
        else:
            logger.warning("Validation warning: test search returned 0 results (index may still be syncing)")
            return False
    except Exception as e:
        logger.error(f"Validation failed: {str(e)}")
        return False


def main():
    logger.info("Starting fresh CAIQ indexing to Azure Cognitive Search...")

    xlsx = Path(CAIQ_XLSX_PATH)
    if not xlsx.exists():
        logger.error(f"CAIQ file not found: {xlsx}")
        return 1

    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    caiq_index = os.getenv("AZURE_SEARCH_CAIQ_INDEX_NAME", "caiq_questions")

    if not endpoint or not api_key:
        logger.error("Missing AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY in .env")
        return 1

    try:
        # Generates fresh embeddings from XLSX and uploads to Azure
        succeeded = build_index(str(xlsx))
        logger.info(f"Indexed {succeeded} questions with fresh embeddings")
    except Exception as e:
        logger.error(f"Indexing failed: {str(e)}")
        return 1

    client = AzureSearchClient(endpoint=endpoint, api_key=api_key, caiq_index_name=caiq_index)
    ok = validate(client)

    if ok:
        logger.info("Done — CAIQ index is ready in Azure Cognitive Search")
        return 0
    else:
        logger.warning("Done with warnings — check logs above")
        return 1


if __name__ == "__main__":
    exit(main())
