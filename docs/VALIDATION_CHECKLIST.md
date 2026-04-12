# Azure Cognitive Search Migration - Validation Checklist

This document provides a step-by-step validation checklist for the migration from Chroma DB + BM25 to Azure Cognitive Search with hybrid indexing.

## Pre-Deployment Validation

### ✓ Phase 1: Azure Infrastructure Setup

- [ ] **Azure Subscription**: Verify access to Azure subscription and billing is enabled
- [ ] **Azure Cognitive Search Service**: Service created and accessible
- [ ] **Service Tier**: Verified tier supports semantic ranking (Standard or higher recommended)
- [ ] **Credentials Obtained**:
  - [ ] AZURE_SEARCH_ENDPOINT (e.g., `https://myservice.search.windows.net`)
  - [ ] AZURE_SEARCH_API_KEY (Admin key)
  - [ ] Secondary API key for production (optional)
- [ ] **Network Connectivity**: Verified connection from development environment to Azure service
- [ ] **.env Updated**: Added credentials to `.env` file
  ```
  AZURE_SEARCH_ENDPOINT=https://your-service.search.windows.net
  AZURE_SEARCH_API_KEY=your-admin-key
  AZURE_SEARCH_CAIQ_INDEX_NAME=caiq_questions
  ```

### ✓ Phase 2: Index Creation

- [ ] **CAIQ Index Created**: `caiq_questions` index exists in Azure CS
  - [ ] Correct schema with 1536-dim vector field
  - [ ] HNSW algorithm configured
  - [ ] Semantic ranking enabled
  - [ ] BM25 analyzer configured
  
- [ ] **Verify Index Schema** via Azure Portal or REST API:
  ```bash
  curl -X GET "https://{service-name}.search.windows.net/indexes/caiq_questions?api-version=2024-07-01" \
    -H "api-key: {admin-key}"
  ```

- [ ] **Index is Empty**: Ready to receive CAIQ data migration

### ✓ Phase 3: Dependencies Installation

- [ ] **Virtual Environment**: Python 3.9+ environment created
- [ ] **Dependencies Installed**: Run `pip install -r requirements.txt`
  - [ ] Verify `azure-search-documents>=11.4.0` installed
  - [ ] Verify `azure-identity>=1.12.0` installed
  
- [ ] **Optional Deps**: Chroma and rank-bm25 still installed (for comparison testing)

- [ ] **Import Test**: Python imports verified
  ```bash
  python -c "from app.core.azure_search import AzureSearchClient; print('✓ Imports OK')"
  ```

## Data Migration Validation

### ✓ Phase 4: CAIQ Data Migration

- [ ] **Existing Data Preserved**: 
  - [ ] `data/questions_store.json` exists with full question metadata
  - [ ] Chroma DB still has vectors (as backup during migration)
  - [ ] BM25 index still persisted

- [ ] **Pre-migration Snapshot**: Document baseline metrics
  - Total questions in XLSX: _______
  - Embeddings dimensions: 1536
  - Embedding model: text-embedding-ada-002

- [ ] **Run Migration Script**:
  ```bash
  python scripts/migrate_caiq_to_azure.py
  ```
  - [ ] Script completes successfully
  - [ ] All questions indexed: ______ / ______ (succeeded / total)
  - [ ] Failed documents logged (if any): _______________________
  - [ ] Validation passed: Test search returned results

- [ ] **Post-migration Verification**:
  - [ ] Query Azure CS for 5 known questions
  - [ ] Scores returned (not empty, > 0)
  - [ ] Results match question_ids from XLSX

### ✓ Phase 5: Data Integrity Tests

- [ ] **CAIQ Field Validation**:
  ```bash
  python scripts/validate_azure_index.py  # (create this script if needed)
  ```
  - [ ] All question_ids present
  - [ ] All domains present (IAM, DATA, etc.)
  - [ ] Vector dimensions correct (1536)
  - [ ] No corrupted embeddings (NaN, Inf)

- [ ] **Sample Query Validation** (manual testing):
  - [ ] Query domain: "IAM" → results are IAM domain
  - [ ] Query domain: "DATA" → results are DATA domain
  - [ ] Free-text query: "encryption" → relevant results
  - [ ] Free-text query: "compliance" → relevant results

## API Endpoint Validation

### ✓ Phase 6: Health Check Endpoint

- [ ] **Start Server**:
  ```bash
  uvicorn main:app --reload --port 8000
  ```

- [ ] **Health Endpoint** (`GET /health`):
  ```bash
  curl http://localhost:8000/health
  ```
  Expected response:
  ```json
  {
    "status": "ok",
    "index_built": false  // Should be false before indexing
  }
  ```

### ✓ Phase 7: Indexing Endpoint

