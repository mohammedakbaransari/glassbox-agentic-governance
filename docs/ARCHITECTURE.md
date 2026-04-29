# GlassBox — Architecture Overview

**v1.2.0 | Mohammed Akbar Ansari | Independent Researcher**

> Full component reference: [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md)

---

## 1. The Problem & Solution

### Without GlassBox — Ungoverned Gap

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart LR
    classDef agent  fill:#74b9ff,stroke:#0984e3,color:#000
    classDef danger fill:#ff7675,stroke:#d63031,color:#fff
    classDef system fill:#b2bec3,stroke:#636e72,color:#000

    A1([Procurement AI]):::agent
    A2([Pricing AI]):::agent
    A3([Trading AI]):::agent
    A4([Clinical AI]):::agent

    GAP["⚠ No Validation\n No Policy Enforcement\n No Audit Trail"]:::danger

    E1[(ERP / SAP)]:::system
    E2[(Trading Desk)]:::system
    E3[(Clinical System)]:::system

    A1 & A2 & A3 & A4 --> GAP --> E1 & E2 & E3
```

### With GlassBox — Governed Decision Layer

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart LR
    classDef agent   fill:#74b9ff,stroke:#0984e3,color:#000
    classDef glass   fill:#6c5ce7,stroke:#4a3ab5,color:#fff
    classDef execute fill:#00b894,stroke:#00695c,color:#fff
    classDef review  fill:#fdcb6e,stroke:#e17055,color:#000
    classDef blocked fill:#ff7675,stroke:#d63031,color:#fff
    classDef storage fill:#dfe6e9,stroke:#636e72,color:#000

    A1([Procurement AI]):::agent
    A2([Pricing AI]):::agent
    A3([Trading AI]):::agent
    A4([Clinical AI]):::agent

    GB["🔷 GlassBox\nDecision Governance\nValidate · Score · Route · Audit"]:::glass

    EX([✅ AUTO_EXECUTE\nDownstream System]):::execute
    HR([⏳ HUMAN_REVIEW\nWorkflow Queue]):::review
    BL([🚫 BLOCKED\nRejected + Audit]):::blocked
    AU[(📋 Immutable\nAudit Trail)]:::storage

    A1 & A2 & A3 & A4 --> GB
    GB -->|"risk ≤ 35"| EX
    GB -->|"35 < risk ≤ 70"| HR
    GB -->|"risk > 70\nor policy fail"| BL
    GB --> AU
```

---

## 2. Four-Tier Layer Architecture

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart TB
    classDef tier4 fill:#6c5ce7,stroke:#4a3ab5,color:#fff
    classDef tier3 fill:#0984e3,stroke:#044f8c,color:#fff
    classDef tier2 fill:#00b894,stroke:#00695c,color:#fff
    classDef tier1 fill:#fdcb6e,stroke:#e17055,color:#000

    subgraph T4["Tier 4 — Integration Layer"]
        direction LR
        LC[LangChain]:::tier4
        LG[LangGraph]:::tier4
        AG[AutoGen]:::tier4
        CA[CrewAI]:::tier4
        SP["PySpark\\nDatabricks"]:::tier4
        API["REST API\\n17 endpoints"]:::tier4
        MCP[MCP\nGateway]:::tier4
        OPA[OPA\nAdapter]:::tier4
    end

    subgraph T3["Tier 3 — Orchestration & AI Layer"]
        direction LR
        OR[AgentOrchestrator\nChain · DAG · Saga]:::tier3
        RAG[AgenticRAG\nGovernance]:::tier3
        MT[MultiTenant\nPipeline]:::tier3
    end

    subgraph T2["Tier 2 — Application Layer"]
        direction LR
        GP[GovernancePipeline]:::tier2
        WE[WorkflowEngine]:::tier2
        DR[DecisionReplay]:::tier2
        RL[RulesLoader\nYAML/JSON]:::tier2
        PS[PolicySimulator]:::tier2
        EP[Enterprise\nPipeline]:::tier2
    end

    subgraph T1["Tier 1 — Core Framework"]
        direction LR
        PE[PolicyEngine\n35 policies]:::tier1
        RE[RiskEvaluator\n0–100]:::tier1
        AD["AnomalyDetector\\nWelford O(1)"]:::tier1
        VB["VelocityBreaker\\nRate Limits"]:::tier1
        AL[AuditLogger\nLock-Pooled]:::tier1
        TA[TamperEvident\nAudit SHA-256]:::tier1
        WAL[WriteAheadLog\nCrash-Safe]:::tier1
        EB[EventBus\n8 domain events]:::tier1
        AC[AccessControl\nRBAC]:::tier1
        SR[StageRegistry\nP50/P99]:::tier1
    end

    T4 --> T3 --> T2 --> T1
