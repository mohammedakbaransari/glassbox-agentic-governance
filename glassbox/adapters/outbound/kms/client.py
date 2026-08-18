"""KMS client seam (GB-006).

Separates *which* key-management service is used from *how* evidence is signed,
for the same reasons the Postgres driver seam exists: the SDK stays optional, the
failure modes are translated once, and the signer is testable without a cloud
account.

The contract is deliberately the smallest thing that supports the guarantee:
generate a MAC, verify a MAC, and describe a key. Notably there is **no**
``get_key_material``. A client that could return the key would let the
application forge, which is the property this whole card exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from glassbox.domain.errors import SigningUnavailableError

__all__ = [
    "KeyMetadata",
    "KmsClient",
    "KmsKeyDisabledError",
    "KmsUnavailableError",
    "AwsKmsClient",
    "MAC_ALGORITHM",
]

#: The only MAC algorithm this adapter uses. HMAC-SHA256 is supported by AWS KMS,
#: Azure Managed HSM and GCP KMS, so the scheme is portable across providers.
MAC_ALGORITHM = "HMAC_SHA_256"


class KmsUnavailableError(SigningUnavailableError):
    """The key service could not be reached, or refused the request.

    A subclass of :class:`~glassbox.domain.errors.SigningUnavailableError`, so a
    caller that already fails closed on signing problems needs no change.
    """

    code = "kms_unavailable"


class KmsKeyDisabledError(SigningUnavailableError):
    """The key exists but is disabled, scheduled for deletion, or the wrong type.

    Distinguished from an outage because it is not transient: retrying will not
    help, and an operator must act.
    """

    code = "kms_key_unusable"


@dataclass(frozen=True, slots=True)
class KeyMetadata:
    """What the signer needs to know about a key before trusting it.

    Attributes:
        key_id: Fully qualified key identifier, as recorded on every evidence row.
        algorithm: MAC algorithm the key supports.
        enabled: Whether the key can currently be used.
        usable_for_mac: Whether the key's usage permits MAC generation. A key
            provisioned for encryption cannot sign, and finding that out at the
            first evidence write is far too late.
    """

    key_id: str
    algorithm: str = MAC_ALGORITHM
    enabled: bool = True
    usable_for_mac: bool = True

    @property
    def is_usable(self) -> bool:
        """Whether the key may be used to sign evidence."""
        return self.enabled and self.usable_for_mac and self.algorithm == MAC_ALGORITHM


@runtime_checkable
class KmsClient(Protocol):
    """The minimum key-service surface the evidence signer needs."""

    def generate_mac(self, key_id: str, message: bytes) -> bytes:
        """Return the MAC of ``message`` computed **inside** the key service.

        Raises:
            KmsUnavailableError: On any transport or throttling failure.
            KmsKeyDisabledError: If the key cannot be used.
        """
        ...

    def verify_mac(self, key_id: str, message: bytes, mac: bytes) -> bool:
        """Return whether ``mac`` authenticates ``message`` under ``key_id``.

        Raises:
            KmsUnavailableError: On any transport failure. An unverifiable
                segment must be reported as ``UNVERIFIABLE``, never ``INTACT``.
            KmsKeyDisabledError: If the key cannot be used.
        """
        ...

    def describe_key(self, key_id: str) -> KeyMetadata:
        """Return the key's metadata.

        Raises:
            KmsUnavailableError: If the key cannot be described.
        """
        ...


class AwsKmsClient:
    """A :class:`KmsClient` backed by AWS KMS.

    Requires the ``kms`` extra. Importing this module does not need ``boto3``;
    only constructing this class does, which keeps the zero-mandatory-dependency
    core intact.

    Args:
        region_name: AWS region. Defaults to the ambient configuration.
        client: A pre-built boto3 KMS client, mainly for injecting a configured
            session.
        connect_timeout_s: Connection timeout. Bounded, because an unbounded wait
            on the signing path stalls every decision behind it.
        read_timeout_s: Read timeout.
        max_attempts: SDK-level retry budget for transient errors.
    """

    __slots__ = ("_client",)

    def __init__(
        self,
        *,
        region_name: Optional[str] = None,
        client: Optional[Any] = None,
        connect_timeout_s: float = 2.0,
        read_timeout_s: float = 3.0,
        max_attempts: int = 3,
    ) -> None:
        if client is not None:
            self._client = client
            return
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise KmsUnavailableError(
                "the KMS evidence signer requires the 'kms' extra",
                remedy="pip install 'glassbox-governance[kms]'",
            ) from exc
        try:
            self._client = boto3.client(
                "kms",
                region_name=region_name,
                config=Config(
                    connect_timeout=connect_timeout_s,
                    read_timeout=read_timeout_s,
                    retries={"max_attempts": max_attempts, "mode": "standard"},
                ),
            )
        except Exception as exc:
            raise KmsUnavailableError(
                "could not construct the KMS client",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def generate_mac(self, key_id: str, message: bytes) -> bytes:
        """Compute the MAC inside KMS; the key never reaches this process."""
        try:
            response = self._client.generate_mac(
                KeyId=key_id, Message=message, MacAlgorithm=MAC_ALGORITHM
            )
        except Exception as exc:
            raise _translate(exc, key_id=key_id, operation="generate_mac") from exc
        mac = response.get("Mac")
        if not isinstance(mac, (bytes, bytearray)):
            raise KmsUnavailableError(
                "KMS returned no MAC", key_id=key_id, operation="generate_mac"
            )
        return bytes(mac)

    def verify_mac(self, key_id: str, message: bytes, mac: bytes) -> bool:
        """Verify inside KMS, so no comparison happens against local material."""
        try:
            response = self._client.verify_mac(
                KeyId=key_id, Message=message, Mac=mac, MacAlgorithm=MAC_ALGORITHM
            )
        except Exception as exc:
            translated = _translate(exc, key_id=key_id, operation="verify_mac")
            # KMS raises rather than returning false for a bad MAC; that is a
            # verification *result*, not an outage, and must not be reported as one.
            if type(exc).__name__ == "KMSInvalidMacException":
                return False
            raise translated from exc
        return bool(response.get("MacValid", False))

    def describe_key(self, key_id: str) -> KeyMetadata:
        """Read key state so an unusable key fails at startup, not mid-decision."""
        try:
            response = self._client.describe_key(KeyId=key_id)
        except Exception as exc:
            raise _translate(exc, key_id=key_id, operation="describe_key") from exc
        metadata = response.get("KeyMetadata", {})
        specs = metadata.get("MacAlgorithms") or [MAC_ALGORITHM]
        return KeyMetadata(
            key_id=str(metadata.get("Arn") or metadata.get("KeyId") or key_id),
            algorithm=MAC_ALGORITHM if MAC_ALGORITHM in specs else str(specs[0]),
            enabled=bool(metadata.get("Enabled", False)),
            usable_for_mac=str(metadata.get("KeyUsage", "GENERATE_VERIFY_MAC"))
            == "GENERATE_VERIFY_MAC",
        )


_NON_TRANSIENT = frozenset(
    {
        "DisabledException",
        "KeyUnavailableException",
        "KMSInvalidStateException",
        "NotFoundException",
        "InvalidKeyUsageException",
    }
)


def _translate(exc: Exception, *, key_id: str, operation: str) -> SigningUnavailableError:
    """Map an SDK exception onto a domain error, preserving the distinction.

    A disabled key and a network blip both stop evidence being written, but only
    one of them will resolve on its own. Collapsing them would send an operator
    looking in the wrong place.
    """
    name = type(exc).__name__
    error_type = KmsKeyDisabledError if name in _NON_TRANSIENT else KmsUnavailableError
    return error_type(
        f"KMS {operation} failed",
        key_id=key_id,
        operation=operation,
        cause=name,
        detail=str(exc),
    )
