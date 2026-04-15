"""
"""Create Azure Cognitive Search indexes via SDK (v6).

Creates three indexes:
  1. sop_chunks              — SOP document chunks (vector + BM25)
  2. question_items          — custom questions from uploaded Excel (vector + BM25)
  3. semantic_mappings       — SOP capability → Question category mappings

Use this when the Azure Portal is blocked due to private network access restrictions.

Usage:
    python scripts/create_index.py                  # creates all three indexes
    python scripts/create_index.py --index sop      # sop_chunks only
    python scripts/create_index.py --index questions # question_items only
    python scripts/create_index.py --index mappings # semantic_mappings only

Prerequisites:
    - AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY in .env
    - Your machine must be on the allowed network (VPN/private endpoint if required)
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    HnswParameters,
    VectorSearchProfile,
    SemanticConfiguration,
    SemanticSearch,
    SemanticPrioritizedFields,
    SemanticField,
)

import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def create_caiq_index(endpoint: str, api_key: str, index_name: str = "caiq_questions") -> None:
    credential = AzureKeyCredential(api_key)
    client = SearchIndexClient(endpoint=endpoint, credential=credential)

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="question_id", type=SearchFieldDataType.String, filterable=True, retrievable=True),
        SearchableField(name="domain", type=SearchFieldDataType.String, filterable=True, retrievable=True),
        SearchableField(name="question_text", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True, retrievable=True),
        SimpleField(name="doc_name", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(name="metadata", type=SearchFieldDataType.String, retrievable=True),
        SearchField(
            name="vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            vector_search_dimensions=1536,
            vector_search_profile_name="default-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="default-hnsw",
                parameters=HnswParameters(
                    m=4,
                    ef_construction=400,
                    ef_search=500,
                    metric="cosine",
                ),
            )
        ],
        profiles=[
            VectorSearchProfile(name="default-profile", algorithm_configuration_name="default-hnsw")
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="question_text")],
                    keywords_fields=[
                        SemanticField(field_name="domain"),
                        SemanticField(field_name="question_id"),
                    ],
                ),
            )
        ]
    )

    index = SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    logger.info(f"Creating index '{index_name}'...")
    result = client.create_or_update_index(index)
    logger.info(f"Index '{result.name}' created/updated successfully.")


def _make_vector_search_and_semantic(content_field: str, keyword_fields: list):
    """Shared HNSW vector search config + semantic config for any index."""
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="default-hnsw",
                parameters=HnswParameters(
                    m=4, ef_construction=400, ef_search=500, metric="cosine",
                ),
            )
        ],
        profiles=[
            VectorSearchProfile(name="default-profile", algorithm_configuration_name="default-hnsw")
        ],
    )
    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name=content_field)],
                    keywords_fields=[SemanticField(field_name=f) for f in keyword_fields],
                ),
            )
        ]
    )
    return vector_search, semantic_search


def create_sop_index(endpoint: str, api_key: str, index_name: str = "sop_chunks") -> None:
    """Create the SOP chunks index (vector + BM25)."""
    credential = AzureKeyCredential(api_key)
    client = SearchIndexClient(endpoint=endpoint, credential=credential)

    fields = [
        SimpleField(name="id",           type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="chunk_id",     type=SearchFieldDataType.String, retrievable=True),
        SearchableField(name="filename",   type=SearchFieldDataType.String, filterable=True, retrievable=True),
        SimpleField(name="capability",   type=SearchFieldDataType.String, filterable=True, retrievable=True),
        SearchableField(name="chunk_text", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(name="chunk_index",  type=SearchFieldDataType.Int32,  retrievable=True),
        SearchField(
            name="vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            vector_search_dimensions=1536,
            vector_search_profile_name="default-profile",
        ),
    ]

    vector_search, semantic_search = _make_vector_search_and_semantic(
        "chunk_text", ["capability", "filename"]
    )

    index = SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    logger.info(f"Creating SOP index '{index_name}'...")
    result = client.create_or_update_index(index)
    logger.info(f"SOP index '{result.name}' created/updated successfully.")


def create_custom_questions_index(endpoint: str, api_key: str, index_name: str = "custom_questions") -> None:
    """Create the custom questions index (vector + BM25)."""
    credential = AzureKeyCredential(api_key)
    client = SearchIndexClient(endpoint=endpoint, credential=credential)

    fields = [
        SimpleField(name="id",            type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="question_id",   type=SearchFieldDataType.String, retrievable=True),
        SearchableField(name="category",  type=SearchFieldDataType.String, filterable=True, retrievable=True),
        SearchableField(name="question_text", type=SearchFieldDataType.String, retrievable=True),
        SearchField(
            name="vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            vector_search_dimensions=1536,
            vector_search_profile_name="default-profile",
        ),
    ]

    vector_search, semantic_search = _make_vector_search_and_semantic(
        "question_text", ["category", "question_id"]
    )

    index = SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    logger.info(f"Creating custom questions index '{index_name}'...")
    result = client.create_or_update_index(index)
    logger.info(f"Custom questions index '{result.name}' created/updated successfully.")


def main():
    parser = argparse.ArgumentParser(description="Create Azure Cognitive Search indexes")
    parser.add_argument(
        "--index",
        choices=["sop", "questions", "mappings", "all"],
        default="all",
        help="Which index to create (default: all)",
    )
    args = parser.parse_args()

    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key  = os.getenv("AZURE_SEARCH_API_KEY")

    if not endpoint or not api_key:
        logger.error("Missing AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY in .env")
        return 1

    sop_name  = os.getenv("AZURE_SEARCH_SOP_INDEX_NAME", "sop_chunks")
    q_name    = os.getenv("AZURE_SEARCH_QUESTIONS_INDEX_NAME", "question_items")

    try:
        if args.index in ("sop", "all"):
            create_sop_index(endpoint, api_key, sop_name)
        if args.index in ("questions", "all"):
            create_custom_questions_index(endpoint, api_key, q_name)
        return 0
    except Exception as e:
        logger.error(f"Failed to create index: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
