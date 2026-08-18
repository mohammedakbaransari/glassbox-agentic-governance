"""Tests for the KMS evidence signer (GB-006).

The headline assertion is :meth:`TestNoLocalKeyMaterial.test_the_signer_holds_no_key_material`:
the signer's entire object graph is walked and the test fails if anything that
could serve as key material is found. That is the property the card exists for --
v1's chain was an unkeyed SHA-256, so the review measured a forged record
re-verifying as ``intact``, and a keyed chain whose key sits in the same process
as the writer only narrows the problem rather than removing it.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from glassbox.adapters.outbound.kms.client import (
    MAC_ALGORITHM,
    KeyMetadata,
    KmsClient,
    KmsKeyDisabledError,
    KmsUnavailableError,
)
from glassbox.adapters.outbound.kms.signer import CircuitBreaker, KmsMacSigner
from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.signing import MAC_DOMAIN_TAG, mac_message
from glassbox.domain.errors import SigningUnavailableError
from glassbox.ports.keys import MacSigner
from tests.conformance_signing import OTHER_PAYLOAD, PAYLOAD, MacSignerConformance

# --------------------------------------------------------------------------- #
# A fake key service
# --------------------------------------------------------------------------- #


class FakeKmsClient:
    """An in-process stand-in for a key service.

    Key material lives *here*, standing in for the service boundary. The signer
    under test never receives it, which is exactly the arrangement the real
    adapter has.
    """

    def __init__(self, keys: Optional[Dict[str, bytes]] = None) -> None:
        self.keys: Dict[str, bytes] = dict(keys or {"key/current": b"\x01" * 32})
        self.disabled: Set[str] = set()
        self.available = True
        self.calls: List[Tuple[str, str]] = []
        self.failures = 0
        self._lock = threading.RLock()

    def _guard(self, key_id: str, operation: str) -> bytes:
        with self._lock:
            self.calls.append((operation, key_id))
        if not self.available:
            self.failures += 1
            raise KmsUnavailableError("simulated key service outage", key_id=key_id)
        if key_id in self.disabled:
            raise KmsKeyDisabledError("key is disabled", key_id=key_id)
        material = self.keys.get(key_id)
        if material is None:
            raise KmsKeyDisabledError("no such key", key_id=key_id)
        return material

    def generate_mac(self, key_id: str, message: bytes) -> bytes:
        import hashlib
        import hmac

        return hmac.new(self._guard(key_id, "generate_mac"), message, hashlib.sha256).digest()

    def verify_mac(self, key_id: str, message: bytes, mac: bytes) -> bool:
        import hashlib
        import hmac

        expected = hmac.new(self._guard(key_id, "verify_mac"), message, hashlib.sha256).digest()
        return hmac.compare_digest(expected, bytes(mac))

    def describe_key(self, key_id: str) -> KeyMetadata:
        with self._lock:
            self.calls.append(("describe_key", key_id))
        if not self.available:
            raise KmsUnavailableError("simulated key service outage", key_id=key_id)
        return KeyMetadata(
            key_id=key_id,
            algorithm=MAC_ALGORITHM,
            enabled=key_id in self.keys and key_id not in self.disabled,
            usable_for_mac=True,
        )

    def set_available(self, available: bool) -> None:
        self.available = available


class ControllableKmsSigner(KmsMacSigner):
    """A signer whose backing service outage can be driven from a test."""

    def set_available(self, available: bool) -> None:
        self._client.set_available(available)  # type: ignore[attr-defined]


def kms_signer(**kwargs: Any) -> ControllableKmsSigner:
    """Build a signer over a fresh fake key service."""
    client = FakeKmsClient()
    return ControllableKmsSigner(client, "key/current", **kwargs)


# --------------------------------------------------------------------------- #
# Conformance
# --------------------------------------------------------------------------- #


class TestKmsSignerConformance(MacSignerConformance):
    """The KMS signer must satisfy the shared port specification."""

    @pytest.fixture
    def signer(self) -> ControllableKmsSigner:
        return kms_signer(cache_size=0)

    @pytest.fixture
    def independent_signer(self) -> KmsMacSigner:
        client = FakeKmsClient({"key/other": b"\x02" * 32})
        return KmsMacSigner(client, "key/other", cache_size=0)


class TestLocalSignerConformance(MacSignerConformance):
    """The local reference signer is held to the same specification."""

    @pytest.fixture
    def signer(self) -> LocalMacSigner:
        return LocalMacSigner(key_id="local.key", key=b"\x11" * 32)

    @pytest.fixture
    def independent_signer(self) -> LocalMacSigner:
        return LocalMacSigner(key_id="local.key", key=b"\x22" * 32)


# --------------------------------------------------------------------------- #
# The core guarantee
# --------------------------------------------------------------------------- #


class TestNoLocalKeyMaterial:
    """The application must be unable to forge, not merely disinclined to."""

    @staticmethod
    def _reachable_bytes(root: Any, *, depth: int = 4) -> List[bytes]:
        """Collect every ``bytes`` value reachable from an object's attributes."""
        found: List[bytes] = []
        seen: Set[int] = set()

        def walk(value: Any, level: int) -> None:
            if level > depth or id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, (bytes, bytearray)):
                found.append(bytes(value))
                return
            if isinstance(value, (list, tuple, set, frozenset)):
                for item in value:
                    walk(item, level + 1)
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(key, level + 1)
                    walk(item, level + 1)
                return
            for slot in getattr(type(value), "__slots__", ()) or ():
                walk(getattr(value, slot, None), level + 1)
            walk(getattr(value, "__dict__", None), level + 1)

        walk(root, 0)
        return found

    def test_the_signer_holds_no_key_material(self) -> None:
        """The acceptance criterion of GB-006, asserted structurally.

        Every ``bytes`` reachable from the signer is collected and each is tried
        as an HMAC key against a real MAC. If any of them reproduces it, the
        application can forge and the guarantee is void.
        """
        import hashlib
        import hmac

        signer = kms_signer(cache_size=0)
        # The client stands in for the service boundary; exclude it, since in the
        # real adapter it holds only an SDK handle.
        signer_only = {
            slot: getattr(signer, slot, None)
            for slot in KmsMacSigner.__slots__
            if slot != "_client"
        }

        authentic = signer.mac(PAYLOAD)
        message = mac_message(PAYLOAD)
        for candidate in self._reachable_bytes(signer_only):
            forged = hmac.new(candidate, message, hashlib.sha256).digest()
            assert forged != authentic, (
                "the signer holds material that reproduces a valid MAC; "
                "the application can forge evidence"
            )

    def test_the_client_protocol_offers_no_way_to_read_a_key(self) -> None:
        """A client that could export the key would defeat the whole arrangement."""
        surface = {name for name in dir(KmsClient) if not name.startswith("_")}
        assert surface == {"generate_mac", "verify_mac", "describe_key"}

    def test_forging_with_application_available_material_fails_verification(self) -> None:
        """End to end: rewrite the payload, recompute with what the app has, verify."""
        import hashlib
        import hmac

        signer = kms_signer(cache_size=0)
        authentic = signer.mac(PAYLOAD)
        assert signer.verify(PAYLOAD, authentic, key_id=signer.key_id) is True

        forged = hmac.new(b"\x00" * 32, mac_message(OTHER_PAYLOAD), hashlib.sha256).digest()
        assert signer.verify(OTHER_PAYLOAD, forged, key_id=signer.key_id) is False


