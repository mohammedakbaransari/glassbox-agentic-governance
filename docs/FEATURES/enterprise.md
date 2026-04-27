# GlassBox Framework v1.2.0 — Enterprise Features Reference Guide

**Author:** Mohammed Akbar Ansari  
**Status:** Production Ready

---

## Table of Contents

1. [Overview](#overview)
2. [Database Abstraction Layer](#database-abstraction-layer)
3. [Advanced Access Control](#advanced-access-control)
4. [Encryption & Secrets Management](#encryption--secrets-management)
5. [Advanced Audit Logging](#advanced-audit-logging)
6. [Request Context & Configuration](#request-context--configuration)
7. [API Gateway & Middleware](#api-gateway--middleware)
8. [Integration Examples](#integration-examples)
9. [Performance & Optimization](#performance--optimization)
10. [Deployment Guide](#deployment-guide)

---

## Overview

GlassBox v1.2.0 features **enterprise-grade modules** for large-scale, regulated deployments plus **distributed governance components** for multi-replica correctness:

| Module | Purpose | Key Features |
|--------|---------|--------------|
| **Database Abstraction** | Pluggable multi-DB support | SQLite, PostgreSQL, SQL Server + connection pooling |
| **Access Control** | Enterprise RBAC/ABAC | Role hierarchy, permission scoping, impersonation audit |
| **Encryption** | Field-level cryptography | AES-256-GCM, key derivation, HMAC verification |
| **Advanced Audit** | Immutable audit trails | HMAC/SHA-256 hash chaining, tamper detection, compliance export |
| **Request Context** | Multi-tenant isolation | Thread-local context, distributed tracing, config mgmt |
| **API Gateway** | Middleware pipeline | Auth, rate-limiting, validation, CORS, logging |
| **DistributedFleetBudgetPolicy** | Multi-replica fleet budget | Redis `INCRBYFLOAT` — atomic cumulative spend across replicas |
| **DistributedAnomalyDetector** | Shared anomaly baselines | Redis Lua Welford — O(1) shared `mean`/`M2` across replicas |
| **PolicyParameterStore** | Runtime threshold updates | Update sanctions lists, amount limits — no restart required |
| **StageRegistry + P50/P99** | Pipeline observability | Per-stage latency percentiles in `/health` endpoint |
| **WriteAheadLog** | Crash-safe finalization | Two-phase side-effect tracking with startup recovery |

**Design Philosophy:**
- **Zero configuration for development** (SQLite in-memory), **production-ready for enterprise** (PostgreSQL + connection pools)
- **Security by default** (encrypted fields, audit trails, permission boundaries)
- **Compliance-focused** (HIPAA/SOX/GDPR audit trails, immutable records, non-repudiation)

---

## Database Abstraction Layer

### Module: `glassbox.store.database_abstraction`

Pluggable database backend supporting SQLite, PostgreSQL, and SQL Server with automatic connection pooling and schema migration.

### Core Classes

#### `DatabaseBackend` (Abstract)

Base interface for all database implementations.

```python
class DatabaseBackend(ABC):
    @abstractmethod
    def execute(query: str, params: Tuple = (), commit: bool = True) -> int: ...
    
    @abstractmethod
    def query_one(query: str, params: Tuple = ()) -> Optional[Dict]: ...
    
    @abstractmethod
    def query_all(query: str, params: Tuple = ()) -> List[Dict]: ...
    
    @abstractmethod
    def transaction(self): ...  # context manager
    
    @abstractmethod
    def health_check() -> bool: ...
    
    @abstractmethod
    def get_stats() -> Dict: ...
```

### SQLite Backend

**Zero-config embedded database for development and small deployments.**

```python
from glassbox.store.database_abstraction import DatabaseFactory

# In-memory database (development)
db = DatabaseFactory.create("sqlite", db_path=":memory:")

# File-based database (production-lite)
db = DatabaseFactory.create("sqlite", db_path="/data/glassbox.db")

# WAL mode enabled by default for better concurrency
db.execute("INSERT INTO records (data) VALUES (?)", ("test",))
db.close()
```

**Features:**
- Thread-local connection pooling (SQLite is single-threaded)
- WAL (Write-Ahead Logging) mode for concurrent reads
- Automatic transaction rollback on errors

### PostgreSQL Backend

**Production-grade relational database with connection pooling.**

```python
db = DatabaseFactory.create(
    "postgresql",
    host="pg.example.com",
    port=5432,
    database="glassbox",
    user="app",
    password="secret",
    pool_size=10,  # Connection pool size
    timeout=5.0,
)

# Connection pooling automatically manages connections
result = db.query_one("SELECT * FROM users WHERE id = ?", (123,))

# Get pool statistics
stats = db.get_stats()
print(stats["pool_size"], stats["available"])

db.close()
```

**Features:**
- Connection pooling (default: 10 connections)
- Configurable timeout per connection
- Pool statistics (available, exhausted, gets/puts)
- Automatic connection health checks

### SQL Server Backend

**Enterprise database with Windows native support.**

```python
db = DatabaseFactory.create(
    "sqlserver",
    server="sql.example.com",
    database="glassbox",
    user="app",
    password="secret",
    port=1433,
    pool_size=10,
)

# Same interface as PostgreSQL
stats = db.get_stats()
db.close()
```

### Transaction Support

**Context manager for atomic operations:**

```python
db = DatabaseFactory.create("sqlite", db_path=":memory:")

try:
    with db.transaction():
        db.execute("INSERT INTO audit (action) VALUES (?)", ("action1",))
        db.execute("INSERT INTO audit (action) VALUES (?)", ("action2",))
    # Both inserts committed together
except Exception as e:
    # Automatic rollback on exception
    print(f"Transaction failed: {e}")
```

### Health Checks

**Monitor database connectivity:**

```python
if not db.health_check():
    raise RuntimeError("Database unreachable!")
```

### Migration Path

**SQLite → PostgreSQL:**

```python
# Existing code works without changes!
# Only change the factory call

# Development: SQLite
db_dev = DatabaseFactory.create("sqlite", db_path=":memory:")

# Production: PostgreSQL
db_prod = DatabaseFactory.create(
    "postgresql",
    host=os.getenv("DB_HOST", "localhost"),
    database=os.getenv("DB_NAME", "glassbox"),
)
```

---

## Advanced Access Control

### Module: `glassbox.governance.access_control`

Enterprise RBAC (Role-Based Access Control) with hierarchical roles, attribute-based conditions, and audit trail for permission decisions.

### Core Concepts

**Permission Model:** `<resource>:<action>:<scope>`

```
Examples:
  - "audit_log:read:own_tenant"    (read audit logs in own tenant)
  - "policy:write:any_tenant"       (write policies (admin))
  - "data:export:own_record"        (export own records)
```

**Scope Hierarchy:**
```
OWN_RECORD < OWN_TENANT < ANY_TENANT < CUSTOM < ANY
         (higher scope includes lower ones)
```

### Role Definition

```python
from glassbox.governance.access_control import AccessControl, Role, User, PermissionScope

# Define admin role
admin_role = Role("admin", description="System Administrator")
admin_role.grant_permission(
    resource="policy",
    action="read",
    scope=PermissionScope.ANY,
)
admin_role.grant_permission("policy", "write", PermissionScope.ANY)
admin_role.grant_permission("audit_log", "read", PermissionScope.ANY)

# Define analyst role (inherits from admin)
analyst_role = Role("analyst", description="Data Analyst")
analyst_role.grant_permission("policy", "read", PermissionScope.OWN_TENANT)
analyst_role.set_parent(admin_role)  # Inherits admin permissions

# Analyst now has: all admin permissions + own read permission
assert analyst_role.has_permission("policy", "write", PermissionScope.ANY)  # From admin
```

### Access Control Engine

```python
# Initialize
ac = AccessControl(enable_caching=True, cache_ttl_sec=300)

# Register roles
ac.register_role(admin_role)
ac.register_role(analyst_role)

# Create users
admin_user = User("admin1", roles={"admin"})
analyst_user = User("analyst1", roles={"analyst"})

ac.register_user(admin_user)
ac.register_user(analyst_user)

# Check permissions
can_admin_read = ac.has_permission(
    user_id="admin1",
    resource="policy",
    action="read",
    context={"scope": PermissionScope.ANY}
)  # True

can_analyst_write = ac.has_permission(
    user_id="analyst1",
    resource="policy",
    action="write",
    context={"scope": PermissionScope.ANY}
)  # True (inherited from admin)
```

### Permission Caching

**Speeds up repeated permission checks:**

```python
ac = AccessControl(enable_caching=True, cache_ttl_sec=300)

# First call: computes decision
ac.has_permission("user1", "policy", "read")

# Second call: returns cached result (< 1ms)
ac.has_permission("user1", "policy", "read")

# Clear cache if roles change
ac.clear_cache()
```

### Impersonation & Delegation

**Admin/support staff can impersonate users (all actions logged):**

```python
# Admin temporarily acts as analyst
with ac.impersonate("analyst", "admin1"):
    # All operations logged as "admin1 impersonating analyst"
    ac.has_permission("admin1", "policy", "read")
    # Permission check uses "analyst" role, but audit shows "admin1"

# Impersonation automatically cleaned up
```

### Custom Validators

**Add business logic to permission checks:**

```python
def business_hours_validator(context: Dict[str, Any]) -> bool:
    """Only allow access during business hours."""
    from datetime import datetime
    hour = datetime.now().hour
    return 9 <= hour <= 17  # 9 AM - 5 PM

ac.add_validator(business_hours_validator)

# Now all permission checks include business hours validation
ac.has_permission("user1", "data", "read")  # True only during 9-5
```

### Decision Audit Trail

**View permission decisions for debugging:**

```python
# Get recent permission checks
decisions = ac.get_decision_history(limit=10)

for decision in decisions:
    print(f"{decision.principal} -> {decision.resource}:{decision.action} = {decision.allowed}")

# Get statistics
stats = ac.get_stats()
print(f"Users: {stats['users_count']}, Roles: {stats['roles_count']}")
```

---

## Encryption & Secrets Management

### Module: `glassbox.governance.encryption`

AES-256-GCM authenticated encryption for field-level data protection, password hashing, and secure secrets handling.

### Encryption Basics

```python
from glassbox.governance.encryption import CryptoManager, EncryptedField, SecretManager

# Create crypto manager with auto-generated 256-bit key
crypto = CryptoManager()

# Encrypt data
plaintext = b"sensitive PII"
encrypted = crypto.encrypt(plaintext)

# Decrypt
decrypted = crypto.decrypt(encrypted)
assert decrypted == plaintext
```

### Authenticated Encryption (AES-256-GCM)

**Prevents tampering and verifies data integrity:**

```python
# Encrypt with additional authenticated data (not encrypted but authenticated)
aad = b"user_id:12345"  # Context that must be verified
encrypted = crypto.encrypt(b"payment_amount:100", aad=aad)

# Decryption will fail if AAD doesn't match
try:
    crypto.decrypt(encrypted, aad=b"user_id:54321")  # Wrong AAD
except:
    print("Tampering detected!")

# Correct AAD succeeds
decrypted = crypto.decrypt(encrypted, aad=b"user_id:12345")
```

### Field-Level Encryption

**Encrypt individual record fields:**

```python
# Create field
password_field = EncryptedField(
    name="password",
    plaintext="SecurePass123!"
)

# Encrypt
encrypted_field = crypto.encrypt_field(password_field)
assert encrypted_field.plaintext is None
assert encrypted_field.ciphertext is not None
assert encrypted_field.encrypted_at is not None

# Store encrypted_field.ciphertext in database
# Later: retrieve and decrypt
decrypted_field = crypto.decrypt_field(encrypted_field)
assert decrypted_field.plaintext == "SecurePass123!"
```

### Password Hashing (PBKDF2)

**Secure password storage (never store plaintext!):**

```python
# Hash password
password = "UserPassword123!"
hashed_password, salt = CryptoManager.hash_password(password)

# Store both hashed_password and salt in database

# Later: verify login
if CryptoManager.verify_password(password, hashed_password, salt):
    print("Login successful!")
else:
    print("Wrong password!")
```

### Key Derivation from Passphrase

**Deterministic key generation for password-based encryption:**

```python
# Derive same key from passphrase every time
passphrase = "master_passphrase_123"
crypto1 = CryptoManager.from_passphrase(passphrase, salt=b"fixed_salt_16b_")

# Later, re-derive same key
crypto2 = CryptoManager.from_passphrase(passphrase, salt=b"fixed_salt_16b_")

assert crypto1.key == crypto2.key

# Encrypt with crypto1, decrypt with crypto2
encrypted = crypto1.encrypt(b"secret_data")
decrypted = crypto2.decrypt(encrypted)
```

### HMAC for Integrity

**Verify data hasn't been modified:**

```python
data = b"important record"

# Compute HMAC
hmac_digest = CryptoManager.compute_hmac(data)

# Later: verify data hasn't changed
if CryptoManager.verify_hmac(data, hmac_digest):
    print("Data integrity verified!")
else:
    print("Data has been tampered with!")
```

### Secret Manager

**Secure in-memory secret storage (overwrites before deletion):**

```python
secrets = SecretManager()

# Store secret
secrets.store_secret("database_password", "my_secret_password")

# Retrieve
pwd = secrets.get_secret("database_password")

# Secure deletion (overwrites memory before deleting)
secrets.delete_secret("database_password")

# Clear all secrets
secrets.clear_all_secrets()
```

### Encryption Statistics

```python
stats = crypto.get_stats()
print(stats)
# {
#     'encryptions': 1523,
#     'decryptions': 1520,
#     'errors': 0,
#     'key_size_bits': 256
# }
```

---

## Advanced Audit Logging

### Module: `glassbox.governance.advanced_audit`

Append-only audit trail with cryptographic hash chaining for detecting tampering, compliance-ready record export, and configurable retention policies.

### Audit Record Structure

```python
from glassbox.governance.advanced_audit import AuditLogger, AuditRecord

logger = AuditLogger(db_path=":memory:")

# Log action
record = logger.log_action(
    user_id="analyst1",
    action="policy_update",
    resource_type="policy",
    resource_id="policy_456",
    result="success",  # or "failure", "partial"
    context={
        "old_value": "threshold=0.5",
        "new_value": "threshold=0.8",
        "reason": "Q4 Risk Assessment"
    },
    error_message=None,  # Only if result="failure"
)

# Record includes:
#   - timestamp, user_id, action, resource_type/id
#   - result, context, error_message
#   - record_hash (SHA-256), previous_hash (hash chain)
```

### Hash Chain for Tamper Detection

**Each record links to previous via SHA-256 hash (immutable audit trail):**

```python
logger = AuditLogger(db_path=":memory:", enable_hash_chain=True)

# Log sequence of actions
logger.log_action("user1", "file_accessed", "document", "doc_1", "success")
logger.log_action("user2", "file_modified", "document", "doc_1", "success")
logger.log_action("user1", "file_deleted", "document", "doc_1", "success")

# Verify integrity of entire chain
if logger.verify_hash_chain():
    print("Audit trail has not been tampered with!")
else:
    print("TAMPERING DETECTED! Record has been modified!")
    # Alert security team
```

**How it works:**
```
Record 1: hash = SHA256(record_1_data + previous_hash=None)
Record 2: hash = SHA256(record_2_data + previous_hash=hash_1)
Record 3: hash = SHA256(record_3_data + previous_hash=hash_2)

If anyone modifies Record 2:
  - Its hash will no longer match Record 3's "previous_hash"
  - Verification will fail
```

### Searching Audit Trail

```python
# Search by user
records = logger.search(user_id="analyst1", limit=100)

# Search by action (supports wildcards)
policy_records = logger.search(action="policy_*")

# Search by date range
from datetime import datetime, timedelta
week_ago = datetime.now() - timedelta(days=7)
recent = logger.search(start_time=week_ago)

# Search by resource
doc_records = logger.search(resource_type="document", resource_id="doc_1")

# Combine filters
records = logger.search(
    user_id="analyst1",
    action="export",
    result="success",
    start_time=week_ago,
    limit=50
)
```

### Compliance Export

**Generate audit reports for regulators (SOX, HIPAA, GDPR):**

```python
# Export as JSON
json_export = logger.export_records(
    format="json",
    filters={"user_id": "analyst1", "action": "report_*"}
)
# Write to file for auditors
with open("audit_report_q4.json", "w") as f:
    f.write(json_export)

# Export as CSV
csv_export = logger.export_records(
    format="csv",
    filters={"start_time": week_ago}
)
```

### Retention Policies

```python
# Auto-delete records older than 7 years (default)
logger = AuditLogger(retention_days=2555)

# Manually purge old records
deleted_count = logger.purge_old_records(days=730)  # Purge > 2 years
print(f"Deleted {deleted_count} old records")
```

### Audit Statistics

```python
stats = logger.get_stats()
print(stats)
# {
#     'total_records': 15234,
#     'oldest_record': '2024-01-01T00:00:00',
#     'newest_record': '2025-01-15T14:30:00',
#     'hash_chain_enabled': True,
#     'retention_days': 2555
# }
```

---

## Request Context & Configuration

### Module: `glassbox.governance.request_context`

Thread-local request context for multi-tenant isolation, distributed tracing, and centralized configuration management.

### Request Context

**Capture request metadata in thread-local storage:**

```python
from glassbox.governance.request_context import RequestContext, ContextManager

# Create request context
ctx = RequestContext(
    request_id="req-12345",
    user_id="analyst1",
    tenant_id="acme-corp",
    correlation_id="corr-7890",  # For distributed tracing
    session_id="sess-abc123"
)

RequestContext.set_current(ctx)

# Later in same thread/request
current_ctx = RequestContext.get_current()
print(current_ctx.user_id)  # "analyst1"
print(current_ctx.tenant_id)  # "acme-corp"

# Use context manager for automatic cleanup
with ContextManager(user_id="analyst2", tenant_id="globex"):
    ctx = RequestContext.get_current()
    # ctx.user_id = "analyst2", ctx.tenant_id = "globex"
# Automatically reverts after context exit
```

### Distributed Tracing

**Track requests across microservices:**

```python
# Client sets X-Correlation-ID header
ctx = RequestContext(
    correlation_id=request.headers.get("X-Correlation-ID")
)

# Pass correlation_id in downstream calls
downstream_response = requests.get(
    "http://other-service/api/data",
    headers={"X-Correlation-ID": ctx.correlation_id}
)

# Logs from both services have same correlation_id for tracing
```

### Configuration Management

```python
from glassbox.governance.request_context import Config

# Load from YAML (production)
config = Config.load("/etc/glassbox/config.yaml")

# Load from JSON
config = Config.load("/etc/glassbox/config.json")

# Get values with dot notation
db_host = config.get("database.host", default="localhost")
db_port = config.get("database.port", default=5432)

# Environment variable override (recommended for secrets!)
api_key = config.get_secret(
    "api.key",
    env_var="GLASSBOX_API_KEY",  # Check env first
    default="fallback_key"
)

# Runtime overrides
config.set("feature_flags.new_ui", True)
```

### Configuration File Example

**`/etc/glassbox/config.yaml`:**
```yaml
database:
  backend: postgresql
  host: pg.example.com
  port: 5432
  database: glassbox
  pool_size: 10

encryption:
  algorithm: aes256gcm
  key_rotation_days: 90

audit:
  enabled: true
  retention_days: 2555
  hash_chain: true

api:
  rate_limit_per_minute: 100
  cors_origins:
    - http://frontend.example.com
    - https://frontend.example.com
```

### Multi-Tenant Isolation

```python
# Each request/user gets isolated context
with ContextManager(user_id="user1", tenant_id="tenant_acme"):
    # Query only tenant_acme data
    query = "SELECT * FROM documents WHERE tenant_id = ?"
    db.query_all(query, (RequestContext.get_current().tenant_id,))

with ContextManager(user_id="user2", tenant_id="tenant_globex"):
    # Query only tenant_globex data
    # Different tenant, different isolation
```

---

## API Gateway & Middleware

### Module: `glassbox.governance.api_gateway`

Extensible middleware pipeline for authentication, rate limiting, request validation, CORS, and comprehensive logging.

### Core Architecture

```
Incoming Request
    ↓
[Middleware 1 - process_request()]
    ↓
[Middleware 2 - process_request()]
    ↓
[Middleware N - process_request()]
    ↓
[Route Handler]
    ↓
[Middleware N - process_response()]
    ↓
[Middleware 2 - process_response()]
    ↓
[Middleware 1 - process_response()]
    ↓
Response Sent to Client
```

### Basic Usage

```python
from glassbox.governance.api_gateway import (
    APIGateway, Response,
    AuthenticationMiddleware, RateLimitMiddleware,
    RequestLoggingMiddleware, CORSMiddleware
)

# Create gateway
gateway = APIGateway()

# Add middleware (executes in order)
gateway.add_middleware(AuthenticationMiddleware(secret_key="secret123"))
gateway.add_middleware(RateLimitMiddleware(requests_per_minute=100))
gateway.add_middleware(RequestLoggingMiddleware())
gateway.add_middleware(CORSMiddleware(allowed_origins=["http://localhost:3000"]))

# Register route handler
def get_policies_handler(request):
    ctx = RequestContext.get_current()
    policies = db.query_all(
        "SELECT * FROM policies WHERE tenant_id = ?",
        (ctx.tenant_id,)
    )
    return Response(
        status_code=200,
        body={"policies": policies}
    )

gateway.register_route("GET", "/api/policies", get_policies_handler)

# Handle request
response = gateway.handle_request(
    method="GET",
    path="/api/policies",
    headers={
        "Authorization": "Bearer secret123",
        "X-Request-ID": "req-12345"
    }
)

print(response.status_code)  # 200
print(response.body)  # {"policies": [...]}
```

### Authentication Middleware

**Validate Authorization header:**

```python
auth_middleware = AuthenticationMiddleware(secret_key="my_token")
gateway.add_middleware(auth_middleware)

# Request without Authorization header -> 401
# Request with valid token -> passes to next middleware
# Request with invalid token -> 401
```

### Rate Limiting Middleware

**Throttle requests per user/endpoint:**

```python
rate_limit_middleware = RateLimitMiddleware(requests_per_minute=100)
gateway.add_middleware(rate_limit_middleware)

# First 100 requests per minute succeed
# Request 101+ returns 429 (Too Many Requests)
```

### Request Validation Middleware

**Validate request structure:**

```python
from glassbox.governance.api_gateway import RequestValidationMiddleware

validation = RequestValidationMiddleware()

def validate_policy(request):
    """Custom validator for policy creation."""
    if request.method != "POST":
        return True
    try:
        body = json.loads(request.body)
        return "name" in body and "rules" in body
    except:
        return False

validation.register_validator("/api/policies", validate_policy)
gateway.add_middleware(validation)

# POST to /api/policies
# - Valid body with "name" and "rules" -> passes
# - Missing fields -> 400 Bad Request
```

### CORS Middleware

**Handle Cross-Origin requests:**

```python
cors = CORSMiddleware(
    allowed_origins=["http://localhost:3000", "https://app.example.com"],
    allowed_methods=["GET", "POST", "PUT", "DELETE"],
    allowed_headers=["Content-Type", "Authorization", "X-Request-ID"]
)
gateway.add_middleware(cors)

# Preflight (OPTIONS) requests auto-handled
# Response includes Access-Control-Allow-* headers
```

### Request Logging Middleware

**Log all requests and responses:**

```python
logging_middleware = RequestLoggingMiddleware()
gateway.add_middleware(logging_middleware)

# Log format:
# INFO: Incoming request: POST /api/policies [user=analyst1, tenant=acme, req_id=req-123]
# INFO: Outgoing response: POST /api/policies -> 201 [req_id=req-123]
```

### Custom Middleware

**Implement custom middleware:**

```python
from glassbox.governance.api_gateway import Middleware, Request, Response

class CustomMiddleware(Middleware):
    def process_request(self, request: Request) -> Optional[Response]:
        """
        Process request before handler.
        Return Response to short-circuit, None to continue.
        """
        print(f"Processing {request.method} {request.path}")
        return None  # Continue to next middleware

    def process_response(self, request: Request, response: Response) -> Response:
        """Process response after handler."""
        response.headers["X-Custom-Header"] = "value"
        return response

gateway.add_middleware(CustomMiddleware())
```

### Gateway Statistics

```python
stats = gateway.get_stats()
print(stats)
# {
#     'middleware_count': 4,
#     'routes_count': 8,
#     'routes': ['GET /api/policies', 'POST /api/policies', ...]
# }
```

---

## Integration Examples

### Example 1: Complete Governance Flow

```python
from glassbox.store.database_abstraction import DatabaseFactory
from glassbox.governance.access_control import AccessControl, Role, User, PermissionScope
from glassbox.governance.advanced_audit import AuditLogger
from glassbox.governance.encryption import CryptoManager
from glassbox.governance.request_context import RequestContext, ContextManager
from glassbox.governance.api_gateway import APIGateway, Response, AuthenticationMiddleware

# 1. Setup database
db = DatabaseFactory.create("postgresql",
    host="pg.example.com",
    database="glassbox",
    user="app"
)

# 2. Setup access control
ac = AccessControl()

analyst_role = Role("analyst")
analyst_role.grant_permission("report", "read", PermissionScope.OWN_TENANT)
analyst_role.grant_permission("report", "export", PermissionScope.OWN_TENANT)

ac.register_role(analyst_role)

analyst = User("analyst1", roles={"analyst"})
ac.register_user(analyst)

# 3. Setup audit logging
logger = AuditLogger(enable_hash_chain=True)

# 4. Setup encryption
crypto = CryptoManager()

# 5. Setup API gateway
gateway = APIGateway()
gateway.add_middleware(AuthenticationMiddleware(secret_key="token123"))

def export_report_handler(request):
    ctx = RequestContext.get_current()
    
    # Check permission
    if not ac.has_permission(
        ctx.user_id,
        "report",
        "export",
        context={"scope": PermissionScope.OWN_TENANT, "tenant_id": ctx.tenant_id}
    ):
        logger.log_action(ctx.user_id, "report_export", "report", "report_1", "failure",
            error_message="Permission denied")
        return Response(status_code=403, error="Permission denied")
    
    # Query database
    reports = db.query_all(
        "SELECT * FROM reports WHERE tenant_id = ?",
        (ctx.tenant_id,)
    )
    
    # Export and encrypt sensitive data
    export_data = json.dumps(reports).encode()
    encrypted_export = crypto.encrypt(export_data)
    
    # Log action
    logger.log_action(
        user_id=ctx.user_id,
        action="report_export",
        resource_type="report",
        resource_id="report_1",
        result="success",
        context={"export_size": len(export_data)}
    )
    
    return Response(
        status_code=200,
        body={"encrypted_data": encrypted_export.hex()}
    )

gateway.register_route("POST", "/api/reports/export", export_report_handler)

# 6. Process request
with ContextManager(user_id="analyst1", tenant_id="acme"):
    response = gateway.handle_request(
        method="POST",
        path="/api/reports/export",
        headers={"Authorization": "Bearer token123"}
    )

# 7. Verify audit trail
assert logger.verify_hash_chain()
print("Governance flow completed successfully!")
```

### Example 2: Multi-Tenant SaaS

```python
# Configuration
config = Config.load("/etc/glassbox/config.yaml")

# Database per tenant (or single DB with tenant_id filtering)
db_config = config.get_section("database")
db = DatabaseFactory.create(
    db_config["backend"],
    **{k: v for k, v in db_config.items() if k != "backend"}
)

# Shared access control across tenants
ac = AccessControl()

# Register roles for each tenant
for role_name in ["admin", "analyst", "viewer"]:
    role = Role(role_name)
    # ... grant permissions
    ac.register_role(role)

# Register users
for user_data in db.query_all("SELECT * FROM users"):
    user = User(user_data["id"], roles=user_data["roles"].split(","))
    ac.register_user(user)

# Request handler with tenant isolation
def get_user_data_handler(request):
    ctx = RequestContext.get_current()
    
    # Verify user belongs to tenant
    user_tenant = db.query_one(
        "SELECT tenant_id FROM users WHERE id = ?",
        (ctx.user_id,)
    )
    
    if user_tenant["tenant_id"] != ctx.tenant_id:
        return Response(status_code=403, error="Unauthorized")
    
    # Query only this tenant's data
    data = db.query_all(
        "SELECT * FROM user_data WHERE tenant_id = ? AND user_id = ?",
        (ctx.tenant_id, ctx.user_id)
    )
    
    return Response(status_code=200, body={"data": data})
```

### Example 3: Compliance Reporting

```python
# Generate quarterly compliance report
from datetime import datetime, timedelta

logger = AuditLogger()

end_date = datetime.now()
start_date = end_date - timedelta(days=90)

# Search audit logs
records = logger.search(
    start_time=start_date,
    end_time=end_date
)

# Export for auditors
export = logger.export_records(
    format="json",
    filters={
        "action": "*.write",  # All write operations
        "start_time": start_date,
        "end_time": end_date
    }
)

# Verify integrity
if logger.verify_hash_chain():
    with open(f"audit_report_q4_{datetime.now().year}.json", "w") as f:
        f.write(export)
    print("Compliance report ready for SOX auditors!")
else:
    print("ERROR: Audit trail integrity check failed!")
    # Alert security team
```

---

## Performance & Optimization

### Database Performance

**Connection Pooling:**
```python
# PostgreSQL with optimized pool
db = DatabaseFactory.create(
    "postgresql",
    host="pg.example.com",
    pool_size=20,  # Increase for high concurrency
    timeout=10.0,
)

stats = db.get_stats()
print(f"Pool exhausted {stats['exhausted']} times")  # Monitor for adjustment
```

**Indexing:**
```python
# Create indexes on frequently searched columns
db.execute("CREATE INDEX idx_tenant_user ON documents(tenant_id, user_id)")
db.execute("CREATE INDEX idx_audit_timestamp ON audit_records(timestamp DESC)")
```

### Permission Caching

**Cache permission decisions:**
```python
ac = AccessControl(
    enable_caching=True,
    cache_ttl_sec=300  # 5-minute cache
)

# First check: ~50ms
result = ac.has_permission("user1", "policy", "read")

# Subsequent checks: ~1ms (cached)
result = ac.has_permission("user1", "policy", "read")

# Clear cache on role changes
ac.clear_cache()
```

### Audit Log Optimization

**Purge old records:**
```python
logger = AuditLogger(retention_days=2555)

# Manually purge older records
deleted = logger.purge_old_records(days=730)
print(f"Purged {deleted} records")
```

**Batch logging:**
```python
# Instead of logging each action individually:
for action in large_batch:
    logger.log_action(...)  # Slow

# Better: wrap in transaction
with db.transaction():
    for action in large_batch:
        logger.log_action(...)  # Faster
```

### Request Context Overhead

**Minimal thread-local storage:**
```python
import sys

# Check size of request context
ctx = RequestContext()
print(sys.getsizeof(ctx))  # ~200 bytes

# Safe for millions of concurrent requests
```

---

## Deployment Guide

### Docker Compose Stack

**`docker-compose.yml`:**
```yaml
version: '3.8'

services:
  glassbox-api:
    image: glassbox:v1.2.0
    environment:
      GLASSBOX_DB_BACKEND: postgresql
      GLASSBOX_DB_HOST: postgres
      GLASSBOX_DB_DATABASE: glassbox
      GLASSBOX_DB_USER: glassbox
      GLASSBOX_DB_PASSWORD: ${DB_PASSWORD}
      GLASSBOX_CONFIG_PATH: /etc/glassbox/config.yaml
    volumes:
      - ./config.yaml:/etc/glassbox/config.yaml
    depends_on:
      - postgres
    ports:
      - "8000:8000"

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: glassbox
      POSTGRES_USER: glassbox
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

**Deploy:**
```bash
docker-compose up -d
```

### Environment Variables

```bash
# Database
export GLASSBOX_DB_HOST=pg.example.com
export GLASSBOX_DB_PORT=5432
export GLASSBOX_DB_DATABASE=glassbox
export GLASSBOX_DB_USER=app
export GLASSBOX_DB_PASSWORD=secure_password

# Secrets (never hardcode!)
export GLASSBOX_API_KEY=secret_key_123
export GLASSBOX_ENCRYPTION_KEY=$(python3 -c "import os; print(os.urandom(32).hex())")

# Configuration
export GLASSBOX_CONFIG_PATH=/etc/glassbox/config.yaml
```

### Production Checklist

- [ ] Database: PostgreSQL with dedicated connection pool
- [ ] Encryption: Strong random keys (256-bit)
- [ ] Audit: Hash chain enabled, retention policy set
- [ ] Access Control: Roles defined, permissions audited
- [ ] API Gateway: All middleware enabled (auth, logging, CORS)
- [ ] Secrets: All in environment variables, never hardcoded
- [ ] Monitoring: Health checks, performance metrics
- [ ] Backup: Daily database backups, audit log exports
- [ ] Testing: Full test suite passing, integration tests
- [ ] Documentation: Runbooks, troubleshooting guides

---

## Distributed Governance Components (v1.2.0)

### DistributedFleetBudgetPolicy

Replaces the in-process `FleetBudgetPolicy` with a Redis-backed version for multi-replica correctness.

```python
import redis
from glassbox.governance.velocity_breaker import DistributedFleetBudgetPolicy

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)

fleet = DistributedFleetBudgetPolicy(
    budget=1_000_000.0,    # daily fleet-wide spend cap
    period_hours=24,
    redis_client=redis_client,
    policy_id="FLEET-001",
    warn_threshold=0.8,    # warn at 80% utilisation
)

# Register as a policy
pipeline.policy_engine.register(fleet.as_policy())

# After decision executes, record spend (call from EventBus handler)
fleet.record_execution(amount=50_000.0)
```

Key properties:
- `_rule()` **previews** projected spend atomically before committing
- `record_execution()` commits spend via Redis `INCRBYFLOAT` after execution
- Falls back to in-memory counter if Redis is unavailable
- Key pattern: `{namespace}:fleet_budget:{YYYY-MM-DD}`

### DistributedAnomalyDetector

Extends `AnomalyDetectorOptimized` to share Welford running statistics across replicas via Redis.

```python
from glassbox.governance.anomaly_detector import DistributedAnomalyDetector, RedisAnomalyStore

store = RedisAnomalyStore(
    redis_client=redis_client,
    namespace="prod",       # Key prefix for isolation
    window_size=100,        # Welford window
)
detector = DistributedAnomalyDetector(store=store, z_threshold=3.0)
pipeline  = GovernancePipeline(anomaly_detector=detector)
```

Key properties:
- Atomic Lua script maintains `{count, mean, M2}` per `{namespace}:{agent_id}:{type}:{field}` Redis Hash
- Exponential forgetting when `count >= window_size` keeps memory O(1)
- Categorical tracking remains in-process (per replica)
- Falls back to parent in-process stats on Redis failure (`_store_ok = False`)

### PolicyParameterStore — Runtime Updates

```python
from glassbox.governance.policy_parameters import _param_store

# Update PROC-006 sanctions list at runtime — takes effect immediately
_param_store.set(
    "PROC-006",
    "sanctioned_countries",
    ["IR", "KP", "SY", "CU", "RU", "BY", "XX"],   # new list
    updated_by="compliance_officer",
)

# Update debarred suppliers
_param_store.set(
    "PROC-006",
    "debarred_suppliers",
    ["DEBARRED-001", "BLACKLISTED-CORP", "NEW-BAD-VENDOR"],
    updated_by="procurement_admin",
)
```

Changes take effect on the **next policy evaluation** — no restart, no redeployment.

### Write-Ahead Log — Crash Recovery

```python
# Enable WAL recovery on startup
pipeline = GovernancePipeline(recover_wal_on_startup=True)

# WAL replays any PENDING or IN_PROGRESS entries from the last run
# WorkflowEngine.create_from_decision() is idempotent — replay-safe
```

WAL state transitions:
```
begin_transaction() → PENDING
mark_side_effect()  → IN_PROGRESS
commit()            → COMMITTED
rollback()          → ROLLED_BACK
```

---

## Summary

GlassBox v1.2.0 provides **production-ready enterprise features**:

| Feature | Module | Benefit |
|---------|--------|---------|
| Multi-DB support | `database_abstraction` | Scale from SQLite to PostgreSQL |
| RBAC/ABAC | `access_control` | Fine-grained permission control |
| AES-256-GCM | `encryption` | Industry-standard data protection |
| Immutable audit trail | `advanced_audit` | HMAC/SHA-256 tamper-proof records |
| Request isolation | `request_context` | Secure multi-tenant operation |
| Middleware pipeline | `api_gateway` | Composable security layers |
| Redis fleet budget | `velocity_breaker` | Multi-replica spend correctness |
| Redis anomaly baselines | `anomaly_detector` | Shared Welford stats across replicas |
| Runtime policy params | `policy_parameters` | Live threshold updates, no restart |
| Stage latency tracking | `stage_registry` | P50/P99 per stage in `/health` |
| Write-ahead log | `write_ahead_log` | Crash-safe finalization + recovery |

**See also:**
- [API Reference](../API/endpoint_reference.md)
- [Deployment Guide](../DEPLOYMENT.md)
- [Troubleshooting Guide](../USER/troubleshooting.md)
- [Integration Tests](../tests/test_v1_1_enterprise.py)

---

## Known Limitations (v1.2.0)

| Module | Issue | Workaround |
|---|---|---|
| `access_control.py` | Role cycle detection: `set_parent()` allows circular role inheritance; `get_all_permissions()` returns an incomplete set if A→B→A | Validate role hierarchy at configuration time; cycle resolution is non-destructive |
| `multitenancy.py` | `tenant_id` path validation does not assert that the resolved path stays inside `GLASSBOX_LOG_DIR` | Restrict tenant IDs to alphanumeric via `[a-z0-9_-]+` regex before calling API |
| `advanced_audit.py` | `GENESIS_SENTINEL = "0"*64` is hardcoded; a partial genesis record in WAL on crash could break chain verification | Enable `recover_wal_on_startup=True` to replay before reading chain |
| `api_gateway.py` | Rate limiter evicts oldest shard key without timestamp awareness under sustained attack | Tune `_max_keys_per_shard` upward; back with Redis for distributed deployments |

---

**Author:** Mohammed Akbar Ansari  
**License:** Apache 2.0
