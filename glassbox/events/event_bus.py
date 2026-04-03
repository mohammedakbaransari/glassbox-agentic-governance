"""
GlassBox Framework — Event System  (v1.0.0)
============================================
Domain event bus for GlassBox governance events.

Events are the primary integration point for external systems.
Instead of coupling the pipeline to external services, any system
can subscribe to governance events and react accordingly.

Events published by the pipeline:
  DecisionExecuted        — a decision was approved and executed
  DecisionBlocked         — a decision was blocked (with reasons)
  DecisionPendingReview   — a decision routed to human review queue
  PolicyViolated          — one or more policy violations detected
  CircuitBreakerTripped   — velocity or anomaly breaker fired
  AnomalyDetected         — statistical anomaly in decision payload
  SecurityViolation       — injection or malicious payload detected
  WorkflowCreated         — approval workflow instance created
  WorkflowResolved        — workflow approved/rejected/escalated
  SLABreached             — review SLA timer expired

Subscription patterns:
  1. Direct handler registration (in-process, synchronous)
  2. Async handler registration (in-process, asyncio)
  3. Webhook handler (HTTP POST to external URL)

Thread-safety: all methods are thread-safe.

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


# ── Domain Events ─────────────────────────────────────────────────────────────

@dataclass
class GlassBoxEvent:
    """Base class for all GlassBox domain events."""
    event_type:  str
    event_id:    str                  = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:   str                  = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source:      str                  = "glassbox.pipeline"
    payload:     Dict[str, Any]       = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id":   self.event_id,
            "event_type": self.event_type,
            "timestamp":  self.timestamp,
            "source":     self.source,
            "payload":    self.payload,
        }


def DecisionExecuted(decision_id: str, agent_id: str, decision_type: str,
                     risk_score: float, latency_ms: float) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="decision.executed",
        payload={"decision_id": decision_id, "agent_id": agent_id,
                 "decision_type": decision_type, "risk_score": risk_score,
                 "latency_ms": latency_ms},
    )


def DecisionBlocked(decision_id: str, agent_id: str, decision_type: str,
                    violations: List[str], risk_score: Optional[float]) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="decision.blocked",
        payload={"decision_id": decision_id, "agent_id": agent_id,
                 "decision_type": decision_type, "violations": violations,
                 "risk_score": risk_score},
    )


def DecisionPendingReview(decision_id: str, agent_id: str, decision_type: str,
                           risk_score: float, workflow_id: Optional[str] = None) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="decision.pending_review",
        payload={"decision_id": decision_id, "agent_id": agent_id,
                 "decision_type": decision_type, "risk_score": risk_score,
                 "workflow_id": workflow_id},
    )


def PolicyViolated(decision_id: str, agent_id: str,
                   violations: List[str], warnings: List[str]) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="policy.violated",
        payload={"decision_id": decision_id, "agent_id": agent_id,
                 "violations": violations, "warnings": warnings,
                 "violation_count": len(violations)},
    )


def CircuitBreakerTripped(agent_id: str, breaker_name: str,
                           reason: str, is_ecosystem: bool) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="circuit_breaker.tripped",
        payload={"agent_id": agent_id, "breaker_name": breaker_name,
                 "reason": reason, "is_ecosystem": is_ecosystem},
    )


def AnomalyDetected(decision_id: str, agent_id: str, decision_type: str,
                     anomalous_fields: List[str], z_score: float) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="anomaly.detected",
        payload={"decision_id": decision_id, "agent_id": agent_id,
                 "decision_type": decision_type, "anomalous_fields": anomalous_fields,
                 "z_score": z_score},
    )


def SecurityViolation(agent_id: str, decision_type: str,
                       findings: List[str]) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="security.violation",
        payload={"agent_id": agent_id, "decision_type": decision_type,
                 "findings": findings, "severity": "critical"},
    )


def SLABreached(workflow_id: str, decision_id: str, agent_id: str,
                 sla_minutes: int, elapsed_minutes: float) -> GlassBoxEvent:
    return GlassBoxEvent(
        event_type="workflow.sla_breached",
        payload={"workflow_id": workflow_id, "decision_id": decision_id,
                 "agent_id": agent_id, "sla_minutes": sla_minutes,
                 "elapsed_minutes": round(elapsed_minutes, 1)},
    )


# ── Event Bus ──────────────────────────────────────────────────────────────────

class EventBus:
    """
    In-process, thread-safe, async-capable event bus.

    Supports three handler types:
      1. Synchronous:  fn(event: GlassBoxEvent) -> None
      2. Asynchronous: async fn(event: GlassBoxEvent) -> None
      3. Wildcard:     subscribe to "*" to receive all events

    Handlers are called in a thread pool so slow handlers
    never block the governance pipeline.

    Usage:
        bus = EventBus()

        # Subscribe
        bus.subscribe("decision.blocked", my_alert_handler)
        bus.subscribe("*", audit_all_events)

        # Publish (from pipeline or anywhere)
        bus.publish(DecisionBlocked(...))
    """

    def __init__(self, max_workers: int = 4):
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._lock    = threading.Lock()
        self._pool    = ThreadPoolExecutor(max_workers=max_workers,
                                           thread_name_prefix="glassbox-events")
        self._history: List[GlassBoxEvent] = []
        self._history_max = 1000
        self._history_lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        Register a handler for an event type.
        Use "*" to subscribe to all events.
        Thread-safe.
        """
        with self._lock:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        with self._lock:
            if event_type in self._handlers:
                try:
                    self._handlers[event_type].remove(handler)
                except ValueError:
                    pass

    def publish(self, event: GlassBoxEvent) -> None:
        """
        Publish an event. All matching handlers are called asynchronously
        in the thread pool — never blocks the calling thread.
        """
        # Record in history
        with self._history_lock:
            self._history.append(event)
            if len(self._history) > self._history_max:
                self._history.pop(0)

        with self._lock:
            specific  = list(self._handlers.get(event.event_type, []))
            wildcard  = list(self._handlers.get("*", []))
        all_handlers = specific + wildcard

        for handler in all_handlers:
            self._pool.submit(self._invoke, handler, event)

    def publish_sync(self, event: GlassBoxEvent) -> None:
        """Publish synchronously — blocks until all handlers complete."""
        with self._history_lock:
            self._history.append(event)
        with self._lock:
            specific = list(self._handlers.get(event.event_type, []))
            wildcard = list(self._handlers.get("*", []))
        for handler in specific + wildcard:
            self._invoke(handler, event)

    def _invoke(self, handler: Callable, event: GlassBoxEvent) -> None:
        try:
            if asyncio.iscoroutinefunction(handler):
                # Run async handler in a new event loop
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(handler(event))
                finally:
                    loop.close()
            else:
                handler(event)
        except Exception as exc:
            # Handlers must never crash the bus
            import logging
            logging.getLogger("glassbox.events").error(
                "EventBus handler %s failed for %s: %s",
                getattr(handler, "__name__", str(handler)), event.event_type, exc
            )

    def recent(self, event_type: Optional[str] = None, n: int = 50) -> List[GlassBoxEvent]:
        """Return recent events from in-memory history."""
        with self._history_lock:
            events = list(self._history)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-n:]

    def shutdown(self) -> None:
        """Graceful shutdown — wait for handlers to complete."""
        self._pool.shutdown(wait=True)


