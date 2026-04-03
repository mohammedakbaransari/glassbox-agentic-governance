# glassbox/rag — RAG & Agentic RAG Governance

The `rag` package governs the Retrieval-Augmented Generation pipeline at three points.

| Module | Role |
|---|---|
| `governance.py` | `RAGQueryGovernor`, `RAGRetrievalGovernor`, `AgenticRAGOrchestrator` |

**Interception points:**
1. **Query governance** — validate query before retrieval (injection, scope, length)
2. **Retrieval governance** — validate chunks before agent sees them (source, relevance, freshness, PII)
3. **Action governance** — govern the act step through the main pipeline

```python
query_gov     = RAGQueryGovernor(allowed_topics=["clinical", "drug"])
retrieval_gov = RAGRetrievalGovernor(source_registry=registry, min_relevance=0.5)

rag = AgenticRAGOrchestrator(pipeline, query_gov, retrieval_gov, retriever_fn=kb.search)
result = rag.run(agent_id="clinical_ai", initial_query="max dose morphine",
                 action_fn=lambda ctx: prescribe(ctx))
```

See [../../docs/USECASES.md](../../docs/USECASES.md) Pattern 9.
