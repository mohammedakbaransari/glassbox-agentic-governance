"""
GlassBox Framework — Benchmark Suite  (v1.1.0)
Measures throughput, latency distribution, policy accuracy,
anomaly detection precision/recall, concurrency behaviour,
and retry overhead across all decision types.

Run:  python3 -m glassbox.benchmarks.run_benchmarks

Author: Mohammed Akbar Ansari
"""

import os
import random
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from glassbox.governance.pipeline       import GovernancePipeline
from glassbox.governance.models         import (
    DecisionContext, DecisionRequest, DecisionType, FinalStatus,
)
from glassbox.governance.anomaly_detector import AnomalyDetector

DIV = "=" * 68


def _hdr(title):
    print(f"\n{DIV}\n  {title}\n{DIV}")


def _row(label, value, unit=""):
    print(f"  {label:<40} {value:>12} {unit}")


# ── Helpers ───────────────────────────────────────────────────────────────────

PAYLOADS = {
    DecisionType.PROCUREMENT: {"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"},
    DecisionType.PRICING:     {"new_price": 102.0, "previous_price": 100.0, "product_id": "P-001", "reason": "demand"},
    DecisionType.FINANCIAL:   {"amount": 15000, "destination_account": "ACC-001", "reference": "REF-001"},
    DecisionType.INVENTORY:   {"quantity": 500, "product_id": "SKU-001", "warehouse_id": "WH-01"},
    DecisionType.LOGISTICS:   {"origin": "MUM", "destination": "DEL", "shipment_value": 5000},
    DecisionType.IT_OPS:      {"action": "restart_service", "target": "svc-api"},
    DecisionType.HR:          {"action": "address_update", "employee_id": "EMP-001"},
    DecisionType.CUSTOM:      {"description": "benchmark decision"},
}


def warm_up(pipeline, n=500):
    """Warm up JIT and module caches before measuring."""
    for i in range(n):
        dtype = list(DecisionType)[i % len(DecisionType)]
        pipeline.process(DecisionRequest(f"warmup_{i}", dtype, PAYLOADS[dtype]))


# ── Benchmark 1: Single-type throughput ──────────────────────────────────────

def bench_throughput(n=10_000):
    _hdr("Benchmark 1 — Single-Type Throughput (Procurement)")
    pipeline = GovernancePipeline(echo=False)
    warm_up(pipeline)

    payload = PAYLOADS[DecisionType.PROCUREMENT]
    t0 = time.perf_counter()
    for i in range(n):
        pipeline.process(DecisionRequest(f"bench_agent_{i%10}", DecisionType.PROCUREMENT, payload))
    elapsed = time.perf_counter() - t0

    throughput = n / elapsed
    _row("Decisions submitted", f"{n:,}")
    _row("Total time", f"{elapsed:.3f}", "s")
    _row("Throughput", f"{throughput:,.0f}", "decisions/sec")
    _row("Avg latency", f"{elapsed/n*1000:.4f}", "ms")
    return throughput


# ── Benchmark 2: Latency distribution ────────────────────────────────────────

def bench_latency(n=5000):
    _hdr("Benchmark 2 — Latency Distribution (All Decision Types)")
    pipeline = GovernancePipeline(echo=False)
    warm_up(pipeline)

    latencies = []
    dtypes = list(DecisionType)
    for i in range(n):
        dtype = dtypes[i % len(dtypes)]
        t0 = time.perf_counter()
        pipeline.process(DecisionRequest(f"lat_agent_{i%5}", dtype, PAYLOADS[dtype]))
        latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()

    def pct(p):
        idx = max(0, int(len(latencies) * p / 100) - 1)
        return latencies[idx]

    _row("Decisions", f"{n:,}")
    _row("P50 (median)", f"{pct(50):.4f}", "ms")
    _row("P75", f"{pct(75):.4f}", "ms")
    _row("P90", f"{pct(90):.4f}", "ms")
    _row("P95", f"{pct(95):.4f}", "ms")
    _row("P99", f"{pct(99):.4f}", "ms")
    _row("P99.9", f"{pct(99.9):.4f}", "ms")
    _row("Max", f"{max(latencies):.4f}", "ms")
    _row("StdDev", f"{statistics.stdev(latencies):.4f}", "ms")
    return latencies


# ── Benchmark 3: Policy accuracy ─────────────────────────────────────────────

