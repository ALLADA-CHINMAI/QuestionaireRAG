"""
Create the caiq_questions index in Azure Cognitive Search via SDK.

Use this when the Azure Portal is blocked due to private network access restrictions.

Usage:
    python scripts/create_index.py

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


def main():
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    index_name = os.getenv("AZURE_SEARCH_CAIQ_INDEX_NAME", "caiq_questions")

    if not endpoint or not api_key:
        logger.error("Missing AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY in .env")
        return 1

    try:
        create_caiq_index(endpoint, api_key, index_name)
        return 0
    except Exception as e:
        logger.error(f"Failed to create index: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
