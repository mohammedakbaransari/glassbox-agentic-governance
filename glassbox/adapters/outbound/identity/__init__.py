"""Identity verification adapters (GB-009).

Production-grade :class:`~glassbox.ports.identity.IdentityVerifier`
implementations. Every principal they produce derives ``tenant_id``,
``agent_ref`` and ``delegating_subject`` from verified claims only -- never from
a request header -- which is what closes v1's F1 defect: a shared bearer token
plus an ``X-Tenant-ID`` header that was copied straight into the request
context.

:mod:`~glassbox.adapters.outbound.identity.assertions` is shared by every
verifier in this package (and by the development verifier in
:mod:`glassbox.adapters.outbound.memory`) so the spoofing check cannot drift
between implementations.

mTLS/SPIFFE workload identity is not implemented in this pass; OIDC covers the
more broadly applicable case for now and is a reasonable place to have started.
"""

from __future__ import annotations

from glassbox.adapters.outbound.identity.assertions import check_assertion
from glassbox.adapters.outbound.identity.delegation import verify_delegation_chain
from glassbox.adapters.outbound.identity.jwks import StaticJwksProvider
from glassbox.adapters.outbound.identity.jwt_verify import (
    SUPPORTED_ALGORITHMS,
    VerifiedJws,
    verify_compact_jws,
)
from glassbox.adapters.outbound.identity.oidc import (
    ClaimMapping,
    JwksProvider,
    OidcIdentityVerifier,
)

__all__ = [
    "SUPPORTED_ALGORITHMS",
    "ClaimMapping",
    "JwksProvider",
    "OidcIdentityVerifier",
    "StaticJwksProvider",
    "VerifiedJws",
    "check_assertion",
    "verify_compact_jws",
    "verify_delegation_chain",
]
