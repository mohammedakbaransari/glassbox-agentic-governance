"""
GlassBox Framework — Advanced Access Control
======================================================

Legacy, in-process RBAC. Not persisted, not shared across replicas, and not
wired to any identity provider -- see docs/CLAIMS.md for the measured
reasons this must not be relied on as a production security boundary. For
real identity and policy decisions, use ``glassbox.app``/``glassbox.ports``
instead.

Provides:
  - Role hierarchy (permissions inherited from parent roles)
  - Attribute-based access control (ABAC) via context matching
  - Resource-scoped permissions (per-resource, per-action)
  - Dynamic permission evaluation at request time, with a bounded cache

Design Patterns:
  - Permission model: <resource>:<action>:<scope>
    Example: "audit_log:read:own_tenant"
  - Context-aware evaluation: Check role + attributes at runtime
  - Audit all permission decisions for compliance

Usage:
    from glassbox.governance.access_control import AccessControl, Role

    # Define roles
    admin_role = Role("admin", description="Super admin")
    admin_role.grant_permission("audit_log:read:any_tenant")
    admin_role.grant_permission("policy:write:any_tenant")

    analyst_role = Role("analyst", description="Data analyst")
    analyst_role.grant_permission("audit_log:read:own_tenant")
    analyst_role.set_parent(admin_role)  # Inherit admin permissions

    # Initialize access control
    ac = AccessControl()
    ac.register_role(admin_role)
    ac.register_role(analyst_role)

    # Check permission
    can_read = ac.has_permission(
        user_id="user123",
        role="analyst",
        resource="audit_log",
        action="read",
        context={"tenant_id": "tenant1", "record_tenant_id": "tenant1"}
    )

Author: Mohammed Akbar Ansari
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from glassbox.governance.logging_manager import get_logger

log = get_logger("access_control")


class PermissionScope(str, Enum):
    """Permission scope levels."""

    OWN_RECORD = "own_record"  # User's own record
    OWN_TENANT = "own_tenant"  # Own tenant/organization
    ANY_TENANT = "any_tenant"  # Any tenant (admin)
    ANY = "any"  # Unrestricted
    CUSTOM = "custom"  # Custom scope logic


@dataclass
class Permission:
    """Represents a granular permission."""

    resource: str  # e.g., "audit_log", "policy"
    action: str  # e.g., "read", "write", "delete"
    scope: str  # e.g., "own_tenant", "any_tenant"
    conditions: Dict[str, Any] = field(default_factory=dict)  # Optional conditions

    def matches(self, resource: str, action: str) -> bool:
        """Check if this permission matches resource:action."""
        return self.resource == resource and self.action == action

    def __str__(self) -> str:
        return f"{self.resource}:{self.action}:{self.scope}"

    def __hash__(self) -> int:
        return hash(str(self))

    def __eq__(self, other) -> bool:
        return str(self) == str(other)


@dataclass
class Role:
    """Represents a role with permissions and hierarchy."""

    name: str
    description: str = ""
    permissions: Set[Permission] = field(default_factory=set)
    parent_role: Optional["Role"] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def grant_permission(
        self,
        resource: str,
        action: str,
        scope: str = PermissionScope.ANY,
        conditions: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Grant a permission to this role."""
        perm = Permission(
            resource=resource,
            action=action,
            scope=scope,
            conditions=conditions or {},
        )
        self.permissions.add(perm)
        log.debug("Role %s granted permission: %s", self.name, perm)

    def revoke_permission(
        self,
        resource: str,
        action: str,
        scope: str = PermissionScope.ANY,
    ) -> None:
        """Revoke a permission from this role."""
        self.permissions = {
            p
            for p in self.permissions
            if not (p.resource == resource and p.action == action and p.scope == scope)
        }
        log.debug("Role %s revoked permission: %s:%s:%s", self.name, resource, action, scope)

    def set_parent(self, parent_role: "Role") -> None:
        """Set parent role for inheritance."""
        self.parent_role = parent_role
        log.debug("Role %s inherits from %s", self.name, parent_role.name)

    def get_all_permissions(self, _visited: Optional[Set[str]] = None) -> Set["Permission"]:
        """Get all permissions (including inherited). Cycle-safe."""
        if _visited is None:
            _visited = set()
        # Guard against circular role inheritance (role_a.parent = role_b; role_b.parent = role_a).
        if self.name in _visited:
            return set()
        _visited.add(self.name)
        perms = set(self.permissions)
        if self.parent_role:
            perms.update(self.parent_role.get_all_permissions(_visited))
        return perms

    def has_permission(
        self,
        resource: str,
        action: str,
        scope: Optional[str] = None,
    ) -> bool:
        """Check if role has permission (including inherited)."""
        for perm in self.get_all_permissions():
            if perm.matches(resource, action):
                if scope is None or self._scope_matches(perm.scope, scope):
                    return True
        return False

    def _scope_matches(self, perm_scope: str, context_scope: str) -> bool:
        """Check if permission scope allows context scope."""
        # Scope hierarchy: ANY > CUSTOM > ANY_TENANT > OWN_TENANT > OWN_RECORD
        hierarchy: List[str] = [
            PermissionScope.OWN_RECORD,
            PermissionScope.OWN_TENANT,
            PermissionScope.ANY_TENANT,
            PermissionScope.CUSTOM,
            PermissionScope.ANY,
        ]

        try:
            perm_level = hierarchy.index(perm_scope)
            context_level = hierarchy.index(context_scope)
            return perm_level >= context_level
        except ValueError:
            return perm_scope == context_scope


