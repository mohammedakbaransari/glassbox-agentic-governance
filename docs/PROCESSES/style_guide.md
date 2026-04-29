# Documentation Style Guide

This guide defines how to write and maintain documentation in this repository.

## Scope

Applies to:

- `README.md`
- `CONTRIBUTING.md`
- `docs/**/*.md`
- `glassbox/*/README.md`

## Core Principles

- Be accurate to current code, not historical assumptions.
- Prefer clarity over marketing language.
- Keep examples runnable or obviously marked as pseudocode.
- Avoid hardcoded counts that drift quickly (for example, exact test totals) unless automatically generated.

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

## Versioning Guidance

- Canonical version source is `pyproject.toml`.
- Avoid embedding legacy version matrices unless actively maintained.
- If version-specific docs are introduced, ensure each version folder is complete and link-checked.

## Link and Reference Rules

- Prefer relative links within docs.
- Every new/changed link must resolve in the repository.
- Avoid references to non-existent files (for example, removed changelog/version folders).
- For API behavior, keep `glassbox/api/README.md` and `docs/API/endpoint_reference.md` aligned.

## Consistency Rules

- Terminology should be consistent:
  - "governance pipeline"
  - "decision request/response"
  - "policy violations"
  - "pending review"
- Use the same heading capitalization style within a file.
- Keep list formatting consistent and single-level.

## Maintenance Checklist (Per Docs PR)

- [ ] Commands are valid for current repo layout.
- [ ] No stale file/test references.
- [ ] Version mentions match `pyproject.toml`.
- [ ] API docs match implemented routes.
- [ ] Related links resolve.
- [ ] Grammar/spelling pass completed.

## Suggested Validation Commands

```bash
# quick grep for obvious stale references
rg -n "runtime-decision-governance|CHANGELOG\.md|test_velocity_distributed\.py" docs glassbox README.md CONTRIBUTING.md

# enumerate markdown files
rg --files -g "*.md"
```

## Ownership

When code behavior changes, docs should be updated in the same PR by the author of the change.