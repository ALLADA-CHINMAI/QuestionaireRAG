# QuestionaireRAG v6 - Complete Setup Guide

**Date:** April 15, 2026  
**Version:** v6 (SOW-Based Prioritization & Configurable Results)

---

## ✅ Prerequisites Checklist

- [ ] Python 3.9+ installed
- [ ] Azure subscription with access to Azure Cognitive Search
- [ ] Azure OpenAI endpoint with deployments for GPT-4o and text-embedding-ada-002
- [ ] Network access to Azure services (or VPN/private endpoint configured)

---

## Step 1: Install Python Dependencies (5 min)

### 1.1 Create/Activate Virtual Environment

```powershell
# Create venv if not exists
python -m venv venv

# Activate venv
.\venv\Scripts\activate
```

### 1.2 Install Requirements

```powershell
pip install -r requirements.txt
```

### 1.3 Verify Installation

```powershell
# Check key packages are installed
pip list | Select-String "azure-search|openai|fastapi|python-docx"
```

Expected output should include:
```
azure-search-documents    11.4.0+
fastapi                   0.111.0
openai                    1.0.0+
python-docx               1.1.0+
```

---

## Step 2: Configure Environment Variables (10 min)

### 2.1 Copy Example File

```powershell
# Copy .env.example to .env if it doesn't exist
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

### 2.2 Edit `.env` File

Open `.env` and fill in your actual values:

```ini
# Azure OpenAI Configuration (REQUIRED)
OPENAI_ENDPOINT=https://YOUR-ENDPOINT.openai.azure.com
OPENAI_DEPLOYMENT_NAME=gpt-4o
OPENAI_API_KEY=YOUR-AZURE-OPENAI-API-KEY-HERE
EMBEDINGS_OPENAI_DEPLOYMENT_NAME=text-embedding-ada-002

# Azure Authentication (if using Azure AD tokens - OPTIONAL)
AUTH_TENANT_ID=your-tenant-id
AUTH_CLIENT_ID=your-client-id
AUTH_CLIENT_SECRET=your-client-secret
AUTH_SCOPE=api://your-scope/.default

# Azure Cognitive Search (REQUIRED)
AZURE_SEARCH_ENDPOINT=https://YOUR-SEARCH-SERVICE.search.windows.net
AZURE_SEARCH_API_KEY=YOUR-SEARCH-ADMIN-KEY

# v6 Index Names (use defaults or customize)
AZURE_SEARCH_SOP_INDEX_NAME=sop_chunks
AZURE_SEARCH_QUESTIONS_INDEX_NAME=psmart_questions
AZURE_SEARCH_MAPPINGS_INDEX_NAME=semantic_mappings
AZURE_SEARCH_QUESTIONS_INDEX_NAME=psmart_questions
```

### 2.3 How to Get Azure Credentials

**Azure OpenAI:**
1. Go to Azure Portal → Your Azure OpenAI resource
2. Click "Keys and Endpoint" in left menu
3. Copy:
   - **Endpoint** → `OPENAI_ENDPOINT`
   - **KEY 1** → `OPENAI_API_KEY`
4. Click "Model deployments" → Copy deployment names

**Azure Cognitive Search:**
1. Go to Azure Portal → Your Search service
2. Click "Keys" in left menu
3. Copy:
   - **URL** (at top) → `AZURE_SEARCH_ENDPOINT`
   - **Primary admin key** → `AZURE_SEARCH_API_KEY`

---

## Step 3: Create Azure Search Indices (15 min)

v6 requires **3 indices** to be created in Azure Cognitive Search:

| Index Name | Purpose |
|------------|---------|
| `sop_chunks` | SOP document chunks with vector embeddings |
| `psmart_questions` | Custom questions from Excel uploads |
| `semantic_mappings` | SOP capability → Question category mappings |

### 3.1 Run Index Creation Script

```powershell
# Create all 3 indices
python scripts/create_index.py
```

Expected output:
```
2026-04-15 10:30:45 - INFO - Creating SOP index 'sop_chunks'...
2026-04-15 10:30:47 - INFO - SOP index 'sop_chunks' created/updated successfully.
2026-04-15 10:30:47 - INFO - Creating custom questions index 'psmart_questions'...
2026-04-15 10:30:49 - INFO - Questions index 'psmart_questions' created/updated successfully.
2026-04-15 10:30:49 - INFO - Creating semantic mappings index 'semantic_mappings'...
2026-04-15 10:30:51 - INFO - Mappings index 'semantic_mappings' created/updated successfully.
```

### 3.2 Verify Indices Created

**Option A: Via Azure Portal**
1. Go to Azure Portal → Your Search service
2. Click "Indexes" in left menu
3. You should see:
   - ✅ `sop_chunks` (0 documents initially)
   - ✅ `psmart_questions` (0 documents initially)
   - ✅ `semantic_mappings` (0 documents initially)

**Option B: Via Script**
```powershell
# Python verification
python -c "from app.core.azure_search import AzureSearchClient; client = AzureSearchClient(); print('✅ Azure Search connection OK')"
```

### 3.3 Troubleshooting

**Error: "Authentication failed"**
- Double-check `AZURE_SEARCH_API_KEY` in `.env`
- Ensure you copied the **admin key**, not query key

**Error: "Could not resolve host"**
- Verify `AZURE_SEARCH_ENDPOINT` format: `https://NAME.search.windows.net`
- Check network/VPN connection if behind private endpoint