```

---

## 3. The 9-Stage Pipeline (+ 2 Security Pre-checks)

Every `DecisionRequest` passes through these steps. Any step can **block** execution — all later steps are skipped.

| Step | Name | Module | Blocks On |
|---|---|---|---|
| Pre-1 | Security Sanitization | `security/sanitizer.py` | SQL/XSS/SSTI/path-traversal in payload or `agent_id` |
| Pre-2 | Agent-ID Sanitization | `security/sanitizer.py` | Unicode homoglyphs, null bytes |
| 0 | AgentContract Validation | `governance/pipeline.py` | Unauthorised `decision_type`, amount limit exceeded |
| 1 | Context Capture | `governance/context_capture.py` | — enrichment only |
| 2 | Schema Validation | `governance/schema_validator.py` | Missing/wrong-type required fields |
| 3 | Velocity Breaker | `governance/velocity_breaker.py` | Per-agent > 100 req/min; ecosystem limit |
| 4 | Anomaly Detection | `governance/anomaly_detector.py` | Z-score > 3σ after min_samples |
| 5 | Policy Enforcement | `governance/policy_engine.py` | Any registered policy returns `fail` |
| 6 | Risk Evaluation | `governance/risk_evaluator.py` | Composite score → disposition routing |
| 7 | Disposition + Finalise | `governance/pipeline.py` | WAL + audit + EventBus publish |

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart TD
    classDef precheck  fill:#a29bfe,stroke:#6c5ce7,color:#fff
    classDef stage     fill:#74b9ff,stroke:#0984e3,color:#000
    classDef check     fill:#636e72,stroke:#2d3436,color:#fff
    classDef blocked   fill:#ff7675,stroke:#d63031,color:#fff
    classDef execute   fill:#00b894,stroke:#00695c,color:#fff
    classDef review    fill:#fdcb6e,stroke:#e17055,color:#000
    classDef storage   fill:#dfe6e9,stroke:#636e72,color:#000
    classDef enrichment fill:#55efc4,stroke:#00b894,color:#000

    REQ(["📥 DecisionRequest\nagent_id · type · payload"])

    subgraph SECURITY["🛡 Security Pre-checks"]
        SEC1{"SQL / XSS / SSTI\nPath-Traversal?"}:::precheck
        SEC2{"Null bytes /\nHomoglyphs?"}:::precheck
    end

    subgraph PIPELINE["⚙ 9-Stage Governance Pipeline"]
        S0{"Stage 0\nAgentContract"}:::check
        S1["Stage 1\nContext Capture\n⏱ timestamp · host · platform"]:::enrichment
        S2{"Stage 2\nSchema Validation"}:::check
        S3{"Stage 3\nVelocity Breaker\n⏱ P50 0.03ms"}:::check
        S4{"Stage 4\nAnomaly Detection\n⏱ P50 0.04ms"}:::check
        S5{"Stage 5\nPolicy Enforcement\n35 built-in policies\n⏱ P50 0.05ms"}:::check
        S6["Stage 6\nRisk Evaluation\n0 – 100 composite score\n⏱ P50 0.04ms"]:::stage
        S7{"Stage 7\nDisposition\nrouting by risk score"}:::check
    end

    subgraph FINAL["📦 Finalise"]
        WAL["WriteAheadLog\nbegin → commit"]:::storage
        AUD["AuditLogger +\nTamperEvidentAudit"]:::storage
        EVT["EventBus\npublish domain event"]:::storage
    end

    BLK_S(["🚫 BLOCKED\ninjection detected"]):::blocked
    BLK_0(["🚫 BLOCKED\nunauthorised type"]):::blocked
    BLK_2(["🚫 BLOCKED\nschema error"]):::blocked
    BLK_3(["🚫 BLOCKED\nrate exceeded"]):::blocked
    BLK_4(["🚫 BLOCKED\nZ-score > 3σ"]):::blocked
    BLK_5(["🚫 BLOCKED\npolicy violation"]):::blocked
    EX(["✅ AUTO_EXECUTE\nrisk ≤ 35"]):::execute
    HR(["⏳ HUMAN_REVIEW\n35 < risk ≤ 70\n→ WorkflowEngine"]):::review
    BLK_7(["🚫 BLOCKED\nrisk > 70"]):::blocked

    RESP(["📤 DecisionResponse\n+ ExecutionTrace"])

    REQ --> SEC1
    SEC1 -->|"clean"| SEC2
    SEC1 -->|"injection found"| BLK_S
    SEC2 -->|"clean"| S0
    SEC2 -->|"malformed"| BLK_S

    S0 -->|"type/amount violation"| BLK_0
    S0 -->|"ok"| S1
    S1 --> S2
    S2 -->|"missing field / type error"| BLK_2
    S2 -->|"ok"| S3
    S3 -->|"rate exceeded"| BLK_3
    S3 -->|"ok"| S4
    S4 -->|"z-score > 3σ"| BLK_4
    S4 -->|"ok"| S5
    S5 -->|"policy fail"| BLK_5
    S5 -->|"pass"| S6
    S6 --> S7
    S7 -->|"risk ≤ 35"| EX
    S7 -->|"35 < risk ≤ 70"| HR
    S7 -->|"risk > 70"| BLK_7
    S7 --> WAL
    WAL --> AUD --> EVT --> RESP
```

