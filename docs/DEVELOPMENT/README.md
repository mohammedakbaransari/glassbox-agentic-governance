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

### 9-Stage Pipeline
GlassBox processes decisions through 9 sequential stages:

1. **Payload Sanitization** - Validate & clean input
2. **Schema Validation** - Check decision type schema
3. **Velocity Checking** - Per-agent rate limiting
4. **Anomaly Detection** - Statistical outlier detection
5. **Policy Evaluation** - Apply business rules
6. **Risk Scoring** - Calculate composite risk
7. **Context Enrichment** - Add contextual data
8. **Audit Logging** - Record all decisions
9. **Execution** - Execute or queue for review

### Key Components

| Component | Purpose | Location |
|-----------|---------|----------|
| GovernancePipeline | Main orchestrator | governance/ |
| PolicyEngine | Rule evaluation | governance/ |
| VelocityBreaker | Rate limiting | governance/ |
| AnomalyDetector | Statistical analysis | governance/ |
| RiskEvaluator | Risk scoring | governance/ |
| AuditLogger | Decision recording | governance/ |
| EventBus | System events | events/ |
| DecisionStore | Decision persistence | store/ |

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
├── governance/          # Core decision pipeline (35 modules)
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
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_core.py -v

# Specific test class
python -m pytest tests/test_core.py::TestPolicyEngine -v

# With coverage
python -m pytest tests/ --cov=glassbox --cov-report=html
```

## 🚀 Performance Optimization

Key performance optimizations in v1.1:

| Optimization | Impact | Location |
|--------------|--------|----------|
| Lock pooling (audit) | 95% contention reduction | audit_logger.py |
| Welford's algorithm (anomaly) | O(n) → O(1) stats | anomaly_detector.py |
| Snapshot pattern (policy) | Eliminate deep copies | policy_engine.py |
| Distributed velocity breaking | Multi-instance support | velocity_breaker.py |
| Redis caching | Sub-millisecond lookups | govenance/models.py |

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
- ✅ 90%+ test coverage target
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


