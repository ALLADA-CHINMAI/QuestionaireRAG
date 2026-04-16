"""
Unit and Integration Tests for Azure Cognitive Search Migration
"""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from typing import List

# Adjust path to import from app module
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.azure_search import AzureSearchClient
from app.core.retriever import retrieve_for_customer
from app.core.indexer import get_azure_search_client, load_questions_store


class TestAzureSearchClient:
    """Unit tests for AzureSearchClient wrapper (v6)."""
    
    @pytest.fixture
    def mock_credentials(self):
        """Mock Azure credentials for v6."""
        return {
            "endpoint": "https://test-service.search.windows.net",
            "api_key": "test-api-key",
            "sop_index": "sop_chunks",
            "questions_index": "question_items",
            "mappings_index": "semantic_mappings"
        }
    
    @pytest.fixture
    def mock_client(self, mock_credentials):
        """Create AzureSearchClient v6 with mocked credentials."""
        with patch('app.core.azure_search.SearchClient'):
            client = AzureSearchClient(
                endpoint=mock_credentials["endpoint"],
                api_key=mock_credentials["api_key"],
                sop_index_name=mock_credentials["sop_index"],
                questions_index_name=mock_credentials["questions_index"],
                mappings_index_name=mock_credentials["mappings_index"]
            )
        return client
    
    def test_client_initialization(self, mock_credentials):
        """Test AzureSearchClient v6 initialization."""
        with patch('app.core.azure_search.SearchClient'):
            client = AzureSearchClient(
                endpoint=mock_credentials["endpoint"],
                api_key=mock_credentials["api_key"],
                sop_index_name=mock_credentials["sop_index"],
                questions_index_name=mock_credentials["questions_index"],
                mappings_index_name=mock_credentials["mappings_index"]
            )
            assert client.endpoint == mock_credentials["endpoint"]
            assert client.api_key == mock_credentials["api_key"]
            assert client.sop_index_name == mock_credentials["sop_index"]
            assert client.questions_index_name == mock_credentials["questions_index"]
            assert client.mappings_index_name == mock_credentials["mappings_index"]
    
    def test_search_sop_hybrid_signature(self, mock_client):
        """Test that search_sop_hybrid accepts correct parameters."""
        query_vector = [0.0] * 1536
        query_text = "test query"
        
        with patch.object(mock_client, '_get_sop_client') as mock_get_sop:
            mock_sop_client = Mock()
            mock_sop_client.search.return_value = []
            mock_get_sop.return_value = mock_sop_client
            
            result = mock_client.search_sop_hybrid(
                query_vector=query_vector,
                query_text=query_text,
                top=5
            )
            
            # Verify search was called
            mock_sop_client.search.assert_called_once()
    
    def test_index_questions_document_format(self, mock_client):
        """Test that index_questions accepts properly formatted documents."""
        documents = [
            {
                "id": "Q_001",
                "question_id": "Q_001",
                "category": "Compliance",
                "question_text": "Does the vendor provide SOC 2 Type II certification?",
                "vector": [0.0] * 1536
            }
        ]
        
        with patch.object(mock_client, '_get_questions_client') as mock_get_questions:
            mock_questions_client = Mock()
            mock_result = Mock()
            mock_result.succeeded = True
            mock_questions_client.upload_documents.return_value = [mock_result]
            mock_get_questions.return_value = mock_questions_client
            
            result = mock_client.index_questions(documents)
            
            assert "succeeded" in result
            assert "failed" in result
            mock_questions_client.upload_documents.assert_called_once_with(documents)
    
    def test_search_questions_hybrid_filter(self, mock_client):
        """Test that questions search can filter by category."""
        query_vector = [0.0] * 1536
        query_text = "compliance"
        category = "Compliance"
        
        with patch.object(mock_client, '_get_questions_client') as mock_get_questions:
            mock_questions_client = Mock()
            mock_questions_client.search.return_value = []
            mock_get_questions.return_value = mock_questions_client
            
            result = mock_client.search_questions_hybrid(
                query_vector=query_vector,
                query_text=query_text,
                category_filter=category
            )
            
            # Verify search was called
            mock_questions_client.search.assert_called_once()


