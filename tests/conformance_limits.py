"""Shared ``LimitStore`` conformance suite (GB-011).

One behavioural specification for every atomic counter backend, for the same
reason as the evidence and signing suites: v1's in-memory and Redis velocity
breakers disagreed (the in-memory one degraded to fail-open; Redis undercounted
same-tick admissions), and nothing ever proved the two implementations were
equivalent.

Not named ``test_*.py``, so the abstract class is not collected. Each adapter
subclasses :class:`LimitStoreConformance` and supplies a ``store_factory``
fixture: a callable of ``(*, default_limit: float, cooldown_seconds: float) ->
LimitStore`` that returns a fresh, empty store for one test.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List

import pytest

from glassbox.domain.errors import LimitStoreUnavailable
from glassbox.domain.limits import LimitKey, LimitScope, Window

StoreFactory = Callable[..., Any]

NOW = 1_760_000_000.0

TENANT = "acme"
AGENT = "agent.treasury-bot"


def _key(window_seconds: int = 60, *, action: str = "payments.wire_transfer") -> LimitKey:
    return LimitKey(
        tenant_id=TENANT,
        scope=LimitScope.AGENT,
        subject=AGENT,
        window=Window(window_seconds),
        action=action,
    )


class LimitStoreConformance:
    """Behaviour every ``LimitStore`` must exhibit."""

    def test_admissions_stop_exactly_at_the_ceiling(self, store_factory: StoreFactory) -> None:
        store = store_factory(default_limit=3.0, cooldown_seconds=300.0)
        key = _key()
        verdicts = [
            store.try_consume(key, cost=1.0, decision_id=f"decision-{i}", now=NOW + i * 0.001)
            for i in range(5)
        ]
        assert [verdict.admitted for verdict in verdicts] == [True, True, True, False, False]

    def test_same_tick_decisions_are_counted_separately(self, store_factory: StoreFactory) -> None:
        """Regression: v1's ``ZADD key now now`` collapsed same-tick admissions."""
        store = store_factory(default_limit=2.0, cooldown_seconds=300.0)
        key = _key()
        first = store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        second = store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW)
        third = store.try_consume(key, cost=1.0, decision_id="decision-c", now=NOW)
        assert (first.admitted, second.admitted, third.admitted) == (True, True, False)

    def test_a_repeated_decision_id_is_the_same_admission(
        self, store_factory: StoreFactory
    ) -> None:
        store = store_factory(default_limit=1.0, cooldown_seconds=300.0)
        key = _key()
        assert store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW).admitted
        assert store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW).admitted

    def test_cooldown_is_held_by_the_store(self, store_factory: StoreFactory) -> None:
        """Regression: v1 kept the tripped flag locally while counting in Redis,
        so the effective cooldown collapsed to the window length."""
        store = store_factory(default_limit=1.0, cooldown_seconds=300.0)
        key = _key(window_seconds=10)
        store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        tripped = store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW)
        assert tripped.admitted is False
        assert tripped.cooldown_until == pytest.approx(NOW + 300.0)

        still_tripped = store.try_consume(key, cost=1.0, decision_id="decision-c", now=NOW + 20.0)
        assert still_tripped.admitted is False

        recovered = store.try_consume(key, cost=1.0, decision_id="decision-d", now=NOW + 400.0)
        assert recovered.admitted is True

    def test_the_window_slides(self, store_factory: StoreFactory) -> None:
        store = store_factory(default_limit=1.0, cooldown_seconds=0.0)
        key = _key(window_seconds=10)
        assert store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW).admitted
        assert store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW + 20.0).admitted

    def test_release_returns_budget_for_an_abandoned_decision(
        self, store_factory: StoreFactory
    ) -> None:
        store = store_factory(default_limit=1.0, cooldown_seconds=300.0)
        key = _key()
        store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        store.release(key, decision_id="decision-a")
        assert store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW).admitted

    def test_release_is_idempotent(self, store_factory: StoreFactory) -> None:
        store = store_factory(default_limit=1.0, cooldown_seconds=300.0)
        key = _key()
        store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        store.release(key, decision_id="decision-a")
        store.release(key, decision_id="decision-a")  # must not raise

    def test_cumulative_reports_consumption_without_consuming_it(
        self, store_factory: StoreFactory
    ) -> None:
        store = store_factory(default_limit=10.0, cooldown_seconds=300.0)
        key = _key(window_seconds=60)
        store.try_consume(key, cost=2.0, decision_id="decision-a", now=NOW)
        store.try_consume(key, cost=3.0, decision_id="decision-b", now=NOW)
        first = store.cumulative(key, key.window, now=NOW)
        second = store.cumulative(key, key.window, now=NOW)
        assert first == second == 5.0

    def test_cumulative_excludes_expired_entries(self, store_factory: StoreFactory) -> None:
        store = store_factory(default_limit=10.0, cooldown_seconds=300.0)
        key = _key(window_seconds=10)
        store.try_consume(key, cost=4.0, decision_id="decision-a", now=NOW)
        assert store.cumulative(key, key.window, now=NOW + 20.0) == 0.0

    def test_different_actions_are_independent_counters(self, store_factory: StoreFactory) -> None:
        store = store_factory(default_limit=1.0, cooldown_seconds=300.0)
        wire = _key(action="payments.wire_transfer")
        refund = _key(action="payments.refund")
        assert store.try_consume(wire, cost=1.0, decision_id="decision-a", now=NOW).admitted
        assert store.try_consume(refund, cost=1.0, decision_id="decision-b", now=NOW).admitted

    def test_an_outage_raises_rather_than_admitting_everything(
        self, store_factory: StoreFactory
    ) -> None:
        """Regression: v1 failed open at velocity_breaker.py:604."""
        store = store_factory(default_limit=1.0, cooldown_seconds=300.0)
        setter = getattr(store, "set_available", None)
        if setter is None:
            pytest.skip("backend does not expose a controllable outage")
        setter(False)
        try:
            with pytest.raises(LimitStoreUnavailable):
                store.try_consume(_key(), cost=1.0, decision_id="decision-a", now=NOW)
        finally:
            setter(True)

    def test_concurrent_callers_never_exceed_the_limit(self, store_factory: StoreFactory) -> None:
        """The invariant v1's concurrency tests never asserted.

        v1's own test launched 500 threads and only asserted that no exception
        was raised; it never checked that admissions stayed within the ceiling.
        """
        store = store_factory(default_limit=50.0, cooldown_seconds=300.0)
        key = _key()
        admitted: List[bool] = []
        lock = threading.Lock()

        def attempt(index: int) -> None:
            verdict = store.try_consume(key, cost=1.0, decision_id=f"decision-{index:04d}", now=NOW)
            with lock:
                admitted.append(verdict.admitted)

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(attempt, range(500)))

        assert sum(admitted) == 50, f"admitted {sum(admitted)} of a limit of 50"
