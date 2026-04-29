# glassbox/rag

Governance helpers for retrieval-augmented generation flows.

## Key Modules

- `governance.py`: query governance, retrieval governance, and orchestrated RAG flow control

## Quick Start

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.rag.governance import RAGQueryGovernor, RAGRetrievalGovernor, AgenticRAGOrchestrator

pipeline = GovernancePipeline()
query_gov = RAGQueryGovernor(allowed_topics=["compliance"])
retrieval_gov = RAGRetrievalGovernor(min_relevance=0.5)

# rag = AgenticRAGOrchestrator(pipeline, query_gov, retrieval_gov, retriever_fn=my_retriever)
```

## Operational Notes

- Gate queries before retrieval to reduce injection and out-of-scope requests.
- Validate retrieval chunks for relevance, freshness, and source trust.
- Keep final action generation under main governance pipeline checks.

## Testing

```bash
python -m pytest tests/test_framework.py -q
python -m pytest tests/test_regression.py -q
```

## Related Docs

- [docs/DEVELOPMENT/architecture.md](../../docs/DEVELOPMENT/architecture.md)
- [docs/USER/use_cases.md](../../docs/USER/use_cases.md)