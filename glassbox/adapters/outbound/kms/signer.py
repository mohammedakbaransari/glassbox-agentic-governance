"""KMS-backed evidence signer (GB-006).

Completes fundamental problem **F3**. v1's chain was an unkeyed SHA-256, so
anyone able to write an evidence row could recompute the chain -- the review
measured a forged record re-verifying as ``intact``. GB-005 made the chain keyed;
this card puts the key somewhere the writer cannot reach.

**The property.** :class:`KmsMacSigner` holds no key material at any point. Every
MAC is computed inside the key service. Compromising the application process, the
database, or a backup of either yields no ability to forge. That is asserted, not
merely claimed: ``tests/test_kms_signer.py`` walks the signer's attributes and
fails if anything that could serve as key material is found.

**Rotation.** ``key_id`` is recorded on every evidence row. Signing uses the
current key; verification uses whichever key signed the record. Rotating
therefore never invalidates history, and a record signed under a retired key
keeps verifying until that key is destroyed -- at which point the segment becomes
``UNVERIFIABLE``, which is the truthful answer rather than ``BROKEN``.

**Availability.** A key service on the critical path is a real risk (register R4).
Three mitigations, none of which ever degrades to an unkeyed digest:

* a **bounded MAC cache** -- the MAC of a payload is a deterministic function, so
  caching it is sound and makes retries and idempotent re-appends free;
* a **circuit breaker** that fails fast while the service is down, with half-open
  recovery. v1's ``_store_ok = False`` was never reset, so one transient blip
  degraded a component permanently;
* **fail closed**. When the service is unavailable, ``mac`` raises and the caller
  does not dispatch. Writing unkeyed evidence is not an available outcome.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

from glassbox.adapters.outbound.kms.client import (
    KeyMetadata,
    KmsClient,
    KmsKeyDisabledError,
    KmsUnavailableError,
)
from glassbox.adapters.outbound.signing import mac_message
from glassbox.domain.errors import SigningUnavailableError

__all__ = ["KmsMacSigner", "CircuitBreaker", "DEFAULT_MAC_CACHE_SIZE"]

#: Bounded so a long-running process cannot grow without limit. Entries are keyed
#: by ``(key_id, message)``; the message is already a fixed 57 bytes.
DEFAULT_MAC_CACHE_SIZE = 4096


class CircuitBreaker:
    """Fails fast while a dependency is down, and probes for recovery.

    Args:
        failure_threshold: Consecutive failures before the circuit opens.
        reset_after_s: How long to stay open before allowing one probe.
        monotonic: Injected time source, so the recovery behaviour is testable
            without sleeping.
    """

    __slots__ = ("_threshold", "_reset_after", "_monotonic", "_lock", "_failures", "_opened_at")

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_after_s: float = 10.0,
        monotonic: Optional[object] = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._threshold = failure_threshold
        self._reset_after = reset_after_s
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._failures = 0
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        """Whether calls are currently being rejected without an attempt."""
        with self._lock:
            return self._opened_at is not None and not self._ready_to_probe()

    def _ready_to_probe(self) -> bool:
        """Whether the open period has elapsed and one probe is allowed."""
        if self._opened_at is None:
            return True
        return float(self._monotonic()) - self._opened_at >= self._reset_after  # type: ignore[operator]

    def before_call(self) -> None:
        """Raise if the circuit is open.

        Raises:
            KmsUnavailableError: While the circuit is open. Failing fast here is
                what stops a key-service outage turning into a thread pileup.
        """
        with self._lock:
            if self._opened_at is not None and not self._ready_to_probe():
                raise KmsUnavailableError(
                    "key service circuit is open; failing closed",
                    consecutive_failures=self._failures,
                )

    def record_success(self) -> None:
        """Close the circuit and forget prior failures."""
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        """Count a failure and open the circuit once the threshold is reached."""
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = float(self._monotonic())  # type: ignore[operator]


class KmsMacSigner:
    """Signs and verifies evidence MACs inside a key management service.

    Args:
        client: The key service.
        key_id: Key used for new signatures. Recorded on every evidence row.
        historic_key_ids: Retired keys that must still verify existing records.
        cache_size: Bounded MAC cache. ``0`` disables caching.
        breaker: Circuit breaker. One is created if omitted.
        verify_key_on_start: Describe the key during construction, so an unusable
            key fails the deployment rather than the first evidence write.

    Raises:
        KmsKeyDisabledError: If ``verify_key_on_start`` is set and the key is
            disabled, deleted or not usable for MAC generation.
    """

    __slots__ = (
        "_client",
        "_key_id",
        "_historic",
        "_cache",
        "_cache_size",
        "_lock",
        "_breaker",
        "_metadata",
    )

    def __init__(
        self,
        client: KmsClient,
        key_id: str,
        *,
        historic_key_ids: Tuple[str, ...] = (),
        cache_size: int = DEFAULT_MAC_CACHE_SIZE,
        breaker: Optional[CircuitBreaker] = None,
        verify_key_on_start: bool = True,
    ) -> None:
        if client is None:
            raise SigningUnavailableError("a KMS signer requires a client")
        if not key_id:
            raise SigningUnavailableError("a KMS signer requires a key id")
        self._client = client
        self._key_id = key_id
        self._historic = tuple(historic_key_ids)
        self._cache: "OrderedDict[Tuple[str, bytes], bytes]" = OrderedDict()
        self._cache_size = max(0, cache_size)
        self._lock = threading.RLock()
        self._breaker = breaker or CircuitBreaker()
        self._metadata: Optional[KeyMetadata] = None

        if verify_key_on_start:
            metadata = self._client.describe_key(key_id)
            if not metadata.is_usable:
                raise KmsKeyDisabledError(
                    "the configured evidence signing key cannot be used",
                    key_id=key_id,
                    enabled=metadata.enabled,
                    usable_for_mac=metadata.usable_for_mac,
                    algorithm=metadata.algorithm,
                )
            self._metadata = metadata

    @property
    def key_id(self) -> str:
        """Identifier of the key currently used for signing."""
        return self._key_id

    @property
    def known_key_ids(self) -> Tuple[str, ...]:
        """Every key this signer can verify against, current first, de-duplicated."""
        return tuple(dict.fromkeys((self._key_id,) + self._historic))

    def mac(self, payload: bytes) -> bytes:
        """Return the MAC of ``payload``, computed inside the key service.

        A cache hit is served without contacting the service. That is sound --
        the MAC is a deterministic function of the payload and the key, so a
        cached value is the same value the service would return -- and it means a
        retry of an in-flight decision survives a brief outage. A *new* decision
        has a new ``decision_id``, so it misses the cache and fails closed.

        Raises:
            SigningUnavailableError: If the key service cannot be reached or the
                key is unusable. Callers must fail closed; there is no unkeyed
                fallback.
        """
        message = mac_message(payload)
        cached = self._cache_get(self._key_id, message)
        if cached is not None:
            return cached

        self._breaker.before_call()
        try:
            result = self._client.generate_mac(self._key_id, message)
        except KmsKeyDisabledError:
            # Not transient: opening the circuit would hide a configuration fault
            # behind an availability symptom.
            raise
        except Exception as exc:
            self._breaker.record_failure()
            raise _as_signing_error(exc, key_id=self._key_id, operation="generate_mac") from exc
        self._breaker.record_success()

        if not isinstance(result, (bytes, bytearray)) or len(result) < 32:
            raise SigningUnavailableError(
                "key service returned an unusable MAC",
                key_id=self._key_id,
                length=len(result) if isinstance(result, (bytes, bytearray)) else -1,
            )
        mac = bytes(result)
        self._cache_put(self._key_id, message, mac)
        return mac

    def verify(self, payload: bytes, mac: bytes, *, key_id: str) -> bool:
        """Return whether ``mac`` authenticates ``payload`` under ``key_id``.

        A key this signer does not know is refused rather than verified against
        the current key, which would report a record as authentic on the strength
        of the wrong key.

        Raises:
            SigningUnavailableError: If the key cannot be reached or is unknown.
                The caller reports ``UNVERIFIABLE``, never ``INTACT``.
        """
        if key_id not in self.known_key_ids:
            raise SigningUnavailableError(
                "unknown signing key; the segment is unverifiable",
                key_id=key_id,
                known_key_ids=",".join(self.known_key_ids),
            )
        message = mac_message(payload)

        cached = self._cache_get(key_id, message)
        if cached is not None:
            return hmac.compare_digest(cached, bytes(mac))

        self._breaker.before_call()
        try:
            valid = self._client.verify_mac(key_id, message, bytes(mac))
        except KmsKeyDisabledError:
            raise
        except Exception as exc:
            self._breaker.record_failure()
            raise _as_signing_error(exc, key_id=key_id, operation="verify_mac") from exc
        self._breaker.record_success()
        return bool(valid)

    def with_rotated_key(self, new_key_id: str) -> "KmsMacSigner":
        """Return a signer that signs under ``new_key_id`` and still verifies this one.

        Rotation produces a new signer rather than mutating this one, so a request
        already in flight cannot observe the key changing underneath it. The
        historic list is de-duplicated: re-applying the same rotation must not
        grow the key list without bound.

        Raises:
            SigningUnavailableError: If ``new_key_id`` is empty.
        """
        if not new_key_id:
            raise SigningUnavailableError("rotation requires a key id")
        historic = tuple(
            key_id for key_id in dict.fromkeys(self.known_key_ids) if key_id != new_key_id
        )
        return KmsMacSigner(
            self._client,
            new_key_id,
            historic_key_ids=historic,
            cache_size=self._cache_size,
            breaker=self._breaker,
            verify_key_on_start=False,
        )

    # ----------------------------------------------------------------- #
    # Cache
    # ----------------------------------------------------------------- #

    def _cache_get(self, key_id: str, message: bytes) -> Optional[bytes]:
        """Return a cached MAC, refreshing its recency."""
        if self._cache_size == 0:
            return None
        with self._lock:
            entry = self._cache.get((key_id, message))
            if entry is not None:
                self._cache.move_to_end((key_id, message))
            return entry

    def _cache_put(self, key_id: str, message: bytes, mac: bytes) -> None:
        """Store a MAC, evicting the least recently used entry when full."""
        if self._cache_size == 0:
            return
        with self._lock:
            self._cache[(key_id, message)] = mac
            self._cache.move_to_end((key_id, message))
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    @property
    def cache_size(self) -> int:
        """Number of cached MACs, for memory-bound assertions."""
        with self._lock:
            return len(self._cache)


def _as_signing_error(exc: Exception, *, key_id: str, operation: str) -> SigningUnavailableError:
    """Ensure every failure surfaces as a domain signing error."""
    if isinstance(exc, SigningUnavailableError):
        return exc
    return KmsUnavailableError(
        f"key service {operation} failed",
        key_id=key_id,
        operation=operation,
        cause=type(exc).__name__,
        detail=str(exc),
    )
