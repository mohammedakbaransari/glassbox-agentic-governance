# GlassBox Documentation Index

This directory contains the maintained documentation for the active code line (`v1.2.0`).

## How to Navigate

Start here based on your goal:

- Build/integrate quickly: [USER/quick_start.md](USER/quick_start.md)
- Understand internals: [ARCHITECTURE.md](ARCHITECTURE.md)
- Extend framework behavior: [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md)
- Operate in production: [DEPLOYMENT/README.md](DEPLOYMENT/README.md)
- Integrate over HTTP: [API/endpoint_reference.md](API/endpoint_reference.md)
- Harden security posture: [SECURITY/hardening.md](SECURITY/hardening.md)

## Documentation Structure

- [API/](API/) - endpoint contracts, auth/security patterns, API operations
- [USER/](USER/) - onboarding, scenarios, troubleshooting
- [DEVELOPMENT/](DEVELOPMENT/) - internals and extension implementation
- [DEPLOYMENT/](DEPLOYMENT/) - operational setup, tuning, and references
- [FEATURES/](FEATURES/) - deep dives for specific capabilities
- [COMPLIANCE/](COMPLIANCE/) - standards mapping and evidence concepts
- [SECURITY/](SECURITY/) - threat-aware deployment and controls
- [PROCESSES/](PROCESSES/) - contribution/review process docs
- [VERSIONS/](VERSIONS/) - versioning policy for docs in this repo
- [SEARCH/](SEARCH/) - topic index support

## Update Discipline

When code changes, update docs in the same PR:

- API behavior: update both `glassbox/api/README.md` and `docs/API/endpoint_reference.md`
- Pipeline/stages/policies: update `docs/ARCHITECTURE.md` and `docs/DEVELOPMENT/*`
- Test workflow changes: update `CONTRIBUTING.md` and user/dev command snippets
- New module capability: add or update the corresponding `glassbox/<module>/README.md`

## Source of Truth Pointers

- Package/runtime version: `pyproject.toml`
- API implementation: `glassbox/api/app.py`
- Governance runtime behavior: `glassbox/governance/pipeline.py`
- Test orchestration: `scripts/run_test_batches.py`