# Version Comparison

This repository currently maintains one active docs/code line (`v1.2.0`).

## Comparison Approach for Maintainers

When comparing behavior across revisions:

1. use git tags/commits
2. compare API route contracts (`glassbox/api/app.py`)
3. compare governance behavior (`glassbox/governance/pipeline.py`)
4. compare test outcomes via batch summaries

## Why No Static Matrix Here

Static historical matrices drift quickly when legacy folders are removed or rebased. This page intentionally stays policy-focused and points to source-of-truth files.