# --------------------------------------------------------------------------- #
# Rotation
# --------------------------------------------------------------------------- #


class TestRotation:
    """Rotating a key must never invalidate history."""

    def test_a_rotated_signer_still_verifies_the_previous_key(self) -> None:
        client = FakeKmsClient({"key/v1": b"\x01" * 32, "key/v2": b"\x02" * 32})
        first = KmsMacSigner(client, "key/v1", cache_size=0)
        old_mac = first.mac(PAYLOAD)

        second = first.with_rotated_key("key/v2")
        assert second.key_id == "key/v2"
        assert second.verify(PAYLOAD, old_mac, key_id="key/v1") is True

    def test_a_rotated_signer_signs_under_the_new_key(self) -> None:
        client = FakeKmsClient({"key/v1": b"\x01" * 32, "key/v2": b"\x02" * 32})
        first = KmsMacSigner(client, "key/v1", cache_size=0)
        second = first.with_rotated_key("key/v2")
        assert first.mac(PAYLOAD) != second.mac(PAYLOAD)

    def test_rotation_does_not_mutate_the_existing_signer(self) -> None:
        """A request already in flight must not see the key change underneath it."""
        client = FakeKmsClient({"key/v1": b"\x01" * 32, "key/v2": b"\x02" * 32})
        first = KmsMacSigner(client, "key/v1", cache_size=0)
        first.with_rotated_key("key/v2")
        assert first.key_id == "key/v1"

    def test_known_keys_are_reported_current_first(self) -> None:
        client = FakeKmsClient({"key/v1": b"\x01" * 32, "key/v2": b"\x02" * 32})
        signer = KmsMacSigner(client, "key/v1", cache_size=0).with_rotated_key("key/v2")
        assert signer.known_key_ids == ("key/v2", "key/v1")

    def test_rotating_to_the_same_key_does_not_duplicate_it(self) -> None:
        """Otherwise a re-applied rotation grows the key list without bound."""
        client = FakeKmsClient({"key/v1": b"\x01" * 32})
        signer = KmsMacSigner(client, "key/v1", cache_size=0)
        assert signer.with_rotated_key("key/v1").known_key_ids == ("key/v1",)

    def test_repeated_rotation_does_not_accumulate_duplicates(self) -> None:
        client = FakeKmsClient(
            {"key/v1": b"\x01" * 32, "key/v2": b"\x02" * 32, "key/v3": b"\x03" * 32}
        )
        signer = KmsMacSigner(client, "key/v1", cache_size=0)
        for key_id in ("key/v2", "key/v3", "key/v2", "key/v3"):
            signer = signer.with_rotated_key(key_id)
        assert len(signer.known_key_ids) == len(set(signer.known_key_ids))

    def test_rotation_requires_a_key_id(self) -> None:
        signer = KmsMacSigner(FakeKmsClient(), "key/current", cache_size=0)
        with pytest.raises(SigningUnavailableError):
            signer.with_rotated_key("")

    def test_a_destroyed_key_makes_records_unverifiable_not_broken(self) -> None:
        """The truthful answer when a key is gone is 'cannot tell', not 'forged'."""
        client = FakeKmsClient({"key/v1": b"\x01" * 32})
        signer = KmsMacSigner(client, "key/v1", cache_size=0)
        mac = signer.mac(PAYLOAD)
        client.disabled.add("key/v1")
        with pytest.raises(SigningUnavailableError):
            signer.verify(PAYLOAD, mac, key_id="key/v1")


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


