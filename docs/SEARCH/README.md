# Documentation Search Index

Use this index for quick lookup across the GlassBox documentation. Start with
the [documentation index](../README.md) when navigating by audience or task.

## Search Strategy

The GlassBox documentation uses multiple search approaches:

### Documentation Index

Use [docs/README.md](../README.md) for role-based entry points and the current
documentation structure.

### Full-Text Search

From the repository root, use ripgrep:

```bash
rg -n -i "search term" docs
rg -n "pattern|variation" docs
```

### Category Indexes

- [API](../API/README.md)
- [User guides](../USER/README.md)
- [Development](../DEVELOPMENT/README.md)
- [Features](../FEATURES/README.md)
- [Deployment](../DEPLOYMENT/README.md)
- [Compliance](../COMPLIANCE/README.md)
- [Security](../SECURITY/README.md)
- [Processes](../PROCESSES/README.md)

---

## Keyword Index

### A
- **Adapters** → [../ARCHITECTURE.md](../ARCHITECTURE.md), [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **Admission Control** → [SECURITY/hardening.md](../SECURITY/hardening.md), [OPERATIONS/README.md](../OPERATIONS/README.md)
- **Anomaly Detection** → [../ARCHITECTURE.md](../ARCHITECTURE.md), [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **API Key** → [API/README.md](../API/README.md), [API/v2_endpoint_reference.md](../API/v2_endpoint_reference.md)
- **Approval Workflow** → [FEATURES/enterprise.md](../FEATURES/enterprise.md), [API/v2_endpoint_reference.md](../API/v2_endpoint_reference.md), [glassbox/workflow/README.md](../../glassbox/workflow/README.md)
- **Audit Trail** → [COMPLIANCE/README.md](../COMPLIANCE/README.md), [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Authentication** → [API/v2_endpoint_reference.md](../API/v2_endpoint_reference.md), [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Authorization** → [SECURITY/hardening.md](../SECURITY/hardening.md)

### B
- **Backup** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Benchmark** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Business Rules** → [USER/quick_start.md](../USER/quick_start.md), [FEATURES/README.md](../FEATURES/README.md)

### C
- **Circuit Breaker** → [FEATURES/velocity_breaker.md](../FEATURES/velocity_breaker.md)
- **CLI** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Compliance** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Configuration** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Containerization** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### D
- **Database** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Decision** → [../ARCHITECTURE.md](../ARCHITECTURE.md), [USER/quick_start.md](../USER/quick_start.md)
- **Decision Replay** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Deployment** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Disaster Recovery** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Distributed** → [FEATURES/velocity_breaker.md](../FEATURES/velocity_breaker.md)
- **Docker** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### E
- **Encryption** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Environment** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Error Handling** → [USER/troubleshooting.md](../USER/troubleshooting.md)
- **Example** → [USER/use_cases.md](../USER/use_cases.md)
- **Extension** → [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)

### F
- **Failover** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Financial** → [USER/use_cases.md](../USER/use_cases.md)

### G
- **GDPR** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Getting Started** → [USER/quick_start.md](../USER/quick_start.md)
- **GlassBox** → [USER/quick_start.md](../USER/quick_start.md)
- **Governance** → [../ARCHITECTURE.md](../ARCHITECTURE.md)
- **Guide** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### H
- **Health** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **HIPAA** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Horizontal Scaling** → [DEPLOYMENT/README.md](../DEPLOYMENT/README.md)

### I
- **Installation** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Integration** → [API/v2_endpoint_reference.md](../API/v2_endpoint_reference.md)
- **Internal** → [../ARCHITECTURE.md](../ARCHITECTURE.md)
- **Interpreter** → [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **ISO 27001** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)

### K
- **Kubernetes** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Key Management** → [SECURITY/hardening.md](../SECURITY/hardening.md)

### L
- **Learning Path** → [docs/README.md](../README.md)
- **Load Balancer** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Lock** → [../ARCHITECTURE.md](../ARCHITECTURE.md)
- **Logging** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### M
- **Metrics** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Migration** → [Architecture](../ARCHITECTURE.md), [claims](../CLAIMS.md)
- **Monitoring** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Multi-Tenant** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)

### N
- **Network** → [SECURITY/hardening.md](../SECURITY/hardening.md)

### O
- **Operations** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### P
- **PCI DSS** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Performance** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Pipeline** → [../ARCHITECTURE.md](../ARCHITECTURE.md)
- **Policy** → [USER/quick_start.md](../USER/quick_start.md), [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **PostgreSQL** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Profiling** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

### Q
- **Quick Start** → [USER/quick_start.md](../USER/quick_start.md)

### R
- **RBAC** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Redis** → [FEATURES/velocity_breaker.md](../FEATURES/velocity_breaker.md), [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Regression** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Release** → [PROCESSES/README.md](../PROCESSES/README.md)
- **Reliability** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Response Time** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Risk** → [../ARCHITECTURE.md](../ARCHITECTURE.md), [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Risk Threshold** → [FEATURES/enterprise.md](../FEATURES/enterprise.md), [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Rollback** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### S
- **Scenario** → [USER/use_cases.md](../USER/use_cases.md)
- **Scaling** → [DEPLOYMENT/README.md](../DEPLOYMENT/README.md)
- **Schema** → [../ARCHITECTURE.md](../ARCHITECTURE.md)
- **Secrets** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Security** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **SOC 2** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **SSL/TLS** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **State** → [FEATURES/velocity_breaker.md](../FEATURES/velocity_breaker.md)
- **Status** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### T
- **Telemetry** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Testing** → [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **Thread** → [../ARCHITECTURE.md](../ARCHITECTURE.md)
- **Tool Output Quarantine** → [SECURITY/README.md](../SECURITY/README.md), [OPERATIONS/README.md](../OPERATIONS/README.md)
- **Tenancy** → [GLOSSARY.md](../GLOSSARY.md), [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Throughput** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Timeout** → [USER/troubleshooting.md](../USER/troubleshooting.md)
- **TLS** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Troubleshoot** → [USER/troubleshooting.md](../USER/troubleshooting.md)
- **Tune** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

### U
- **Upgrade** → [Architecture](../ARCHITECTURE.md), [deployment](../DEPLOYMENT/README.md)
- **Use Case** → [USER/use_cases.md](../USER/use_cases.md)

### V
- **Velocity Breaker** → [FEATURES/velocity_breaker.md](../FEATURES/velocity_breaker.md)
- **Version** → [pyproject.toml](../../pyproject.toml)
- **Vertical Scaling** → [DEPLOYMENT/README.md](../DEPLOYMENT/README.md)

### W
- **Workflow** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)

### X-Z
- **YAML** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)

## Search Tips

- Prefer specific terms such as `risk threshold`, `outcome chain`, or
	`admission control`.
- Search [CLAIMS.md](../CLAIMS.md) when validating a guarantee.
- Search [GLOSSARY.md](../GLOSSARY.md) when terminology is unfamiliar.
- Check the related-document links at the end of each guide.
