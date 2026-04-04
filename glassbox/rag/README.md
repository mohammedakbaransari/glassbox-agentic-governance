# glassbox/rag — RAG & Agentic RAG Governance

The `rag` package governs the Retrieval-Augmented Generation pipeline at three points.

| Module | Role |
|---|---|
| `governance.py` | `RAGQueryGovernor`, `RAGRetrievalGovernor`, `AgenticRAGOrchestrator` |

**Interception points:**
1. **Query governance** — validate query before retrieval (injection, scope, length)
2. **Retrieval governance** — validate chunks before agent sees them (source, relevance, freshness, PII)
3. **Action governance** — govern the act step through the main pipeline

---

## Quick Start

```python
from glassbox.rag.governance import (
    RAGQueryGovernor,
    RAGRetrievalGovernor,
    AgenticRAGOrchestrator
)
from glassbox.governance.pipeline import GovernancePipeline

# 1. Query validation
query_gov = RAGQueryGovernor(
    allowed_topics=["clinical", "drug"],
    max_query_length=500,
    prohibited_patterns=["DROP TABLE", "DELETE FROM"]
)

# 2. Retrieval validation  
retrieval_gov = RAGRetrievalGovernor(
    source_registry=["medical_db", "fda_approved_list"],  # Trusted sources
    min_relevance_score=0.75,
    max_chunk_age_seconds=86400  # Chunks < 24 hours old
)

# 3. Execution governance (full pipeline)
pipeline = GovernancePipeline(environment="production")

# 4. Setup orchestrator
rag = AgenticRAGOrchestrator(
    pipeline=pipeline,
    query_governor=query_gov,
    retrieval_governor=retrieval_gov,
    retriever_fn=knowledge_base.search
)

# 5. Run query
result = rag.run(
    agent_id="clinical_copilot",
    initial_query="what is the maximum dosage for morphine?",
    action_fn=lambda context: generate_prescription_draft(context)
)

print(f"Final decision: {result.disposition}")
print(f"Retrieved chunks: {result.retrieved_chunks}")
print(f"Violations: {result.violations}")
```

---

## Governance Stages in RAG Pipeline

### Stage 1: Query Governance

```python
query_gov = RAGQueryGovernor(
    allowed_topics=["clinical", "drug", "dosage"],
    prohibited_topics=["financial", "employee_data"],
    max_query_length=500,
    prohibited_patterns=[
        "DROP TABLE",      # SQL injection
        "{{ system_prompt }}",  # Prompt injection
        "__import__"       # Code injection
    ]
)

# Validates before retrieval
if query_gov.is_valid(query="SELECT morphine dosage FROM patients"):
    # Caught SQL injection attempt
    raise SecurityViolation("Query contains SQL injection pattern")

if query_gov.is_valid(query="morphine max dose"):
    # Safe; proceed to retrieval
    pass
```

### Stage 2: Retrieval Governance

```python
retrieval_gov = RAGRetrievalGovernor(
    source_registry={
        "medical_db": {"trusted": True, "pii_sensitive": True},
        "public_wiki": {"trusted": False, "pii_sensitive": False},
    },
    min_relevance_score=0.75,
    max_chunk_age_seconds=604800,  # 7 days
    pii_masking_enabled=True
)

# Validates chunks before LLM sees them
chunks = retriever.search("morphine", top_k=5)
# chunks = [
#   {"text": "...", "source": "medical_db", "relevance": 0.92, "age_sec": 3600},
#   {"text": "...", "source": "public_wiki", "relevance": 0.45, "age_sec": 2592000},
# ]

validated = retrieval_gov.validate(chunks)
# Returns only trusted, relevant, fresh chunks
```

### Stage 3: Action Governance

```python
# Full decision governance for the LLM's action/decision
action_fn = lambda context: generate_prescription(context)

result = rag.run(
    agent_id="clinical_copilot",
    initial_query="morphine dosage",
    action_fn=action_fn
)

# The LLM's output (prescription) goes through full governance:
# - Policy checks (dosage limits, interactions)
# - Anomaly detection (unusual dosage pattern)
# - Audit logging
# - Potential escalation to human review
```

---

## Performance Characteristics

| Operation | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| query_validation() | 1–5 ms | 200–1K queries/sec | Rules-based checks |
| retriever.search() | 50–200 ms | — | Vector / full-text search |
| retrieval_validation() | 5–20 ms | — | 5–10 chunks validated |
| full RAG pipeline | 100–500 ms | 2–10 queries/sec | Query + retrieval + LLM + governance |
| Agentic loop (N=5) | 500ms–2s | — | 5 iterations of query+act+validate |

**Optimization:**
```python
# Cache validated chunks to avoid repeated retrieval
from functools import lru_cache

@lru_cache(maxsize=1000)
def cached_retrieval(query):
    return retrieval_gov.validate(retriever.search(query))

# Batch queries for throughput
results = [rag.run(agent_id, q, action_fn) for q in batch_of_queries]
```

---

## Common Errors

### Error: "Query injection detected; query blocked"

**Symptom:**
```python
result = rag.run(
    agent_id="copilot",
    initial_query="SELECT * FROM patients WHERE id=1; DROP TABLE patients;--",
    action_fn=generate_response
)
# Blocked: SQL injection pattern detected
```

