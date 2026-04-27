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

## Commit Message Convention

Use **conventional commits** format to keep commit history clean and scannable:

**Format:** `<type>(<scope>): <subject>`

```
feat(policy_engine): Add new policy PROC-001
fix(anomaly_detector): Thread-safety issue in baseline update
docs(README): Update installation section
test(velocity_breaker): Add edge case tests
refactor(pipeline): Simplify stage orchestration
perf(policy_engine): Add caching for policy evaluation
```

### Types
- `feat` — New feature or policy
- `fix` — Bug fix
- `docs` — Documentation changes
- `test` — Test additions/changes
- `refactor` — Code structure changes (no behaviour change)
- `perf` — Performance improvements
- `chore` — Dependency updates, tooling

### Rules
- Use imperative mood ("Add" not "Adds")
- Don't capitalize first letter of subject
- Keep subject under 50 characters
- Include issue reference if applicable: `fix(pipeline): Resolve #123`

---

## Code Review Checklist

Before submitting a PR, ensure:

- [ ] **Thread-safety**: All mutable state protected by locks
- [ ] **Type hints**: All public method signatures have type hints
- [ ] **Docstrings**: Every public class and method has a docstring
- [ ] **Tests**: Min 1 happy-path + 1 edge-case test per public method
- [ ] **Tests passing**: All 551 tests pass — `python3 -m unittest ...`
- [ ] **No breaking changes**: Or documented in CHANGELOG.md
- [ ] **Python 3.9+ compatible**: No f-strings with backslashes
- [ ] **No new mandatory dependencies**: Only optional in `pyproject.toml`
- [ ] **CHANGELOG.md updated**: Add entry under [Unreleased] section
- [ ] **No hardcoded secrets**: Use environment variables

**Reviewers will check:**
- Correctness of logic
- Performance impact (P99 latency < 0.5 ms)
- Security implications (no injection vulnerabilities)
- Compliance with framework design patterns
- Test coverage adequacy

---

## Security Vulnerability Reporting

**IMPORTANT:** Never file vulnerabilities as public GitHub issues.

### Reporting Process

1. **Email:** `security@glassbox.dev` (monitored address)
   - Subject: `[SECURITY] GlassBox Vulnerability Report`
   - Include: Description, reproduction steps, impact

2. **Information to provide:**
   - Vulnerability type (SQL injection, XSS, bypass, etc.)
   - Affected component (e.g., `PayloadSanitizer`)
   - Minimal reproduction code
   - Your suggested fix (if any)

3. **Disclosure Timeline:**
   - Vulnerabilities fixed within 7 days
   - Security patch released within 14 days
   - Public disclosure coordinated after patch availability

4. **Recognition:**
   - Credited in release notes (if desired)
   - Listed in SECURITY.md

---

## Performance Baseline

Before committing performance-related code:

```bash
# Run benchmarks on your changes
GLASSBOX_LOG_LEVEL=CRITICAL python3 -m glassbox.benchmarks.run_benchmarks
```

**Expected baselines:**
- Single-thread throughput: > 3,000 decisions/sec
- P99 latency: < 0.5 ms
- P50 latency: < 0.15 ms
- No memory leaks (run with `tracemalloc`)

If your change degrades performance, profile and optimize before committing.

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

## Python Version Support Policy

- **Supported**: Python 3.9, 3.10, 3.11, 3.12
- **EOL schedule**: Python 3.9 support dropped when it reaches EOL (Oct 2025)
- **Testing**: Always test locally with oldest (3.9) and newest (3.12) supported versions
- **CI**: GitHub Actions CI tests all 4 versions

---

## Adding a New Module

If adding a new package (e.g., `glassbox/my_feature/`):

1. **Create structure:**
   ```
   glassbox/my_feature/
   ├── __init__.py
   ├── core.py         # Main implementation
   ├── models.py       # Dataclasses (if needed)
   └── README.md       # Module documentation
   ```

2. **Add tests:**
   ```
   tests/test_my_feature.py  # Add to appropriate suite
   ```

3. **Document:**
   - Add section to `docs/ARCHITECTURE.md` component map
   - Create `glassbox/my_feature/README.md` with examples
   - Update `CHANGELOG.md`

4. **Verify:**
   - No circular imports
   - All tests pass
   - Type hints complete

---

## Zero-Dependency Principle

