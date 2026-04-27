# Development & Architecture

This directory contains technical documentation for developers, architects, and contributors.

## 📖 Contents

### Architecture & Design
- **[architecture.md](architecture.md)** - System design overview
  - Components and their interactions
  - Data flow and pipelines
  - Decision evaluation stages
  - Thread-safety and concurrency model
  - Integration points

### Implementation Guide
- **[implementation_guide.md](implementation_guide.md)** - How to extend GlassBox
  - Adding custom policies
  - Creating custom adapters
  - Extending the decision pipeline
  - Integration patterns

### Deployment Guide
- **[guide.md](guide.md)** - Production deployment
  - Installation steps
  - Configuration management
  - Performance tuning
  - Monitoring setup

## 🏗️ System Architecture

### 9-Stage Pipeline (+ 2 Security Pre-checks)

Every `DecisionRequest` passes through these steps in order. Any step can **block** execution; subsequent steps are skipped.

| Step | Name | Module | Blocks On |
|------|------|--------|-----------|
| Pre-1 | Security Sanitization | `security/sanitizer.py` | SQL/XSS/SSTI/path-traversal in payload or `agent_id` |
| Pre-2 | Agent-ID Sanitization | `security/sanitizer.py` | Unicode homoglyphs, null bytes |
| 0 | AgentContract Validation | `governance/pipeline.py` | Unauthorised `decision_type`, amount limit exceeded |
| 1 | Context Capture | `governance/context_capture.py` | — (enrichment only) |
| 2 | Schema Validation | `governance/schema_validator.py` | Missing/wrong-type required fields |
| 3 | Velocity Breaker | `governance/velocity_breaker.py` | Per-agent > 100 req/min; ecosystem limit |
| 4 | Anomaly Detection | `governance/anomaly_detector.py` | Z-score > 3σ after min_samples |
| 5 | Policy Enforcement | `governance/policy_engine.py` | Any registered policy returns `fail` |
| 6 | Risk Evaluation | `governance/risk_evaluator.py` | Composite risk score routing |
| 7 | Disposition + Finalise | `governance/pipeline.py` | WAL + audit persist + EventBus publish |

**Disposition thresholds:** risk ≤ 35 → `AUTO_EXECUTE` · 36–70 → `HUMAN_REVIEW` · > 70 → `BLOCK`

### Key Components

| Component | Purpose | Location |
|-----------|---------|----------|
| `GovernancePipeline` | Main 9-stage orchestrator | `governance/pipeline.py` |
| `PolicyEngine` | 35 built-in policies + custom registry | `governance/policy_engine.py` |
| `VelocityBreaker` | Per-agent + fleet-wide rate limiting | `governance/velocity_breaker.py` |
| `AnomalyDetector` | Welford Z-score baselines (O(1)) | `governance/anomaly_detector.py` |
| `RiskEvaluator` | Weighted composite risk scoring (0–100) | `governance/risk_evaluator.py` |
| `AuditLogger` | Lock-pooled ring buffer + JSONL rotation | `governance/audit_logger.py` |
| `TamperEvidentAuditLogger` | SHA-256 hash-chained immutable audit | `governance/advanced_audit.py` |
| `WriteAheadLog` | Crash-safe two-phase side-effect tracking | `governance/write_ahead_log.py` |
| `EventBus` | Domain events (async handlers, webhooks) | `events/event_bus.py` |
| `GlassBoxDB` / `Repository` | SQLite/PostgreSQL/SQL Server persistence | `store/` |
| `AccessController` | Enterprise RBAC with role hierarchy | `governance/access_control.py` |
| `TenantRegistry` | Strict multi-tenant isolation | `governance/multitenancy.py` |

## 👨‍💻 Developer Workflows

### Adding a Custom Policy

1. Understand existing policies in PolicyEngine
2. Create policy function: `(payload, context) → PolicyEvaluation`
3. Register: `engine.register(Policy("ID", "Name", [types], rule_func))`
4. Test with unit tests
5. Deploy to policy repository