- [ ] **Index Questionnaires** (`POST /index/questionnaires`):
  ```bash
  curl -X POST http://localhost:8000/index/questionnaires
  ```
  Expected response:
  ```json
  {
    "message": "Index built successfully in Azure Cognitive Search",
    "questions_indexed": ___  // Total count
  }
  ```

- [ ] **Health Check After Indexing**:
  ```bash
  curl http://localhost:8000/health
  ```
  Expected: `"index_built": true`

- [ ] **Performance Metrics**:
  - Total indexing time: ________ seconds
  - Questions indexed per second: ________

### ✓ Phase 8: Query Endpoint

- [ ] **Test Customer Exists**: `data/customers/customer_1/` directory with PDFs

- [ ] **Query Endpoint** (`POST /query`):
  ```bash
  curl -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{
      "customer_id": "customer_1",
      "top_k": 10,
      "use_semantic_ranking": true
    }'
  ```

- [ ] **Response Validation**:
  - [ ] HTTP 200 status
  - [ ] `customer_id` matches request
  - [ ] `context_summary` populated (non-empty string)
  - [ ] `total_results` > 0
  - [ ] `questions` array has expected structure:
    - [ ] `rank` field (1, 2, 3, ...)
    - [ ] `question_id` field (e.g., "IAM-01.1")
    - [ ] `domain` field (e.g., "IAM")
    - [ ] `question` field (full text)
    - [ ] `score` field (numeric, > 0)
    - [ ] `semantic_score` field (if semantic ranking enabled)
    - [ ] `source` field (e.g., "CAIQ")

- [ ] **Query Performance**:
  - End-to-end latency: ________ seconds (target: < 2s)
  - Breakdown:
    - Document loading: _________ ms
    - Summarization: _________ ms
    - Embedding: _________ ms
    - Azure CS search: _________ ms
    - Enrichment: _________ ms

### ✓ Phase 9: Semantic Ranking Validation

- [ ] **With Semantic Ranking** (`use_semantic_ranking: true`):
  - [ ] Response includes `semantic_score` in results
  - [ ] Semantic scores between 0 and 1
  - [ ] Semantic scores generally > 0.5 for top results

- [ ] **Without Semantic Ranking** (`use_semantic_ranking: false`):
  - [ ] Response does not include `semantic_score`
  - [ ] Query latency lower than with ranking

- [ ] **Score Comparison**:
  - Top result's hybrid score: ________
  - Top result's semantic score: ________
  - Scores correlate well?  [ ] Yes  [ ] No (investigate)

## Hybrid Search Validation

### ✓ Phase 10: Vector + BM25 Coverage

- [ ] **Vector Search Bias** (query with semantic understanding):
  - Query: "What are identity and access management controls?"
  - Results should include IAM questions
  
- [ ] **Keyword Search Bias** (query with exact keywords):
  - Query: "firewall network encryption"
  - Results should include network/security questions

- [ ] **Hybrid Benefit**:
  - Term "compliance" returns compliance questions ✓
  - Term "audit" returns audit questions ✓
  - Semantic query returns semantically similar concepts ✓

## Comparison Validation (Chroma vs Azure CS)

### ✓ Phase 11: Baseline Comparison

- [ ] **Prepare Test Queries**: 5-10 representative queries
  - Query 1: ___________________________
  - Query 2: ___________________________
  - Query 3: ___________________________
  - etc.

- [ ] **Run Queries Against Both Systems**:
  
  **Chroma + BM25 (Legacy)**:
  - Modify retriever.py temporarily to use Chroma
  - Record top-5 results and RRF scores
  
  **Azure CS (New)**:
  - Use updated retriever.py
  - Record top-5 results and hybrid scores

- [ ] **Compare Results**:
  - [ ] Top results are quality? (Human review)
  - [ ] Azure CS results > Chroma results? [ ] Yes [ ] No
  - [ ] Relevance improved? [ ] Yes [ ] No
  - [ ] False positives reduced? [ ] Yes [ ] No

- [ ] **Score Distribution**:
  - Chroma RRF scores average: ________
  - Azure CS hybrid scores average: ________
  - Azure semantic scores average: ________

## Multi-Tenant Validation

### ✓ Phase 12: Customer Isolation

- [ ] **Test Multiple Customers**:
  - [ ] Query customer_1: Results appropriate for customer_1
  - [ ] Query customer_2: Results appropriate for customer_2 (different questions returned)
  - [ ] Verify no cross-contamination in results

- [ ] **Data Isolation Tests**:
  - If customer_docs indexes are used, verify customer_id filter works:
    - [ ] Query returns only customer_1 docs for customer_1
    - [ ] Query returns only customer_2 docs for customer_2

## Production Readiness

### ✓ Phase 13: Logging & Monitoring