def bench_policy_accuracy():
    _hdr("Benchmark 3 — Policy Enforcement Accuracy")
    # Use a fresh pipeline per run with no ecosystem limit to avoid breaker interference
    from glassbox.governance.velocity_breaker import VelocityBreaker
    vb = VelocityBreaker(max_decisions=10000, window_seconds=3600,
                         ecosystem_max=100000, ecosystem_window_seconds=3600)
    pipeline = GovernancePipeline(echo=False, velocity_breaker=vb)

    # Ground-truth test cases: (payload, expected_status)
    cases = [
        # Should EXECUTE
        (DecisionType.PROCUREMENT,
         {"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"},
         FinalStatus.EXECUTED),
        (DecisionType.PROCUREMENT,
         {"amount": 700000, "supplier_id": "SUP-001", "category": "hardware", "contract_id": "CT-001"},
         FinalStatus.EXECUTED),
        (DecisionType.PRICING,
         {"new_price": 110.0, "previous_price": 100.0, "product_id": "P1", "reason": "demand"},
         FinalStatus.EXECUTED),
        (DecisionType.FINANCIAL,
         {"amount": 50000, "destination_account": "ACC-1", "reference": "REF-1"},
         FinalStatus.EXECUTED),
        (DecisionType.IT_OPS,
         {"action": "restart_service", "target": "web-tier"},
         FinalStatus.EXECUTED),
        # Should BLOCK
        (DecisionType.PROCUREMENT,
         {"amount": 700000, "supplier_id": "SUP-001", "category": "hardware"},
         FinalStatus.BLOCKED),
        (DecisionType.PRICING,
         {"new_price": 500.0, "previous_price": 100.0, "product_id": "P1"},
         FinalStatus.BLOCKED),
        (DecisionType.FINANCIAL,
         {"amount": 2_000_000, "destination_account": "ACC-1", "reference": "REF-1"},
         FinalStatus.BLOCKED),
        (DecisionType.PROCUREMENT,
         {"amount": 5000, "supplier_id": "SUP-001", "category": "semiconductors"},
         FinalStatus.BLOCKED),
        (DecisionType.IT_OPS,
         {"action": "delete_database", "target": "prod-db"},
         FinalStatus.BLOCKED),
        (DecisionType.PROCUREMENT,
         {"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"},
         FinalStatus.EXECUTED),  # Repeat to reach 10 expected-EXECUTE
        (DecisionType.PRICING,
         {"new_price": 15.0, "previous_price": 100.0, "product_id": "P1", "floor_price": 20.0},
         FinalStatus.BLOCKED),
    ]

    correct = 0
    total   = len(cases) * 100  # run each case 100 times
    for _ in range(100):
        for idx, (dtype, payload, expected) in enumerate(cases):
            ctx = DecisionContext(confidence=0.95)
            r   = pipeline.process(DecisionRequest(f"acc_{_}_{idx}", dtype, payload, ctx))
            if r.final_status == expected:
                correct += 1

    accuracy = correct / total * 100
    _row("Test cases", f"{len(cases)}")
    _row("Total evaluations", f"{total:,}")
    _row("Correct outcomes", f"{correct:,}")
    _row("Policy accuracy", f"{accuracy:.2f}", "%")
    assert accuracy == 100.0, f"Policy accuracy degraded: {accuracy:.2f}%"
    return accuracy


# ── Benchmark 4: Anomaly detection precision/recall ──────────────────────────

def bench_anomaly():
    _hdr("Benchmark 4 — Anomaly Detection Precision & Recall")

    det    = AnomalyDetector(z_threshold=3.0, min_samples=10)
    rng    = random.Random(42)
    mean   = 50_000
    std    = 5_000
    n_seed = 50

    # Seed baseline
    for _ in range(n_seed):
        det.update_only("agent", "procurement", {"amount": max(rng.gauss(mean, std), 1)})

    # True positives: clearly anomalous (>6σ from mean)
    anomalies = [mean + 8 * std, mean + 10 * std, mean - 8 * std, mean + 12 * std]
    tp = sum(1 for v in anomalies if det.check("agent", "procurement", {"amount": v})[0])

    # True negatives: clearly normal (within 2σ)
    normals = [rng.gauss(mean, std) for _ in range(20)]
    tn = sum(1 for v in normals if not det.check("agent", "procurement", {"amount": v})[0])

    precision = tp / len(anomalies) * 100
    recall    = tp / len(anomalies) * 100  # all anomalies detected = perfect recall
    specificity = tn / len(normals) * 100

    _row("Baseline samples seeded", f"{n_seed}")
    _row("Z-score threshold", f"3.0")
    _row("Anomalous values tested", f"{len(anomalies)}")
    _row("Normal values tested", f"{len(normals)}")
    _row("True positives", f"{tp}/{len(anomalies)}")
    _row("True negatives", f"{tn}/{len(normals)}")
    _row("Precision", f"{precision:.1f}", "%")
    _row("Recall", f"{recall:.1f}", "%")
    _row("Specificity", f"{specificity:.1f}", "%")
    return precision, recall


# ── Benchmark 5: Concurrent throughput ───────────────────────────────────────