---

## 4. Security Threat Model — What Gets Blocked Where

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart LR
    classDef threat  fill:#ff7675,stroke:#d63031,color:#fff
    classDef guard   fill:#a29bfe,stroke:#6c5ce7,color:#fff
    classDef outcome fill:#00b894,stroke:#00695c,color:#fff

    subgraph THREATS["⚠ Incoming Threats"]
        T1["SQL Injection\nSELECT * FROM..."]:::threat
        T2["SSTI\n{{ 7*7 }}"]:::threat
        T3["XSS\n&lt;script&gt;..."]:::threat
        T4["Path Traversal\n../../etc/passwd"]:::threat
        T5["Homoglyph Bypass\nаdmin (Cyrillic a)"]:::threat
        T6["Oversized Amount\n$750K without contract_id"]:::threat
        T7["Rate Flood\n500 req/min"]:::threat
        T8["Statistical Spike\nZ-score = 8.3σ"]:::threat
        T9["Policy Breach\nBlacklisted supplier"]:::threat
        T10["High Risk Score\ncomposite = 82"]:::threat
    end

    subgraph GUARDS["🛡 GlassBox Defences"]
        G1["PayloadSanitizer\n25+ pattern detectors"]:::guard
        G2["AgentContract\nvalidation"]:::guard
        G3["VelocityBreaker\n100 req/min per agent"]:::guard
        G4["AnomalyDetector\nWelford Z-score"]:::guard
        G5["PolicyEngine\n35 built-in rules"]:::guard
        G6["RiskEvaluator\ndisposition routing"]:::guard
    end

    SAFE(["✅ Clean decisions\nreach execution"]):::outcome

    T1 & T2 & T3 & T4 & T5 --> G1
    T6 --> G2
    T7 --> G3
    T8 --> G4
    T9 --> G5
    T10 --> G6

    G1 & G2 & G3 & G4 & G5 & G6 --> SAFE
