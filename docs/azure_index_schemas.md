# Azure Cognitive Search Index Schemas

This document defines the index schemas required for migrating from Chroma DB to Azure Cognitive Search with hybrid indexing.

## Overview

Two index types are needed:
1. **CAIQ Questions Index** (static, shared): All CAIQ questions with vector embeddings
2. **Customer Documents Index** (per-customer, dynamic): Indexed on-demand when customer uploads docs

---

## Index 1: CAIQ Questions Index

**Index Name**: `psmart_questions`

### Fields

| Field Name | Type | Retrievable | Searchable | Filterable | Sortable | Facetable | Analyzer | Vector Config |
|------------|------|------------|-----------|-----------|----------|-----------|----------|---------------|
| `id` | Edm.String | ✓ | ✗ | ✓ | ✓ | ✗ | - | - |
| `question_id` | Edm.String | ✓ | ✓ | ✓ | ✓ | ✓ | standard | - |
| `domain` | Edm.String | ✓ | ✓ | ✓ | ✓ | ✓ | standard | - |
| `question_text` | Edm.String | ✓ | ✓ | ✗ | ✓ | ✗ | standard | - |
| `source` | Edm.String | ✓ | ✓ | ✓ | ✗ | ✓ | standard | - |
| `vector` | Collection(Edm.Single) | ✓ | ✗ | ✗ | ✗ | ✗ | - | 1536-dim, HNSW |

### Key

- **Primary Key**: `id`

### Vector Search Configuration

```json
{
  "vector_search": {
    "algorithms": [
      {
        "name": "my-hnsw",
        "kind": "hnsw",
        "parameters": {
          "m": 4,
          "ef_construction": 400,
          "ef_search": 500,
          "metric": "cosine"
        }
      }
    ],
    "profiles": [
      {
        "name": "my-vector-config",
        "algorithm_configuration_name": "my-hnsw",
        "vectorizer_name": null
      }
    ]
  }
}
```

### Semantic Configuration

```json
{
  "semantic_configurations": [
    {
      "name": "default",
      "prioritized_fields": {
        "title_field": {
          "field_name": "domain"
        },
        "content_fields": [
          {
            "field_name": "question_text"
          }
        ],
        "keywords_fields": [
          {
            "field_name": "source"
          }
        ]
      }
    }
  ]
}
```

### Scoring Profile (Optional)

```json
{
  "scoring_profiles": [
    {
      "name": "hybrid_score",
      "text_weights": {
        "weights": {
          "question_text": 2,
          "domain": 1,
          "source": 0.5
        }
      },
      "function_aggregation": "sum"
    }
  ]
}
```

### REST API Example: Create Index

```bash
PUT https://{service-name}.search.windows.net/indexes/psmart_questions?api-version=2024-07-01
Content-Type: application/json
api-key: {admin-key}

{
  "name": "psmart_questions",
  "fields": [
    {
      "name": "id",
      "type": "Edm.String",
      "key": true,
      "retrievable": true,
      "searchable": false,
      "filterable": true,
      "sortable": true,
      "facetable": false,
      "analyzer": null
    },
    {
      "name": "question_id",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": true,
      "filterable": true,
      "sortable": true,
      "facetable": true,
      "analyzer": "standard.lucene"
    },
    {
      "name": "domain",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": true,
      "filterable": true,
      "sortable": true,
      "facetable": true,
      "analyzer": "standard.lucene"
    },
    {
      "name": "question_text",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": true,
      "filterable": false,
      "sortable": true,
      "facetable": false,
      "analyzer": "standard.lucene"
    },
    {
      "name": "source",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": true,
      "filterable": true,
      "sortable": false,
      "facetable": true,
      "analyzer": "standard.lucene"
    },
    {
      "name": "vector",
      "type": "Collection(Edm.Single)",
      "retrievable": true,
      "searchable": false,
      "filterable": false,
      "sortable": false,
      "facetable": false,
      "vector_search_dimensions": 1536,
      "vector_search_profile_name": "my-vector-config"
    }
  ],
  "vector_search": {
    "algorithms": [
      {
        "name": "my-hnsw",
        "kind": "hnsw",
        "parameters": {
          "m": 4,
          "ef_construction": 400,
          "ef_search": 500,
          "metric": "cosine"
        }
      }
    ],
    "profiles": [
      {
        "name": "my-vector-config",
        "algorithm_configuration_name": "my-hnsw"
      }
    ]
  },
  "semantic_configurations": [
    {
      "name": "default",
      "prioritized_fields": {
        "title_field": {
          "field_name": "domain"
        },
        "content_fields": [
          {
            "field_name": "question_text"
          }
        ],
        "keywords_fields": [
          {
            "field_name": "source"
          }
        ]
      }
    }
  ]
}
```

