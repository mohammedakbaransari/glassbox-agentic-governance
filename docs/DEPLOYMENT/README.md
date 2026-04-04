# Deployment & Operations

This directory contains guides for deploying, configuring, and operating GlassBox in production.

## 📖 Contents

### Getting Started
- **[guide.md](guide.md)** - Deployment step-by-step
  - System requirements
  - Installation procedures
  - Configuration management
  - Verification & testing

### Performance Tuning
- **[performance_tuning.md](performance_tuning.md)**
  - Performance monitoring
  - Bottleneck identification
  - Optimization techniques
  - Benchmark results

### Deployment Reference
- **[deployment_reference.md](deployment_reference.md)**
  - Platform-specific deployments
  - Configuration options
  - Environment variables
  - Scaling considerations

## 🚀 Deployment Paths

### Development
```
Local Machine / Docker
├── Single instance
├── SQLite database
├── Local logging
└── No Redis required (uses fallback)
```

### Testing/Staging
```
Kubernetes or VM
├── Multiple replicas
├── PostgreSQL database
├── Redis for distributed state
├── Comprehensive logging
└── Monitoring enabled
```

### Production
```
Enterprise Platform
├── Kubernetes / VM cluster
├── HA database (PostgreSQL/Cloud)
├── Redis cluster
├── Multiple replicas
├── Full monitoring/alerting
├── Disaster recovery
└── Compliance audit trail
```

## 🏗️ Architecture Deployment Models

### Single Instance
Best for: Development, small deployments
```
Client → GlassBox (single)
         ├→ Policy Engine
         ├→ Velocity Breaker (local)
         └→ Database (SQLite)
```

### Multi-Instance with Distributed VB
Best for: Production, high throughput
```
Load Balancer
    ├→ GlassBox-1 ┐
    ├→ GlassBox-2 ├→ Redis (shared state)
    └→ GlassBox-3 ┘      ↓
                    Policy Store
                    Decision Store
```

### Kubernetes Multi-Tenant
Best for: Enterprise, multi-customer
```
Ingress Controller
    ├→ Pod 1 ────┐
    ├→ Pod 2 ────┼→ Redis Cluster
    └→ Pod 3 ────┤
              ConfigMap (policies)
              Secret (credentials)
```

## 📋 Pre-Deployment Checklist

- [ ] System meets requirements (Python 3.9+, memory, CPU)
- [ ] Dependencies installed (optional: flask, redis, pyyaml)
- [ ] Database configured and tested
- [ ] Redis configured (if distributed VB used)
- [ ] Environment variables set
- [ ] SSL certificates ready (if HTTPS required)
- [ ] Policies defined and tested
- [ ] Backup strategy documented
- [ ] Monitoring tools configured
- [ ] Team trained on operations

## 🔧 System Requirements

### Minimum (Single Instance)
- **CPU**: 2 cores
- **RAM**: 2 GB
- **Storage**: 10 GB (50+ GB for audit logs)
- **Network**: 100 Mbps
- **Python**: 3.9, 3.10, 3.11, 3.12

### Recommended (Production)
- **CPU**: 8+ cores
- **RAM**: 16 GB+
- **Storage**: 500 GB+ (SSD recommended)
- **Network**: 1 Gbps
- **Database**: PostgreSQL 12+
- **Cache**: Redis 6.0+
- **Load Balancer**: 2+ instances behind LB

### Enterprise (High Scale)
- **Kubernetes cluster**: 3+ nodes
- **CPU per node**: 16+ cores
- **RAM per node**: 64 GB+
- **Database cluster**: 3+ nodes (HA)
- **Redis cluster**: 3+ nodes (HA)
- **Network**: 10 Gbps interconnect
- **CDN**: For distributed deployments

## 📊 Configuration Examples

### Development Config
```env
GLASSBOX_ENV=development
GLASSBOX_LOG_LEVEL=DEBUG
GLASSBOX_DATABASE_URL=sqlite:///decisions.db
GLASSBOX_VELOCITY_BREAKER=local
```

