"""Tests for Redis Sentinel HA wiring (GB-011 follow-up, Workstream B).

Real Redis/Sentinel integration is deliberately out of scope here -- exactly
like ``tests/test_redis_limits.py``, a protocol fake would only prove the fake
agrees with itself. What these tests verify instead is the *wiring*: given
Sentinel config, the factory builds a ``redis.sentinel.Sentinel`` with the
right hosts and asks it for the right master, rather than connecting to a
fixed single instance. That is real, deterministic, non-network behaviour.
"""

from __future__ import annotations

from typing import Tuple
from unittest.mock import MagicMock, patch

from glassbox.adapters.outbound.redis.baseline import build_baseline_store
from glassbox.adapters.outbound.redis.limits import build_limit_store
from glassbox.app.config import BaselineConfig, GlassBoxConfig, LimitsConfig, RuntimeProfile


def _config(
    *, limits: LimitsConfig = None, baseline: BaselineConfig = None
) -> GlassBoxConfig:
    return GlassBoxConfig(
        profile=RuntimeProfile.DEV,
        limits=limits if limits is not None else LimitsConfig(url="redis://x"),
        baseline=baseline if baseline is not None else BaselineConfig(url="redis://x"),
    )


class TestLimitStoreSentinelWiring:
    def test_without_sentinel_hosts_connects_by_url(self) -> None:
        config = _config(limits=LimitsConfig(url="redis://plain-instance:6379/0"))
        with patch("redis.Redis.from_url") as from_url:
            from_url.return_value = MagicMock()
            build_limit_store(config)
        from_url.assert_called_once_with("redis://plain-instance:6379/0", decode_responses=True)

    def test_with_sentinel_hosts_discovers_the_master(self) -> None:
        hosts: Tuple[Tuple[str, int], ...] = (("sentinel-1", 26379), ("sentinel-2", 26379))
        config = _config(
            limits=LimitsConfig(sentinel_hosts=hosts, sentinel_service_name="glassbox-limits")
        )
        with patch("redis.sentinel.Sentinel") as sentinel_cls:
            instance = MagicMock()
            sentinel_cls.return_value = instance
            build_limit_store(config)

        sentinel_cls.assert_called_once()
        called_hosts, called_kwargs = sentinel_cls.call_args
        assert list(called_hosts[0]) == list(hosts)
        assert called_kwargs["socket_timeout"] == config.limits.sentinel_socket_timeout_s
        instance.master_for.assert_called_once_with("glassbox-limits", decode_responses=True)

    def test_sentinel_takes_priority_over_a_configured_url(self) -> None:
        """A stale `url` left in config must not silently bypass Sentinel."""
        config = _config(
            limits=LimitsConfig(
                url="redis://should-be-ignored:6379",
                sentinel_hosts=(("sentinel-1", 26379),),
                sentinel_service_name="glassbox-limits",
            )
        )
        with patch("redis.Redis.from_url") as from_url, patch("redis.sentinel.Sentinel") as sentinel_cls:
            sentinel_cls.return_value = MagicMock()
            build_limit_store(config)
        from_url.assert_not_called()
        sentinel_cls.assert_called_once()


class TestBaselineStoreSentinelWiring:
    def test_without_sentinel_hosts_connects_by_url(self) -> None:
        config = _config(baseline=BaselineConfig(url="redis://plain-instance:6379/1"))
        with patch("redis.Redis.from_url") as from_url:
            from_url.return_value = MagicMock()
            build_baseline_store(config)
        from_url.assert_called_once_with("redis://plain-instance:6379/1", decode_responses=True)

    def test_with_sentinel_hosts_discovers_the_master(self) -> None:
        hosts: Tuple[Tuple[str, int], ...] = (("sentinel-1", 26379),)
        config = _config(
            baseline=BaselineConfig(sentinel_hosts=hosts, sentinel_service_name="glassbox-baseline")
        )
        with patch("redis.sentinel.Sentinel") as sentinel_cls:
            instance = MagicMock()
            sentinel_cls.return_value = instance
            build_baseline_store(config)

        instance.master_for.assert_called_once_with("glassbox-baseline", decode_responses=True)
