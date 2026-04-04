# glassbox/compliance — Compliance Control Catalogue

The `compliance` package stores all compliance obligations as structured database records — nothing hardcoded.

| Module | Role |
|---|---|
| `catalogue.py` | `ComplianceCatalogue` — 70 controls, 17 frameworks, evidence collection |

**Frameworks:** NIST AI RMF · EU AI Act · NIST CSF 2.0 · OWASP Agentic Top 10 · NIST 800-207 ZTA · ASD Essential Eight · IEC 62443 · NERC CIP · SOCI Act · Purdue Model 2.0 · Cyber Security Act 2024

---

## Quick Start

```python
from glassbox.compliance.catalogue import ComplianceCatalogue

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

---

## Performance Characteristics

| Operation | Latency | Notes |
|-----------|---------|-------|
| posture_summary() | 5–10 ms | Aggregates all 70 controls |
| gap_analysis(framework) | 2–5 ms | Filters to framework |
| get_evidence(control_id) | 1–2 ms | Direct lookup |
| record_evidence() | 3–8 ms | SQLite insert |

---

## Common Errors

### Error: "Duplicate control_id"

**Symptom:**
```python
cat.add_custom_control({
    "control_id": "EUAI.A12",  # Already exists
    ...
})
# Error: DuplicateControlError
```

**Solution:**
```python
# Use unique ID for custom controls
cat.add_custom_control({
    "control_id": "CUSTOM-001",  # Must be unique
    "framework": "Internal Policy",
    ...
})
```

### Error: "Gap analysis returns empty"

**Symptom:**
```python
gaps = cat.gap_analysis("EU AI Act")
# Returns empty list even though controls are missing
```

**Cause:** Gap analysis only returns controls with implementation_status != "implemented"

**Solution:**
```python
# View all controls for framework, not just gaps
all_controls = cat.get_framework_controls("EU AI Act")
print(f"Implemented: {sum(1 for c in all_controls if c['status'] == 'implemented')}")
print(f"Partial: {sum(1 for c in all_controls if c['status'] == 'partial')}")
print(f"Missing: {sum(1 for c in all_controls if c['status'] == 'missing')}")
```

### Error: "Evidence not auto-collected"

**Symptom:**
```python
cat.record_evidence(...)  # manual
pipeline = GovernancePipeline(compliance_catalogue=cat)
response = pipeline.process(request)
# But evidence still empty
```

**Cause:** Compliance catalogue may not be passed to pipeline

**Solution:**
```python
# Ensure catalogue is wired into pipeline
pipeline = GovernancePipeline(
    compliance_catalogue=cat,  # Must be set
    audit_repo=db.audit_repo(),
    ...
)

# Now pipeline.process() will auto-record evidence
```

---

## Adding Custom Controls

```python
cat.add_custom_control({
    "control_id": "INTERNAL-001",
    "framework": "Internal Policy",
    "category": "AI Decision Governance",
    "title": "Procurement AI authority limits",
    "description": "All AI procurement decisions above $100K require human approval.",
    "glassbox_mapping": "AgentContract max_amount + WorkflowEngine",
    "implementation_status": "implemented",
    "notes": "Implemented via AgentContract for all procurement agents",
})

gaps = cat.gap_analysis("Internal Policy")
```

---

See [../../docs/COMPLIANCE.md](../../docs/COMPLIANCE.md) for all frameworks and mapping strategy.
