# Security & Hardening

This directory contains security documentation, hardening guides, and best practices.

## 📖 Contents

### Security Hardening Guide
- **[hardening.md](hardening.md)**
  - Authentication & authorization
  - Encryption (at rest & in transit)
  - Input validation & sanitization
  - API security
  - Network security
  - Vulnerability management

## Security-First Design

GlassBox follows "Security by Default":
- All inputs validated (PayloadSanitizer, 25+ injection patterns)
- All state protected by locks (RLock, per-agent lock pool)
- All decisions audited (immutable HMAC/SHA-256 hash chain)
- All API requests authenticatable (Bearer token middleware)
- All traffic encryptable (AES-256-GCM field-level)
- No hardcoded credentials or secrets
- Principle of least privilege (RBAC with role hierarchy)
- **Policy exception messages sanitized** — internal stack traces never reach callers (v1.2.0)
- **Sanctions lists runtime-configurable** — no code deployment needed (v1.2.0)
- **Tamper-evident audit** — HMAC/SHA-256 hash chaining detects any record modification

## 🛡️ Security Layers

### Layer 1: Input Security
- **Payload Sanitization** - Validate & clean inputs
- **Schema Validation** - Per-decision-type schemas
- **Type Checking** - Prevent injection attacks
- **Size Limits** - Prevent buffer overflows
- **SQL Injection** - Parameterized queries

### Layer 2: Access Control
- **API Authentication** - Bearer token validation
- **Role-Based Access Control** - RBAC policies
- **Multi-Tenancy** - Complete data isolation
- **Audit Logging** - Track all access
- **Rate Limiting** - Prevent abuse

### Layer 3: Data Protection
- **Encryption at Rest** - AES-256 (optional)
- **Encryption in Transit** - TLS 1.2+ required
- **Key Management** - Secure key storage
- **Data Retention** - Configurable purging
- **Backup Encryption** - All backups encrypted

### Layer 4: Application Security
- **Thread Safety** - All shared state locked
- **Memory Safety** - Python GC protects
- **Error Handling** - No sensitive data leaked
- **Dependency Security** - Minimal dependencies
- **Code Review** - Peer review required

### Layer 5: Infrastructure Security
- **Network Segmentation** - Isolated VPCs
- **Firewall Rules** - Whitelist access
- **Intrusion Detection** - Monitor anomalies
- **DDoS Protection** - Rate limiting
- **Backup Integrity** - Verify backups

## 🔐 Security Controls

### Authentication

API endpoints are unauthenticated in the default distribution. Add a Bearer-token middleware for production:

```python
# Bearer token middleware (add to glassbox/api/app.py or a reverse proxy)
from functools import wraps
from flask import request, jsonify
import os

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "")
        expected = f"Bearer {os.environ.get('GLASSBOX_API_KEY', '')}"
        if token != expected:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated
```

### Encryption

Field-level AES-256-GCM encryption is provided by `glassbox.governance.encryption`:

```python
from glassbox.governance.encryption import FieldEncryptor
import os

encryptor = FieldEncryptor(password=os.environ["GLASSBOX_ENCRYPT_PASSWORD"])

# Encrypt a sensitive field before storing
ciphertext = encryptor.encrypt("patient-id-12345")

# Decrypt on retrieval
plaintext = encryptor.decrypt(ciphertext)
```

### Access Control

RBAC is in `glassbox.governance.access_control`:

```python
from glassbox.governance.access_control import AccessController, Role, Permission

controller = AccessController()

# Create roles
admin_role = Role("admin")
viewer_role = Role("viewer")

# Assign permissions
admin_role.add_permission(Permission("policy:write"))
viewer_role.add_permission(Permission("decision:read"))

# Grant roles to users
controller.assign_role("user-123", viewer_role)
controller.assign_role("admin-456", admin_role)

# Enforce at runtime
if not controller.has_permission("user-123", "policy:write"):
    raise PermissionError("Insufficient privileges")
```

## ✅ Security Checklist

### Pre-Deployment
- [ ] Change all default credentials
- [ ] Generate strong API keys
- [ ] Configure encryption keys (not in code)
- [ ] Review access control policies
- [ ] Enable audit logging
- [ ] Configure backups
- [ ] Test disaster recovery
- [ ] Run security scan
- [ ] Penetration test (recommended)
- [ ] Security team review

### Ongoing
- [ ] Monitor audit logs daily
- [ ] Review access logs weekly
- [ ] Update dependencies monthly
- [ ] Rotate encryption keys quarterly
- [ ] Security training annually
- [ ] Vulnerability scans monthly
- [ ] Penetration tests annually
- [ ] Compliance audits (per framework)

