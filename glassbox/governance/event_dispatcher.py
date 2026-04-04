"""
GlassBox Framework — Resilient Event Dispatcher  (v1.0.1)
===========================================================
Wraps the event bus with circuit breaker, retry, and fallback logic.
Ensures that event publication failures don't impact decision governance.

Problem solved:
  - Before: bare `except Exception: pass` meant event failures were invisible
  - After: circuit breaker + fallback logging + metrics

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("glassbox.event_dispatcher")


class CircuitBreakerState:
    """State machine for circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing; reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


class ResilientEventDispatcher:
    """
    Wraps event bus with circuit breaker + fallback + observability.
    
    Ensures that event publication failures don't block decision processing.
    
    State machine:
      CLOSED (normal)
        ↓ [failure_threshold breaches]
      OPEN (rejecting)
        ↓ [timeout_sec elapses]
      HALF_OPEN (testing recovery)
        ↓ [recovery_attempt succeeds] → CLOSED
        ↓ [recovery_attempt fails] → OPEN
    
    Usage:
        dispatcher = ResilientEventDispatcher(
            event_bus=eventbus,
            fallback_log_fn=logger.warning,
            max_failures=10,
            failure_timeout_sec=60,
        )
        
        dispatcher.publish(my_event, event_type="DecisionExecuted")
    """

    def __init__(
        self,
        event_bus: Any,
        fallback_log_fn: Optional[Callable[[str], None]] = None,
        max_failures: int = 10,
        failure_timeout_sec: int = 60,
    ):
        self.event_bus = event_bus
        self.fallback_log_fn = fallback_log_fn or log.warning
        self.max_failures = max_failures
        self.failure_timeout_sec = failure_timeout_sec

        # Circuit breaker state
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

        log.info(
            "ResilientEventDispatcher initialized",
            extra={
                "component": "event_dispatcher",
                "max_failures": max_failures,
                "timeout_sec": failure_timeout_sec,
            },
        )

    def publish(
        self,
        event: Any,
        event_type: str = "Unknown",
        on_failure: Optional[Callable[[Any, Exception], None]] = None,
    ) -> bool:
        """
        Publish event with circuit breaker protection.

        Args:
            event: The event object to publish
            event_type: String identifier for logging (e.g., "DecisionExecuted")
            on_failure: Optional callback on failure: fn(event, exception)

        Returns:
            bool: True if published successfully, False if circuit open or error
        """
        with self._lock:
            # Check circuit breaker state
            if self._state == CircuitBreakerState.OPEN:
                now = time.time()
                elapsed = now - (self._last_failure_time or now)

                if elapsed >= self.failure_timeout_sec:
                    # Try recovery
                    self._state = CircuitBreakerState.HALF_OPEN
                    log.info(
                        "EventBus circuit breaker: HALF_OPEN (testing recovery)",
                        extra={"component": "event_dispatcher"},
                    )
                else:
                    # Still open; reject
                    self._log_circuit_open(event, event_type)
                    return False

        # Try to publish
        try:
            self.event_bus.publish(event)

            # Success: decay failure count
            with self._lock:
                self._failure_count = max(0, self._failure_count - 1)

                if self._state == CircuitBreakerState.HALF_OPEN:
                    self._state = CircuitBreakerState.CLOSED
                    log.info(
                        "EventBus circuit breaker: CLOSED (recovered)",
                        extra={"component": "event_dispatcher"},
                    )

            return True

        except Exception as exc:
            return self._handle_failure(event, event_type, exc, on_failure)

    def _handle_failure(
        self,
        event: Any,
        event_type: str,
        exc: Exception,
        on_failure: Optional[Callable] = None,
    ) -> bool:
        """Handle publication failure."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            # Log the error with full context
            log.error(
                f"Event publication failed [{event_type}]: {type(exc).__name__}: {exc}",
                extra={
                    "component": "event_dispatcher",
                    "event_type": event_type,
                    "error_type": type(exc).__name__,
                    "failure_count": self._failure_count,
                    "max_failures": self.max_failures,
                },
                exc_info=True,  # Include traceback
            )

            # Trip circuit breaker if threshold exceeded
            if self._failure_count >= self.max_failures:
                self._state = CircuitBreakerState.OPEN
                log.critical(
                    f"EventBus circuit breaker: OPEN "
                    f"(after {self._failure_count} failures)",
                    extra={
                        "component": "event_dispatcher",
                        "failure_count": self._failure_count,
                    },
                )

        # Invoke callback if provided
        if on_failure:
            try:
                on_failure(event, exc)
            except Exception as cb_exc:
                log.error(
                    f"Failure callback raised exception: {cb_exc}",
                    extra={"component": "event_dispatcher"},
                    exc_info=True,
                )

        return False

    def _log_circuit_open(self, event: Any, event_type: str) -> None:
        """Log that circuit is open and request is being rejected."""
        log.warning(
            f"EventBus circuit breaker OPEN; event not published: {event_type}",
            extra={
                "component": "event_dispatcher",
                "event_type": event_type,
                "state": self._state,
            },
        )

    def reset(self) -> None:
        """Manually reset circuit breaker (e.g., after maintenance)."""
        with self._lock:
            old_state = self._state
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

            log.info(
                f"EventBus circuit breaker manually reset: {old_state} → CLOSED",
                extra={"component": "event_dispatcher"},
            )

    def status(self) -> dict:
        """Get current circuit breaker status."""
        with self._lock:
            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "last_failure_time": self._last_failure_time,
            }
