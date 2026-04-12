"""
Phase 3: Migrate CAIQ questions from Chroma DB to Azure Cognitive Search.

This script:
1. Loads existing CAIQ data from Chroma DB (vectors preserved)
2. Retrieves embeddings from local storage
3. Formats documents for Azure CS
4. Bulk uploads to Azure CS CAIQ index

Usage:
    python scripts/migrate_caiq_to_azure.py

Prerequisites:
    - Chroma DB with existing CAIQ indexing
    - questions_store.json with full question metadata
    - Azure Cognitive Search service provisioned
    - Credentials in .env: AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY
"""

import os
import json
import logging
from typing import List, Dict, Any
from pathlib import Path
from dotenv import load_dotenv
import chromadb

# Import custom modules
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.azure_search import AzureSearchClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()

# Configuration
CHROMA_PERSIST_DIR = "data/chroma_db"
COLLECTION_NAME = "caiq_questions"
QUESTIONS_STORE_PATH = "data/questions_store.json"


def load_chroma_embeddings() -> Dict[str, List[float]]:
    """Load existing CAIQ embeddings from Chroma DB."""
    logger.info("Loading embeddings from Chroma DB...")
    
    try:
        client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        collection = client.get_collection(name=COLLECTION_NAME)
        
        # Get all documents with embeddings
        results = collection.get(include=["embeddings", "documents", "metadatas"])
        
        embeddings_map = {}
        for idx, doc_id in enumerate(results["ids"]):
            if results["embeddings"] and idx < len(results["embeddings"]):
                embeddings_map[doc_id] = results["embeddings"][idx]
        
        logger.info(f"Loaded {len(embeddings_map)} embeddings from Chroma")
        return embeddings_map
    
    except Exception as e:
        logger.error(f"Error loading Chroma embeddings: {str(e)}")
        raise


def load_questions_store() -> Dict[str, Any]:
    """Load question metadata from questions_store.json."""
    logger.info("Loading questions store...")
    
    if not os.path.exists(QUESTIONS_STORE_PATH):
        raise FileNotFoundError(f"questions_store.json not found at {QUESTIONS_STORE_PATH}")
    
    with open(QUESTIONS_STORE_PATH, 'r') as f:
        questions = json.load(f)
    
    logger.info(f"Loaded {len(questions)} questions from store")
    return questions


def format_documents_for_azure(
    questions: Dict[str, Any],
    embeddings_map: Dict[str, List[float]]
) -> List[Dict[str, Any]]:
    """
    Format questions and embeddings for Azure CS indexing.
    
    Args:
        questions: Dict mapping question_id to question metadata
        embeddings_map: Dict mapping doc_id to embedding vectors
    
    Returns:
        List of Azure CS documents ready for indexing
    """
    logger.info("Formatting documents for Azure CS...")
    
    documents = []
    
    for question_id, question_data in questions.items():
        # Create document ID (same as question_id for simplicity)
        doc_id = question_id
        
        # Get embedding (key might be the question_id or internal ID)
        embedding = embeddings_map.get(doc_id) or embeddings_map.get(question_id)
        
        if not embedding:
            logger.warning(f"No embedding found for {question_id}, skipping")
            continue
        
        # Ensure embedding is 1536 dimensions
        if len(embedding) != 1536:
            logger.warning(f"Embedding for {question_id} has {len(embedding)} dims, expected 1536")
            continue
        
        # Build Azure CS document
        doc = {
            "id": doc_id,
            "question_id": question_id,
            "domain": question_data.get("domain", ""),
            "question_text": question_data.get("question_text", ""),
            "source": question_data.get("source", "CAIQ"),
            "vector": embedding,
        }
        
        documents.append(doc)
    
    logger.info(f"Formatted {len(documents)} documents for Azure CS indexing")
    return documents


