from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from glassbox.testing.batch_runner import (
    _DEFAULT_MANIFEST,
    build_execution_plan,
    load_manifest,
    render_execution_plan,
    render_plan_summary,
    build_run_analysis,
    render_run_analysis_summary,
    render_ci_summary,
    render_summary_text,
    resolve_latest_summary,
    run_batches,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repository tests as isolated batches with file-based reporting.")
    parser.add_argument("--manifest", default=str(_DEFAULT_MANIFEST), help="Path to the batch manifest JSON file.")
    parser.add_argument("--batch", action="append", dest="include_batches", default=[], help="Batch name to include. Can be specified multiple times.")
    parser.add_argument("--exclude-batch", action="append", dest="exclude_batches", default=[], help="Batch name to exclude. Can be specified multiple times.")
    parser.add_argument("--tag", action="append", dest="include_tags", default=[], help="Require batches to include this tag. Can be specified multiple times.")
    parser.add_argument("--exclude-tag", action="append", dest="exclude_tags", default=[], help="Exclude batches carrying this tag. Can be specified multiple times.")
    parser.add_argument("--output-dir", default="test-results", help="Root directory for batch result artifacts.")
    parser.add_argument("--run-id", default=None, help="Optional run identifier. Defaults to a UTC timestamp.")
    parser.add_argument("--max-workers", type=int, default=1, help="Maximum workers for parallel-safe batches.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed batch.")
    parser.add_argument("--schedule", choices=["manifest", "longest-first", "shortest-first"], default="manifest", help="Batch scheduling strategy. History-aware modes use durations from history.json when available.")
    parser.add_argument("--rerun-failed-from", default=None, help="Path to a previous summary.json; reruns only failed batches.")
    parser.add_argument("--rerun-failed-latest", action="store_true", help="Rerun failed batches from the latest run recorded in the output root.")
    parser.add_argument("--list-batches", action="store_true", help="List the batches defined in the manifest and exit.")
    parser.add_argument("--plan", action="store_true", help="Preview the selected batches and execution order without running them.")
    parser.add_argument("--plan-summary", action="store_true", help="Emit a compact single-line execution plan summary without running tests.")
    parser.add_argument("--plan-json", action="store_true", help="Emit the selected execution plan as JSON without running tests.")
    parser.add_argument("--plan-json-file", default=None, help="Write the selected execution plan as JSON to the given file without running tests.")
    parser.add_argument("--ci-summary", action="store_true", help="Print a single-line CI-oriented summary after execution.")
    parser.add_argument("--ci-analysis-summary", action="store_true", help="Print only the one-line CI-oriented run analysis summary after execution.")
    parser.add_argument("--max-order-changes", type=int, default=None, help="Fail the command if observed completion order changes exceed this threshold.")
    parser.add_argument("--max-runner-changes", type=int, default=None, help="Fail the command if observed runner changes exceed this threshold.")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Extra pytest args, supplied after '--'.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest)

    if args.list_batches:
        for batch in load_manifest(manifest_path):
            tag_text = f" [{', '.join(batch.tags)}]" if batch.tags else ""
            print(f"{batch.name}: {batch.profile}{tag_text} -> {', '.join(batch.targets)}")
        return 0

    rerun_failed_from = None
    if args.rerun_failed_latest:
        rerun_failed_from = resolve_latest_summary(Path(args.output_dir))
        if rerun_failed_from is None:
            parser.error("No latest run metadata found under the requested output directory.")
    elif args.rerun_failed_from:
        rerun_failed_from = Path(args.rerun_failed_from)

    extra_pytest_args = list(args.pytest_args)
    if extra_pytest_args and extra_pytest_args[0] == "--":
        extra_pytest_args = extra_pytest_args[1:]

    selected_plan_modes = [
        bool(args.plan),
        bool(args.plan_summary),
        bool(args.plan_json),
        bool(args.plan_json_file),
    ]
    if sum(selected_plan_modes) > 1:
        parser.error("Choose exactly one plan output mode: --plan, --plan-summary, --plan-json, or --plan-json-file.")
    if args.ci_summary and args.ci_analysis_summary:
        parser.error("Choose either --ci-summary or --ci-analysis-summary, not both.")
    if args.max_order_changes is not None and args.max_order_changes < 0:
        parser.error("--max-order-changes must be non-negative.")
    if args.max_runner_changes is not None and args.max_runner_changes < 0:
        parser.error("--max-runner-changes must be non-negative.")

    if args.plan or args.plan_summary or args.plan_json or args.plan_json_file:
        plan = build_execution_plan(
            manifest_path=manifest_path,
            include_batches=args.include_batches,
            exclude_batches=args.exclude_batches,
            include_tags=args.include_tags,
            exclude_tags=args.exclude_tags,
            rerun_failed_from=rerun_failed_from,
            output_root=Path(args.output_dir),
            scheduling_strategy=args.schedule,
            max_workers=max(args.max_workers, 1),
        )
        if args.plan_json or args.plan_json_file:
            payload = json.dumps(plan.to_dict(), indent=2)
            if args.plan_json_file:
                plan_json_path = Path(args.plan_json_file)
                plan_json_path.parent.mkdir(parents=True, exist_ok=True)
                plan_json_path.write_text(payload, encoding="utf-8")
            if args.plan_json:
                print(payload)
            elif args.plan_json_file:
                print(f"Wrote execution plan JSON to {args.plan_json_file}")
        elif args.plan_summary:
            print(render_plan_summary(plan))
        else:
            print(render_execution_plan(plan))
        return 0

    summary = run_batches(
        manifest_path=manifest_path,
        include_batches=args.include_batches,
        exclude_batches=args.exclude_batches,
        include_tags=args.include_tags,
        exclude_tags=args.exclude_tags,
        rerun_failed_from=rerun_failed_from,
        output_root=Path(args.output_dir),
        run_id=args.run_id,
        max_workers=max(args.max_workers, 1),
        fail_fast=args.fail_fast,
        scheduling_strategy=args.schedule,
        python_executable=sys.executable,
        extra_pytest_args=extra_pytest_args,
    )
    if args.rerun_failed_latest and not summary.selected_batches:
        print("No failed batches were found in the latest recorded run.")
        return 0
    analysis = build_run_analysis(summary)
    if args.ci_summary:
        print(render_ci_summary(summary))
        print(render_run_analysis_summary(analysis))
    elif args.ci_analysis_summary:
        print(render_run_analysis_summary(analysis))
    else:
        print(render_summary_text(summary))

    exit_code = 0 if summary.failed_batches == 0 and summary.timed_out_batches == 0 else 1
    if args.max_order_changes is not None and analysis["order_changes"] > args.max_order_changes:
        exit_code = 1
    if args.max_runner_changes is not None and analysis["runner_changes"] > args.max_runner_changes:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())