```

---

## 5. Risk Scoring & Disposition Routing

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart TD
    classDef low    fill:#00b894,stroke:#00695c,color:#fff
    classDef medium fill:#fdcb6e,stroke:#e17055,color:#000
    classDef high   fill:#ff7675,stroke:#d63031,color:#fff
    classDef factor fill:#74b9ff,stroke:#0984e3,color:#000
    classDef result fill:#dfe6e9,stroke:#636e72,color:#000

    subgraph FACTORS["Risk Factor Inputs (weighted composite)"]
        F1["Policy Violations\n× weight 0.40"]:::factor
        F2["Anomaly Flag\n× weight 0.25"]:::factor
        F3["Decision Type Risk\n× weight 0.20"]:::factor
        F4["Amount Magnitude\n× weight 0.10"]:::factor
        F5["Agent Trust Level\n× weight 0.05"]:::factor
    end

    SCORE["Composite Risk Score\n0 – 100"]:::result

    ZONE1{"Score ≤ 35?\nLow Risk"}:::low
    ZONE2{"35 < Score ≤ 70?\nMedium Risk"}:::medium
    ZONE3{"Score > 70?\nHigh Risk"}:::high

    OUT1(["✅ AUTO_EXECUTE\nProceeds immediately"]):::low
    OUT2(["⏳ HUMAN_REVIEW\nWorkflowEngine\nSLA: configurable minutes"]):::medium
    OUT3(["🚫 BLOCK\nRejected\nemit decision.blocked"]):::high

    F1 & F2 & F3 & F4 & F5 --> SCORE
    SCORE --> ZONE1
    SCORE --> ZONE2
    SCORE --> ZONE3
    ZONE1 --> OUT1
    ZONE2 --> OUT2
    ZONE3 --> OUT3
```

---

## 6. Multi-Tenant Architecture

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart TB
    classDef tenant  fill:#74b9ff,stroke:#0984e3,color:#000
    classDef shared  fill:#6c5ce7,stroke:#4a3ab5,color:#fff
    classDef iso     fill:#00b894,stroke:#00695c,color:#fff
    classDef storage fill:#dfe6e9,stroke:#636e72,color:#000

    subgraph TRA["Tenant A — org_a"]
        PA["GovernancePipeline A\nPolicies A · Thresholds A"]:::tenant
        ALA["AuditLog A\n/glassbox_logs/org_a/"]:::storage
        VA["VelocityBreaker A\nquota A"]:::tenant
    end

    subgraph TRB["Tenant B — org_b"]
        PB["GovernancePipeline B\nPolicies B · Thresholds B"]:::tenant
        ALB["AuditLog B\n/glassbox_logs/org_b/"]:::storage
        VB["VelocityBreaker B\nquota B"]:::tenant
    end

    MT["MultiTenantPipeline\n+ TenantRegistry"]:::shared
    CTX["RequestContext\nthread-local tenant_id\n+ distributed trace_id"]:::iso

    REQ_A(["Request: tenant_id=org_a"]):::tenant
    REQ_B(["Request: tenant_id=org_b"]):::tenant

    REQ_A --> MT
    REQ_B --> MT
    MT --> CTX
    CTX --> PA & PB
    PA --> ALA & VA
    PB --> ALB & VB

    NOTE["🔒 Zero cross-tenant\nstate leakage\nSeparate policy engines\nSeparate audit logs\nSeparate rate limits"]:::iso
```

---

## 7. Async Audit Architecture

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart LR
    classDef pipeline fill:#74b9ff,stroke:#0984e3,color:#000
    classDef queue    fill:#fdcb6e,stroke:#e17055,color:#000
    classDef writer   fill:#a29bfe,stroke:#6c5ce7,color:#fff
    classDef storage  fill:#dfe6e9,stroke:#636e72,color:#000
    classDef alert    fill:#ff7675,stroke:#d63031,color:#fff

    subgraph MAIN["Main Request Thread (non-blocking)"]
        GP["GovernancePipeline\nprocess()"]:::pipeline
        BQ["BoundedQueue\nmaxsize=1000\nbackpressure-safe"]:::queue
    end

    subgraph ASYNC["Background Writer Thread"]
        BA["Batch Accumulator\naudit_batch_size records"]:::writer
        RING["AuditLogger\nIn-Memory Ring Buffer\n+ Lock Pool (8–16 locks)"]:::writer
    end

    subgraph PERSIST["Persistence Outputs"]
        JSONL["JSONL File\naudit-YYYY-MM-DD.jsonl\n(rotates at max_size_mb)"]:::storage
        DB["SQLite / PostgreSQL\nAuditRepository"]:::storage
        TA["TamperEvidentAudit\nSHA-256 hash chain\nHMAC verification"]:::storage
    end

    FULL(["⚠ RuntimeError\nQueue Full\n→ Priority 1 fix: sync fallback"]):::alert

    GP -->|"async_audit_writes=True"| BQ
    BQ -->|"queue not full"| BA
    BQ -->|"queue.Full"| FULL
    BA --> RING
    RING --> JSONL & DB & TA
```

