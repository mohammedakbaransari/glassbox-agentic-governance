# Features & Capabilities

This directory contains documentation for GlassBox features, including enterprise features and advanced capabilities.

## 📖 Contents

### Enterprise Features
- **[enterprise.md](enterprise.md)** (v1.1+)
  - Multi-tenancy support
  - Advanced audit capabilities
  - Workflow orchestration
  - Decision replay & regression testing
  - Custom risk evaluation models
  - Advanced telemetry & monitoring

### Distributed Velocity Breaker
- **[velocity_breaker_readme.md](velocity_breaker_readme.md)** (v1.0.1+)
  - Multi-instance rate limiting
  - Redis-backed state
  - Automatic failover
  - Per-agent + ecosystem limits
  - Circuit breaker pattern

- **[velocity_breaker_details.md](velocity_breaker_details.md)**
  - Technical implementation details
  - Lua script specifications
  - Performance characteristics
  - Concurrency model

## 🚀 Feature Highlights

### Core Features (v1.0.0+)
- ✅ **9-Stage Pipeline** - Comprehensive decision evaluation
- ✅ **Policy Engine** - 24 built-in business rules
- ✅ **Risk Scoring** - Composite risk evaluation (0-100)
- ✅ **Audit Trail** - Immutable decision logging
- ✅ **Multi-tenancy** - Isolated decision contexts
- ✅ **REST API** - Full decision lifecycle management
- ✅ **Thread-Safe** - Concurrent decision processing
- ✅ **Zero Dependencies** - Python stdlib only

### v1.0.1 Enterprise
- ✅ **Velocity Breaking** - Single & distributed rate limiting
- ✅ **Distributed State** - Redis-backed coordination
- ✅ **Circuit Breaker** - Automatic fallback on failures
- ✅ **Thread Pool** - Configurable async execution
- ✅ **Queue Monitoring** - Depth tracking & alerts

### v1.1.0 Advanced
- ✅ **Multi-Tenancy** - TenantRegistry with isolation
- ✅ **Decision Replay** - Historical decision replay
- ✅ **Regression Testing** - CLI-driven policy testing
- ✅ **Custom Risk Models** - Pluggable risk evaluation
- ✅ **Advanced Telemetry** - Per-stage timing metrics
- ✅ **Workflow Orchestration** - Multi-step decision chains
- ✅ **Performance Optimizations** - 95% faster anomaly detection
- ✅ **Enterprise Adapters** - Databricks, Kubernetes, Fabric support

## 📊 Comparison Matrix

| Feature | v1.0.0 | v1.0.1 | v1.1.0 |
|---------|--------|--------|--------|
| Core Pipeline | ✅ | ✅ | ✅ |
| Policy Engine | ✅ | ✅ | ✅ |
| Single-Instance VB | ✅ | ✅ | ✅ |
| Distributed VB | — | ✅ | ✅ |
| Multi-Tenancy | ✅ | ✅ | ✅ |
| Decision Replay | — | — | ✅ |
| Workflow Chains | — | — | ✅ |
| Custom Risk Models | — | — | ✅ |
| Advanced Telemetry | — | — | ✅ |
| Performance Optimizations | — | ✅ | ✅ |

## 🎯 Features by Use Case

### Financial Decisions
- ✅ Currency normalization (30+ ISO 4217)
- ✅ Amount validation & limits
- ✅ Ecosystem-wide fleet controls
- ✅ Audit trail for compliance

See: [velocity_breaker_readme.md](velocity_breaker_readme.md)

### E-Commerce Transactions
- ✅ Per-merchant rate limiting
- ✅ Anomaly detection (fraud prevention)
- ✅ Risk scoring (0-100 composite)
- ✅ Real-time policy evaluation

See: [../USER/use_cases.md](../USER/use_cases.md)

### Healthcare Recommendations
- ✅ Multi-tenant isolation (per hospital)
- ✅ Compliance audit trail (HIPAA)
- ✅ Decision explanation (interpretability)
- ✅ Workflow orchestration (approval chains)