### Creating an Adapter

1. Extend `BaseAdapter` class
2. Implement platform-specific initialization
3. Override execution method
4. Add tests for your platform
5. Document in [../FEATURES/](../FEATURES/)

### Extending the Pipeline

1. Create custom stage class
2. Integrate at appropriate point
3. Add to GovernancePipeline
4. Document behavior
5. Add performance tests

## 🔧 Development Setup

```bash
# Clone repository
git clone https://github.com/mohammedakbaransari/glassbox-agentic-governance
cd glassbox-agentic-governance

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install in development mode
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run validation
python scripts/validate.py
```

## 📦 Project Structure

```
glassbox/
├── governance/          # Core decision pipeline (32 modules)
├── adapters/           # Platform-specific integrations
├── api/                # REST API layer
├── authoring/          # Policy authoring tools
├── compliance/         # Compliance tracking
├── events/             # Event bus system
├── integrations/       # External service integrations
├── orchestration/      # Workflow orchestration
├── security/           # Security & sanitization
├── store/              # Decision persistence
├── telemetry/          # Monitoring & metrics
└── workflow/           # Workflow definitions
```

## 🧪 Testing

### Test Categories

- **Unit tests** - Individual component tests
- **Integration tests** - Multi-component workflows
- **Performance tests** - Benchmark suite
- **Security tests** - Input validation, injection prevention
- **Regression tests** - Catch regressions between versions

### Running Tests

```bash
# Recommended full-suite path: isolated batch harness
python scripts/run_test_batches.py

# List available batches
python scripts/run_test_batches.py --list-batches

# Rerun only failed batches from a previous run
python scripts/run_test_batches.py --rerun-failed-from test-results/<run-id>/summary.json

# Rerun failed batches from the latest recorded run in the output root
python scripts/run_test_batches.py --rerun-failed-latest

# Run only one batch
python scripts/run_test_batches.py --batch governance

# Run all shard batches for governance and exclude full-suite batches
python scripts/run_test_batches.py --tag governance --tag shard

# Run only security-related batches
python scripts/run_test_batches.py --tag security

# Emit a one-line CI summary instead of the human-readable report
python scripts/run_test_batches.py --ci-summary

# CI summary mode now emits both BATCH_RUN and RUN_ANALYSIS_SUMMARY lines
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --max-workers 2 --ci-summary

# Emit only the CI-oriented run analysis line
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --max-workers 2 --ci-analysis-summary

# Fail CI if plan-vs-actual drift exceeds your tolerance
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --max-workers 2 --ci-analysis-summary --max-order-changes 0 --max-runner-changes 0

# Use history-aware scheduling to start with the longest known batches
python scripts/run_test_batches.py --schedule longest-first

# Preview the selected execution order without running anything
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --plan

# Emit the same preview as JSON for automation or CI wrappers
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --plan-json

# Write the JSON execution plan directly to a file
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --plan-json-file artifacts/plan.json

# Preview expected worker-lane assignment for parallel-safe batches
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --max-workers 2 --plan

# Emit a compact single-line preview for CI logs
python scripts/run_test_batches.py --tag governance --tag shard --schedule longest-first --max-workers 2 --plan-summary

# Raw pytest still works for direct local debugging
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_core.py -v

# Specific test class
python -m pytest tests/test_core.py::TestPolicyEngine -v

# With coverage
python -m pytest tests/ --cov=glassbox --cov-report=html
```

