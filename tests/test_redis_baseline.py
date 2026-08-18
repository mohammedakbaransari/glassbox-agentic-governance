"""Tests for the Redis distributed baseline store (GB-022).

**Integration tests.** Gated behind ``GLASSBOX_REDIS_URL``. Set it to run:

    $env:GLASSBOX_REDIS_URL = "redis://localhost:6379/0"
    python -m pytest tests/test_redis_baseline.py -q
"""

from __future__ import annotations

import os
import uuid

import pytest

from glassbox.domain.errors import BaselineStoreUnavailable
from glassbox.domain.limits import Window
from glassbox.ports.baseline import BaselineKey, BaselineScope
from tests.conformance_baseline import BaselineStoreConformance

REDIS_URL = os.environ.get("GLASSBOX_REDIS_URL", "")

_requires_redis = pytest.mark.skipif(
    not REDIS_URL, reason="set GLASSBOX_REDIS_URL to run the Redis integration tests"
)


@_requires_redis
class TestRedisBaselineStoreConformance(BaselineStoreConformance):
    """The Redis adapter must satisfy the shared port specification."""

    @pytest.fixture
    def store_factory(self):
        from glassbox.adapters.outbound.redis import RedisBaselineStore

        prefix = f"test:{uuid.uuid4().hex}:"
        clients = []

        def factory(*, min_samples: int) -> RedisBaselineStore:
            import redis

            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            clients.append(client)
            return RedisBaselineStore(client, min_samples=min_samples, key_prefix=prefix)

        yield factory

        for client in clients:
            client.close()


@_requires_redis
class TestRedisBaselineStoreOutage:
    """Behaviour only a broken connection can prove."""

    def test_an_unreachable_server_raises(self) -> None:
        import redis

        from glassbox.adapters.outbound.redis import RedisBaselineStore

        client = redis.Redis.from_url(
            "redis://localhost:1", socket_connect_timeout=0.2, socket_timeout=0.2
        )
        store = RedisBaselineStore(client)
        key = BaselineKey(
            tenant_id="acme",
            scope=BaselineScope.AGENT,
            subject="agent.x",
            metric="exposure_monetary",
            window=Window(60),
        )
        with pytest.raises(BaselineStoreUnavailable):
            store.observe(key, 100.0, now=1_760_000_000.0)
