# Contributing to GlassBox

Thanks for contributing.

## Setup

```bash
git clone https://github.com/mohammedakbaransari/glassbox-agentic-governance
cd glassbox-agentic-governance
pip install -e .[dev]
```

If you do not need full dev extras:

```bash
pip install -e .
pip install pytest pytest-cov
```

## Development workflow

1. Create a branch from `main`.
2. Make your change with tests.
3. Run the test suite (or relevant subset).
4. Update documentation when behavior changes.
5. Open a pull request with a clear summary.

## Test commands

```bash
# Fast local run
python -m pytest tests -q

# With coverage
python -m pytest tests --cov=glassbox --cov-report=term-missing

# Batch harness (artifacts + scheduling support)
python scripts/run_test_batches.py
```

## Code quality expectations

- Keep Python 3.9+ compatibility.
- Add/maintain type hints for public interfaces.
- Add tests for new behavior and bug fixes.
- Avoid adding mandatory runtime dependencies to core.
- Preserve thread safety for mutable shared state.

## Documentation expectations

When behavior changes, update related docs in the same PR:

- root [README.md](README.md)
- [docs/API/endpoint_reference.md](docs/API/endpoint_reference.md) for API changes
- [docs/DEVELOPMENT/](docs/DEVELOPMENT/) for architecture/extension changes
- relevant module README under `glassbox/*/README.md`

## Commit messages

Conventional commit style is recommended:

- `feat(scope): description`
- `fix(scope): description`
- `docs(scope): description`
- `test(scope): description`
- `refactor(scope): description`

## Security reporting

Do not open public issues for vulnerabilities.

- Email: `security@glassbox.dev`
- Include: impact, reproduction steps, affected component, and suggested remediation if available

## License

By contributing, you agree your contributions are licensed under Apache 2.0.