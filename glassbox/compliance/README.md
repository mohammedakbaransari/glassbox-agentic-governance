# glassbox/compliance — Compliance Control Catalogue

The `compliance` package stores all compliance obligations as structured database records — nothing hardcoded.

| Module | Role |
|---|---|
| `catalogue.py` | `ComplianceCatalogue` — 48 controls, 11 frameworks, evidence collection |

**Frameworks:** NIST AI RMF · EU AI Act · NIST CSF 2.0 · OWASP Agentic Top 10 · NIST 800-207 ZTA · ASD Essential Eight · IEC 62443 · NERC CIP · SOCI Act · Purdue Model 2.0 · Cyber Security Act 2024

```python
cat = ComplianceCatalogue()

# Posture report
summary = cat.posture_summary()
# {'EU AI Act': {'total':6, 'implemented':4, 'coverage_pct':83.3}, ...}

# Gap analysis
gaps = cat.gap_analysis("NIST CSF 2.0")

# Evidence auto-collected from pipeline decisions
pipeline = GovernancePipeline(compliance_catalogue=cat)
```

See [../../docs/COMPLIANCE.md](../../docs/COMPLIANCE.md) for full framework mapping.
