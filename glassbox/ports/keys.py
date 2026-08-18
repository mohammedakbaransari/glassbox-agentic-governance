"""Message authentication port (GB-002, WS-2).

Separated from the evidence store so the signing key can live somewhere the
application role cannot read. That separation is the whole point: an unkeyed
chain, or a keyed chain whose key sits in the same process that writes the rows,
is forgeable by anyone who can write a row. v1's chain was unkeyed and a forged
record re-verified as intact.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["MacSigner"]


@runtime_checkable
class MacSigner(Protocol):
    """Produces and verifies message authentication codes over evidence payloads.

    Conforming adapters must:

    * hold the key outside the application's reach in production (cloud KMS, HSM);
    * **never** fall back to an unkeyed digest when the key is unavailable --
      raise :class:`~glassbox.domain.errors.SigningUnavailableError` instead;
    * use a constant-time comparison in :meth:`verify`;
    * support rotation by resolving historic keys through ``key_id``.
    """

    @property
    def key_id(self) -> str:
        """Identifier of the key currently used for signing.

        Recorded on every evidence row so that rotation does not invalidate
        historical verification.
        """
        ...

    def mac(self, payload: bytes) -> bytes:
        """Return the MAC over ``payload``.

        Args:
            payload: Canonical bytes from
                :meth:`~glassbox.domain.evidence.IntentRecord.chain_payload`.

        Returns:
            At least 32 bytes (HMAC-SHA256 or stronger).

        Raises:
            glassbox.domain.errors.SigningUnavailableError: If the key cannot be
                reached. Callers must fail closed.
        """
        ...

    def verify(self, payload: bytes, mac: bytes, *, key_id: str) -> bool:
        """Return whether ``mac`` authenticates ``payload`` under ``key_id``.

        Must compare in constant time.

        Raises:
            glassbox.domain.errors.SigningUnavailableError: If ``key_id`` cannot
                be resolved. An unresolvable key yields
                :attr:`~glassbox.domain.evidence.IntegrityStatus.UNVERIFIABLE`,
                never ``INTACT``.
        """
        ...