## 🚨 Common Vulnerabilities & Prevention

| Vulnerability | Risk | Prevention |
|---------------|------|-----------|
| SQL Injection | HIGH | Use parameterized queries |
| XSS Attacks | MEDIUM | Input validation, output encoding |
| CSRF | MEDIUM | CSRF tokens, SameSite cookies |
| DoS | MEDIUM | Rate limiting, timeout configs |
| Weak Auth | HIGH | Strong keys, MFA, TLS |
| Data Breach | CRITICAL | Encryption, access controls |
| Privilege Escalation | HIGH | RBAC, audit logging |
| Insecure Dependencies | MEDIUM | Regular updates, scanning |

## 🔍 Security Monitoring

### Key Metrics
- **Failed auth attempts** - Track unauthorized access
- **Rate limit triggers** - Identify abuse patterns
- **Audit log volume** - Verify all decisions logged
- **Encryption coverage** - Verify protected data
- **Patch currency** - Ensure updates applied
- **Vulnerability scan results** - Track open issues

### Alerting
Configure alerts for:
- Failed authentication (3+ failures)
- Rate limit exceeded
- Encryption key rotation needed
- Backup verification failed
- Access control policy changes
- Vulnerability detected

## 🛠️ Security Tools

### Built-In Tools
```python
# Payload Sanitizer
from glassbox.security.sanitizer import PayloadSanitizer
sanitizer = PayloadSanitizer()
clean_payload = sanitizer.sanitize(raw_payload)

# Schema Validator
from glassbox.governance.schema_validator import SchemaValidator
validator = SchemaValidator()
validator.validate(payload, decision_type)

# Audit Logger
from glassbox.governance.audit_logger import AuditLogger
logger = AuditLogger()
logger.record_decision(decision_record)
```

### External Tools
- **OWASP ZAP** - Web application scanning
- **SonarQube** - Code quality & security
- **Snyk** - Dependency vulnerability scanning
- **HashiCorp Vault** - Secrets management
- **AWS KMS / GCP Cloud KMS** - Key management

## 🔑 Key Management

### Generating Encryption Keys
```bash
# Generate new encryption key
openssl enc -aes-256-cbc -S $(openssl rand -hex 8) -P -pass pass:secret

# Store securely
export GLASSBOX_ENCRYPTION_KEY="your-key-here"
# Better: Use secrets manager (Vault, AWS Secrets Manager, etc.)
```

### Key Rotation
- Rotate encryption keys: Quarterly
- Rotate API keys: Annually (or upon compromise)
- Rotate database credentials: Annually
- Rotate SSL/TLS certs: Before expiration

## 🧪 Security Testing

### Unit Testing
```python
def test_payload_sanitization():
    """Verify payloads are sanitized."""
    malicious = "<script>alert('xss')</script>"
    sanitized = PayloadSanitizer().sanitize({'value': malicious})
    assert '<script>' not in str(sanitized)
```

### Integration Testing
```python
def test_authentication_required():
    """Verify API requires authentication."""
    response = requests.get('/api/v1/decisions')
    assert response.status_code == 401
```

### Penetration Testing
- SQL injection attempts
- Cross-site scripting (XSS)
- Cross-site request forgery (CSRF)
- Authentication bypass
- Authorization bypass
- Privilege escalation
- Sensitive data exposure

## 📚 Related Documentation

- **Hardening guide**: [hardening.md](hardening.md)
- **Compliance**: [../COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Deployment**: [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Architecture**: [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)

## 🤝 Reporting Security Issues

Found a security vulnerability?
1. **Do NOT** open a public GitHub issue
2. Open a **private** security advisory at: [github.com/mohammedakbaransari/glassbox-agentic-governance/security/advisories](https://github.com/mohammedakbaransari/glassbox-agentic-governance/security/advisories)
3. Include: Description, impact, reproduction steps
4. Credit in release notes (if desired)

## Known Security Considerations (v1.2.0)

| Item | Risk | Status |
|---|---|---|
| `tenant_id` path validation does not verify result stays inside `GLASSBOX_LOG_DIR` after `Path.resolve()` | MEDIUM — mitigated by OS file permissions | Open — fix: add `startswith(base_log_dir)` check |
| `user_override` accepted from request body in v1.0.0 | HIGH | Fixed in v1.0.1 — now restricted to authenticated sessions only |
| Policy snapshot uses `object.__setattr__` guard; can be bypassed via `object.__setattr__` directly | LOW — requires code access | Accepted risk |
| GENESIS_SENTINEL (`"0"*64`) hardcoded in advanced audit | LOW — only affects crash recovery edge case | Accepted risk |