- [ ] **Logs Verification**:
  - [ ] Startup logs show "✓ Azure Cognitive Search connection verified"
  - [ ] Query logs include timestamps and performance metrics
  - [ ] Error logs capture failures with full context

- [ ] **Log Levels**: Check that appropriate levels are used
  - [ ] DEBUG for detailed operations
  - [ ] INFO for major steps (indexing complete, queries)
  - [ ] WARNING for degraded states (network issues, partial failures)
  - [ ] ERROR for failures that stop processing

### ✓ Phase 14: Error Handling

- [ ] **Azure Service Unavailable**:
  - Stop Azure Search service temporarily
  - Verify /health returns False (not 500 error)
  - Verify /query returns meaningful error message

- [ ] **Invalid Customer**:
  - Query with non-existent customer_id
  - Verify 404 error with message

- [ ] **Invalid Credentials**:
  - Modify .env with wrong API key
  - Restart server
  - Verify startup warning/error logged

### ✓ Phase 15: Cost Monitoring

- [ ] **Azure Portal Cost Analysis**:
  - [ ] Monitor search unit charges (baseline: $250+/month for Standard tier)
  - [ ] Monitor query volume and cost
  - [ ] Set up alerts for unexpected charges

- [ ] **Estimated Monthly Cost**:
  - Base tier cost: $__________
  - Semantic ranking: +$__________
  - Storage: +$__________
  - **Total: $__________**

## Deprecation & Cleanup

### ✓ Phase 16: Legacy System Deprecation

- [ ] **Decision**: Keep or remove Chroma DB files?
  - [ ] Keep as backup: (checkpoint data directory)
  - [ ] Remove after final validation: (delete data/chroma_db, data/bm25_index.pkl)

- [ ] **Notification**: Inform team that Chroma/BM25 are deprecated

- [ ] **Codebase Cleanup**: In next sprint
  - Update code comments to reflect Azure CS
  - Remove deprecated function implementations (just keep deprecation warnings)

### ✓ Phase 17: Documentation Updates

- [ ] **Update README**: Migration complete, now using Azure CS
- [ ] **Architecture Diagram**: Updated to show Azure CS
- [ ] **API Documentation**: Updated to reflect new score fields
- [ ] **Deployment Guide**: Updated with Azure credentials setup

## Final Gate

### ✓ Phase 18: Sign-Off Checklist

Before deploying to production:

- [ ] All phases 1-17 completed and validated
- [ ] Team review and approval obtained
- [ ] Backup of existing Chroma data taken
- [ ] Rollback plan documented (if failures occur)
- [ ] Monitoring and alerting configured
- [ ] Cost budget approved
- [ ] SLA/performance targets documented
- [ ] Data retention policies reviewed

## Post-Deployment Monitoring

### Ongoing (First 2 Weeks)

- [ ] Daily check: `/health` endpoint returns true
- [ ] Daily check: Query latency < 2s
- [ ] Daily check: No spike in error rates
- [ ] Daily check: Semantic ranking working as expected
- [ ] Weekly: Review logs for warnings/errors
- [ ] Weekly: Monitor Azure CS costs

### Monthly (Ongoing)

- [ ] Review query performance metrics
- [ ] Validate customer isolation still working
- [ ] Check for any data corruption or index issues
- [ ] Update documentation based on learnings
- [ ] Consider index optimization if needed

---

## Troubleshooting Guide

### Issue: Azure CS Connection Fails at Startup

**Solution**:
1. Verify credentials in `.env` are correct
2. Check network connectivity to Azure service
3. Verify service is running (Azure Portal)
4. Restart application

### Issue: Indexing Fails with Dimension Mismatch

**Solution**:
1. Verify embeddings are 1536-dimensional (ada-002 output)
2. Check that all vectors in batch have same shape
3. Ensure no NaN or Inf values in vectors

### Issue: Query Returns No Results

**Solution**:
1. Verify CAIQ data was successfully indexed (check Azure Portal)
2. Verify `questions_store.json` exists and is not empty
3. Run health check to verify Azure CS connection
4. Check server logs for error messages

### Issue: Semantic Ranking Not Improving Results

**Solution**:
1. Verify semantic ranking is enabled in index config (Azure Portal)
2. Ensure `use_semantic_ranking=True` in API request
3. Compare scores with/without semantic ranking
4. May need to tune Azure CS semantic configuration

---

## Approvals & Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Developer | _________________ | _____ | _________ |
| QA Lead | _________________ | _____ | _________ |
| DevOps/Cloud Architect | _________________ | _____ | _________ |
| Project Manager | _________________ | _____ | _________ |

---

**Migration Status**: [ ] In Progress  [ ] Validated  [ ] Deployed  [ ] Complete

**Final Sign-Off Date**: _______________

**Notes**: ________________________________________________________________