---

## 8. Agent Orchestration Patterns

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart LR
    classDef node    fill:#74b9ff,stroke:#0984e3,color:#000
    classDef blocked fill:#ff7675,stroke:#d63031,color:#fff
    classDef ok      fill:#00b894,stroke:#00695c,color:#fff
    classDef saga    fill:#a29bfe,stroke:#6c5ce7,color:#fff
    classDef comp    fill:#fdcb6e,stroke:#e17055,color:#000

    subgraph CHAIN["🔗 Chain Pattern — Sequential, abort-on-block"]
        C1["Node 1\nForecast"]:::node --> C2["Node 2\nApproval"]:::node --> C3["Node 3\nExecution"]:::node
        C2 -->|"BLOCKED"| CBLK(["❌ Chain Stops"]):::blocked
    end

    subgraph DAG["🔀 DAG Pattern — Parallel, independent"]
        D1["Node A\nSupplier Check"]:::node & D2["Node B\nBudget Check"]:::node & D3["Node C\nRisk Check"]:::node
        D1 & D2 & D3 --> D4["Node D\nFinal Decision"]:::node
        D1 -->|"BLOCKED"| DBLK(["⚠ Partial block\nothers continue"]):::blocked
    end

    subgraph SAGA["♻ Saga Pattern — Compensation on failure"]
        S1["Step 1\nReserve Inventory"]:::saga --> S2["Step 2\nCharge Account"]:::saga --> S3["Step 3\nShip Order"]:::saga
        S3 -->|"FAIL"| CO3["Compensate 3\nCancel Shipment"]:::comp
        S2 -->|"FAIL"| CO2["Compensate 2\nRefund Charge"]:::comp
        S1 -->|"FAIL"| CO1["Compensate 1\nRelease Inventory"]:::comp
        CO3 --> CO2 --> CO1
    end
```

---

## 9. Policy Domain Coverage Map

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
mindmap
  root((GlassBox<br/>35 Policies<br/>10 Domains))
    Procurement
      PROC-001 Amount > 500K needs contract_id
      PROC-002 Approved supplier registry
      PROC-003 High-risk category ref
      PROC-006 Sanctions/debarment check
    Financial
      FIN-001 Transfer limits
      FIN-002 BSA structuring detection
      FIN-003 GDPR Art.22 automated decisions
      FIN-004 Round-amount CTR flag
      FIN-005 Destination account validation
    Pricing
      PRICE-001 Max 30% single-decision change
      PRICE-002 Floor price enforcement
    IT Operations
      IT-OPS-002 Destructive action guard
      IT-OPS-003 Change-window enforcement
      IT-OPS-004 Production override restriction
    Clinical
      CLIN-001 Dosage safety limits
      CLIN-002 Trial protocol compliance
      AI-001 Model confidence ≥ 0.30
      SECURITY-001 No user_override in prod
    Trading
      TRADE-001 Position size limits
      TRADE-002 Algorithmic circuit breaker
      AI-001 Model confidence ≥ 0.30
      SECURITY-001 No user_override in prod
    HR
      HR-001 Compensation limits
      HR-002 Approval reference required
      HR-003 GDPR data rights
    Logistics
      LOG-001 High-value shipment ref
    Compliance Gates
      COMPLIANCE-001 Regulatory gate
      COMPLIANCE-002 Framework check
      COMPLIANCE-003 Evidence requirement
    General / All Types
      RISK-001 Composite risk threshold
      AI-001 Confidence floor
      SECURITY-001 No production override
```

---

