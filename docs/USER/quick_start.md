# GlassBox Framework v1.1.0 — Quick Navigation Guide

Welcome to GlassBox Framework v1.1.0! This guide helps you find what you need.

## 📍 I Want To...

### 🚀 Get Started Quickly
- **New to GlassBox?** Start here: [Quick Start Guide](#quick-start)
- **Want examples?** See: [Integration Examples](#integration-examples)
- **Need to deploy?** See: [Deployment Guide](#deployment-guide)

### 📚 Learn About Features
- **Database support** → [Database Abstraction](#1-database-abstraction)
- **User permissions** → [Access Control](#2-access-control)
- **Encryption** → [Encryption & Secrets](#3-encryption--secrets)
- **Audit trails** → [Advanced Audit Logging](#4-advanced-audit-logging)
- **Multi-tenant support** → [Request Context](#5-request-context)
- **API security** → [API Gateway](#6-api-gateway)

### 🔍 Find Documentation
- **Full API reference** → [docs/API.md](docs/API.md)
- **Enterprise features** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md)
- **Release notes** → [RELEASE_NOTES_v1_1.md](RELEASE_NOTES_v1_1.md)
- **Deployment guide** → [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- **Troubleshooting** → [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

### 💻 See Code Examples
- **Complete integration tests** → [tests/test_v1_1_enterprise.py](tests/test_v1_1_enterprise.py) (30+ tests)
- **Database examples** → See `TestDatabaseAbstraction` class
- **Access control examples** → See `TestAccessControl` class
- **Encryption examples** → See `TestEncryption` class
- **Audit logging examples** → See `TestAdvancedAudit` class
- **End-to-end flow** → See `TestEndToEndIntegration` class

### 🔐 Understand Security
- **Security hardening** → [docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md)
- **Encryption details** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#encryption--secrets-management)
- **Access control** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#advanced-access-control)
- **Audit trails** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#advanced-audit-logging)

### ⚙️ Configure & Deploy
- **Configuration** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#request-context--configuration)
- **Docker setup** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#deployment-guide)
- **Production checklist** → [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#production-checklist)
- **Performance tuning** → [docs/PERFORMANCE_TUNING.md](docs/PERFORMANCE_TUNING.md)

---

## 📦 Six New Modules in v1.1.0

### 1. Database Abstraction
**File:** `glassbox/store/database_abstraction.py`

Pluggable multi-database support (SQLite, PostgreSQL, SQL Server)

**Key Classes:**
- `DatabaseFactory` — Create database backends
- `DatabaseBackend` — Abstract interface
- `SQLiteBackend` — Embedded database
- `PostgreSQLBackend` — Production database
- `SQLServerBackend` — Enterprise database
- `ConnectionPool` — Manage connections

**Quick Example:**
```python
from glassbox.store.database_abstraction import DatabaseFactory

# SQLite (development)
db = DatabaseFactory.create("sqlite", db_path=":memory:")

# PostgreSQL (production)
db = DatabaseFactory.create("postgresql", host="pg.example.com")

result = db.query_one("SELECT * FROM users WHERE id=?", (123,))
db.close()
```

**Learn More:** [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#database-abstraction-layer)

---

### 2. Advanced Access Control
**File:** `glassbox/governance/access_control.py`

Enterprise RBAC with role hierarchy and attribute-based conditions

**Key Classes:**
- `AccessControl` — Main engine
- `Role` — Define roles with permissions
- `User` — Users with roles
- `Permission` — resource:action:scope model
- `AccessDecision` — Audit decisions

**Quick Example:**
```python
from glassbox.governance.access_control import AccessControl, Role, PermissionScope

ac = AccessControl()

admin = Role("admin")
admin.grant_permission("policy", "write", PermissionScope.ANY)

ac.register_role(admin)
user.add_role("admin")

can_write = ac.has_permission("user1", "policy", "write")
```

**Learn More:** [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#advanced-access-control)

---

### 3. Encryption & Secrets
**File:** `glassbox/governance/encryption.py`

AES-256-GCM encryption for data protection

**Key Classes:**
- `CryptoManager` — Encryption operations
- `EncryptedField` — Encrypted field wrapper
- `SecretManager` — In-memory secret storage

**Quick Example:**
```python
from glassbox.governance.encryption import CryptoManager

crypto = CryptoManager()

encrypted = crypto.encrypt(b"sensitive data")
decrypted = crypto.decrypt(encrypted)

hashed, salt = CryptoManager.hash_password("password")
```

**Learn More:** [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#encryption--secrets-management)

---

### 4. Advanced Audit Logging
**File:** `glassbox/governance/advanced_audit.py`

Immutable append-only audit trail with hash chain

**Key Classes:**
- `AuditLogger` — Main logger
- `AuditRecord` — Audit record

**Quick Example:**
```python
from glassbox.governance.advanced_audit import AuditLogger

logger = AuditLogger(enable_hash_chain=True)

logger.log_action("user1", "update", "policy", "p123", "success")

records = logger.search(user_id="user1")
is_valid = logger.verify_hash_chain()  # Detect tampering
```

**Learn More:** [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#advanced-audit-logging)

---

### 5. Request Context & Configuration
**File:** `glassbox/governance/request_context.py`

Thread-local context and configuration management

**Key Classes:**
- `RequestContext` — Request-scoped data
- `Config` — Configuration management
- `ContextManager` — Context lifecycle

**Quick Example:**
```python
from glassbox.governance.request_context import RequestContext, Config, ContextManager

with ContextManager(user_id="user1", tenant_id="acme"):
    ctx = RequestContext.get_current()
    print(ctx.user_id)  # "user1"

config = Config.load("/etc/glassbox/config.yaml")
db_host = config.get("database.host")
```

**Learn More:** [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#request-context--configuration)

---

### 6. API Gateway & Middleware
**File:** `glassbox/governance/api_gateway.py`

Composable middleware pipeline for API security

**Key Classes:**
- `APIGateway` — Main gateway
- `Middleware` — Base middleware class
- `AuthenticationMiddleware` — Token validation
- `RateLimitMiddleware` — Request throttling
- `RequestValidationMiddleware` — Request validation
- `RequestLoggingMiddleware` — Request/response logging
- `CORSMiddleware` — CORS support

**Quick Example:**
```python
from glassbox.governance.api_gateway import APIGateway, Response, AuthenticationMiddleware

gateway = APIGateway()
gateway.add_middleware(AuthenticationMiddleware(secret_key="token"))

def handler(request):
    return Response(status_code=200, body={"success": True})

gateway.register_route("GET", "/api/data", handler)
response = gateway.handle_request("GET", "/api/data")
```

**Learn More:** [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md#api-gateway--middleware)

---

## 🧪 Testing

**Comprehensive test suite:** `tests/test_v1_1_enterprise.py`

30+ tests covering all modules:
- `TestDatabaseAbstraction` (5 tests)
- `TestAccessControl` (5 tests)
- `TestEncryption` (6 tests)
- `TestAdvancedAudit` (4 tests)
- `TestRequestContext` (3 tests)
- `TestAPIGateway` (5 tests)
- `TestEndToEndIntegration` (1 test)

**Run tests:**
```bash
pytest tests/test_v1_1_enterprise.py -v
```

---

## 📚 Quick Start

### 1. Installation

```bash
pip install -r requirements.txt
```

### 2. Basic Example

```python
from glassbox.store.database_abstraction import DatabaseFactory
from glassbox.governance.access_control import AccessControl, Role
from glassbox.governance.advanced_audit import AuditLogger
from glassbox.governance.encryption import CryptoManager

# Setup database
db = DatabaseFactory.create("sqlite", db_path=":memory:")

# Setup access control
ac = AccessControl()
analyst = Role("analyst")
analyst.grant_permission("data", "read", "own_tenant")
ac.register_role(analyst)

# Setup encryption
crypto = CryptoManager()

# Setup audit logging
logger = AuditLogger()

# Use
db.execute("CREATE TABLE data (id INTEGER, value TEXT)")
db.execute("INSERT INTO data VALUES (1, 'test')")

result = db.query_one("SELECT * FROM data WHERE id=?", (1,))
print(result)  # {'id': 1, 'value': 'test'}

encrypted = crypto.encrypt(b"sensitive")
decrypted = crypto.decrypt(encrypted)

logger.log_action("user1", "read", "data", "1", "success")
print(logger.verify_hash_chain())  # True
```

---

## 🗂️ File Structure

```
glassbox-agentic-governance/
├── glassbox/
│   ├── store/
│   │   └── database_abstraction.py       ✨ NEW
│   └── governance/
│       ├── access_control.py             ✨ NEW
│       ├── encryption.py                 ✨ NEW
│       ├── advanced_audit.py             ✨ NEW
│       ├── request_context.py            ✨ NEW
│       ├── api_gateway.py                ✨ NEW
│       └── ...
├── tests/
│   ├── test_v1_1_enterprise.py           ✨ NEW
│   └── ...
├── docs/
│   ├── ENTERPRISE_FEATURES_v1_1.md       ✨ NEW
│   ├── API.md
│   ├── DEPLOYMENT.md
│   └── ...
├── V1_1_0_DELIVERY_SUMMARY.md            ✨ NEW
├── RELEASE_NOTES_v1_1.md                 ✨ NEW
└── ...
```

---

## 📊 Quick Stats

| Metric | Value |
|--------|-------|
| **New Modules** | 6 |
| **New Code Lines** | 6,000+ |
| **Tests** | 30+ |
| **Documentation Lines** | 2,500+ |
| **Supported Databases** | 3 (SQLite, PostgreSQL, SQL Server) |
| **Built-in Middleware** | 5 |
| **Encryption Algorithm** | AES-256-GCM |
| **Hash Algorithm** | SHA-256 |

---

## 🔗 Important Links

**Documentation:**
- [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md) — Comprehensive reference
- [RELEASE_NOTES_v1_1.md](RELEASE_NOTES_v1_1.md) — Release summary
- [V1_1_0_DELIVERY_SUMMARY.md](V1_1_0_DELIVERY_SUMMARY.md) — Delivery summary

**Code:**
- [glassbox/store/database_abstraction.py](glassbox/store/database_abstraction.py) — Database module
- [glassbox/governance/access_control.py](glassbox/governance/access_control.py) — Access control
- [glassbox/governance/encryption.py](glassbox/governance/encryption.py) — Encryption
- [glassbox/governance/advanced_audit.py](glassbox/governance/advanced_audit.py) — Audit logging
- [glassbox/governance/request_context.py](glassbox/governance/request_context.py) — Request context
- [glassbox/governance/api_gateway.py](glassbox/governance/api_gateway.py) — API gateway

**Tests:**
- [tests/test_v1_1_enterprise.py](tests/test_v1_1_enterprise.py) — Integration tests

---

## ❓ FAQ

**Q: Is v1.1.0 backward compatible with v1.0?**  
A: Yes! No breaking changes. All v1.0 code works as-is.

**Q: Can I use SQLite in development and PostgreSQL in production?**  
A: Yes! The database abstraction layer supports this seamlessly.

**Q: How do I encrypt sensitive fields?**  
A: Use `CryptoManager.encrypt_field()` for individual fields.

**Q: How do I detect tampering with audit logs?**  
A: Call `logger.verify_hash_chain()` — it detects any modifications.

**Q: How do I set up multi-tenant isolation?**  
A: Use `RequestContext` with `tenant_id` and filter queries by tenant.

**Q: Which middleware do I need?**  
A: Start with `AuthenticationMiddleware` and `RateLimitMiddleware`.

---

## 📞 Getting Help

1. **API Reference:** [docs/API.md](docs/API.md)
2. **Examples:** [tests/test_v1_1_enterprise.py](tests/test_v1_1_enterprise.py)
3. **Troubleshooting:** [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
4. **Deployment:** [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
5. **Security:** [docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md)

---

## 🎉 What's Next?

1. **Read** the [docs/ENTERPRISE_FEATURES_v1_1.md](docs/ENTERPRISE_FEATURES_v1_1.md) guide
2. **Run** the tests: `pytest tests/test_v1_1_enterprise.py -v`
3. **Try** the examples in the guide
4. **Deploy** using Docker Compose (see deployment guide)
5. **Customize** middleware for your use case

---

**Status:** Production Ready ✅
