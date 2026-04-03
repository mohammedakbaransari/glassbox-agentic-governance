"""
GlassBox Framework — Decision Replay  (v1.0.0)
Enables historical decisions to be replayed through the current governance
pipeline. Essential for retroactive compliance auditing and policy testing.

Changes from v1.0.0:
  - async_replay_one()  — async variant for use in async pipelines
  - replay_many() now supports parallel execution via ThreadPoolExecutor
  - replay_many_async() — fully async parallel batch replay
  - compare_summary() extended with per-type breakdown

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from glassbox.governance.models import (
    AuditRecord, DecisionContext, DecisionRequest,
    DecisionResponse, FinalStatus,
)

if TYPE_CHECKING:
    from glassbox.governance.pipeline import GovernancePipeline


class DecisionReplay:
    """
    Replays historical decisions through the governance pipeline.

    Use cases:
      - Verify a past decision still passes under updated policies
      - Retroactive compliance auditing after policy changes
      - Debugging governance behaviour against known inputs
      - Policy regression testing with production history
    """

    def __init__(self, pipeline: "GovernancePipeline"):
        self.pipeline = pipeline

    # ── Sync API ──────────────────────────────────────────────────────────────

    def replay_one(self, record: AuditRecord) -> DecisionResponse:
        """
        Replay a single historical AuditRecord through the current pipeline.
        The replayed decision is tagged with replay_of=original_decision_id.
        Thread-safe: delegates to pipeline.process() which is thread-safe.
        """
        request = self._build_request(record)
        response = self.pipeline.process(
            request,
            request_metadata={"is_replay": True, "original_id": record.decision_id},
        )
        self._tag_replay(response, record.decision_id)
        return response

    def replay_many(
        self,
        records:         List[AuditRecord],
        stop_on_change:  bool = False,
        parallel:        bool = False,
        max_workers:     int  = 4,
    ) -> List[Dict[str, Any]]:
        """
        Replay a batch of historical records.

        Args:
            records:        List of AuditRecord to replay.
            stop_on_change: Stop at first outcome change.
            parallel:       Use ThreadPoolExecutor for parallel replay.
            max_workers:    Thread pool size for parallel mode.

        Returns:
            List of comparison dicts with original vs replayed outcome.
        """
        if parallel:
            return self._replay_parallel(records, stop_on_change, max_workers)
        return self._replay_sequential(records, stop_on_change)

    def _replay_sequential(
        self,
        records:        List[AuditRecord],
        stop_on_change: bool,
    ) -> List[Dict[str, Any]]:
        results = []
        for record in records:
            try:
                response = self.replay_one(record)
                entry = self._compare_entry(record, response)
                results.append(entry)
                if stop_on_change and entry.get("outcome_changed"):
                    break
            except Exception as exc:
                results.append({"decision_id": record.decision_id, "error": str(exc)})
        return results

    def _replay_parallel(
        self,
        records:        List[AuditRecord],
        stop_on_change: bool,
        max_workers:    int,
    ) -> List[Dict[str, Any]]:
        results: List[Optional[Dict]] = [None] * len(records)
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="glassbox-replay") as pool:
            futures = {
                pool.submit(self.replay_one, rec): i
                for i, rec in enumerate(records)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    response = fut.result()
                    results[idx] = self._compare_entry(records[idx], response)
                except Exception as exc:
                    results[idx] = {
                        "decision_id": records[idx].decision_id,
                        "error": str(exc),
                    }
        return [r for r in results if r is not None]

    # ── Async API ─────────────────────────────────────────────────────────────

    async def async_replay_one(self, record: AuditRecord) -> DecisionResponse:
        """
        Async variant of replay_one().
        Delegates to pipeline.process_async() — never blocks event loop.
        """
        request = self._build_request(record)
        response = await self.pipeline.process_async(
            request,
            request_metadata={"is_replay": True, "original_id": record.decision_id},
        )
        self._tag_replay(response, record.decision_id)
        return response

    async def async_replay_many(
        self,
        records:    List[AuditRecord],
        max_concurrency: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Fully async parallel batch replay.
        Uses asyncio.Semaphore to cap concurrency.
        """
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(rec: AuditRecord, idx: int):
            async with sem:
                try:
                    response = await self.async_replay_one(rec)
                    return idx, self._compare_entry(rec, response)
                except Exception as exc:
                    return idx, {"decision_id": rec.decision_id, "error": str(exc)}

        tasks = [_bounded(rec, i) for i, rec in enumerate(records)]
        raw = await asyncio.gather(*tasks)
        return [entry for _, entry in sorted(raw, key=lambda x: x[0])]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_request(self, record: AuditRecord) -> DecisionRequest:
        return DecisionRequest(
            agent_id=record.agent_id,
            decision_type=record.decision_type,
            payload=record.payload,
            context=DecisionContext(
                environment=record.context.environment,
                source_system="replay",
                confidence=record.context.confidence,
                agent_chain=record.context.agent_chain,
                metadata={"replay_of": record.decision_id, **record.context.metadata},
            ),
        )

    def _tag_replay(self, response: DecisionResponse, original_id: str) -> None:
        if response.audit_record:
            response.audit_record.replay_of    = original_id
            response.audit_record.final_status = FinalStatus.REPLAYED

    def _compare_entry(
        self,
        record:   AuditRecord,
        response: DecisionResponse,
    ) -> Dict[str, Any]:
        return {
            "decision_id":        record.decision_id,
            "agent_id":           record.agent_id,
            "decision_type":      record.decision_type.value,
            "original_status":    record.final_status.value if record.final_status else None,
            "replayed_status":    response.final_status.value,
            "outcome_changed":    record.final_status != response.final_status,
            "original_risk":      record.risk_result.risk_score if record.risk_result else None,
            "replayed_risk":      response.risk_score,
            "replay_decision_id": response.decision_id,
        }

    def compare_summary(self, replay_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarise a batch replay result with per-type breakdown."""
        total   = len(replay_results)
        changed = sum(1 for r in replay_results if r.get("outcome_changed"))
        errors  = sum(1 for r in replay_results if "error" in r)

        by_type: Dict[str, Dict[str, int]] = {}
        for r in replay_results:
            t = r.get("decision_type", "unknown")
            if t not in by_type:
                by_type[t] = {"total": 0, "changed": 0, "errors": 0}
            by_type[t]["total"] += 1
            if r.get("outcome_changed"):
                by_type[t]["changed"] += 1
            if "error" in r:
                by_type[t]["errors"] += 1

        return {
            "total_replayed":      total,
            "outcomes_unchanged":  total - changed - errors,
            "outcomes_changed":    changed,
            "errors":              errors,
            "change_rate_pct":     round(changed / max(total, 1) * 100, 1),
            "by_decision_type":    by_type,
        }
