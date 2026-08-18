"""The shared identity-assertion check (GB-009).

One function, used by every :class:`~glassbox.ports.identity.IdentityVerifier`
implementation, so the security-critical comparison at the API boundary cannot
drift between adapters the way v1's ad hoc header handling did.

v1 copied ``X-Tenant-ID`` and ``X-User-ID`` straight into the request context
(``api/app.py:255``), so any holder of the single shared API key could act as
any tenant or any user. Here, a transport-layer claim is never trusted on its
own (invariant I2): it is checked against the already-verified principal, and a
mismatch is refused rather than silently overridden or merely logged, so that
the spoofing attempt is visible to :class:`~glassbox.app.decision_service.DecisionService`,
which evidences it (GB-009).
"""

from __future__ import annotations

from typing import List

from glassbox.domain.errors import IdentityError
from glassbox.domain.identity import VerifiedPrincipal

__all__ = ["check_assertion"]


def check_assertion(
    principal: VerifiedPrincipal, *, asserted_tenant_id: str = "", asserted_subject: str = ""
) -> None:
    """Raise if a transport-layer identity claim contradicts ``principal``.

    An empty assertion is not a mismatch: a transport that carries no such
    header asserts nothing, and asserting nothing cannot contradict anything.

    Args:
        principal: The already-verified principal.
        asserted_tenant_id: A tenant claim from the transport layer, if any.
        asserted_subject: A subject claim from the transport layer, if any. A
            match against either :attr:`~VerifiedPrincipal.agent_ref` or
            :attr:`~VerifiedPrincipal.delegating_subject` is accepted, since a
            transport may name either the acting agent or the human behind it.

    Raises:
        glassbox.domain.errors.IdentityError: If either assertion is present and
            disagrees with the verified principal. Every disagreement is
            reported together, not just the first.
    """
    mismatches: List[str] = []
    if asserted_tenant_id and asserted_tenant_id != principal.tenant_id:
        mismatches.append(
            f"asserted tenant {asserted_tenant_id!r} != verified {principal.tenant_id!r}"
        )
    if asserted_subject and asserted_subject not in {
        principal.agent_ref,
        principal.delegating_subject,
    }:
        mismatches.append(f"asserted subject {asserted_subject!r} is not in the principal")
    if mismatches:
        raise IdentityError(
            "transport assertion contradicts the verified principal",
            mismatches="; ".join(mismatches),
            tenant_id=principal.tenant_id,
            agent_ref=principal.agent_ref,
        )
