"""Local MAC signer (GB-003, reference implementation for GB-006).

**Development only.** The key lives in the application process, so anyone who can
write an evidence row can also recompute its MAC. That is precisely the v1 defect
-- a forged record re-verified as intact -- and it is why
:attr:`~glassbox.app.config.SigningConfig.allow_local_key` is a safety switch the
production profile refuses.

What this class *does* get right, and what the KMS adapter in GB-006 must
preserve:

* the MAC is **keyed**, never a bare digest;
* :meth:`LocalMacSigner.verify` compares in **constant time**;
* every MAC is attributable to a ``key_id``, so rotation does not invalidate
  historical verification;
* an unavailable key raises
  :class:`~glassbox.domain.errors.SigningUnavailableError` rather than silently
  degrading to an unkeyed digest.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Dict, Optional

from glassbox.adapters.outbound.signing import mac_message
from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import SigningUnavailableError
from glassbox.ports.keys import MacSigner

__all__ = ["LocalMacSigner", "build_mac_signer"]

_DEFAULT_DEV_KEY_ID = "local.dev.v1"


class LocalMacSigner:
    """HMAC-SHA256 over evidence payloads, keyed by an in-process secret.

    Args:
        key_id: Identifier recorded on every evidence row.
        key: Raw key material. A cryptographically random key is generated when
            omitted, which means evidence written by one process cannot be
            verified by another -- an honest reflection of the fact that this
            adapter provides no assurance.
        historic_keys: Retired ``key_id -> key`` pairs, so records signed before
            a rotation still verify.
    """

    __slots__ = ("_key_id", "_keys", "_available")

    def __init__(
        self,
        key_id: str = _DEFAULT_DEV_KEY_ID,
        key: Optional[bytes] = None,
        historic_keys: Optional[Dict[str, bytes]] = None,
    ) -> None:
        self._key_id = key_id
        self._keys: Dict[str, bytes] = dict(historic_keys or {})
        self._keys[key_id] = key if key is not None else secrets.token_bytes(32)
        self._available = True

    @property
    def key_id(self) -> str:
        """Identifier of the key currently used for signing."""
        return self._key_id

    def mac(self, payload: bytes) -> bytes:
        """Return the HMAC-SHA256 of ``payload`` under the active key.

        Raises:
            SigningUnavailableError: If the key store has been marked unavailable.
                Callers must fail closed rather than write unkeyed evidence.
        """
        if not self._available:
            raise SigningUnavailableError(
                "signing key is unavailable", key_id=self._key_id, adapter="LocalMacSigner"
            )
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload must be bytes")
        return hmac.new(self._keys[self._key_id], mac_message(payload), hashlib.sha256).digest()

    def verify(self, payload: bytes, mac: bytes, *, key_id: str) -> bool:
        """Return whether ``mac`` authenticates ``payload`` under ``key_id``.

        Raises:
            SigningUnavailableError: If ``key_id`` cannot be resolved. An
                unresolvable key yields ``UNVERIFIABLE``, never ``INTACT``.
        """
        if not self._available:
            raise SigningUnavailableError(
                "signing key is unavailable", key_id=key_id, adapter="LocalMacSigner"
            )
        key = self._keys.get(key_id)
        if key is None:
            raise SigningUnavailableError(
                "unknown signing key; the segment is unverifiable",
                key_id=key_id,
                adapter="LocalMacSigner",
            )
        expected = hmac.new(key, mac_message(payload), hashlib.sha256).digest()
        return hmac.compare_digest(expected, bytes(mac))

    def rotate(self, new_key_id: str) -> None:
        """Begin signing under a new key, retaining the old one for verification."""
        self._keys[new_key_id] = secrets.token_bytes(32)
        self._key_id = new_key_id

    def set_available(self, available: bool) -> None:
        """Simulate a KMS outage, for fail-closed tests."""
        self._available = available


def build_mac_signer(config: GlassBoxConfig) -> MacSigner:
    """Factory used by the adapter set."""
    return LocalMacSigner(key_id=config.signing.key_id or _DEFAULT_DEV_KEY_ID)
