"""
GPT-4o based re-ranking for question relevance scoring.
"""

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional
from azure.identity import ClientSecretCredential
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

# Lazy singletons — initialized on first call (same pattern as embedder.py)
_openai_client = None
_credential = None
_token_cache = {"token": None, "expires_at": 0}


def _get_credential() -> ClientSecretCredential:
    """Get or create Azure AD credential."""
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=os.environ["AUTH_TENANT_ID"],
            client_id=os.environ["AUTH_CLIENT_ID"],
            client_secret=os.environ["AUTH_CLIENT_SECRET"]
        )
    return _credential


def _get_token() -> str:
    """Get a valid Azure AD token, refreshing if necessary."""
    global _token_cache
    
    # Check if we have a cached token that's still valid (with 5 min buffer)
    if _token_cache["token"] and time.time() < (_token_cache["expires_at"] - 300):
        return _token_cache["token"]
    
    # Get a new token
    try:
        credential = _get_credential()
        token_result = credential.get_token(os.environ["AUTH_SCOPE"])
        
        _token_cache["token"] = token_result.token
        _token_cache["expires_at"] = token_result.expires_on
        
        logger.info("✓ Got new Azure AD token for reranker")
        return token_result.token
    except Exception as e:
        logger.error(f"❌ Failed to get Azure AD token: {e}")
        raise


def _get_openai_client() -> AzureOpenAI:
    """
    Return (or create) the shared Azure OpenAI client.
    Uses same auth pattern as embedder.py.
    """
    global _openai_client
    
    # Recreate client if it doesn't exist OR token is expiring soon
    if _openai_client is None or time.time() >= (_token_cache["expires_at"] - 300):
        token = _get_token()
        
        _openai_client = AzureOpenAI(
            azure_endpoint=os.environ["OPENAI_ENDPOINT"],
            api_key=os.environ["OPENAI_API_KEY"],
            api_version="2024-08-01-preview",
            default_headers={
                "Authorization": f"Bearer {token}"
            }
        )
        logger.info("Azure OpenAI client ready for reranking")
    return _openai_client


def rerank_questions_with_gpt4o(
    candidates: List[Dict],
    sow_context: str,
    sop_contexts: List[str],
    top_n: int = 20,
    batch_size: int = 10
) -> List[Dict]:
    """
    Re-rank candidate questions using GPT-4o for deep semantic analysis.
    
    Args:
        candidates: List of question candidates with initial scores
        sow_context: Combined SOW chunks context
        sop_contexts: List of relevant SOP chunk texts
        top_n: Number of top results to return
        batch_size: Number of questions to evaluate in parallel
        
    Returns:
        List of re-ranked questions with GPT-4o scores and explanations
    """
    logger.info(f"Re-ranking {len(candidates)} candidates with GPT-4o...")
    
    # Combine SOP context
    sop_text = "\n\n".join(sop_contexts[:3]) if sop_contexts else "No SOP context available"
    
    # Truncate contexts to fit in prompt
    sow_context = sow_context[:2000]
    sop_text = sop_text[:2000]
    
    reranked = []
    
    # Process in batches
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        
        try:
            scored_batch = _score_batch(batch, sow_context, sop_text)
            reranked.extend(scored_batch)
        except Exception as e:
            logger.error(f"Error scoring batch {i//batch_size + 1}: {e}")
            # Fallback: keep original scores
            for candidate in batch:
                candidate["gpt4o_score"] = candidate.get("score", 0.0) * 10
                candidate["explanation"] = "Scoring failed - using vector search score"
            reranked.extend(batch)
    
    # Sort by GPT-4o score descending
    reranked.sort(key=lambda x: x.get("gpt4o_score", 0), reverse=True)
    
    # Update ranks
    for rank, item in enumerate(reranked[:top_n], start=1):
        item["rank"] = rank
        item["score"] = item.get("gpt4o_score", 0.0)
    
    logger.info(f"Re-ranking complete. Top score: {reranked[0].get('gpt4o_score', 0):.2f}")
    
    return reranked[:top_n]


