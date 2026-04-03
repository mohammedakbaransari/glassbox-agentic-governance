# Contributing to GlassBox

**Thank you for your interest in contributing to GlassBox!**

---

## Ways to Contribute

1. **New policies** — Add built-in policies for new domains
2. **New adapters** — Add platform or AI framework adapters
3. **New examples** — Add industry use-case examples
4. **Bug fixes** — Fix issues identified in any component
5. **Documentation** — Improve or expand any documentation
6. **Tests** — Add test coverage for edge cases

---

## Development Setup

```bash
git clone https://github.com/mohammedakbaransari/runtime-decision-governance
cd runtime-decision-governance
pip install flask pyyaml   # optional dependencies for tests

# Run all tests
GLASSBOX_LOG_LEVEL=CRITICAL python3 -m unittest \
  tests.test_glassbox tests.test_load_stress_security \
  tests.test_framework tests.test_advanced
```

---

## Adding a Built-In Policy

1. Add the rule function to `glassbox/governance/policy_engine.py`
2. Add to `DEFAULT_POLICIES` list
3. Add test in `tests/test_glassbox.py` (class `TestPolicyEngine`)
4. Document in `docs/COMPLIANCE.md` if it maps to a standard

```python
def _my_domain_policy(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    value = float(payload.get("my_field", 0))
    if value > MY_LIMIT:
        return PolicyEvaluation("MY-001", "My Policy", "fail",
            f"Value {value} exceeds limit {MY_LIMIT}")
    return PolicyEvaluation("MY-001", "My Policy", "pass", "OK")
```

---

## Zero-Dependency Principle

GlassBox core has **zero mandatory dependencies**. All Python stdlib. When adding a new feature:
- If it requires an external package, make it opt-in (`optional-dependencies` in `pyproject.toml`)
- Guard the import: `try: import pyyaml except ImportError: ...`
- Document the optional dependency clearly

---

## Thread-Safety Requirements

Every new component that holds mutable state **must** protect it with a lock:
- Use `threading.RLock` when the same thread may re-enter
- Use `threading.Lock` for simple read/write protection
- Never hold a lock while calling user-supplied code (policy rules, callbacks)
- Always take a snapshot of shared state under the lock, then process outside

---

## Testing Requirements

New code must include tests:
- Core pipeline features → `tests/test_glassbox.py`
- Framework components (repos, events, rules) → `tests/test_framework.py`
- Advanced features (orchestration, RAG, multi-tenancy) → `tests/test_advanced.py`
- Load/stress → `tests/test_load_stress_security.py`

Minimum: one happy-path test + one edge-case test per public method.

---

## Code Style

- Python 3.9+ compatible
- Type hints on all public method signatures
- Docstring on every public class and method
- No f-strings with backslashes (Python 3.11 compatibility)
- British English in comments and docstrings (the project uses British English)

---

## Submitting Changes

1. Fork the repository
2. Create a branch: `git checkout -b feature/my-policy`
3. Add tests and ensure all 383 pass
4. Update CHANGELOG.md
5. Submit a pull request

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
