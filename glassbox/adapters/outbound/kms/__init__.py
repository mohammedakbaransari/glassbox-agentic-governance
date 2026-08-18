"""KMS outbound adapters (GB-006).

Evidence signing where the key never enters this process. Requires the ``kms``
extra for the AWS client::

    pip install 'glassbox-governance[kms]'

Importing this package does not need ``boto3``; only constructing
:class:`~glassbox.adapters.outbound.kms.client.AwsKmsClient` does.
"""

from __future__ import annotations

from glassbox.adapters.outbound.kms.client import (
    MAC_ALGORITHM,
    AwsKmsClient,
    KeyMetadata,
    KmsClient,
    KmsKeyDisabledError,
    KmsUnavailableError,
)
from glassbox.adapters.outbound.kms.signer import (
    DEFAULT_MAC_CACHE_SIZE,
    CircuitBreaker,
    KmsMacSigner,
)
from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import SigningUnavailableError
from glassbox.ports.keys import MacSigner

__all__ = [
    "DEFAULT_MAC_CACHE_SIZE",
    "MAC_ALGORITHM",
    "AwsKmsClient",
    "CircuitBreaker",
    "KeyMetadata",
    "KmsClient",
    "KmsKeyDisabledError",
    "KmsMacSigner",
    "KmsUnavailableError",
    "build_mac_signer",
]


def build_mac_signer(config: GlassBoxConfig) -> MacSigner:
    """Factory for the composition root.

    Describes the key during construction, so a disabled or wrongly-provisioned
    key fails the deployment instead of the first evidence write -- at which point
    the system would be refusing every non-advisory decision.

    Raises:
        SigningUnavailableError: If no key id is configured, or the key is
            unusable. The process must not start.
    """
    if not config.signing.key_id:
        raise SigningUnavailableError(
            "the KMS evidence signer requires signing.key_id",
            profile=config.profile.value,
        )
    if config.signing.allow_local_key:
        raise SigningUnavailableError(
            "signing.allow_local_key is set; use the local signer explicitly rather "
            "than silently weakening the KMS one",
            profile=config.profile.value,
        )
    client = AwsKmsClient()
    return KmsMacSigner(client, config.signing.key_id)
