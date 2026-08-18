"""Redis-backed distributed limit store (GB-011).

Completes the piece GB-003a's reference adapter marked ``dev_only``: with the
in-memory store, N replicas each enforce the configured ceiling independently,
so N replicas admit N times the intended budget. This adapter holds every
counter, cooldown and idempotency record in Redis, shared by every replica.

Three properties this adapter must preserve, carried over from the reference
implementation it is checked against (``tests/conformance_limits.py``):

* **Atomic check-and-consume.** Reading the window, computing whether budget
  remains, and recording the admission all happen inside one Lua script, which
  Redis executes without interleaving another client's script. A read followed
  by a separate write -- the shape of v1's Redis script -- admits more than the
  limit under concurrency.
* **A collision-free member per admission**
  (:meth:`~glassbox.domain.limits.LimitKey.member_for`). v1's script used the
  timestamp as both the sorted-set score *and* member (``ZADD key now now``),
  so two decisions in the same clock tick collapsed into one and the window
  undercounted. Binding the member to the decision id keeps them distinct, and
  makes a retried ``decision_id`` idempotent rather than double-counted.
* **Cooldown lives in Redis, not in the adapter.** v1 kept ``_tripped`` in a
  process-local flag while counting happened in Redis, so the effective
  cooldown collapsed to the window length the moment a second replica existed.

**Fail closed.** Any Redis error -- connection refused, timeout, a script
error -- raises :class:`~glassbox.domain.errors.LimitStoreUnavailable`. There is
no code path that returns a permissive verdict when Redis cannot be reached.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import LimitStoreUnavailable
from glassbox.domain.limits import LimitKey, LimitVerdict, Window

__all__ = ["RedisLimitStore", "build_limit_store"]

#: Extra seconds of Redis-side TTL beyond the window and cooldown, so a
#: counter that stops being touched is eventually reclaimed rather than kept
#: forever -- the Redis analogue of the in-memory adapter's subject eviction.
_TTL_PADDING_SECONDS = 60

# Atomically: trim expired entries, honour an in-force cooldown, treat a
# repeated member as the same admission, and otherwise admit-and-record or
# trip the cooldown. KEYS: [window zset, cost hash, cooldown key].
# ARGV: [now, horizon, member, cost, ceiling, cooldown_seconds, ttl_seconds].
_CONSUME_SCRIPT = """
local window_key, cost_key, cooldown_key = KEYS[1], KEYS[2], KEYS[3]
local now = tonumber(ARGV[1])
local horizon = tonumber(ARGV[2])
local member = ARGV[3]
local cost = tonumber(ARGV[4])
local ceiling = tonumber(ARGV[5])
local cooldown_seconds = tonumber(ARGV[6])
local ttl = tonumber(ARGV[7])

local function observed()
    local members = redis.call('ZRANGE', window_key, 0, -1)
    if #members == 0 then
        return 0
    end
    local costs = redis.call('HMGET', cost_key, unpack(members))
    local total = 0
    for _, c in ipairs(costs) do
        if c then total = total + tonumber(c) end
    end
    return total
end

local expired = redis.call('ZRANGEBYSCORE', window_key, '-inf', horizon)
if #expired > 0 then
    redis.call('ZREM', window_key, unpack(expired))
    redis.call('HDEL', cost_key, unpack(expired))
end

local cooldown_until = redis.call('GET', cooldown_key)
if cooldown_until then
    cooldown_until = tonumber(cooldown_until)
    if now < cooldown_until then
        return {0, tostring(observed()), tostring(ceiling), tostring(cooldown_until - now), tostring(cooldown_until)}
    end
    redis.call('DEL', cooldown_key)
end

if redis.call('HEXISTS', cost_key, member) == 1 then
    return {1, tostring(observed()), tostring(ceiling), '', ''}
end

local current = observed()
if current + cost > ceiling then
    local trip_until = now + cooldown_seconds
    redis.call('SET', cooldown_key, tostring(trip_until))
    redis.call('EXPIRE', cooldown_key, math.max(1, math.ceil(cooldown_seconds)))
    return {0, tostring(current + cost), tostring(ceiling), tostring(cooldown_seconds), tostring(trip_until)}
end

redis.call('ZADD', window_key, now, member)
redis.call('HSET', cost_key, member, cost)
redis.call('EXPIRE', window_key, ttl)
redis.call('EXPIRE', cost_key, ttl)
return {1, tostring(current + cost), tostring(ceiling), '', ''}
"""

# Read-only: sum entries strictly newer than the horizon, without mutating
# anything. KEYS: [window zset, cost hash]. ARGV: [horizon].
_CUMULATIVE_SCRIPT = """
local window_key, cost_key = KEYS[1], KEYS[2]
local horizon = ARGV[1]
local members = redis.call('ZRANGEBYSCORE', window_key, '(' .. horizon, '+inf')
if #members == 0 then
    return '0'
end
local costs = redis.call('HMGET', cost_key, unpack(members))
local total = 0
for _, c in ipairs(costs) do
    if c then total = total + tonumber(c) end
