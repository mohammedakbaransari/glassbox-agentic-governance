# glassbox/rules — Declarative Rules Engine

The `rules` package enables non-developers to author governance policies using YAML or JSON — no Python required.

| Module | Role |
|---|---|
| `rules_engine.py` | `RuleCondition`, `DeclarativeRule`, `RulesLoader`, 12 operators |

**Supported operators:** `gt` `gte` `lt` `lte` `eq` `neq` `in` `not_in` `missing` `present` `contains` `regex`

---

## Quick Start: Writing Your First Rule

```yaml
# rules/spending_limits.yaml
rules:
  - policy_id: ORG-001
    name: "Spending Cap for Non-Approved Suppliers"
    applies_to: [procurement]
    logic: and
    conditions:
      - field: amount
        op: gt
        value: 100000
      - field: approval_ref
        op: missing
    result: fail
    message: "Spending over $100K requires prior approval (approval_ref missing)."

  - policy_id: ORG-002
    name: "Restricted Vendors"
    applies_to: [procurement]
    conditions:
      - field: vendor_id
        op: in
        value: ["VENDOR-BLOCKED-001", "VENDOR-BLOCKED-002"]
    result: block
    message: "Vendor {vendor_id} is restricted by compliance."

  - policy_id: ORG-003
    name: "Email Domain Whitelist"
    applies_to: [crm]
    conditions:
      - field: requester_email
        op: regex
        value: "^.*@company\\.com$"
    result: pass
    message: "Internal company email verified."
```

### Loading Rules

```python
from glassbox.rules.rules_engine import RulesLoader
from glassbox.governance.pipeline import GovernancePipeline

# Load all rules from directory
loader = RulesLoader()
loader.load_and_register(
    path="rules/",
    engine=pipeline.policy_engine,
    is_directory=True
)

# Or load single file
loader.load_and_register(
    path="rules/spending_limits.yaml",
    engine=pipeline.policy_engine,
    is_directory=False
)
```

---

## Rule Authoring Guide

### Operator Reference

| Operator | Type | Example | Matches |
|----------|------|---------|---------|
| `gt` | Numeric > | `{amount: 50000, op: gt, value: 10000}` | 50000 > 10000 ✓ |
| `gte` | Numeric ≥ | `{amount: 10000, op: gte, value: 10000}` | 10000 ≥ 10000 ✓ |
| `lt` | Numeric < | `{age: 18, op: lt, value: 21}` | 18 < 21 ✓ |
| `lte` | Numeric ≤ | `{age: 21, op: lte, value: 21}` | 21 ≤ 21 ✓ |
| `eq` | Equality | `{status: active, op: eq, value: active}` | ✓ |
| `neq` | Not equal | `{status: inactive, op: neq, value: active}` | ✓ |
| `in` | List membership | `{country: "US", op: in, value: ["US","CA"]}` | ✓ |
| `not_in` | List exclusion | `{country: "RU", op: not_in, value: ["RU","KP"]}` | ✓ |
| `missing` | Field absent | `{approval_ref: null, op: missing}` | ✓ |
| `present` | Field exists | `{approval_ref: "A123", op: present}` | ✓ |
| `contains` | Substring | `{description: "payment ... failed", op: contains, value: "failed"}` | ✓ |
| `regex` | Pattern match | `{email: "user@co.com", op: regex, value: ".*@co\\.com"}` | ✓ |

### Logic: `and` vs `or`

```yaml
# AND logic: ALL conditions must match
- policy_id: RULE-1
  name: "Strict Rule"
  logic: and  # <-- ALL must be true
  conditions:
    - field: amount
      op: gt
      value: 50000
    - field: country
      op: in
      value: ["US", "CA"]
  # Triggers only if: amount > 50000 AND country in [US, CA]

# OR logic: ANY condition matches
- policy_id: RULE-2
  name: "Broad Rule"
  logic: or   # <-- ANY is true
  conditions:
    - field: amount
      op: gt
      value: 1000000
    - field: vendor_id
      op: in
      value: ["RISKY-001", "RISKY-002"]
  # Triggers if: amount > 1M OR vendor is risky
```

---

## Performance Characteristics