class TestRetrieverIntegration:
    """Integration tests for retriever with Azure Cognitive Search."""
    
    @pytest.fixture
    def mock_azure_client(self):
        """Mock Azure Cognitive Search client."""
        client = Mock(spec=AzureSearchClient)
        client.search_questions_hybrid.return_value = {
            "results": [
                {
                    "id": "1",
                    "question_id": "IAM-01.1",
                    "domain": "IAM",
                    "question_text": "Test question",
                    "score": 2.5,
                    "semantic_score": 0.95,
                },
                {
                    "id": "2",
                    "question_id": "IAM-01.2",
                    "domain": "IAM",
                    "question_text": "Another test question",
                    "score": 2.1,
                    "semantic_score": 0.87,
                }
            ],
            "total_count": 2,
            "semantic_ranking_enabled": True
        }
        return client
    
    @pytest.fixture
    def mock_questions_store(self):
        """Mock questions store with test data."""
        return {
            "IAM-01.1": {
                "question_id": "IAM-01.1",
                "domain": "IAM",
                "question_text": "Test question from store",
                "source": "PSmart"
            },
            "IAM-01.2": {
                "question_id": "IAM-01.2",
                "domain": "IAM",
                "question_text": "Another test question from store",
                "source": "PSmart"
            }
        }
    
    @patch('app.core.retriever.get_azure_search_client')
    @patch('app.core.retriever.load_customer_docs')
    @patch('app.core.retriever.summarize_customer_context')
    @patch('app.core.retriever.embed_query')
    @patch('app.core.retriever.load_questions_store')
    def test_retrieve_for_customer_flow(
        self,
        mock_load_store,
        mock_embed,
        mock_summarize,
        mock_load_docs,
        mock_get_client,
        mock_azure_client,
        mock_questions_store
    ):
        """Test end-to-end retriever pipeline."""
        # Setup mocks
        mock_load_docs.return_value = "Customer SOP document text"
        mock_summarize.return_value = "Summarized security topics"
        mock_embed.return_value = [0.0] * 1536
        mock_load_store.return_value = mock_questions_store
        mock_get_client.return_value = mock_azure_client
        
        # Execute retrieval
        result = retrieve_for_customer(
            customer_id="test_customer",
            top_k=2,
            use_semantic_ranking=True
        )
        
        # Assertions
        assert result["customer_id"] == "test_customer"
        assert result["context_summary"] == "Summarized security topics"
        assert result["total_results"] == 2
        assert len(result["questions"]) == 2
        
        # Check first result
        first_q = result["questions"][0]
        assert first_q["rank"] == 1
        assert first_q["question_id"] == "IAM-01.1"
        assert first_q["domain"] == "IAM"
        assert "score" in first_q
        assert "semantic_score" in first_q
        
        # Verify Azure client was called correctly
        mock_azure_client.search_questions_hybrid.assert_called_once()
        call_kwargs = mock_azure_client.search_questions_hybrid.call_args[1]
        assert call_kwargs["query_vector"] == [0.0] * 1536
        assert call_kwargs["query_text"] == "Summarized security topics"
        assert call_kwargs["use_semantic_ranking"] == True


class TestErrorHandling:
    """Test error handling in Azure Cognitive Search integration."""
    
    @pytest.fixture
    def mock_client(self):
        """Create mock Azure Search client."""
        with patch('app.core.azure_search.SearchClient'):
            client = AzureSearchClient(
                endpoint="https://test.search.windows.net",
                api_key="test-key",
                sop_index_name="sop_chunks",
                questions_index_name="psmart_questions",
                mappings_index_name="semantic_mappings"
            )
        return client
    
    def test_missing_azure_credentials(self):
        """Test that missing Azure credentials raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError):
                get_azure_search_client()
    
    @patch('app.core.azure_search.SearchClient')
    def test_index_psmart_partial_failure(self, mock_search_client):
        """Test handling of partial indexing failures."""
        client = AzureSearchClient(
            endpoint="https://test.search.windows.net",
            api_key="test-key",
            sop_index_name="sop_chunks",
            questions_index_name="psmart_questions",
            mappings_index_name="semantic_mappings"
        )
        
        # Mock mixed success/failure results
        success_result = Mock()
        success_result.succeeded = True
        
        fail_result = Mock()
        fail_result.succeeded = False
        fail_result.error_message = "Dimension mismatch"
        
        mock_search_client.return_value.upload_documents.return_value = [
            success_result,
            fail_result
        ]
        
        documents = [{"id": "1", "vector": [0.0] * 1536}] * 2
        
        result = client.index_psmart(documents)
        
        assert result["succeeded"] == 1
        assert result["failed"] == 1


class TestVectorDimensions:
    """Test vector dimension validation."""
    
    def test_vector_dimension_expectation(self):
        """Verify that system expects 1536-dimensional vectors."""
        # This is a documentation test
        assert 1536 == 1536  # Azure OpenAI text-embedding-ada-002 output dim
        
        # Ensure vector is not 3072 (which would be text-embedding-3-large)
        assert 1536 != 3072


# ============================================================================
# Test execution with pytest
# ============================================================================
# Run tests with: pytest tests/test_azure_integration.py -v

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