**Error: "Index already exists"**
- This is OK! Script uses `create_or_update_index`, so it updates existing indices

---

## Step 4: Start the Application (2 min)

### 4.1 Run FastAPI Server

```powershell
# Start server with auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process using WatchFiles
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### 4.2 Verify Server is Running

Open a **new terminal** and test:

```powershell
# Test health endpoint
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "indices": {
    "sop_chunks": {"indexed": true, "document_count": 0},
    "psmart_questions": {"indexed": true, "document_count": 0},
    "semantic_mappings": {"indexed": false, "mapping_count": 0}
  }
}
```

---

## Step 5: Access the Web UI (1 min)

### 5.1 Open Browser

Navigate to: **http://localhost:8000**

You should see the QuestionaireRAG v6 interface with three main sections:
1. **Data Upload Management** (for SOPs and Questions)
2. **SOW Query Interface** (for querying with SOW documents)
3. **Results Display** (shows retrieved questions with context)

---

## Step 6: Index Your Data (15-30 min)

Before you can query, you need to upload and index:
1. **SOP documents** (Standard Operating Procedures)
2. **Questions Excel** (your custom questionnaire)

### 6.1 Upload SOP Documents

**Via UI:**
1. Go to Section 1: "Data Upload Management"
2. Click "Upload SOPs"
3. Drag & drop or select your SOP files (PDF, Word, Excel)
4. Click "Index SOPs"
5. Wait for confirmation (progress bar)

**Via API:**
```powershell
# Upload multiple SOP files
curl -X POST http://localhost:8000/data/upload-sops `
  -F "files=@path/to/sop1.pdf" `
  -F "files=@path/to/sop2.docx" `
  -F "rebuild_index=true"
```

### 6.2 Upload Questions Excel

**Via UI:**
1. In Section 1, click "Upload Questions"
2. Select your questions Excel file
3. Click "Index Questions"
4. Wait for confirmation

**Via API:**
```powershell
# Upload questions Excel
curl -X POST http://localhost:8000/data/upload-questions `
  -F "file=@path/to/questions.xlsx" `
  -F "rebuild_index=true"
```

### 6.3 Verify Indexing Complete

```powershell
# Check health endpoint
curl http://localhost:8000/health
```

Should now show document counts > 0:
```json
{
  "status": "healthy",
  "indices": {
    "sop_chunks": {"indexed": true, "document_count": 145},
    "psmart_questions": {"indexed": true, "document_count": 263},
    "semantic_mappings": {"indexed": true, "mapping_count": 42}
  }
}
```

---

## Step 7: Run Your First Query! (2 min)

### 7.1 Via UI

1. Go to Section 2: "SOW Query Interface"
2. Upload one or more SOW (Statement of Work) documents
3. Configure settings:
   - **Top-N:** How many questions to return (e.g., 10)
   - **Ranking Mode:** SOW Priority (default) / Balanced / SOP Priority
   - **Include SOP chunks:** ☑️ (show supporting SOP context)
   - **Show Context:** ☑️ (show why questions matched)
