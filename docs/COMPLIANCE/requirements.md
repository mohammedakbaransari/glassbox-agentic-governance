# GlassBox — Compliance Framework Reference

**v1.2.0 | Mohammed Akbar Ansari | Independent Researcher**

This document maps every supported compliance standard to GlassBox components and controls. All 97 controls are stored as database records in `ComplianceCatalogue` — no hardcoding.

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
# {'NIST AI RMF': {'total': 5, 'implemented': 5, 'coverage_pct': 100.0}, ...}

# Gaps
gaps = cat.gap_analysis()

# All controls for a framework
controls = cat.list_controls(framework="EU AI Act")
print(f"Implemented: {sum(1 for c in controls if c['implementation_status'] == 'implemented')}")
print(f"Partial:     {sum(1 for c in controls if c['implementation_status'] == 'partial')}")

# Evidence for a control
ev = cat.get_evidence("EUAI.A12")

# Record manual evidence
cat.record_evidence("EUAI.A16", "manual", notes="Quality management system active")

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
| EUAI.A11 | Art. 11 | Technical documentation | ComplianceCatalogue.posture_summary(), ComplianceReporter | ⚠️ Partial |
| EUAI.A12 | Art. 12 | Record-keeping | AuditLogger (immutable append-only) | ✅ Implemented |
| EUAI.A13 | Art. 13 | Transparency | ExecutionTrace, PolicyEvaluation.message | ✅ Implemented |
| EUAI.A14 | Art. 14 | Human oversight | WorkflowEngine, HUMAN_REVIEW | ✅ Implemented |
| EUAI.A15 | Art. 15 | Accuracy and robustness | AI-001 confidence threshold, AnomalyDetector, PayloadSanitizer | ⚠️ Partial |
| EUAI.A16 | Art. 16 | Provider obligations | PolicyRepository versioning, ComplianceCatalogue | ⚠️ Partial |
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

## NIST 800-207 — Zero Trust Architecture

| Control ID | Tenet | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ZTA.TE-01 | 1 | Never trust, always verify | Per-decision governance regardless of source | ✅ Implemented |
| ZTA.TE-02 | 2 | Least privilege | AgentContract permitted_types, max_amount | ✅ Implemented |
| ZTA.TE-03 | 3 | Assume breach | PayloadSanitizer on every request | ✅ Implemented |
| ZTA.PE-01 | — | Dynamic policy evaluation | PolicyEngine.evaluate() per-decision | ✅ Implemented |

---

## ISO 27001:2022

International standard for information security management systems.

| Control ID | Annex A | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ISO27K.A5.1 | A.5.1 | Policies for information security | PolicyEngine (policy-as-code), PolicyHotReloader | ✅ Implemented |
| ISO27K.A5.2 | A.5.2 | Information security roles and responsibilities | AgentContract (role-based authority), WorkflowEngine (review roles) | ✅ Implemented |
| ISO27K.A5.36 | A.5.36 | Compliance with policies, rules and standards | ComplianceCatalogue, ComplianceReporter, DecisionReplay | ✅ Implemented |
| ISO27K.A8.15 | A.8.15 | Logging | AuditLogger (immutable decision log), SQLiteAuditRepository | ✅ Implemented |
| ISO27K.A8.16 | A.8.16 | Monitoring activities | AnomalyDetector, VelocityBreaker, OtelExporter | ✅ Implemented |

---

## ISO/IEC 42001:2023 — AI Management System

| Control ID | Clause | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ISO42K.6.1 | 6.1 | Actions to address AI risks and opportunities | RiskEvaluator, ComplianceCatalogue risk tolerance | ✅ Implemented |
| ISO42K.8.4 | 8.4 | AI system impact assessment | PolicySimulator (pre-deployment impact), ComplianceReporter | ✅ Implemented |
| ISO42K.9.1 | 9.1 | Monitoring, measurement, analysis and evaluation | OtelExporter, AuditLogger.summary_stats, ComplianceReporter | ✅ Implemented |
| ISO42K.10.1 | 10.1 | Continual improvement | DecisionReplay (policy regression), PolicySimulator, ComplianceCatalogue.gap_analysis | ✅ Implemented |

---

## SOC 2 Type II

AICPA Trust Services Criteria for security, availability, processing integrity, confidentiality, and privacy.

| Control ID | Criteria | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| SOC2.CC6.1 | CC6.1 | Logical access security measures | AgentContract validation, PayloadSanitizer, validate_agent_id | ✅ Implemented |
| SOC2.CC7.2 | CC7.2 | System monitoring | AnomalyDetector, VelocityBreaker, OtelExporter metrics | ✅ Implemented |
| SOC2.CC8.1 | CC8.1 | Change management controls | PolicyHotReloader, PolicySimulator, IT-OPS-004 change log | ✅ Implemented |
| SOC2.CC9.1 | CC9.1 | Risk mitigation activities | RiskEvaluator, WorkflowEngine (human review for high-risk decisions) | ✅ Implemented |

---

## HIPAA Security and Privacy Rules

