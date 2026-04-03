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
