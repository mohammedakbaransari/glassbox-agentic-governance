# glassbox/store — Transactional Relational Storage

The `store` package provides the persistence layer using Python stdlib `sqlite3`.

| Module | Role |
|---|---|
| `database.py` | `GlassBoxDB` — unified SQLite database, ACID transactions, WAL mode, schema migrations |
| `repository.py` | Repository interfaces + implementations: `PolicyRepository`, `AuditRepository`, `WorkflowRepository` |

**Why SQLite over JSON document stores:**
- Compliance evidence requires JOINs (which decisions satisfy which controls?)
- ACID transactions are mandatory for audit records
- Foreign key integrity enforces referential consistency
- Zero extra dependencies (Python stdlib)
- WAL mode enables concurrent read/write from multiple threads

```python
from glassbox.store.database import GlassBoxDB

db = GlassBoxDB("/var/lib/glassbox/glassbox.db")

# All repos backed by the same transactional database
audit_repo    = db.audit_repo()
workflow_repo = db.workflow_repo()
policy_repo   = db.policy_repo()

# Explicit transactions
with db.transaction() as tx:
    tx._execute("UPDATE policies SET status=? WHERE policy_id=?", ("deprecated","OLD-001"))
    tx._execute("INSERT INTO policies ...", (...))
    # auto-committed on exit, auto-rolled-back on exception
```
