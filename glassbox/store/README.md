# glassbox/store

Persistence and repository abstractions for policies, workflows, and audit records.

## Key Modules

- `database.py`: SQLite-oriented storage wrapper
- `database_abstraction.py`: backend abstraction/factory
- `repository.py`: repository interfaces and implementations

## Quick Start

```python
from glassbox.store.database import GlassBoxDB

db = GlassBoxDB("./glassbox.db")
audit_repo = db.audit_repo()
workflow_repo = db.workflow_repo()
policy_repo = db.policy_repo()
```

## Operational Notes

- Keep persistence configured for production auditability.
- For scale-out or different backend strategy, use abstraction/factory paths.
- Align retention/backup with your compliance requirements.

## Testing

```bash
python -m pytest tests/test_sqlite_repo.py -q
python -m pytest tests/test_framework.py -q
```

## Related Docs

- [docs/DEPLOYMENT/deployment_reference.md](../../docs/DEPLOYMENT/deployment_reference.md)
- [docs/COMPLIANCE/requirements.md](../../docs/COMPLIANCE/requirements.md)