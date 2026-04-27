from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MANIFEST = _REPO_ROOT / "tests" / "batch_manifest.json"
_DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "test-results"
_SEQUENTIAL_PROFILES = {"isolated", "perf", "heavy-io"}


@dataclass(frozen=True)
class BatchSpec:
    name: str
    targets: List[str]
    profile: str = "standard"
    timeout_seconds: int = 300
    pytest_args: List[str] = field(default_factory=list)
    description: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class BatchResult:
    name: str
    status: str
    exit_code: Optional[int]
    duration_seconds: float
    started_at: str
    finished_at: str
    profile: str
    targets: List[str]
    stdout_path: str
    stderr_path: str
    junit_xml_path: str
    batch_json_path: str
    tests: Optional[int] = None
    failures: Optional[int] = None
    errors: Optional[int] = None
    skipped: Optional[int] = None
    timeout: bool = False
    failure_excerpt: str = ""
    planned_runner: str = ""
    observed_runner: str = ""
    raw_observed_runner: str = ""
    completion_order: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunSummary:
    run_id: str
    started_at: str
    finished_at: str
    duration_seconds: float
    scheduling_strategy: str
    manifest_path: str
    output_dir: str
    selected_batches: List[str]
    passed_batches: int
    failed_batches: int
    timed_out_batches: int
    total_tests: int
    total_failures: int
    total_errors: int
    total_skipped: int
    failed_batch_names: List[str]
    batch_results: List[BatchResult]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["batch_results"] = [result.to_dict() for result in self.batch_results]
        return data


@dataclass(frozen=True)
class PlannedBatch:
    name: str
    profile: str
    targets: List[str]
    tags: List[str]
    execution_group: str
    planned_runner: str
    estimated_duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionPlan:
    manifest_path: str
    output_root: str
    scheduling_strategy: str
    max_workers: int
    parallel_worker_count: int
    selected_batches: List[str]
    sequential_batch_names: List[str]
    parallel_batch_names: List[str]
    batch_results: List[PlannedBatch]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["batch_results"] = [batch.to_dict() for batch in self.batch_results]
        return data


def load_manifest(manifest_path: Optional[Path] = None) -> List[BatchSpec]:
    path = Path(manifest_path or _DEFAULT_MANIFEST)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        BatchSpec(
            name=item["name"],
            targets=list(item["targets"]),
            profile=item.get("profile", "standard"),
            timeout_seconds=int(item.get("timeout_seconds", 300)),
            pytest_args=list(item.get("pytest_args", [])),
            description=item.get("description", ""),
            tags=list(item.get("tags", [])),
        )
        for item in payload.get("batches", [])
    ]


def select_batches(
    batches: Sequence[BatchSpec],
    include: Optional[Iterable[str]] = None,
    exclude: Optional[Iterable[str]] = None,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
) -> List[BatchSpec]:
    include_set = set(include or [])
    exclude_set = set(exclude or [])
    include_tag_set = set(include_tags or [])
    exclude_tag_set = set(exclude_tags or [])
    selected = []
    for batch in batches:
        if include_set and batch.name not in include_set:
            continue
        if batch.name in exclude_set:
            continue
        batch_tags = set(batch.tags)
        if include_tag_set and not include_tag_set.issubset(batch_tags):
            continue
        if exclude_tag_set and batch_tags.intersection(exclude_tag_set):
            continue
        selected.append(batch)
    return selected


def rerun_failed_batches(previous_summary_path: Path, batches: Sequence[BatchSpec]) -> List[BatchSpec]:
    payload = json.loads(Path(previous_summary_path).read_text(encoding="utf-8"))
    failed_names = {
        item["name"]
        for item in payload.get("batch_results", [])
        if item.get("status") not in {"passed"}
    }
    return [batch for batch in batches if batch.name in failed_names]


def resolve_latest_summary(output_root: Path = _DEFAULT_OUTPUT_ROOT) -> Optional[Path]:
    latest_path = Path(output_root) / "latest.json"
    if not latest_path.exists():
        return None
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    summary_path = payload.get("summary_json")
    if not summary_path:
        return None
    resolved = Path(summary_path)
    return resolved if resolved.exists() else None


