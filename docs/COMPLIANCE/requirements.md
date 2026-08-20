# Compliance Framework Crosswalk

> **Engineering reference, not certification.** This crosswalk maps external
> compliance-framework controls to the GlassBox v2 mechanism that produces
> evidence toward them. A ✅ means the runtime mechanism exists, is real code,
> and is covered by a test cited in [CLAIMS.md](../CLAIMS.md) — not that an
> auditor has certified your specific deployment. Status values describe
> repository mappings, not an organization's control design, operating
> effectiveness, legal compliance, or audit opinion. Reconcile every row with
> [the evidence model](README.md), [CLAIMS.md](../CLAIMS.md), and your deployed
> configuration before using it in an assessment.

GlassBox does not ship pre-built industry policy content (no built-in "35
policies", no hardcoded sector rules). Every control below maps to either a
**mechanism** (a real, tested runtime capability) or a **policy hook** (a
place to attach your own signed [`PolicyBundle`](../ARCHITECTURE.md) rule,
evaluated and evidenced the same way as every other decision). Rows marked
**Policy-defined** are not implemented for you; they are a documented seam to
implement them in.

---

## How Compliance Evidence Works in GlassBox v2

```
Proposed action ──► DecisionService.decide_and_dispatch(...)
                            │
      identity → mandate → policy → risk → limits → baseline
                            │
                    IntentRecord (evidence-before-effect)
                            │
              EvidenceStore.append_intent() — MAC-chained, append-only
                            │
                    Dispatcher.dispatch() (only if ALLOW)
                            │
                    OutcomeRecord — append_outcome()
```

Every governed decision — allowed, denied, or requiring approval — produces a
durable, signed `IntentRecord` before any effect can occur (invariant: evidence
before effect). An auditor asks "show me evidence this decision was governed",
not "show me a control satisfied" — GlassBox proves the former directly;
mapping that to a specific external control is what this document does.

---

## Quick Reference

```python
from glassbox.app.decision_service import DecisionService
from glassbox.domain.decision import DecisionEffect

outcome = service.decide_and_dispatch(credential, action)
outcome.decision.effect          # DecisionEffect.ALLOW / DENY / REQUIRE_APPROVAL
outcome.decision.reasons         # DenialReason values, if denied
outcome.decision.rationale       # human-readable explanation, always present
outcome.receipt                  # EvidenceReceipt: segment_id, seq, record_hmac

# Verify a segment's MAC chain (auditor-facing)
report = evidence_store.verify(segment_id, now=clock.now())
report.status  # IntegrityStatus.INTACT / TAMPERED / UNVERIFIABLE

# Pending human review (compliance sign-off) queue
from glassbox.app.approval_service import ApprovalService
approvals = ApprovalService(runtime)
approvals.list_pending()
```

---

## NIST AI RMF

| Control ID | Function | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| AIRM.GV.01 | GOVERN | Risk management policies | Signed `PolicyBundle` evaluated by the policy decision point on every decision | ✅ Mechanism |
| AIRM.MP.01 | MAP | AI risk identification | `ConsequenceClass`/`Exposure` on every `ProposedAction`; `RiskInputs` | ✅ Mechanism |
| AIRM.ME.01 | MEASURE | AI risk measurement | `glassbox.domain.risk.RiskScore` (0–100, deterministic, no clock reads) | ✅ Mechanism |
| AIRM.MG.01 | MANAGE | AI risk treatment | `DecisionEffect` (ALLOW / DENY / REQUIRE_APPROVAL) + opt-in `RiskConfig.enforce_threshold`/`deny_level` | ✅ Mechanism |
| AIRM.MG.02 | MANAGE | AI decision audit trail | `EvidenceStore.append_intent`/`append_outcome`, MAC-chained, append-only | ✅ Mechanism |

## EU AI Act

Applicable to high-risk AI systems operating in the EU.

| Control ID | Article | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| EUAI.A9 | Art. 9 | Risk management system | `RiskScore` + policy bundle evaluation | ✅ Mechanism |
| EUAI.A11 | Art. 11 | Technical documentation | `AuthorizationDecision.rationale` + policy bundle digest cited on every decision | ⚠️ Partial |
| EUAI.A12 | Art. 12 | Record-keeping | MAC-chained `IntentRecord`/`OutcomeRecord`; see the accepted gap below | ✅ Mechanism |
| EUAI.A13 | Art. 13 | Transparency | `AuthorizationDecision.rationale`, always populated, never omitted | ✅ Mechanism |
| EUAI.A14 | Art. 14 | Human oversight | `DecisionEffect.REQUIRE_APPROVAL` + `ApprovalService` + `WorkflowEngine.quorum_approve` | ✅ Mechanism |
| EUAI.A15 | Art. 15 | Accuracy and robustness | `glassbox.domain.prompt_injection.scan()` on inbound fields and tool output | ⚠️ Partial |
| EUAI.A16 | Art. 16 | Provider obligations | Signed, versioned `PolicyBundle`; content-addressed by SHA-256 | ⚠️ Partial |
| EUAI.A17 | Art. 17 | Quality management | `decide_and_dispatch`'s `replay()`/`diff_outcomes()` for regression testing | ⚠️ Partial |

## NIST CSF 2.0

| Control ID | Function | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| CSF2.GV.OC-01 | GOVERN | Organisational context | `GlassBoxConfig.profile` (`dev`/`production`) + `RuntimeProfile` guard | ⚠️ Partial |
| CSF2.GV.RM-01 | GOVERN | Risk management strategy | `RiskConfig.enforce_threshold`/`deny_level` | ⚠️ Partial |
| CSF2.ID.AM-01 | IDENTIFY | Asset management | `glassbox.domain.catalogue.ActionCatalogueBundle`, `glassbox.domain.tool_registry.ToolRegistryBundle` | ✅ Mechanism |
| CSF2.PR.AA-01 | PROTECT | Identity management | `glassbox.ports.identity.IdentityVerifier` + `VerifiedPrincipal` | ✅ Mechanism |
| CSF2.PR.DS-01 | PROTECT | Data security | MAC-chained evidence; `S3WormAnchorStore` (Object Lock) for sealed anchors | ✅ Mechanism |
| CSF2.DE.AE-01 | DETECT | Anomaly analysis | `glassbox.ports.baseline.BaselineStore` (z-score) | ✅ Mechanism |
| CSF2.DE.CM-01 | DETECT | Continuous monitoring | `glassbox.ports.limits.LimitStore` (distributed, atomic, per-tenant quota) | ✅ Mechanism |
| CSF2.RS.MA-01 | RESPOND | Incident management | Limit-store cooldown; `ToolOutputQuarantinedError` on flagged tool output | ⚠️ Partial |
| CSF2.RC.RP-01 | RECOVER | Recovery planning | `replay()` re-evaluates a past decision against current config | ⚠️ Partial |

## OWASP Agentic Top 10

| Control ID | Risk | Title | GlassBox Mitigation | Status |
|---|---|---|---|---|
| OWASP.A01 | A01 | Prompt Injection | `prompt_injection.scan()` on inbound fields **and** tool output (`ToolOutputQuarantinedError`) | ✅ Mechanism |
| OWASP.A02 | A02 | Insecure Output Handling | Tool-output re-scanning; flagged content never enters evidence, only its digest | ✅ Mechanism |
| OWASP.A03 | A03 | Excessive Agency | `Mandate` (max_consequence, max_exposure, allowed_actions) + `ActionResourceGrant` (resource-scoped) | ✅ Mechanism |
| OWASP.A04 | A04 | Uncontrolled Resource Consumption | Distributed `LimitStore` + per-tenant subject quota + HTTP `admission_control` | ✅ Mechanism |
| OWASP.A05 | A05 | Tool Integrity Failure | `ToolDefinition` digest pinning (`TOOL_DEFINITION_CHANGED` denial) | ✅ Mechanism |
| OWASP.A06 | A06 | Sensitive Data Exposure | Evidence stores only a `result_digest`, never raw tool output | ✅ Mechanism |
| OWASP.A07 | A07 | Cascading Agent Failures | Fail-closed dependency handling (`DenialReason.DEPENDENCY_UNAVAILABLE`) per stage | ✅ Mechanism |
| OWASP.A08 | A08 | Weak Authentication | `IdentityVerifier` + `RawCredential`/`CredentialType`; no shared bearer keys | ✅ Mechanism |
| OWASP.A09 | A09 | Supply Chain Risk | Policy-defined (sanctions/supplier checks are not built in; attach via `PolicyBundle`) | ⛔ Policy-defined |
| OWASP.A10 | A10 | Multi-Agent Trust | `DelegationChain`/`DelegationHop` capability narrowing per hop | ✅ Mechanism |

## NIST 800-207 — Zero Trust Architecture

| Control ID | Tenet | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ZTA.TE-01 | 1 | Never trust, always verify | Every action re-verified through identity → mandate → policy → risk regardless of caller | ✅ Mechanism |
| ZTA.TE-02 | 2 | Least privilege | `Mandate.allowed_actions`/`allowed_resources`, `ActionResourceGrant` | ✅ Mechanism |
| ZTA.TE-03 | 3 | Assume breach | `prompt_injection.scan()` on every untrusted field and every tool result | ✅ Mechanism |
| ZTA.PE-01 | — | Dynamic policy evaluation | `PolicyDecisionPoint.decide()` per decision, never cached across requests | ✅ Mechanism |

## ISO 27001:2022

| Control ID | Annex A | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ISO27K.A5.1 | A.5.1 | Policies for information security | Signed, content-addressed `PolicyBundle` | ✅ Mechanism |
| ISO27K.A5.2 | A.5.2 | Information security roles | `Mandate` (role-scoped authority) + `ApprovalService` (review roles) | ✅ Mechanism |
| ISO27K.A5.36 | A.5.36 | Compliance with policies, rules and standards | `replay()` for policy-change regression; evidence chain for verification | ✅ Mechanism |
| ISO27K.A8.15 | A.8.15 | Logging | MAC-chained `IntentRecord`/`OutcomeRecord` | ✅ Mechanism |
| ISO27K.A8.16 | A.8.16 | Monitoring activities | `BaselineStore`, `LimitStore`, OTel adapter (`glassbox/adapters/outbound/otel/`) | ✅ Mechanism |

## ISO/IEC 42001:2023 — AI Management System

| Control ID | Clause | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| ISO42K.6.1 | 6.1 | Actions to address AI risks | `RiskScore` + `CONSEQUENCE_FLOORS` | ✅ Mechanism |
| ISO42K.8.4 | 8.4 | AI system impact assessment | `replay()` against a candidate policy bundle before rollout | ⚠️ Partial |
| ISO42K.9.1 | 9.1 | Monitoring, measurement, analysis | OTel adapter + evidence store queries | ✅ Mechanism |
| ISO42K.10.1 | 10.1 | Continual improvement | `diff_outcomes()` policy-change regression | ✅ Mechanism |

## SOC 2 Type II

| Control ID | Criteria | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| SOC2.CC6.1 | CC6.1 | Logical access security measures | `IdentityVerifier`, `Mandate` validation, HTTP `admission_control` | ✅ Mechanism |
| SOC2.CC7.2 | CC7.2 | System monitoring | `BaselineStore`, `LimitStore`, OTel metrics | ✅ Mechanism |
| SOC2.CC8.1 | CC8.1 | Change management controls | Policy bundle versioning (content-addressed SHA-256); `replay()` regression | ✅ Mechanism |
| SOC2.CC9.1 | CC9.1 | Risk mitigation activities | `RiskScore` + `ApprovalService` human review for high-risk decisions | ✅ Mechanism |

## HIPAA Security and Privacy Rules

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| HIPAA.164.308a1 | §164.308(a)(1) | Security management process | `DecisionService` + policy bundle + evidence store | ✅ Mechanism |
| HIPAA.164.308a3 | §164.308(a)(3) | Workforce security | `Mandate.allowed_actions`; production-profile guard against dev adapters | ✅ Mechanism |
| HIPAA.164.312b | §164.312(b) | Audit controls | MAC-chained evidence; `verify()` | ✅ Mechanism |
| HIPAA.164.514e | §164.514(e) | Minimum necessary standard | Policy-defined (PHI minimisation rules are content, not runtime code) | ⛔ Policy-defined |

## Colorado AI Act — SB 24-205

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| COL.SB205.8 | §8 | Risk management policy for high-risk AI | `RiskScore` + policy bundle | ✅ Mechanism |
| COL.SB205.9 | §9 | Human review mechanism | `ApprovalService` + `WorkflowEngine.quorum_approve` | ✅ Mechanism |
| COL.SB205.10 | §10 | Disclosure of high-risk AI use | `AuthorizationDecision.rationale` | ✅ Mechanism |

## PCI DSS v4.0

| Control ID | Requirement | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| PCI4.10.3 | Req. 10.3 | Audit logs protected from destruction | MAC-chained, append-only evidence; DB triggers revoke `UPDATE`/`DELETE` | ✅ Mechanism |
| PCI4.6.3 | Req. 6.3 | Security event detection and response | `ToolOutputQuarantinedError`, `LimitStore` cooldown, structured logging | ⚠️ Partial |

## GDPR

| Control ID | Article | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| GDPR.A5 | Art. 5 | Data minimisation and purpose limitation | Evidence stores digests, not raw payloads, for tool output | ⚠️ Partial |
| GDPR.A22 | Art. 22 | Automated individual decision-making | `DecisionEffect.REQUIRE_APPROVAL` gate; policy-defined per jurisdiction | ⛔ Policy-defined |
| GDPR.A33 | Art. 33 | Notification of a personal data breach (72h) | Policy-defined (external alerting integration, not built in) | ⛔ Policy-defined |

## DORA — EU Digital Operational Resilience Act

| Control ID | Article | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| DORA.Art6 | Art. 6 | ICT risk management framework | `RiskScore` + policy bundle | ✅ Mechanism |
| DORA.Art17 | Art. 17 | ICT-related incident management | `LimitStore` cooldown; structured error logging | ⚠️ Partial |
| DORA.Art24 | Art. 24 | Digital operational resilience testing | `replay()` scenario regression | ✅ Mechanism |
| DORA.Art28 | Art. 28 | Third-party ICT risk management | Policy-defined (supplier/sanctions checks are not built in) | ⛔ Policy-defined |

## APRA CPS 234

| Control ID | Paragraph | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| CPS234.15 | Para 15 | Information security controls | `Mandate` scoping + policy bundle | ✅ Mechanism |
| CPS234.36 | Para 36 | Notify regulator of material incidents (72h) | Policy-defined (external alerting integration) | ⛔ Policy-defined |
| CPS234.51 | Para 51 | Information security control testing | `replay()` control regression | ✅ Mechanism |

## FFIEC CAT

| Control ID | Domain | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| FFIEC.D1.CC | Domain 1 | Cyber risk identification and classification | `RiskScore` + `ConsequenceClass` taxonomy | ✅ Mechanism |
| FFIEC.D2.TI | Domain 2 | Threat intelligence | `BaselineStore` z-score drift + `LimitStore` volumetric anomaly | ✅ Mechanism |
| FFIEC.D3.CY | Domain 3 | Cybersecurity controls | Policy bundle + `prompt_injection.scan()` + `Mandate` | ✅ Mechanism |
| FFIEC.D4.EX | Domain 4 | External dependency management | Policy-defined (supplier registry not built in) | ⛔ Policy-defined |

## FDA 21 CFR Part 11 — Electronic Records

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| FDA11.11.10e | §11.10(e) | Audit trails | MAC-chained evidence (SHA-256/HMAC chain, not plain hash) | ✅ Mechanism |
| FDA11.11.10d | §11.10(d) | System access limited to authorised individuals | `IdentityVerifier` + `Mandate` | ✅ Mechanism |
| FDA11.11.50 | §11.50 | Signature manifestations | `WorkflowEngine.quorum_approve` records reviewer identity; not a compliant e-signature manifest | ⚠️ Partial |

## MAS TRM

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| MASTRM.5 | Section 5 | Access control | `Mandate` least-privilege scoping | ✅ Mechanism |
| MASTRM.12 | Section 12 | IT incident management | `LimitStore` cooldown; structured logging | ⚠️ Partial |
| MASTRM.13 | Section 13 | Outsourcing risk management | Policy-defined (supplier/outsourcing checks not built in) | ⛔ Policy-defined |

## NIST SP 800-53 Rev.5

| Control ID | Family | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| 800-53.AU-2 | Audit | Event logging | `IntentRecord`/`OutcomeRecord` per decision | ✅ Mechanism |
| 800-53.AU-9 | Audit | Protection of audit information | MAC-chained, append-only; DB-level `REVOKE UPDATE, TRUNCATE` | ✅ Mechanism |
| 800-53.CM-3 | Config Mgmt | Configuration change control | Content-addressed `PolicyBundle` (any change is a new, signed digest) | ✅ Mechanism |
| 800-53.RA-3 | Risk Assess | Risk assessment | `RiskScore` + `BaselineStore` | ✅ Mechanism |
| 800-53.SI-3 | System Integrity | Malicious code protection | `prompt_injection.scan()` on every request and tool result | ✅ Mechanism |

## ASD Essential Eight (AU)

| Control ID | Mitigation | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| E8.ML2.01 | App Control | Agent/action allow-listing | `ActionCatalogueBundle` + `ToolRegistryBundle` (only declared actions/tools are governable) | ✅ Mechanism |
| E8.ML2.02 | Patching | Version management | `pyproject.toml` pins; git tags/commits | ⚠️ Partial |
| E8.ML2.03 | Audit Logging | Activity logging | MAC-chained evidence | ✅ Mechanism |
| E8.ML3.01 | MFA | Privileged operations | `ApprovalService` dual-control quorum approval | ⚠️ Partial |

## NERC CIP (Power Sector)

| Control ID | Standard | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| NERC.CIP007 | CIP-007 | Systems Security Management | Policy-defined (sector-specific dual-authorisation rules) | ⛔ Policy-defined |
| NERC.CIP010 | CIP-010 | Configuration Change Management | Policy bundle versioning + `replay()` regression | ⚠️ Partial |

## IEC 62443 / ISA 99

| Control ID | Requirement | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| IEC62443.SR1.1 | SR 1.1 | User identification | `IdentityVerifier` | ✅ Mechanism |
| IEC62443.SR2.1 | SR 2.1 | Authorisation enforcement | `PolicyDecisionPoint.decide()` | ✅ Mechanism |
| IEC62443.SR6.1 | SR 6.1 | Audit log accessibility | `EvidenceStore` query surface | ✅ Mechanism |

## SOCI Act 2018 (AU)

| Control ID | Section | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| SOCI.S30BC | s30BC | Positive security obligation | Policy-defined; mandate + evidence provide the enforcement substrate | ⚠️ Partial |
| SOCI.S30BD | s30BD | Incident reporting | Policy-defined (external alerting integration) | ⛔ Policy-defined |

## Purdue Model 2.0

| Control ID | Level | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| PURDUE.L3-L4 | L3–L4 | OT/Enterprise boundary | Policy-defined (zone-specific action scoping via `Mandate.allowed_resources`) | ⚠️ Partial |
| PURDUE.L0-L2 | L0–L2 | OT protection | `ApprovalService` quorum approval for destructive actions | ⚠️ Partial |

## Cyber Security Act 2024 (AU)

| Control ID | Obligation | Title | GlassBox Mapping | Status |
|---|---|---|---|---|
| CSA24.INCIDENT | Mandatory | Incident reporting | Policy-defined (external alerting integration) | ⛔ Policy-defined |

---

## Adding a Custom Control Mapping

There is no `ComplianceCatalogue` database to register controls in — a
mapping is documentation, not runtime state. Add a row to the relevant table
above (or a new table for a framework not yet listed), citing the real
mechanism or policy hook it relies on, and cite the test that proves the
mechanism works if one exists in [CLAIMS.md](../CLAIMS.md).

---

## Known Limitations

- **Rows marked "Policy-defined"** describe a seam, not a shipped feature:
  attach the actual rule as a signed `PolicyBundle` entry, evaluated the same
  way as every other decision, and evidenced identically.
- **Outcome records are not yet MAC-chained** — only `evidence_intent` rows
  participate in the hash chain today; `evidence_outcome` rows are an
  accepted gap (see [CLAIMS.md](../CLAIMS.md)). An auditor relying on
  outcome-record tamper-evidence specifically (e.g. FDA 21 CFR Part 11
  §11.50) should treat that row as partial until this is closed.
- **GDPR Art. 22 / jurisdiction-scoped gates** require the caller to pass
  jurisdiction context through a policy rule; GlassBox does not infer
  jurisdiction from a request.

---

## See Also

- **[GLOSSARY.md](../GLOSSARY.md)** — Definitions of governance terms
- **[CLAIMS.md](../CLAIMS.md)** — Every guarantee, cited to the test that proves it
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — How the decision pipeline produces evidence
- **[COMPLIANCE/README.md](README.md)** — The evidence model this crosswalk maps onto
- **[DEPLOYMENT.md](../DEPLOYMENT.md)** — Compliance considerations for production deployment

---

*GlassBox · Apache 2.0*