def migrate_to_azure(documents: List[Dict[str, Any]]) -> bool:
    """
    Upload formatted documents to Azure Cognitive Search.
    
    Args:
        documents: List of documents to index
    
    Returns:
        True if migration successful
    """
    logger.info("Initializing Azure Search client...")
    
    # Get credentials from environment
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    caiq_index = os.getenv("AZURE_SEARCH_CAIQ_INDEX_NAME", "caiq_questions")
    
    if not endpoint or not api_key:
        raise ValueError("AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY must be set in .env")
    
    # Create client
    client = AzureSearchClient(
        endpoint=endpoint,
        api_key=api_key,
        caiq_index_name=caiq_index
    )
    
    # Verify connection
    logger.info("Verifying Azure Search connection...")
    if not client.health_check():
        raise ConnectionError("Failed to connect to Azure Cognitive Search service")
    
    logger.info("Connection verified. Proceeding with indexing...")
    
    # Index documents in batches (Azure has size limits)
    batch_size = 1000
    total_succeeded = 0
    total_failed = 0
    
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        logger.info(f"Indexing batch {batch_num} ({len(batch)} documents)...")
        
        try:
            result = client.index_caiq(batch)
            
            succeeded = result.get("succeeded", 0)
            failed = result.get("failed", 0)
            errors = result.get("errors", [])
            
            total_succeeded += succeeded
            total_failed += failed
            
            logger.info(f"  Batch {batch_num}: {succeeded} succeeded, {failed} failed")
            
            if errors:
                logger.warning(f"  Sample errors: {errors[:3]}")
        
        except Exception as e:
            logger.error(f"Error indexing batch {batch_num}: {str(e)}")
            total_failed += len(batch)
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Migration Summary:")
    logger.info(f"  Total Documents Indexed: {total_succeeded}")
    logger.info(f"  Total Failed: {total_failed}")
    logger.info(f"  Success Rate: {100*total_succeeded/(total_succeeded+total_failed):.1f}%")
    logger.info(f"{'='*60}\n")
    
    return total_failed == 0


def validate_migration() -> bool:
    """
    Validate that migration was successful by querying Azure CS.
    
    Returns:
        True if validation passed
    """
    logger.info("Validating migration...")
    
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    caiq_index = os.getenv("AZURE_SEARCH_CAIQ_INDEX_NAME", "caiq_questions")
    
    if not endpoint or not api_key:
        logger.warning("Cannot validate without credentials")
        return False
    
    client = AzureSearchClient(
        endpoint=endpoint,
        api_key=api_key,
        caiq_index_name=caiq_index
    )
    
    # Try a test search
    logger.info("Running test search query...")
    
    try:
        # Create a dummy query vector (zeros)
        test_vector = [0.0] * 1536
        
        result = client.search_caiq_hybrid(
            query_vector=test_vector,
            query_text="security",
            top=5,
            use_semantic_ranking=False
        )
        
        total_count = result.get("total_count", 0)
        logger.info(f"Test search returned {total_count} results")
        
        if total_count > 0:
            logger.info("✓ Validation passed: Azure CS is responding correctly")
            return True
        else:
            logger.warning("✗ Validation warning: No results returned (index may be empty)")
            return False
    
    except Exception as e:
        logger.error(f"✗ Validation failed: {str(e)}")
        return False


def main():
    """Main migration flow."""
    logger.info("Starting CAIQ to Azure Cognitive Search migration...")
    logger.info(f"Working directory: {os.getcwd()}\n")
    
    try:
        # Step 1: Load Chroma embeddings
        embeddings_map = load_chroma_embeddings()
        
        # Step 2: Load existing questions
        questions = load_questions_store()
        
        # Step 3: Format for Azure CS
        documents = format_documents_for_azure(questions, embeddings_map)
        
        if not documents:
            raise ValueError("No documents formatted for migration")
        
        # Step 4: Migrate to Azure
        success = migrate_to_azure(documents)
        
        if not success:
            logger.warning("Migration completed with some failures. Review errors above.")
        
        # Step 5: Validate
        validation_passed = validate_migration()
        
        if success and validation_passed:
            logger.info("\n✓ Migration completed successfully!")
            return 0
        else:
            logger.warning("\n⚠ Migration completed with warnings. Review above.")
            return 1
    
    except Exception as e:
        logger.error(f"\n✗ Migration failed: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
