# GlassBox Security Hardening Guide

Production security strategies for protecting GlassBox deployments against threats, misuse, and attack vectors.

**Quick Navigation:**
- [Threat Model](#threat-model) | [Authentication & Authorization](#authentication--authorization) | [Network Security](#network-security) | [Data Security](#data-security) | [Secret Management](#secret-management) | [Compliance & Auditing](#compliance--auditing) | [Incident Response](#incident-response)

---

## Threat Model

### Assets at Risk

| Asset | Impact of Breach | Confidentiality | Integrity | Availability |
|---|---|---|---|---|
| Policy database | Attacker modifies policies; all decisions affected | Critical | Critical | High |
| Audit logs | Loss of evidence; non-repudiation violation | Critical | Critical | Medium |
| Decision payloads | Exposure of PII/financial/medical data | Critical | High | Low |
| API credentials | Unauthorized decision requests | High | High | Medium |
| Pipeline execution | Malicious decisions bypass governance | Critical | Critical | Critical |
| Agent credentials | Attacker impersonates legitimate agents | High | Critical | Medium |

### Threat Actors & Attack Vectors

| Threat | Attacker | Vector | Impact | Severity |
|---|---|---|---|---|
| **SQL Injection** | External (unauthenticated) | Malicious payload in decision API | Credential theft, data exfiltration | Critical |
| **Policy Tampering** | Insider (engineer, ops) | Direct DB modification | All subsequent decisions invalid | Critical |
| **Audit Log Deletion** | Insider with admin access | DROP TABLE, log rotation abuse | Non-repudiation failure | High |
| **Replay Attack** | External | Capture & resend valid decision | Duplicate unwanted actions | High |
| **Privilege Escalation** | Authorized user (analyst) | API to execute admin operations | Full system compromise | High |
| **Denial of Service** | External | Rate limit bypass; resource exhaustion | Service unavailable | Medium |
| **Man-in-the-Middle** | Network attacker | Intercept unencrypted API traffic | Decision interception/modification | High |
| **Credential Theft** | External | Phishing, exposed env vars | Unauthorized API access | High |
| **Malware / Ransomware** | Malicious code | Infect container or VM | Full compromise | Critical |
| **Supply Chain** | External vendor | Compromised dependency | Logic injection into pipeline | Medium |

### Risk Matrix

```
Severity vs. Likelihood

CRITICAL:  SQL Injection, Policy Tampering, Ransomware, Malicious Agent
HIGH:      Replay, Privilege Escalation, Credential Theft, MitM
MEDIUM:    DoS, Audit Deletion, Supply Chain
LOW:       Physical theft (minor since data encrypted)
```

---

## Authentication & Authorization

### API Authentication

#### Strategy 1: API Keys (Simple, for Internal Services)

```python
from glassbox.api.auth import APIKeyAuth

# Generate key
key = APIKeyAuth.generate_key("procurement_service")
# Output: sk_live_abc123def456...

# Require in all API requests
@app.route("/decisions", methods=["POST"])
@APIKeyAuth.require_api_key
def post_decision():
    payload = request.json
    result = pipeline.execute(payload)
    return result
```

**Security measures:**
- Store keys in secrets manager (AWS Secrets, HashiCorp Vault), not env vars
- Rotate keys every 90 days
- Revoke immediately if compromised
- Use separate key per service (don't share)
- Log all API calls with key ID

```bash
# Secure key storage
export GLASSBOX_API_KEY_STORE="aws-secrets"  # Not inline!
```

#### Strategy 2: OAuth 2.0 (Enterprise, for User Apps)

```python
from glassbox.api.auth import OAuth2Auth

# Setup OAuth
oauth = OAuth2Auth(
    provider="https://auth.company.com",
    client_id="glassbox_app",
    client_secret=os.environ["OAUTH_SECRET"],  # From secrets manager
)

# All requests must include OAuth token
@app.route("/decisions", methods=["POST"])
@oauth.require_oauth(scopes=["decide:create"])
def post_decision():
    # User identity available
    user_id = oauth.current_user()
    decision_id = execute_and_log(request.json, user_id)
    return {"decision_id": decision_id}
```

**Security measures:**
- Use provider IAM (Okta, Azure AD, Keycloak)
- Short-lived tokens (15 min expiry)
- Refresh tokens stored securely
- Scope permissions finely (least privilege)

#### Strategy 3: mTLS (Service-to-Service)

```python
# Enable mutual TLS for inter-service communication
# glassbox/api/app.py
from flask import Flask
import ssl

app = Flask(__name__)

# Load certificates
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain(
    certfile="/etc/glassbox/certs/glassbox.crt",   # GlassBox certificate
    keyfile="/etc/glassbox/certs/glassbox.key",    # Private key
)

# Require client certificate
context.verify_mode = ssl.CERT_REQUIRED
context.load_verify_locations("/etc/glassbox/certs/ca.crt")  # Client CA

# Run with mTLS
if __name__ == "__main__":
    app.run(ssl_context=context, host="0.0.0.0", port=8443)

# Client code
import requests
response = requests.post(
    "https://glassbox-api:8443/decisions",
    json=payload,
    cert=("/path/to/client.crt", "/path/to/client.key"),  # Client cert pair
    verify="/path/to/ca.crt",  # Verify server cert
)
```

### Authorization (Role-Based Access Control — RBAC)

```python
from enum import Enum
from glassbox.api.auth import RBAC

class Role(Enum):
    ANALYST      = "analyst"      # View decisions
    APPROVER     = "approver"     # Approve/reject workflows
    ADMIN        = "admin"        # Manage policies, users
    COMPLIANCE   = "compliance"   # View audit logs
    DEVELOPER    = "developer"    # Deploy policies (dev only)

# Define permissions
RBAC.add_role(
    Role.ANALYST,
    permissions=[
        "decision:read",
        "decision:list",
    ]
)

RBAC.add_role(
    Role.APPROVER,
    permissions=[
        "decision:read",
        "workflow:approve",
        "workflow:reject",
    ]
)

RBAC.add_role(
    Role.ADMIN,
    permissions=[
        "policy:create",
        "policy:update",
        "policy:delete",
        "user:manage",
        "audit:read",
    ]
)

# Enforce permissions
@app.route("/policies", methods=["POST"])
@RBAC.require_permission("policy:create")
def create_policy():
    # Only admins reach here
    new_policy = Policy(**request.json)
    return {"policy_id": new_policy.id}

@app.route("/decisions/<id>/approve", methods=["POST"])
@RBAC.require_permission("workflow:approve")
def approve_decision(id):
    # Only approvers reach here
    wfe.approve(id, actor=current_user())
    return {"status": "approved"}
```

### Principle of Least Privilege

```python
# BAD: Everyone is admin
user.role = Role.ADMIN

# GOOD: Minimal required permissions
# Analyst: can only view decisions
user.role = Role.ANALYST

# Procurement approver: can only approve procurement decisions
user.role = Role.APPROVER
user.policy_tags = ["PROCUREMENT"]  # Scoped to domain

# Temporary escalation for incident
user.role = Role.ADMIN
user.escalation_expires_at = datetime.now() + timedelta(hours=1)
audit_log(f"User escalated to ADMIN for 1 hour: {reason}")
```

---

## Network Security

### 1. Encryption in Transit (TLS 1.3)

```python
# Force HTTPS; reject HTTP
from flask import redirect, request
from flask_talisman import Talisman

app = Flask(__name__)

# Enforce HTTPS + Security Headers
Talisman(app, force_https=True, strict_transport_security=True)

# Redirect HTTP → HTTPS
@app.before_request
def enforce_https():
    if not request.is_secure and not request.host.startswith("localhost"):
        return redirect(request.url.replace("http://", "https://"), code=301)
```

### 2. Network Segmentation (VPC / Firewall Rules)

**Kubernetes:**
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: glassbox-ingress
spec:
  podSelector:
    labels:
      app: glassbox
  policyTypes:
  - Ingress
  ingress:
  # Only allow from API gateway
  - from:
    - namespaceSelector:
        matchLabels:
          name: api-gateway
    ports:
    - protocol: TCP
      port: 8443
```

**AWS:**
```hcl
# Security group: restrict to API gateway
resource "aws_security_group" "glassbox" {
  ingress {
    from_port   = 8443
    to_port     = 8443
    protocol    = "tcp"
    security_groups = [aws_security_group.api_gateway.id]  # Only API gateway
  }
  
  egress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [aws_db_instance.postgres.address]  # Only PostgreSQL
  }
}
```

### 3. WAF (Web Application Firewall)

```python
# Use AWS WAF or similar before reaching API
# Block:
# - SQL injection patterns
# - Large payloads (> 1 MB)
# - Rapid requests from single IP (DDoS)
# - Known attack patterns

# AWS WAF rules example:
# - AWSManagedRulesSQLiRuleSet
# - RateLimitRule: 2000 requests/5 minutes per IP
# - SizeConstraintSet: max 1 MB payload
```

### 4. Rate Limiting & DDoS Protection

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

@app.route("/decisions", methods=["POST"])
@limiter.limit("100 per minute")  # Tight limit on decisions
def post_decision():
    return pipeline.execute(request.json)

# Use distributed rate limiter for multi-instance
# Redis: rate_limiter = RedisLimiter(redis.Redis())
```

---

## Data Security

### 1. Encryption at Rest

#### For Audit Databases

```python
# Transparent Data Encryption (TDE)
# PostgreSQL
ALTER DATABASE glassbox SET encryption = 'on';

# OR file-level encryption
# Linux: LUKS
sudo cryptsetup luksFormat /dev/sdb
sudo cryptsetup luksOpen /dev/sdb glassbox_encrypted
sudo mkfs.ext4 /dev/mapper/glassbox_encrypted
sudo mount /dev/mapper/glassbox_encrypted /var/lib/pgdata/
```

#### For Audit Log Files

```python
from cryptography.fernet import Fernet
import os

# Generate encryption key
key = Fernet.generate_key()  # Store in secrets manager
cipher = Fernet(key)

# Write encrypted audit logs
def write_encrypted_audit(record):
    encrypted = cipher.encrypt(json.dumps(record).encode())
    audit_file.write(encrypted + b"\n")

# Read encrypted audit logs
def read_encrypted_audit(file_path):
    for line in open(file_path, "rb"):
        decrypted = cipher.decrypt(line.strip())
        yield json.loads(decrypted)
```

### 2. PII Masking & Minimization

```python
from glassbox.security.masker import PII_Masker

masker = PII_Masker()

# Mask sensitive fields before audit
def audit_with_masking(decision):
    masked = {
        "decision_id": decision["decision_id"],
        "agent_id": decision["agent_id"],
        "timestamp": decision["timestamp"],
        # PII fields masked
        "user_id": masker.mask_email(decision.get("user_email")),
        "amount": masker.mask_amount(decision.get("amount")),
    }
    audit_repo.save(masked)

# Regex patterns for common PII
masker.add_pattern(
    name="credit_card",
    pattern=r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
    replacement="****-****-****-****"
)

masker.add_pattern(
    name="phone",
    pattern=r"\b\d{3}[\s\-]?\d{3}[\s\-]?\d{4}\b",
    replacement="***-***-****"
)

# Mask all payloads by default
pipeline = GovernancePipeline(
    mask_sensitive_data=True,
    audit_logger=AuditLogger(masker=masker)
)
```

### 3. Data Retention & Deletion

```python
from datetime import datetime, timedelta

# Delete old audit records
def retention_cleanup(days=90):
    cutoff = datetime.now() - timedelta(days=days)
    deleted = audit_repo.delete_before(cutoff)
    audit_log(f"Deleted {deleted} audit records older than {days} days")

# Schedule daily cleanup
import schedule
schedule.every().day.at("02:00").do(retention_cleanup, days=90)

# GDPR: Right to deletion
def delete_user_data(user_id):
    """Remove all records for user (upon request)"""
    audit_repo.delete_by_user(user_id)
    compliance_repo.delete_by_user(user_id)
    audit_log(f"User {user_id} data deletion: COMPLETE")
```

---

## Secret Management

### 1. Secrets Storage (NEVER in Code/Env Vars)

```python
# BAD ❌
DB_PASSWORD = "postgres123"  # Hardcoded
os.environ["API_KEY"] = "sk_live_abc123"  # In env var

# GOOD ✅
import hvac  # HashiCorp Vault

client = hvac.Client(url="https://vault.company.com:8200")
client.auth.kubernetes.login(
    role="glassbox-role",
    jwt=read_jwt_from_serviceaccount()
)

# Retrieve secrets
db_password = client.secrets.kv.v2.read_secret_version(
    path="glassbox/pg_password"
)["data"]["data"]["password"]

api_key = client.secrets.kv.v2.read_secret_version(
    path="glassbox/api_key"
)["data"]["data"]["key"]
```

### 2. Secrets Rotation

```python
# Rotate database credentials every 30 days
def rotate_postgres_credentials():
    """Rotate DB password in Postgres and Vault"""
    
    # Generate new password (24 chars, mixed)
    new_password = secrets.token_urlsafe(18)
    
    # Update in PostgreSQL
    conn = psycopg2.connect(current_password=old_password)
    conn.cursor().execute(f"ALTER USER glassbox PASSWORD %s", (new_password,))
    
    # Update in Vault
    vault_client.secrets.kv.v2.create_or_update_secret(
        path="glassbox/pg_password",
        secret_data={"password": new_password, "rotated_at": now()}
    )
    
    # Log rotation
    audit_log(f"DB credentials rotated: {now()}")

# Schedule
import schedule
schedule.every(30).days.do(rotate_postgres_credentials)
```

### 3. API Key Rotation

```python
def rotate_api_keys():
    """Retire old API keys, generate new ones"""
    
    old_key_id = get_active_api_key_id()
    
    # Generate new key
    new_key_id, new_key_secret = APIKeyAuth.generate_key("procurement_service")
    
    # Update in Vault
    vault_client.secrets.kv.v2.create_or_update_secret(
        path="glassbox/api_keys/procurement_service",
        secret_data={"key_id": new_key_id, "secret": new_key_secret}
    )
    
    # Give services 24 hours to update
    schedule_key_retirement(old_key_id, hours=24)
    
    audit_log(f"API key rotated: {old_key_id} → {new_key_id}")
```

---

## Compliance & Auditing

### 1. Immutable Audit Logs

```python
# Use append-only storage: WORM (Write Once, Read Many)
# AWS S3: S3 Object Lock
# Azure: Blob Immutable Storage
# GCP: Bucket Lock

from glassbox.compliance.audit_immutable import ImmutableAuditLogger

logger = ImmutableAuditLogger(
    s3_bucket="glassbox-audit-immutable",
    retention_years=7  # Immutable for 7 years (FINRA requirement)
)

# Once written, cannot be deleted or modified (enforced by cloud provider)
logger.write(audit_record)
# Result: object stored in S3 with legal hold + object lock enabled
```

### 2. Audit Logging for Security Events

```python
# Log all security-relevant events
def log_security_event(event_type, actor, resource, outcome, details=None):
    """Log authentication, authorization, admin actions"""
    audit_log({
        "event_type": event_type,
        "timestamp": now(),
        "actor": actor,
        "actor_ip": request.remote_addr,
        "actor_user_agent": request.user_agent,
        "resource": resource,
        "outcome": outcome,  # success, failure, denied
        "details": details,
    })

# Examples
log_security_event("api_key_created", "admin@co.com", "procurement_service", "success")
log_security_event("policy_modified", "engineer@co.com", "PROC-001", "success", 
                  diff={"from": old_rule, "to": new_rule})
log_security_event("authentication_failed", "unknown", "api", "failure", 
                  reason="Invalid API key")
```

### 3. Access Logging

```python
# Log all decision requests for audit trail
@app.before_request
def log_request():
    request.start_time = time.time()

@app.after_request
def log_response(response):
    duration_ms = (time.time() - request.start_time) * 1000
    
    access_log({
        "timestamp": now(),
        "method": request.method,
        "path": request.path,
        "query": request.query_string,
        "status_code": response.status_code,
        "duration_ms": duration_ms,
        "user": current_user(),
        "ip": request.remote_addr,
        "user_agent": request.user_agent,
    })
    
    # Alert on abnormal latency
    if duration_ms > 5000:
        alert_ops(f"Slow request: {request.path} took {duration_ms} ms")
    
    return response
```

---

## Incident Response

### 1. Detection & Alerting

```python
import logging

# Alert on suspicious activity
class SecurityAlerts:
    @staticmethod
    def failed_auth_spike(failed_attempts_in_5min=10):
        """Alert if > 10 failed auth in 5 min (possible brute force)"""
        recent_failures = audit_repo.count_by_type("authentication_failed", last_minutes=5)
        if recent_failures > 10:
            alert_ops(f"Auth spike detected: {recent_failures} failures in 5m")
    
    @staticmethod
    def policy_tampering(changes_in_1min=5):
        """Alert if > 5 policy changes in 1 min (suspicious)"""
        recent_changes = audit_repo.count_by_type("policy_modified", last_minutes=1)
        if recent_changes > 5:
            alert_ops(f"Policy tampering detected: {recent_changes} changes in 1m")
    
    @staticmethod
    def unauthorized_access(denied_count=20):
        """Alert if > 20 authorization denials (escalation attempt)"""
        denied = audit_repo.count_by_outcome("denied", last_minutes=5)
        if denied > denied_count:
            alert_ops(f"Escalation attempt detected: {denied} denials in 5m")

# Run monitoring background thread
import schedule
import threading

def run_security_monitoring():
    while True:
        SecurityAlerts.failed_auth_spike()
        SecurityAlerts.policy_tampering()
        SecurityAlerts.unauthorized_access()
        schedule.run_pending()
        time.sleep(60)

monitor_thread = threading.Thread(target=run_security_monitoring, daemon=True)
monitor_thread.start()
```

### 2. Incident Response Playbook

```python
# Incident: Unauthorized API key used
# Steps:
# 1. Detect
if unauthorized_api_call_detected():
    # 2. Isolate
    revoke_api_key(compromised_key_id)
    alert_ops("CRITICAL: Unauthorized API access detected")
    
    # 3. Investigate
    suspicious_decisions = audit_repo.query(
        api_key=compromised_key_id,
        start_time=compromised_key.created_at,
        end_time=now()
    )
    
    # 4. Contain
    for decision in suspicious_decisions:
        if decision.status == "EXECUTED":
            # Attempt to reverse
            reverse_decision(decision)
            audit_log(f"Decision {decision.id} reversed due to unauthorized API")
    
    # 5. Communication
    notify_executives(
        title="Security Incident: Unauthorized API Access",
        details=f"Compromised key used for {len(suspicious_decisions)} decisions",
        affected_systems=["Procurement", "Financial"],
        actions_taken=["API key revoked", "Decisions reversed"]
    )

# Incident: Policy database compromised
# Steps:
# 1. Snapshot policies (immutable backup)
policy_snapshot = policy_repo.export_all()
backup_to_s3("s3://backups/policies-precompromise-2026-04-04.json.enc", policy_snapshot)

# 2. Restore from known-good backup
policy_repo.restore_from_backup("2026-04-03T00:00:00Z")

# 3. Audit what policies were modified
compromised_policies = audit_repo.query(
    event_type="policy_modified",
    start_time=compromise_detected_at,
    end_time=compromise_detected_at + timedelta(hours=24)
)

# 4. Replay affected decisions
affected_decisions = audit_repo.query(
    policy_ids=[p.id for p in compromised_policies],
    start_time=compromise_detected_at
)
for decision in affected_decisions:
    decision_replay.replay_with_restored_policies(decision)
```

### 3. Post-Incident Review

```python
def post_incident_review(incident_id):
    """Document lessons learned"""
    incident = incident_repo.get(incident_id)
    
    review = {
        "incident_id": incident_id,
        "date": now(),
        "timeline": [
            {"time": "14:32:10", "event": "Unauthorized API call detected"},
            {"time": "14:32:45", "event": "API key revoked"},
            {"time": "14:35:00", "event": "5 unauthorized decisions rolled back"},
        ],
        "root_cause": "API key leaked in GitHub commit",
        "remediation": [
            "Enable secret scanning in GitHub Actions",
            "Rotate all API keys immediately",
            "Implement API key expiry (60 days)",
        ],
        "owner": "infrastructure_team",
        "deadline": now() + timedelta(days=7),
    }
    
    incident_repo.save(review)
    notify_team(review)
```

---

## Security Hardening Checklist

### Pre-Production

- [ ] All secrets in secrets manager (not env vars, code, or config files)
- [ ] TLS 1.3 enforced (no HTTP)
- [ ] API authentication enabled (API key, OAuth 2.0, or mTLS)
- [ ] Role-based access control implemented (RBAC)
- [ ] Network segmentation (firewall rules, VPC)
- [ ] WAF deployed
- [ ] Rate limiting enabled
- [ ] Audit logging configured
- [ ] Encryption at rest enabled (databases + logs)
- [ ] PII masking configured
- [ ] Dependency vulnerabilities scanned (pip-audit, safety)
- [ ] Code security scanned (bandit, semgrep)
- [ ] Secrets scanning enabled (pre-commit hooks, GitHub Actions)
- [ ] SAST tool integrated (SonarQube, CodeQL)
- [ ] Penetration testing scheduled
- [ ] Incident response playbook written and tested
- [ ] Team trained on security policies

### Ongoing

- [ ] Daily security monitoring (auth spikes, policy tampering)
- [ ] Monthly secret rotation
- [ ] Quarterly audit log review for anomalies
- [ ] Semi-annual penetration testing
- [ ] Annual security policy review
- [ ] Incident response drills (monthly)

---

## See Also

- **[TROUBLESHOOTING.md](../USER/troubleshooting.md#security)** — Security-related issues and solutions
- **[security/README.md](../glassbox/security/README.md)** — Payload sanitization and injection detection
- **[DEPLOYMENT.md](../DEPLOYMENT.md#security-hardening)** — Platform-specific security deployment
- **[COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)** — Compliance requirements and controls
- **[CONTRIBUTING.md](../CONTRIBUTING.md#security-vulnerability-reporting)** — Responsible disclosure

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
