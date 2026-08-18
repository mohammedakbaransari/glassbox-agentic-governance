"""The evidence MAC scheme (GB-006).

Shared by every :class:`~glassbox.ports.keys.MacSigner` implementation so that
local and KMS-backed signers compute *the same function* of a payload. v1's local
and Redis anomaly stores diverged precisely because two implementations of one
idea were written separately; one scheme, in one module, prevents the repeat.

The message that is actually MAC-ed is::

    MAC_MESSAGE = DOMAIN_TAG || 0x00 || SHA-256(canonical_payload)

Three reasons, all of which matter in production.

**Pre-hashing bounds the message.** AWS KMS ``GenerateMac`` rejects messages over
4096 bytes, and an evidence record carrying delegation chains, obligations and
risk factors will exceed that. MAC-ing a fixed 32-byte digest removes the limit
entirely, and HMAC over a digest is a standard, sound construction.

**Domain separation stops cross-protocol reuse.** If the same KMS key is ever
also used to MAC something else -- a session token, a webhook body -- an attacker
must not be able to present one as the other. The tag makes the two message
spaces disjoint.

**A version byte makes the scheme migratable.** The tag carries ``v1``. Changing
the construction means a new tag, so records signed under the old scheme keep
verifying against it instead of silently failing.
"""

from __future__ import annotations

import hashlib

__all__ = ["MAC_DOMAIN_TAG", "MAC_MESSAGE_LENGTH", "mac_message"]

#: Domain separation tag. Changing this is a scheme change and needs a new
#: ``signer_key_id`` namespace so historical records still verify.
MAC_DOMAIN_TAG = b"glassbox.evidence.mac.v1"

#: Length of the message handed to the signer: tag, separator, SHA-256 digest.
MAC_MESSAGE_LENGTH = len(MAC_DOMAIN_TAG) + 1 + hashlib.sha256().digest_size


def mac_message(payload: bytes) -> bytes:
    """Return the domain-separated, length-bounded message to be MAC-ed.

    Args:
        payload: Canonical bytes, normally from
            :meth:`~glassbox.domain.evidence.IntentRecord.chain_payload`.

    Returns:
        A fixed-length message, regardless of how large ``payload`` is.

    Raises:
        TypeError: If ``payload`` is not bytes. Accepting ``str`` here would make
            the MAC depend on the ambient encoding.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(f"payload must be bytes, got {type(payload).__name__}")
    return MAC_DOMAIN_TAG + b"\x00" + hashlib.sha256(bytes(payload)).digest()
