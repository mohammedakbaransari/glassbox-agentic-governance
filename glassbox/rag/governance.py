"""
GlassBox — RAG & Agentic RAG Governance  (v1.0.0)
==================================================
Governs the Retrieval-Augmented Generation pipeline before
retrieved content reaches AI agents.

In a RAG system, governance has three distinct interception points:

  Point 1: Query Governance (before retrieval)
    Validate the query being sent to the vector store / search engine.
    Block queries that are injection attempts or out-of-scope.

  Point 2: Retrieval Governance (after retrieval, before agent)
    Validate retrieved documents for:
    - Relevance (are these chunks actually relevant to the query?)
    - Freshness (are documents within acceptable time bounds?)
    - Source authority (is the source on the approved list?)
    - Sensitive content (do retrieved chunks contain PII, secrets?)
    - Hallucination risk (are citations in the context verifiable?)

  Point 3: Agentic RAG Governance (the full reason→retrieve→act loop)
    In Agentic RAG, the agent decides WHAT to retrieve and WHAT to do
    with the retrieved content. Each "act" step is a DecisionRequest.
    The retrieve step itself is governed as a CUSTOM decision type.

Why this matters:
  Without RAG governance:
    - A corrupted knowledge base poisons every decision downstream
    - An agent querying for "how to override safety controls" gets answers
    - Stale regulatory documents lead to non-compliant AI decisions
    - Retrieved PII is passed unmasked to downstream systems

This module provides:
  RAGQueryGovernor     — governs the query before it hits the retriever
  RAGRetrievalGovernor — governs the retrieved chunks before agent sees them
  AgenticRAGOrchestrator — governs the full Retrieve-Reason-Act loop

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from glassbox.governance.models import (
    DecisionContext, DecisionRequest, DecisionResponse,
    DecisionType, FinalStatus,
)
from glassbox.security.sanitizer import PayloadSanitizer


# ── RAG Data Models ────────────────────────────────────────────────────────────

@dataclass
class RetrievedChunk:
    """
    A single document chunk returned by a retriever.
    Populate as many fields as your retriever provides.
    """
    chunk_id:        str
    content:         str
    source:          str                     # document ID, URL, filename
    source_type:     str = "unknown"         # "pdf" | "web" | "database" | "wiki"
    relevance_score: float = 1.0            # 0.0 = irrelevant, 1.0 = exact match
    created_at:      Optional[str] = None   # ISO timestamp of source document
    metadata:        Dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGQueryResult:
    """Result of governing a RAG query before retrieval."""
    allowed:       bool
    query:         str
    cleaned_query: Optional[str]   = None   # sanitised version if allowed
    blocked_reason: Optional[str]  = None
    warnings:      List[str]       = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "query": self.query,
                "cleaned_query": self.cleaned_query,
                "blocked_reason": self.blocked_reason,
                "warnings": self.warnings}


@dataclass
class RAGRetrievalResult:
    """Result of governing retrieved chunks before they reach an agent."""
    allowed_chunks:  List[RetrievedChunk]
    blocked_chunks:  List[Tuple[RetrievedChunk, str]]  # (chunk, reason)
    warnings:        List[str]
    total_retrieved: int
    passed_count:    int
    blocked_count:   int

    @property
    def all_blocked(self) -> bool:
        return self.passed_count == 0 and self.total_retrieved > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_retrieved": self.total_retrieved,
            "passed":          self.passed_count,
            "blocked":         self.blocked_count,
            "all_blocked":     self.all_blocked,
            "warnings":        self.warnings,
            "blocked_sources": [c.source for c, _ in self.blocked_chunks],
        }


# ── Approved Sources Registry ─────────────────────────────────────────────────

class ApprovedSourceRegistry:
    """
    Thread-safe registry of approved document sources.
    Governs which sources RAG can retrieve from.
    """

    def __init__(self, approved_sources: Optional[List[str]] = None):
        self._approved: set = set(approved_sources or [])
        self._blocked:  set = set()
        self._lock = threading.Lock()

    def add_approved(self, source: str) -> None:
        with self._lock:
            self._approved.add(source.lower())
            self._blocked.discard(source.lower())

    def block_source(self, source: str) -> None:
        with self._lock:
            self._blocked.add(source.lower())
            self._approved.discard(source.lower())

    def is_approved(self, source: str) -> bool:
        """True if source is explicitly approved (or registry is open/empty)."""
        with self._lock:
            if source.lower() in self._blocked:
                return False
            if not self._approved:
                return True   # open registry — allow all unless explicitly blocked
            return source.lower() in self._approved

    def is_blocked(self, source: str) -> bool:
        with self._lock:
            return source.lower() in self._blocked


# ── RAG Query Governor ─────────────────────────────────────────────────────────

class RAGQueryGovernor:
    """
    Governs queries before they are sent to the retriever.

    Checks:
    1. Injection detection (SQL, SSTI, XSS — attacker controlling the query)
    2. Scope validation (query matches allowed topics)
    3. Sensitive keyword detection (queries for credentials, secrets, PII)
    4. Query length limits (prevent corpus-poisoning via long queries)

    Usage:
        governor = RAGQueryGovernor()
        result   = governor.check(query="What are the drug interactions for morphine?",
                                  agent_id="clinical_agent")
        if result.allowed:
            chunks = retriever.retrieve(result.cleaned_query)
    """

    _SENSITIVE_PATTERNS = [
        re.compile(r"(?i)(password|passwd|secret|api.?key|token|credential)", re.I),
        re.compile(r"(?i)(override|bypass|disable|ignore).*(safety|policy|governance|control)", re.I),
        re.compile(r"(?i)(jailbreak|prompt.inject|ignore.previous|forget.instruction)", re.I),
        re.compile(r"(?i)(social.security|ssn|credit.card|bank.account)\s*\d", re.I),
        re.compile(r"(?i)(how.to.hack|how.to.exploit|vulnerability.in|0.?day)", re.I),
    ]

    def __init__(
        self,
        max_query_length:      int         = 2048,
        allowed_topics:        Optional[List[str]] = None,   # None = all topics allowed
        block_on_out_of_scope: bool        = False,
        sanitizer:             Optional[PayloadSanitizer] = None,
    ):
        self.max_query_length      = max_query_length
        self.allowed_topics        = [t.lower() for t in allowed_topics] if allowed_topics else None
        self.block_on_out_of_scope = block_on_out_of_scope
        self.sanitizer             = sanitizer or PayloadSanitizer()

    def check(self, query: str, agent_id: str = "") -> RAGQueryResult:
        """Govern a RAG query before it reaches the retriever."""
        if not query or not isinstance(query, str):
            return RAGQueryResult(allowed=False, query=str(query),
                                  blocked_reason="Query must be a non-empty string")

        # Length check
        if len(query) > self.max_query_length:
            return RAGQueryResult(
                allowed=False, query=query,
                blocked_reason=f"Query length {len(query)} exceeds maximum {self.max_query_length}"
            )

        # Injection check via payload sanitizer
        sec = self.sanitizer.check({"query": query}, agent_id=agent_id)
        if sec.blocked:
            findings = [f.detail for f in sec.findings if f.severity in ("critical","high")]
            return RAGQueryResult(
                allowed=False, query=query,
                blocked_reason=f"Security: {'; '.join(findings[:2])}"
            )

        # Sensitive pattern check
        for pattern in self._SENSITIVE_PATTERNS:
            if pattern.search(query):
                return RAGQueryResult(
                    allowed=False, query=query,
                    blocked_reason=f"Query matches sensitive pattern: {pattern.pattern[:60]}"
                )

        # Topic scope check
        warnings = []
        if self.allowed_topics:
            query_lower = query.lower()
            in_scope = any(
                re.search(r'\b' + re.escape(topic) + r'\b', query_lower)
                for topic in self.allowed_topics
            )
            if not in_scope:
                if self.block_on_out_of_scope:
                    return RAGQueryResult(
                        allowed=False, query=query,
                        blocked_reason=f"Query out of scope. Allowed topics: {self.allowed_topics[:5]}"
                    )
                warnings.append(
                    f"Query may be out of scope. Allowed topics: {self.allowed_topics[:5]}")

        # Return clean query (sanitised string)
        cleaned = (sec.clean_payload or {}).get("query", query)
        return RAGQueryResult(
            allowed=True, query=query,
            cleaned_query=cleaned, warnings=warnings,
        )


# ── RAG Retrieval Governor ──────────────────────────────────────────────────────

class RAGRetrievalGovernor:
    """
    Governs retrieved document chunks before they reach the agent.

    Checks per chunk:
    1. Source approval (is this source authorised?)
    2. Relevance threshold (min score below which chunks are excluded)
    3. Freshness (max age of source documents)
    4. Content safety (PII, secrets, injection in retrieved content)
    5. Content hash deduplication (same content from different sources)

    Usage:
        governor = RAGRetrievalGovernor(
            source_registry=registry,
            min_relevance=0.5,
            max_age_days=90,
        )
        result = governor.check(chunks, query="What is the refund policy?")
        agent_sees = result.allowed_chunks
    """

    def __init__(
        self,
        source_registry:    Optional[ApprovedSourceRegistry] = None,
        min_relevance:      float = 0.3,
        max_age_days:       Optional[int] = None,
        deduplicate:        bool = True,
        content_sanitizer:  Optional[PayloadSanitizer] = None,
        max_chunks_to_pass: int = 10,
    ):
        self.source_registry  = source_registry or ApprovedSourceRegistry()
        self.min_relevance    = min_relevance
        self.max_age_days     = max_age_days
        self.deduplicate      = deduplicate
        self.sanitizer        = content_sanitizer or PayloadSanitizer(
            block_on_sql=False, block_on_script=False  # retrieval content is less strict
        )
        self.max_chunks       = max_chunks_to_pass

    def check(
        self,
        chunks:  List[RetrievedChunk],
        query:   str = "",
    ) -> RAGRetrievalResult:
        """Filter retrieved chunks, returning only safe and relevant ones."""
        allowed  = []
        blocked  = []
        warnings = []
        seen_hashes: set = set()

        for chunk in chunks[:100]:  # cap at 100 to prevent runaway
            reason = self._check_chunk(chunk, seen_hashes, query)
            if reason:
                blocked.append((chunk, reason))
            else:
                allowed.append(chunk)
                # Track content hash for deduplication
                if self.deduplicate:
                    content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()[:16]
                    seen_hashes.add(content_hash)

        # Warn if most chunks were blocked
        if len(chunks) > 0 and len(blocked) / len(chunks) > 0.5:
            warnings.append(
                f"Majority of retrieved chunks blocked ({len(blocked)}/{len(chunks)}). "
                f"Review source registry and relevance thresholds."
            )

        # Apply max_chunks cap
        allowed = allowed[:self.max_chunks]

        return RAGRetrievalResult(
            allowed_chunks=allowed,
            blocked_chunks=blocked,
            warnings=warnings,
            total_retrieved=len(chunks),
            passed_count=len(allowed),
            blocked_count=len(blocked),
        )

    def _check_chunk(
        self,
        chunk:        RetrievedChunk,
        seen_hashes:  set,
        query:        str,
    ) -> Optional[str]:
        """Return a reason string if the chunk should be blocked, else None."""

        # Source authorisation — check explicit block FIRST for accurate messages
        if self.source_registry.is_blocked(chunk.source):
            return f"Source '{chunk.source}' is explicitly blocked"

        if not self.source_registry.is_approved(chunk.source):
            return f"Source '{chunk.source}' not in approved source registry"

        # Relevance threshold
        if chunk.relevance_score < self.min_relevance:
            return (f"Relevance score {chunk.relevance_score:.2f} below "
                    f"minimum {self.min_relevance:.2f}")

        # Freshness check
        if self.max_age_days and chunk.created_at:
            try:
                created = datetime.fromisoformat(chunk.created_at.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created).days
                if age_days > self.max_age_days:
                    return (f"Document age {age_days} days exceeds "
                            f"maximum {self.max_age_days} days")
            except (ValueError, TypeError):
                pass  # cannot parse date — allow through with warning

        # Deduplication
        if self.deduplicate:
            content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()[:16]
            if content_hash in seen_hashes:
                return "Duplicate content (same hash already included)"

        # Content safety — look for credentials/PII in retrieved content
        sec = self.sanitizer.check({"content": chunk.content[:5000]})  # truncate for scan
        critical = [f for f in sec.findings if f.severity == "critical"]
        if critical:
            return f"Critical security finding in content: {critical[0].detail[:80]}"

        return None


# ── Agentic RAG Orchestrator ────────────────────────────────────────────────────

class AgenticRAGOrchestrator:
    """
    Governs the full Agentic RAG loop: Reason → Retrieve → Govern → Act.

    In standard RAG: query → retrieve → generate
    In Agentic RAG:  reason about what to retrieve →
                     retrieve → govern retrieval →
                     reason about what to do →
                     act (governed decision) →
                     repeat if needed

    This orchestrator governs every step in that loop.

    Usage:
        rag = AgenticRAGOrchestrator(
            pipeline=pipeline,
            query_governor=RAGQueryGovernor(),
            retrieval_governor=RAGRetrievalGovernor(source_registry=registry),
            retriever_fn=my_vector_store.search,
        )
        result = rag.run(
            agent_id="clinical_ai",
            initial_query="What is the maximum safe dose of ibuprofen?",
            action_fn=lambda context: prescribe_drug(context),
            action_decision_type=DecisionType.CUSTOM,
        )
    """

    def __init__(
        self,
        pipeline,
        query_governor:     Optional[RAGQueryGovernor]     = None,
        retrieval_governor: Optional[RAGRetrievalGovernor] = None,
        retriever_fn:       Optional[Callable]             = None,
        max_iterations:     int                            = 5,
    ):
        self.pipeline           = pipeline
        self.query_governor     = query_governor or RAGQueryGovernor()
        self.retrieval_governor = retrieval_governor or RAGRetrievalGovernor()
        self.retriever_fn       = retriever_fn
        self.max_iterations     = max_iterations

    def run(
        self,
        agent_id:            str,
        initial_query:       str,
        action_fn:           Callable[[Dict], Any],
        action_decision_type: DecisionType = DecisionType.CUSTOM,
        action_payload_fn:   Optional[Callable[[Dict], Dict]] = None,
        confidence:          float = 1.0,
        should_continue_fn:  Optional[Callable[[Dict], bool]] = None,
        next_query_fn:       Optional[Callable[[Dict], str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute one or more Agentic RAG iterations:
        1. Govern the query
        2. Retrieve (if retriever_fn is provided)
        3. Govern the retrieval result
        4. Govern the action
        5. Execute the action

        Repeats up to max_iterations if should_continue_fn returns True.
        Returns a result dict with governance outcome and action result.
        """
        result = {
            "agent_id":         agent_id,
            "query":            initial_query,
            "query_gov":        None,
            "retrieval_gov":    None,
            "decision_id":      None,
            "final_status":     "unknown",
            "action_result":    None,
            "blocked_reason":   None,
            "iterations":       0,
        }

        working_query = initial_query

        for _iteration in range(self.max_iterations):
            result["iterations"] += 1

            # Step 1: Govern the query
            query_result = self.query_governor.check(working_query, agent_id=agent_id)
            result["query_gov"] = query_result.to_dict()

            if not query_result.allowed:
                result["final_status"]   = "blocked_at_query"
                result["blocked_reason"] = query_result.blocked_reason
                return result

            governed_query = query_result.cleaned_query or working_query

            # Step 2: Retrieve (optional — only if retriever provided)
            retrieved_chunks = []
            if self.retriever_fn:
                try:
                    raw_chunks = self.retriever_fn(governed_query)
                    if isinstance(raw_chunks, list):
                        retrieved_chunks = raw_chunks
                except Exception as exc:
                    result["final_status"]   = "retrieval_error"
                    result["blocked_reason"] = f"Retriever error: {exc}"
                    return result

                # Step 3: Govern the retrieval result
                retrieval_result = self.retrieval_governor.check(
                    retrieved_chunks, query=governed_query)
                result["retrieval_gov"] = retrieval_result.to_dict()

                if retrieval_result.all_blocked:
                    result["final_status"]   = "blocked_at_retrieval"
                    result["blocked_reason"] = "All retrieved chunks were blocked by retrieval governance"
                    return result

                working_chunks = retrieval_result.allowed_chunks
            else:
                working_chunks = []

            # Build context for action
            context_for_action: Dict[str, Any] = {
                "query":           governed_query,
                "retrieved_chunks": [
                    {"content": c.content[:500], "source": c.source,
                     "relevance": c.relevance_score}
                    for c in working_chunks
                ],
            }

            # Step 4: Govern the action through the pipeline
            if action_payload_fn:
                action_payload = action_payload_fn(context_for_action)
            else:
                action_payload = {
                    "query":        governed_query,
                    "chunk_count":  len(working_chunks),
                    "sources":      list({c.source for c in working_chunks}),
                    "action_type":  "rag_response",
                }

            ctx     = DecisionContext(
                confidence=confidence, source_system="agentic_rag",
                metadata={"query": governed_query[:200],
                          "chunk_count": len(working_chunks)},
            )
            request = DecisionRequest(
                agent_id=agent_id, decision_type=action_decision_type,
                payload=action_payload, context=ctx,
            )
            response = self.pipeline.process(request)
            result["decision_id"]  = response.decision_id
            result["final_status"] = response.final_status.value

            if response.final_status == FinalStatus.BLOCKED:
                result["blocked_reason"] = (
                    response.policy_violations[0] if response.policy_violations
                    else response.message
                )
                return result

            # Step 5: Execute the action
            try:
                result["action_result"] = action_fn(context_for_action)
            except Exception as exc:
                result["action_error"] = str(exc)

            # Decide whether to iterate
            if should_continue_fn is None or not should_continue_fn(result):
                break

            if next_query_fn is not None:
                working_query = next_query_fn(result)

        return result

    def check_query(self, query: str, agent_id: str = "") -> RAGQueryResult:
        """Expose query governance as a standalone check."""
        return self.query_governor.check(query, agent_id=agent_id)

    def check_chunks(self, chunks: List[RetrievedChunk]) -> RAGRetrievalResult:
        """Expose retrieval governance as a standalone check."""
        return self.retrieval_governor.check(chunks)