def load_duration_history(output_root: Path = _DEFAULT_OUTPUT_ROOT) -> Dict[str, float]:
    history_path = Path(output_root) / "history.json"
    if not history_path.exists():
        return {}
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    durations: Dict[str, List[float]] = {}
    for run in payload.get("runs", []):
        for batch_name, batch_data in run.get("batch_durations", {}).items():
            duration = batch_data.get("duration_seconds")
            if isinstance(duration, (int, float)):
                durations.setdefault(batch_name, []).append(float(duration))

    return {
        batch_name: round(sum(values) / len(values), 3)
        for batch_name, values in durations.items()
        if values
    }


def schedule_batches(
    batches: Sequence[BatchSpec],
    *,
    output_root: Path = _DEFAULT_OUTPUT_ROOT,
    strategy: str = "manifest",
) -> List[BatchSpec]:
    if strategy == "manifest":
        return list(batches)

    known_durations = load_duration_history(output_root)

    def sort_key(batch: BatchSpec) -> tuple[float, str]:
        duration = known_durations.get(batch.name)
        if duration is None:
            if strategy == "longest-first":
                return (float("inf"), batch.name)
            return (float("inf"), batch.name)
        if strategy == "longest-first":
            return (-duration, batch.name)
        return (duration, batch.name)

    return sorted(batches, key=sort_key)


def prepare_batches(
    *,
    manifest_path: Optional[Path] = None,
    include_batches: Optional[Iterable[str]] = None,
    exclude_batches: Optional[Iterable[str]] = None,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    rerun_failed_from: Optional[Path] = None,
    output_root: Path = _DEFAULT_OUTPUT_ROOT,
    scheduling_strategy: str = "manifest",
) -> tuple[Path, List[BatchSpec]]:
    manifest = Path(manifest_path or _DEFAULT_MANIFEST)
    batches = load_manifest(manifest)
    if rerun_failed_from:
        batches = rerun_failed_batches(Path(rerun_failed_from), batches)
    else:
        batches = select_batches(batches, include_batches, exclude_batches, include_tags, exclude_tags)
    batches = schedule_batches(batches, output_root=Path(output_root), strategy=scheduling_strategy)
    return manifest, batches


def build_execution_plan(
    *,
    manifest_path: Optional[Path] = None,
    include_batches: Optional[Iterable[str]] = None,
    exclude_batches: Optional[Iterable[str]] = None,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    rerun_failed_from: Optional[Path] = None,
    output_root: Path = _DEFAULT_OUTPUT_ROOT,
    scheduling_strategy: str = "manifest",
    max_workers: int = 1,
) -> ExecutionPlan:
    manifest, batches = prepare_batches(
        manifest_path=manifest_path,
        include_batches=include_batches,
        exclude_batches=exclude_batches,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        rerun_failed_from=rerun_failed_from,
        output_root=output_root,
        scheduling_strategy=scheduling_strategy,
    )
    return _build_execution_plan_from_batches(
        manifest,
        batches,
        output_root=output_root,
        scheduling_strategy=scheduling_strategy,
        max_workers=max_workers,
    )


def _build_execution_plan_from_batches(
    manifest: Path,
    batches: Sequence[BatchSpec],
    *,
    output_root: Path,
    scheduling_strategy: str,
    max_workers: int,
) -> ExecutionPlan:
    known_durations = load_duration_history(output_root)
    planned_runners = _assign_planned_runners(batches, known_durations, max_workers=max_workers)
    planned_batches = [
        PlannedBatch(
            name=batch.name,
            profile=batch.profile,
            targets=list(batch.targets),
            tags=list(batch.tags),
            execution_group="sequential" if batch.profile in _SEQUENTIAL_PROFILES else "parallel",
            planned_runner=planned_runners[batch.name],
            estimated_duration_seconds=known_durations.get(batch.name),
        )
        for batch in batches
    ]
    return ExecutionPlan(
        manifest_path=str(manifest),
        output_root=str(Path(output_root)),
        scheduling_strategy=scheduling_strategy,
        max_workers=max(max_workers, 1),
        parallel_worker_count=max(1, min(max(max_workers, 1), len([batch for batch in batches if batch.profile not in _SEQUENTIAL_PROFILES]))) if any(batch.profile not in _SEQUENTIAL_PROFILES for batch in batches) else 0,
        selected_batches=[batch.name for batch in batches],
        sequential_batch_names=[batch.name for batch in batches if batch.profile in _SEQUENTIAL_PROFILES],
        parallel_batch_names=[batch.name for batch in batches if batch.profile not in _SEQUENTIAL_PROFILES],
        batch_results=planned_batches,
    )


