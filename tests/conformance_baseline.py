"""Shared ``BaselineStore`` conformance suite (GB-022).

One behavioural specification for every baseline backend, for the reason
named in the plan review: v1's Redis path used exponential forgetting while
its local path used a sliding window, so the two disagreed about what was
anomalous depending on where the code ran. Both backends here must agree on
every case in this file.

Not named ``test_*.py``, so the abstract class is not collected. Each adapter
subclasses :class:`BaselineStoreConformance` and supplies a ``store_factory``
fixture: a callable of ``(*, min_samples: int) -> BaselineStore``.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from glassbox.domain.limits import Window
from glassbox.ports.baseline import BaselineKey, BaselineScope

StoreFactory = Callable[..., Any]

NOW = 1_760_000_000.0
TENANT = "acme"


def _key(subject: str = "agent.treasury-bot", *, window_seconds: int = 30 * 86_400) -> BaselineKey:
    return BaselineKey(
        tenant_id=TENANT,
        scope=BaselineScope.AGENT,
        subject=subject,
        metric="exposure_monetary",
        window=Window(window_seconds),
    )


class BaselineStoreConformance:
    """Behaviour every ``BaselineStore`` must exhibit."""

    def test_no_baseline_exists_before_any_observation(self, store_factory: StoreFactory) -> None:
        store = store_factory(min_samples=5)
        assert store.get(_key(), now=NOW) is None

    def test_observations_build_a_baseline(self, store_factory: StoreFactory) -> None:
        store = store_factory(min_samples=5)
        for value in (98.0, 99.0, 100.0, 101.0, 102.0):
            store.observe(_key(), value, now=NOW)
        baseline = store.get(_key(), now=NOW)
        assert baseline is not None
        assert baseline.sample_count == 5
        assert baseline.mean == pytest.approx(100.0)

    def test_cold_start_uses_the_peer_group_prior(self, store_factory: StoreFactory) -> None:
        """Regression: v1's first ten observations for a new agent were never
        flagged. A subject with too little history must use the peer prior."""
        store = store_factory(min_samples=10)
        peer_key = _key("peer-group-treasury")
        for value in (95.0, 100.0, 105.0, 98.0, 102.0, 97.0, 103.0, 99.0, 101.0, 100.0):
            store.observe(peer_key, value, now=NOW)

        verdict = store.evaluate(
            _key("agent.brand-new"),
            observation=1_000_000_000_000.0,
            peer_group="peer-group-treasury",
            threshold=3.0,
            now=NOW,
        )
        assert verdict.used_peer_prior is True
        assert verdict.anomalous is True

    def test_a_subject_with_no_prior_at_all_is_treated_as_anomalous(
        self, store_factory: StoreFactory
    ) -> None:
        """Detection is never skipped for lack of a prior."""
        store = store_factory(min_samples=5)
        verdict = store.evaluate(
            _key("agent.never-seen"),
            observation=100.0,
            peer_group="peer-group-unknown",
            threshold=3.0,
            now=NOW,
        )
        assert verdict.anomalous is True
        assert verdict.sample_count == 0

    def test_a_typical_observation_is_not_anomalous(self, store_factory: StoreFactory) -> None:
        store = store_factory(min_samples=5)
        key = _key()
        for value in (95.0, 100.0, 105.0, 98.0, 102.0, 97.0, 103.0, 99.0, 101.0, 100.0):
            store.observe(key, value, now=NOW)
        verdict = store.evaluate(key, observation=101.0, peer_group="peers", threshold=3.0, now=NOW)
        assert verdict.anomalous is False
        assert verdict.used_peer_prior is False

    def test_a_far_outlying_observation_is_anomalous(self, store_factory: StoreFactory) -> None:
        store = store_factory(min_samples=5)
        key = _key()
        for value in (99.0, 100.0, 101.0, 100.0, 99.0, 101.0, 100.0, 99.0, 100.0, 101.0):
            store.observe(key, value, now=NOW)
        verdict = store.evaluate(
            key, observation=50_000_000.0, peer_group="peers", threshold=3.0, now=NOW
        )
        assert verdict.anomalous is True

    def test_evaluate_does_not_consume_the_observation(self, store_factory: StoreFactory) -> None:
        store = store_factory(min_samples=5)
        key = _key()
        for value in (100.0, 100.0, 100.0, 100.0, 100.0):
            store.observe(key, value, now=NOW)
        store.evaluate(key, observation=999.0, peer_group="peers", threshold=3.0, now=NOW)
        baseline = store.get(key, now=NOW)
        assert baseline is not None and baseline.sample_count == 5

    def test_an_outage_raises_rather_than_skipping_detection(
        self, store_factory: StoreFactory
    ) -> None:
        store = store_factory(min_samples=5)
        setter = getattr(store, "set_available", None)
        if setter is None:
            pytest.skip("backend does not expose a controllable outage")
        setter(False)
        try:
            from glassbox.domain.errors import BaselineStoreUnavailable

            with pytest.raises(BaselineStoreUnavailable):
                store.observe(_key(), 100.0, now=NOW)
        finally:
            setter(True)
