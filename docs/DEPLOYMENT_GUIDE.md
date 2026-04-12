# Azure Cognitive Search Migration - Deployment Guide

Complete step-by-step guide to deploy the QuestionaireRAG with Azure Cognitive Search hybrid indexing.

## Overview

This deployment replaces Chroma DB + manual BM25 indexing with Azure Cognitive Search, enabling:
- **Hybrid search**: Vector (HNSW) + BM25 keyword search in single query
- **Semantic ranking**: AI-powered relevance reranking
- **Scalability**: Azure-managed infrastructure
- **Native multi-tenancy**: Per-customer document indexes with strong isolation

**Timeline**: ~4-6 hours (depending on CAIQ data size and Azure setup)

---

## Prerequisites

### Required

- [ ] Python 3.9+ installed locally
- [ ] Azure subscription with billing enabled
- [ ] Azure CLI installed (`az --version`)
- [ ] Git and GitHub access
- [ ] Admin/DevOps access to Azure portal

### Optional

- [ ] Docker/Docker Compose (for containerized deployment)
- [ ] VS Code with Azure Tools extension (for portal inspection)
- [ ] Git experience for version control

---

## Step 1: Azure Infrastructure Setup (30-45 min)

### 1.1 Create Azure Cognitive Search Service

**Via Azure Portal**:
1. Go to https://portal.azure.com
2. Search for "Azure Cognitive Search"
3. Click "Create"
4. Fill in:
   - **Resource Group**: Create new (e.g., `questionnaire-rag-rg`)
   - **Service Name**: `questionnaire-search` (must be globally unique)
   - **Location**: Choose region closest to users (e.g., `East US`)
   - **Pricing Tier**: **Standard** or higher (Free tier has limits on semantic ranking)
5. Click "Review + Create" → "Create"
6. Wait for deployment (5-10 min)

**Via Azure CLI**:
```bash
az search service create \
  --resource-group questionnaire-rag-rg \
  --name questionnaire-search \
  --sku Standard \
  --location eastus
```

### 1.2 Obtain Credentials

1. Go to your Azure Cognitive Search service in Portal
2. Click "Keys" in left sidebar
3. Copy:
   - **Primary Admin Key** → `AZURE_SEARCH_API_KEY`
   - **Endpoint URL** (top of page) → `AZURE_SEARCH_ENDPOINT`

Example:
```
AZURE_SEARCH_ENDPOINT=https://questionnaire-search.search.windows.net
AZURE_SEARCH_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 1.3 Create Index via Portal

1. In Azure Cognitive Search service
2. Click "Indexes" → "Create index"
3. Use the schema from [docs/azure_index_schemas.md](../docs/azure_index_schemas.md)
4. Or use REST API:
```bash
curl -X PUT https://questionnaire-search.search.windows.net/indexes/caiq_questions?api-version=2024-07-01 \
  -H "Content-Type: application/json" \
  -H "api-key: YOUR_ADMIN_KEY" \
  -d @docs/index_schema_caiq.json
```

---

## Step 2: Environment Setup (15 min)

### 2.1 Clone or Navigate to Repository

```bash
cd /Users/chinmaiallada/QuestionaireRAG
git status  # Verify you're on correct branch
```

### 2.2 Update Environment Variables

Edit `.env`:
```bash
# Azure OpenAI (existing — verify these are correct)
OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com
OPENAI_API_KEY=your-key
EMBEDINGS_OPENAI_DEPLOYMENT_NAME=text-embedding-ada-002

# Azure Cognitive Search (NEW — add these)
AZURE_SEARCH_ENDPOINT=https://questionnaire-search.search.windows.net
AZURE_SEARCH_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AZURE_SEARCH_CAIQ_INDEX_NAME=caiq_questions
```

### 2.3 Verify File Structure

```bash
ls -la data/
  - questions_store.json (should exist)
  - chroma_db/ (existing, will keep for comparison)
  - bm25_index.pkl (existing, will keep for comparison)
  - questionnaires/CAIQv4.0.3_STAR-Security-Questionnaire_*.xlsx

ls -la app/core/
  - azure_search.py (NEW)
  - indexer.py (UPDATED)
  - retriever.py (UPDATED)
  - embedder.py (unchanged)
  - ingestor.py (unchanged)
```

---

## Step 3: Install Dependencies (10 min)

### 3.1 Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows
```

### 3.2 Install Requirements

```bash
pip install -r requirements.txt
```

Verify key packages:
```bash
pip list | grep -E "azure-search|azure-identity|chromadb|openai"
```

### 3.3 Verify Imports

```bash
python -c "from app.core.azure_search import AzureSearchClient; print('✓ Imports OK')"
```

---

## Step 4: Data Migration (30 min - 2 hours depending on CAIQ size)

### 4.1 Pre-Migration Backup

```bash
# Backup existing data (just in case)
cp -r data data.backup.$(date +%Y%m%d_%H%M%S)
cp -r data/chroma_db data/chroma_db.backup
cp data/bm25_index.pkl data/bm25_index.pkl.backup
```

