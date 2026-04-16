import os
from azure.search.documents.indexes import SearchIndexClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get endpoint and API key
endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
api_key = os.getenv("AZURE_SEARCH_API_KEY")

if not endpoint or not api_key:
    raise ValueError("Missing AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY in .env")

# Initialize the client
client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(api_key))

# List of indexes to check
indexes_to_check = ["psmart_questions", "semantic_mappings", "sop_chunks"]

for index_name in indexes_to_check:
    try:
        index = client.get_index(index_name)
        print(f"\nSchema for index '{index_name}':")
        for field in index.fields:
            print(f"- {field.name} (Type: {field.type}, Searchable: {field.searchable}, Filterable: {field.filterable})")
    except Exception as e:
        print(f"Error retrieving schema for index '{index_name}': {e}")