**Solution:**
```python
# Option 1: Sanitize input
from glassbox.security.sanitizer import sanitize_query
safe_query = sanitize_query(user_input)
result = rag.run(agent_id, safe_query, action_fn)

# Option 2: Allow only whitelisted query patterns
query_gov = RAGQueryGovernor(
    allowed_patterns=["^[a-zA-Z ]+$"],  # Only letters and spaces
    max_query_length=100
)

# Option 3: Log for monitoring
if "DROP TABLE" in query:
    log_suspicious_query(agent_id, query)
```

### Error: "Retrieved chunk is stale; source not current"

**Symptom:**
```
Chunk timestamps: [1 hour old, 3 days old, 2 weeks old]
Validation failed: max_chunk_age_seconds=86400 (1 day)
Blocking LLM from seeing 14-day-old chunk
```

**Cause:** Knowledge base not recently updated; outdated information

**Solution:**
```python
# Option 1: Reduce freshness requirement (if acceptable)
retrieval_gov = RAGRetrievalGovernor(
    max_chunk_age_seconds=604800  # 7 days instead of 1 day
)

# Option 2: Trigger knowledge base refresh
if len(stale_chunks) > 0:
    knowledge_base.refresh_from_source()
    chunks = retriever.search(query)  # Retry after refresh

# Option 3: Alert ops to update knowledge base
alert_ops(f"Knowledge base stale: {stale_chunk_count} chunks > 1 days old")
```

### Error: "Retrieval score below threshold; no valid chunks"

**Symptom:**
```
Retriever found 5 chunks, but all scored < 0.75 (min_relevance_score)
No chunks passed validation
LLM has no context to answer question
```

**Cause:** Query not well-matched to knowledge base; poor retrieval

**Solution:**
```python
# Option 1: Reword query
result = rag.run(
    agent_id="copilot",
    initial_query="What is the recommended morphine dose for severe pain?",  # More specific
    action_fn=action_fn
)

# Option 2: Lower relevance threshold (if quality acceptable)
retrieval_gov = RAGRetrievalGovernor(
    min_relevance_score=0.5  # Instead of 0.75
)

# Option 3: Return "no relevant information" instead of hallucinating
if len(validated_chunks) == 0:
    return {"disposition": "no_data", "message": "No relevant information in knowledge base"}
```

### Error: "PII detected in retrieved chunk; masking applied"

**Symptom:**
```
Chunk contains: "Patient John Smith (DOB: 1980-01-15, SSN: 123-45-6789)"
Governance flagged as PII-sensitive
Masked chunk sent to LLM: "Patient [REDACTED] (DOB: [REDACTED], SSN: [REDACTED])"
```

**Cause:** Knowledge base contains personally identifiable information

**Solution:**
```python
# Option 1: Ensure PII masking enabled for sensitive sources
retrieval_gov = RAGRetrievalGovernor(
    source_registry={
        "patient_db": {"pii_sensitive": True},  # Enable masking
        "public_faq": {"pii_sensitive": False}
    },
    pii_masking_enabled=True
)

# Option 2: Separate PII-sensitive and public sources
public_chunks = retriever.search(query, source_filter="public_faq")
# PII-sensitive sources only used after additional approval

# Option 3: Log PII exposure for audit
if pii_detected_in(chunk):
    audit_log(f"PII in chunk from {chunk['source']}; masked before LLM")
```

### Error: "Agentic loop exceeded max iterations; hallucination risk"

**Symptom:**
```
Iteration 1: Query → Retrieve → Act → Another question needed
Iteration 2: Query → Retrieve → Act → Another question needed  
Iteration 3: Query → Retrieve → Act → Another question needed
...
Max iterations (5) reached; stopping to prevent hallucination spiral
```

**Cause:** LLM stuck in loop; model cannot answer with available data

**Solution:**
```python
# Option 1: Increase iteration limit (carefully)
result = rag.run(
    agent_id="copilot",
    initial_query=query,
    action_fn=action_fn,
    max_iterations=10  # Instead of 5
)

# Option 2: Break loop early on confidence
if llm_confidence_score < 0.6:
    return {"disposition": "uncertain", "confidence": llm_confidence_score}

# Option 3: Escalate to human
if iterations >= max_iterations:
    route_to_human_review(agent_id, query, intermediate_results)
```

---

## Source Registry Pattern

```python
source_registry = {
    "fda_approved": {
        "trusted": True,
        "authority": "US FDA",
        "pii_sensitive": False,
        "update_frequency": "daily"
    },
    "clinical_trials": {
        "trusted": True,
        "authority": "NIH",
        "pii_sensitive": True,  # Patient data
        "update_frequency": "weekly"
    },
    "vendor_docs": {
        "trusted": False,
        "authority": "Vendor-provided",
        "pii_sensitive": False,
        "requires_approval": True
    },
}

retrieval_gov = RAGRetrievalGovernor(
    source_registry=source_registry,
    min_relevance_score=0.75
)
```

---

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#rag-pipeline) for technical details and [../../docs/USECASES.md](../../docs/USECASES.md) Pattern 9 for retrieval-augmented generation examples.