GlassBox core has **zero mandatory dependencies**. When adding a feature:

**If it requires an external package:**
1. Make it **optional** in `pyproject.toml`
2. **Guard the import:**
   ```python
   try:
       import pyyaml
   except ImportError:
       raise ImportError("YAML support requires: pip install pyyaml")
   ```
3. **Document clearly** in README and type hints

---

## Thread-Safety Requirements

Every new component holding mutable state **must** protect it:

```python
import threading

class MyComponent:
    def __init__(self):
        self._lock = threading.RLock()  # RLock for potential re-entry
        self._state = {}
    
    def update_state(self, key, value):
        with self._lock:
            self._state[key] = value  # Safe
        # Never hold lock while calling user code!
    
    def expensive_operation(self):
        with self._lock:
            snapshot = dict(self._state)  # Take snapshot
        
        # Process outside lock
        result = self._process(snapshot)
        return result
```

**Rules:**
- Use `threading.RLock` when thread may re-enter
- Use `threading.Lock` for simple read/write
- Always take snapshot under lock, process outside
- Never call user-supplied callbacks (policies, handlers) while holding lock

---

## Testing Requirements

### Test Placement

- **Core pipeline**: `tests/test_glassbox.py`
- **Framework components** (repos, events, rules, workflow): `tests/test_framework.py`
- **Advanced features** (orchestration, RAG, multi-tenancy): `tests/test_advanced.py`
- **Load/stress/security**: `tests/test_load_stress_security.py`

### Test Naming

```python
class TestMyComponent:
    def test_happy_path(self):
        """Main success scenario."""
        ...
    
    def test_edge_case_empty_input(self):
        """Edge case: empty input."""
        ...
    
    def test_error_case_invalid_state(self):
        """Error case: invalid state raises exception."""
        ...
```

### Minimum Coverage

Per public method:
- 1 happy-path test
- 1 edge-case test
- 1 error-case test (if possible)

---

## Code Style

- **Python**: 3.9+ compatible
- **Type hints**: Required on all public signatures
- **Docstrings**: Google style, required on all public items
- **Line length**: 100 characters (tool: `black`)
- **Linting**: Must pass `pylint`/`flake8`
- **Imports**: Sorted with `isort`

### Example

```python
def evaluate_policy(
    policy_id: str,
    payload: Dict[str, Any],
    context: DecisionContext
) -> PolicyEvaluation:
    """Evaluate a single policy against a decision.
    
    Args:
        policy_id: Unique policy identifier (e.g., 'PROC-001')
        payload: Decision payload to evaluate
        context: Decision context (agent, timestamp, environment, etc.)
    
    Returns:
        PolicyEvaluation with result ('pass' or 'fail') and message
    
    Raises:
        PolicyNotFoundError: If policy_id not registered
        TypeError: If payload is not a dict
    
    Example:
        >>> eval_result = engine.evaluate_policy(
        ...     'PROC-001',
        ...     {'amount': 50_000},
        ...     ctx
        ... )
        >>> print(eval_result.result)
        'pass'
    """
    ...
```

---

## Submitting Changes

1. **Fork** the repository
2. **Create** a feature branch: `git checkout -b feature/my-policy`
3. **Code** with tests (see Tests Requirements)
4. **Verify** all tests pass and performance is acceptable
5. **Update** CHANGELOG.md with your changes
6. **Commit** using conventional commits format
7. **Push** to your fork
8. **Create** a pull request with clear description

**PR Description Template:**
```markdown
## Description
Brief explanation of what this PR does

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Performance improvement

## Testing Done
- [ ] All 551 tests passing
- [ ] New tests added (X new tests)
- [ ] Performance tested: [latency/throughput results]

## Checklist
- [ ] Code follows style guide
- [ ] Type hints added
- [ ] Tests added/updated
- [ ] CHANGELOG.md updated
- [ ] No breaking changes (or documented)
```

---

## Code Review Expectations

- Respectful, constructive feedback
- Assume good intent
- Focus on code, not person
- Ask questions rather than making demands
- Approve once concerns addressed

**Timeline:** PRs reviewed within 3 business days.

---

## Recognition

Contributors are recognized in:
1. CHANGELOG.md (in release notes)
2. GitHub contributors page
3. Special collaborators section (if substantial contribution)

---

*Thank you for contributing to GlassBox!*

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher ·  *
