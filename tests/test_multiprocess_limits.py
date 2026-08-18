"""Multi-process integration test for distributed limits (GB-011, GB-035).

Every other concurrency test for the limit store uses a thread pool -- which
proves the store is thread-safe, but not that N independent *processes*
(the real replica topology in production) never jointly admit more than
``max_decisions``. A thread pool inside one interpreter can share state a
separate process topology cannot; this is the test that only a real,
out-of-process Redis and real OS processes can prove.

Gated behind ``GLASSBOX_REDIS_URL``, the same convention every other real-
backend integration test in this suite uses.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List

import pytest

REDIS_URL = os.environ.get("GLASSBOX_REDIS_URL", "")

_requires_redis = pytest.mark.skipif(
    not REDIS_URL, reason="set GLASSBOX_REDIS_URL to run the multi-process integration test"
)

#: Module-level so it is picklable for ``ProcessPoolExecutor`` (a closure or a
#: bound method cannot be sent to a worker process).
_LIMIT = 50.0
_WINDOW_SECONDS = 60


def _attempt_admission(args: "tuple[str, str, float]") -> bool:
    """Run in a worker process: one admission attempt against real Redis.

    Rebuilding the Redis client here, rather than passing one from the parent,
    is required -- a socket connection is not meaningfully shareable across a
    process boundary.
    """
    redis_url, tenant_id, decision_index = args
    import redis

    from glassbox.adapters.outbound.redis import RedisLimitStore
    from glassbox.domain.limits import LimitKey, LimitScope, Window

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    store = RedisLimitStore(client, default_limit=_LIMIT)
    key = LimitKey(
        tenant_id=tenant_id,
        scope=LimitScope.AGENT,
        subject="agent.multiprocess-test",
        window=Window(_WINDOW_SECONDS),
    )
    verdict = store.try_consume(key, cost=1.0, decision_id=f"decision-{decision_index}", now=0.0)
    return verdict.admitted


@_requires_redis
class TestMultiProcessDistributedLimits:
    """The plan's S4 criterion literally: 3+ replicas, real Redis, admissions
    never exceed the configured ceiling."""

    def test_five_processes_never_jointly_admit_more_than_the_limit(self) -> None:
        tenant_id = f"acme-{uuid.uuid4().hex[:8]}"
        attempts = 200
        args = [(REDIS_URL, tenant_id, i) for i in range(attempts)]

        admitted: List[bool] = []
        with ProcessPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_attempt_admission, arg) for arg in args]
            for future in as_completed(futures):
                admitted.append(future.result())

        assert (
            sum(admitted) == _LIMIT
        ), f"admitted {sum(admitted)} across 5 processes, expected exactly {_LIMIT}"
