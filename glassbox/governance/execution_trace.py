"""
GlassBox Framework — Execution Step Logger  (v1.0.0)
=====================================================
Records granular per-stage execution traces for every decision.

Each pipeline run produces an ExecutionTrace containing one
ExecutionStep per stage. Each step records:
  - Stage number and name
  - Input snapshot (what entered the stage)
  - Output snapshot (what the stage produced)
  - Duration in milliseconds
  - Stage outcome: passed | blocked | warned | skipped
  - Any error or exception that occurred

This answers the question: "Why exactly was this decision blocked?"
Previously the pipeline ran all 9 stages and returned a final status,
but there was no way to see what happened at stage 4 vs stage 5.

The trace is attached to DecisionResponse.execution_trace and stored
alongside the AuditRecord. It is opt-in (trace_enabled=False by default)
to avoid overhead in high-throughput production deployments.

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionStep:
    """Record of one pipeline stage execution."""
    stage_num:    int
    stage_name:   str
    outcome:      str           # "passed" | "blocked" | "warned" | "skipped" | "error"
    duration_ms:  float         = 0.0
    input_summary: Dict[str, Any] = field(default_factory=dict)
    output_summary: Dict[str, Any] = field(default_factory=dict)
    detail:       str           = ""
    error:        Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_num":      self.stage_num,
            "stage_name":     self.stage_name,
            "outcome":        self.outcome,
            "duration_ms":    round(self.duration_ms, 3),
            "input_summary":  self.input_summary,
            "output_summary": self.output_summary,
            "detail":         self.detail,
            "error":          self.error,
        }


class ExecutionTrace:
    """
    Full per-decision pipeline execution trace.

    Attached to DecisionResponse when trace_enabled=True on the pipeline.
    Provides a stage-by-stage explanation of the governance outcome.

    Usage:
        pipeline = GovernancePipeline(trace_enabled=True)
        response = pipeline.process(request)
        for step in response.execution_trace.steps:
            print(f"Stage {step.stage_num} {step.stage_name}: {step.outcome} ({step.duration_ms}ms)")
    """

    def __init__(self, decision_id: str):
        self.decision_id = decision_id
        self.steps:    List[ExecutionStep] = []
        self._started  = time.perf_counter()
        self.total_ms  = 0.0

    def add(self, step: ExecutionStep) -> None:
        self.steps.append(step)

    def finalise(self) -> None:
        self.total_ms = round((time.perf_counter() - self._started) * 1000, 3)

    def blocked_at(self) -> Optional[str]:
        """Return the stage name that caused a block, or None."""
        for s in self.steps:
            if s.outcome == "blocked":
                return s.stage_name
        return None

    def summary(self) -> str:
        """One-line summary of the trace for logging."""
        stages = " → ".join(
            f"{s.stage_name}[{s.outcome[0].upper()}]"
            for s in self.steps
        )
        return f"decision={self.decision_id} | {stages} | total={self.total_ms}ms"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "total_ms":    self.total_ms,
            "blocked_at":  self.blocked_at(),
            "steps":       [s.to_dict() for s in self.steps],
        }


class StageTimer:
    """
    Context manager for timing a pipeline stage.

    Usage:
        with StageTimer(trace, stage_num=2, stage_name="SchemaValidation") as t:
            ok, err = validator.validate(...)
            t.outcome = "passed" if ok else "blocked"
            t.detail  = err or ""
    """

    def __init__(
        self,
        trace:      ExecutionTrace,
        stage_num:  int,
        stage_name: str,
        input_summary: Dict[str, Any] = None,
    ):
        self.trace         = trace
        self.stage_num     = stage_num
        self.stage_name    = stage_name
        self.input_summary = input_summary or {}
        self.outcome       = "passed"
        self.detail        = ""
        self.output_summary: Dict[str, Any] = {}
        self.error: Optional[str] = None
        self._t0: float = 0.0

    def __enter__(self) -> "StageTimer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        duration_ms = round((time.perf_counter() - self._t0) * 1000, 3)
        if exc_val is not None:
            self.outcome = "error"
            self.error   = f"{type(exc_val).__name__}: {exc_val}"
        self.trace.add(ExecutionStep(
            stage_num=self.stage_num,
            stage_name=self.stage_name,
            outcome=self.outcome,
            duration_ms=duration_ms,
            input_summary=self.input_summary,
            output_summary=self.output_summary,
            detail=self.detail,
            error=self.error,
        ))
        return False   # do not suppress exceptions
