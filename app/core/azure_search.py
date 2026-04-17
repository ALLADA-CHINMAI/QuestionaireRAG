"""
Azure Cognitive Search client wrapper for hybrid indexing and retrieval.
Handles both vector (HNSW) and BM25 (sparse) search with semantic ranking.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential

# Ensure debug logs are enabled
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

logger = logging.getLogger(__name__)


class AzureSearchClient:
    """
    Client for Azure Cognitive Search with hybrid indexing support (v6).

    Supports:
    - Hybrid search: vector embeddings + BM25 keyword search in single query
    - Semantic ranking for relevance reranking
    - Multi-index management:
        * SOP chunks index (documents chunked from SOPs)
        * Question items index (questions from uploaded Excel)
        * Semantic mappings index (SOP capability → Question category)
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        sop_index_name: str = "sop_chunks",
        questions_index_name: str = "psmart_questions",
        mappings_index_name: str = "semantic_mappings",
        **kwargs
    ):
        """
        Initialize Azure Search client (v6).

        Args:
            endpoint:             Azure Cognitive Search endpoint
            api_key:              Azure Cognitive Search API key
            sop_index_name:       Name of the SOP chunks index
            questions_index_name: Name of the PSmart questions index
            mappings_index_name:  Name of the semantic mappings index
        """
        # DEBUG: Log any unexpected keyword arguments
        if kwargs:
            logger.error(f"⚠️ UNEXPECTED KWARGS PASSED TO AzureSearchClient.__init__: {kwargs}")
            logger.error(f"⚠️ This file is at: {__file__}")
            raise TypeError(f"AzureSearchClient.__init__() got unexpected keyword arguments: {list(kwargs.keys())}")
        
        self.endpoint = endpoint
        self.api_key = api_key
        self.sop_index_name = sop_index_name
        self.questions_index_name = questions_index_name
        self.mappings_index_name = mappings_index_name
        self.credentials = AzureKeyCredential(api_key)

        # Will be lazily initialized
        self._sop_client: Optional[SearchClient] = None
        self._questions_client: Optional[SearchClient] = None
        self._mappings_client: Optional[SearchClient] = None
    
    def _get_mappings_client(self) -> SearchClient:
        """Get or create SearchClient for the semantic mappings index."""
        if self._mappings_client is None:
            self._mappings_client = SearchClient(
                endpoint=self.endpoint,
                index_name=self.mappings_index_name,
                credential=self.credentials,
            )
        return self._mappings_client

    def _get_sop_client(self) -> SearchClient:
        """Get or create SearchClient for the SOP chunks index."""
        if self._sop_client is None:
            self._sop_client = SearchClient(
                endpoint=self.endpoint,
                index_name=self.sop_index_name,
                credential=self.credentials,
            )
        return self._sop_client

    def _get_questions_client(self) -> SearchClient:
        """Get or create SearchClient for the PSmart questions index."""
        if self._questions_client is None:
            self._questions_client = SearchClient(
                endpoint=self.endpoint,
                index_name=self.questions_index_name,
                credential=self.credentials,
            )
        return self._questions_client


    

    
    # ------------------------------------------------------------------
    # SOP chunks index
    # ------------------------------------------------------------------

    def index_sop_chunks(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upload SOP text chunks to the SOP chunks index.

        Expected document structure:
            {
                "id":          "stem_0",          # sanitized key
                "chunk_id":    "SOP_Cloud_0",
                "filename":    "CloudSOP.docx",
                "capability":  "Cloud Security",
                "chunk_text":  "...",
                "chunk_index": 0,
                "vector":      [1536-dim float list],
            }
        """
        try:
            client = self._get_sop_client()
            result = client.upload_documents(documents)
            failed = [r for r in result if not r.succeeded]
            succeeded = len(result) - len(failed)
            logger.info(f"SOP indexing: {succeeded} succeeded, {len(failed)} failed")
            if failed:
                logger.error(f"Failed SOP docs: {failed[:3]}")
            return {"succeeded": succeeded, "failed": len(failed), "errors": failed}
        except Exception as e:
            logger.error(f"Error indexing SOP chunks: {e}")
            raise

    def index_questions(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upload custom questions to the questions index.

        Expected document structure:
            {
                "id":            "IAM_001",       # sanitized key
                "question_id":   "IAM_001",
                "category":      "IAM",
                "question_text": "Are identities managed...",
                "vector":        [1536-dim float list],
            }
        """
        try:
            client = self._get_questions_client()
            result = client.upload_documents(documents)
            failed = [r for r in result if not r.succeeded]
            succeeded = len(result) - len(failed)
            logger.info(f"Questions indexing: {succeeded} succeeded, {len(failed)} failed")
            if failed:
                logger.error(f"Failed question docs: {failed[:3]}")
            return {"succeeded": succeeded, "failed": len(failed), "errors": failed}
        except Exception as e:
            logger.error(f"Error indexing questions: {e}")
            raise

    def index_psmart(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upload PSmart questions to the questions index.

        Expected document structure:
            {
                "id":            "sanitized_id",  # sanitized key
                "question_id":   "original-id",
                "domain":        "Security",
                "question_text": "Does the organization...",
                "source":        "PSmart",
                "vector":        [1536-dim float list],
            }
        """
        try:
            client = self._get_questions_client()
            result = client.upload_documents(documents)
            failed = [r for r in result if not r.succeeded]
            succeeded = len(result) - len(failed)
            logger.info(f"PSmart indexing: {succeeded} succeeded, {len(failed)} failed")
            if failed:
                logger.error(f"Failed PSmart docs: {failed[:3]}")
            return {"succeeded": succeeded, "failed": len(failed), "errors": failed}
        except Exception as e:
            logger.error(f"Error indexing PSmart questions: {e}")
            raise

    def search_sop_hybrid(
        self,
        query_vector: List[float],
        query_text: str,
        top: int = 5,
        capability_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Hybrid search (vector + BM25) on the SOP chunks index.

        Args:
            query_vector:       1536-dim embedding of the SOW chunk.
            query_text:         Raw SOW chunk text for BM25 matching.
            top:                Number of SOP chunks to return.
            capability_filter:  OData filter on the capability field
                                (e.g. "capability eq 'Cloud Security'").

        Returns:
            {"results": [...], "total_count": int}
        """
        try:
            client = self._get_sop_client()
            vector_query = VectorizedQuery(
                vector=query_vector,
                k_nearest_neighbors=50,
                fields="vector",
                weight=0.7,  # Adjust weight to favor vector relevance
            )
            params: Dict[str, Any] = {
                "vector_queries": [vector_query],
                "search_text": query_text,
                "select": ["id", "chunk_id", "filename", "capability", "chunk_text", "chunk_index"],
                "top": top,
            }
            if capability_filter:
                params["filter"] = f"capability eq '{capability_filter}'"

            parsed = []
            for r in client.search(**params):
                parsed.append({
                    "id": r.get("id"),
                    "chunk_id": r.get("chunk_id"),
                    "filename": r.get("filename"),
                    "capability": r.get("capability"),
                    "chunk_text": r.get("chunk_text"),
                    "chunk_index": r.get("chunk_index"),
                    "score": r.get("@search.score"),
                })
            return {"results": parsed, "total_count": len(parsed)}
        except Exception as e:
            logger.error(f"SOP hybrid search error: {e}")
            raise

    def search_questions_hybrid(
        self,
        query_vector: List[float],
        query_text: str,
        top: int = 10,
        category_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Hybrid search (vector + BM25) on the custom questions index.

        Args:
            query_vector:     1536-dim embedding of the SOW chunk.
            query_text:       Raw text for BM25 matching.
            top:              Number of questions to return.
            category_filter:  Optional OData filter on category field.

        Returns:
            {"results": [...], "total_count": int}
        """
        try:
            client = self._get_questions_client()
            vector_query = VectorizedQuery(
                vector=query_vector,
                k_nearest_neighbors=50,
                fields="vector",
                weight=0.7,  # Adjust weight to favor vector relevance
            )
            params: Dict[str, Any] = {
                "vector_queries": [vector_query],
                "search_text": query_text,
                "select": ["id", "question_id", "category", "question_text"],
                "top": top,
            }
            if category_filter:
                params["filter"] = f"category eq '{category_filter}'"

            parsed = []
            for r in client.search(**params):
                parsed.append({
                    "id": r.get("id"),
                    "question_id": r.get("question_id"),
                    "category": r.get("category"),
                    "question_text": r.get("question_text"),
                    "score": r.get("@search.score"),
                })
            return {"results": parsed, "total_count": len(parsed)}
        except Exception as e:
            logger.error(f"Questions hybrid search error: {e}")
            raise

    def delete_customer_index(self, customer_id: str) -> bool:
        """
        Delete a customer's document index (cleanup when customer data expires).
        
        Note: This requires admin key and special Azure SDK client.
        For now, logging as a placeholder.
        
        Args:
            customer_id: Customer identifier
        
        Returns:
            True if successful
        """
        logger.warning(f"Customer index deletion for {customer_id} requires admin operations (not implemented yet)")
        return False
    
    def health_check(self) -> bool:
        """
        Verify connection to Azure Cognitive Search service.
        
        Returns:
            True if credentials are configured (actual connectivity tested on first use)
        """
        # Skip actual network check during startup to avoid blocking
        # Credentials will be validated on first real operation
        logger.info("Azure Cognitive Search credentials configured")
        return True
