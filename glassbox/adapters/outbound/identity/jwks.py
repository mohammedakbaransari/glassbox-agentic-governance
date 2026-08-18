"""A static, in-process key resolver (GB-009).

For deployments where signing keys are provisioned out-of-band -- a Kubernetes
secret, a mounted config map, a value read once at startup from a vault -- and
fetching a live JWKS document over HTTPS is unnecessary or undesirable. Keys are
supplied at construction; nothing here performs I/O.

A JWKS-fetching provider (with caching and rotation handling) is a reasonable
next adapter to add once a deployment needs it; the :class:`~glassbox.adapters.outbound.identity.oidc.JwksProvider`
contract is exactly what it would implement.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from glassbox.domain.errors import IdentityError

__all__ = ["StaticJwksProvider"]


class StaticJwksProvider:
    """Resolves keys from a fixed, caller-supplied mapping.

    Args:
        keys: ``key_id -> cryptography public key object``.
    """

    __slots__ = ("_keys",)

    def __init__(self, keys: Mapping[str, Any]) -> None:
        self._keys: Dict[str, Any] = dict(keys)

    def get_key(self, key_id: str) -> Any:
        """Return the public key for ``key_id``.

        Raises:
            glassbox.domain.errors.IdentityError: If ``key_id`` is not known.
        """
        key = self._keys.get(key_id)
        if key is None:
            raise IdentityError("no verification key is provisioned for this kid", kid=key_id)
        return key