def _assign_planned_runners(
    batches: Sequence[BatchSpec],
    known_durations: Dict[str, float],
    *,
    max_workers: int,
) -> Dict[str, str]:
    runner_map: Dict[str, str] = {}
    worker_count = max(max_workers, 1)

    for batch in batches:
        if batch.profile in _SEQUENTIAL_PROFILES:
            runner_map[batch.name] = "sequential"

    parallel_batches = [batch for batch in batches if batch.profile not in _SEQUENTIAL_PROFILES]
    if not parallel_batches:
        return runner_map

    if worker_count <= 1:
        for batch in parallel_batches:
            runner_map[batch.name] = "worker-1"
        return runner_map

    lane_loads = [0.0 for _ in range(worker_count)]
    for batch in parallel_batches:
        lane_index = min(range(worker_count), key=lambda idx: (lane_loads[idx], idx))
        runner_map[batch.name] = f"worker-{lane_index + 1}"
        lane_loads[lane_index] += known_durations.get(batch.name, 0.0)

    return runner_map


def create_run_directory(output_root: Path = _DEFAULT_OUTPUT_ROOT, run_id: Optional[str] = None) -> Path:
    resolved_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(output_root) / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def execute_batch(
    batch: BatchSpec,
    *,
    run_dir: Path,
    planned_runner: str = "",
    repo_root: Path = _REPO_ROOT,
    python_executable: str = sys.executable,
    extra_pytest_args: Optional[Sequence[str]] = None,
) -> BatchResult:
    batch_dir = run_dir / batch.name
    batch_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = batch_dir / "stdout.txt"
    stderr_path = batch_dir / "stderr.txt"
    junit_path = batch_dir / "junit.xml"
    batch_json_path = batch_dir / "batch.json"

    started = datetime.now(timezone.utc)
    started_at = started.isoformat()
    command = [
        python_executable,
        "-m",
        "pytest",
        *batch.targets,
        "--tb=short",
        "-rA",
        f"--junitxml={junit_path}",
        *batch.pytest_args,
        *(extra_pytest_args or []),
    ]

    exit_code: Optional[int] = None
    status = "failed"
    timed_out = False
    stdout_text = ""
    stderr_text = ""
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=batch.timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
        exit_code = completed.returncode
        stdout_text = completed.stdout
        stderr_text = completed.stderr
        status = "passed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        status = "timed_out"
        stdout_text = exc.stdout or ""
        stderr_text = exc.stderr or ""

    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")

    finished = datetime.now(timezone.utc)
    current_thread = threading.current_thread()
    raw_observed_runner = "sequential" if current_thread.name == "MainThread" else current_thread.name
    result = BatchResult(
        name=batch.name,
        status=status,
        exit_code=exit_code,
        duration_seconds=round((finished - started).total_seconds(), 3),
        started_at=started_at,
        finished_at=finished.isoformat(),
        profile=batch.profile,
        targets=batch.targets,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        junit_xml_path=str(junit_path),
        batch_json_path=str(batch_json_path),
        timeout=timed_out,
        failure_excerpt=_build_failure_excerpt(stdout_text, stderr_text),
        planned_runner=planned_runner,
        observed_runner=raw_observed_runner,
        raw_observed_runner=raw_observed_runner,
    )

    counts = _parse_junit_counts(junit_path)
    if counts:
        result.tests = counts["tests"]
        result.failures = counts["failures"]
        result.errors = counts["errors"]
        result.skipped = counts["skipped"]

    batch_json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def run_batches(
    *,
    manifest_path: Optional[Path] = None,
    include_batches: Optional[Iterable[str]] = None,
    exclude_batches: Optional[Iterable[str]] = None,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    rerun_failed_from: Optional[Path] = None,
    output_root: Path = _DEFAULT_OUTPUT_ROOT,
    run_id: Optional[str] = None,
    max_workers: int = 1,
    fail_fast: bool = False,
    scheduling_strategy: str = "manifest",
    python_executable: str = sys.executable,
    repo_root: Path = _REPO_ROOT,
    extra_pytest_args: Optional[Sequence[str]] = None,
) -> RunSummary:
    manifest, batches = prepare_batches(
        manifest_path=manifest_path,
        include_batches=include_batches,
        exclude_batches=exclude_batches,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        rerun_failed_from=rerun_failed_from,
        output_root=output_root,
        scheduling_strategy=scheduling_strategy,
    )

    run_dir = create_run_directory(output_root=Path(output_root), run_id=run_id)
    plan = _build_execution_plan_from_batches(
        manifest,
        batches,
        output_root=Path(output_root),
        scheduling_strategy=scheduling_strategy,
        max_workers=max_workers,
    )
    _write_execution_plan_artifacts(run_dir, plan)
    started = datetime.now(timezone.utc)
    results: List[BatchResult] = []
    planned_runner_by_name = {batch.name: batch.planned_runner for batch in plan.batch_results}

    sequential = [batch for batch in batches if batch.profile in _SEQUENTIAL_PROFILES]
    parallel = [batch for batch in batches if batch.profile not in _SEQUENTIAL_PROFILES]

    for batch in sequential:
        result = execute_batch(
            batch,
            run_dir=run_dir,
            planned_runner=planned_runner_by_name.get(batch.name, "sequential"),
            repo_root=repo_root,
            python_executable=python_executable,
            extra_pytest_args=extra_pytest_args,
        )
        result.completion_order = len(results) + 1
        results.append(result)
        if fail_fast and result.status != "passed":
            return _finalize_summary(started, manifest, run_dir, batches, results, scheduling_strategy=scheduling_strategy)

    if parallel:
        if max_workers <= 1:
            for batch in parallel:
                result = execute_batch(
                    batch,
                    run_dir=run_dir,
                    planned_runner=planned_runner_by_name.get(batch.name, "worker-1"),
                    repo_root=repo_root,
                    python_executable=python_executable,
                    extra_pytest_args=extra_pytest_args,
                )
                result.completion_order = len(results) + 1
                results.append(result)
                if fail_fast and result.status != "passed":
                    break
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        execute_batch,
                        batch,
                        run_dir=run_dir,
                        planned_runner=planned_runner_by_name.get(batch.name, f"worker-1"),
                        repo_root=repo_root,
                        python_executable=python_executable,
                        extra_pytest_args=extra_pytest_args,
                    ): batch
                    for batch in parallel
                }
                for future in as_completed(future_map):
                    result = future.result()
                    result.completion_order = len(results) + 1
                    results.append(result)
                    if fail_fast and result.status != "passed":
                        break

    return _finalize_summary(started, manifest, run_dir, batches, results, scheduling_strategy=scheduling_strategy)


