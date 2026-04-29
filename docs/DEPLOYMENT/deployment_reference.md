# Deployment Reference (v1.2.0)

Reference-oriented deployment details aligned with current implementation.

## Runtime Baseline

- Python `>=3.9`
- Core package has zero mandatory dependencies
- Optional dependency groups in `pyproject.toml`

## Common Install Profiles

```bash
# Core
pip install -e .

# API + rules + crypto + redis + spark
pip install -e .[api,yaml,crypto,redis,spark]
```

## Verification Commands

```bash
python -m pytest tests -q
python -m pytest tests --cov=glassbox --cov-report=term-missing
python scripts/run_test_batches.py --ci-summary
```

## API Route Reference Snapshot

Current implemented routes:

- `GET /health`, `GET /ready`, `GET /metrics`, `GET /openapi.json`
- `POST /decisions`, `POST /decisions/simulate`, `POST /decisions/batch`
- `GET /decisions`, `GET /decisions/{decision_id}`
- `POST /decisions/{decision_id}/replay`
- `GET /stats`, `GET /policies`, `GET /contracts`, `GET /ecosystem`
- `GET /agents/{agent_id}/velocity`, `GET /agents/{agent_id}/anomaly`
- `GET /events/stream`

Canonical API contract: [../API/endpoint_reference.md](../API/endpoint_reference.md)

## Operational Controls

- request-size cap enforcement
- in-memory per-agent and per-IP rate limiting
- health/readiness/metrics endpoints for monitoring
- OpenAPI endpoint for contract synchronization

## Observability Recommendations

Collect and alert on:

- total decisions, block rate, p99 latency
- per-stage p50/p99 from pipeline health payload
- audit persist failures/queue depth
- rate-limit exceedance counts

## Security and Compliance Ops

- enforce authentication and authorization externally or via middleware
- rotate secrets/API keys on schedule
- retain audit records per policy/regulatory requirements
- verify policy changes through simulator/staging before production

## Deployment Exit Criteria

- [ ] tests passing in target environment
- [ ] health/readiness/metrics green
- [ ] security controls enabled (TLS + auth)
- [ ] alerts configured for latency and block-rate anomalies
- [ ] rollback plan validated