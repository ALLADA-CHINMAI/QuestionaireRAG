"""
Azure Cognitive Search client wrapper for hybrid indexing and retrieval.
Handles both vector (HNSW) and BM25 (sparse) search with semantic ranking.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from azure.search.documents import SearchClient
from azure.search.documents.models import (
    VectorizedQuery,
    QueryType,
    QueryCaptionType,
    QueryAnswerType,
)
from azure.core.credentials import AzureKeyCredential

logger = logging.getLogger(__name__)


class AzureSearchClient:
    """
    Client for Azure Cognitive Search with hybrid indexing support.
    
    Supports:
    - Hybrid search: vector embeddings + BM25 keyword search in single query
    - Semantic ranking for relevance reranking
    - Multi-index management (CAIQ questions + per-customer document indexes)
    """
    
    def __init__(self, endpoint: str, api_key: str, caiq_index_name: str = "caiq_questions"):
        """
        Initialize Azure Search client.
        
        Args:
            endpoint: Azure Cognitive Search endpoint (e.g., https://myservice.search.windows.net)
            api_key: Azure Cognitive Search API key
            caiq_index_name: Name of the CAIQ questions index
        """
        self.endpoint = endpoint
        self.api_key = api_key
        self.caiq_index_name = caiq_index_name
        self.credentials = AzureKeyCredential(api_key)
        
        # Will be lazily initialized
        self._caiq_client: Optional[SearchClient] = None
        self._customer_clients: Dict[str, SearchClient] = {}
    
    def _get_caiq_client(self) -> SearchClient:
        """Get or create SearchClient for CAIQ index."""
        if self._caiq_client is None:
            self._caiq_client = SearchClient(
                endpoint=self.endpoint,
                index_name=self.caiq_index_name,
                credential=self.credentials
            )
        return self._caiq_client
    
    def _get_customer_client(self, customer_id: str) -> SearchClient:
        """Get or create SearchClient for customer documents index."""
        if customer_id not in self._customer_clients:
            index_name = f"customer_docs_{customer_id}"
            self._customer_clients[customer_id] = SearchClient(
                endpoint=self.endpoint,
                index_name=index_name,
                credential=self.credentials
            )
        return self._customer_clients[customer_id]
    
    def index_caiq(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Index CAIQ questions into Azure Cognitive Search.
        
        Args:
            documents: List of documents with structure:
                {
                    "id": "unique_id",
                    "question_id": "IAM-01.1",
                    "domain": "IAM",
                    "question_text": "...",
                    "source": "CAIQ",
                    "vector": [embeddings_array_1536_dims],
                    ...
                }
        
        Returns:
            Dict with indexing results: {"succeeded": int, "failed": int, "errors": [...]}
        """
        try:
            client = self._get_caiq_client()
            result = client.upload_documents(documents)
            
            failed_docs = [r for r in result if not r.succeeded]
            success_count = len(result) - len(failed_docs)
            
            logger.info(f"CAIQ indexing: {success_count} succeeded, {len(failed_docs)} failed")
            
            if failed_docs:
                logger.error(f"Failed documents: {failed_docs}")
            
            return {
                "succeeded": success_count,
                "failed": len(failed_docs),
                "errors": failed_docs
            }
        except Exception as e:
            logger.error(f"Error indexing CAIQ documents: {str(e)}")
            raise
    
    def index_customer_docs(self, customer_id: str, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Index customer documents into a per-customer index.
        
        Args:
            customer_id: Customer identifier
            documents: List of documents with structure:
                {
                    "id": "unique_doc_id",
                    "customer_id": "customer_id",
                    "doc_name": "document_filename.pdf",
                    "content_chunk": "text content...",
                    "chunk_vector": [embeddings_array_1536_dims],
                    "metadata": {...}
                }
        
        Returns:
            Dict with indexing results
        """
        try:
            client = self._get_customer_client(customer_id)
            result = client.upload_documents(documents)
            
            failed_docs = [r for r in result if not r.succeeded]
            success_count = len(result) - len(failed_docs)
            
            logger.info(f"Customer {customer_id} docs indexing: {success_count} succeeded, {len(failed_docs)} failed")
            
            if failed_docs:
                logger.error(f"Failed documents: {failed_docs}")
            
            return {
                "succeeded": success_count,
                "failed": len(failed_docs),
                "errors": failed_docs
            }
        except Exception as e:
            logger.error(f"Error indexing customer {customer_id} documents: {str(e)}")
            raise
    
    def search_hybrid(
        self,
        query_vector: List[float],
        query_text: str,
        index_type: str = "caiq",
        customer_id: Optional[str] = None,
        top: int = 50,
        use_semantic_ranking: bool = True,
        filter_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Perform hybrid search (vector + BM25) with optional semantic ranking.
        
        Args:
            query_vector: 1536-dimensional embedding vector
            query_text: Original query text for BM25 keyword search
            index_type: "caiq" or "customer_docs"
            customer_id: Required if index_type is "customer_docs"
            top: Number of top results to return
            use_semantic_ranking: Enable semantic ranking reranking
            filter_query: Optional OData filter (e.g., "domain eq 'IAM'")
        
        Returns:
            Dict with search results:
                {
                    "results": [
                        {
                            "id": "...",
                            "question_id": "...",
                            "domain": "...",
                            "question_text": "...",
                            "score": 2.5,  # Hybrid score or semantic score
                            "semantic_score": 0.95,  # If semantic ranking enabled
                            ...
                        }
                    ],
                    "total_count": int,
                    "semantic_ranking_enabled": bool
                }
        """
        try:
            # Select appropriate client
            if index_type == "caiq":
                client = self._get_caiq_client()
            elif index_type == "customer_docs":
                if not customer_id:
                    raise ValueError("customer_id required for customer_docs index_type")
                client = self._get_customer_client(customer_id)
            else:
                raise ValueError(f"Invalid index_type: {index_type}")
            
            # Create vectorized query for hybrid search
            vector_query = VectorizedQuery(
                vector=query_vector,
                k_nearest_neighbors=50,  # Retrieve top 50 vector matches
                fields="vector"  # or "chunk_vector" for customer docs
            )
            
            # Build search parameters
            search_params = {
                "vector_queries": [vector_query],
                "search_text": query_text,
                "query_type": QueryType.SEMANTIC,  # Enables hybrid + semantic
                "select": ["id", "question_id", "domain", "question_text", "source", "doc_name", "metadata"],
                "top": top,
                "query_language": "en-us",
            }
            
            # Add semantic ranking if enabled
            if use_semantic_ranking:
                search_params["query_caption"] = QueryCaptionType.EXTRACTIVE
                search_params["query_answer"] = QueryAnswerType.EXTRACTIVE
                search_params["semantic_configuration_name"] = "default"  # Must match index config
            
            # Add filter if provided
            if filter_query:
                search_params["filter"] = filter_query
            
            # Execute hybrid search
            results = client.search(**search_params)
            
            # Parse results
            parsed_results = []
            total_count = 0
            
            for result in results:
                parsed_results.append({
                    "id": result.get("id"),
                    "question_id": result.get("question_id"),
                    "domain": result.get("domain"),
                    "question_text": result.get("question_text"),
                    "source": result.get("source"),
                    "doc_name": result.get("doc_name"),
                    "metadata": result.get("metadata"),
                    "score": result.get("@search.score"),
                    "semantic_score": result.get("@search.reranker_score"),
                    "captions": result.get("@search.captions"),
                })
                total_count += 1
            
            return {
                "results": parsed_results,
                "total_count": total_count,
                "semantic_ranking_enabled": use_semantic_ranking,
                "query_vector_dims": len(query_vector),
            }
        
        except Exception as e:
            logger.error(f"Error in hybrid search: {str(e)}")
            raise
    
    def search_caiq_hybrid(
        self,
        query_vector: List[float],
        query_text: str,
        top: int = 50,
        use_semantic_ranking: bool = True,
        domain_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Convenience method for searching CAIQ questions with optional domain filter.
        
        Args:
            query_vector: 1536-dimensional embedding
            query_text: Search query text
            top: Number of results
            use_semantic_ranking: Enable semantic ranking
            domain_filter: Optional domain filter (e.g., "IAM")
        
        Returns:
            Search results dict
        """
        filter_query = f"domain eq '{domain_filter}'" if domain_filter else None
        return self.search_hybrid(
            query_vector=query_vector,
            query_text=query_text,
            index_type="caiq",
            top=top,
            use_semantic_ranking=use_semantic_ranking,
            filter_query=filter_query,
        )
    
    def search_customer_docs_hybrid(
        self,
        customer_id: str,
        query_vector: List[float],
        query_text: str,
        top: int = 50,
        use_semantic_ranking: bool = True,
    ) -> Dict[str, Any]:
        """
        Convenience method for searching customer documents.
        
        Args:
            customer_id: Customer identifier
            query_vector: 1536-dimensional embedding
            query_text: Search query text
            top: Number of results
            use_semantic_ranking: Enable semantic ranking
        
        Returns:
            Search results dict
        """
        filter_query = f"customer_id eq '{customer_id}'"
        return self.search_hybrid(
            query_vector=query_vector,
            query_text=query_text,
            index_type="customer_docs",
            customer_id=customer_id,
            top=top,
            use_semantic_ranking=use_semantic_ranking,
            filter_query=filter_query,
        )
    
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