def render_summary_text(summary: RunSummary) -> str:
    analysis = build_run_analysis(summary)
    lines = [
        f"Run ID: {summary.run_id}",
        f"Output: {summary.output_dir}",
        (
            f"Batches: {len(summary.batch_results)} total, "
            f"{summary.passed_batches} passed, "
            f"{summary.failed_batches} failed, "
            f"{summary.timed_out_batches} timed out"
        ),
        (
            f"Tests: {summary.total_tests} total, "
            f"{summary.total_failures} failures, "
            f"{summary.total_errors} errors, "
            f"{summary.total_skipped} skipped"
        ),
        f"Duration: {summary.duration_seconds:.3f}s",
        f"Schedule: {summary.scheduling_strategy}",
        (
            f"Comparison: planned={','.join(analysis['planned_order']) or 'n/a'} "
            f"observed={','.join(analysis['observed_completion_order']) or 'n/a'} "
            f"order_changes={analysis['order_changes']} runner_changes={analysis['runner_changes']}"
        ),
        "",
        "Batch Results:",
    ]
    for result in sorted(summary.batch_results, key=lambda item: item.name):
        lines.append(
            (
                f"- {result.name}: {result.status} in {result.duration_seconds:.3f}s "
                f"(runner={result.observed_runner or 'n/a'}, planned={result.planned_runner or 'n/a'}, completion={result.completion_order or 'n/a'}, "
                f"tests={result.tests if result.tests is not None else 'n/a'}, "
                f"failures={result.failures if result.failures is not None else 'n/a'})"
            )
        )
        if result.failure_excerpt:
            lines.append(f"  excerpt: {result.failure_excerpt}")
    return "\n".join(lines)


