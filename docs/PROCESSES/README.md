# Processes and Workflows

This section defines repository-owned engineering practices. Deployment teams
remain responsible for their own release approvals, incident severity model,
on-call coverage, communication channels, and service-level objectives.

## Documentation Style

Use the [documentation style guide](style_guide.md) for structure, terminology,
links, examples, and review expectations.

## Development Workflow

1. Create a focused branch from `main`.
2. Implement the smallest complete change.
3. Add or update tests for changed behavior.
4. Run the relevant focused tests, then the repository quality gates.
5. Update user, operator, API, and claims documentation where behavior changed.
6. Open a pull request that explains the change, risk, and validation performed.

The enforced coverage floor is 80%, as configured in `pyproject.toml`. Coverage
is a release signal, not a substitute for testing failure modes, concurrency,
tenant isolation, and evidence integrity.

## Review Checklist

- [ ] The behavior and trust-boundary impact are clear.
- [ ] Tests cover the success path and relevant failure paths.
- [ ] Format, lint, type, architecture, security, and test gates pass.
- [ ] Public interfaces and compatibility implications are identified.
- [ ] Documentation and claim-to-test citations are current.
- [ ] Performance-sensitive changes include measurements where appropriate.
- [ ] No secret, credential, or unsafe production default was introduced.

## Quality Gates

The authoritative commands and versions are defined in `pyproject.toml` and
`.github/workflows/ci.yml`. The principal gates are:

- formatting with Black and isort;
- linting with Ruff and pylint;
- type checking with mypy;
- architecture checks with import-linter and `tests/test_layering.py`;
- security checks with Bandit, pip-audit, dependency review, and secret scanning;
- the full pytest suite, including the 80% coverage threshold;
- claims, packaging, and lockfile validation.

Do not treat deployment as a pull-request quality gate. Release and production
promotion are separate, environment-owned processes.

## Incident Workflow

1. Detect the condition through telemetry, an alert, or a user report.
2. Classify it using the deployment's severity model and the
   [diagnostic matrix](../USER/troubleshooting.md#quick-diagnostic-matrix).
3. Determine whether the event is a governance denial, dependency outage,
   integrity failure, or uncertain target-system outcome.
4. Mitigate it using the matching [operations runbook](../OPERATIONS/README.md).
   Never bypass fail-closed controls merely to restore throughput.
5. Restore the affected dependency or configuration and validate the original
   invariant before reopening traffic.
6. If evidence or dispatch was involved, verify the evidence segment and
   reconcile the dispatch ledger with the target system.
7. Record the root cause, affected decision or segment identifiers, corrective
   actions, and follow-up tests.

## Deployment-Team Responsibilities

Each deployment should define and maintain:

- severity levels, escalation paths, and on-call coverage;
- response and recovery objectives based on measured behavior;
- incident communication channels and status-page procedures;
- release approval, rollback, and disaster-recovery processes;
- regular backup, failover, key-rotation, and evidence-integrity exercises;
- operational metrics such as latency, error rate, denial rate, recovery time,
  deployment frequency, and change failure rate.

The repository provides technical controls and reference runbooks. It does not
prescribe a particular team structure, meeting cadence, paging product, or SLA.

## Related Documentation

- [Contributing](../../CONTRIBUTING.md)
- [Development](../DEVELOPMENT/README.md)
- [Deployment](../DEPLOYMENT/README.md)
- [Operations](../OPERATIONS/README.md)
- [Security](../SECURITY/README.md)