class TestFailClosed:
    """A key service outage denies decisions; it never writes unkeyed evidence."""

    def test_an_outage_raises_from_mac(self) -> None:
        signer = kms_signer(cache_size=0)
        signer.set_available(False)
        with pytest.raises(SigningUnavailableError):
            signer.mac(PAYLOAD)

    def test_an_outage_raises_from_verify(self) -> None:
        signer = kms_signer(cache_size=0)
        signer.mac(PAYLOAD)
        signer.set_available(False)
        with pytest.raises(SigningUnavailableError):
            signer.verify(PAYLOAD, b"\x00" * 32, key_id=signer.key_id)

    def test_a_disabled_key_fails_at_construction(self) -> None:
        """An unusable key must fail the deployment, not the first decision."""
        client = FakeKmsClient()
        client.disabled.add("key/current")
        with pytest.raises(KmsKeyDisabledError):
            KmsMacSigner(client, "key/current")

    def test_a_signer_requires_a_client_and_a_key_id(self) -> None:
        with pytest.raises(SigningUnavailableError):
            KmsMacSigner(None, "key/current")  # type: ignore[arg-type]
        with pytest.raises(SigningUnavailableError):
            KmsMacSigner(FakeKmsClient(), "")

    def test_a_short_mac_from_the_service_is_refused(self) -> None:
        class TruncatingClient(FakeKmsClient):
            def generate_mac(self, key_id: str, message: bytes) -> bytes:
                return super().generate_mac(key_id, message)[:16]

        signer = KmsMacSigner(TruncatingClient(), "key/current", cache_size=0)
        with pytest.raises(SigningUnavailableError):
            signer.mac(PAYLOAD)