def render_ci_summary(summary: RunSummary) -> str:
    status = "PASS" if summary.failed_batches == 0 and summary.timed_out_batches == 0 else "FAIL"
    return (
        f"BATCH_RUN status={status} run_id={summary.run_id} "
        f"schedule={summary.scheduling_strategy} "
        f"batches={len(summary.batch_results)} passed={summary.passed_batches} "
        f"failed={summary.failed_batches} timed_out={summary.timed_out_batches} "
        f"tests={summary.total_tests} failures={summary.total_failures} errors={summary.total_errors} "
        f"skipped={summary.total_skipped} duration_s={summary.duration_seconds:.3f}"
    )


def render_run_analysis_summary(analysis: Dict[str, Any]) -> str:
    return (
        f"RUN_ANALYSIS_SUMMARY run_id={analysis['run_id']} "
        f"order_changes={analysis['order_changes']} runner_changes={analysis['runner_changes']} "
        f"planned={','.join(analysis['planned_order']) or 'n/a'} "
        f"observed={','.join(analysis['observed_completion_order']) or 'n/a'}"
    )


def render_execution_plan(plan: ExecutionPlan) -> str:
    lines = [
        f"Manifest: {plan.manifest_path}",
        f"Output root: {plan.output_root}",
        f"Schedule: {plan.scheduling_strategy}",
        f"Max workers: {plan.max_workers}",
        f"Batches: {len(plan.batch_results)} total",
        (
            f"Execution groups: {len(plan.sequential_batch_names)} sequential, "
            f"{len(plan.parallel_batch_names)} parallel"
        ),
        "",
        "Planned Order:",
    ]
    for index, batch in enumerate(plan.batch_results, start=1):
        estimate = (
            f"{batch.estimated_duration_seconds:.3f}s"
            if batch.estimated_duration_seconds is not None
            else "unknown"
        )
        tag_text = f" tags={','.join(batch.tags)}" if batch.tags else ""
        lines.append(
            (
                f"{index}. {batch.name} [{batch.execution_group}] runner={batch.planned_runner} profile={batch.profile} "
                f"estimate={estimate}{tag_text} -> {', '.join(batch.targets)}"
            )
        )
    return "\n".join(lines)


def render_plan_summary(plan: ExecutionPlan) -> str:
    known_estimates = [
        batch.estimated_duration_seconds
        for batch in plan.batch_results
        if batch.estimated_duration_seconds is not None
    ]
    estimated_total = sum(known_estimates)
    unknown_estimates = len(plan.batch_results) - len(known_estimates)
    order = ",".join(batch.name for batch in plan.batch_results)
    runners = ",".join(f"{batch.name}:{batch.planned_runner}" for batch in plan.batch_results)
    estimate_text = f"{estimated_total:.3f}" if known_estimates else "unknown"
    return (
        f"PLAN_SUMMARY schedule={plan.scheduling_strategy} max_workers={plan.max_workers} "
        f"parallel_workers={plan.parallel_worker_count} batches={len(plan.batch_results)} "
        f"sequential={len(plan.sequential_batch_names)} parallel={len(plan.parallel_batch_names)} "
        f"estimated_total_s={estimate_text} unknown_estimates={unknown_estimates} "
        f"order={order} runners={runners}"
    )


def build_run_analysis(summary: RunSummary) -> Dict[str, Any]:
    planned_order = list(summary.selected_batches)
    planned_positions = {name: index + 1 for index, name in enumerate(planned_order)}

    observed_results = sorted(
        summary.batch_results,
        key=lambda result: (
            result.completion_order if result.completion_order is not None else float("inf"),
            planned_positions.get(result.name, float("inf")),
            result.name,
        ),
    )
    observed_completion_order = [result.name for result in observed_results]
    observed_positions = {name: index + 1 for index, name in enumerate(observed_completion_order)}

    comparisons = []
    order_changes = 0
    runner_changes = 0
    for result in summary.batch_results:
        planned_position = planned_positions.get(result.name)
        observed_position = observed_positions.get(result.name)
        order_changed = planned_position != observed_position
        runner_changed = bool(result.planned_runner) and result.planned_runner != result.observed_runner
        if order_changed:
            order_changes += 1
        if runner_changed:
            runner_changes += 1
        comparisons.append(
            {
                "name": result.name,
                "planned_position": planned_position,
                "observed_completion_position": observed_position,
                "planned_runner": result.planned_runner,
                "observed_runner": result.observed_runner,
                "order_changed": order_changed,
                "runner_changed": runner_changed,
                "completion_order": result.completion_order,
                "duration_seconds": result.duration_seconds,
                "status": result.status,
            }
        )

    comparisons.sort(key=lambda item: (item["planned_position"] or float("inf"), item["name"]))
    return {
        "run_id": summary.run_id,
        "planned_order": planned_order,
        "observed_completion_order": observed_completion_order,
        "order_changes": order_changes,
        "runner_changes": runner_changes,
        "comparisons": comparisons,
    }


