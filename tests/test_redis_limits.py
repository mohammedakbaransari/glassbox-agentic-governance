"""Tests for the Redis distributed limit store (GB-011).

**Integration tests.** Gated behind ``GLASSBOX_REDIS_URL``. Only a real server
can prove the atomicity, TTL and cooldown behaviour the Lua scripts depend on;
a mock of the Redis protocol would only prove that the mock agrees with itself.

Set the variable, e.g. ``redis://localhost:6379/0``, to run this file:

    $env:GLASSBOX_REDIS_URL = "redis://localhost:6379/0"
    python -m pytest tests/test_redis_limits.py -q
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from glassbox.domain.errors import LimitStoreUnavailable
from glassbox.domain.limits import LimitKey, LimitScope, Window
from tests.conformance_limits import LimitStoreConformance

REDIS_URL = os.environ.get("GLASSBOX_REDIS_URL", "")

_requires_redis = pytest.mark.skipif(
    not REDIS_URL, reason="set GLASSBOX_REDIS_URL to run the Redis integration tests"
)


@_requires_redis
class TestRedisLimitStoreConformance(LimitStoreConformance):
    """The Redis adapter must satisfy the shared port specification."""

    @pytest.fixture
    def store_factory(self):
        from glassbox.adapters.outbound.redis import RedisLimitStore

        # A fresh key prefix per test isolates counters from every other test
        # and run sharing the same server.
        prefix = f"test:{uuid.uuid4().hex}:"
        clients = []

        def factory(*, default_limit: float, cooldown_seconds: float) -> RedisLimitStore:
            import redis

            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            clients.append(client)
            return RedisLimitStore(
                client,
                default_limit=default_limit,
                cooldown_seconds=cooldown_seconds,
                key_prefix=prefix,
            )

        yield factory

        for client in clients:
            client.close()


@_requires_redis
class TestRedisLimitStoreOutage:
    """Behaviour only a broken connection can prove."""

    def test_an_unreachable_server_raises(self) -> None:
        import redis

        from glassbox.adapters.outbound.redis import RedisLimitStore

        client = redis.Redis.from_url(
            "redis://localhost:1", socket_connect_timeout=0.2, socket_timeout=0.2
        )
        store = RedisLimitStore(client, default_limit=10.0)
        key = LimitKey(
            tenant_id="acme", scope=LimitScope.AGENT, subject="agent.x", window=Window(60)
        )
        with pytest.raises(LimitStoreUnavailable):
            store.try_consume(key, cost=1.0, decision_id="decision-a", now=1_760_000_000.0)


@_requires_redis
class TestRedisLimitStoreTenantQuota:
    """F-07: a tenant's own footprint is bounded, independent of any ceiling."""

    @pytest.fixture
    def store(self):
        import redis

        from glassbox.adapters.outbound.redis import RedisLimitStore

        prefix = f"test:{uuid.uuid4().hex}:"
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        yield RedisLimitStore(
            client, default_limit=100.0, key_prefix=prefix, max_tenant_subjects=2
        )
        client.close()

    def _key(self, subject: str) -> LimitKey:
        return LimitKey(
            tenant_id="acme", scope=LimitScope.AGENT, subject=subject, window=Window(60)
        )

    def test_the_oldest_subject_is_evicted_once_the_cap_is_exceeded(self, store: Any) -> None:
        now = 1_760_000_000.0
        store.try_consume(self._key("agent.a"), cost=5.0, decision_id="d-a", now=now)
        store.try_consume(self._key("agent.b"), cost=5.0, decision_id="d-b", now=now + 1.0)
        # A third distinct subject exceeds max_tenant_subjects=2: agent.a's own
        # window/cost/cooldown keys are deleted outright, not merely capped.
        store.try_consume(self._key("agent.c"), cost=5.0, decision_id="d-c", now=now + 2.0)

        assert store.cumulative(self._key("agent.a"), Window(60), now=now + 2.0) == 0.0
        assert store.cumulative(self._key("agent.b"), Window(60), now=now + 2.0) == 5.0
        assert store.cumulative(self._key("agent.c"), Window(60), now=now + 2.0) == 5.0

    def test_a_disabled_quota_never_evicts(self) -> None:
        import redis

        from glassbox.adapters.outbound.redis import RedisLimitStore

        prefix = f"test:{uuid.uuid4().hex}:"
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        try:
            unbounded = RedisLimitStore(client, default_limit=100.0, key_prefix=prefix)
            now = 1_760_000_000.0
            for index, subject in enumerate(["agent.a", "agent.b", "agent.c"]):
                key = self._key(subject)
                unbounded.try_consume(key, cost=5.0, decision_id=f"d-{index}", now=now + index)
            for subject in ["agent.a", "agent.b", "agent.c"]:
                assert unbounded.cumulative(self._key(subject), Window(60), now=now + 3.0) == 5.0
        finally:
            client.close()
