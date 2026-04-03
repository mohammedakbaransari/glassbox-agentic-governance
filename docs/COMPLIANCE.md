# GlassBox — Compliance Framework Reference

**v1.0.0 | Mohammed Akbar Ansari | Independent Researcher**

This document maps every supported compliance standard to GlassBox components and controls. All 48 controls are stored as database records in `ComplianceCatalogue` — no hardcoding.

---

## How Compliance Works in GlassBox

```
AI Decision ──► GovernancePipeline ──► Disposition
                       │
                       ▼
              _collect_compliance_evidence()
                       │
                ┌──────┴──────┐
                │             │
          EUAI.A12      AIRM.MG.02    ... (auto-mapped by decision type + outcome)
                │
          ComplianceCatalogue (SQLite)
                │
          posture_summary() / gap_analysis()
```

Every governed decision automatically produces compliance evidence. The `ComplianceCatalogue` stores which decisions satisfy which controls, enabling auditors to query: "Show me evidence that we comply with EU AI Act Article 12."

---

## Quick Reference

```python
from glassbox.compliance.catalogue import ComplianceCatalogue

cat = ComplianceCatalogue()

# Overall posture
summary = cat.posture_summary()
# {'NIST AI RMF': {'total': 5, 'implemented': 4, 'coverage_pct': 90.0}, ...}

# Gaps
gaps = cat.gap_analysis()

# Evidence for a control
ev = cat.get_evidence("EUAI.A12")

# Record manual evidence
cat.record_evidence("EUAI.A14", "manual", notes="Human review queue active")

# All frameworks
cat.frameworks_list()
```

---

## NIST AI RMF

The AI Risk Management Framework from NIST (2023) defines four core functions.

| Control ID | Function | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| AIRM.GV.01 | GOVERN | Risk management policies | PolicyEngine, PolicyRepository | ✅ Implemented |
| AIRM.MP.01 | MAP | AI risk identification | DecisionType taxonomy, RiskEvaluator | ✅ Implemented |
| AIRM.ME.01 | MEASURE | AI risk measurement | RiskEvaluator (0–100 score) | ✅ Implemented |
| AIRM.MG.01 | MANAGE | AI risk treatment | Disposition (EXECUTE/REVIEW/BLOCK) | ✅ Implemented |
| AIRM.MG.02 | MANAGE | AI decision audit trail | AuditLogger, SQLiteAuditRepository | ✅ Implemented |

---

## EU AI Act

Applicable to high-risk AI systems operating in the EU (effective 2026).

| Control ID | Article | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| EUAI.A9 | Art. 9 | Risk management system | RiskEvaluator, PolicyEngine | ✅ Implemented |
| EUAI.A12 | Art. 12 | Record-keeping | AuditLogger (immutable append-only) | ✅ Implemented |
| EUAI.A13 | Art. 13 | Transparency | ExecutionTrace, PolicyEvaluation.message | ✅ Implemented |
| EUAI.A14 | Art. 14 | Human oversight | WorkflowEngine, HUMAN_REVIEW | ✅ Implemented |
| EUAI.A16 | Art. 16 | Provider obligations | PolicyRepository versioning | ⚠️ Partial |
| EUAI.A17 | Art. 17 | Quality management | ComplianceCatalogue, DecisionReplay | ⚠️ Partial |

---

## NIST CSF 2.0

The Cybersecurity Framework v2.0 (2024) adds GOVERN as a new sixth function.

| Control ID | Function | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| CSF2.GV.OC-01 | GOVERN | Organisational context | GovernancePipeline(environment=) | ⚠️ Partial |
| CSF2.GV.RM-01 | GOVERN | Risk management strategy | RiskEvaluator thresholds | ⚠️ Partial |
| CSF2.ID.AM-01 | IDENTIFY | Asset management | AgentContract registry | ✅ Implemented |
| CSF2.PR.AA-01 | PROTECT | Identity management | validate_agent_id(), AgentContract | ✅ Implemented |
| CSF2.PR.DS-01 | PROTECT | Data security | AuditLogger (immutable) | ⚠️ Partial |
| CSF2.DE.AE-01 | DETECT | Anomaly analysis | AnomalyDetector Z-score baseline | ✅ Implemented |
| CSF2.DE.CM-01 | DETECT | Continuous monitoring | VelocityBreaker, AnomalyDetector | ✅ Implemented |
| CSF2.RS.MA-01 | RESPOND | Incident management | VelocityBreaker cooldown, circuit breaker | ⚠️ Partial |
| CSF2.RC.RP-01 | RECOVER | Recovery planning | DecisionReplay, WorkflowEngine | ⚠️ Partial |

---

## OWASP Agentic Top 10 (2026)

Security risks specific to autonomous AI agent systems.

