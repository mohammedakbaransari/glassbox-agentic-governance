# glassbox/rules

Declarative rule loading and registration for policy-as-data workflows.

## Key Modules

- `rules_engine.py`: rule parsing, condition operators, registration helpers
- `hot_reload.py`: optional runtime rule refresh support

## Quick Start

```python
from glassbox.rules.rules_engine import RulesLoader
from glassbox.governance.pipeline import GovernancePipeline

pipeline = GovernancePipeline()
RulesLoader().load_and_register("rules/", pipeline.policy_engine, is_directory=True)
```

## Operational Notes

- Prefer declarative rules for frequent policy updates by non-core developers.
- Keep schema and field naming conventions stable across producer systems.
- Validate new rules in staging before production rollout.

## Testing

```bash
python -m pytest tests/test_framework.py -q
python -m pytest tests/test_regression.py -q
```

## Related Docs

- [docs/DEVELOPMENT/implementation_guide.md](../../docs/DEVELOPMENT/implementation_guide.md)
- [docs/FEATURES/enterprise.md](../../docs/FEATURES/enterprise.md)