def render_run_analysis_text(analysis: Dict[str, Any]) -> str:
    lines = [
        render_run_analysis_summary(analysis),
        "",
        "Comparisons:",
    ]
    for comparison in analysis["comparisons"]:
        lines.append(
            (
                f"- {comparison['name']}: planned_pos={comparison['planned_position']} "
                f"observed_pos={comparison['observed_completion_position']} planned_runner={comparison['planned_runner'] or 'n/a'} "
                f"observed_runner={comparison['observed_runner'] or 'n/a'} order_changed={comparison['order_changed']} "
                f"runner_changed={comparison['runner_changed']} status={comparison['status']} "
                f"duration_s={comparison['duration_seconds']:.3f}"
            )
        )
    return "\n".join(lines)


def _finalize_summary(
    started: datetime,
    manifest: Path,
    run_dir: Path,
    selected_batches: Sequence[BatchSpec],
    results: Sequence[BatchResult],
    scheduling_strategy: str = "manifest",
) -> RunSummary:
    finished = datetime.now(timezone.utc)
    _normalize_observed_runners(results)
    ordered_results = sorted(results, key=lambda item: item.name)
    summary = RunSummary(
        run_id=run_dir.name,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds(), 3),
        scheduling_strategy=scheduling_strategy,
        manifest_path=str(manifest),
        output_dir=str(run_dir),
        selected_batches=[batch.name for batch in selected_batches],
        passed_batches=sum(1 for result in ordered_results if result.status == "passed"),
        failed_batches=sum(1 for result in ordered_results if result.status == "failed"),
        timed_out_batches=sum(1 for result in ordered_results if result.status == "timed_out"),
        total_tests=sum(result.tests or 0 for result in ordered_results),
        total_failures=sum(result.failures or 0 for result in ordered_results),
        total_errors=sum(result.errors or 0 for result in ordered_results),
        total_skipped=sum(result.skipped or 0 for result in ordered_results),
        failed_batch_names=[result.name for result in ordered_results if result.status != "passed"],
        batch_results=list(ordered_results),
    )

    summary_json = run_dir / "summary.json"
    summary_txt = run_dir / "summary.txt"
    manifest_copy = run_dir / "manifest.json"
    _persist_batch_result_artifacts(ordered_results)
    summary_json.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
    summary_txt.write_text(render_summary_text(summary), encoding="utf-8")
    manifest_copy.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    _write_run_analysis_artifacts(run_dir, summary)
    _write_latest_pointer(run_dir.parent, summary)
    _append_history(run_dir.parent, summary)
    return summary


def _parse_junit_counts(junit_path: Path) -> Optional[Dict[str, int]]:
    if not junit_path.exists():
        return None
    try:
        root = ET.fromstring(junit_path.read_text(encoding="utf-8"))
    except ET.ParseError:
        return None

    if root.tag == "testsuites":
        suites = root.findall("testsuite")
        if not suites:
            suites = [root]
        tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
        failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
        errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
        skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    else:
        tests = int(root.attrib.get("tests", 0))
        failures = int(root.attrib.get("failures", 0))
        errors = int(root.attrib.get("errors", 0))
        skipped = int(root.attrib.get("skipped", 0))
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def _build_failure_excerpt(stdout_text: str, stderr_text: str, limit: int = 300) -> str:
    combined = (stderr_text or "").strip() or (stdout_text or "").strip()
    if not combined:
        return ""
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    excerpt = " | ".join(lines[-3:])
    return excerpt[:limit]