| Control ID | Risk | Title | GlassBox Mitigation | Status |
|---|---|---|---|---|
| OWASP.A01 | A01 | Prompt Injection | PayloadSanitizer + AgentContract | ✅ Implemented |
| OWASP.A02 | A02 | Insecure Output Handling | SchemaValidator, PayloadSanitizer | ✅ Implemented |
| OWASP.A03 | A03 | Excessive Agency | AgentContract (types/amounts/delegation) | ✅ Implemented |
| OWASP.A04 | A04 | Uncontrolled Resource Consumption | VelocityBreaker + fleet ecosystem budget | ✅ Implemented |
| OWASP.A05 | A05 | Tool Integrity Failure | AnomalyDetector, PayloadSanitizer | ✅ Implemented |
| OWASP.A06 | A06 | Sensitive Data Exposure | AuditLogger.include_payload=False | ⚠️ Partial |
| OWASP.A07 | A07 | Cascading Agent Failures | AgentOrchestrator chain abort | ✅ Implemented |
| OWASP.A08 | A08 | Weak Authentication | AgentContract, validate_agent_id() | ✅ Implemented |
| OWASP.A09 | A09 | Supply Chain Risk | PROC-002 supplier registry | ✅ Implemented |
| OWASP.A10 | A10 | Multi-Agent Trust | AgentContract delegation limits | ✅ Implemented |

---

## NIST SP 800-207 — Zero Trust Architecture

| Control ID | Tenet | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ZTA.TE-01 | 1 | Never trust, always verify | Per-decision governance regardless of source | ✅ Implemented |
| ZTA.TE-02 | 2 | Least privilege | AgentContract permitted_types, max_amount | ✅ Implemented |
| ZTA.TE-03 | 3 | Assume breach | PayloadSanitizer on every request | ✅ Implemented |
| ZTA.PE-01 | — | Dynamic policy evaluation | PolicyEngine.evaluate() per-decision | ✅ Implemented |

---

## ASD Essential Eight (AU)

Australia's Essential Eight Maturity Model.

| Control ID | Mitigation | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| E8.ML2.01 | App Control | Agent whitelisting | AgentContract permitted decision types | ✅ Implemented |
| E8.ML2.02 | Patching | Version management | pyproject.toml, CHANGELOG.md | ⚠️ Partial |
| E8.ML3.01 | MFA | Privileged operations | AgentContract + WorkflowEngine dual-approval | ⚠️ Partial |
| E8.ML2.03 | Audit Logging | Activity logging | AuditLogger, SQLiteAuditRepository | ✅ Implemented |

---

## NERC CIP — Power Sector

North American Electric Reliability Corporation Critical Infrastructure Protection.

| Control ID | Standard | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| NERC.CIP007 | CIP-007 | Systems Security Management | IT_OPS decision type + GRID-001 policy | ⚠️ Partial |
| NERC.CIP010 | CIP-010 | Configuration Change Management | ITOPS-001 change window policy | ⚠️ Partial |

---

## IEC 62443 / ISA 99 — Industrial Automation

International standard for industrial cybersecurity.

| Control ID | Requirement | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| IEC62443.SR1.1 | SR 1.1 | User identification | AgentContract, validate_agent_id() | ✅ Implemented |
| IEC62443.SR2.1 | SR 2.1 | Authorisation enforcement | PolicyEngine evaluation | ✅ Implemented |
| IEC62443.SR6.1 | SR 6.1 | Audit log accessibility | AuditRepository.query() | ✅ Implemented |

---

## SOCI Act 2018 (AU)

Australian Security of Critical Infrastructure Act.

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| SOCI.S30BC | s30BC | Positive security obligation | ComplianceCatalogue posture, mandatory controls | ⚠️ Partial |
| SOCI.S30BD | s30BD | Incident reporting | EventBus SecurityViolation → WebhookHandler | ⚠️ Partial |

---

## Purdue Model 2.0

Architecture reference for OT/ICS network segmentation.

| Control ID | Level | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| PURDUE.L3-L4 | L3–L4 | OT/Enterprise boundary | AgentContract zone-specific types | ⚠️ Partial |
| PURDUE.L0-L2 | L0–L2 | OT protection | ITOPS-001, GRID-001 dual authorisation | ⚠️ Partial |

---

## Cyber Security Act 2024 (AU)

| Control ID | Obligation | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| CSA24.INCIDENT | Mandatory | Incident reporting | EventBus SecurityViolation events | ⚠️ Partial |

---

## Adding Custom Controls

```python
cat.add_custom_control({
    "control_id":            "INTERNAL-001",
    "framework":             "Internal Policy",
    "category":              "AI Decision Governance",
    "title":                 "Procurement AI authority limits",
    "description":           "All AI procurement decisions above $100K require human approval.",
    "glassbox_mapping":      "AgentContract max_amount + WorkflowEngine",
    "implementation_status": "implemented",
    "notes":                 "Implemented as AgentContract for all procurement agents",
})
```

---

## Compliance Evidence Auto-Collection

GlassBox automatically collects evidence from governed decisions. Every `pipeline.process()` call maps the decision outcome to relevant control IDs and records evidence in the compliance database.

```python
pipeline = GovernancePipeline(compliance_catalogue=cat)
pipeline.process(request)        # evidence auto-collected

# Query evidence
evidence = cat.get_evidence("EUAI.A12")   # all evidence for Art. 12
posture  = cat.posture_summary()           # framework-level coverage
gaps     = cat.gap_analysis("NIST CSF 2.0")  # missing controls
```

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