class TestCircuitBreaker:
    """One transient blip must not degrade the component permanently."""

    class _Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    def test_the_circuit_opens_after_repeated_failures(self) -> None:
        clock = self._Clock()
        breaker = CircuitBreaker(failure_threshold=3, reset_after_s=10.0, monotonic=clock)
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_open is True
        with pytest.raises(KmsUnavailableError):
            breaker.before_call()

    def test_the_circuit_half_opens_after_the_reset_period(self) -> None:
        """Regression: v1's ``_store_ok = False`` was never reset."""
        clock = self._Clock()
        breaker = CircuitBreaker(failure_threshold=1, reset_after_s=10.0, monotonic=clock)
        breaker.record_failure()
        assert breaker.is_open is True

        clock.value = 11.0
        assert breaker.is_open is False
        breaker.before_call()

    def test_success_closes_the_circuit(self) -> None:
        clock = self._Clock()
        breaker = CircuitBreaker(failure_threshold=2, reset_after_s=10.0, monotonic=clock)
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        assert breaker.is_open is False

    def test_a_signer_recovers_after_the_service_returns(self) -> None:
        clock = self._Clock()
        signer = kms_signer(
            cache_size=0,
            breaker=CircuitBreaker(failure_threshold=2, reset_after_s=5.0, monotonic=clock),
        )
        signer.set_available(False)
        for _ in range(2):
            with pytest.raises(SigningUnavailableError):
                signer.mac(PAYLOAD)

        signer.set_available(True)
        clock.value = 6.0
        assert len(signer.mac(PAYLOAD)) == 32

    def test_a_disabled_key_does_not_open_the_circuit(self) -> None:
        """A configuration fault must not be hidden behind an availability symptom."""
        client = FakeKmsClient()
        breaker = CircuitBreaker(failure_threshold=1, reset_after_s=10.0)
        signer = KmsMacSigner(
            client, "key/current", cache_size=0, breaker=breaker, verify_key_on_start=False
        )
        client.disabled.add("key/current")
        with pytest.raises(KmsKeyDisabledError):
            signer.mac(PAYLOAD)
        assert breaker.is_open is False

    def test_an_invalid_threshold_is_refused(self) -> None:
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=0)