## 10. Distributed Deployment (Redis-Backed)

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart TB
    classDef replica fill:#74b9ff,stroke:#0984e3,color:#000
    classDef redis   fill:#ff7675,stroke:#d63031,color:#fff
    classDef local   fill:#55efc4,stroke:#00b894,color:#000
    classDef lb      fill:#6c5ce7,stroke:#4a3ab5,color:#fff

    LB["Load Balancer\nnginx / Kubernetes Ingress"]:::lb

    subgraph REPLICAS["Multi-Replica Deployment (Kubernetes / Docker)"]
        R1["Replica 1\nGovernancePipeline"]:::replica
        R2["Replica 2\nGovernancePipeline"]:::replica
        R3["Replica N\nGovernancePipeline"]:::replica
    end

    subgraph REDIS["Shared State — Redis"]
        RFB[("RedisFleetBudgetBackend\nINCRBYFLOAT atomic\nfleet spend tracking")]:::redis
        RAS[("RedisAnomalyStore\nLua Welford script\nshared mean · M2 · count")]:::redis
    end

    subgraph FALLBACK["Local Fallback (Redis unavailable)"]
        LFB["In-process\nFleetBudgetPolicy"]:::local
        LAD["In-process\nAnomalyDetector"]:::local
    end

    LB --> R1 & R2 & R3
    R1 & R2 & R3 -->|"DistributedFleetBudgetPolicy"| RFB
    R1 & R2 & R3 -->|"DistributedAnomalyDetector"| RAS
    R1 & R2 & R3 -.->|"Redis unavailable\n(circuit breaker)"| LFB & LAD
```

---

## 11. Write-Ahead Log (WAL) — Crash Safety

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
stateDiagram-v2
    direction LR

    [*] --> PENDING : pipeline.process()\ncalls begin_transaction()

    PENDING --> IN_PROGRESS : mark_side_effect()\n"audit write scheduled"
    IN_PROGRESS --> IN_PROGRESS : mark_side_effect()\n"repo persist scheduled"
    IN_PROGRESS --> IN_PROGRESS : mark_side_effect()\n"event emit scheduled"

    IN_PROGRESS --> COMMITTED : commit()\nall side effects flushed

    IN_PROGRESS --> ROLLED_BACK : rollback()\nor exception during finalize

    COMMITTED --> [*] : DecisionResponse returned

    ROLLED_BACK --> [*] : error propagated

    note right of PENDING
        On crash: startup recovery
        replays PENDING +
        IN_PROGRESS entries
        WorkflowEngine.create_from_decision()
        is idempotent — safe to replay
    end note
```

---

## 12. Data Flow — Full Decision Lifecycle

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
sequenceDiagram
    autonumber
    participant AG  as 🤖 AI Agent
    participant GP  as GovernancePipeline
    participant SAN as PayloadSanitizer
    participant CTX as ContextCapture
    participant SCH as SchemaValidator
    participant VEL as VelocityBreaker
    participant ANO as AnomalyDetector
    participant POL as PolicyEngine
    participant RSK as RiskEvaluator
    participant WAL as WriteAheadLog
    participant WFE as WorkflowEngine
    participant AUD as AuditLogger
    participant EB  as EventBus

    AG  ->> GP  : process(DecisionRequest)

    Note over GP,SAN: Security Pre-checks (P50 ≈ 0.01ms)
    GP  ->> SAN : sanitize(payload, agent_id)
    SAN -->> GP : SecurityReport — clean / BLOCK

    Note over GP,CTX: Stage 0–1 (P50 ≈ 0.03ms)
    GP  ->> CTX : capture(request)
    CTX -->> GP : enriched context (host, timestamp, platform)

    Note over GP,SCH: Stage 2 (P50 ≈ 0.05ms)
    GP  ->> SCH : validate(payload, decision_type)
    SCH -->> GP : ValidationResult — ok / BLOCK

    Note over GP,VEL: Stage 3 (P50 ≈ 0.03ms)
    GP  ->> VEL : check(agent_id, amount)
    VEL -->> GP : allowed / rate_exceeded

    Note over GP,ANO: Stage 4 (P50 ≈ 0.04ms)
    GP  ->> ANO : check(field, value)
    ANO -->> GP : normal / anomaly (z-score)

    Note over GP,POL: Stage 5 (P50 ≈ 0.05ms)
    GP  ->> POL : evaluate(decision_type, payload, context)
    POL -->> GP : PolicyResult — pass / violations[]

    Note over GP,RSK: Stage 6 (P50 ≈ 0.04ms)
    GP  ->> RSK : score(decision, violations, anomaly_flag)
    RSK -->> GP : risk_score 0–100

    Note over GP,WAL: Stage 7 — Finalise
    GP  ->> WAL : begin_transaction()
    opt risk 35-70 → HUMAN_REVIEW
        GP  ->> WFE : create_from_decision() [idempotent]
        WFE -->> GP : workflow_id
    end
    GP  ->> AUD : append(audit_record)
    GP  ->> WAL : commit()
    GP  ->> EB  : publish(decision.executed | decision.blocked | decision.pending_review)

    GP  -->> AG : DecisionResponse\n(final_status · risk_score · violations · trace)
