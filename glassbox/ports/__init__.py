"""GlassBox port definitions (GB-002).

Every infrastructure boundary in the rebuilt system is a ``Protocol`` declared
here (invariant I8). The application layer depends only on these; concrete
adapters -- Postgres, Redis, KMS, HTTP, Spark -- are wired at the composition root
(GB-003) and are never imported by ``glassbox.app`` or ``glassbox.domain``.

This is the direct fix for the v1 dependency-inversion score of 1/5, where
``GovernancePipeline`` took 25+ constructor parameters and built eight concrete
collaborators itself, making it untestable without monkey-patching.

Two rules apply to everything in this package:

1. **Ports declare behaviour, not data.** They import domain types and define
   method signatures. They contain no logic, no defaults and no state.
2. **Ports declare their failure modes.** Each docstring states which exception a
   conforming adapter must raise, because callers fail closed on those exceptions
   and the choice cannot be left to the adapter author.
"""

from __future__ import annotations

from glassbox.ports.attestation import AttestationProvider
from glassbox.ports.baseline import (
    Baseline,
    BaselineKey,
    BaselineScope,
    BaselineStore,
    BaselineVerdict,
)
from glassbox.ports.catalogue import ActionCatalogue
from glassbox.ports.clock import Clock
from glassbox.ports.dispatcher import Dispatcher
from glassbox.ports.evidence import EvidenceStore
from glassbox.ports.identity import IdentityVerifier
from glassbox.ports.keys import MacSigner
from glassbox.ports.limits import LimitStore
from glassbox.ports.mandate import MandateStore
from glassbox.ports.policy import PolicyDecisionPoint
from glassbox.ports.risk import RiskEngine
from glassbox.ports.tool_registry import ToolRegistry

__all__ = [
    "ActionCatalogue",
    "AttestationProvider",
    "Baseline",
    "BaselineKey",
    "BaselineScope",
    "BaselineStore",
    "BaselineVerdict",
    "Clock",
    "Dispatcher",
    "EvidenceStore",
    "IdentityVerifier",
    "MacSigner",
    "LimitStore",
    "MandateStore",
    "PolicyDecisionPoint",
    "RiskEngine",
    "ToolRegistry",
]