Applicable to healthcare AI decisions involving protected health information (PHI).

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| HIPAA.164.308a1 | §164.308(a)(1) | Security management process | GovernancePipeline, PolicyEngine, AuditLogger | ✅ Implemented |
| HIPAA.164.308a3 | §164.308(a)(3) | Workforce security | AgentContract (permitted_types), SECURITY-001 (production control) | ✅ Implemented |
| HIPAA.164.312b | §164.312(b) | Audit controls | AuditLogger (immutable), SQLiteAuditRepository, ExecutionTrace | ✅ Implemented |
| HIPAA.164.514e | §164.514(e) | Minimum necessary standard | CLIN-001/002 clinical policies, GEN-001 PII detection | ⚠️ Partial |

---

## Colorado AI Act — SB 24-205

Colorado's high-risk AI system law, effective February 2026.

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| COL.SB205.8 | §8 | Risk management policy for high-risk AI | GovernancePipeline, RiskEvaluator, PolicyEngine | ✅ Implemented |
| COL.SB205.9 | §9 | Human review mechanism | WorkflowEngine (human review queue), quorum_approve | ✅ Implemented |
| COL.SB205.10 | §10 | Disclosure of high-risk AI use | DecisionExplainer, explanation field in DecisionResponse | ✅ Implemented |

---

## PCI DSS v4.0

Payment Card Industry Data Security Standard, applicable to AI decisions in payment flows.

| Control ID | Requirement | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| PCI4.10.3 | Req. 10.3 | Audit logs are protected from destruction | AuditLogger (append-only, immutable), SQLiteAuditRepository | ✅ Implemented |
| PCI4.6.3 | Req. 6.3 | Security event detection and response | PayloadSanitizer, EventBus SecurityViolation, VelocityBreaker | ✅ Implemented |

---

## GDPR — EU General Data Protection Regulation

Applicable to AI systems processing personal data of EU data subjects.

| Control ID | Article | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| GDPR.A5 | Art. 5 | Data minimisation and purpose limitation | AuditLogger.include_payload=False, GEN-001 PII detection, COMPLIANCE-001 | ⚠️ Partial |
| GDPR.A22 | Art. 22 | Automated individual decision-making | GEN-002 EU Automated Decision Gate (forces human_review_available=True for EU subjects) | ✅ Implemented |
| GDPR.A33 | Art. 33 | Notification of a personal data breach (72h) | COMPLIANCE-003 breach notification, EventBus SecurityViolation events | ✅ Implemented |

---

## DORA — EU Digital Operational Resilience Act

Applicable to EU financial entities; effective January 2025.

| Control ID | Article | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| DORA.Art6 | Art. 6 | ICT risk management framework | RiskEvaluator (0–100 composite), PolicyEngine, ComplianceCatalogue | ✅ Implemented |
| DORA.Art17 | Art. 17 | ICT-related incident management | EventBus SecurityViolation + CircuitBreakerTripped, VelocityBreaker cooldown | ⚠️ Partial |
| DORA.Art24 | Art. 24 | Digital operational resilience testing | DecisionReplay (scenario regression), PolicySimulator | ✅ Implemented |
| DORA.Art28 | Art. 28 | Third-party ICT risk management | PROC-002 supplier check, PROC-006 sanctions/debarment, PROC-003 category risk | ✅ Implemented |

---

## APRA CPS 234 — Australian Prudential Standard

Information security standard for APRA-regulated financial entities.

| Control ID | Paragraph | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| CPS234.15 | Para 15 | Information security controls | AgentContract permitted_types (asset classification), PolicyEngine | ✅ Implemented |
| CPS234.36 | Para 36 | Notify APRA of material incidents (72h) | EventBus SecurityViolation, COMPLIANCE-003 breach notification, WebhookEventHandler | ⚠️ Partial |
| CPS234.51 | Para 51 | Information security control testing | DecisionReplay (control regression), PolicySimulator (impact analysis) | ✅ Implemented |

---

## FFIEC CAT — Financial Institution Cybersecurity Assessment Tool

US federal banking regulators' cybersecurity assessment framework.

| Control ID | Domain | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| FFIEC.D1.CC | Domain 1 | Cyber risk identification and classification | RiskEvaluator, DecisionType taxonomy, ComplianceCatalogue | ✅ Implemented |
| FFIEC.D2.TI | Domain 2 | Threat intelligence | AnomalyDetector (Z-score drift), VelocityBreaker (volumetric anomaly) | ✅ Implemented |
| FFIEC.D3.CY | Domain 3 | Cybersecurity controls | PolicyEngine (35 built-in controls), PayloadSanitizer, AgentContract | ✅ Implemented |
| FFIEC.D4.EX | Domain 4 | External dependency management | PROC-002/006 supplier registry and sanctions, MCP Gateway controls | ⚠️ Partial |

---

## FDA 21 CFR Part 11 — Electronic Records