```

---

## 13. Component Map

```
glassbox/
├── governance/                   Core pipeline domain logic (32 modules)
│   ├── pipeline.py               GovernancePipeline — 9-stage orchestrator
│   ├── models.py                 DecisionRequest, AuditRecord, DecisionResponse, …
│   ├── policy_engine.py          Thread-safe policy registry + evaluator (35 built-in)
│   ├── policy_parameters.py      PolicyParameterStore — runtime threshold updates
│   ├── risk_evaluator.py         Weighted composite scoring 0–100
│   ├── anomaly_detector.py       Welford Z-score; DistributedAnomalyDetector (Redis)
│   ├── velocity_breaker.py       Sliding-window breaker; DistributedFleetBudgetPolicy (Redis)
│   ├── stage_registry.py         StageRegistry — feature flags, P50/P99 per stage
│   ├── write_ahead_log.py        WAL — crash-safe two-phase side-effect tracking
│   ├── advanced_audit.py         TamperEvidentAuditLogger — SHA-256 hash chain
│   ├── audit_logger.py           AuditLogger — lock-pooled ring buffer + JSONL rotation
│   ├── bounded_queue.py          BoundedQueue — backpressure-safe async audit writes
│   ├── event_dispatcher.py       EventDispatcher — fan-out to EventBus handlers
│   ├── schema_validator.py       Payload structure validation per decision type
│   ├── decision_replay.py        Sync + async + parallel batch replay
│   ├── retry_policy.py           RetryExecutor — sync + async retry with backoff
│   ├── context_capture.py        Platform-safe metadata enrichment
│   ├── logging_manager.py        GlassBoxLogger — JSON/text, rotating
│   ├── execution_trace.py        Per-stage timing and outcome trace (opt-in)
│   ├── simulator.py              PolicySimulator — dry-run impact analysis
│   ├── multitenancy.py           TenantRegistry + MultiTenantPipeline
│   ├── access_control.py         RBAC — role hierarchy, permission caching
│   ├── encryption.py             AES-256-GCM field-level encryption + PBKDF2
│   ├── api_gateway.py            Middleware pipeline — auth, rate-limit, CORS
│   ├── request_context.py        Thread-local context — multi-tenant + trace
│   ├── threadpool_config.py      Async worker pool sizing
│   ├── enterprise_pipeline.py    EnterprisePipeline — full-stack production wrapper
│   ├── trust.py                  TrustLevel — agent trust chain validation
│   ├── explainer.py              DecisionExplainer — natural-language rationale
│   ├── currency.py               CurrencyConverter — multi-currency normalisation
│   └── idempotency.py            IdempotencyStore — request deduplication guard
│
├── store/                        Persistence layer
│   ├── database_abstraction.py   DatabaseFactory — SQLite / PostgreSQL / SQL Server
│   └── repository.py             PolicyRepository, AuditRepository, WorkflowRepository
│
├── security/                     Input sanitisation
│   └── sanitizer.py              PayloadSanitizer — 25+ injection pattern detectors
│
├── rules/                        Declarative rules engine
│   ├── rules_engine.py           YAML/JSON → Policy compilation, 13 operators
│   └── hot_reload.py             Live rule updates without restart
│
├── workflow/                     Approval workflow
│   └── workflow_engine.py        pending → in_review → approved/rejected (idempotent)
│
├── events/                       Domain events
│   └── event_bus.py              8 event types, async handlers, webhooks, SSE
│
├── orchestration/                Multi-agent orchestration
│   └── orchestrator.py           Chain, DAG graph, Saga patterns
│
├── rag/                          RAG governance
│   └── governance.py             Query, retrieval, agentic loop governance
│
├── adapters/                     Platform adapters
│   ├── platforms.py              Databricks, Kubernetes, Fabric; auto_detect_adapter()
│   └── spark.py                  GlassBoxSparkAdapter — UDF, mapPartitions, Streaming
│
├── integrations/                 AI framework adapters
│   ├── adapters.py               LangChain, LangGraph, AutoGen
│   ├── extended_adapters.py      CrewAI, AutoGen extended
│   ├── mcp_gateway.py            MCP (Model Context Protocol) gateway
│   └── opa_adapter.py            Open Policy Agent bridge
│
├── compliance/                   Compliance catalogue
│   └── catalogue.py              70 controls across 17 frameworks
│
├── telemetry/                    Observability
│   └── otel_exporter.py          OpenTelemetry trace/span export
│
└── api/                          REST API
    └── app.py                    Flask — 17 endpoints, built-in rate limiting