---

## Index 2: Customer Documents Index (Template)

**Index Name Pattern**: `customer_docs_{customer_id}`

Example: `customer_docs_adventhealth`, `customer_docs_customer_1`

### Fields

| Field Name | Type | Retrievable | Searchable | Filterable | Sortable | Facetable | Analyzer | Vector Config |
|------------|------|------------|-----------|-----------|----------|-----------|----------|---------------|
| `id` | Edm.String | ✓ | ✗ | ✓ | ✓ | ✗ | - | - |
| `customer_id` | Edm.String | ✓ | ✗ | ✓ | ✗ | ✓ | - | - |
| `doc_name` | Edm.String | ✓ | ✓ | ✓ | ✓ | ✓ | standard | - |
| `content_chunk` | Edm.String | ✓ | ✓ | ✗ | ✓ | ✗ | standard | - |
| `chunk_index` | Edm.Int32 | ✓ | ✗ | ✓ | ✓ | ✗ | - | - |
| `chunk_vector` | Collection(Edm.Single) | ✓ | ✗ | ✗ | ✗ | ✗ | - | 1536-dim, HNSW |
| `metadata` | Edm.ComplexType | ✓ | ✗ | ✗ | ✗ | ✗ | - | - |
| `created_at` | Edm.DateTimeOffset | ✓ | ✗ | ✓ | ✓ | ✗ | - | - |

### Metadata Sub-Fields (ComplexType)

```json
{
  "name": "metadata",
  "type": "Collection(Edm.ComplexType)",
  "fields": [
    {
      "name": "page_number",
      "type": "Edm.Int32"
    },
    {
      "name": "source_file",
      "type": "Edm.String"
    },
    {
      "name": "upload_date",
      "type": "Edm.DateTimeOffset"
    }
  ]
}
```

### Key

- **Primary Key**: `id` (format: `{customer_id}_{doc_name}_{chunk_index}`)

### Vector Search Configuration

Same as CAIQ index (HNSW, cosine, 1536 dimensions).

### Semantic Configuration

```json
{
  "semantic_configurations": [
    {
      "name": "default",
      "prioritized_fields": {
        "title_field": {
          "field_name": "doc_name"
        },
        "content_fields": [
          {
            "field_name": "content_chunk"
          }
        ],
        "keywords_fields": [
          {
            "field_name": "customer_id"
          }
        ]
      }
    }
  ]
}
```

### REST API Example: Create Index

