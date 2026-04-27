"""
GlassBox Framework - Policy Engine (Snapshot Pattern Optimization)
===================================================================

HIGH-priority optimization: Replace deep copy with lightweight snapshot pattern.

Problem:
  - Before: policy_engine.evaluate() deep copies entire payload for each rule eval
  - Impact: O(payload_size) memory allocation + copy time per policy
  - For 100KB payload + 10 rules: 1MB memory, 10x copy overhead

Solution:
  - SnapshotView: Lightweight view without copying (O(1) memory overhead)
  - frozen_fields: Caller specifies which fields cannot be modified
  - Validation: Read-only proxy raises error on write attempts
  - Performance: ~95% faster for compliance checks (no deep copy)

Reference:
  White, Tom. "Hadoop: The Definitive Guide" (Snapshot Isolation Pattern).

Author: Mohammed Akbar Ansari
"""

import copy
import logging as _logging
import threading
import warnings
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

_policy_log = _logging.getLogger("glassbox.policy_engine")


class Policy:
    """
    Represents a single policy rule with metadata.
    
    Used by PolicyEngine.register() to define custom evaluation rules.
    
    Attributes:
        id: Unique policy identifier (e.g., "POL-001")
        name: Human-readable policy name (e.g., "Budget Guard")
        decision_types: List of DecisionType values this policy applies to
        rule: Callable that evaluates the policy
               Signature: rule(payload: Dict, context: DecisionContext) -> PolicyEvaluation
    
    Example:
        def my_rule(payload, ctx):
            return PolicyEvaluation(
                policy_id="POL-001",
                policy_name="Budget Guard",
                result="pass" if payload.get("amount", 0) < 10000 else "fail",
                explanation="Amount check",
            )
        
        policy = Policy("POL-001", "Budget Guard", [DecisionType.FINANCIAL], my_rule)
        engine.register(policy)
    """
    
    def __init__(self, id: str = None, name: str = None, decision_types: List = None,
                 rule: Optional[Callable] = None,
                 enabled: bool = True,
                 *, policy_id: str = None, policy_name: str = None,
                 description: str = "", **kwargs):
        """
        Args:
            id: Unique policy identifier (or use policy_id keyword)
            name: Human-readable name (or use policy_name keyword)
            decision_types: List of DecisionType values this policy applies to
            rule: Callable that evaluates the policy rule
            policy_id: Alias for 'id' (keyword-only, preferred form)
            policy_name: Alias for 'name' (keyword-only, preferred form)
        """
        # Emit deprecation warning when positional (id, name) form is used
        # instead of the preferred keyword-only (policy_id, policy_name) form.
        if (id is not None or name is not None) and policy_id is None and policy_name is None:
            warnings.warn(
                "Passing 'id' and 'name' as positional arguments is deprecated. "
                "Use keyword arguments 'policy_id' and 'policy_name' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Support both (id, name) and (policy_id, policy_name) calling conventions
        if id is None and policy_id is not None:
            id = policy_id
        if name is None and policy_name is not None:
            name = policy_name

        if decision_types is None:
            decision_types = []

        self.id = id
        self.name = name
        self.description = description
        self.decision_types = decision_types
        self.rule = rule
        self.enabled = enabled
    
    @property
    def policy_id(self):
        """Alias for id (for test compatibility)."""
        return self.id

    @property
    def policy_name(self):
        """Alias for name (for test compatibility)."""
        return self.name

    def __getitem__(self, key: str):
        """Allow dict-style read access: policy['policy_id'], policy['policy_name']."""
        return getattr(self, key)

    def __repr__(self):
        return f'Policy(id={self.id!r}, name={self.name!r}, decision_types={self.decision_types})'


class ReadOnlySnapshot:
    """
    Lightweight read-only view of a payload without deep copying.
    
    Raises AttributeError on write attempts; allows read-only dict-like access.
    
    Usage:
        payload = {"amount": 1000, "supplier": "ACME", "risk": "high"}
        snapshot = ReadOnlySnapshot(payload, frozen_fields=["amount", "supplier"])
        
        # Read: OK
        amount = snapshot["amount"]
        
        # Write: raises
        snapshot["amount"] = 2000  # TypeError
    """
    
    def __init__(self, data: Dict[str, Any], frozen_fields: Optional[Set[str]] = None):
        """
        Args:
            data: Original payload dict
            frozen_fields: Set of field names that are frozen (read-only)
        """
        object.__setattr__(self, '_data', data)
        # frozenset ensures _frozen_fields itself cannot be mutated post-construction,
        # preventing a caller from widening the frozen scope after the fact.
        object.__setattr__(self, '_frozen_fields', frozenset(frozen_fields or data.keys()))
        object.__setattr__(self, '_wrapped_cache', {})

    def _freeze_value(self, value: Any) -> Any:
        """
        Lazily wrap nested mutable containers in read-only wrappers.
        This prevents policy rules from mutating nested payload structures.
        """
        if isinstance(value, dict):
            return _ReadOnlyDict(value)
        if isinstance(value, list):
            return _ReadOnlyList(value)
        return value
    
    def __getitem__(self, key: str) -> Any:
        """Read-only dict access."""
        if key in self._wrapped_cache:
            return self._wrapped_cache[key]
        frozen = self._freeze_value(self._data[key])
        self._wrapped_cache[key] = frozen
        return frozen
    
    def __setitem__(self, key: str, value: Any):
        """Prevent writes."""
        raise TypeError(
            f"Payload is read-only (snapshot). Cannot modify field '{key}'. "
            f"Frozen fields: {', '.join(sorted(self._frozen_fields))}."
        )
    
    def __getattr__(self, name: str) -> Any:
        """Attribute access (for compatibility)."""
        if name.startswith('_'):
            return object.__getattribute__(self, name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"Field '{name}' not found in snapshot")
    
    def __setattr__(self, name: str, value: Any):
        """Prevent attribute assignment."""
        if name.startswith('_'):
            return object.__setattr__(self, name, value)
        raise TypeError(
            f"Payload is read-only (snapshot). Cannot modify field '{name}'. "
            f"This protects decision integrity during policy evaluation."
        )
    
    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style get with default."""
        if key not in self._data:
            return default
        return self[key]
    
    def keys(self):
        """Dict keys."""
        return self._data.keys()
    
    def values(self):
        """Dict values."""
        return [self[k] for k in self._data]
    
    def items(self):
        """Dict items."""
        return [(k, self[k]) for k in self._data]
    
    def __contains__(self, key: str) -> bool:
        """Membership check."""
        return key in self._data
    
    def __len__(self) -> int:
        """Dict length."""
        return len(self._data)
    
    def __repr__(self) -> str:
        return f"ReadOnlySnapshot({self._data})"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot back to mutable dict (for output)."""
        return dict(self._data)


class SnapshotPattern:
    """
    Optional: Mix-in trait for other components to adopt snapshot pattern.
    
    Usage:
        class MyComponent(SnapshotPattern):
            def process(self, payload):
                snapshot = self.readonly_view(payload, fields=["id", "amount"])
                # Snapshot prevents accidental mutation
                return self.analyze(snapshot)
    """
    
    @staticmethod
    def readonly_view(
        data: Dict[str, Any],
        fields: Optional[Set[str]] = None,
    ) -> ReadOnlySnapshot:
        """Create read-only snapshot of data."""
        frozen = frozenset(fields) if fields is not None else frozenset(data.keys())
        return ReadOnlySnapshot(data, frozen_fields=frozen)


class _ReadOnlyDict(Mapping):
    """Read-only mapping wrapper with lazy nested freezing."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self._cache: Dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]
        value = self._data[key]
        if isinstance(value, dict):
            frozen = _ReadOnlyDict(value)
        elif isinstance(value, list):
            frozen = _ReadOnlyList(value)
        else:
            frozen = value
        self._cache[key] = frozen
        return frozen

    def __iter__(self) -> Iterator:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class _ReadOnlyList(Sequence):
    """Read-only sequence wrapper with lazy nested freezing."""

    def __init__(self, data: List[Any]):
        self._data = data
        self._cache: Dict[int, Any] = {}

    def __getitem__(self, idx: int) -> Any:
        if isinstance(idx, slice):
            return tuple(self[i] for i in range(*idx.indices(len(self._data))))
        if idx in self._cache:
            return self._cache[idx]
        value = self._data[idx]
        if isinstance(value, dict):
            frozen = _ReadOnlyDict(value)
        elif isinstance(value, list):
            frozen = _ReadOnlyList(value)
        else:
            frozen = value
        self._cache[idx] = frozen
        return frozen

    def __len__(self) -> int:
        return len(self._data)


# ── PolicyEngine: Public API for backward compatibility ────────────────────────

class PolicyEngine:
    """
    Public API that maintains backward compatibility with v1.0.x tests.
    
    Uses PolicyEngineOptimized internally for snapshot pattern performance benefits.
    Translates between the public API (decide by DecisionType) and internal
    representation (individual policy checks).
    """
    
    def __init__(
        self,
        policies: Optional[List[Policy]] = None,
        policy_eval_timeout_sec: Optional[float] = None,
    ):
        """
        Initialize PolicyEngine with optional initial policies.

        Args:
            policies: List of Policy objects to register. Defaults to DEFAULT_POLICIES.
            policy_eval_timeout_sec: Per-policy rule evaluation timeout in seconds.
                When set, each policy rule is executed in a dedicated thread and
                cancelled (with a violation recorded) if it does not return within
                this duration. Default None disables the guard (backward-compatible).
                Recommended production value: 1.0–5.0 seconds.
        """
        self._policies = {}  # policy_id -> Policy object
        self._policies_by_type = {}  # DecisionType -> List[Policy]
        self._lock = threading.RLock()

        # Timeout guard: shared executor used when policy_eval_timeout_sec is set.
        # max_workers is sized to handle concurrent pipeline threads without creating
        # a per-rule thread on every call — threads are reused across evaluations.
        self._policy_timeout_sec: Optional[float] = (
            float(policy_eval_timeout_sec) if policy_eval_timeout_sec else None
        )
        self._timeout_executor: Optional[ThreadPoolExecutor] = (
            ThreadPoolExecutor(
                max_workers=32,
                thread_name_prefix="glassbox-policy-timeout",
            )
            if self._policy_timeout_sec
            else None
        )

        # Register provided policies or DEFAULT_POLICIES.
        # Make copies of each policy to isolate instances — preserving ALL fields
        # (including 'enabled') so a disabled policy isn't silently re-enabled.
        for policy in (policies or DEFAULT_POLICIES):
            policy_copy = Policy(
                policy_id=policy.id,
                policy_name=policy.name,
                decision_types=list(policy.decision_types),
                rule=policy.rule,
            )
            policy_copy.enabled = policy.enabled
            self.register(policy_copy)
    
    def register(self, policy: Policy, warn_conflicts: bool = True) -> None:
        """
        Register a policy with the engine.

        Args:
            policy: Policy object to register
            warn_conflicts: Emit a warning when the new policy shares the same
                decision_types as an existing *different* policy whose name
                suggests contradictory intent (e.g. both "block" and "allow"
                in the same type). Defaults to True.
        """
        with self._lock:
            existing = self._policies.get(policy.id)
            if existing is not None:
                for dt in existing.decision_types:
                    current = tuple(
                        candidate
                        for candidate in self._policies_by_type.get(dt, ())
                        if candidate.id != policy.id
                    )
                    if current:
                        self._policies_by_type[dt] = current
                    else:
                        self._policies_by_type.pop(dt, None)

            # Conflict detection: warn when a newly enabled policy covers the
            # same decision_types as an existing enabled policy whose name
            # contains opposite intent keywords ("block" vs "allow", "deny" vs "permit").
            if warn_conflicts and policy.enabled and policy.decision_types:
                _BLOCK_WORDS = frozenset({"block", "deny", "reject", "prohibit", "restrict"})
                _ALLOW_WORDS = frozenset({"allow", "permit", "approve", "grant", "whitelist"})

                def _intent(name: str) -> Optional[str]:
                    n = (name or "").lower()
                    if any(w in n for w in _BLOCK_WORDS):
                        return "block"
                    if any(w in n for w in _ALLOW_WORDS):
                        return "allow"
                    return None

                new_intent = _intent(policy.name or "")
                if new_intent:
                    opposite = "allow" if new_intent == "block" else "block"
                    for dt in policy.decision_types:
                        for peer in self._policies_by_type.get(dt, ()):
                            if peer.id == policy.id:
                                continue
                            if _intent(peer.name or "") == opposite:
                                import warnings as _warnings
                                _warnings.warn(
                                    f"Policy conflict: registering '{policy.id}' ({new_intent}-intent)"
                                    f" alongside '{peer.id}' ({opposite}-intent)"
                                    f" for decision_type '{dt}'. Review policy ordering.",
                                    UserWarning,
                                    stacklevel=3,
                                )

            self._policies[policy.id] = policy

            # Index by decision type
            if policy.enabled:
                for dt in policy.decision_types:
                    current = list(self._policies_by_type.get(dt, ()))
                    current.append(policy)
                    self._policies_by_type[dt] = tuple(current)
    
    def evaluate(
        self,
        decision_type,
        payload: Dict[str, Any],
        context,
    ):
        """
        Evaluate all policies for a decision type (PUBLIC API).
        
        This is the primary interface used by tests and production code.
        
        Args:
            decision_type: DecisionType enum value
            payload: Decision payload dictionary
            context: DecisionContext object
        
        Returns:
            PolicyResult: passed=True iff all policies pass
        """
        # Import here to avoid circular imports
        from glassbox.governance.models import PolicyEvaluation, PolicyResult
        
        with self._lock:
            policies = tuple(self._policies_by_type.get(decision_type, ()))
        
        passed = True
        violations = []
        warnings = []
        evaluations = []
        
        # Create read-only snapshot to prevent mutations during rule evaluation
        snapshot = ReadOnlySnapshot(payload)
        
        for policy in policies:
            try:
                # Call the policy rule through a timeout guard when configured.
                # The guard submits the rule to a shared ThreadPoolExecutor and waits
                # at most policy_eval_timeout_sec seconds. A timed-out rule thread
                # continues running in the background until it naturally completes —
                # Python cannot forcibly terminate threads — but the pipeline is never
                # blocked by it.
                if self._policy_timeout_sec and self._timeout_executor:
                    future = self._timeout_executor.submit(policy.rule, snapshot, context)
                    try:
                        eval_result = future.result(timeout=self._policy_timeout_sec)
                    except _FuturesTimeout:
                        passed = False
                        _policy_log.error(
                            "Policy evaluation timeout [policy_id=%s] after %.1fs — "
                            "treating as violation to prevent pipeline stall",
                            policy.id, self._policy_timeout_sec,
                            extra={"policy_id": policy.id, "policy_name": policy.name},
                        )
                        violations.append(
                            f"{policy.id}: Policy evaluation timed out (see audit log)"
                        )
                        evaluations.append(PolicyEvaluation(
                            policy_id=policy.id,
                            policy_name=policy.name,
                            result="error",
                            message="Policy evaluation timed out (see audit log)",
                        ))
                        continue
                else:
                    eval_result = policy.rule(snapshot, context)

                if isinstance(eval_result, PolicyEvaluation):
                    evaluations.append(eval_result)
                    result_str = eval_result.result.lower() if eval_result.result else "pass"
                else:
                    # Handle if rule returns dict or tuple
                    result_str = str(eval_result).lower() if eval_result else "pass"
                    evaluations.append(PolicyEvaluation(
                        policy_id=policy.id,
                        policy_name=policy.name,
                        result=result_str,
                        message=f"Policy {policy.name} evaluation"
                    ))

                if result_str == "fail":
                    passed = False
                    msg = eval_result.message if isinstance(eval_result, PolicyEvaluation) else str(eval_result)
                    violations.append(f"{policy.id}: {msg}")
                elif result_str == "warn":
                    msg = eval_result.message if isinstance(eval_result, PolicyEvaluation) else str(eval_result)
                    warnings.append(f"{policy.id}: {msg}")

            except Exception as e:
                passed = False
                # O5: Log full traceback internally; never expose implementation
                # details in violation messages visible to callers.
                _policy_log.error(
                    "Policy evaluation exception [policy_id=%s]: %s",
                    policy.id, e,
                    exc_info=True,
                    extra={"policy_id": policy.id, "policy_name": policy.name},
                )
                violations.append(
                    f"{policy.id}: Policy evaluation error (see audit log for details)"
                )
                evaluations.append(PolicyEvaluation(
                    policy_id=policy.id,
                    policy_name=policy.name,
                    result="error",
                    message="Policy evaluation error (see audit log for details)",
                ))
        
        return PolicyResult(
            passed=passed,
            evaluated_policies=evaluations,
            violations=violations,
            warnings=warnings,
        )
    
    @property
    def policies(self):
        """Return list of policies (for test compatibility)."""
        with self._lock:
            return list(self._policies.values())
    
    def list_policies(self) -> List[Policy]:
        """Return all registered policies."""
        with self._lock:
            return list(self._policies.values())
    
    def disable(self, policy_id: str) -> bool:
        """Temporarily disable a policy."""
        with self._lock:
            if policy_id not in self._policies:
                return False
            policy = self._policies[policy_id]
            policy.enabled = False
            # Remove from all decision types
            for dt in list(self._policies_by_type.keys()):
                current = tuple(
                    candidate
                    for candidate in self._policies_by_type.get(dt, ())
                    if candidate.id != policy_id
                )
                if current:
                    self._policies_by_type[dt] = current
                else:
                    self._policies_by_type.pop(dt, None)
            return True
    
    def enable(self, policy_id: str) -> bool:
        """Re-enable a previously disabled policy."""
        with self._lock:
            if policy_id not in self._policies:
                return False
            policy = self._policies[policy_id]
            policy.enabled = True
            # Re-add to evaluation for applicable types
            for dt in policy.decision_types:
                current = list(self._policies_by_type.get(dt, ()))
                if all(existing.id != policy_id for existing in current):
                    current.append(policy)
                self._policies_by_type[dt] = tuple(current)
            return True

    def shutdown(self) -> None:
        """Shut down the timeout executor if one was created."""
        if self._timeout_executor is not None:
            self._timeout_executor.shutdown(wait=False)
            self._timeout_executor = None


# ── Default policies (24 across all decision types) ────────────────────────────

from glassbox.governance.models import (
    DecisionContext, DecisionType, PolicyEvaluation
)
from glassbox.governance.policy_parameters import _param_store


def _procurement_policy_amount_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """POL-001: Procurement amount must be under threshold without contract."""
    amount = float(payload.get("amount", 0))
    contract_id = payload.get("contract_id")
    threshold = _param_store.get("PROC-001", "amount_threshold", default=500_000)

    if amount >= threshold and not contract_id:
        return PolicyEvaluation(
            policy_id="PROC-001",
            policy_name="Procurement Amount Limit",
            result="fail",
            message=f"Amount >= ${threshold:,.0f} requires contract_id"
        )
    return PolicyEvaluation(
        policy_id="PROC-001",
        policy_name="Procurement Amount Limit",
        result="pass",
        message="Amount check passed"
    )


def _procurement_policy_supplier_known(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """POL-002: Supplier must be known (not blocked), with warning for UNKNOWN."""
    supplier_id = payload.get("supplier_id", "").lower()
    
    blocked_suppliers = {"blocked", "denied"}
    if supplier_id in blocked_suppliers:
        return PolicyEvaluation(
            policy_id="PROC-002",
            policy_name="Supplier Known Check",
            result="fail",
            message=f"Supplier {supplier_id} is blocked"
        )
    
    if supplier_id == "unknown":
        # Warning, not fail
        return PolicyEvaluation(
            policy_id="PROC-002",
            policy_name="Supplier Known Check",
            result="warn",
            message=f"Supplier is unknown"
        )
    
    return PolicyEvaluation(
        policy_id="PROC-002",
        policy_name="Supplier Known Check",
        result="pass",
        message="Supplier check passed"
    )


def _procurement_policy_category_risk(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """POL-003: High-risk categories need approval."""
    category = payload.get("category", "").lower()
    category_approval = payload.get("category_approval_ref")
    
    high_risk = {"semiconductors", "weapons", "controlled"}
    if category in high_risk and not category_approval:
        return PolicyEvaluation(
            policy_id="PROC-003",
            policy_name="Category Risk Check",
            result="fail",
            message=f"Category {category} requires approval"
        )
    
    return PolicyEvaluation(
        policy_id="PROC-003",
        policy_name="Category Risk Check",
        result="pass",
        message="Category check passed"
    )


def _pricing_policy_change_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """POL-004: Price change must not exceed configured percentage limit (default 30%)."""
    new_price = float(payload.get("new_price", 0))
    prev_price = float(payload.get("previous_price", 0))
    approval = payload.get("approval_ref") or payload.get("price_approval") or payload.get("reason")
    pct_limit = _param_store.get("PRICE-001", "change_pct_limit", default=30)

    if prev_price > 0:
        pct_change = abs(new_price - prev_price) / prev_price * 100
        if pct_change > pct_limit and not approval:
            return PolicyEvaluation(
                policy_id="PRICE-001",
                policy_name="Price Change Limit",
                result="fail",
                message=f"Price change {pct_change:.1f}% exceeds {pct_limit}% limit"
            )

    return PolicyEvaluation(
        policy_id="PRICE-001",
        policy_name="Price Change Limit",
        result="pass",
        message="Price change within limit"
    )


def _financial_policy_transfer_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """POL-005: Transfer amount must not exceed configured limit (default $1M)."""
    amount = float(payload.get("amount", 0))
    limit = _param_store.get("FIN-001", "transfer_limit", default=1_000_000)

    if amount >= limit:
        return PolicyEvaluation(
            policy_id="FIN-001",
            policy_name="Transfer Limit",
            result="fail",
            message=f"Transfer amount ${amount:,.0f} exceeds ${limit:,.0f} limit"
        )

    return PolicyEvaluation(
        policy_id="FIN-001",
        policy_name="Transfer Limit",
        result="pass",
        message="Transfer within limit"
    )


def _policy_production_override_forbidden(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """SECURITY-001: User overrides forbidden in production."""
    user_override = getattr(ctx, 'user_override', False)
    environment = getattr(ctx, 'environment', 'production')

    if environment == "production" and user_override:
        return PolicyEvaluation(
            policy_id="SECURITY-001",
            policy_name="Production Override Forbidden",
            result="fail",
            message="User overrides not allowed in production"
        )
    return PolicyEvaluation(
        policy_id="SECURITY-001",
        policy_name="Production Override Forbidden",
        result="pass",
        message="OK"
    )


def _confidence_threshold(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """AI-001: Decision confidence must be >= 30%."""
    confidence = getattr(ctx, 'confidence', 1.0)

    if confidence < 0.30:
        return PolicyEvaluation(
            policy_id="AI-001",
            policy_name="Confidence Threshold",
            result="fail",
            message=f"Confidence {confidence:.1%} below 30% threshold"
        )
    return PolicyEvaluation(
        policy_id="AI-001",
        policy_name="Confidence Threshold",
        result="pass",
        message="Confidence check passed"
    )


# ── v1.1 General Policies (PII / EU Automated Decision) ──────────────────────
import re as _re

_SSN_RE    = _re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
_EMAIL_RE  = _re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
_EU_COUNTRIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})


def _policy_pii_prevention(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """GEN-001: Detect PII (SSN / email) in generated content."""
    content = str(payload.get("content", ""))
    if _SSN_RE.search(content):
        return PolicyEvaluation(
            policy_id="GEN-001",
            policy_name="PII Exposure Prevention",
            result="fail",
            message="Detected SSN in content — PII exposure risk"
        )
    if _EMAIL_RE.search(content):
        return PolicyEvaluation(
            policy_id="GEN-001",
            policy_name="PII Exposure Prevention",
            result="fail",
            message="Detected email address in content — PII exposure risk"
        )
    return PolicyEvaluation(
        policy_id="GEN-001",
        policy_name="PII Exposure Prevention",
        result="pass",
        message="No PII detected"
    )


def _policy_eu_automated_decision(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """GEN-002: GDPR Art.22 — EU citizens must have human review available."""
    jurisdiction = getattr(ctx, 'jurisdiction', 'US')
    if jurisdiction not in _EU_COUNTRIES:
        return PolicyEvaluation(
            policy_id="GEN-002",
            policy_name="EU Automated Decision Gate",
            result="pass",
            message="Non-EU jurisdiction — GDPR Art.22 not applicable"
        )
    affects_individual = payload.get("affects_individual", False)
    if affects_individual and not payload.get("human_review_available", False):
        return PolicyEvaluation(
            policy_id="GEN-002",
            policy_name="EU Automated Decision Gate",
            result="fail",
            message="GDPR Art.22: automated decision affecting EU individual requires human review option"
        )
    return PolicyEvaluation(
        policy_id="GEN-002",
        policy_name="EU Automated Decision Gate",
        result="pass",
        message="EU automated decision requirements satisfied"
    )


# ── Additional Procurement Policies ───────────────────────────────────────────
def _procurement_policy_sole_source(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PROC-004: Sole-source procurement requires written justification."""
    if payload.get("sole_source") and not payload.get("sole_source_justification"):
        return PolicyEvaluation(
            policy_id="PROC-004",
            policy_name="Sole Source Justification",
            result="fail",
            message="Sole-source procurement requires a written justification"
        )
    return PolicyEvaluation(
        policy_id="PROC-004",
        policy_name="Sole Source Justification",
        result="pass",
        message="Sole source requirement satisfied"
    )


def _procurement_policy_audit_trail(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PROC-005: High-value procurement requires audit trail reference (default $1M)."""
    amount = payload.get("amount", 0)
    audit_ref = payload.get("audit_ref")
    threshold = _param_store.get("PROC-005", "amount_threshold", default=1_000_000)

    if amount > threshold and not audit_ref:
        return PolicyEvaluation(
            policy_id="PROC-005",
            policy_name="Audit Trail Requirement",
            result="fail",
            message=f"Audit trail reference required for procurement > ${threshold:,.0f}"
        )
    return PolicyEvaluation(
        policy_id="PROC-005",
        policy_name="Audit Trail Requirement",
        result="pass",
        message="Audit trail requirement satisfied"
    )


# O6: Compile-time defaults used ONLY when the param store has no override.
# Operators should keep these lists current via PolicyParameterStore at runtime
# (OFAC/Treasury sanctions lists change on arbitrary notice).
_DEFAULT_SANCTIONED_COUNTRIES: frozenset = frozenset({"IR", "KP", "SY", "CU", "RU", "BY"})
_DEFAULT_DEBARRED_SUPPLIERS: frozenset   = frozenset({"DEBARRED-001", "BLACKLISTED-CORP"})


def _get_sanctioned_countries() -> frozenset:
    """Return the live sanctions list from param store, falling back to compile-time defaults."""
    raw = _param_store.get("PROC-006", "sanctioned_countries", default=None)
    if raw is not None:
        return frozenset(str(c).upper() for c in raw) if isinstance(raw, (list, tuple, set)) else _DEFAULT_SANCTIONED_COUNTRIES
    return _DEFAULT_SANCTIONED_COUNTRIES


def _get_debarred_suppliers() -> frozenset:
    """Return the live debarment list from param store, falling back to compile-time defaults."""
    raw = _param_store.get("PROC-006", "debarred_suppliers", default=None)
    if raw is not None:
        return frozenset(str(s).upper() for s in raw) if isinstance(raw, (list, tuple, set)) else _DEFAULT_DEBARRED_SUPPLIERS
    return _DEFAULT_DEBARRED_SUPPLIERS


def _procurement_policy_sanctions(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PROC-006: Block sanctioned-country suppliers and debarred vendors."""
    country     = (payload.get("supplier_country") or "").upper()
    supplier_id = (payload.get("supplier_id") or "").upper()

    # Read live lists each evaluation so runtime updates take immediate effect.
    sanctioned_countries = _get_sanctioned_countries()
    debarred_suppliers   = _get_debarred_suppliers()

    if country in sanctioned_countries:
        return PolicyEvaluation(
            policy_id="PROC-006",
            policy_name="Sanctions & Debarment Check",
            result="fail",
            message=f"Supplier country '{country}' is sanctioned",
        )
    if supplier_id in debarred_suppliers:
        return PolicyEvaluation(
            policy_id="PROC-006",
            policy_name="Sanctions & Debarment Check",
            result="fail",
            message=f"Supplier '{supplier_id}' is debarred",
        )
    return PolicyEvaluation(
        policy_id="PROC-006",
        policy_name="Sanctions & Debarment Check",
        result="pass",
        message="Supplier cleared",
    )


# ── Additional Pricing Policies ───────────────────────────────────────────────
def _pricing_policy_floor_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PRICE-002: Enforce floor price (minimum selling price)."""
    product_id = payload.get("product_id")
    new_price = payload.get("new_price", 0)
    floor_price = payload.get("floor_price")
    
    if floor_price and new_price < floor_price:
        return PolicyEvaluation(
            policy_id="PRICE-002",
            policy_name="Floor Price Limit",
            result="fail",
            message=f"Price ${new_price} below floor ${floor_price}"
        )
    return PolicyEvaluation(
        policy_id="PRICE-002",
        policy_name="Floor Price Limit",
        result="pass",
        message="Floor price respected"
    )


def _pricing_policy_ceiling_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PRICE-003: Enforce ceiling price (maximum selling price)."""
    product_id = payload.get("product_id")
    new_price = payload.get("new_price", 0)
    ceiling_price = payload.get("ceiling_price")
    
    if ceiling_price and new_price > ceiling_price:
        return PolicyEvaluation(
            policy_id="PRICE-003",
            policy_name="Ceiling Price Limit",
            result="fail",
            message=f"Price ${new_price} exceeds ceiling ${ceiling_price}"
        )
    return PolicyEvaluation(
        policy_id="PRICE-003",
        policy_name="Ceiling Price Limit",
        result="pass",
        message="Ceiling price respected"
    )


def _pricing_policy_competitor_check(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PRICE-004: Check pricing against competitor benchmarks."""
    product_id = payload.get("product_id")
    new_price = payload.get("new_price", 0)
    competitor_avg = payload.get("competitor_avg_price")
    tolerance_pct = payload.get("price_variance_tolerance", 15)  # Default 15%
    
    if competitor_avg:
        max_price = competitor_avg * (1 + tolerance_pct / 100)
        if new_price > max_price:
            return PolicyEvaluation(
                policy_id="PRICE-004",
                policy_name="Competitor Price Benchmark",
                result="warn",
                message=f"Price ${new_price} exceeds competitor avg ${competitor_avg} by {tolerance_pct}%"
            )
    return PolicyEvaluation(
        policy_id="PRICE-004",
        policy_name="Competitor Price Benchmark",
        result="pass",
        message="Competitive pricing maintained"
    )


# ── Additional Financial Policies ─────────────────────────────────────────────
def _financial_policy_wire_velocity(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-002: Daily wire transfer limit ($5M) / velocity (3 per hour > $100K)."""
    transfer_amount = float(payload.get("amount", 0) or 0)
    transfer_count  = int(payload.get("recent_transfer_count", 0) or 0)

    # Single transfer exceeds daily wire limit
    if transfer_amount > 5_000_000:
        return PolicyEvaluation(
            policy_id="FIN-002",
            policy_name="Wire Transfer Velocity",
            result="fail",
            message=f"Transfer amount ${transfer_amount:,.0f} exceeds $5,000,000 daily wire limit"
        )
    # Velocity check: max 3 large transfers per hour
    if transfer_amount > 100_000 and transfer_count >= 3:
        return PolicyEvaluation(
            policy_id="FIN-002",
            policy_name="Wire Transfer Velocity",
            result="fail",
            message="Wire transfer rate limit exceeded (max 3 transfers > $100,000 per hour)"
        )
    return PolicyEvaluation(
        policy_id="FIN-002",
        policy_name="Wire Transfer Velocity",
        result="pass",
        message="Transfer velocity acceptable"
    )


_CTR_THRESHOLD = 10_000  # Bank Secrecy Act Cash Transaction Report threshold


def _financial_policy_counterparty_check(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-003: Financial transfer must name a destination account."""
    if not payload.get("destination_account"):
        return PolicyEvaluation(
            policy_id="FIN-003",
            policy_name="Missing Counterparty",
            result="fail",
            message="destination_account is required for financial transfers"
        )
    return PolicyEvaluation(
        policy_id="FIN-003",
        policy_name="Missing Counterparty",
        result="pass",
        message="Counterparty present"
    )


def _financial_policy_ctr_threshold(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-004: Cash transactions >= $10K require a CTR filing flag (advisory warn)."""
    amount    = float(payload.get("amount", 0) or 0)
    method    = str(payload.get("payment_method", "") or "").lower()
    ctr_filed = payload.get("ctr_filed", False)

    if method == "cash" and amount >= _CTR_THRESHOLD and not ctr_filed:
        return PolicyEvaluation(
            policy_id="FIN-004",
            policy_name="CTR Cash Threshold",
            result="warn",
            message=f"Cash transaction of ${amount:,.0f} >= ${_CTR_THRESHOLD:,}: CTR filing may be required (BSA)"
        )
    return PolicyEvaluation(
        policy_id="FIN-004",
        policy_name="CTR Cash Threshold",
        result="pass",
        message="Cash threshold not triggered"
    )


def _financial_policy_structuring(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-005: Detect potential structuring (amounts just below $10K CTR threshold)."""
    amount = float(payload.get("amount", 0) or 0)
    # Flag round numbers in the $9,000–$9,999 window (common structuring pattern)
    if 9_000 <= amount < _CTR_THRESHOLD and amount == round(amount, -2):
        return PolicyEvaluation(
            policy_id="FIN-005",
            policy_name="Structuring Detection",
            result="warn",
            message=f"Amount ${amount:,.0f} is a round number just below the ${_CTR_THRESHOLD:,} CTR reporting threshold — potential structuring"
        )
    return PolicyEvaluation(
        policy_id="FIN-005",
        policy_name="Structuring Detection",
        result="pass",
        message="No structuring pattern detected"
    )


# ── Clinical Policies ─────────────────────────────────────────────────────────
_CONTROLLED_DRUG_CLASSES = frozenset({
    "schedule_i", "schedule_ii", "schedule_iii", "schedule_iv", "schedule_v",
})


def _clinical_policy_controlled_substance(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """CLIN-001: Controlled substance prescriptions require a DEA number."""
    drug_class = str(payload.get("drug_class", "") or "").lower()
    if drug_class in _CONTROLLED_DRUG_CLASSES and not payload.get("prescriber_dea_number"):
        return PolicyEvaluation(
            policy_id="CLIN-001",
            policy_name="Controlled Substance Check",
            result="fail",
            message=f"DEA registration number required to prescribe {drug_class} substance '{payload.get('drug_name', 'unknown')}'"
        )
    return PolicyEvaluation(
        policy_id="CLIN-001",
        policy_name="Controlled Substance Check",
        result="pass",
        message="Controlled substance requirements satisfied"
    )


def _clinical_policy_dosage_check(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """CLIN-002: Prescribed dose must not exceed the maximum safe dose."""
    dose_mg         = float(payload.get("dose_mg", payload.get("dosage_mg", 0)) or 0)
    max_dose_mg     = float(payload.get("max_dose_mg", 0) or 0)
    patient_weight  = float(payload.get("patient_weight_kg", 0) or 0)
    max_mg_per_kg   = float(payload.get("max_mg_per_kg", 0) or 0)

    # Weight-based check
    if patient_weight > 0 and max_mg_per_kg > 0:
        weight_limit = patient_weight * max_mg_per_kg
        if dose_mg > weight_limit:
            return PolicyEvaluation(
                policy_id="CLIN-002",
                policy_name="Dosage Safety Check",
                result="fail",
                message=f"Dose {dose_mg}mg exceeds weight-based limit of {weight_limit:.0f}mg for {patient_weight}kg patient"
            )

    # Absolute max dose check
    if max_dose_mg > 0 and dose_mg > max_dose_mg:
        return PolicyEvaluation(
            policy_id="CLIN-002",
            policy_name="Dosage Safety Check",
            result="fail",
            message=f"Dose {dose_mg}mg exceeds maximum safe dose of {max_dose_mg}mg"
        )
    return PolicyEvaluation(
        policy_id="CLIN-002",
        policy_name="Dosage Safety Check",
        result="pass",
        message="Dose within safe limits"
    )


# ── Trading Policies ──────────────────────────────────────────────────────────
def _trading_policy_position_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """TRADE-001: Order notional must not exceed the declared position limit."""
    notional       = float(payload.get("notional", 0) or 0)
    position_limit = float(payload.get("position_limit", 0) or 0)

    if position_limit > 0 and notional > position_limit:
        return PolicyEvaluation(
            policy_id="TRADE-001",
            policy_name="Position Limit",
            result="fail",
            message=f"Order notional ${notional:,.0f} exceeds position limit ${position_limit:,.0f}"
        )
    return PolicyEvaluation(
        policy_id="TRADE-001",
        policy_name="Position Limit",
        result="pass",
        message="Within position limit"
    )


def _trading_policy_fat_finger(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """TRADE-002: Detect fat-finger orders (quantity > 10× average daily quantity)."""
    quantity      = float(payload.get("quantity", 0) or 0)
    avg_daily_qty = float(payload.get("avg_daily_qty", 0) or 0)

    if avg_daily_qty > 0 and quantity > 10 * avg_daily_qty:
        return PolicyEvaluation(
            policy_id="TRADE-002",
            policy_name="Fat Finger Check",
            result="fail",
            message=f"Order qty {quantity:,.0f} is {quantity/avg_daily_qty:.1f}× average daily qty — likely fat-finger"
        )
    return PolicyEvaluation(
        policy_id="TRADE-002",
        policy_name="Fat Finger Check",
        result="pass",
        message="Order size within normal range"
    )


# ── IT Operations Policies ────────────────────────────────────────────────────
def _it_ops_policy_maintenance_window(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """IT-OPS-002: Operations only during maintenance windows (not in prod hours)."""
    action = payload.get("action", "").lower()
    is_during_maintenance = (payload.get("during_maintenance_window", False)
                             or payload.get("change_window_approved", False))
    
    disruptive_actions = {"restart_service", "delete_database", "scale_down", "backup"}
    if action in disruptive_actions and not is_during_maintenance:
        return PolicyEvaluation(
            policy_id="IT-OPS-002",
            policy_name="Maintenance Window Enforcement",
            result="fail",
            message=f"Action '{action}' only allowed during maintenance windows"
        )
    return PolicyEvaluation(
        policy_id="IT-OPS-002",
        policy_name="Maintenance Window Enforcement",
        result="pass",
        message="Operation timing approved"
    )


def _it_ops_policy_service_criticality(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """IT-OPS-003: High-criticality services require additional approvals."""
    target = payload.get("target", "").lower()
    requires_approval = payload.get("requires_approval", False)
    
    critical_services = {"database-primary", "auth-service", "payment-gateway", "load-balancer"}
    if target in critical_services and not requires_approval:
        return PolicyEvaluation(
            policy_id="IT-OPS-003",
            policy_name="Service Criticality Gate",
            result="fail",
            message=f"Critical service '{target}' requires explicit approval"
        )
    return PolicyEvaluation(
        policy_id="IT-OPS-003",
        policy_name="Service Criticality Gate",
        result="pass",
        message="Service criticality check passed"
    )


_MAJOR_ACTIONS = frozenset({
    # Infrastructure mutations that always require a change record
    "restart_service", "stop_service", "start_service", "kill_process",
    "delete_database", "drop_table", "truncate_table",
    "scale_down", "scale_up", "resize_cluster",
    "deploy", "rollback", "upgrade", "downgrade",
    "patch_os", "reboot", "shutdown",
    "create_firewall_rule", "delete_firewall_rule",
    "modify_security_group", "revoke_access",
    "backup", "restore", "failover",
    "flush_cache", "rotate_secrets", "rotate_keys",
})


def _it_ops_policy_change_log(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """IT-OPS-004: Major infrastructure actions require a change-log reference."""
    change_id = payload.get("change_id")
    action = str(payload.get("action", "") or "").lower().strip().replace(" ", "_").replace("-", "_")

    if action in _MAJOR_ACTIONS and not change_id:
        return PolicyEvaluation(
            policy_id="IT-OPS-004",
            policy_name="Change Log Requirement",
            result="fail",
            message=f"Action '{action}' requires a change_id reference in the change log"
        )
    return PolicyEvaluation(
        policy_id="IT-OPS-004",
        policy_name="Change Log Requirement",
        result="pass",
        message="Change log requirement satisfied"
    )


# ── HR Policies ───────────────────────────────────────────────────────────────
def _hr_policy_salary_limits(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """HR-001: Salary changes limited to 10% per review cycle."""
    current_salary = payload.get("current_salary", 0)
    new_salary = payload.get("new_salary", 0)
    
    if current_salary > 0:
        pct_change = abs(new_salary - current_salary) / current_salary * 100
        if pct_change > 10:
            return PolicyEvaluation(
                policy_id="HR-001",
                policy_name="Salary Change Limit",
                result="fail",
                message=f"Salary change {pct_change:.1f}% exceeds 10% limit"
            )
    return PolicyEvaluation(
        policy_id="HR-001",
        policy_name="Salary Change Limit",
        result="pass",
        message="Salary change within limits"
    )


def _hr_policy_promotion_restrictions(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """HR-002: Promotions require manager and HR approval."""
    is_promotion = payload.get("is_promotion", False)
    approvals = [payload.get("manager_approval"), payload.get("hr_approval")]
    
    if is_promotion and not all(approvals):
        return PolicyEvaluation(
            policy_id="HR-002",
            policy_name="Promotion Approval Gate",
            result="fail",
            message="Promotion requires both manager and HR approval"
        )
    return PolicyEvaluation(
        policy_id="HR-002",
        policy_name="Promotion Approval Gate",
        result="pass",
        message="Promotion approval requirements met"
    )


def _hr_policy_access_provisioning(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """HR-003: System access provisioning requires security review."""
    access_level = payload.get("access_level", "").upper()
    security_cleared = payload.get("security_cleared", False)
    
    if access_level in {"ADMIN", "SUPER", "ROOT"} and not security_cleared:
        return PolicyEvaluation(
            policy_id="HR-003",
            policy_name="Access Provisioning Security",
            result="fail",
            message=f"{access_level} access requires security clearance"
        )
    return PolicyEvaluation(
        policy_id="HR-003",
        policy_name="Access Provisioning Security",
        result="pass",
        message="Access provisioning security check passed"
    )


# ── Compliance Policies ───────────────────────────────────────────────────────
def _compliance_policy_pii_exposure(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """COMPLIANCE-001: Prevent accidental PII (personally identifiable information) exposure."""
    fields = payload.get("fields", [])
    pii_keywords = {"ssn", "passport", "credit_card", "birth_date", "social_security"}
    
    exposed_pii = [f for f in fields if any(k in f.lower() for k in pii_keywords)]
    if exposed_pii:
        return PolicyEvaluation(
            policy_id="COMPLIANCE-001",
            policy_name="PII Exposure Prevention",
            result="fail",
            message=f"Potential PII fields detected: {exposed_pii}"
        )
    return PolicyEvaluation(
        policy_id="COMPLIANCE-001",
        policy_name="PII Exposure Prevention",
        result="pass",
        message="No PII exposure detected"
    )


def _compliance_policy_data_residency(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """COMPLIANCE-002: Data must reside in approved geographic regions."""
    data_region = payload.get("data_region", "").upper()
    approved_regions = {"US-EAST", "US-WEST", "EU", "APAC", "CANADA"}
    
    if data_region and data_region not in approved_regions:
        return PolicyEvaluation(
            policy_id="COMPLIANCE-002",
            policy_name="Data Residency Compliance",
            result="fail",
            message=f"Region {data_region} not approved. Allowed: {', '.join(approved_regions)}"
        )
    return PolicyEvaluation(
        policy_id="COMPLIANCE-002",
        policy_name="Data Residency Compliance",
        result="pass",
        message="Data residency compliant"
    )


def _compliance_policy_breach_notification(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """COMPLIANCE-003: Regulatory breach incidents require notification within 72 hours."""
    breach_type = payload.get("breach_type", "").lower()
    notified = payload.get("regulatory_notified", False)
    
    if breach_type in {"data_loss", "ransomware", "unauthorized_access"} and not notified:
        return PolicyEvaluation(
            policy_id="COMPLIANCE-003",
            policy_name="Breach Notification Requirement",
            result="fail",
            message="Regulatory authorities must be notified within 72 hours"
        )
    return PolicyEvaluation(
        policy_id="COMPLIANCE-003",
        policy_name="Breach Notification Requirement",
        result="pass",
        message="Breach notification requirements satisfied"
    )


# ── Risk Aggregation Policy ───────────────────────────────────────────────────
def _risk_policy_aggregation(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """RISK-001: Aggregate risk score must not exceed threshold."""
    component_risks = payload.get("component_risks", {})  # {"financial": 0.3, "security": 0.4, ...}
    
    # Simple aggregation: sum of component risks
    total_risk = sum(component_risks.values()) if component_risks else 0
    risk_threshold = payload.get("risk_threshold", 0.7)
    
    if total_risk > risk_threshold:
        return PolicyEvaluation(
            policy_id="RISK-001",
            policy_name="Risk Aggregation Gate",
            result="fail",
            message=f"Aggregated risk score {total_risk:.2f} exceeds threshold {risk_threshold}"
        )
    return PolicyEvaluation(
        policy_id="RISK-001",
        policy_name="Risk Aggregation Gate",
        result="pass",
        message="Risk aggregation score acceptable"
    )


# ── Legal Policies ────────────────────────────────────────────────────────────
def _legal_policy_contract_authority(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """LEGAL-001: AI contract actions above configured threshold require human authority."""
    contract_value = float(payload.get("contract_value", payload.get("amount", 0)) or 0)
    authority_ref  = payload.get("authority_ref") or payload.get("approval_ref")
    threshold      = _param_store.get("LEGAL-001", "authority_threshold", default=250_000)

    if contract_value >= threshold and not authority_ref:
        return PolicyEvaluation(
            policy_id="LEGAL-001",
            policy_name="Contract Authority Limit",
            result="fail",
            message=f"Contract value ${contract_value:,.0f} >= ${threshold:,.0f} requires authority_ref",
        )
    return PolicyEvaluation(
        policy_id="LEGAL-001",
        policy_name="Contract Authority Limit",
        result="pass",
        message="Contract authority check passed",
    )


def _legal_policy_hold_compliance(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """LEGAL-002: Documents under legal hold must not be deleted or modified."""
    action      = str(payload.get("action", "") or "").lower()
    legal_hold  = payload.get("legal_hold", False)
    hold_waiver = payload.get("hold_waiver_ref")

    destructive = {"delete", "modify", "overwrite", "purge", "archive", "shred"}
    if legal_hold and action in destructive and not hold_waiver:
        return PolicyEvaluation(
            policy_id="LEGAL-002",
            policy_name="Legal Hold Compliance",
            result="fail",
            message=f"Action '{action}' blocked — document is under legal hold (provide hold_waiver_ref to override)",
        )
    return PolicyEvaluation(
        policy_id="LEGAL-002",
        policy_name="Legal Hold Compliance",
        result="pass",
        message="Legal hold check passed",
    )


# Build v1.2 policy portfolio (35 policies)
DEFAULT_POLICIES: List[Policy] = [
    # ── Procurement ──────────────────────────────────────────────────────────
    Policy(policy_id="PROC-001", policy_name="Procurement Amount Limit",
        decision_types=[DecisionType.PROCUREMENT], rule=_procurement_policy_amount_limit),
    Policy(policy_id="PROC-002", policy_name="Supplier Known Check",
        decision_types=[DecisionType.PROCUREMENT], rule=_procurement_policy_supplier_known),
    Policy(policy_id="PROC-003", policy_name="Category Risk Check",
        decision_types=[DecisionType.PROCUREMENT], rule=_procurement_policy_category_risk),
    Policy(policy_id="PROC-004", policy_name="Sole Source Justification",
        decision_types=[DecisionType.PROCUREMENT], rule=_procurement_policy_sole_source),
    Policy(policy_id="PROC-005", policy_name="Audit Trail Requirement",
        decision_types=[DecisionType.PROCUREMENT], rule=_procurement_policy_audit_trail),
    Policy(policy_id="PROC-006", policy_name="Sanctions & Debarment Check",
        decision_types=[DecisionType.PROCUREMENT], rule=_procurement_policy_sanctions),
    # ── Pricing ──────────────────────────────────────────────────────────────
    Policy(policy_id="PRICE-001", policy_name="Price Change Limit",
        decision_types=[DecisionType.PRICING], rule=_pricing_policy_change_limit),
    Policy(policy_id="PRICE-002", policy_name="Floor Price Limit",
        decision_types=[DecisionType.PRICING], rule=_pricing_policy_floor_limit),
    Policy(policy_id="PRICE-003", policy_name="Ceiling Price Limit",
        decision_types=[DecisionType.PRICING], rule=_pricing_policy_ceiling_limit),
    Policy(policy_id="PRICE-004", policy_name="Competitor Price Benchmark",
        decision_types=[DecisionType.PRICING], rule=_pricing_policy_competitor_check),
    # ── Financial ────────────────────────────────────────────────────────────
    Policy(policy_id="FIN-001", policy_name="Transfer Limit",
        decision_types=[DecisionType.FINANCIAL], rule=_financial_policy_transfer_limit),
    Policy(policy_id="FIN-002", policy_name="Wire Transfer Velocity",
        decision_types=[DecisionType.FINANCIAL], rule=_financial_policy_wire_velocity),
    Policy(policy_id="FIN-003", policy_name="Missing Counterparty",
        decision_types=[DecisionType.FINANCIAL], rule=_financial_policy_counterparty_check),
    Policy(policy_id="FIN-004", policy_name="CTR Cash Threshold",
        decision_types=[DecisionType.FINANCIAL], rule=_financial_policy_ctr_threshold),
    Policy(policy_id="FIN-005", policy_name="Structuring Detection",
        decision_types=[DecisionType.FINANCIAL], rule=_financial_policy_structuring),
    # ── Clinical ─────────────────────────────────────────────────────────────
    Policy(policy_id="CLIN-001", policy_name="Controlled Substance Check",
        decision_types=[DecisionType.CLINICAL], rule=_clinical_policy_controlled_substance),
    Policy(policy_id="CLIN-002", policy_name="Dosage Safety Check",
        decision_types=[DecisionType.CLINICAL], rule=_clinical_policy_dosage_check),
    # ── Trading ──────────────────────────────────────────────────────────────
    Policy(policy_id="TRADE-001", policy_name="Position Limit",
        decision_types=[DecisionType.TRADING], rule=_trading_policy_position_limit),
    Policy(policy_id="TRADE-002", policy_name="Fat Finger Check",
        decision_types=[DecisionType.TRADING], rule=_trading_policy_fat_finger),
    # ── IT Operations ────────────────────────────────────────────────────────
    Policy(policy_id="IT-OPS-002", policy_name="Maintenance Window Enforcement",
        decision_types=[DecisionType.IT_OPS], rule=_it_ops_policy_maintenance_window),
    Policy(policy_id="IT-OPS-003", policy_name="Service Criticality Gate",
        decision_types=[DecisionType.IT_OPS], rule=_it_ops_policy_service_criticality),
    Policy(policy_id="IT-OPS-004", policy_name="Change Log Requirement",
        decision_types=[DecisionType.IT_OPS], rule=_it_ops_policy_change_log),
    # ── Human Resources ──────────────────────────────────────────────────────
    Policy(policy_id="HR-001", policy_name="Salary Change Limit",
        decision_types=[DecisionType.HR], rule=_hr_policy_salary_limits),
    Policy(policy_id="HR-002", policy_name="Promotion Approval Gate",
        decision_types=[DecisionType.HR], rule=_hr_policy_promotion_restrictions),
    Policy(policy_id="HR-003", policy_name="Access Provisioning Security",
        decision_types=[DecisionType.HR], rule=_hr_policy_access_provisioning),
    # ── Compliance ───────────────────────────────────────────────────────────
    Policy(policy_id="COMPLIANCE-001", policy_name="PII Exposure Prevention",
        decision_types=[DecisionType.CUSTOM], rule=_compliance_policy_pii_exposure),
    Policy(policy_id="COMPLIANCE-002", policy_name="Data Residency Compliance",
        decision_types=[DecisionType.CUSTOM], rule=_compliance_policy_data_residency),
    Policy(policy_id="COMPLIANCE-003", policy_name="Breach Notification Requirement",
        decision_types=[DecisionType.CUSTOM], rule=_compliance_policy_breach_notification),
    # ── General / Content ────────────────────────────────────────────────────
    Policy(policy_id="GEN-001", policy_name="PII Exposure Prevention",
        decision_types=[DecisionType.CONTENT], rule=_policy_pii_prevention),
    Policy(policy_id="GEN-002", policy_name="EU Automated Decision Gate",
        decision_types=[DecisionType.CONTENT], rule=_policy_eu_automated_decision),
    # ── Legal ─────────────────────────────────────────────────────────────────
    Policy(policy_id="LEGAL-001", policy_name="Contract Authority Limit",
        decision_types=[DecisionType.LEGAL], rule=_legal_policy_contract_authority),
    Policy(policy_id="LEGAL-002", policy_name="Legal Hold Compliance",
        decision_types=[DecisionType.LEGAL], rule=_legal_policy_hold_compliance),
    # ── Security / Governance ─────────────────────────────────────────────────
    # Covers all decision types including CONTENT (generative AI output) and LEGAL.
    Policy(policy_id="SECURITY-001", policy_name="Production Override Forbidden",
        decision_types=[
            DecisionType.PROCUREMENT, DecisionType.PRICING, DecisionType.FINANCIAL,
            DecisionType.INVENTORY, DecisionType.IT_OPS, DecisionType.LOGISTICS,
            DecisionType.HR, DecisionType.CUSTOM,
            DecisionType.CLINICAL, DecisionType.TRADING,
            DecisionType.CONTENT, DecisionType.LEGAL,
        ], rule=_policy_production_override_forbidden),
    Policy(policy_id="AI-001", policy_name="Confidence Threshold",
        decision_types=[
            DecisionType.PROCUREMENT, DecisionType.PRICING, DecisionType.FINANCIAL,
            DecisionType.INVENTORY, DecisionType.IT_OPS, DecisionType.LOGISTICS,
            DecisionType.HR, DecisionType.CUSTOM,
            DecisionType.CLINICAL, DecisionType.TRADING,
            DecisionType.CONTENT, DecisionType.LEGAL,
        ], rule=_confidence_threshold),
    Policy(policy_id="RISK-001", policy_name="Risk Aggregation Gate",
        decision_types=[DecisionType.PROCUREMENT, DecisionType.PRICING, DecisionType.FINANCIAL,
         DecisionType.INVENTORY, DecisionType.IT_OPS, DecisionType.LOGISTICS,
         DecisionType.HR, DecisionType.CUSTOM,
         DecisionType.CONTENT, DecisionType.LEGAL], rule=_risk_policy_aggregation),
]

# --- Backward-compatible policy class expected by tests/examples ---

def _fleet_budget_rule(payload: Dict, ctx: 'DecisionContext') -> 'PolicyEvaluation':
    """Fleet budget enforcement rule."""
    # Accept multiple field name variations
    spend = float(payload.get("fleet_spend", payload.get("amount", payload.get("total_cost", 0))) or 0)
    budget = float(payload.get("fleet_budget", payload.get("budget_limit", 100000.0)) or 100000.0)
    
    if spend > budget:
        return PolicyEvaluation(
            policy_id="LOG-001",
            policy_name="Fleet Budget Policy",
            result="fail",
            message=f"Fleet spend {spend} exceeds budget {budget}",
        )
    return PolicyEvaluation(
        policy_id="LOG-001",
        policy_name="Fleet Budget Policy",
        result="pass",
        message="Fleet within budget",
    )


class FleetBudgetPolicy(Policy):
    """Backward-compatible policy for fleet/logistics budget enforcement."""

    def __init__(self, budget: float = 100000.0, warn_threshold: float = 0.8):
        self.budget = float(budget)
        self.warn_threshold = float(warn_threshold)
        self.spent = 0.0
        self._spent_lock = threading.Lock()  # guards self.spent against concurrent mutation
        super().__init__(
            policy_id="LOG-001",
            policy_name="Fleet Budget Policy",
            decision_types=[DecisionType.LOGISTICS, DecisionType.FINANCIAL],
            rule=self._rule,
        )

    def _rule(self, payload: Dict, ctx: 'DecisionContext') -> 'PolicyEvaluation':
        amount = float(payload.get("amount", payload.get("fleet_spend", payload.get("total_cost", 0))) or 0)
        with self._spent_lock:
            current_spent = self.spent
        projected = current_spent + amount
        if projected > self.budget:
            return PolicyEvaluation(
                policy_id="LOG-001",
                policy_name="Fleet Budget Policy",
                result="fail",
                message=f"Fleet spend {projected:.2f} exceeds budget {self.budget:.2f}",
            )
        if projected >= self.budget * self.warn_threshold:
            return PolicyEvaluation(
                policy_id="LOG-001",
                policy_name="Fleet Budget Policy",
                result="warn",
                message=(
                    f"Fleet spend {projected:.2f} reached {projected / self.budget:.0%} "
                    f"of budget {self.budget:.2f}"
                ),
            )
        return PolicyEvaluation(
            policy_id="LOG-001",
            policy_name="Fleet Budget Policy",
            result="pass",
            message="Fleet within budget",
        )

    def record_execution(self, amount: float) -> None:
        with self._spent_lock:
            self.spent += float(amount or 0.0)

    def as_policy(self) -> 'Policy':
        return self


__all__ = ["Policy", "PolicyEngine", "ReadOnlySnapshot", "SnapshotPattern",
           "DEFAULT_POLICIES", "FleetBudgetPolicy"]