end
return tostring(total)
"""

# Remove every member ending in ":<decision_id>", regardless of its
# timestamp -- release does not receive `now`, by port contract.
# KEYS: [window zset, cost hash]. ARGV: [suffix].
_RELEASE_SCRIPT = """
local window_key, cost_key = KEYS[1], KEYS[2]
local suffix = ARGV[1]
local all_members = redis.call('ZRANGE', window_key, 0, -1)
local to_remove = {}
for _, m in ipairs(all_members) do
    if string.sub(m, -string.len(suffix)) == suffix then
        table.insert(to_remove, m)
    end
end
if #to_remove > 0 then
    redis.call('ZREM', window_key, unpack(to_remove))
    redis.call('HDEL', cost_key, unpack(to_remove))
end
return #to_remove
"""


class RedisLimitStore:
    """A :class:`~glassbox.ports.limits.LimitStore` shared across every replica.

    Args:
        client: A ``redis.Redis``-compatible client. Only ``register_script`` and
            the script objects' call interface are used, so a compatible fake is
            enough for tests that do not need a live server.
        limits: ``canonical_key -> ceiling``. A key with no configured ceiling
            uses ``default_limit``.
        default_limit: Ceiling applied to unconfigured keys.
        cooldown_seconds: How long a breaker stays tripped after a rejection.
        key_prefix: Prepended to every Redis key, so one server can host
            multiple isolated deployments (or, in tests, multiple test runs).
    """

    __slots__ = (
        "_client",
        "_limits",
        "_default_limit",
        "_cooldown_seconds",
        "_key_prefix",
        "_consume",
        "_cumulative",
        "_release",
    )

    def __init__(
        self,
        client: Any,
        *,
        limits: Optional[Mapping[str, float]] = None,
        default_limit: float = 100.0,
        cooldown_seconds: float = 300.0,
        key_prefix: str = "",
    ) -> None:
        self._client = client
        self._limits: Dict[str, float] = dict(limits or {})
        self._default_limit = default_limit
        self._cooldown_seconds = cooldown_seconds
        self._key_prefix = key_prefix
        self._consume = client.register_script(_CONSUME_SCRIPT)
        self._cumulative = client.register_script(_CUMULATIVE_SCRIPT)
        self._release = client.register_script(_RELEASE_SCRIPT)

    def try_consume(
        self, key: LimitKey, *, cost: float, decision_id: str, now: float
    ) -> LimitVerdict:
        """Atomically consume ``cost`` from the counter if budget remains."""
        canonical = key.canonical_key()
        member = LimitKey.member_for(decision_id, now)
        ceiling = self._limits.get(canonical, self._default_limit)
        horizon = key.window.start_of(now)
        ttl = int(key.window.seconds + self._cooldown_seconds + _TTL_PADDING_SECONDS)
        try:
            admitted, observed, limit, retry_after, cooldown_until = self._consume(
                keys=self._keys(canonical),
                args=[now, horizon, member, cost, ceiling, self._cooldown_seconds, ttl],
            )
        except Exception as exc:  # noqa: BLE001 -- any backend failure fails closed
            raise LimitStoreUnavailable(
                "redis limit store is unreachable",
                key=canonical,
                adapter="RedisLimitStore",
            ) from exc
        return LimitVerdict(
            admitted=bool(int(admitted)),
            key=key,
            limit=float(limit),
            observed=float(observed),
            evaluated_at=now,
            retry_after_s=float(retry_after) if retry_after else None,
            cooldown_until=float(cooldown_until) if cooldown_until else None,
        )

    def cumulative(self, key: LimitKey, window: Window, *, now: float) -> float:
        """Return consumption within ``window`` without consuming budget."""
        canonical = key.canonical_key()
        horizon = window.start_of(now)
        try:
            total = self._cumulative(keys=self._keys(canonical), args=[horizon])
        except Exception as exc:  # noqa: BLE001 -- any backend failure fails closed
            raise LimitStoreUnavailable(
                "redis limit store is unreachable",
                key=canonical,
                adapter="RedisLimitStore",
            ) from exc
        return float(total)

    def release(self, key: LimitKey, *, decision_id: str) -> None:
        """Return budget consumed by a decision that never took effect."""
        canonical = key.canonical_key()
        suffix = f":{decision_id}"
        try:
            self._release(keys=self._keys(canonical), args=[suffix])
        except Exception as exc:  # noqa: BLE001 -- any backend failure fails closed
            raise LimitStoreUnavailable(
                "redis limit store is unreachable",
                key=canonical,
                adapter="RedisLimitStore",
            ) from exc

    def configure(self, key: LimitKey, ceiling: float) -> None:
        """Set the ceiling for one counter."""
        self._limits[key.canonical_key()] = ceiling

    def _keys(self, canonical: str) -> list:
        prefixed = f"{self._key_prefix}{canonical}"
        return [f"{prefixed}:w", f"{prefixed}:c", f"{prefixed}:cd"]


def build_limit_store(config: GlassBoxConfig) -> RedisLimitStore:
    """Factory used by a durable adapter set.

    Raises:
        glassbox.app.errors.CompositionError: Indirectly, via
            :class:`~glassbox.app.composition.AdapterSet` conformance checking,
            if ``redis`` is not installed or ``config.limits.url`` is empty.
    """
    import redis  # local import: `redis` is an optional extra

    client = redis.Redis.from_url(config.limits.url, decode_responses=True)
    return RedisLimitStore(client, cooldown_seconds=float(config.limits.cooldown_seconds))