| Operation | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| evaluate_rule() | 0.1–0.5 ms | 2,000–10K rules/sec | Single rule evaluation |
| evaluate_all_rules() | 1–5 ms | 200–1K decisions/sec | 20 rules evaluated per decision |
| load_rules() | 10–50 ms | — | Parse YAML, build index |
| Hot reload() | 50–100 ms | — | Reload without restart |

**Optimization:**
```yaml
# Rule order matters: put high-frequency rules first
rules:
  - policy_id: FAST-001  # Evaluated first
    conditions:
      - field: amount
        op: lt
        value: 1000  # Most decisions < $1K
    result: pass

  - policy_id: SLOW-001  # Evaluated second
    conditions:
      - field: amount
        op: gt
        value: 100000
      - field: requires_audit
        op: eq
        value: true
    result: review
```

---

## Common Errors

### Error: "Field not found in payload"

**Symptom:**
```yaml
conditions:
  - field: vendor_id  # Payload has 'vendor' not 'vendor_id'
    op: eq
    value: "V-001"
```
Results in: `Rule did not match (condition evaluation returned null)`

**Solution:**
```yaml
# Check payload structure first
# Option 1: Use correct field name
- field: vendor  # Not vendor_id

# Option 2: Use nested field syntax
- field: supplier.vendor_id
  op: eq
  value: "V-001"

# Option 3: Debug payload before running rules
from glassbox.rules.rules_engine import RuleCondition
print(f"Payload fields: {decision_payload.keys()}")
```

### Error: "Regex pattern invalid"

**Symptom:**
```yaml
- field: email
  op: regex
  value: "^[a-zA-Z0-9@\\.com$"  # Unmatched parentheses
```
Results in: `Invalid regex pattern: unterminated character set`

**Solution:**
```yaml
# Fix regex syntax
- field: email
  op: regex
  value: "^[a-zA-Z0-9._%+-]+@company\\.com$"

# Escape special characters properly
- field: url
  op: regex
  value: "https://api\\.example\\.com/.*"
```

### Error: "Type mismatch: comparing string to number"

**Symptom:**
```yaml
- field: amount
  op: gt
  value: "100000"  # String instead of number
```
Results in: String-to-number comparison fails

**Solution:**
```yaml
# Use proper YAML types
- field: amount
  op: gt
  value: 100000   # Number, not string

- field: status
  op: eq
  value: "active" # String, in quotes
```

### Error: "Rule result invalid; must be pass|fail|block|review"

**Symptom:**
```yaml
- policy_id: RULE-001
  result: approve  # Invalid; should be one of pass/fail/block/review
```

**Solution:**
```yaml
# Use valid result values
- policy_id: RULE-001
  result: pass    # OK: Allow
- policy_id: RULE-002
  result: fail    # OK: Log violation but allow
- policy_id: RULE-003
  result: block   # OK: Drop decision
- policy_id: RULE-004
  result: review  # OK: Route to manual review
```

---

## Hot Reload (Update Rules Without Restart)

```python
from glassbox.rules.hot_reload import enable_hot_reload

# Watch rules directory for changes
enable_hot_reload(
    rules_directory="rules/",
    engine=pipeline.policy_engine,
    poll_interval_seconds=5  # Check for updates every 5 seconds
)

# Now: Edit rules/spending_limits.yaml
# In 5 seconds: New rules automatically loaded, no restart
```

---

## Advanced: Multi-Condition Rules with Nested Logic

```yaml
# Complex rule: (A AND B) OR (C AND D)
- policy_id: COMPLEX-001
  name: "Multi-condition Risk Assessment"
  logic: or
  conditions:
    # Branch 1: High-value from new vendor
    - logic: and
      conditions:
        - field: amount
          op: gt
          value: 50000
        - field: vendor_age_days
          op: lt
          value: 90
    # Branch 2: Restricted country + large transfer
    - logic: and
      conditions:
        - field: country
          op: in
          value: ["KP", "IR", "SY"]
        - field: amount
          op: gt
          value: 100000
  result: block
  message: "Transaction blocked by risk rule."
```

---

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#rules-engine) for technical details and [../../docs/USECASES.md](../../docs/USECASES.md) for policy examples.
