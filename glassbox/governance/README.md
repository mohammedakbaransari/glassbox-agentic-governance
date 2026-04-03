# glassbox/governance — Core Governance Engine

The `governance` package contains the 9-stage pipeline and all stage components.

| Module | Role |
|---|---|
| `pipeline.py` | `GovernancePipeline` — the central orchestrator |
| `models.py` | All dataclasses and enums (DecisionRequest, AuditRecord, …) |
| `policy_engine.py` | Thread-safe policy registry + evaluator |
| `risk_evaluator.py` | Weighted composite risk scoring (0–100) |
| `anomaly_detector.py` | Z-score rolling baseline anomaly detection |
| `velocity_breaker.py` | Per-agent + ecosystem circuit breakers |
| `schema_validator.py` | Payload structure validation per decision type |
| `audit_logger.py` | In-memory ring buffer + JSONL file persistence |
| `decision_replay.py` | Sync + async + parallel batch replay |
| `retry_policy.py` | Sync + async retry with configurable backoff |
| `context_capture.py` | Platform-safe metadata enrichment |
| `logging_manager.py` | Structured JSON/text logging, GLASSBOX_LOG_LEVEL |
| `execution_trace.py` | Per-stage timing and outcome trace (opt-in) |
| `multitenancy.py` | TenantRegistry + MultiTenantPipeline context isolation |

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for pipeline diagrams.