### Production Config
```env
GLASSBOX_ENV=production
GLASSBOX_LOG_LEVEL=INFO
GLASSBOX_DATABASE_URL=postgresql://user:pass@db-cluster:5432/glassbox
GLASSBOX_REDIS_URL=redis://redis-cluster:6379/0
GLASSBOX_VELOCITY_BREAKER=distributed
GLASSBOX_MAX_WORKERS=32
GLASSBOX_AUDIT_RETENTION=90d
```

### Enterprise Config
```env
GLASSBOX_ENV=production
GLASSBOX_LOG_LEVEL=WARNING
GLASSBOX_DATABASE_URL=postgresql://...
GLASSBOX_REDIS_CLUSTER=redis-cluster:6379
GLASSBOX_MULTITENANCY=enabled
GLASSBOX_AUDIT_ENCRYPTION=enabled
GLASSBOX_COMPLIANCE_MODE=HIPAA
GLASSBOX_MAX_TENANTS=100
GLASSBOX_WORKERS_PER_TENANT=4
```

## 🔍 Monitoring & Observability

### Key Metrics
- **Decision throughput** - Decisions/second
- **Pipeline latency** - P50, P95, P99
- **Policy evaluation rate** - ms per policy
- **Velocity breaker hits** - Rate-limited requests
- **Anomaly detection** - Outliers detected
- **Audit log size** - GB/day

### Health Checks
```bash
# System health
curl http://localhost:8000/api/v1/health

# Metrics
curl http://localhost:8000/api/v1/metrics

# Status
curl http://localhost:8000/api/v1/status
```

### Logging
- **glassbox.governance** - Core decision pipeline
- **glassbox.api** - API layer
- **glassbox.security** - Security events
- **glassbox.audit** - Decision audit trail

## 🚨 Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| High latency | Blocked policy evaluation | Check [performance_tuning.md](performance_tuning.md) |
| Memory leak | Unbounded cache growth | Restart service, check settings |
| Redis unavailable | Connection issues | VB uses local fallback |
| Database full | Audit logs grow | Configure retention policy |
| Rate limiting | Too many requests | Check velocity_breaker config |

See [../USER/troubleshooting.md](../USER/troubleshooting.md) for more.

## 📈 Scaling Guidelines

### Vertical Scaling (bigger machine)
- Add CPU → faster policy evaluation
- Add RAM → larger caches
- SSD storage → faster audit logging

### Horizontal Scaling (more machines)
- Load balance traffic
- Share Redis state
- Coordinate policies
- Distributed decision store

## 🔐 Production Security

1. **Network** - TLS/SSL for all communication
2. **Auth** - API key validation on endpoints
3. **Audit** - All decisions logged immutably
4. **Secrets** - Use env vars, not config files
5. **Encryption** - Optional field-level encryption
6. **Updates** - Regular security patches
7. **Compliance** - Maintain audit trail for regulators

See: [../SECURITY/hardening.md](../SECURITY/hardening.md)

## 🔄 Disaster Recovery

### Backup Strategy
- Daily database backups
- Weekly full system snapshots
- Monthly archive to S3/Cloud
- Off-site backup replication

### Recovery
- RPO (Recovery Point Objective): 1 hour
- RTO (Recovery Time Objective): 30 minutes
- Test recovery quarterly
- Document runbooks

## 🚀 Deployment Orchestration

### Docker
```bash
docker build -t glassbox:1.1.0 .
docker run -e GLASSBOX_ENV=production glassbox:1.1.0
```

### Kubernetes
```bash
kubectl apply -f k8s/glassbox-deployment.yaml
kubectl autoscale deployment glassbox --min=2 --max=10
```

### Terraform (Infrastructure as Code)
```hcl
module "gla ssbox" {
  source = "terraform-glassbox"
  version = "1.1.0"
  environment = "production"
  min_replicas = 2
  max_replicas = 10
}
```

## 📚 Related Documentation

- **Getting started**: [guide.md](guide.md)
- **Performance**: [performance_tuning.md](performance_tuning.md)
- **Configuration**: [deployment_reference.md](deployment_reference.md)
- **Security**: [../SECURITY/hardening.md](../SECURITY/hardening.md)
- **Compliance**: [../COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)