### 4.2 Verify CAIQ Data Exists

```bash
ls -lh data/questionnaires/CAIQv*.xlsx
```

If not found, download from CAIQ website and place in `data/questionnaires/`.

### 4.3 Run Migration Script

```bash
python scripts/migrate_caiq_to_azure.py
```

Monitor output:
```
Loading questions from CAIQ...
  Found 1200 questions
Loading embeddings from Chroma DB...
  Loaded 1200 embeddings from Chroma
Loading questions store...
  Loaded 1200 questions from store
Formatting documents for Azure CS...
  Formatted 1200 documents for Azure CS indexing
Initializing Azure Search client...
Verifying Azure Search connection...
Connection verified. Proceeding with indexing...
Indexing batch 1 (1000 documents)...
  Batch 1: 1000 succeeded, 0 failed
Indexing batch 2 (200 documents)...
  Batch 2: 200 succeeded, 0 failed

============================================================
Migration Summary:
  Total Documents Indexed: 1200
  Total Failed: 0
  Success Rate: 100.0%
============================================================

Validating migration...
Running test search query...
Test search returned 50 results
✓ Validation passed: Azure CS is responding correctly
```

**If migration fails**:
- Check Azure credentials in `.env`
- Verify Azure CS service is running (Portal → status green)
- Check network connectivity: `ping questionnaire-search.search.windows.net`
- Review error logs for details

### 4.4 Verify Data in Azure Portal

1. Go to Azure Cognitive Search service in Portal
2. Click "Indexes" → "caiq_questions"
3. Verify document count shows 1200 (or your CAIQ size)

---

## Step 5: Start Development Server (5 min)

### 5.1 Run FastAPI Server

```bash
uvicorn main:app --reload --port 8000
```

Expected output:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
INFO:     QuestionaireRAG server starting up...
INFO:     ✓ Azure Cognitive Search connection verified
```

If startup fails:
```bash
# Check for port conflicts
lsof -i :8000  # macOS/Linux
netstat -ano | findstr :8000  # Windows
```

### 5.2 Test Health Endpoint

```bash
curl http://localhost:8000/health
# Response: {"status":"ok","index_built":false}  <- Before indexing via API
```

---

## Step 6: Index CAIQ via API (5-10 min)

### 6.1 Trigger Indexing

```bash
curl -X POST http://localhost:8000/index/questionnaires
```

Expected response:
```json
{
  "message": "Index built successfully in Azure Cognitive Search",
  "questions_indexed": 1200
}
```

### 6.2 Monitor Progress

Watch server logs for progress:
```
INFO:     Retrieved query for customer customer_1 with top_k=20
INFO:     Successfully retrieved 20 questions
```

### 6.3 Verify Indexing Complete

```bash
curl http://localhost:8000/health
# Response: {"status":"ok","index_built":true}  <- Now true!
```

---

## Step 7: Test Query Endpoints (15 min)

### 7.1 Prepare Test Customer

Ensure test customer has PDFs:
```bash
ls data/customers/customer_1/
# Should see .pdf files (SOW, SOP, security docs, etc.)
```

### 7.2 Run Sample Query

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "customer_1",
    "top_k": 10,
    "use_semantic_ranking": true
  }'
```

Expected response (pretty-printed):
```json
{
  "customer_id": "customer_1",
  "context_summary": "The organization implements multi-factor authentication, password policies, role-based access control...",
  "total_results": 10,
  "questions": [
    {
      "rank": 1,
      "question_id": "IAM-01.1",
      "domain": "IAM",
      "question": "Does the organization have an Identity and Access Management (IAM) policy?",
      "score": 4.235,
      "semantic_score": 0.956,
      "source": "CAIQ"
    },
    ...
  ]
}
```

### 7.3 Test Various Parameters

```bash
# Without semantic ranking
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "customer_1",
    "top_k": 20,
    "use_semantic_ranking": false
  }'

# Different customer
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "customer_2",
    "top_k": 15,
    "use_semantic_ranking": true
  }'
```

---

## Step 8: Production Deployment

### 8.1 Choose Deployment Platform

#### Option A: Azure App Service (Recommended)

**Advantages**: Native Azure integration, auto-scaling, managed SSL

```bash
# Create App Service Plan
az appservice plan create \
  --name questionnaire-rag-plan \
  --resource-group questionnaire-rag-rg \
  --sku B1

# Create Web App
az webapp create \
  --name questionnaire-rag-app \
  --resource-group questionnaire-rag-rg \
  --plan questionnaire-rag-plan \
  --runtime "python|3.11"

# Configure environment
az webapp config appsettings set \
  --name questionnaire-rag-app \
  --resource-group questionnaire-rag-rg \
  --settings \
    AZURE_SEARCH_ENDPOINT="https://questionnaire-search.search.windows.net" \
    AZURE_SEARCH_API_KEY="$AZURE_SEARCH_API_KEY" \
    OPENAI_API_KEY="$OPENAI_API_KEY"

# Deploy
az webapp deployment source config-zip \
  --name questionnaire-rag-app \
  --resource-group questionnaire-rag-rg \
  --src app.zip
```