The batch runner writes one artifact directory per run under `test-results/harness-shards/<run-id>/` (all test results consolidated into `test-results/`).
Each batch gets its own `stdout.txt`, `stderr.txt`, `junit.xml`, and `batch.json`, and the run root contains `execution_plan.json`, `execution_plan.txt`, `run_analysis.json`, `run_analysis_summary.txt`, `run_analysis.txt`, `summary.json`, `summary.txt`, `latest.json`, and `history.json` for aggregation-friendly reporting and rerun workflows.
The manifest also supports `tags`, so large file-level suites can coexist with class-level shard batches and be selected without editing commands.
When `history.json` exists, `--schedule longest-first` or `--schedule shortest-first` can reorder batches using recorded durations rather than static manifest order.
Use `--plan` to preview the exact post-filter, post-scheduling execution order before starting a run.
Use `--plan-summary` when CI logs need one compact line instead of the full text preview.
Use `--plan-json` when a script or CI job needs that same execution plan in a machine-readable form.
Use `--plan-json-file` when automation should persist the plan directly to disk instead of capturing stdout.
When `--max-workers` is greater than 1, plan output also shows the expected worker lane for parallel-safe batches.
Actual runs persist that resolved execution plan into the run directory so post-run analysis can compare planned order with observed results.
The human-readable summary now includes a compact comparison line, while `run_analysis.json`, `run_analysis_summary.txt`, and `run_analysis.txt` expose plan-versus-actual ordering for CI and downstream tooling.

## Performance Optimization

Key performance optimizations in v1.2.0:

| Optimization | Impact | Location |
|--------------|--------|----------|
| Lock pooling (audit) | 95% contention reduction | `audit_logger.py` |
| Welford's algorithm (anomaly) | O(n) → O(1) stats | `anomaly_detector.py` |
| Snapshot pattern (policy) | Eliminate deep copies during eval | `policy_engine.py` |
| DistributedFleetBudgetPolicy | Redis INCRBYFLOAT — multi-replica fleet budget | `velocity_breaker.py` |
| DistributedAnomalyDetector | Redis Lua Welford — shared baselines | `anomaly_detector.py` |
| BoundedQueue | Backpressure-safe async audit writes | `bounded_queue.py` |
| StageRegistry P50/P99 | Per-stage latency in `/health` endpoint | `stage_registry.py` |
| PolicyParameterStore | Runtime threshold updates — no restart | `policy_parameters.py` |

## 📊 Design Patterns

### Used in GlassBox

- **Pipeline Pattern** - 9-stage decision processing
- **Strategy Pattern** - Pluggable policies and adapters
- **Observer Pattern** - Event-driven architecture
- **Circuit Breaker** - Velocity limiting & fallbacks
- **Decorator Pattern** - Middleware-style stages
- **Snapshot Pattern** - Lightweight data views

## 🔐 Security Considerations

1. **Input Validation** - All payloads sanitized
2. **Thread Safety** - All shared state protected by locks
3. **Authentication** - API key validation on all endpoints
4. **Audit Trail** - All decisions recorded immutably
5. **Error Handling** - No sensitive data in errors
6. **Rate Limiting** - Velocity breaker prevents abuse
7. **Encryption** - Optional for sensitive data
8. **RBAC** - Role-based access control

## 📚 Related Documentation

- **Architecture deep-dive**: [architecture.md](architecture.md)
- **Implementation guide**: [implementation_guide.md](implementation_guide.md)
- **Deployment**: [guide.md](guide.md)
- **API**: [../API/endpoint_reference.md](../API/endpoint_reference.md)
- **Features**: [../FEATURES/](../FEATURES/)

## 🤝 Contributing

See main [CONTRIBUTING.md](../../CONTRIBUTING.md) for:
- Code style guidelines
- PR process
- Testing requirements
- Documentation standards

## 📝 Code Quality

- ✅ Type hints on all public APIs
- ✅ Docstrings on all classes/methods
- ✅ 80% minimum test coverage enforced in CI (`fail_under = 80`)
- ✅ No hard-coded values (use config)
- ✅ Thread-safe by default
- ✅ Zero mandatory dependencies

## 🐛 Common Development Issues

| Issue | Solution |
|-------|----------|
| Import errors | Check PYTHONPATH, ensure in .venv |
| Tests fail | Run `pip install -e ".[dev]"` |
| Performance slow | Profile with cProfile, check contention |
| Redis unavailable | Velocity breaker uses local fallback |


