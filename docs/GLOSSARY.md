# GlassBox Glossary

Technical terms and concepts used throughout the GlassBox framework.

---

## A

**Anomaly Detector**
Statistical module that identifies unusual patterns in decision payloads. Triggers decisions into the anomaly advisory block if statistical deviation exceeds baseline. Example: If historical procurement requests average $50K but one request is $500K, anomaly detector flags for review.
- See: [governance/anomaly_detector.py](../glassbox/governance/anomaly_detector.py), [ARCHITECTURE.md](ARCHITECTURE.md#stage-6-anomaly-detection)

**Agentic RAG**
Retrieval-Augmented Generation system where an AI agent iteratively queries a knowledge base, retrieves chunks, and makes decisions. GlassBox governs the query, retrieval, and action steps independently.
- See: [rag/README.md](../glassbox/rag/README.md)

---

## B

**Baseline**
Historical statistics (mean, stddev, quartiles) of a metric used by anomaly detector. Updated periodically as new decisions are recorded. Example: Baseline for "transaction amount" might be {mean: $5K, p99: $50K}.
- See: [governance/anomaly_detector.py](../glassbox/governance/anomaly_detector.py), [ARCHITECTURE.md](ARCHITECTURE.md#anomaly-baseline)

**Breach**
Violation of a policy rule. When a decision fails policy evaluation, one or more breaches are recorded. Example: "Amount exceeds spending limit" is a breach.
- See: [governance/policy_engine.py](../glassbox/governance/policy_engine.py)

---

## C

**Circuit Breaker** (Velocity & Anomaly)
Failsafe mechanism that trips when thresholds exceeded (e.g., > 1000 decisions/sec or anomaly score > 3σ). When tripped, routes all subsequent decisions to human review or blocks them. Resets after cooldown period.
- See: [governance/velocity_breaker.py](../glassbox/governance/velocity_breaker.py), [ARCHITECTURE.md](ARCHITECTURE.md#stage-7-circuit-breakers)

**Compliance Catalogue**
Registry of governance controls (e.g., "SOC2-C1.2 Access Control", "HIPAA-164.308") mapped to required checks. GlassBox validates each decision against active controls.
- See: [compliance/catalogue.py](../glassbox/compliance/catalogue.py), [COMPLIANCE.md](COMPLIANCE.md)

**Context Capture**
Process of automatically recording decision context (payload, agent metadata, environment) for audit and analysis. Includes payload sanitization, agent validation, and timestamp recording.
- See: [governance/context_capture.py](../glassbox/governance/context_capture.py)

---

## D

**Decision**
Atomic governance unit: an action requested by an agent (e.g., "approve loan", "transfer $5000"). Passes through 9-stage pipeline; results in disposition (PASS, FAIL, BLOCK, REVIEW).
- See: [ARCHITECTURE.md](ARCHITECTURE.md#9-stage-pipeline)

**Disposition**
Final outcome of a decision after governance pipeline. One of: `PASS` (approved), `FAIL` (logged but allowed), `BLOCK` (rejected), `REVIEW` (routed to human).
- See: [governance/models.py](../glassbox/governance/models.py), [ARCHITECTURE.md](ARCHITECTURE.md#dispositions)

**Domain Event**
Fact published by the pipeline to notify external systems of governance outcomes. Examples: `decision.executed`, `policy.violated`, `security.violation`.
- See: [events/README.md](../glassbox/events/README.md)

---

## E

**Execution Trace**
Detailed log of all stages a decision passes through, including timing, policy matches, violations, and reasoning. Enables post-hoc analysis and debugging.
- See: [governance/execution_trace.py](../glassbox/governance/execution_trace.py)

**Explainer**
Module that generates human-readable explanations for governance decisions. Translates policy violations and anomaly flags into clear language for audit reports and user communication.
- See: [governance/explainer.py](../glassbox/governance/explainer.py)

---

## F

**Fail-Fast**
Governance strategy: stop processing at first policy violation and route to human review (Stage 5). Alternative to "audit log all violations then decide".
- See: [ARCHITECTURE.md](ARCHITECTURE.md#stage-5-disposition)

---

## G

**Governance Pipeline**
Core orchestration engine: accepts decision payloads, runs 9-stage evaluation (context capture, schema validation, policy engine, anomaly detection, etc.), returns disposition.
- See: [governance/pipeline.py](../glassbox/governance/pipeline.py), [ARCHITECTURE.md](ARCHITECTURE.md#9-stage-pipeline)

---

## H

**Hot Reload**
Ability to update rules/policies without restarting the process. GlassBox watches configuration files; new rules automatically loaded within seconds.
- See: [rules/hot_reload.py](../glassbox/rules/hot_reload.py), [rules/README.md](../glassbox/rules/README.md)

**Human Review**
Stage 4 disposition where a decision is routed to a manual approval queue (workflow engine) for analyst review and approval/rejection.
- See: [workflow/README.md](../glassbox/workflow/README.md), [ARCHITECTURE.md](ARCHITECTURE.md#stage-5-disposition)

---

## I

**Idempotency**
Property where repeating the same decision with identical inputs always produces identical outputs. GlassBox uses idempotency keys for safe retries.
- See: [docs/API.md](API.md#idempotency-key)

**Injection Attack**
Malicious payload containing SQL/command/template code intended to execute unintended actions. GlassBox sanitizer detects and blocks before Stage 0. Examples: SQL injection, SSTI, XSS.
- See: [security/README.md](../glassbox/security/README.md)

---

## L

**LangChain / LangGraph / AutoGen Adapter**
Drop-in wrappers that govern every tool call (LangChain), graph node (LangGraph), or function (AutoGen) transparently. All decisions flow through GlassBox pipeline.
- See: [integrations/README.md](../glassbox/integrations/README.md)

---

## M

**Multitenancy**
Architecture where single GlassBox instance serves multiple organizations, each with isolated policies, audit trails, and workflows. Tenant routing happens at Stage 0.
- See: [governance/multitenancy.py](../glassbox/governance/multitenancy.py)

---

## P

**Policy**
Declarative rule defining governance constraints. Examples: "Procurement > $100K requires approval", "US-only vendor access". Policies evaluated in Stage 3.
- See: [rules/README.md](../glassbox/rules/README.md), [USECASES.md](USECASES.md)

**Policy Violation**
Outcome when decision payload fails policy evaluation. Recorded in audit trail; may trigger fail-fast block or advisory review depending on configuration.
- See: [governance/policy_engine.py](../glassbox/governance/policy_engine.py)

---

## R

**Replay**
Ability to re-execute a past decision through the governance pipeline with modified policies or payloads for testing/debugging. Enables "what-if" analysis.
- See: [governance/decision_replay.py](../glassbox/governance/decision_replay.py)

**Repository**
Abstraction layer for persistence (audit logs, policies, workflows). GlassBox ships with SQLite implementation; PostgreSQL adapters available.
- See: [store/README.md](../glassbox/store/README.md)

**Retry Policy**
Configuration for automatic re-execution of failed decisions. Includes backoff strategy (exponential, linear), max attempts, and failure codes to retry on.
- See: [governance/retry_policy.py](../glassbox/governance/retry_policy.py)

**Risk Evaluator**
Module that assigns risk scores to decisions based on policy breaches, anomaly flags, and historical patterns. Informs disposition routing (e.g., high-risk → human review).
- See: [governance/risk_evaluator.py](../glassbox/governance/risk_evaluator.py)

---

## S

**SSTI (Server-Side Template Injection)**
Attack where malicious code embedded in payload (e.g., `{{7*7}}` or `${...}`) is executed by template engines. GlassBox sanitizer detects and blocks.
- See: [security/README.md](../glassbox/security/README.md)

**Schema Validator**
Stage 1 of pipeline: ensures incoming payload matches expected schema (field presence, types, value ranges). Blocks malformed decisions before policy evaluation.
- See: [governance/schema_validator.py](../glassbox/governance/schema_validator.py)

**Simulator**
Testing tool: runs historical decision payloads through governance pipeline with hypothetical policies to predict policy impact.
- See: [governance/simulator.py](../glassbox/governance/simulator.py)

---

## T

**Trust**
Metadata indicating how confident the governance pipeline is in its own decision. Influenced by anomaly score, policy match quality, and execution trace completeness.
- See: [governance/trust.py](../glassbox/governance/trust.py)

---

## V

**Velocity Breaker**
Circuit breaker that trips if decision request rate exceeds threshold (e.g., > 10K decisions/sec). Prevents DoS attacks and resource exhaustion.
- See: [governance/velocity_breaker.py](../glassbox/governance/velocity_breaker.py), [ARCHITECTURE.md](ARCHITECTURE.md#stage-7-circuit-breakers)

---

## W

**Workflow Engine**
System managing multi-stage approval processes. Maintains state (pending → in_review → approved/rejected), SLA timers, escalation rules. Used when decisions route to human review.
- See: [workflow/README.md](../glassbox/workflow/README.md)

---

## Z

**Zero Mandatory Dependencies**
Core GlassBox (glassbox/governance, glassbox/compliance, glassbox/rules) uses only Python standard library; no external package requirements. Optional dependencies available for integrations (Flask, prometheus_client, etc.).
- See: [README.md](../README.md), [requirements.txt](../requirements.txt)

---

## Quick Reference by Category

### Pipeline Stages
- Context Capture (Stage 0)
- Schema Validator (Stage 1)
- Decision Replay (Stage 2)
- Policy Engine (Stage 3)
- Disposition Routing (Stage 4–5)
- Anomaly Detection (Stage 6)
- Circuit Breakers (Stage 7)
- Audit Logging (Stage 8)
- Event Publishing (Stage 9)

See [ARCHITECTURE.md](ARCHITECTURE.md#9-stage-pipeline) for full pipeline reference.

### Governance Concepts
- Decision, Disposition, Policy, Breach
- Baseline, Anomaly Detector, Risk Evaluator
- Circuit Breaker (Velocity & Anomaly)
- Trust, Execution Trace, Explainer

### Compliance & Audit
- Compliance Catalogue, Control
- Audit Trail, Execution Trace
- Replay, Simulator

### Integration & Deployment
- Domain Event, Repository, Workflow Engine
- LangChain/LangGraph/AutoGen Adapters
- Multitenancy, Hot Reload
- Idempotency, Retry Policy

See also: [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [USECASES.md](USECASES.md)
