"""
GlassBox Framework — Retry Policy  (v1.0.0)
Configurable retry-with-backoff for downstream executor calls.
Zero external dependencies — Python stdlib only.

Changes from v1.0.0:
  - Added async_execute() for use with asyncio / LangChain / AutoGen executors
  - async_execute() uses asyncio.sleep() instead of time.sleep() — never blocks event loop
  - Both sync and async variants share the same backoff calculation
  - Non-retryable exceptions propagate immediately in both variants

Strategies:
  NONE               — no retry, fail immediately
  FIXED              — fixed delay between attempts
  EXPONENTIAL        — delay doubles each attempt
  EXPONENTIAL_JITTER — exponential + random jitter (recommended for prod)

Usage (sync):
    executor = RetryExecutor(config=RetryConfig(max_attempts=3))
    result   = executor.execute(my_downstream_fn, audit_record)

Usage (async):
    result = await executor.async_execute(my_async_fn, audit_record)

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Coroutine, Dict, Optional

from glassbox.governance.models import (
    AuditRecord, ExecutionResult, RetryConfig, RetryStrategy,
)

log = logging.getLogger("glassbox.retry")


class RetryExecutor:
    """
    Wraps a downstream executor callable with configurable retry logic.

    Sync:  executor_fn(record) -> Dict[str, Any]
    Async: executor_fn(record) -> Coroutine[..., Dict[str, Any]]

    Retryable exceptions trigger retry with backoff.
    All other exceptions propagate immediately (non-retryable).
    """

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()

    def _delay_seconds(self, attempt: int) -> float:
        """Compute backoff delay in seconds for attempt N (1-indexed)."""
        cfg = self.config
        if cfg.strategy == RetryStrategy.NONE:
            return 0.0
        if cfg.strategy == RetryStrategy.FIXED:
            return cfg.base_delay_s
        delay = cfg.base_delay_s * (cfg.backoff_factor ** (attempt - 1))
        delay = min(delay, cfg.max_delay_s)
        if cfg.strategy == RetryStrategy.EXPONENTIAL_JITTER:
            delay = delay * (0.5 + random.random() * 0.5)   # 50–100%
        return delay

    def execute(
        self,
        executor_fn: Callable[[AuditRecord], Dict[str, Any]],
        record:      AuditRecord,
    ) -> ExecutionResult:
        """
        Synchronous retry executor.
        Uses time.sleep() — intended for thread-pool contexts (not event loops).
        Safe to call from GovernancePipeline.process().
        """
        cfg = self.config
        last_exc: Optional[Exception] = None
        total_delay_ms = 0.0

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                result = executor_fn(record)
                if attempt > 1:
                    log.info("Executor succeeded on attempt %d/%d for %s",
                             attempt, cfg.max_attempts, record.decision_id)
                return ExecutionResult(
                    success=True, result=result,
                    attempts=attempt, total_delay_ms=total_delay_ms,
                )
            except cfg.retryable_exceptions as exc:
                last_exc = exc
                if attempt < cfg.max_attempts and cfg.strategy != RetryStrategy.NONE:
                    delay_s = self._delay_seconds(attempt)
                    total_delay_ms += delay_s * 1000
                    log.warning("Executor attempt %d/%d failed for %s: %s. Retrying in %.2fs.",
                                attempt, cfg.max_attempts, record.decision_id, exc, delay_s)
                    time.sleep(delay_s)
                else:
                    log.error("Executor failed permanently after %d attempts for %s: %s",
                              attempt, record.decision_id, exc)
                    break
            except Exception as exc:
                log.error("Executor non-retryable error for %s: %s", record.decision_id, exc)
                return ExecutionResult(
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    attempts=attempt,
                    total_delay_ms=total_delay_ms,
                )

        return ExecutionResult(
            success=False,
            error=f"All {cfg.max_attempts} attempt(s) exhausted. Last: {last_exc}",
            attempts=cfg.max_attempts,
            total_delay_ms=total_delay_ms,
        )

    async def async_execute(
        self,
        executor_fn: Callable[[AuditRecord], Coroutine[Any, Any, Dict[str, Any]]],
        record:      AuditRecord,
    ) -> ExecutionResult:
        """
        Asynchronous retry executor.
        Uses asyncio.sleep() — never blocks the event loop.
        Use with GovernancePipeline.process_async() and async executor functions.
        """
        cfg = self.config
        last_exc: Optional[Exception] = None
        total_delay_ms = 0.0

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                result = await executor_fn(record)
                if attempt > 1:
                    log.info("Async executor succeeded on attempt %d/%d for %s",
                             attempt, cfg.max_attempts, record.decision_id)
                return ExecutionResult(
                    success=True, result=result,
                    attempts=attempt, total_delay_ms=total_delay_ms,
                )
            except cfg.retryable_exceptions as exc:
                last_exc = exc
                if attempt < cfg.max_attempts and cfg.strategy != RetryStrategy.NONE:
                    delay_s = self._delay_seconds(attempt)
                    total_delay_ms += delay_s * 1000
                    log.warning("Async executor attempt %d/%d failed for %s: %s. Retrying in %.2fs.",
                                attempt, cfg.max_attempts, record.decision_id, exc, delay_s)
                    await asyncio.sleep(delay_s)   # never blocks event loop
                else:
                    log.error("Async executor failed permanently after %d attempts for %s: %s",
                              attempt, record.decision_id, exc)
                    break
            except Exception as exc:
                log.error("Async executor non-retryable error for %s: %s", record.decision_id, exc)
                return ExecutionResult(
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    attempts=attempt,
                    total_delay_ms=total_delay_ms,
                )

        return ExecutionResult(
            success=False,
            error=f"All {cfg.max_attempts} attempt(s) exhausted. Last: {last_exc}",
            attempts=cfg.max_attempts,
            total_delay_ms=total_delay_ms,
        )
