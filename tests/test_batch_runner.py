from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from glassbox.testing.batch_runner import (
    BatchResult,
    BatchSpec,
    ExecutionPlan,
    PlannedBatch,
    build_run_analysis,
    build_execution_plan,
    load_manifest,
    load_duration_history,
    render_execution_plan,
    render_plan_summary,
    render_run_analysis_summary,
    render_run_analysis_text,
    render_ci_summary,
    render_summary_text,
    resolve_latest_summary,
    run_batches,
    schedule_batches,
    select_batches,
)


class TestBatchRunner(unittest.TestCase):

    @staticmethod
    def _cli_script_path() -> Path:
        return Path(__file__).resolve().parents[1] / "scripts" / "run_test_batches.py"

    def test_load_manifest(self):
        manifest_path = Path(__file__).with_name("batch_manifest.json")
        batches = load_manifest(manifest_path)
        self.assertGreaterEqual(len(batches), 5)
        self.assertEqual(batches[0].name, "core-fast")

    def test_select_batches_include_and_exclude(self):
        batches = [
            BatchSpec(name="a", targets=["tests/test_a.py"], tags=["core"]),
            BatchSpec(name="b", targets=["tests/test_b.py"], tags=["core", "shard"]),
            BatchSpec(name="c", targets=["tests/test_c.py"], tags=["governance"]),
        ]
        selected = select_batches(batches, include=["a", "c"], exclude=["c"])
        self.assertEqual([batch.name for batch in selected], ["a"])

    def test_select_batches_by_tags(self):
        batches = [
            BatchSpec(name="a", targets=["tests/test_a.py"], tags=["core"]),
            BatchSpec(name="b", targets=["tests/test_b.py"], tags=["core", "shard"]),
            BatchSpec(name="c", targets=["tests/test_c.py"], tags=["governance", "shard"]),
        ]
        selected = select_batches(batches, include_tags=["core", "shard"])
        self.assertEqual([batch.name for batch in selected], ["b"])

        selected = select_batches(batches, include_tags=["shard"], exclude_tags=["governance"])
        self.assertEqual([batch.name for batch in selected], ["b"])

    def test_load_manifest_exposes_tags(self):
        manifest_path = Path(__file__).with_name("batch_manifest.json")
        batches = load_manifest(manifest_path)
        by_name = {batch.name: batch for batch in batches}
        self.assertIn("core", by_name["core-fast"].tags)
        self.assertIn("shard", by_name["governance-adapters-api"].tags)

    def test_render_summary_text(self):
        result = BatchResult(
            name="sample",
            status="passed",
            exit_code=0,
            duration_seconds=1.25,
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:01+00:00",
            profile="standard",
            targets=["tests/test_sample.py"],
            stdout_path="stdout.txt",
            stderr_path="stderr.txt",
            junit_xml_path="junit.xml",
            batch_json_path="batch.json",
            tests=3,
            failures=0,
            errors=0,
            skipped=0,
        )
        from glassbox.testing.batch_runner import RunSummary
        summary = RunSummary(
            run_id="rid",
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:01+00:00",
            duration_seconds=1.25,
            scheduling_strategy="manifest",
            manifest_path="manifest.json",
            output_dir="test-results/rid",
            selected_batches=["sample"],
            passed_batches=1,
            failed_batches=0,
            timed_out_batches=0,
            total_tests=3,
            total_failures=0,
            total_errors=0,
            total_skipped=0,
            failed_batch_names=[],
            batch_results=[result],
        )
        text = render_summary_text(summary)
        self.assertIn("Run ID: rid", text)
        self.assertIn("sample: passed", text)
        self.assertIn("Batches: 1 total", text)
        self.assertIn("Comparison: planned=sample observed=sample", text)

    def test_render_ci_summary(self):
        result = BatchResult(
            name="sample",
            status="failed",
            exit_code=1,
            duration_seconds=1.0,
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:01+00:00",
            profile="standard",
            targets=["tests/test_sample.py"],
            stdout_path="stdout.txt",
            stderr_path="stderr.txt",
            junit_xml_path="junit.xml",
            batch_json_path="batch.json",
            tests=3,
            failures=1,
            errors=0,
            skipped=0,
        )
        from glassbox.testing.batch_runner import RunSummary
        summary = RunSummary(
            run_id="rid",
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:01+00:00",
            duration_seconds=1.0,
            scheduling_strategy="manifest",
            manifest_path="manifest.json",
            output_dir="test-results/rid",
            selected_batches=["sample"],
            passed_batches=0,
            failed_batches=1,
            timed_out_batches=0,
            total_tests=3,
            total_failures=1,
            total_errors=0,
            total_skipped=0,
            failed_batch_names=["sample"],
            batch_results=[result],
        )
        text = render_ci_summary(summary)
        self.assertIn("status=FAIL", text)
        self.assertIn("failed=1", text)

    def test_run_batches_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tests_dir = root / "tests"
            tests_dir.mkdir(parents=True)
            test_file = tests_dir / "test_sample.py"
            test_file.write_text(
                "def test_ok():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "sample",
                                "targets": [str(test_file)],
                                "profile": "standard",
                                "timeout_seconds": 60
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            summary = run_batches(
                manifest_path=manifest_path,
                output_root=root / "results",
                run_id="testrun",
                python_executable=sys.executable,
                repo_root=root,
            )

            self.assertEqual(summary.passed_batches, 1)
            self.assertEqual(summary.total_tests, 1)
            self.assertEqual(summary.failed_batch_names, [])
            run_dir = root / "results" / "testrun"
            self.assertEqual(summary.scheduling_strategy, "manifest")
            self.assertTrue((run_dir / "summary.json").exists())
            self.assertTrue((run_dir / "summary.txt").exists())
            self.assertTrue((run_dir / "execution_plan.json").exists())
            self.assertTrue((run_dir / "execution_plan.txt").exists())
            self.assertTrue((run_dir / "run_analysis.json").exists())
            self.assertTrue((run_dir / "run_analysis_summary.txt").exists())
            self.assertTrue((run_dir / "run_analysis.txt").exists())
            self.assertTrue((run_dir / "sample" / "batch.json").exists())
            self.assertTrue((run_dir / "sample" / "stdout.txt").exists())
            self.assertTrue((run_dir / "sample" / "junit.xml").exists())
            self.assertTrue((root / "results" / "latest.json").exists())
            self.assertTrue((root / "results" / "history.json").exists())
            latest_summary = resolve_latest_summary(root / "results")
            self.assertEqual(latest_summary, run_dir / "summary.json")
            duration_history = load_duration_history(root / "results")
            self.assertIn("sample", duration_history)
            self.assertEqual(summary.batch_results[0].planned_runner, "worker-1")
            self.assertEqual(summary.batch_results[0].observed_runner, "sequential")
            self.assertEqual(summary.batch_results[0].raw_observed_runner, "sequential")
            self.assertEqual(summary.batch_results[0].completion_order, 1)
            execution_plan = json.loads((run_dir / "execution_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(execution_plan["selected_batches"], ["sample"])
            self.assertEqual(execution_plan["batch_results"][0]["planned_runner"], "worker-1")
            summary_payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["batch_results"][0]["observed_runner"], "sequential")
            self.assertEqual(summary_payload["batch_results"][0]["raw_observed_runner"], "sequential")
            self.assertEqual(summary_payload["batch_results"][0]["completion_order"], 1)
            analysis_payload = json.loads((run_dir / "run_analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis_payload["planned_order"], ["sample"])
            self.assertEqual(analysis_payload["observed_completion_order"], ["sample"])
            self.assertEqual(analysis_payload["order_changes"], 0)

    def test_rerun_failed_from_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tests_dir = root / "tests"
            tests_dir.mkdir(parents=True)
            passing = tests_dir / "test_pass.py"
            failing = tests_dir / "test_fail.py"
            passing.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            failing.write_text("def test_bad():\n    assert False\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {"name": "pass-batch", "targets": [str(passing)], "timeout_seconds": 60},
                            {"name": "fail-batch", "targets": [str(failing)], "timeout_seconds": 60},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            first = run_batches(
                manifest_path=manifest_path,
                output_root=root / "results",
                run_id="first",
                python_executable=sys.executable,
                repo_root=root,
            )
            self.assertEqual(first.failed_batch_names, ["fail-batch"])

            rerun = run_batches(
                manifest_path=manifest_path,
                rerun_failed_from=resolve_latest_summary(root / "results"),
                output_root=root / "results",
                run_id="second",
                python_executable=sys.executable,
                repo_root=root,
            )
            self.assertEqual(rerun.selected_batches, ["fail-batch"])

    def test_schedule_batches_uses_duration_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            history_path = root / "history.json"
            history_path.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "batch_durations": {
                                    "slow": {"duration_seconds": 5.0},
                                    "fast": {"duration_seconds": 1.0}
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batches = [
                BatchSpec(name="slow", targets=["tests/test_slow.py"]),
                BatchSpec(name="unknown", targets=["tests/test_unknown.py"]),
                BatchSpec(name="fast", targets=["tests/test_fast.py"]),
            ]

            longest = schedule_batches(batches, output_root=root, strategy="longest-first")
            self.assertEqual([batch.name for batch in longest], ["slow", "fast", "unknown"])

            shortest = schedule_batches(batches, output_root=root, strategy="shortest-first")
            self.assertEqual([batch.name for batch in shortest], ["fast", "slow", "unknown"])

    def test_build_execution_plan_respects_schedule_and_groups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "perf-batch",
                                "targets": ["tests/test_perf.py"],
                                "profile": "perf",
                                "tags": ["perf"]
                            },
                            {
                                "name": "core-batch",
                                "targets": ["tests/test_core.py"],
                                "profile": "standard",
                                "tags": ["core", "shard"]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "history.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "batch_durations": {
                                    "perf-batch": {"duration_seconds": 9.0},
                                    "core-batch": {"duration_seconds": 2.0}
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = build_execution_plan(
                manifest_path=manifest_path,
                output_root=root,
                scheduling_strategy="longest-first",
                max_workers=2,
            )

            self.assertEqual(plan.selected_batches, ["perf-batch", "core-batch"])
            self.assertEqual(plan.sequential_batch_names, ["perf-batch"])
            self.assertEqual(plan.parallel_batch_names, ["core-batch"])
            self.assertEqual(plan.max_workers, 2)
            self.assertEqual(plan.parallel_worker_count, 1)
            self.assertEqual(plan.batch_results[0].planned_runner, "sequential")
            self.assertEqual(plan.batch_results[1].planned_runner, "worker-1")
            self.assertEqual(plan.batch_results[0].estimated_duration_seconds, 9.0)

    def test_render_execution_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "core-batch",
                                "targets": ["tests/test_core.py"],
                                "profile": "standard",
                                "tags": ["core"]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = build_execution_plan(manifest_path=manifest_path, output_root=root)
            rendered = render_execution_plan(plan)
            self.assertIn("Planned Order:", rendered)
            self.assertIn("core-batch", rendered)
            self.assertIn("runner=worker-1", rendered)
            self.assertIn("estimate=unknown", rendered)

    def test_execution_plan_to_dict(self):
        plan = ExecutionPlan(
            manifest_path="manifest.json",
            output_root="test-results",
            scheduling_strategy="longest-first",
            max_workers=2,
            parallel_worker_count=1,
            selected_batches=["core-batch"],
            sequential_batch_names=[],
            parallel_batch_names=["core-batch"],
            batch_results=[
                PlannedBatch(
                    name="core-batch",
                    profile="standard",
                    targets=["tests/test_core.py"],
                    tags=["core", "shard"],
                    execution_group="parallel",
                    planned_runner="worker-1",
                    estimated_duration_seconds=1.5,
                )
            ],
        )

        payload = plan.to_dict()
        self.assertEqual(payload["scheduling_strategy"], "longest-first")
        self.assertEqual(payload["max_workers"], 2)
        self.assertEqual(payload["selected_batches"], ["core-batch"])
        self.assertEqual(payload["batch_results"][0]["name"], "core-batch")
        self.assertEqual(payload["batch_results"][0]["planned_runner"], "worker-1")
        self.assertEqual(payload["batch_results"][0]["estimated_duration_seconds"], 1.5)

    def test_render_plan_summary(self):
        plan = ExecutionPlan(
            manifest_path="manifest.json",
            output_root="test-results",
            scheduling_strategy="longest-first",
            max_workers=2,
            parallel_worker_count=2,
            selected_batches=["alpha", "beta"],
            sequential_batch_names=[],
            parallel_batch_names=["alpha", "beta"],
            batch_results=[
                PlannedBatch(
                    name="alpha",
                    profile="standard",
                    targets=["tests/test_alpha.py"],
                    tags=["core"],
                    execution_group="parallel",
                    planned_runner="worker-1",
                    estimated_duration_seconds=2.5,
                ),
                PlannedBatch(
                    name="beta",
                    profile="standard",
                    targets=["tests/test_beta.py"],
                    tags=["core"],
                    execution_group="parallel",
                    planned_runner="worker-2",
                    estimated_duration_seconds=None,
                ),
            ],
        )

        summary = render_plan_summary(plan)
        self.assertIn("PLAN_SUMMARY", summary)
        self.assertIn("estimated_total_s=2.500", summary)
        self.assertIn("unknown_estimates=1", summary)
        self.assertIn("order=alpha,beta", summary)
        self.assertIn("runners=alpha:worker-1,beta:worker-2", summary)

    def test_build_run_analysis_and_render_text(self):
        result_one = BatchResult(
            name="alpha",
            status="passed",
            exit_code=0,
            duration_seconds=2.0,
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:02+00:00",
            profile="standard",
            targets=["tests/test_alpha.py"],
            stdout_path="stdout-alpha.txt",
            stderr_path="stderr-alpha.txt",
            junit_xml_path="alpha.xml",
            batch_json_path="alpha.json",
            planned_runner="worker-1",
            observed_runner="worker-1",
            raw_observed_runner="ThreadPoolExecutor-0_1",
            completion_order=2,
        )
        result_two = BatchResult(
            name="beta",
            status="passed",
            exit_code=0,
            duration_seconds=1.0,
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:01+00:00",
            profile="standard",
            targets=["tests/test_beta.py"],
            stdout_path="stdout-beta.txt",
            stderr_path="stderr-beta.txt",
            junit_xml_path="beta.xml",
            batch_json_path="beta.json",
            planned_runner="worker-2",
            observed_runner="worker-2",
            raw_observed_runner="ThreadPoolExecutor-0_0",
            completion_order=1,
        )
        from glassbox.testing.batch_runner import RunSummary

        summary = RunSummary(
            run_id="rid",
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:00:02+00:00",
            duration_seconds=2.0,
            scheduling_strategy="longest-first",
            manifest_path="manifest.json",
            output_dir="test-results/rid",
            selected_batches=["alpha", "beta"],
            passed_batches=2,
            failed_batches=0,
            timed_out_batches=0,
            total_tests=0,
            total_failures=0,
            total_errors=0,
            total_skipped=0,
            failed_batch_names=[],
            batch_results=[result_one, result_two],
        )

        analysis = build_run_analysis(summary)
        self.assertEqual(analysis["planned_order"], ["alpha", "beta"])
        self.assertEqual(analysis["observed_completion_order"], ["beta", "alpha"])
        self.assertEqual(analysis["order_changes"], 2)
        self.assertEqual(analysis["runner_changes"], 0)

        summary_line = render_run_analysis_summary(analysis)
        self.assertIn("RUN_ANALYSIS_SUMMARY run_id=rid", summary_line)
        self.assertIn("planned=alpha,beta observed=beta,alpha", summary_line)

        rendered = render_run_analysis_text(analysis)
        self.assertIn("RUN_ANALYSIS_SUMMARY run_id=rid", rendered)
        self.assertIn("planned=alpha,beta observed=beta,alpha", rendered)

    def test_build_execution_plan_assigns_parallel_lanes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {"name": "alpha", "targets": ["tests/test_alpha.py"], "profile": "standard"},
                            {"name": "beta", "targets": ["tests/test_beta.py"], "profile": "standard"},
                            {"name": "gamma", "targets": ["tests/test_gamma.py"], "profile": "standard"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "history.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "batch_durations": {
                                    "alpha": {"duration_seconds": 6.0},
                                    "beta": {"duration_seconds": 4.0},
                                    "gamma": {"duration_seconds": 2.0}
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = build_execution_plan(
                manifest_path=manifest_path,
                output_root=root,
                scheduling_strategy="longest-first",
                max_workers=2,
            )

            by_name = {batch.name: batch for batch in plan.batch_results}
            self.assertEqual(plan.parallel_worker_count, 2)
            self.assertEqual(by_name["alpha"].planned_runner, "worker-1")
            self.assertEqual(by_name["beta"].planned_runner, "worker-2")
            self.assertEqual(by_name["gamma"].planned_runner, "worker-2")

    def test_cli_plan_json_file_writes_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "core-batch",
                                "targets": ["tests/test_core.py"],
                                "profile": "standard",
                                "tags": ["core"]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            plan_path = root / "artifacts" / "plan.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(root / "results"),
                    "--plan-json-file",
                    str(plan_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(plan_path.exists())
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_batches"], ["core-batch"])
            self.assertIn("Wrote execution plan JSON", completed.stdout)

    def test_cli_plan_text_and_json_file_conflict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "core-batch",
                                "targets": ["tests/test_core.py"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--plan",
                    "--plan-json-file",
                    str(root / "plan.json"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Choose exactly one plan output mode", completed.stderr)

    def test_cli_plan_summary_conflicts_with_other_plan_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "core-batch",
                                "targets": ["tests/test_core.py"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--plan-summary",
                    "--plan-json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Choose exactly one plan output mode", completed.stderr)

    def test_cli_ci_analysis_summary_only_outputs_analysis_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tests_dir = root / "tests"
            tests_dir.mkdir(parents=True)
            test_file = tests_dir / "test_sample.py"
            test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "sample",
                                "targets": [str(test_file)],
                                "profile": "standard",
                                "timeout_seconds": 60,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(root / "results"),
                    "--run-id",
                    "ci-analysis-only",
                    "--ci-analysis-summary",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("RUN_ANALYSIS_SUMMARY run_id=ci-analysis-only", completed.stdout)
            self.assertNotIn("BATCH_RUN", completed.stdout)

    def test_cli_ci_summary_conflicts_with_ci_analysis_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {
                                "name": "core-batch",
                                "targets": ["tests/test_core.py"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--ci-summary",
                    "--ci-analysis-summary",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Choose either --ci-summary or --ci-analysis-summary", completed.stderr)

    def test_cli_ci_analysis_summary_fails_on_order_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tests_dir = root / "tests"
            tests_dir.mkdir(parents=True)
            slow_file = tests_dir / "test_slow.py"
            fast_file = tests_dir / "test_fast.py"
            slow_file.write_text(
                "import time\n\n"
                "def test_slow():\n"
                "    time.sleep(0.2)\n"
                "    assert True\n",
                encoding="utf-8",
            )
            fast_file.write_text(
                "def test_fast():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {"name": "slow", "targets": [str(slow_file)], "profile": "standard", "timeout_seconds": 60},
                            {"name": "fast", "targets": [str(fast_file)], "profile": "standard", "timeout_seconds": 60},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            history_path = root / "results" / "history.json"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "batch_durations": {
                                    "slow": {"duration_seconds": 5.0},
                                    "fast": {"duration_seconds": 1.0},
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(root / "results"),
                    "--run-id",
                    "threshold-order-fail",
                    "--schedule",
                    "longest-first",
                    "--max-workers",
                    "2",
                    "--ci-analysis-summary",
                    "--max-order-changes",
                    "0",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("RUN_ANALYSIS_SUMMARY", completed.stdout)

    def test_cli_ci_analysis_summary_passes_with_runner_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tests_dir = root / "tests"
            tests_dir.mkdir(parents=True)
            test_file = tests_dir / "test_sample.py"
            test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "batches": [
                            {"name": "sample", "targets": [str(test_file)], "profile": "standard", "timeout_seconds": 60}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(self._cli_script_path()),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(root / "results"),
                    "--run-id",
                    "threshold-runner-pass",
                    "--max-workers",
                    "2",
                    "--ci-analysis-summary",
                    "--max-runner-changes",
                    "0",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("RUN_ANALYSIS_SUMMARY run_id=threshold-runner-pass", completed.stdout)


if __name__ == "__main__":
    unittest.main()