```

---

## 14. Performance Characteristics

| Metric | Typical | P99 | Notes |
|---|---|---|---|
| Full pipeline latency | 0.10–0.11 ms | 0.18–0.22 ms | In-memory, no I/O |
| With SQLite audit write | < 2 ms | < 5 ms | WAL mode |
| Throughput (single thread) | 5,500 req/s | — | In-memory audit |
| Throughput (SQLite) | 200–600 req/s | — | Disk I/O bound |
| Throughput (10 threads) | 1,500–2,500 req/s | — | Lock-pooled contention |
| Throughput (PostgreSQL, 10 threads) | 5,000–10,000 req/s | — | Parallel writes |

See [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md) for full tuning guide.

---

## 15. Thread-Safety Model

```mermaid
%%{init: {'theme': 'neutral', 'flowchart': {'curve': 'linear'}, 'themeVariables': {'fontFamily': 'Arial'}}}%%
flowchart LR
    classDef rlock  fill:#74b9ff,stroke:#0984e3,color:#000
    classDef lock   fill:#a29bfe,stroke:#6c5ce7,color:#fff
    classDef pool   fill:#00b894,stroke:#00695c,color:#fff

    subgraph RLOCKS["threading.RLock  (re-entrant)"]
        R1["AnomalyDetector\n._stats\ncheck · update · reset"]:::rlock
        R2["PolicyEngine\n._policies\nregister · disable · evaluate"]:::rlock
        R3["GovernancePipeline\n._contracts\ncontract registry"]:::rlock
        R4["TenantRegistry\n._tenants\ncreate · lookup"]:::rlock
    end

    subgraph LOCKS["threading.Lock  (standard)"]
        L1["AuditLogger\n._records\nring buffer append"]:::lock
        L2["VelocityBreaker\n._ecosystem\ndeque operations"]:::lock
        L3["EventBus\n._handlers\nsubscribe · publish"]:::lock
        L4["GlassBoxLogger\n._loggers\ndouble-checked init"]:::lock
        L5["StageRegistry\n._latencies\nP50/P99 samples"]:::lock
    end

    subgraph POOL["Per-resource Lock Pool (8–16 locks)"]
        P1["AuditLogger\n._file_locks\nJSONL file writes\nhash(path) % pool_size"]:::pool
        P2["VelocityBreaker\n._windows\nper-agent sliding window"]:::pool
    end

    NOTE["process() and process_async()\nare stateless per-request —\nsafe for any concurrency level"]
```

---

## 16. Known Limitations & Open Engineering Items

See [README.md](../README.md#known-limitations--roadmap) for the full prioritised roadmap.

### Priority 1 — Pre-Production

| Item | 
#ll componen
-noqterprise features**: [FEATURES/enterprise.md](FEATURES/enterprise.md)
- **Troubleshooting**: [USER/troubleshooting.md](USER/troubleshooting.md)
- **Glossary**: [GLOSSARY.md](GLOSSARY.md)

---

*GlassBox v1.2.0 · Apache 2.0 · Mohammed Akbar Ansari*