def _write_latest_pointer(output_root: Path, summary: RunSummary) -> None:
    latest_path = output_root / "latest.json"
    payload = {
        "run_id": summary.run_id,
        "execution_plan_json": str(Path(summary.output_dir) / "execution_plan.json"),
        "execution_plan_txt": str(Path(summary.output_dir) / "execution_plan.txt"),
        "run_analysis_json": str(Path(summary.output_dir) / "run_analysis.json"),
        "run_analysis_summary_txt": str(Path(summary.output_dir) / "run_analysis_summary.txt"),
        "run_analysis_txt": str(Path(summary.output_dir) / "run_analysis.txt"),
        "summary_json": str(Path(summary.output_dir) / "summary.json"),
        "summary_txt": str(Path(summary.output_dir) / "summary.txt"),
        "finished_at": summary.finished_at,
        "scheduling_strategy": summary.scheduling_strategy,
        "status": "passed" if summary.failed_batches == 0 and summary.timed_out_batches == 0 else "failed",
    }
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _append_history(output_root: Path, summary: RunSummary, max_entries: int = 50) -> None:
    history_path = output_root / "history.json"
    if history_path.exists():
        try:
            payload = json.loads(history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"runs": []}
    else:
        payload = {"runs": []}

    runs = list(payload.get("runs", []))
    runs.append(
        {
            "run_id": summary.run_id,
            "finished_at": summary.finished_at,
            "duration_seconds": summary.duration_seconds,
            "execution_plan_json": str(Path(summary.output_dir) / "execution_plan.json"),
            "execution_plan_txt": str(Path(summary.output_dir) / "execution_plan.txt"),
            "run_analysis_json": str(Path(summary.output_dir) / "run_analysis.json"),
            "run_analysis_summary_txt": str(Path(summary.output_dir) / "run_analysis_summary.txt"),
            "run_analysis_txt": str(Path(summary.output_dir) / "run_analysis.txt"),
            "scheduling_strategy": summary.scheduling_strategy,
            "passed_batches": summary.passed_batches,
            "failed_batches": summary.failed_batches,
            "timed_out_batches": summary.timed_out_batches,
            "total_tests": summary.total_tests,
            "summary_json": str(Path(summary.output_dir) / "summary.json"),
            "batch_durations": {
                result.name: {
                    "duration_seconds": result.duration_seconds,
                    "status": result.status,
                    "tests": result.tests,
                    "planned_runner": result.planned_runner,
                    "observed_runner": result.observed_runner,
                    "raw_observed_runner": result.raw_observed_runner,
                    "completion_order": result.completion_order,
                }
                for result in summary.batch_results
            },
        }
    )
    payload["runs"] = runs[-max_entries:]
    history_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_execution_plan_artifacts(run_dir: Path, plan: ExecutionPlan) -> None:
    execution_plan_json = run_dir / "execution_plan.json"
    execution_plan_txt = run_dir / "execution_plan.txt"
    execution_plan_json.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    execution_plan_txt.write_text(render_execution_plan(plan), encoding="utf-8")


def _write_run_analysis_artifacts(run_dir: Path, summary: RunSummary) -> None:
    run_analysis = build_run_analysis(summary)
    run_analysis_json = run_dir / "run_analysis.json"
    run_analysis_summary_txt = run_dir / "run_analysis_summary.txt"
    run_analysis_txt = run_dir / "run_analysis.txt"
    run_analysis_json.write_text(json.dumps(run_analysis, indent=2), encoding="utf-8")
    run_analysis_summary_txt.write_text(render_run_analysis_summary(run_analysis), encoding="utf-8")
    run_analysis_txt.write_text(render_run_analysis_text(run_analysis), encoding="utf-8")


def _normalize_observed_runners(results: Sequence[BatchResult]) -> None:
    runner_first_seen: Dict[str, str] = {}
    next_worker_id = 1
    ordered_results = sorted(
        results,
        key=lambda result: (
            result.started_at,
            result.name,
        ),
    )
    for result in ordered_results:
        raw_runner = result.raw_observed_runner or result.observed_runner or ""
        if not raw_runner or raw_runner == "sequential":
            result.observed_runner = "sequential"
            if raw_runner == "sequential":
                result.raw_observed_runner = "sequential"
            continue
        if raw_runner not in runner_first_seen:
            runner_first_seen[raw_runner] = f"worker-{next_worker_id}"
            next_worker_id += 1
        result.raw_observed_runner = raw_runner
        result.observed_runner = runner_first_seen[raw_runner]


def _persist_batch_result_artifacts(results: Sequence[BatchResult]) -> None:
    for result in results:
        Path(result.batch_json_path).write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")