# Deployment Guide (v1.2.0)

This guide is the practical runbook for getting GlassBox into a stable deployment.

## 1. Install

```bash
pip install -e .
# add optional extras as needed
# pip install -e .[api,yaml,crypto,redis,spark,authoring]
```

## 2. Verify Runtime and Tests

```bash
python -m pytest tests -q
python -m pytest tests --cov=glassbox --cov-report=term-missing
```

For structured batch execution and artifacts:

```bash
python scripts/run_test_batches.py
```

## 3. Start API Service

```bash
python -m glassbox.api.app
```

Validate health endpoints:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## 4. Configure Runtime

Important runtime controls:

- API host/port env vars
- max payload size control
- log level and log directory
- authentication mode and secret provisioning

Use environment-specific config management (Kubernetes secrets, vault, cloud parameter store).

## 5. Production Hardening

- place app behind reverse proxy/ingress
- enforce HTTPS
- enable authentication and route-level authorization
- apply ingress-level rate limits in addition to app-level limits
- configure persistent audit repository
- monitor `/health`, `/ready`, `/metrics`

## 6. Rollout Strategy

- deploy canary environment first
- replay representative decision traffic in staging
- compare block rates and latency against baseline
- promote only after policy and anomaly thresholds are verified

## 7. Incident Readiness

Keep runbooks for:

- spike in `blocked` decisions
- degraded audit persistence
- sudden latency increase in specific stages
- rate-limit saturation/false positives

## Related Docs

- [deployment_reference.md](deployment_reference.md)
- [performance_tuning.md](performance_tuning.md)
- [../API/endpoint_reference.md](../API/endpoint_reference.md)
- [../SECURITY/hardening.md](../SECURITY/hardening.md)