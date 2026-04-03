# glassbox/rules — Declarative Rules Engine

The `rules` package enables non-developers to author governance policies using YAML or JSON — no Python required.

| Module | Role |
|---|---|
| `rules_engine.py` | `RuleCondition`, `DeclarativeRule`, `RulesLoader`, 12 operators |

**Supported operators:** `gt` `gte` `lt` `lte` `eq` `neq` `in` `not_in` `missing` `present` `contains` `regex`

```yaml
# rules/my_limits.yaml
rules:
  - policy_id: ORG-001
    name: Spending Cap
    applies_to: [procurement]
    logic: and
    conditions:
      - field: amount
        op: gt
        value: 100000
      - field: approval_ref
        op: missing
    result: fail
    message: "Amount {amount} requires approval_ref."
```

```python
from glassbox.rules.rules_engine import RulesLoader
RulesLoader().load_and_register("rules/", pipeline.policy_engine, is_directory=True)
```
