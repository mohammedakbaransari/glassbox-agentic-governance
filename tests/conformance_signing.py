"""Shared ``MacSigner`` conformance suite (GB-006).

One behavioural specification for every signer, for the same reason as the
evidence conformance suite: two implementations of one port that are tested
separately will drift, and the weaker one silently becomes the real behaviour.

Not named ``test_*.py``, so the abstract class is not collected. Each adapter
subclasses :class:`MacSignerConformance` and supplies a ``signer`` fixture, plus
``independent_signer`` where the backend can produce a differently-keyed instance.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional

import pytest

from glassbox.adapters.outbound.signing import (
    MAC_DOMAIN_TAG,
    MAC_MESSAGE_LENGTH,
    mac_message,
)
from glassbox.domain.errors import SigningUnavailableError

PAYLOAD = b'{"seq":0,"prev_hash":"00","record":{"decision_id":"decision-0001"}}'
OTHER_PAYLOAD = b'{"seq":0,"prev_hash":"00","record":{"decision_id":"decision-0002"}}'


class MacSignerConformance:
    """Behaviour every ``MacSigner`` must exhibit."""

    @staticmethod
    def _independent(request: Any) -> Optional[Any]:
        """Return a differently-keyed signer, or ``None`` if unsupported."""
        return None

    # ----------------------------------------------------------------- #
    # The core guarantee
    # ----------------------------------------------------------------- #

    def test_mac_is_at_least_256_bits(self, signer: Any) -> None:
        assert len(signer.mac(PAYLOAD)) >= 32

    def test_mac_is_deterministic(self, signer: Any) -> None:
        """A replay must reproduce the chain exactly, so the MAC cannot vary."""
        assert signer.mac(PAYLOAD) == signer.mac(PAYLOAD)

    def test_different_payloads_produce_different_macs(self, signer: Any) -> None:
        assert signer.mac(PAYLOAD) != signer.mac(OTHER_PAYLOAD)

    def test_verify_accepts_the_authentic_mac(self, signer: Any) -> None:
        mac = signer.mac(PAYLOAD)
        assert signer.verify(PAYLOAD, mac, key_id=signer.key_id) is True

    def test_verify_rejects_a_mac_for_a_different_payload(self, signer: Any) -> None:
        """Regression: v1's unkeyed chain re-verified after a record was rewritten."""
        mac = signer.mac(PAYLOAD)
        assert signer.verify(OTHER_PAYLOAD, mac, key_id=signer.key_id) is False

    def test_verify_rejects_a_corrupted_mac(self, signer: Any) -> None:
        mac = bytearray(signer.mac(PAYLOAD))
        mac[0] ^= 0xFF
        assert signer.verify(PAYLOAD, bytes(mac), key_id=signer.key_id) is False

    def test_a_single_bit_change_changes_the_mac(self, signer: Any) -> None:
        flipped = bytearray(PAYLOAD)
        flipped[-2] ^= 0x01
        assert signer.mac(PAYLOAD) != signer.mac(bytes(flipped))

    # ----------------------------------------------------------------- #
    # Keying
    # ----------------------------------------------------------------- #

    def test_the_signer_reports_a_key_id(self, signer: Any) -> None:
        """Without it, rotation would invalidate every historical record."""
        assert isinstance(signer.key_id, str) and signer.key_id

    def test_an_unknown_key_is_unverifiable_not_intact(self, signer: Any) -> None:
        mac = signer.mac(PAYLOAD)
        with pytest.raises(SigningUnavailableError):
            signer.verify(PAYLOAD, mac, key_id="no-such-key")

    def test_an_independently_keyed_signer_cannot_reproduce_the_mac(
        self, signer: Any, independent_signer: Optional[Any]
    ) -> None:
        """The MAC must depend on the key, not only on the payload.

        This is the property v1 lacked entirely: its chain was an unkeyed digest,
        so anyone who could write a row could recompute it.
        """
        if independent_signer is None:
            pytest.skip("backend cannot produce an independently keyed signer")
        assert signer.mac(PAYLOAD) != independent_signer.mac(PAYLOAD)

    # ----------------------------------------------------------------- #
    # The scheme
    # ----------------------------------------------------------------- #

    def test_the_signed_message_is_domain_separated_and_bounded(self) -> None:
        """Pre-hashing keeps the message inside the KMS 4096-byte limit."""
        message = mac_message(b"x" * 100_000)
        assert message.startswith(MAC_DOMAIN_TAG)
        assert len(message) == MAC_MESSAGE_LENGTH

    def test_a_large_payload_can_be_signed(self, signer: Any) -> None:
        """An evidence record with long delegation chains must not break signing."""
        assert len(signer.mac(b"y" * 200_000)) >= 32

    def test_non_bytes_payloads_are_refused(self, signer: Any) -> None:
        """Accepting str would make the MAC depend on the ambient encoding."""
        with pytest.raises(TypeError):
            signer.mac("not bytes")  # type: ignore[arg-type]

    # ----------------------------------------------------------------- #
    # Failure and concurrency
    # ----------------------------------------------------------------- #

    def test_an_outage_raises_rather_than_degrading_to_unkeyed(self, signer: Any) -> None:
        setter = getattr(signer, "set_available", None)
        if setter is None:
            pytest.skip("backend does not expose a controllable outage")
        setter(False)
        try:
            with pytest.raises(SigningUnavailableError):
                signer.mac(PAYLOAD)
        finally:
            setter(True)

    def test_concurrent_signing_is_consistent(self, signer: Any) -> None:
        results: List[bytes] = []
        lock = threading.Lock()

        def sign(_index: int) -> None:
            mac = signer.mac(PAYLOAD)
            with lock:
                results.append(mac)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(sign, range(64)))

        assert len(set(results)) == 1, "concurrent signing produced divergent MACs"