# ── Built-in handlers ─────────────────────────────────────────────────────────

class LoggingEventHandler:
    """
    Built-in handler: logs all events using the GlassBox logging manager.
    Register with bus.subscribe("*", LoggingEventHandler().handle)
    """

    def __init__(self):
        from glassbox.governance.logging_manager import get_logger
        self.log = get_logger("events")

    def handle(self, event: GlassBoxEvent) -> None:
        self.log.info(
            "event:%s", event.event_type,
            extra={"component": "events", "event_id": event.event_id,
                   "event_type": event.event_type, **event.payload},
        )


class WebhookEventHandler:
    """
    Built-in handler: HTTP POST events to a webhook URL.

    Usage:
        handler = WebhookEventHandler("https://my-system.com/glassbox-webhook")
        bus.subscribe("decision.blocked", handler.handle)
    """

    def __init__(
        self,
        url:      str,
        headers:  Optional[Dict[str, str]] = None,
        timeout:  int = 5,
        on_types: Optional[List[str]] = None,
    ):
        self.url      = url
        self.headers  = headers or {"Content-Type": "application/json"}
        self.timeout  = timeout
        self.on_types = set(on_types) if on_types else None

    def handle(self, event: GlassBoxEvent) -> None:
        if self.on_types and event.event_type not in self.on_types:
            return
        try:
            import urllib.request
            data = json.dumps(event.to_dict()).encode()
            req  = urllib.request.Request(
                self.url, data=data, headers=self.headers, method="POST"
            )
            urllib.request.urlopen(req, timeout=self.timeout)
        except Exception as exc:
            import logging
            logging.getLogger("glassbox.events").warning(
                "WebhookHandler: POST to %s failed: %s", self.url, exc
            )


# ── Global default event bus ──────────────────────────────────────────────────

_default_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Return (or create) the global singleton event bus."""
    global _default_bus
    if _default_bus is None:
        with _bus_lock:
            if _default_bus is None:
                _default_bus = EventBus()
    return _default_bus