class TestMacCache:
    """Caching a deterministic function is sound, and it must stay bounded."""

    def test_a_repeated_payload_does_not_call_the_service_twice(self) -> None:
        client = FakeKmsClient()
        signer = KmsMacSigner(client, "key/current", cache_size=16)
        signer.mac(PAYLOAD)
        before = len([call for call in client.calls if call[0] == "generate_mac"])
        signer.mac(PAYLOAD)
        after = len([call for call in client.calls if call[0] == "generate_mac"])
        assert after == before

    def test_the_cache_returns_the_same_mac_as_the_service(self) -> None:
        signer = kms_signer(cache_size=16)
        assert signer.mac(PAYLOAD) == signer.mac(PAYLOAD)

    def test_the_cache_is_bounded(self) -> None:
        signer = kms_signer(cache_size=8)
        for index in range(200):
            signer.mac(f"payload-{index}".encode())
        assert signer.cache_size <= 8

    def test_caching_can_be_disabled(self) -> None:
        client = FakeKmsClient()
        signer = KmsMacSigner(client, "key/current", cache_size=0)
        signer.mac(PAYLOAD)
        signer.mac(PAYLOAD)
        assert len([call for call in client.calls if call[0] == "generate_mac"]) == 2

    def test_a_cached_verify_still_rejects_a_bad_mac(self) -> None:
        """The cache must never turn into a way to launder an invalid MAC."""
        signer = kms_signer(cache_size=16)
        signer.mac(PAYLOAD)
        assert signer.verify(PAYLOAD, b"\x00" * 32, key_id=signer.key_id) is False

    def test_a_cached_payload_survives_a_brief_outage(self) -> None:
        """Deliberate: a retry of an in-flight decision should not be lost.

        The MAC is a deterministic function of payload and key, so a cached value
        is exactly what the service would return. No security property is
        weakened -- the value was still produced inside the key service.
        """
        signer = kms_signer(cache_size=16)
        first = signer.mac(PAYLOAD)
        signer.set_available(False)
        assert signer.mac(PAYLOAD) == first

    def test_a_new_payload_still_fails_closed_during_an_outage(self) -> None:
        """The other half of the trade-off: a *new* decision cannot slip through.

        A new decision carries a new ``decision_id``, so it misses the cache.
        """
        signer = kms_signer(cache_size=16)
        signer.mac(PAYLOAD)
        signer.set_available(False)
        with pytest.raises(SigningUnavailableError):
            signer.mac(OTHER_PAYLOAD)


# --------------------------------------------------------------------------- #
# Scheme and wiring
# --------------------------------------------------------------------------- #


class TestSchemeAndWiring:
    """The scheme is shared, and the factory refuses an unsafe configuration."""

    def test_both_signers_sign_the_same_message(self) -> None:
        """One scheme, so a future migration between backends is a key change only."""
        import hashlib
        import hmac

        key = b"\x33" * 32
        local = LocalMacSigner(key_id="k", key=key)
        remote = KmsMacSigner(FakeKmsClient({"k": key}), "k", cache_size=0)
        assert local.mac(PAYLOAD) == remote.mac(PAYLOAD)
        expected = hmac.new(key, mac_message(PAYLOAD), hashlib.sha256).digest()
        assert local.mac(PAYLOAD) == expected

    def test_the_domain_tag_is_versioned(self) -> None:
        assert MAC_DOMAIN_TAG.endswith(b".v1")

    def test_both_signers_satisfy_the_port(self) -> None:
        assert isinstance(kms_signer(cache_size=0), MacSigner)
        assert isinstance(LocalMacSigner(), MacSigner)

    def test_the_factory_requires_a_key_id(self) -> None:
        from glassbox.adapters.outbound.kms import build_mac_signer
        from glassbox.app.config import GlassBoxConfig, RuntimeProfile

        with pytest.raises(SigningUnavailableError):
            build_mac_signer(GlassBoxConfig(profile=RuntimeProfile.DEV))

    def test_the_factory_refuses_a_locally_held_key(self) -> None:
        """Silently weakening the KMS signer would defeat the point of choosing it."""
        from glassbox.adapters.outbound.kms import build_mac_signer
        from glassbox.app.config import GlassBoxConfig, RuntimeProfile, SigningConfig

        config = GlassBoxConfig(
            profile=RuntimeProfile.DEV,
            signing=SigningConfig(key_id="kms.key.v1", allow_local_key=True),
        )
        with pytest.raises(SigningUnavailableError):
            build_mac_signer(config)