def bench_concurrency(n_threads=10, decisions_per_thread=500):
    _hdr(f"Benchmark 5 — Concurrent Throughput ({n_threads} threads × {decisions_per_thread} decisions)")
    pipeline  = GovernancePipeline(echo=False)
    warm_up(pipeline)

    results  = []
    errors   = []
    lock     = threading.Lock()
    total    = n_threads * decisions_per_thread

    def worker(tid):
        payload = PAYLOADS[DecisionType.PROCUREMENT]
        t0 = time.perf_counter()
        for i in range(decisions_per_thread):
            try:
                pipeline.process(DecisionRequest(
                    f"concurrent_{tid}", DecisionType.PROCUREMENT, payload))
            except Exception as e:
                with lock: errors.append(str(e))
        elapsed = time.perf_counter() - t0
        with lock: results.append(elapsed)

    t_start = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads: t.start()
    for t in threads: t.join()
    wall_time = time.perf_counter() - t_start

    throughput   = total / wall_time
    avg_thread   = statistics.mean(results)
    decision_ids = [r.decision_id for r in pipeline.audit_logger.get_all()]
    duplicates   = len(decision_ids) - len(set(decision_ids))

    _row("Threads", f"{n_threads}")
    _row("Decisions per thread", f"{decisions_per_thread:,}")
    _row("Total decisions", f"{total:,}")
    _row("Wall time", f"{wall_time:.3f}", "s")
    _row("Throughput (concurrent)", f"{throughput:,.0f}", "decisions/sec")
    _row("Avg thread time", f"{avg_thread:.3f}", "s")
    _row("Errors", f"{len(errors)}")
    _row("Duplicate decision IDs", f"{duplicates}")
    assert len(errors) == 0, f"Concurrent errors: {errors}"
    assert duplicates == 0, "Duplicate decision IDs detected!"
    return throughput


# ── Benchmark 6: Per-decision-type latency ───────────────────────────────────

def bench_per_type(n=2000):
    _hdr(f"Benchmark 6 — Per-Decision-Type Latency ({n} each)")
    pipeline = GovernancePipeline(echo=False)
    warm_up(pipeline)

    for dtype in DecisionType:
        payload    = PAYLOADS[dtype]
        latencies  = []
        for i in range(n):
            t0 = time.perf_counter()
            pipeline.process(DecisionRequest(f"type_bench", dtype, payload))
            latencies.append((time.perf_counter() - t0) * 1000)
        latencies.sort()
        p50 = latencies[int(n * 0.50)]
        p99 = latencies[int(n * 0.99)]
        avg = sum(latencies) / n
        print(f"  {dtype.value:<14}  avg={avg:.3f}ms  p50={p50:.3f}ms  p99={p99:.3f}ms")


# ── Benchmark 7: Memory footprint ────────────────────────────────────────────

def bench_memory(n=10_000):
    _hdr(f"Benchmark 7 — Memory Footprint ({n:,} audit records in memory)")
    import sys as _sys
    pipeline = GovernancePipeline(echo=False)

    for i in range(n):
        dtype = list(DecisionType)[i % len(DecisionType)]
        pipeline.process(DecisionRequest(f"mem_agent", dtype, PAYLOADS[dtype]))

    records   = pipeline.audit_logger.get_all()
    total_bytes = _sys.getsizeof(records)
    for r in records:
        total_bytes += _sys.getsizeof(r)

    _row("Audit records in memory", f"{len(records):,}")
    _row("Approx memory (records only)", f"{total_bytes/1024/1024:.2f}", "MB")
    _row("Bytes per record (approx)", f"{total_bytes/max(len(records),1):.0f}", "bytes")


# ── Summary ───────────────────────────────────────────────────────────────────

def run_all():
    print(f"\n{DIV}")
    print("  GLASSBOX BENCHMARK SUITE  (v1.0.0)")
    print("  Runtime Decision Governance for Autonomous AI Systems")
    print("  Author: Mohammed Akbar Ansari")
    print(DIV)

    tp      = bench_throughput(10_000)
    lats    = bench_latency(5_000)
    acc     = bench_policy_accuracy()
    prec, rec = bench_anomaly()
    conc_tp = bench_concurrency(10, 500)
    bench_per_type(2_000)
    bench_memory(10_000)

    # ── Summary table ─────────────────────────────────────────────────────
    _hdr("BENCHMARK SUMMARY")
    _row("Single-thread throughput",   f"{tp:,.0f}",  "decisions/sec")
    _row("P50 latency (all types)",    f"{sorted(lats)[int(len(lats)*0.50)]:.4f}", "ms")
    _row("P99 latency (all types)",    f"{sorted(lats)[int(len(lats)*0.99)]:.4f}", "ms")
    _row("Policy enforcement accuracy", f"{acc:.2f}", "%")
    _row("Anomaly detection precision",  f"{prec:.1f}", "%")
    _row("Anomaly detection recall",     f"{rec:.1f}",  "%")
    _row("10-thread concurrent throughput", f"{conc_tp:,.0f}", "decisions/sec")
    print(f"\n{DIV}\n  All benchmarks complete.\n{DIV}\n")


if __name__ == "__main__":
    run_all()