@dataclass
class User:
    """Represents a user with roles and attributes."""

    user_id: str
    roles: Set[str] = field(default_factory=set)
    attributes: Dict[str, Any] = field(default_factory=dict)
    delegated_role: Optional[str] = None
    delegated_by: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_checked_at: Optional[datetime] = None

    def add_role(self, role_name: str) -> None:
        """Add a role to user."""
        self.roles.add(role_name)

    def remove_role(self, role_name: str) -> None:
        """Remove a role from user."""
        self.roles.discard(role_name)

    def is_impersonated(self) -> bool:
        """Check if user is impersonated."""
        return self.delegated_role is not None


@dataclass
class AccessDecision:
    """Result of an access control decision."""

    allowed: bool
    reason: str
    principal: str  # user_id or "system"
    resource: str
    action: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0


class AccessControl:
    """Main access control engine.

    GB-040: this is legacy, in-process RBAC. It is not persisted, not shared
    across replicas, and not wired to any real identity provider, so it must
    not be relied on as a production security boundary -- see
    docs/CLAIMS.md and the ``glassbox.app``/``glassbox.ports`` identity and
    policy-decision-point layer for the real one. ``impersonate()`` (an
    in-process, unaudited, unrevocable-from-outside privilege-escalation
    mechanism) was removed entirely rather than hardened, since there is no
    safe way to keep it. The permission cache is bounded (``MAX_CACHE_ENTRIES``)
    to close the unbounded-memory-growth vector the review measured.
    """

    #: Hard cap on cached permission decisions. Without a cap, one cache entry
    #: per unique (user, resource, action, context) combination accumulates
    #: forever between `clear_cache()` calls -- an unbounded-memory vector.
    MAX_CACHE_ENTRIES = 10_000

    def __init__(self, enable_caching: bool = True, cache_ttl_sec: float = 300.0):
        import os as _os

        self.roles: Dict[str, Role] = {}
        self.users: Dict[str, User] = {}
        self.enable_caching = enable_caching
        # Allow deployment-time override without code change:
        # export GLASSBOX_RBAC_CACHE_TTL=30
        self.cache_ttl_sec = float(_os.environ.get("GLASSBOX_RBAC_CACHE_TTL", cache_ttl_sec))
        self._cache: Dict[str, Tuple[bool, float]] = {}  # decision -> (allowed, timestamp)
        self._lock = threading.RLock()
        self._decision_log: List[AccessDecision] = []
        self._validators: List[Callable[[Dict[str, Any]], bool]] = []

    def register_role(self, role: Role) -> None:
        """Register a role."""
        with self._lock:
            self.roles[role.name] = role
            self._cache.clear()  # Invalidate stale permission decisions
            log.info("Role registered: %s", role.name)

    def register_user(self, user: User) -> None:
        """Register a user."""
        with self._lock:
            self.users[user.user_id] = user
            self._cache.clear()  # Invalidate stale permission decisions
            log.info("User registered: %s with roles: %s", user.user_id, user.roles)

    def get_role(self, role_name: str) -> Optional[Role]:
        """Get a role by name."""
        return self.roles.get(role_name)

    def get_user(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        return self.users.get(user_id)

    def grant_permission(
        self,
        role_name: str,
        resource: str,
        action: str,
        scope: str = PermissionScope.ANY,
        conditions: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Grant a permission to a registered role and invalidate the cache."""
        with self._lock:
            role = self.roles.get(role_name)
            if role is None:
                raise ValueError(f"Role '{role_name}' is not registered.")
            role.grant_permission(resource, action, scope, conditions)
            self._cache.clear()
            log.info("Granted %s:%s:%s to role %s", resource, action, scope, role_name)

    def revoke_permission(
        self,
        role_name: str,
        resource: str,
        action: str,
        scope: str = PermissionScope.ANY,
    ) -> None:
        """Revoke a permission from a registered role and invalidate the cache."""
        with self._lock:
            role = self.roles.get(role_name)
            if role is None:
                raise ValueError(f"Role '{role_name}' is not registered.")
            role.revoke_permission(resource, action, scope)
            self._cache.clear()
            log.info("Revoked %s:%s:%s from role %s", resource, action, scope, role_name)

    def add_validator(self, validator: Callable[[Dict[str, Any]], bool]) -> None:
        """
        Add a custom permission validator.

        Validator receives context dict and returns True if allowed.
        """
        self._validators.append(validator)
        log.info("Custom validator registered")

    def has_permission(
        self,
        user_id: str,
        resource: str,
        action: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check if user has permission to perform action on resource.

        Args:
            user_id: User ID to check
            resource: Resource type (e.g., "audit_log")
            action: Action (e.g., "read")
            context: Runtime context (tenant_id, record_owner, etc.)

        Returns:
            True if access allowed, False otherwise
        """
        start_time = time.time()

        # Check cache
        cache_key = f"{user_id}:{resource}:{action}:{str(context)}"
        if self.enable_caching and cache_key in self._cache:
            allowed, cached_at = self._cache[cache_key]
            if (time.time() - cached_at) < self.cache_ttl_sec:
                log.debug(
                    "Permission check CACHED: %s:%s:%s -> %s", user_id, resource, action, allowed
                )
                return allowed

        with self._lock:
            user = self.users.get(user_id)
            if not user:
                self._record_decision(
                    AccessDecision(
                        allowed=False,
                        reason="User not found",
                        principal=user_id,
                        resource=resource,
                        action=action,
                        duration_ms=(time.time() - start_time) * 1000,
                    )
                )
                return False

            # Check all assigned roles (impersonated role only if set)
            role_names = [user.delegated_role] if user.delegated_role else sorted(user.roles)

            if not role_names:
                self._record_decision(
                    AccessDecision(
                        allowed=False,
                        reason="User has no roles",
                        principal=user_id,
                        resource=resource,
                        action=action,
                        duration_ms=(time.time() - start_time) * 1000,
                    )
                )
                return False

            # Check if ANY role grants permission (standard RBAC semantics)
            context = context or {}
            scope = context.get("scope", PermissionScope.ANY)

            allowed = False
            for role_name in role_names:
                role = self.roles.get(role_name)
                if not role:
                    continue

                if role.has_permission(resource, action, scope):
                    allowed = True
                    break

            # Run custom validators
            if allowed:
                for validator in self._validators:
                    if not validator(context):
                        allowed = False
                        break

            # Check context-aware conditions
            if allowed and role is not None:
                allowed = self._evaluate_conditions(role, resource, action, context)

            # Cache result
            if self.enable_caching:
                self._cache[cache_key] = (allowed, time.time())
                self._evict_cache_if_needed()

            decision = AccessDecision(
                allowed=allowed,
                reason="Permission granted" if allowed else "Permission denied",
                principal=user_id,
                resource=resource,
                action=action,
                duration_ms=(time.time() - start_time) * 1000,
            )
            self._record_decision(decision)

            log.info(
                "Permission check: %s:%s:%s -> %s (%.2fms)",
                user_id,
                resource,
                action,
                allowed,
                decision.duration_ms,
            )

            return allowed

    def _evict_cache_if_needed(self) -> None:
        """Bound ``_cache`` (GB-040): drop expired entries first, then the
        oldest remaining entries, until the cache is back under
        ``MAX_CACHE_ENTRIES``. Must be called with ``self._lock`` held."""
        if len(self._cache) <= self.MAX_CACHE_ENTRIES:
            return

        now = time.time()
        expired = [
            k
            for k, (_, cached_at) in self._cache.items()
            if (now - cached_at) >= self.cache_ttl_sec
        ]
        for key in expired:
            del self._cache[key]

        if len(self._cache) > self.MAX_CACHE_ENTRIES:
            oldest_first = sorted(self._cache.items(), key=lambda item: item[1][1])
            overflow = len(self._cache) - self.MAX_CACHE_ENTRIES
            for key, _ in oldest_first[:overflow]:
                del self._cache[key]

    def _evaluate_conditions(
        self,
        role: Role,
        resource: str,
        action: str,
        context: Dict[str, Any],
    ) -> bool:
        """Evaluate permission conditions against context."""
        for perm in role.get_all_permissions():
            if perm.matches(resource, action) and perm.conditions:
                # Simple condition matching: key=value
                for key, value in perm.conditions.items():
                    if context.get(key) != value:
                        return False
        return True

    def impersonate(self, role_name: str, requesting_user_id: str, max_seconds: int = 300):
        """
        Removed (GB-040). This used to be a context manager that granted a
        user a different role in-process for its duration, protected only by
        a daemon watchdog thread with no external audit trail, no durable
        record, and no way for anyone outside this process to revoke it early
        -- a privilege-escalation mechanism with no real safety boundary. There
        is no safe way to hedge this in place, so it is removed rather than
        hardened.

        Raises:
            NotImplementedError: always. See docs/CLAIMS.md's hardening
                notes on this module.
        """
        raise NotImplementedError(
            "AccessControl.impersonate() was removed: it granted "
            "elevated roles in-process with no external audit trail or "
            "revocation. There is no drop-in replacement -- model "
            "impersonation as an explicit, durably-recorded decision through "
            "glassbox.app.decision_service instead."
        )

    def _record_decision(self, decision: AccessDecision) -> None:
        """Record access decision for audit."""
        self._decision_log.append(decision)
        # Keep only last 10000 decisions in memory
        if len(self._decision_log) > 10000:
            self._decision_log = self._decision_log[-10000:]

    def get_decision_history(self, limit: int = 100) -> List[AccessDecision]:
        """Get recent access decisions."""
        return list(reversed(self._decision_log[-limit:]))

    def clear_cache(self) -> None:
        """Clear permission cache."""
        with self._lock:
            self._cache.clear()
            log.info("Permission cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get access control statistics."""
        with self._lock:
            return {
                "roles_count": len(self.roles),
                "users_count": len(self.users),
                "cache_entries": len(self._cache),
                "decision_history_size": len(self._decision_log),
                "caching_enabled": self.enable_caching,
            }