```bash
PUT https://{service-name}.search.windows.net/indexes/customer_docs_customer_1?api-version=2024-07-01
Content-Type: application/json
api-key: {admin-key}

{
  "name": "customer_docs_customer_1",
  "fields": [
    {
      "name": "id",
      "type": "Edm.String",
      "key": true,
      "retrievable": true,
      "searchable": false,
      "filterable": true,
      "sortable": true
    },
    {
      "name": "customer_id",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": false,
      "filterable": true,
      "sortable": false,
      "facetable": true
    },
    {
      "name": "doc_name",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": true,
      "filterable": true,
      "sortable": true,
      "facetable": true,
      "analyzer": "standard.lucene"
    },
    {
      "name": "content_chunk",
      "type": "Edm.String",
      "retrievable": true,
      "searchable": true,
      "filterable": false,
      "analyzer": "standard.lucene"
    },
    {
      "name": "chunk_index",
      "type": "Edm.Int32",
      "retrievable": true,
      "searchable": false,
      "filterable": true,
      "sortable": true
    },
    {
      "name": "chunk_vector",
      "type": "Collection(Edm.Single)",
      "retrievable": true,
      "searchable": false,
      "filterable": false,
      "vector_search_dimensions": 1536,
      "vector_search_profile_name": "my-vector-config"
    },
    {
      "name": "metadata",
      "type": "Collection(Edm.ComplexType)",
      "fields": [
        {
          "name": "page_number",
          "type": "Edm.Int32"
        },
        {
          "name": "source_file",
          "type": "Edm.String"
        },
        {
          "name": "upload_date",
          "type": "Edm.DateTimeOffset"
        }
      ]
    },
    {
      "name": "created_at",
      "type": "Edm.DateTimeOffset",
      "retrievable": true,
      "sortable": true,
      "filterable": true
    }
  ],
  "vector_search": {
    "algorithms": [
      {
        "name": "my-hnsw",
        "kind": "hnsw",
        "parameters": {
          "m": 4,
          "ef_construction": 400,
          "ef_search": 500,
          "metric": "cosine"
        }
      }
    ],
    "profiles": [
      {
        "name": "my-vector-config",
        "algorithm_configuration_name": "my-hnsw"
      }
    ]
  },
  "semantic_configurations": [
    {
      "name": "default",
      "prioritized_fields": {
        "title_field": {
          "field_name": "doc_name"
        },
        "content_fields": [
          {
            "field_name": "content_chunk"
          }
        ],
        "keywords_fields": [
          {
            "field_name": "customer_id"
          }
        ]
      }
    }
  ]
}
```

---

## Setup Instructions

1. **Create Azure Cognitive Search Service**
   - Go to Azure Portal → Create resource → Azure Cognitive Search
   - Choose appropriate pricing tier (Standard or above for semantic ranking)
   - Note the service name and endpoint URL

2. **Create CAIQ Index**
   - Use Azure Portal → Indexes → Create index
   - OR use REST API example above with your admin API key

3. **Create Customer Document Index Template**
   - Repeat for each customer when they upload docs
   - Use template above, replace `{customer_id}` with actual customer ID

4. **Obtain Credentials**
   - Admin API Key: Azure Portal → Azure Cognitive Search → Keys → Primary Admin Key
   - Query API Key (optional, for production): Secondary key
   - Endpoint URL: https://{service-name}.search.windows.net

5. **Verify Connection**
   - Run health check via Python client or REST API
   - Example query: `GET https://{service-name}.search.windows.net/indexes/psmart_questions?api-version=2024-07-01`

---

## Pricing Considerations

- **Search Units**: Charged per month based on tier (Standard: $250/month base)
- **Semantic Ranking**: ~$5/1000 queries (additional)
- **Storage**: ~$0.50/GB per month
- **Index Replicas**: For HA (standard tier requires 2+ replicas)

For estimation: ~$300-500/month for standard tier with semantic ranking, assuming moderate query volume (<10K/day).

---

## Migration Checklist

- [ ] Azure Cognitive Search service created
- [ ] CAIQ index created with correct schema
- [ ] CAIQ data indexed (Python script in Phase 3)
- [ ] Customer doc index template created
- [ ] Credentials added to `.env`
- [ ] `azure_search.py` client initialized
- [ ] Hybrid search validated (CAIQ queries)
- [ ] Semantic ranking tested
- [ ] Customer doc indexing tested (Phase 3)
- [ ] Chroma DB comparisons (baseline validation)
- [ ] Production deployment
