# Documentation Style Guide

This guide defines how to write and maintain documentation in this repository.

## Scope

Applies to:

- `README.md`
- `CONTRIBUTING.md`
- `docs/**/*.md`
- `glassbox/**/README.md`
- `examples/README.md`
- `sdk/**/README.md`

## Core Principles

- Be accurate to current code, not historical assumptions.
- Prefer clarity over marketing language.
- Keep examples runnable or obviously marked as pseudocode.
- Avoid hardcoded counts that drift quickly (for example, exact test totals) unless automatically generated.
- Distinguish current, legacy compatibility, adapter-available, and operator-owned behavior.
- Back public guarantees with code and executable tests in `docs/CLAIMS.md`.

## Standard Document Structure

Use this structure where it fits:

1. Purpose
2. Key modules/components
3. Quick start
4. Operational notes
5. Testing/validation
6. Related docs

For module READMEs, keep sections short and actionable.

## Writing Style

- Use plain, direct language.
- Use present tense for current behavior.
- Keep paragraphs short.
- Prefer bullet lists for procedures/checklists.
- Avoid emojis and decorative symbols.
- Use ASCII punctuation by default.

## Code and Command Snippets

- Wrap commands in fenced `bash` blocks.
- Wrap Python examples in fenced `python` blocks.
- Keep snippets minimal and realistic.
- Use `python -m ...` command style consistently.
- Do not include commands that reference missing files/tests.

## Version and Architecture Guidance

- Do not add release-history narratives or "as of vX.Y.Z" framing to general
  documentation.
- This repository is v2-only: the earlier synchronous implementation
  (`GovernancePipeline` and related packages) has been physically deleted
  (GB-040), not merely deprecated. Do not write documentation that implies a
  v1 path still exists to fall back to, except when explicitly documenting
  history (e.g., "this was deleted in GB-040").
- `glassbox/workflow` and `glassbox/store` are exceptions: they are the
  sanctioned `WorkflowGateway` backend, not legacy debt — do not label them
  "legacy" in new documentation.
- Canonical package version (for packaging/build purposes only) is
  `pyproject.toml`; it should not appear in prose documentation.

## Link and Reference Rules

- Prefer relative links within docs.
- Every new/changed link must resolve in the repository.
- Avoid references to non-existent files (for example, removed changelog/version folders).
- Keep `glassbox/adapters/inbound/http/README.md` and
  `docs/API/v2_endpoint_reference.md` aligned with the actual routes in
  `glassbox/adapters/inbound/http/app.py`.

## Consistency Rules

- Runtime terminology should be consistent: `DecisionService`, governed
  action, verified principal, mandate, decision effect, intent receipt, and
  require approval.
- Use the same heading capitalization style within a file.
- Keep list formatting consistent and single-level.

## Maintenance Checklist (Per Docs PR)

- [ ] Commands are valid for current repo layout.
- [ ] No stale file/test references.
- [ ] No version numbers, badges, or "vX.Y.Z" framing added to prose.
- [ ] API docs match implemented routes.
- [ ] Capability status and deployment responsibilities are explicit.
- [ ] New or changed guarantees are reflected in `CLAIMS.md`.
- [ ] Related links resolve.
- [ ] Grammar/spelling pass completed.

## Suggested Validation Commands

```bash
# Quick grep for obvious stale references (deleted packages/tests).
git grep -n -E "glassbox\.governance|glassbox\.security|glassbox\.api\.|GovernancePipeline" -- '*.md'

# Enumerate tracked Markdown files.
git ls-files '*.md'

# Verify claim citations.
python -m pytest tests/test_claims_coverage.py -q
```

## Ownership

When code behavior changes, docs should be updated in the same PR by the author of the change.