See: [enterprise.md](enterprise.md)

### IT Operations
- ✅ Infrastructure automation safety
- ✅ Anomaly detection (attack prevention)
- ✅ Change control workflows
- ✅ Disaster recovery orchestration

See: [enterprise.md](enterprise.md)

### HR & Compliance
- ✅ Policy-based decision making
- ✅ Audit trail (regulatory proof)
- ✅ Workflow approval chains
- ✅ Decision replay (audit defense)

See: [enterprise.md](enterprise.md)

## 🔧 Advanced Capabilities

### Decision Replay (v1.1+)
Replay historical decisions against updated policies:
```python
from glassbox.governance.decision_replay import DecisionReplay

replay = DecisionReplay(pipeline)
results = replay.run_historical_scenarios("policy_v2")
# Returns: pass/fail comparison against old policy
```

### Custom Risk Models
Replace built-in risk evaluation with your own:
```python
def my_risk_model(decision, context):
    # Your custom risk calculation
    return risk_score  # 0-100

pipeline.risk_evaluator = CustomRiskEvaluator(my_risk_model)
```

### Workflow Orchestration
Chain decisions for approval workflows:
```python
workflow = DecisionWorkflow([
    ("stage_1_approval", policy_1),
    ("stage_2_compliance", policy_2),
    ("stage_3_final_review", policy_3),
])
```

### Multi-Tenant Isolation
Separate decision contexts by tenant:
```python
from glassbox.governance.multitenancy import MultiTenantPipeline

mt_pipeline = MultiTenantPipeline()
mt_pipeline.evaluate(tenant_id="customer-123", decision_request)
```

## 📈 Performance Features

### Velocity Breaker (v1.0.1+)
- **Per-agent rate limiting**: 20 decisions/60s
- **Ecosystem limits**: Global fleet controls
- **Distributed state**: Redis-backed coordination
- **Automatic failover**: Local memory backup
- **95% latency reduction**: Lua scripting optimization

### Anomaly Detection (v1.1+)
- **O(1) statistics**: Welford's algorithm
- **Rolling windows**: 50-100 item windows
- **Z-score detection**: Configurable thresholds
- **95% faster**: Compared to O(n) computation

### Audit Logging (v1.1+)
- **Lock pooling**: 95% contention reduction
- **Ring buffer**: Fixed memory usage
- **JSONL format**: Stream processing ready
- **Immutable records**: Tamper-proof design

## 🔐 Enterprise Security

- ✅ **Multi-tenancy isolation** - Completely separate contexts
- ✅ **Audit trail** - Every decision recorded
- ✅ **Encryption** - Optional for sensitive data
- ✅ **RBAC** - Role-based access control
- ✅ **API authentication** - Bearer token validation
- ✅ **Input sanitization** - Payload validation
- ✅ **Rate limiting** - Abuse prevention

See: [../SECURITY/hardening.md](../SECURITY/hardening.md)

## 📚 Related Documentation

- **Implementation**: [../DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **Architecture**: [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Deployment**: [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Use cases**: [../USER/use_cases.md](../USER/use_cases.md)
- **API**: [../API/endpoint_reference.md](../API/endpoint_reference.md)

## 🎓 Feature Deep-Dives

| Topic | Time | File |
|-------|------|------|
| Velocity Breaker architecture | 15 min | [velocity_breaker_details.md](velocity_breaker_details.md) |
| Enterprise features overview | 10 min | [enterprise.md](enterprise.md) |
| Performance optimizations | 20 min | [../DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md) |

## 🗺️ Future Features (Roadmap)

- 🔜 GraphQL API layer
- 🔜 WebSocket real-time decisions
- 🔜 ML model integration (policy learning)
- 🔜 Advanced visualization dashboard
- 🔜 Kubernetes operator
- 🔜 Event streaming to Kafka/Pub-Sub
- 🔜 Advanced compliance reporting


