# app/core — RAG pipeline modules
#
# Modules in this package:
#   ingestor  — parse PSmart questions XLSX and customer PDFs into raw text/structs
#   embedder  — generate dense vectors + summaries (Azure OpenAI)
#   indexer   — build and persist ChromaDB (dense) + BM25 (sparse) indexes
#   retriever — hybrid search + Reciprocal Rank Fusion, returns ranked questions
