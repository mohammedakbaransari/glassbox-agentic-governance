# GlassBox Glossary

Technical terms and concepts used throughout the GlassBox framework.

GlassBox contains two governance implementations (see
[ARCHITECTURE.md](ARCHITECTURE.md#0-current-architecture)):

- **The current system** — `glassbox/domain`, `glassbox/ports`, `glassbox/app`,
  and `glassbox/adapters` — a hexagonal architecture built around
  `DecisionService`. New governance logic is added here.
- **The earlier pipeline** — `glassbox/governance`, `glassbox/store`,
  `glassbox/api` — a synchronous 9-stage pipeline (`GovernancePipeline`) that
  is still importable and still tested; several of its components
  (`stage_registry.py`, `explainer.py`, `simulator.py`,
  `compliance/catalogue.py`, `workflow/workflow_engine.py`) remain in active
  use.

Terms below are grouped by which implementation they belong to.

---

## Current System

### Action & Resource
**`ProposedAction`**
The concrete action an agent wishes to perform, as understood by the control plane: an action name, a target resource, and a set of parameters. Submitted to `DecisionService` for authorization.
- See: [domain/action.py](../glassbox/domain/action.py)

**`ResourceRef`**
Identifies the resource a `ProposedAction` targets (kind, id, tenant).
- See: [domain/action.py](../glassbox/domain/action.py)

**`Exposure` / `BlastRadius`**
The quantified potential impact of an action (monetary value, record count, blast radius tier). Compared against a ceiling to decide whether an action exceeds what is permitted.
- See: [domain/action.py](../glassbox/domain/action.py)

**Action Catalogue**
The registry of actions GlassBox knows how to govern, including their parameter schema and exposure rules. An action absent from the catalogue is denied by default.
- See: [domain/catalogue.py](../glassbox/domain/catalogue.py)

### Authorization
**`AuthorizationRequest`**
The input to a decision: a `ProposedAction`, the identity presenting it, and any supporting context.
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`AuthorizationDecision`**
The output of a decision: an effect (`ALLOW`/`DENY`/`ALLOW_WITH_OBLIGATIONS`), a rationale, and (when denied) a `DenialReason`.
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`Obligation`**
A condition attached to an `ALLOW` that must be discharged for the action to be considered complete (e.g., a required follow-up notification or approval).
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`DecisionService`**
The application-layer orchestrator that evaluates an `AuthorizationRequest` against identity, policy, mandate, and risk checks, and dispatches the resulting decision for execution.
- See: [app/decision_service.py](../glassbox/app/decision_service.py)

### Identity & Delegation
**`RawCredential`**
An unverified credential presented by a caller (type, material, presentation time), the starting point for identity resolution.
- See: [domain/identity.py](../glassbox/domain/identity.py)

**`DelegationChain` / `DelegationHop`**
The verified chain of principals that delegated authority to the agent making a request, used to bound how far delegated authority can be re-delegated.
- See: [domain/identity.py](../glassbox/domain/identity.py)

**`MandateVerdict`**
The result of checking a request against an agent's mandate (the set of actions and resources it is authorized to act on).
- See: [domain/mandate.py](../glassbox/domain/mandate.py)

**`ToolGrant`**
A specific grant of tool access issued to an agent under a mandate.
- See: [domain/mandate.py](../glassbox/domain/mandate.py)

### Evidence & Integrity
**`EvidenceReceipt`**
The durable record of a decision and its outcome, including the request, the decision, and provenance information.
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`EvidenceSegment`**
A batch of evidence receipts sealed together under a single Merkle root for tamper-evident storage.
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`MerkleProof`**
A cryptographic proof that a specific evidence receipt is included in a sealed segment, without requiring the full segment to verify.
- See: [domain/merkle.py](../glassbox/domain/merkle.py)

**`IntegrityStatus`**
The verification state of a segment or record (e.g., intact, sealed-and-purged, broken).
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`SegmentSealer`**
Publishes a signed, write-once (WORM) anchor of a segment's Merkle root before its underlying records may be purged, so purged records remain provable.
- See: [app/sealer.py](../glassbox/app/sealer.py)

### Policy & Risk
**`PolicyBundle` / `PolicyRule`**
A versioned, signed set of policy rules evaluated against an `AuthorizationRequest`. Each `PolicyRule` has an effect (allow/deny) and a condition.
- See: [domain/policy_bundle.py](../glassbox/domain/policy_bundle.py)

**`RiskInputs` / `RiskFactor`**
The signals fed into risk evaluation for a request, and the individual factors contributing to the resulting risk level.
- See: [domain/risk.py](../glassbox/domain/risk.py)

### Runtime & Composition
**Port**
A `Protocol` interface describing a capability the application layer depends on (e.g., an identity resolver, a policy source, an evidence store), independent of any concrete implementation.
- See: [ports/](../glassbox/ports/)

**Adapter**
A concrete implementation of a port. Inbound adapters expose the system (e.g., the HTTP entry point); outbound adapters back it with infrastructure (e.g., Postgres, Redis, KMS).
- See: [adapters/inbound/http/app.py](../glassbox/adapters/inbound/http/app.py), [adapters/outbound/memory](../glassbox/adapters/outbound/memory/)

**Composition Root**
`build_runtime(config, adapter_set)` — the single place adapters are wired to ports to produce a `GovernanceRuntime`.
- See: [app/composition.py](../glassbox/app/composition.py)

**`GlassBoxConfig` / `RuntimeProfile`**
Runtime configuration and the deployment profile it targets (e.g., dev, production).
- See: [app/config.py](../glassbox/app/config.py)

---

## Earlier Pipeline (`glassbox/governance`)

## A

**Anomaly Detector**
Statistical module that identifies unusual patterns in decision payloads. Triggers decisions into the anomaly advisory block if statistical deviation exceeds baseline. Example: If historical procurement requests average $50K but one request is $500K, anomaly detector flags for review.
- See: [governance/anomaly_detector.py](../glassbox/governance/anomaly_detector.py)

**Agentic RAG**
Retrieval-Augmented Generation system where an AI agent iteratively queries a knowledge base, retrieves chunks, and makes decisions. GlassBox governs the query, retrieval, and action steps independently.
- See: [rag/README.md](../glassbox/rag/README.md)

---

## B

**Baseline**
Historical statistics (mean, stddev, quartiles) of a metric used by anomaly detector. Updated periodically as new decisions are recorded. Example: Baseline for "transaction amount" might be {mean: $5K, p99: $50K}.
- See: [governance/anomaly_detector.py](../glassbox/governance/anomaly_detector.py)

**Breach**
Violation of a policy rule. When a decision fails policy evaluation, one or more breaches are recorded. Example: "Amount exceeds spending limit" is a breach.
- See: [governance/policy_engine.py](../glassbox/governance/policy_engine.py)

---

## C

**Circuit Breaker** (Velocity & Anomaly)
Failsafe mechanism that trips when thresholds exceeded (e.g., > 1000 decisions/sec or anomaly score > 3σ). When tripped, routes all subsequent decisions to human review or blocks them. Resets after cooldown period.
- See: [governance/velocity_breaker.py](../glassbox/governance/velocity_breaker.py)

**Compliance Catalogue**
Registry of governance controls (e.g., "SOC2-C1.2 Access Control", "HIPAA-164.308") mapped to required checks. GlassBox validates each decision against active controls.
- See: [compliance/catalogue.py](../glassbox/compliance/catalogue.py), [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md)

**Context Capture**
Process of automatically recording decision context (payload, agent metadata, environment) for audit and analysis. Includes payload sanitization, agent validation, and timestamp recording.
- See: [governance/context_capture.py](../glassbox/governance/context_capture.py)

---

## D

**Decision**
Atomic governance unit: an action requested by an agent (e.g., "approve loan", "transfer $5000"). Passes through the 9-stage pipeline; results in a disposition (PASS, FAIL, BLOCK, REVIEW).
- See: [ARCHITECTURE.md](ARCHITECTURE.md)

**Disposition**
Final outcome of a decision after the governance pipeline. One of: `PASS` (approved), `FAIL` (logged but allowed), `BLOCK` (rejected), `REVIEW` (routed to human).
- See: [governance/models.py](../glassbox/governance/models.py)

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
Governance strategy: stop processing at first policy violation and route to human review. Alternative to "audit log all violations then decide".
- See: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## G

**Governance Pipeline**
Core orchestration engine of the earlier implementation: accepts decision payloads, runs 9-stage evaluation (context capture, schema validation, policy engine, anomaly detection, etc.), returns a disposition.
- See: [governance/pipeline.py](../glassbox/governance/pipeline.py), [ARCHITECTURE.md](ARCHITECTURE.md)

---

## H

**Hot Reload**
Ability to update rules/policies without restarting the process. GlassBox watches configuration files; new rules automatically loaded within seconds.
- See: [rules/hot_reload.py](../glassbox/rules/hot_reload.py), [rules/README.md](../glassbox/rules/README.md)

**Human Review**
Disposition where a decision is routed to a manual approval queue (workflow engine) for analyst review and approval/rejection.
- See: [workflow/README.md](../glassbox/workflow/README.md)

---

## I

**Idempotency**
Property where repeating the same decision with identical inputs always produces identical outputs. GlassBox uses idempotency keys for safe retries.
- See: [API/endpoint_reference.md](API/endpoint_reference.md#idempotency--retry-strategy)

**Injection Attack**
Malicious payload containing SQL/command/template code intended to execute unintended actions. GlassBox's input validation detects and blocks these before evaluation. Examples: SQL injection, SSTI, XSS.
- See: [security/README.md](../glassbox/security/README.md)

---

## L

**LangChain / LangGraph / AutoGen Adapter**
Drop-in wrappers that govern every tool call (LangChain), graph node (LangGraph), or function (AutoGen) transparently. All decisions flow through the GlassBox pipeline.
- See: [integrations/README.md](../glassbox/integrations/README.md)

---

## M

**Multitenancy**
Architecture where a single GlassBox instance serves multiple organizations, each with isolated policies, audit trails, and workflows.
- See: [governance/multitenancy.py](../glassbox/governance/multitenancy.py)

---

## P

**Policy**
Declarative rule defining governance constraints. Examples: "Procurement > $100K requires approval", "US-only vendor access".
- See: [rules/README.md](../glassbox/rules/README.md), [USER/use_cases.md](USER/use_cases.md)

**Policy Violation**
Outcome when a decision payload fails policy evaluation. Recorded in the audit trail; may trigger a fail-fast block or advisory review depending on configuration.
- See: [governance/policy_engine.py](../glassbox/governance/policy_engine.py)

---

## R

**Replay**
Ability to re-execute a past decision through the governance pipeline with modified policies or payloads for testing/debugging. Enables "what-if" analysis.
- See: [governance/decision_replay.py](../glassbox/governance/decision_replay.py)

**Repository**
Abstraction layer for persistence (audit logs, policies, workflows) used by the earlier pipeline. GlassBox ships with a SQLite implementation; PostgreSQL adapters are also available.
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
Attack where malicious code embedded in a payload (e.g., `{{7*7}}` or `${...}`) is executed by template engines. GlassBox's input validation detects and blocks this.
- See: [security/README.md](../glassbox/security/README.md)

**Schema Validator**
Ensures an incoming payload matches its expected schema (field presence, types, value ranges). Blocks malformed decisions before policy evaluation.
- See: [governance/schema_validator.py](../glassbox/governance/schema_validator.py)

**Simulator**
Testing tool: runs historical decision payloads through the governance pipeline with hypothetical policies to predict policy impact.
- See: [governance/simulator.py](../glassbox/governance/simulator.py)

---

## T

**Trust**
Metadata indicating how confident the governance pipeline is in its own decision. Influenced by anomaly score, policy match quality, and execution trace completeness.
- See: [governance/trust.py](../glassbox/governance/trust.py)

---

## V

**Velocity Breaker**
Circuit breaker that trips if the decision request rate exceeds a threshold (e.g., > 10K decisions/sec). Prevents DoS attacks and resource exhaustion.
- See: [governance/velocity_breaker.py](../glassbox/governance/velocity_breaker.py), [FEATURES/velocity_breaker.md](FEATURES/velocity_breaker.md)

---

## W

**Workflow Engine**
System managing multi-stage approval processes. Maintains state (pending → in_review → approved/rejected), SLA timers, escalation rules. Used when decisions route to human review.
- See: [workflow/README.md](../glassbox/workflow/README.md)

---

## Z

**Zero Mandatory Dependencies**
Core GlassBox uses only the Python standard library; no external package requirements. Optional dependencies are available for integrations (Flask, prometheus_client, Postgres, Redis, KMS, OpenTelemetry, etc.).
- See: [README.md](../README.md), [pyproject.toml](../pyproject.toml)

---

## Quick Reference by Category

### Current System
- Action & Resource: `ProposedAction`, `ResourceRef`, `Exposure`, Action Catalogue
- Authorization: `AuthorizationRequest`, `AuthorizationDecision`, `Obligation`, `DecisionService`
- Identity & Delegation: `RawCredential`, `DelegationChain`, `MandateVerdict`, `ToolGrant`
- Evidence & Integrity: `EvidenceReceipt`, `EvidenceSegment`, `MerkleProof`, `IntegrityStatus`, `SegmentSealer`
- Policy & Risk: `PolicyBundle`, `PolicyRule`, `RiskInputs`, `RiskFactor`
- Runtime & Composition: Port, Adapter, Composition Root, `GlassBoxConfig`, `RuntimeProfile`

### Earlier Pipeline
- Decision, Disposition, Policy, Breach
- Baseline, Anomaly Detector, Risk Evaluator
- Circuit Breaker (Velocity & Anomaly)
- Trust, Execution Trace, Explainer
- Compliance Catalogue, Audit Trail, Replay, Simulator
- Domain Event, Repository, Workflow Engine
- LangChain/LangGraph/AutoGen Adapters
- Multitenancy, Hot Reload, Idempotency, Retry Policy

See also: [API/endpoint_reference.md](API/endpoint_reference.md), [ARCHITECTURE.md](ARCHITECTURE.md), [USER/use_cases.md](USER/use_cases.md)
