"""
Delete Azure Search indexes.
Run this before recreating indexes with create_index.py
"""

import os
import sys
import logging
from pathlib import Path
from azure.search.documents.indexes import SearchIndexClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def delete_index(endpoint: str, api_key: str, index_name: str):
    """Delete an Azure Search index."""
    try:
        credential = AzureKeyCredential(api_key)
        index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
        
        # Check if index exists
        try:
            index_client.get_index(index_name)
            logger.info(f"Deleting index '{index_name}'...")
            index_client.delete_index(index_name)
            logger.info(f"✅ Index '{index_name}' deleted successfully.")
        except Exception as e:
            if "not found" in str(e).lower():
                logger.info(f"ℹ️  Index '{index_name}' does not exist, skipping.")
            else:
                raise
                
    except Exception as e:
        logger.error(f"❌ Failed to delete index '{index_name}': {e}")
        raise


def main():
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    
    if not endpoint or not api_key:
        logger.error("Missing AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY in .env")
        return 1
    
    # Get index names from environment
    sop_index = os.getenv("AZURE_SEARCH_SOP_INDEX_NAME", "sop_chunks")
    questions_index = os.getenv("AZURE_SEARCH_QUESTIONS_INDEX_NAME", "psmart_questions")
    mappings_index = os.getenv("AZURE_SEARCH_MAPPINGS_INDEX_NAME", "semantic_mappings")
    
    try:
        logger.info("Starting index deletion process...")
        logger.info(f"Endpoint: {endpoint}")
        logger.info(f"Indexes to delete: {sop_index}, {questions_index}, {mappings_index}")
        
        # Delete all indexes
        delete_index(endpoint, api_key, sop_index)
        delete_index(endpoint, api_key, questions_index)
        delete_index(endpoint, api_key, mappings_index)
        
        logger.info("\n✅ All indexes deleted successfully!")
        logger.info("\nNext step: Run 'python scripts/create_index.py' to recreate indexes")
        return 0
        
    except Exception as e:
        logger.error(f"\n❌ Index deletion failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