def _score_batch(
    batch: List[Dict],
    sow_context: str,
    sop_context: str
) -> List[Dict]:
    """
    Score a batch of questions using GPT-4o.
    
    Args:
        batch: List of question candidates
        sow_context: SOW context text
        sop_context: SOP context text
        
    Returns:
        Batch with added GPT-4o scores and explanations
    """
    # Build prompt with all questions in batch
    questions_text = "\n".join([
        f"{i+1}. [{q['question_id']}] {q['question']}"
        for i, q in enumerate(batch)
    ])
    
    prompt = f"""You are a security assessment expert analyzing Statement of Work (SOW) documents.

**SOW Context:**
{sow_context}

**Relevant SOP Context:**
{sop_context}

**Questions to Evaluate:**
{questions_text}

**Task:**
For each question above, rate its relevance to the SOW/SOP context on a scale of 0-10, where:
- 0-3: Low relevance (question not applicable to this engagement)
- 4-6: Moderate relevance (question somewhat related but not critical)
- 7-8: High relevance (question directly applicable and important)
- 9-10: Critical relevance (question is essential for this specific engagement)

**Output Format (JSON):**
Return a JSON object with a "results" key containing an array of scores:
{{
  "results": [
    {{
      "question_id": "QUESTION_ID_001",
      "score": 8,
      "explanation": "Brief 1-2 sentence explanation of why this score"
    }},
    ...
  ]
}}

Focus on:
1. Direct alignment with stated SOW requirements
2. Relevance to technical capabilities mentioned
3. Importance for risk assessment
4. Applicability to the engagement scope

Return ONLY valid JSON, no other text."""

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=os.environ["OPENAI_DEPLOYMENT_NAME"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2000,
            response_format={"type": "json_object"}
        )
        
        result_text = response.choices[0].message.content
        
        # Debug: Log raw GPT-4o response
        logger.info(f"GPT-4o raw response (first 500 chars): {result_text[:500]}")
        
        # Parse JSON response
        scores_data = json.loads(result_text)
        
        # Debug logging
        logger.info(f"GPT-4o response parsed. Keys: {scores_data.keys() if isinstance(scores_data, dict) else 'not a dict'}")
        
        # Handle both "results" and "scores" keys for backward compatibility
        if isinstance(scores_data, dict):
            scores_list = scores_data.get("results") or scores_data.get("scores") or []
        elif isinstance(scores_data, list):
            scores_list = scores_data
        else:
            scores_list = []
        
        logger.info(f"Processing {len(scores_list)} scored questions from GPT-4o")
        
        # Debug: Show first result details
        if scores_list:
            first = scores_list[0]
            logger.info(f"First GPT-4o result sample: {first}")
        
        # Map scores back to batch
        score_map = {item["question_id"]: item for item in scores_list}
        
        for candidate in batch:
            qid = candidate["question_id"]
            if qid in score_map:
                candidate["gpt4o_score"] = score_map[qid].get("score", 0)
                explanation = score_map[qid].get("explanation", "")
                candidate["explanation"] = explanation if explanation else "GPT-4o scored this question but did not provide explanation"
                logger.info(f"Q {qid}: gpt4o_score={candidate['gpt4o_score']}, has_explanation={bool(explanation)}, explanation='{explanation[:100] if explanation else 'NONE'}'")
            else:
                # Fallback if question not in response
                candidate["gpt4o_score"] = candidate.get("score", 0.0) * 10
                candidate["explanation"] = "No GPT-4o score available"
                logger.warning(f"Q {qid}: not found in GPT-4o response")
        
        return batch
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GPT-4o JSON response: {e}")
        raise
    except Exception as e:
        logger.error(f"GPT-4o API call failed: {e}")
        raise


def generate_explanation(
    question: str,
    sow_context: str,
    sop_context: str
) -> str:
    """
    Generate a detailed explanation for why a question is relevant.
    
    Args:
        question: The question text
        sow_context: SOW context
        sop_context: SOP context
        
    Returns:
        Explanation string
    """
    prompt = f"""Explain why this security question is relevant to the given SOW context.

**Question:** {question}

**SOW Context:** {sow_context[:1000]}

**SOP Context:** {sop_context[:1000]}

Provide a 1-2 sentence explanation focusing on specific SOW requirements or technical capabilities mentioned."""

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=os.environ["OPENAI_DEPLOYMENT_NAME"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logger.error(f"Failed to generate explanation: {e}")
        return "Explanation not available"


def analyze_sow_requirements(sow_chunks: List[str]) -> str:
    """
    Analyze SOW chunks to extract key requirements and themes.
    
    Args:
        sow_chunks: List of SOW text chunks
        
    Returns:
        Summary of key requirements
    """
    combined_text = "\n\n".join(sow_chunks[:5])[:4000]
    
    prompt = f"""Analyze this Statement of Work and extract the key requirements, technical capabilities, and focus areas.

**SOW Text:**
{combined_text}

**Task:**
Provide a concise summary (3-5 bullet points) of:
1. Main technical capabilities or services required
2. Key security or compliance concerns
3. Critical operational requirements

Format as bullet points."""

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=os.environ["OPENAI_DEPLOYMENT_NAME"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300
        )
        
        summary = response.choices[0].message.content.strip()
        logger.info(f"SOW analysis complete: {summary[:100]}...")
        return summary
        
    except Exception as e:
        logger.error(f"Failed to analyze SOW: {e}")
        return "Analysis not available"