Applicable to AI systems in clinical, pharmaceutical, and regulated laboratory environments.

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| FDA11.11.10e | §11.10(e) | Audit trails | TamperEvidentAuditLogger (SHA-256 hash chain), AuditLogger, SQLiteAuditRepository | ✅ Implemented |
| FDA11.11.10d | §11.10(d) | System access limited to authorised individuals | AgentContract (identity and permitted_types), validate_agent_id() | ✅ Implemented |
| FDA11.11.50 | §11.50 | Signature manifestations | WorkflowEngine quorum_approve (reviewer identity recorded), AuditLogger (decision lineage) | ⚠️ Partial |

---

## MAS TRM — Monetary Authority of Singapore Technology Risk Management

Applicable to MAS-regulated financial institutions operating AI systems.

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| MASTRM.5 | Section 5 | Access control | AgentContract permitted_types (least privilege), SECURITY-001 production override guard | ✅ Implemented |
| MASTRM.12 | Section 12 | IT incident management | EventBus SecurityViolation + CircuitBreakerTripped, VelocityBreaker cooldown | ⚠️ Partial |
| MASTRM.13 | Section 13 | Outsourcing risk management | PROC-002 supplier known check, PROC-006 sanctions, MCP Gateway controls | ✅ Implemented |

---

## NIST SP 800-53 Rev.5 — Security and Privacy Controls

Comprehensive control catalogue for federal information systems; also referenced by FedRAMP.

| Control ID | Family | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| 800-53.AU-2 | Audit | Event logging | AuditLogger (per-decision), EventBus (SecurityViolation, CircuitBreakerTripped) | ✅ Implemented |
| 800-53.AU-9 | Audit | Protection of audit information | TamperEvidentAuditLogger (SHA-256 hash chain), AuditLogger (append-only) | ✅ Implemented |
| 800-53.CM-3 | Config Mgmt | Configuration change control | IT-OPS-004 change log requirement, PolicyHotReloader | ✅ Implemented |
| 800-53.RA-3 | Risk Assess | Risk assessment | RiskEvaluator (0–100 composite), AnomalyDetector, ComplianceCatalogue.gap_analysis | ✅ Implemented |
| 800-53.SI-3 | System Integrity | Malicious code protection | PayloadSanitizer (injection detection on every request), SchemaValidator | ✅ Implemented |

---

## ASD Essential Eight (AU)

Australia's Essential Eight Maturity Model.

| Control ID | Mitigation | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| E8.ML2.01 | App Control | Agent whitelisting | AgentContract permitted decision types | ✅ Implemented |
| E8.ML2.02 | Patching | Version management | pyproject.toml, repository release history (git tags/commits) | ⚠️ Partial |
| E8.ML2.03 | Audit Logging | Activity logging | AuditLogger, SQLiteAuditRepository | ✅ Implemented |
| E8.ML3.01 | MFA | Privileged operations | AgentContract + WorkflowEngine dual-approval | ⚠️ Partial |

---

## NERC CIP — Power Sector

North American Electric Reliability Corporation Critical Infrastructure Protection.

| Control ID | Standard | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| NERC.CIP007 | CIP-007 | Systems Security Management | IT_OPS decision type, IT-OPS-003 service criticality gate | ⚠️ Partial |
| NERC.CIP010 | CIP-010 | Configuration Change Management | IT-OPS-002 maintenance window, IT-OPS-004 change log requirement | ⚠️ Partial |

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
| PURDUE.L3-L4 | L3–L4 | OT/Enterprise boundary | AgentContract zone-specific types, IT-OPS-002 maintenance window | ⚠️ Partial |
| PURDUE.L0-L2 | L0–L2 | OT protection | IT-OPS-004 destructive action change log, WorkflowEngine quorum_approve | ⚠️ Partial |

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
evidence = cat.get_evidence("EUAI.A12")      # all evidence for Art. 12
posture  = cat.posture_summary()              # framework-level coverage
gaps     = cat.gap_analysis("NIST CSF 2.0")  # controls with status='gap'

# All controls for a framework (including partial)
all_controls = cat.list_controls(framework="EU AI Act")
```

---

## Known Limitations

- **`partial` controls** are not returned by `gap_analysis()` — use `list_controls(framework=...)` and filter by `implementation_status` to view them.
- **NERC CIP007/CIP010** and **Purdue L0–L2/L3–L4**: GlassBox provides the governance layer; sector-specific dual-authorisation policies (e.g. GRID-001) must be registered as custom policies via `PolicyEngine.register()` (see `examples/industry_examples.py`).
- **GDPR Art.22**: enforcement requires passing `jurisdiction` on `DecisionContext`. Without it the GEN-002 policy defaults to non-EU.
- **FDA 21 CFR Part 11 §11.50**: `WorkflowEngine` records reviewer identity but does not yet produce a compliant electronic signature manifest — partial coverage only.

---

## See Also

- **[GLOSSARY.md](../GLOSSARY.md)** — Definitions of compliance and governance terms
- **[TROUBLESHOOTING.md](../USER/troubleshooting.md#compliance)** — Common compliance issues and solutions
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — How compliance checks integrate into the 9-stage pipeline
- **[DEPLOYMENT.md](../DEPLOYMENT.md)** — Compliance considerations for production deployment
- **[compliance/README.md](../../glassbox/compliance/README.md)** — Compliance module documentation

---

*GlassBox v1.2.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher*