#### Option B: Docker on Local/VM

**Advantages**: Portable, consistent across environments

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# Build
docker build -t questionnaire-rag:latest .

# Run
docker run -d \
  -p 8000:8000 \
  -e AZURE_SEARCH_ENDPOINT="https://questionnaire-search.search.windows.net" \
  -e AZURE_SEARCH_API_KEY="$AZURE_SEARCH_API_KEY" \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  questionnaire-rag:latest
```

#### Option C: Your Existing Infrastructure

Deploy using your standard CI/CD pipeline (GitHub Actions, GitLab CI, etc.)

### 8.2 Set Environment Variables

In production, never hardcode secrets. Use:
- **Azure App Service**: Configuration → Application settings
- **Docker**: `-e` flags or `.env` file (not in repository!)
- **Kubernetes**: Secrets
- **VM**: Secure configuration management

### 8.3 Database/Storage Considerations

- **questions_store.json**: Should be included in deployment package OR fetched from Azure Blob Storage on startup
- **Customer PDFs**: Uploaded dynamically or stored in Azure Blob Storage

### 8.4 Monitoring & Logging

Configure centralized logging:

```python
# In main.py or a logging config module
import logging.handlers

# Azure Monitor
handler = logging.handlers.SysLogHandler(
    address=("questionnaire-rag-app.monitoring.azure.com", 514)
)
logging.root.addHandler(handler)
```

Or use Application Insights:
```python
from azure.monitor.opentelemetry import configure_azure_monitor

configure_azure_monitor(
    connection_string="InstrumentationKey=..."
)
```

---

## Step 9: Validation & Sign-Off

Follow the checklist in [VALIDATION_CHECKLIST.md](./VALIDATION_CHECKLIST.md).

Key tests:
- [ ] Health endpoint returns `true`
- [ ] Indexing completes all questions
- [ ] Query returns relevant results
- [ ] Semantic ranking improves result quality
- [ ] Performance metrics meet targets (< 2s latency)
- [ ] Error handling works (invalid customer, etc.)

---

## Step 10: Cleanup & Documentation

### 10.1 Decide on Legacy System

```bash
# Option A: Keep Chroma as backup
# (recommended first 2-4 weeks)

# Option B: Remove Chroma after full validation
rm -rf data/chroma_db
rm data/bm25_index.pkl
```

### 10.2 Update Documentation

- [ ] README.md: Updated with Azure CS info
- [ ] API docs: Updated with new score fields
- [ ] Architecture: Diagram updated
- [ ] Deployment: This guide

### 10.3 Notify Team

- [ ] Update wiki/knowledge base
- [ ] Send notification to team
- [ ] Update monitoring/alerting rules

---

## Troubleshooting

### Issue: "Azure Cognitive Search credentials not found"

**Solution**: Verify `.env` file has:
```
AZURE_SEARCH_ENDPOINT=https://...
AZURE_SEARCH_API_KEY=xxx...
```

Restart server after updating `.env`.

### Issue: "Connection to Azure Search failed"

**Solution**:
1. Verify service is running in Azure Portal
2. Check network connectivity: `nslookup questionnaire-search.search.windows.net`
3. Verify firewall rules allow outbound HTTPS on port 443

### Issue: "Index already exists" error during creation

**Solution**: Index already exists. Either:
- Use it as-is (if CAIQ data is current)
- Delete via Portal and recreate
- Use different index name

### Issue: Migration fails with "Dimension mismatch"

**Solution**: Vectors must be exactly 1536-dimensional
- Verify embedding model is `text-embedding-ada-002`
- Check for corrupted vectors in Chroma DB

### Issue: Queries return no results after indexing

**Solution**:
1. Check index has documents: `curl https://questionnaire-search.search.windows.net/indexes/caiq_questions/docs/$count`
2. Verify `questions_store.json` is populated
3. Check Azure CS health in Portal

---

## Performance Tuning

### Query Latency Too High?

1. **Check Azure CS search units**: Portal → Scale settings
   - Increase if consistently > 80% utilization

2. **Disable semantic ranking** if not needed:
   - Set `use_semantic_ranking=false` in requests

3. **Profile query components**: Modify retriever.py to log timestamps for each step

### Cost Too High?

1. **Monitor query volume**: Aim for < 10K queries/day on Standard tier
2. **Consider Free tier** for development (limited to 10K docs)
3. **Implement caching** for frequently asked questions
4. **Batch customer doc indexing** to off-peak hours

---

## Rollback Plan

If critical issues occur:

```bash
# 1. Revert code
git revert HEAD  # or checkout previous commit

# 2. Keep using Chroma DB temporarily
# (retriever.py will have old code)

# 3. Investigate issue
# (review logs, test locally)

# 4. Fix and redeploy
# (or escalate to team)
```

---

**Last Updated**: April 2026
**Version**: 3.0 (Azure Cognitive Search Migration)
**Author**: Migration Team

