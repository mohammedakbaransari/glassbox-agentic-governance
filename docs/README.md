# GlassBox Documentation Index

This directory contains the documentation for GlassBox's current codebase.

## How to Navigate

Start here based on your goal:

- Build/integrate quickly: [USER/quick_start.md](USER/quick_start.md)
- Understand internals: [ARCHITECTURE.md](ARCHITECTURE.md)
- Extend framework behavior: [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md)
- Operate in production: [DEPLOYMENT/README.md](DEPLOYMENT/README.md)
- Integrate over HTTP: [API/endpoint_reference.md](API/endpoint_reference.md)
- Harden security posture: [SECURITY/hardening.md](SECURITY/hardening.md)
- Verify a specific claim: [CLAIMS.md](CLAIMS.md)

## Documentation Structure

- [API/](API/) - endpoint contracts, auth/security patterns, API operations
- [USER/](USER/) - onboarding, scenarios, troubleshooting
- [DEVELOPMENT/](DEVELOPMENT/) - internals and extension implementation
- [DEPLOYMENT/](DEPLOYMENT/) - operational setup, tuning, and references
- [FEATURES/](FEATURES/) - deep dives for specific capabilities
- [COMPLIANCE/](COMPLIANCE/) - standards mapping and evidence concepts
- [SECURITY/](SECURITY/) - threat-aware deployment and controls
- [OPERATIONS/](OPERATIONS/) - SLOs, alerting, and incident runbooks
- [PROCESSES/](PROCESSES/) - contribution/review process docs
- [SEARCH/](SEARCH/) - topic index support
- [CLAIMS.md](CLAIMS.md) - every documented claim, its code, and its test

## Update Discipline

When code changes, update docs in the same change set:

- API behavior: update both `glassbox/api/README.md` and `docs/API/endpoint_reference.md`
- Decision-service stages/policies: update `docs/ARCHITECTURE.md` and `docs/DEVELOPMENT/*`
- Test workflow changes: update `CONTRIBUTING.md` and user/dev command snippets
- New module capability: add or update the corresponding `glassbox/<module>/README.md`
- Any capability claim: add or update its row in `CLAIMS.md`

## Source of Truth Pointers

- Application orchestration: `glassbox/app/decision_service.py`, `glassbox/app/composition.py`
- Domain rules: `glassbox/domain/`
- HTTP entry point: `glassbox/adapters/inbound/http/app.py`
- Compliance control catalogue: `glassbox/compliance/catalogue.py`
- Test orchestration: `python -m pytest tests -q`

