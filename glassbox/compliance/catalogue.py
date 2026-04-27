"""
GlassBox — Compliance Control Catalogue  (v1.2.0)
==================================================
Database-driven compliance framework implementation.

Every compliance standard, control, and obligation is stored as
structured data in SQLite — NOT hardcoded. This allows:
  - Controls to be added/updated without code changes
  - Evidence to be collected against specific controls
  - Compliance posture to be queried and reported
  - Gap analysis between implemented controls and required controls

Standards implemented (24 frameworks, 97 controls):
  NIST CSF 2.0          Core Functions: Govern, Identify, Protect, Detect, Respond, Recover
  NIST AI RMF           AI-specific: Govern, Map, Measure, Manage
  EU AI Act             Articles 9, 11, 12, 13, 14, 15, 16, 17
  ISO/IEC 42001:2023    AI Management System
  ISO 27001:2022        Information Security Management
  OWASP Agentic Top 10  A01–A10 2026
  NIST 800-207 (ZTA)    Zero Trust Architecture
  ASD Essential Eight   Maturity Model controls
  SOCI Act 2018         Critical Infrastructure obligations
  Cyber Security Act    Incident notification + reporting
  NERC CIP              Power sector cybersecurity
  IEC 62443 / ISA 99    Industrial Automation and Control Systems
  Purdue Model 2.0      Network architecture segmentation
  SOC 2 Type II         Trust Services Criteria
  HIPAA                 Health Insurance Portability and Accountability
  Colorado AI Act       SB 24-205 High-Risk AI Systems
  PCI DSS v4.0          Payment Card Industry Data Security Standard
  GDPR                  EU General Data Protection Regulation
  DORA                  EU Digital Operational Resilience Act
  APRA CPS 234          Australian Prudential Standard for Information Security
  FFIEC CAT             Financial Institution Cybersecurity Assessment Tool
  FDA 21 CFR Part 11    Electronic Records for Clinical/Pharma AI
  MAS TRM               Monetary Authority of Singapore Tech Risk Guidelines
  NIST SP 800-53 Rev.5  Security and Privacy Controls for Federal Systems

Each control has:
  - control_id:     Unique ID within the standard (e.g. "CSF2.GV.PO-01")
  - framework:      Standard identifier
  - category:       Functional grouping
  - title:          Short description
  - description:    Full requirement statement
  - glassbox_mapping: Which GlassBox component satisfies this control
  - implementation_status: implemented | partial | gap | not_applicable
  - evidence_query: SQL-compatible filter for evidence collection

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Control catalogue data ────────────────────────────────────────────────────

CONTROL_CATALOGUE: List[Dict[str, Any]] = [

    # ── NIST CSF 2.0 ─────────────────────────────────────────────────────────
    {"control_id":"CSF2.GV.OC-01","framework":"NIST CSF 2.0","category":"GOVERN - Organizational Context",
     "title":"Organisational mission and objectives for cybersecurity",
     "description":"Organisational cybersecurity risk management objectives are established and understood.",
     "glassbox_mapping":"GovernancePipeline(environment=), AgentContract",
     "implementation_status":"partial"},
    {"control_id":"CSF2.GV.RM-01","framework":"NIST CSF 2.0","category":"GOVERN - Risk Management",
     "title":"Risk management strategy",
     "description":"Risk management objectives, risk appetite, and risk tolerance are established.",
     "glassbox_mapping":"RiskEvaluator, ComplianceCatalogue.risk_tolerance_config",
     "implementation_status":"partial"},
    {"control_id":"CSF2.ID.AM-01","framework":"NIST CSF 2.0","category":"IDENTIFY - Asset Management",
     "title":"AI agent inventory",
     "description":"Assets (including AI agents) that affect cybersecurity are identified and managed.",
     "glassbox_mapping":"AgentContract registry, AuditRepository agent inventory",
     "implementation_status":"implemented"},
    {"control_id":"CSF2.PR.AA-01","framework":"NIST CSF 2.0","category":"PROTECT - Identity Management",
     "title":"Agent identity validation",
     "description":"Identities and credentials are managed for authorized agents.",
     "glassbox_mapping":"AgentContract validation, validate_agent_id()",
     "implementation_status":"implemented"},
    {"control_id":"CSF2.PR.DS-01","framework":"NIST CSF 2.0","category":"PROTECT - Data Security",
     "title":"Data at rest protection",
     "description":"Data-at-rest is protected against unauthorized access.",
     "glassbox_mapping":"AuditLogger (immutable), SQLiteAuditRepository",
     "implementation_status":"partial"},
    {"control_id":"CSF2.DE.AE-01","framework":"NIST CSF 2.0","category":"DETECT - Adverse Event Analysis",
     "title":"Anomaly detection",
     "description":"A baseline of network operations and expected data flows is established.",
     "glassbox_mapping":"AnomalyDetector Z-score baseline",
     "implementation_status":"implemented"},
    {"control_id":"CSF2.DE.CM-01","framework":"NIST CSF 2.0","category":"DETECT - Continuous Monitoring",
     "title":"Real-time monitoring",
     "description":"Assets are monitored to find anomalies, indicators of compromise, and other risks.",
     "glassbox_mapping":"VelocityBreaker, AnomalyDetector, EventBus",
     "implementation_status":"implemented"},
    {"control_id":"CSF2.RS.MA-01","framework":"NIST CSF 2.0","category":"RESPOND - Incident Management",
     "title":"Incident response",
     "description":"Incidents are contained and eradicated.",
     "glassbox_mapping":"VelocityBreaker.cooldown, CircuitBreakerResult",
     "implementation_status":"partial"},
    {"control_id":"CSF2.RC.RP-01","framework":"NIST CSF 2.0","category":"RECOVER - Recovery Planning",
     "title":"Governance recovery",
     "description":"Recovery plans are executed and maintained.",
     "glassbox_mapping":"DecisionReplay, WorkflowEngine approval",
     "implementation_status":"partial"},

    # ── NIST AI RMF ──────────────────────────────────────────────────────────
    {"control_id":"AIRM.GV.01","framework":"NIST AI RMF","category":"GOVERN",
     "title":"AI risk management policies",
     "description":"Policies and procedures for AI risk management are established.",
     "glassbox_mapping":"PolicyEngine, ComplianceCatalogue, PolicyRepository",
     "implementation_status":"implemented"},
    {"control_id":"AIRM.MP.01","framework":"NIST AI RMF","category":"MAP",
     "title":"AI risk identification",
     "description":"AI system risks are identified and categorised.",
     "glassbox_mapping":"DecisionType taxonomy, RiskEvaluator factor extraction",
     "implementation_status":"implemented"},
    {"control_id":"AIRM.ME.01","framework":"NIST AI RMF","category":"MEASURE",
     "title":"AI risk measurement",
     "description":"AI risks are analysed and assessed.",
     "glassbox_mapping":"RiskEvaluator (0-100 composite score), AnomalyDetector",
     "implementation_status":"implemented"},
    {"control_id":"AIRM.MG.01","framework":"NIST AI RMF","category":"MANAGE",
     "title":"AI risk treatment",
     "description":"AI risks are prioritised and addressed.",
     "glassbox_mapping":"Disposition (AUTO_EXECUTE/HUMAN_REVIEW/BLOCK), WorkflowEngine",
     "implementation_status":"implemented"},
    {"control_id":"AIRM.MG.02","framework":"NIST AI RMF","category":"MANAGE",
     "title":"AI decision audit trail",
     "description":"A complete audit trail of AI system decisions is maintained.",
     "glassbox_mapping":"AuditLogger, SQLiteAuditRepository, ExecutionTrace",
     "implementation_status":"implemented"},

    # ── EU AI Act ─────────────────────────────────────────────────────────────
    {"control_id":"EUAI.A9","framework":"EU AI Act","category":"Risk Management",
     "title":"Article 9 — Risk management system",
     "description":"A risk management system shall be established, implemented, documented and maintained for high-risk AI systems.",
     "glassbox_mapping":"RiskEvaluator, PolicyEngine, ComplianceCatalogue",
     "implementation_status":"implemented"},
    {"control_id":"EUAI.A12","framework":"EU AI Act","category":"Record-keeping",
     "title":"Article 12 — Record-keeping",
     "description":"High-risk AI systems shall automatically log events throughout their lifetime.",
     "glassbox_mapping":"AuditLogger (immutable append-only), SQLiteAuditRepository",
     "implementation_status":"implemented"},
    {"control_id":"EUAI.A13","framework":"EU AI Act","category":"Transparency",
     "title":"Article 13 — Transparency and provision of information",
     "description":"High-risk AI systems shall be designed with sufficient transparency to interpret output.",
     "glassbox_mapping":"ExecutionTrace, PolicyEvaluation.message, risk_factors",
     "implementation_status":"implemented"},
    {"control_id":"EUAI.A14","framework":"EU AI Act","category":"Human Oversight",
     "title":"Article 14 — Human oversight",
     "description":"High-risk AI systems shall allow effective oversight by natural persons.",
     "glassbox_mapping":"WorkflowEngine, HUMAN_REVIEW disposition, AgentContract",
     "implementation_status":"implemented"},
    {"control_id":"EUAI.A16","framework":"EU AI Act","category":"Obligations",
     "title":"Article 16 — Obligations of providers",
     "description":"Providers shall establish quality management systems and register systems.",
     "glassbox_mapping":"PolicyRepository versioning, ComplianceCatalogue",
     "implementation_status":"partial"},
    {"control_id":"EUAI.A17","framework":"EU AI Act","category":"Quality Management",
     "title":"Article 17 — Quality management system",
     "description":"Providers of high-risk AI systems shall put a quality management system in place.",
     "glassbox_mapping":"ComplianceCatalogue, DecisionReplay regression testing",
     "implementation_status":"partial"},

    # ── OWASP Agentic Top 10 2026 ─────────────────────────────────────────────
    {"control_id":"OWASP.A01","framework":"OWASP Agentic Top 10","category":"A01 Prompt Injection",
     "title":"Prompt injection prevention",
     "description":"Malicious instructions injected via prompts that cause agents to take unintended actions.",
     "glassbox_mapping":"PayloadSanitizer, AgentContract permitted_types",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A02","framework":"OWASP Agentic Top 10","category":"A02 Insecure Output",
     "title":"Insecure output handling",
     "description":"Unvalidated output used by downstream systems without appropriate security checks.",
     "glassbox_mapping":"SchemaValidator, PayloadSanitizer.check()",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A03","framework":"OWASP Agentic Top 10","category":"A03 Excessive Agency",
     "title":"Excessive agency prevention",
     "description":"Agents are given more capabilities than needed for their function.",
     "glassbox_mapping":"AgentContract (permitted_types, max_amount, max_delegation_depth)",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A04","framework":"OWASP Agentic Top 10","category":"A04 Resource Consumption",
     "title":"Uncontrolled resource consumption",
     "description":"Agents exhaust compute, API, financial, or operational resources.",
     "glassbox_mapping":"VelocityBreaker per-agent + ecosystem, AGG-001 fleet budget",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A05","framework":"OWASP Agentic Top 10","category":"A05 Tool Integrity",
     "title":"Tool and resource integrity",
     "description":"Tools used by agents are compromised, injected, or manipulated.",
     "glassbox_mapping":"AnomalyDetector, PayloadSanitizer",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A06","framework":"OWASP Agentic Top 10","category":"A06 Sensitive Data",
     "title":"Sensitive data exposure",
     "description":"Agents inadvertently expose or transmit sensitive information.",
     "glassbox_mapping":"AuditLogger.include_payload=False (PII protection)",
     "implementation_status":"partial"},
    {"control_id":"OWASP.A07","framework":"OWASP Agentic Top 10","category":"A07 Cascading Failures",
     "title":"Cascading agent failures",
     "description":"Failures in one agent propagate and amplify through dependent agents.",
     "glassbox_mapping":"AgentOrchestrator chain abort, VelocityBreaker cooldown",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A08","framework":"OWASP Agentic Top 10","category":"A08 Authentication",
     "title":"Weak authentication and authorisation",
     "description":"Insufficient identity verification for agent operations.",
     "glassbox_mapping":"AgentContract, validate_agent_id(), SECURITY-001",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A09","framework":"OWASP Agentic Top 10","category":"A09 Supply Chain",
     "title":"Supply chain risks",
     "description":"Compromised components in the AI agent supply chain.",
     "glassbox_mapping":"PROC-002 supplier registry, PROC-003 category controls",
     "implementation_status":"implemented"},
    {"control_id":"OWASP.A10","framework":"OWASP Agentic Top 10","category":"A10 Multi-Agent Trust",
     "title":"Multi-agent trust boundary violations",
     "description":"Inappropriate trust between agents in a multi-agent system.",
     "glassbox_mapping":"AgentContract delegation_allowed, chain depth limits",
     "implementation_status":"implemented"},

    # ── NIST 800-207 Zero Trust ───────────────────────────────────────────────
    {"control_id":"ZTA.TE-01","framework":"NIST 800-207","category":"Tenets",
     "title":"Continuous verification — never trust, always verify",
     "description":"All data sources and computing services are considered resources. Every request is authenticated and authorised.",
     "glassbox_mapping":"Per-decision governance: every AI decision is evaluated independently regardless of source",
     "implementation_status":"implemented"},
    {"control_id":"ZTA.TE-02","framework":"NIST 800-207","category":"Tenets",
     "title":"Least privilege access",
     "description":"Access to resources is limited to minimum necessary.",
     "glassbox_mapping":"AgentContract permitted_types, max_amount",
     "implementation_status":"implemented"},
    {"control_id":"ZTA.TE-03","framework":"NIST 800-207","category":"Tenets",
     "title":"Assume breach posture",
     "description":"Resources are designed assuming adversaries are already present.",
     "glassbox_mapping":"PayloadSanitizer (injection on every request), AnomalyDetector",
     "implementation_status":"implemented"},
    {"control_id":"ZTA.PE-01","framework":"NIST 800-207","category":"Policy Engine",
     "title":"Dynamic policy evaluation",
     "description":"Access decisions are made dynamically based on real-time attributes.",
     "glassbox_mapping":"PolicyEngine.evaluate() per-decision, RiskEvaluator dynamic scoring",
     "implementation_status":"implemented"},

    # ── ASD Essential Eight ───────────────────────────────────────────────────
    {"control_id":"E8.ML2.01","framework":"ASD Essential Eight","category":"Application Control",
     "title":"Application control — agent whitelisting",
     "description":"Application control prevents execution of unapproved/malicious programs.",
     "glassbox_mapping":"AgentContract (whitelist of permitted decision types per agent)",
     "implementation_status":"implemented"},
    {"control_id":"E8.ML2.02","framework":"ASD Essential Eight","category":"Patch Applications",
     "title":"Patching and versioning",
     "description":"Security vulnerabilities in applications are patched.",
     "glassbox_mapping":"pyproject.toml version management, CHANGELOG.md",
     "implementation_status":"partial"},
    {"control_id":"E8.ML3.01","framework":"ASD Essential Eight","category":"Multi-Factor Auth",
     "title":"MFA for privileged operations",
     "description":"Multi-factor authentication is used for privileged access.",
     "glassbox_mapping":"AgentContract + WorkflowEngine dual-approval pattern",
     "implementation_status":"partial"},
    {"control_id":"E8.ML2.03","framework":"ASD Essential Eight","category":"Audit Logging",
     "title":"Logging of agent activities",
     "description":"Logs of computer and user activities are collected and protected.",
     "glassbox_mapping":"AuditLogger (immutable), SQLiteAuditRepository, ExecutionTrace",
     "implementation_status":"implemented"},

    # ── NERC CIP ─────────────────────────────────────────────────────────────
    {"control_id":"NERC.CIP007","framework":"NERC CIP","category":"Systems Security Management",
     "title":"CIP-007 — Systems Security Management",
     "description":"Manage cybersecurity risks to BES (Bulk Electric System) cyber systems.",
     "glassbox_mapping":"IT_OPS decision type, IT-OPS-003 service criticality gate (see also examples/industry_examples.py for GRID-001 domain policy template)",
     "implementation_status":"partial"},
    {"control_id":"NERC.CIP010","framework":"NERC CIP","category":"Configuration Management",
     "title":"CIP-010 — Configuration Change Management",
     "description":"Prevent and detect unauthorised changes to BES cyber systems.",
     "glassbox_mapping":"IT-OPS-002 maintenance window policy, IT-OPS-004 change log requirement, DecisionReplay regression testing",
     "implementation_status":"partial"},

    # ── IEC 62443 / ISA 99 ────────────────────────────────────────────────────
    {"control_id":"IEC62443.SR1.1","framework":"IEC 62443","category":"Security Requirements",
     "title":"SR 1.1 — Human user identification and authentication",
     "description":"All human users shall be identified and authenticated.",
     "glassbox_mapping":"AgentContract agent identity, validate_agent_id()",
     "implementation_status":"implemented"},
    {"control_id":"IEC62443.SR2.1","framework":"IEC 62443","category":"Security Requirements",
     "title":"SR 2.1 — Authorisation enforcement",
     "description":"All requests for access to system resources shall be authorised.",
     "glassbox_mapping":"AgentContract.permitted_types, PolicyEngine evaluation",
     "implementation_status":"implemented"},
    {"control_id":"IEC62443.SR6.1","framework":"IEC 62443","category":"Audit",
     "title":"SR 6.1 — Audit log accessibility",
     "description":"The IACS shall provide a capability to read all audit logs.",
     "glassbox_mapping":"AuditRepository.query(), SQLiteAuditRepository indexed queries",
     "implementation_status":"implemented"},

    # ── SOCI Act 2018 ─────────────────────────────────────────────────────────
    {"control_id":"SOCI.S30BC","framework":"SOCI Act 2018","category":"Positive Security Obligation",
     "title":"s30BC — Positive security obligation",
     "description":"Responsible entities must do all they can to manage the security risk of their critical infrastructure assets.",
     "glassbox_mapping":"ComplianceCatalogue risk posture, GovernancePipeline mandatory controls",
     "implementation_status":"partial"},
    {"control_id":"SOCI.S30BD","framework":"SOCI Act 2018","category":"Incident Notification",
     "title":"s30BD — Incident reporting obligation",
     "description":"Mandatory reporting of significant cyber incidents to government.",
     "glassbox_mapping":"EventBus SecurityViolation event → WebhookEventHandler → SOCI portal",
     "implementation_status":"partial"},

    # ── Cyber Security Act 2024 (AU) ──────────────────────────────────────────
    {"control_id":"CSA24.INCIDENT","framework":"Cyber Security Act 2024","category":"Incident Reporting",
     "title":"Mandatory incident reporting",
     "description":"Entities must report ransomware payments and significant cyber incidents.",
     "glassbox_mapping":"EventBus SecurityViolation + CircuitBreakerTripped events",
     "implementation_status":"partial"},

    # ── Purdue Model 2.0 ─────────────────────────────────────────────────────
    {"control_id":"PURDUE.L3-L4","framework":"Purdue Model 2.0","category":"Zone Separation",
     "title":"Level 3–4 boundary control",
     "description":"Manufacturing operations zone separated from enterprise zone.",
     "glassbox_mapping":"AgentContract zone-specific permitted_types, IT-OPS-002 maintenance window, IT_OPS with change_window_approved",
     "implementation_status":"partial"},
    {"control_id":"PURDUE.L0-L2","framework":"Purdue Model 2.0","category":"OT Zone",
     "title":"Level 0–2 OT protection",
     "description":"Physical process, field devices, and control systems are protected.",
     "glassbox_mapping":"IT-OPS-004 destructive action change log, IT-OPS-003 service criticality gate, dual-authorisation via WorkflowEngine quorum_approve",
     "implementation_status":"partial"},

    # ── ISO 27001:2022 ─────────────────────────────────────────────────────────
    {"control_id":"ISO27K.A5.1","framework":"ISO 27001:2022","category":"Organisational Controls",
     "title":"Policies for information security",
     "description":"Information security policy and topic-specific policies shall be defined, approved, published, communicated, and reviewed.",
     "glassbox_mapping":"PolicyEngine (policy-as-code), PolicyHotReloader (policy lifecycle)",
     "implementation_status":"implemented"},
    {"control_id":"ISO27K.A5.2","framework":"ISO 27001:2022","category":"Organisational Controls",
     "title":"Information security roles and responsibilities",
     "description":"Information security responsibilities shall be defined and allocated.",
     "glassbox_mapping":"AgentContract (role-based decision authority), WorkflowEngine (review roles)",
     "implementation_status":"implemented"},
    {"control_id":"ISO27K.A8.15","framework":"ISO 27001:2022","category":"Technological Controls",
     "title":"Logging",
     "description":"Logs that record activities, exceptions, faults and other relevant events shall be produced, stored, protected and analysed.",
     "glassbox_mapping":"AuditLogger (immutable decision log), SQLiteAuditRepository",
     "implementation_status":"implemented"},
    {"control_id":"ISO27K.A8.16","framework":"ISO 27001:2022","category":"Technological Controls",
     "title":"Monitoring activities",
     "description":"Networks, systems and applications shall be monitored for anomalous behaviour and appropriate actions taken.",
     "glassbox_mapping":"AnomalyDetector, VelocityBreaker, OtelExporter",
     "implementation_status":"implemented"},
    {"control_id":"ISO27K.A5.36","framework":"ISO 27001:2022","category":"Organisational Controls",
     "title":"Compliance with policies, rules and standards",
     "description":"Compliance with the organisation's information security policy shall be regularly reviewed.",
     "glassbox_mapping":"ComplianceCatalogue, ComplianceReporter, DecisionReplay",
     "implementation_status":"implemented"},

    # ── SOC 2 Type II ──────────────────────────────────────────────────────────
    {"control_id":"SOC2.CC6.1","framework":"SOC 2 Type II","category":"Logical and Physical Access",
     "title":"Logical access security measures",
     "description":"Logical access security software, infrastructure, and architectures are implemented to protect against threats from outside sources.",
     "glassbox_mapping":"AgentContract validation, PayloadSanitizer, validate_agent_id",
     "implementation_status":"implemented"},
    {"control_id":"SOC2.CC7.2","framework":"SOC 2 Type II","category":"System Operations",
     "title":"System monitoring",
     "description":"The entity monitors system components and the operation of those components for anomalies.",
     "glassbox_mapping":"AnomalyDetector, VelocityBreaker, OtelExporter metrics",
     "implementation_status":"implemented"},
    {"control_id":"SOC2.CC8.1","framework":"SOC 2 Type II","category":"Change Management",
     "title":"Change management controls",
     "description":"Changes to infrastructure, data, software and procedures are authorised, documented, tested, approved, and deployed.",
     "glassbox_mapping":"PolicyHotReloader, PolicySimulator (pre-deployment impact), IT-OPS-004 change log requirement",
     "implementation_status":"implemented"},
    {"control_id":"SOC2.CC9.1","framework":"SOC 2 Type II","category":"Risk Mitigation",
     "title":"Risk mitigation activities",
     "description":"The entity identifies, selects, and develops risk mitigation activities for risks arising from potential business disruptions.",
     "glassbox_mapping":"RiskEvaluator, WorkflowEngine (human review for high-risk decisions)",
     "implementation_status":"implemented"},

    # ── HIPAA Security Rule ────────────────────────────────────────────────────
    {"control_id":"HIPAA.164.308a1","framework":"HIPAA","category":"Administrative Safeguards",
     "title":"Security management process",
     "description":"Implement policies and procedures to prevent, detect, contain, and correct security violations.",
     "glassbox_mapping":"GovernancePipeline, PolicyEngine, AuditLogger",
     "implementation_status":"implemented"},
    {"control_id":"HIPAA.164.312b","framework":"HIPAA","category":"Technical Safeguards",
     "title":"Audit controls",
     "description":"Implement hardware, software, and/or procedural mechanisms to record and examine activity in information systems.",
     "glassbox_mapping":"AuditLogger (immutable), SQLiteAuditRepository, ExecutionTrace",
     "implementation_status":"implemented"},
    {"control_id":"HIPAA.164.514e","framework":"HIPAA","category":"Privacy Rule",
     "title":"Minimum necessary standard",
     "description":"PHI used or disclosed shall be limited to the minimum necessary to accomplish the intended purpose.",
     "glassbox_mapping":"CLIN-001/002 clinical policies, GEN-001 PII detection, DecisionContext.patient_id",
     "implementation_status":"partial"},
    {"control_id":"HIPAA.164.308a3","framework":"HIPAA","category":"Administrative Safeguards",
     "title":"Workforce security",
     "description":"Implement policies to ensure workforce members access ePHI only as needed.",
     "glassbox_mapping":"AgentContract (permitted_types), SECURITY-001 (production override forbidden)",
     "implementation_status":"implemented"},

    # ── ISO/IEC 42001:2023 AI Management System ────────────────────────────────
    {"control_id":"ISO42K.6.1","framework":"ISO/IEC 42001:2023","category":"Planning",
     "title":"Actions to address AI risks and opportunities",
     "description":"Actions to address risks and opportunities related to AI shall be planned.",
     "glassbox_mapping":"RiskEvaluator, ComplianceCatalogue risk tolerance config",
     "implementation_status":"implemented"},
    {"control_id":"ISO42K.8.4","framework":"ISO/IEC 42001:2023","category":"Operation",
     "title":"AI system impact assessment",
     "description":"The organisation shall conduct and document an assessment of the impacts of the AI system.",
     "glassbox_mapping":"PolicySimulator (pre-deployment impact), ComplianceReporter",
     "implementation_status":"implemented"},
    {"control_id":"ISO42K.9.1","framework":"ISO/IEC 42001:2023","category":"Performance Evaluation",
     "title":"Monitoring, measurement, analysis and evaluation",
     "description":"The organisation shall monitor, measure, analyse and evaluate AI system performance.",
     "glassbox_mapping":"OtelExporter, AuditLogger.summary_stats, ComplianceReporter.framework_coverage",
     "implementation_status":"implemented"},
    {"control_id":"ISO42K.10.1","framework":"ISO/IEC 42001:2023","category":"Improvement",
     "title":"Continual improvement",
     "description":"The organisation shall continually improve the suitability, adequacy and effectiveness of the AI management system.",
     "glassbox_mapping":"DecisionReplay (policy regression), PolicySimulator, ComplianceCatalogue.gap_analysis",
     "implementation_status":"implemented"},

    # ── Colorado AI Act SB 24-205 ──────────────────────────────────────────────
    {"control_id":"COL.SB205.8","framework":"Colorado AI Act","category":"High-Risk AI Systems",
     "title":"Risk management policy for high-risk AI",
     "description":"Developers and deployers of high-risk AI systems must implement risk management policies.",
     "glassbox_mapping":"GovernancePipeline, RiskEvaluator, PolicyEngine",
     "implementation_status":"implemented"},
    {"control_id":"COL.SB205.9","framework":"Colorado AI Act","category":"Human Oversight",
     "title":"Human review mechanism",
     "description":"Deployers must provide a mechanism for consumers to appeal algorithmic decisions.",
     "glassbox_mapping":"WorkflowEngine (human review queue), quorum_approve, appeal workflow",
     "implementation_status":"implemented"},
    {"control_id":"COL.SB205.10","framework":"Colorado AI Act","category":"Transparency",
     "title":"Disclosure of high-risk AI use",
     "description":"Consumers must be notified when a high-risk AI system is used to make a consequential decision.",
     "glassbox_mapping":"DecisionExplainer (Art.13-equivalent), explanation field in DecisionResponse",
     "implementation_status":"implemented"},

    # ── PCI DSS v4.0 ───────────────────────────────────────────────────────────
    {"control_id":"PCI4.10.3","framework":"PCI DSS v4.0","category":"Logging and Monitoring",
     "title":"Audit logs are protected from destruction",
     "description":"Audit log files are protected to prevent modifications by individuals.",
     "glassbox_mapping":"AuditLogger (append-only, immutable), SQLiteAuditRepository",
     "implementation_status":"implemented"},
    {"control_id":"PCI4.6.3","framework":"PCI DSS v4.0","category":"Security Systems",
     "title":"Security event detection and response",
     "description":"Security events are detected, analysed, and responded to.",
     "glassbox_mapping":"PayloadSanitizer, EventBus SecurityViolation events, VelocityBreaker",
     "implementation_status":"implemented"},

    # ── EU AI Act — additional articles ───────────────────────────────────────
    {"control_id":"EUAI.A11","framework":"EU AI Act","category":"Technical Documentation",
     "title":"Article 11 — Technical documentation",
     "description":"Providers of high-risk AI systems shall draw up technical documentation demonstrating compliance before placing the system on the market.",
     "glassbox_mapping":"ComplianceCatalogue.posture_summary(), ComplianceReporter (framework coverage report)",
     "implementation_status":"partial"},
    {"control_id":"EUAI.A15","framework":"EU AI Act","category":"Accuracy and Robustness",
     "title":"Article 15 — Accuracy, robustness and cybersecurity",
     "description":"High-risk AI systems shall be designed with appropriate levels of accuracy, robustness, and cybersecurity throughout their lifecycle.",
     "glassbox_mapping":"AI-001 confidence threshold, AnomalyDetector (drift detection), PayloadSanitizer (adversarial input)",
     "implementation_status":"partial"},

    # ── GDPR ──────────────────────────────────────────────────────────────────
    {"control_id":"GDPR.A5","framework":"GDPR","category":"Principles",
     "title":"Article 5 — Data minimisation and purpose limitation",
     "description":"Personal data shall be adequate, relevant and limited to what is necessary in relation to the purposes for which it are processed.",
     "glassbox_mapping":"AuditLogger.include_payload=False (PII exclusion), GEN-001 PII detection, COMPLIANCE-001",
     "implementation_status":"partial"},
    {"control_id":"GDPR.A22","framework":"GDPR","category":"Automated Decision-Making",
     "title":"Article 22 — Automated individual decision-making",
     "description":"Data subjects have the right not to be subject to a decision based solely on automated processing which significantly affects them.",
     "glassbox_mapping":"GEN-002 EU Automated Decision Gate (forces human_review_available=True for EU subjects)",
     "implementation_status":"implemented"},
    {"control_id":"GDPR.A33","framework":"GDPR","category":"Breach Notification",
     "title":"Article 33 — Notification of a personal data breach",
     "description":"Supervisory authority must be notified within 72 hours of becoming aware of a personal data breach.",
     "glassbox_mapping":"COMPLIANCE-003 breach notification policy, EventBus SecurityViolation events",
     "implementation_status":"implemented"},

    # ── DORA (EU) ─────────────────────────────────────────────────────────────
    {"control_id":"DORA.Art6","framework":"DORA","category":"ICT Risk Management",
     "title":"Article 6 — ICT risk management framework",
     "description":"Financial entities shall have in place a sound, comprehensive and well-documented ICT risk management framework.",
     "glassbox_mapping":"RiskEvaluator (0-100 composite score), PolicyEngine, ComplianceCatalogue risk posture",
     "implementation_status":"implemented"},
    {"control_id":"DORA.Art17","framework":"DORA","category":"ICT Incident Management",
     "title":"Article 17 — ICT-related incident management process",
     "description":"Financial entities shall define, establish and implement an ICT-related incident management process.",
     "glassbox_mapping":"EventBus SecurityViolation + CircuitBreakerTripped events, VelocityBreaker cooldown",
     "implementation_status":"partial"},
    {"control_id":"DORA.Art24","framework":"DORA","category":"Digital Operational Resilience Testing",
     "title":"Article 24 — General requirements for digital operational resilience testing",
     "description":"Financial entities shall maintain and review a sound and comprehensive digital operational resilience testing programme.",
     "glassbox_mapping":"DecisionReplay (scenario regression), PolicySimulator (pre-deployment impact testing)",
     "implementation_status":"implemented"},
    {"control_id":"DORA.Art28","framework":"DORA","category":"Third-Party Risk",
     "title":"Article 28 — General principles of sound management of ICT third-party risk",
     "description":"Financial entities shall manage ICT third-party risk as an integral component of ICT risk.",
     "glassbox_mapping":"PROC-002 supplier known check, PROC-006 sanctions and debarment, PROC-003 category risk",
     "implementation_status":"implemented"},

    # ── APRA CPS 234 ─────────────────────────────────────────────────────────
    {"control_id":"CPS234.15","framework":"APRA CPS 234","category":"Information Security Controls",
     "title":"Para 15 — Implement information security controls",
     "description":"An APRA-regulated entity must implement information security controls to protect its information assets commensurate with the criticality and sensitivity of those information assets.",
     "glassbox_mapping":"AgentContract permitted_types (asset classification), PolicyEngine (control enforcement)",
     "implementation_status":"implemented"},
    {"control_id":"CPS234.36","framework":"APRA CPS 234","category":"Incident Management",
     "title":"Para 36 — Notify APRA of material information security incidents",
     "description":"An APRA-regulated entity must notify APRA as soon as possible and no later than 72 hours after becoming aware of a material information security incident.",
     "glassbox_mapping":"EventBus SecurityViolation events, COMPLIANCE-003 breach notification, WebhookEventHandler",
     "implementation_status":"partial"},
    {"control_id":"CPS234.51","framework":"APRA CPS 234","category":"Audit and Review",
     "title":"Para 51 — Information security control testing",
     "description":"An APRA-regulated entity must test information security controls through a systematic testing programme.",
     "glassbox_mapping":"DecisionReplay (control regression testing), PolicySimulator (impact analysis)",
     "implementation_status":"implemented"},

    # ── FFIEC CAT ─────────────────────────────────────────────────────────────
    {"control_id":"FFIEC.D1.CC","framework":"FFIEC CAT","category":"Cyber Risk Management",
     "title":"Domain 1 — Cyber risk identification and classification",
     "description":"Financial institutions should identify and classify cybersecurity risks including AI-driven decision risks.",
     "glassbox_mapping":"RiskEvaluator (composite scoring), DecisionType taxonomy, ComplianceCatalogue risk posture",
     "implementation_status":"implemented"},
    {"control_id":"FFIEC.D2.TI","framework":"FFIEC CAT","category":"Threat Intelligence",
     "title":"Domain 2 — Threat intelligence and collaboration",
     "description":"Cyber threat intelligence is gathered from multiple sources and used to inform the institution's defences.",
     "glassbox_mapping":"AnomalyDetector (Z-score baseline drift), VelocityBreaker (volumetric anomaly)",
     "implementation_status":"implemented"},
    {"control_id":"FFIEC.D3.CY","framework":"FFIEC CAT","category":"Cybersecurity Controls",
     "title":"Domain 3 — Cybersecurity controls",
     "description":"Preventive, detective, and corrective controls are in place to protect information assets.",
     "glassbox_mapping":"PolicyEngine (35 built-in controls), PayloadSanitizer, AgentContract access controls",
     "implementation_status":"implemented"},
    {"control_id":"FFIEC.D4.EX","framework":"FFIEC CAT","category":"External Dependency Management",
     "title":"Domain 4 — External dependency management",
     "description":"Connections, relationships, and dependencies with external entities are managed and monitored.",
     "glassbox_mapping":"PROC-002/006 supplier registry and sanctions, MCP Gateway integration controls",
     "implementation_status":"partial"},

    # ── FDA 21 CFR Part 11 ────────────────────────────────────────────────────
    {"control_id":"FDA11.11.10e","framework":"FDA 21 CFR Part 11","category":"Electronic Records",
     "title":"§11.10(e) — Audit trails",
     "description":"Use of secure, computer-generated, time-stamped audit trails to independently record the date and time of operator entries and actions.",
     "glassbox_mapping":"AuditLogger (immutable SHA-256 hash chain), TamperEvidentAuditLogger, SQLiteAuditRepository",
     "implementation_status":"implemented"},
    {"control_id":"FDA11.11.10d","framework":"FDA 21 CFR Part 11","category":"Access Controls",
     "title":"§11.10(d) — System access limited to authorised individuals",
     "description":"Limiting system access to authorised individuals.",
     "glassbox_mapping":"AgentContract (agent identity and permitted_types), validate_agent_id()",
     "implementation_status":"implemented"},
    {"control_id":"FDA11.11.50","framework":"FDA 21 CFR Part 11","category":"Electronic Signatures",
     "title":"§11.50 — Signature manifestations",
     "description":"Signed electronic records shall contain information associated with the signing: printed name, date/time, meaning of signature.",
     "glassbox_mapping":"WorkflowEngine quorum_approve (reviewer identity recorded), AuditLogger (decision lineage with agent_id)",
     "implementation_status":"partial"},

    # ── MAS TRM ───────────────────────────────────────────────────────────────
    {"control_id":"MASTRM.5","framework":"MAS TRM","category":"Access Control",
     "title":"Section 5 — Access control",
     "description":"FIs should implement robust user access controls, privileged access management, and periodic access reviews.",
     "glassbox_mapping":"AgentContract permitted_types (least privilege), SECURITY-001 production override guard",
     "implementation_status":"implemented"},
    {"control_id":"MASTRM.12","framework":"MAS TRM","category":"Incident Management",
     "title":"Section 12 — IT incident management",
     "description":"FIs should establish an IT incident management process to detect, report, and respond to IT incidents.",
     "glassbox_mapping":"EventBus SecurityViolation + CircuitBreakerTripped, VelocityBreaker cooldown response",
     "implementation_status":"partial"},
    {"control_id":"MASTRM.13","framework":"MAS TRM","category":"Outsourcing Risk",
     "title":"Section 13 — Outsourcing risk management",
     "description":"FIs should manage risks associated with outsourcing arrangements, including third-party AI providers.",
     "glassbox_mapping":"PROC-002 supplier known check, PROC-006 sanctions and debarment check, MCP Gateway controls",
     "implementation_status":"implemented"},

    # ── NIST SP 800-53 Rev.5 ─────────────────────────────────────────────────
    {"control_id":"800-53.AU-2","framework":"NIST SP 800-53 Rev.5","category":"Audit and Accountability",
     "title":"AU-2 — Event logging",
     "description":"Identify the types of events that the system is capable of logging in support of the audit function.",
     "glassbox_mapping":"AuditLogger (per-decision event logging), EventBus (SecurityViolation, CircuitBreakerTripped)",
     "implementation_status":"implemented"},
    {"control_id":"800-53.AU-9","framework":"NIST SP 800-53 Rev.5","category":"Audit and Accountability",
     "title":"AU-9 — Protection of audit information",
     "description":"Protect audit information and audit tools from unauthorised access, modification, and deletion.",
     "glassbox_mapping":"TamperEvidentAuditLogger (SHA-256 hash chain), AuditLogger (append-only immutable)",
     "implementation_status":"implemented"},
    {"control_id":"800-53.SI-3","framework":"NIST SP 800-53 Rev.5","category":"System and Information Integrity",
     "title":"SI-3 — Malicious code protection",
     "description":"Implement malicious code protection mechanisms at system entry and exit points.",
     "glassbox_mapping":"PayloadSanitizer (injection detection on every request), SchemaValidator",
     "implementation_status":"implemented"},
    {"control_id":"800-53.CM-3","framework":"NIST SP 800-53 Rev.5","category":"Configuration Management",
     "title":"CM-3 — Configuration change control",
     "description":"Determine the types of changes to the system that are configuration-controlled.",
     "glassbox_mapping":"IT-OPS-004 change log requirement, PolicyHotReloader (policy change tracking)",
     "implementation_status":"implemented"},
    {"control_id":"800-53.RA-3","framework":"NIST SP 800-53 Rev.5","category":"Risk Assessment",
     "title":"RA-3 — Risk assessment",
     "description":"Conduct a risk assessment, including the likelihood and magnitude of harm from unauthorised access, use, disclosure, disruption, modification, or destruction.",
     "glassbox_mapping":"RiskEvaluator (0-100 composite), AnomalyDetector, ComplianceCatalogue gap_analysis",
     "implementation_status":"implemented"},
]


# ── Compliance Catalogue Repository ───────────────────────────────────────────

class ComplianceCatalogue:
    """
    Database-driven compliance control catalogue.

    Stores ALL compliance obligations as structured records in SQLite.
    Provides:
      - Control catalogue CRUD
      - Evidence collection (link decisions → controls)
      - Compliance posture reporting
      - Gap analysis (controls without evidence)
      - Framework-level coverage summary

    Nothing is hardcoded in files — all controls are database records
    that can be updated, extended, or versioned without code changes.
    """

    def __init__(self, db_path: str = "glassbox_compliance.db"):
        self.db_path = db_path
        self._lock   = threading.Lock()
        self._shared_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
        self._init_schema()
        self._seed_catalogue()

    def _conn(self) -> sqlite3.Connection:
        if self._shared_conn:
            return self._shared_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS controls (
                        control_id              TEXT PRIMARY KEY,
                        framework               TEXT NOT NULL,
                        category                TEXT NOT NULL,
                        title                   TEXT NOT NULL,
                        description             TEXT NOT NULL,
                        glassbox_mapping        TEXT,
                        implementation_status   TEXT DEFAULT 'gap',
                        notes                   TEXT,
                        updated_at              TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS evidence (
                        evidence_id     TEXT PRIMARY KEY,
                        control_id      TEXT NOT NULL,
                        decision_id     TEXT,
                        agent_id        TEXT,
                        evidence_type   TEXT,
                        evidence_data   TEXT,
                        collected_at    TEXT NOT NULL,
                        FOREIGN KEY (control_id) REFERENCES controls(control_id)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ctrl_framework ON controls(framework)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ctrl_status ON controls(implementation_status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ev_ctrl ON evidence(control_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ev_decision ON evidence(decision_id)")

    def _seed_catalogue(self) -> None:
        """Load the built-in catalogue if controls table is empty."""
        with self._lock:
            with self._conn() as conn:
                count = conn.execute("SELECT COUNT(*) FROM controls").fetchone()[0]
                if count > 0:
                    return
                now = datetime.now(timezone.utc).isoformat()
                for ctrl in CONTROL_CATALOGUE:
                    conn.execute("""
                        INSERT OR IGNORE INTO controls
                        (control_id, framework, category, title, description,
                         glassbox_mapping, implementation_status, updated_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (
                        ctrl["control_id"], ctrl["framework"], ctrl["category"],
                        ctrl["title"], ctrl["description"],
                        ctrl.get("glassbox_mapping", ""),
                        ctrl.get("implementation_status", "gap"),
                        now,
                    ))

    # ── Control CRUD ──────────────────────────────────────────────────────────

    def get_control(self, control_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM controls WHERE control_id=?", (control_id,)
                ).fetchone()
                return dict(row) if row else None

    def list_controls(
        self,
        framework:  Optional[str] = None,
        status:     Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where, params = [], []
        if framework: where.append("framework=?"); params.append(framework)
        if status:    where.append("implementation_status=?"); params.append(status)
        sql = "SELECT * FROM controls"
        if where: sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY framework, control_id"
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]

    def update_status(self, control_id: str, status: str, notes: str = "") -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE controls SET implementation_status=?, notes=?, updated_at=? WHERE control_id=?",
                    (status, notes, now, control_id)
                )
                return cur.rowcount > 0

    def add_custom_control(self, control: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO controls
                    (control_id, framework, category, title, description,
                     glassbox_mapping, implementation_status, notes, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    control["control_id"], control["framework"], control["category"],
                    control["title"], control["description"],
                    control.get("glassbox_mapping", ""),
                    control.get("implementation_status", "gap"),
                    control.get("notes", ""), now,
                ))

    # ── Evidence collection ────────────────────────────────────────────────────

    def record_evidence(
        self,
        control_id:    str,
        evidence_type: str,     # "decision" | "audit_record" | "test_result" | "manual"
        decision_id:   Optional[str] = None,
        agent_id:      Optional[str] = None,
        evidence_data: Optional[Dict] = None,
    ) -> str:
        import uuid
        evidence_id = str(uuid.uuid4())
        now         = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO evidence
                    (evidence_id, control_id, decision_id, agent_id,
                     evidence_type, evidence_data, collected_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    evidence_id, control_id, decision_id, agent_id,
                    evidence_type,
                    json.dumps(evidence_data or {}, default=str),
                    now,
                ))
        return evidence_id

    def get_evidence(self, control_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM evidence WHERE control_id=? ORDER BY collected_at DESC",
                    (control_id,)
                ).fetchall()
                return [dict(r) for r in rows]

    # ── Compliance posture ────────────────────────────────────────────────────

    def posture_summary(self) -> Dict[str, Any]:
        """Overall compliance posture across all frameworks."""
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT framework,
                           COUNT(*) as total,
                           SUM(CASE WHEN implementation_status='implemented' THEN 1 ELSE 0 END) as implemented,
                           SUM(CASE WHEN implementation_status='partial'     THEN 1 ELSE 0 END) as partial,
                           SUM(CASE WHEN implementation_status='gap'         THEN 1 ELSE 0 END) as gap,
                           SUM(CASE WHEN implementation_status='not_applicable' THEN 1 ELSE 0 END) as not_applicable
                    FROM controls
                    GROUP BY framework
                    ORDER BY framework
                """).fetchall()

                summary = {}
                for r in rows:
                    total      = r["total"]
                    applicable = total - r["not_applicable"]
                    coverage   = round((r["implemented"] + r["partial"] * 0.5) /
                                       max(applicable, 1) * 100, 1)
                    summary[r["framework"]] = {
                        "total":           total,
                        "implemented":     r["implemented"],
                        "partial":         r["partial"],
                        "gap":             r["gap"],
                        "not_applicable":  r["not_applicable"],
                        "coverage_pct":    coverage,
                    }
                return summary

    def gap_analysis(self, framework: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all controls with status 'gap' — controls not yet addressed."""
        where  = "implementation_status='gap'"
        params = []
        if framework:
            where += " AND framework=?"
            params.append(framework)
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    f"SELECT * FROM controls WHERE {where} ORDER BY framework, control_id",
                    params
                ).fetchall()
                return [dict(r) for r in rows]

    def frameworks_list(self) -> List[str]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT framework FROM controls ORDER BY framework"
                ).fetchall()
                return [r[0] for r in rows]
