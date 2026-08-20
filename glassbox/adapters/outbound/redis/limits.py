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

# Bound one tenant's distinct live subjects. Touches (adds/refreshes) this
# subject in the tenant's zset, then evicts the oldest subjects beyond the
# cap -- deleting their window/cost/cooldown keys outright, so Redis memory
# is actually freed rather than merely forgotten. KEYS: [tenant subjects
# zset]. ARGV: [now, subject_prefix, max_subjects].
_TENANT_QUOTA_SCRIPT = """
local subjects_key = KEYS[1]
local now = ARGV[1]
local subject = ARGV[2]
local max_subjects = tonumber(ARGV[3])
redis.call('ZADD', subjects_key, now, subject)
if max_subjects <= 0 then
    return 0
end
local count = redis.call('ZCARD', subjects_key)
local evicted = 0
if count > max_subjects then
    local overflow = count - max_subjects
    local oldest = redis.call('ZRANGE', subjects_key, 0, overflow - 1)
    for _, evicted_subject in ipairs(oldest) do
        redis.call('DEL', evicted_subject .. ':w', evicted_subject .. ':c', evicted_subject .. ':cd')
        redis.call('ZREM', subjects_key, evicted_subject)
        evicted = evicted + 1
    end
end
return evicted
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
        max_tenant_subjects: Upper bound on distinct limit-key subjects one
            tenant may have live at once (F-07). ``0`` disables the bound.
            When exceeded, the oldest-touched subjects for that tenant are
            evicted outright (their Redis keys deleted), bounding one
            tenant's own footprint so it cannot grow without limit and
            trigger `maxmemory` eviction of another tenant's keys.
    """

    __slots__ = (
        "_client",
        "_limits",
        "_default_limit",
        "_cooldown_seconds",
        "_key_prefix",
        "_max_tenant_subjects",
        "_consume",
        "_cumulative",
        "_release",
        "_tenant_quota",
    )

    def __init__(
        self,
        client: Any,
        *,
        limits: Optional[Mapping[str, float]] = None,
        default_limit: float = 100.0,
        cooldown_seconds: float = 300.0,
        key_prefix: str = "",
        max_tenant_subjects: int = 0,
    ) -> None:
        self._client = client
        self._limits: Dict[str, float] = dict(limits or {})
        self._default_limit = default_limit
        self._cooldown_seconds = cooldown_seconds
        self._key_prefix = key_prefix
        self._max_tenant_subjects = max_tenant_subjects
        self._consume = client.register_script(_CONSUME_SCRIPT)
        self._cumulative = client.register_script(_CUMULATIVE_SCRIPT)
        self._release = client.register_script(_RELEASE_SCRIPT)
        self._tenant_quota = client.register_script(_TENANT_QUOTA_SCRIPT)

    def try_consume(
        self, key: LimitKey, *, cost: float, decision_id: str, now: float
    ) -> LimitVerdict:
        """Atomically consume ``cost`` from the counter if budget remains."""
        canonical = key.canonical_key()
        member = LimitKey.member_for(decision_id, now)
        ceiling = self._limits.get(canonical, self._default_limit)
        horizon = key.window.start_of(now)
        ttl = int(key.window.seconds + self._cooldown_seconds + _TTL_PADDING_SECONDS)
        prefixed = self._prefixed(key)
        try:
            if self._max_tenant_subjects:
                self._tenant_quota(
                    keys=[self._tenant_subjects_key(key)],
                    args=[now, prefixed, self._max_tenant_subjects],
                )
            admitted, observed, limit, retry_after, cooldown_until = self._consume(
                keys=[f"{prefixed}:w", f"{prefixed}:c", f"{prefixed}:cd"],
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
            total = self._cumulative(keys=self._keys(key), args=[horizon])
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
            self._release(keys=self._keys(key), args=[suffix])
        except Exception as exc:  # noqa: BLE001 -- any backend failure fails closed
            raise LimitStoreUnavailable(
                "redis limit store is unreachable",
                key=canonical,
                adapter="RedisLimitStore",
            ) from exc

    def configure(self, key: LimitKey, ceiling: float) -> None:
        """Set the ceiling for one counter."""
        self._limits[key.canonical_key()] = ceiling

    def _keys(self, key: LimitKey) -> list:
        """Redis keys include a tenant hash tag so one tenant's keys stay colocated.

        Redis Cluster uses the substring between the first "{" and the first "}"
        as the hash tag, preventing cross-tenant key collisions while keeping the
        canonical key itself unchanged for downstream evidence and tests.
        """
        prefixed = self._prefixed(key)
        return [f"{prefixed}:w", f"{prefixed}:c", f"{prefixed}:cd"]

    def _prefixed(self, key: LimitKey) -> str:
        return f"{self._key_prefix}{{{key.tenant_id}}}:{key.canonical_key()}"

    def _tenant_subjects_key(self, key: LimitKey) -> str:
        """The zset tracking every subject (canonical key) live for this tenant."""
        return f"{self._key_prefix}{{{key.tenant_id}}}:tenant_subjects"


def build_limit_store(config: GlassBoxConfig) -> RedisLimitStore:
    """Factory used by a durable adapter set.

    Connects through Redis Sentinel when ``config.limits.sentinel_hosts`` is
    set -- discovering and following the current master rather than depending
    on one fixed instance -- and falls back to a plain ``url`` connection
    otherwise. Making Sentinel opt-in keeps every existing single-instance
    deployment unaffected.

    Raises:
        glassbox.app.errors.CompositionError: Indirectly, via
            :class:`~glassbox.app.composition.AdapterSet` conformance checking,
            if ``redis`` is not installed or neither ``sentinel_hosts`` nor
            ``config.limits.url`` is set.
    """
    import redis  # local import: `redis` is an optional extra

    limits = config.limits
    if limits.sentinel_hosts:
        from redis.sentinel import Sentinel

        sentinel = Sentinel(
            list(limits.sentinel_hosts),
            socket_timeout=limits.sentinel_socket_timeout_s,
        )
        client = sentinel.master_for(limits.sentinel_service_name, decode_responses=True)
    else:
        client = redis.Redis.from_url(limits.url, decode_responses=True)
    return RedisLimitStore(
        client,
        cooldown_seconds=float(limits.cooldown_seconds),
        max_tenant_subjects=limits.max_tenant_subjects,
    )