4. Click "Search"
5. View results in Section 3

### 7.2 Via API

```powershell
# Query with SOW files
curl -X POST http://localhost:8000/query/search-with-sow `
  -F "sow_files=@path/to/sow.pdf" `
  -F "top_n=10" `
  -F "ranking_mode=sow_priority" `
  -F "include_context=true"
```

Response includes ranked questions with supporting context:
```json
{
  "status": "success",
  "ranked_questions": [
    {
      "rank": 1,
      "question_text": "Does the vendor provide SOC 2 Type II certification?",
      "category": "Compliance",
      "score": 0.94,
      "supporting_context": {
        "matched_sow_chunk": {
          "text": "Security compliance requirements include...",
          "score": 0.92
        },
        "matched_sop_chunk": {
          "text": "Our certification policy requires...",
          "capability": "Compliance_Management"
        }
      }
    }
  ]
}
```

---

## Step 8: Validation Checklist

- [ ] All dependencies installed successfully
- [ ] `.env` file configured with valid Azure credentials
- [ ] 3 Azure Search indices created (`sop_chunks`, `psmart_questions`, `semantic_mappings`)
- [ ] FastAPI server starts without errors
- [ ] Health endpoint returns `"status": "healthy"`
- [ ] SOPs uploaded and indexed (document_count > 0)
- [ ] Questions uploaded and indexed (document_count > 0)
- [ ] Query with SOW returns ranked questions
- [ ] Results include supporting context and reasoning
- [ ] UI loads at http://localhost:8000

---

## Troubleshooting Common Issues

### Server won't start

**Error: "Port 8000 is already in use"**
```powershell
# Find what's using port 8000
netstat -ano | findstr :8000

# Kill the process (replace PID)
taskkill /PID <PID> /F

# Or use a different port
uvicorn main:app --reload --port 8001
```

**Error: "ModuleNotFoundError: No module named 'app'"**
```powershell
# Ensure you're in the repo root directory
cd C:\Users\allada.chinmai\OneDrive - Providence St. Joseph Health\Documents\Repos\QuestionaireRAG

# Verify structure
dir app\core
```

### Azure connection issues

**Error: "Failed to connect to Azure Search"**
- Verify endpoint URL format: `https://NAME.search.windows.net`
- Check API key is the admin key (not query key)
- Ensure firewall rules allow your IP (if using private endpoint)

**Error: "OpenAI API authentication failed"**
- Verify `OPENAI_API_KEY` is correct
- Check deployment names match what's in Azure Portal
- Ensure resource has quota available

### Query returns no results

- Verify indices have documents: `curl http://localhost:8000/health`
- Check SOW file format is supported (PDF, Word, Excel)
- Try with `ranking_mode=balanced` or `sop_priority`
- Check server logs for errors: look at terminal where `uvicorn` is running

---

## Next Steps

### Production Deployment

For production deployment to Azure App Service, Azure Container Instances, or Kubernetes, see:
- [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)

### Advanced Configuration

- **Adjust ranking weights:** Edit `app/core/retriever.py`
- **Customize chunking:** Edit `app/core/ingestor.py` (token sizes, overlap)
- **Tune vector search:** Edit HNSW parameters in `scripts/create_index.py`

### Monitoring

- Review logs in terminal where server is running
- Check Azure Search service metrics in Portal
- Monitor token usage in Azure OpenAI resource

---

## Quick Reference Commands

```powershell
# Start server
uvicorn main:app --reload --port 8000

# Create indices
python scripts/create_index.py

# Run tests
pytest tests/

# Check health
curl http://localhost:8000/health

# Re-index (if schema changes)
python scripts/create_index.py ; curl -X POST http://localhost:8000/data/upload-sops -F "files=@sops/*"
```

---

## Support

For issues or questions:
1. Check [docs/VALIDATION_CHECKLIST.md](docs/VALIDATION_CHECKLIST.md)
2. Review version documentation: [docs/versions/v6.md](docs/versions/v6.md)
3. Check application logs in terminal

---

**🎉 You're all set! Your QuestionaireRAG v6 app is ready to use.**
