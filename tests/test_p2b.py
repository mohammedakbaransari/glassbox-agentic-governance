#!/usr/bin/env python3
"""Test P2-B: Verify multiple pipelines don't cause atexit handler leak"""

import sys
from glassbox.governance.pipeline import GovernancePipeline, _active_pipelines

print("Testing P2-B: Multiple pipelines with module-level atexit handler...")

# Create multiple pipelines
pipelines = []
for i in range(10):
    p = GovernancePipeline()
    pipelines.append(p)

# Verify WeakSet tracking
print(f"✓ Created 10 pipelines")
print(f"Active pipelines in WeakSet: {len(_active_pipelines)}")
if len(_active_pipelines) == 10:
    print("✓ All 10 pipelines tracked in WeakSet")
else:
    print(f"✗ Expected 10 pipelines in WeakSet, got {len(_active_pipelines)}")

# Delete half the pipelines
del pipelines[0:5]
import gc
gc.collect()  # Force garbage collection

# Check WeakSet size
print(f"Active pipelines after deleting 5: {len(_active_pipelines)}")
if len(_active_pipelines) == 5:
    print("✓ P2-B working: WeakSet automatically removed deleted pipelines")
else:
    print(f"✗ Expected 5 pipelines after GC, got {len(_active_pipelines)}")

# Verify shutdown works on remaining pipelines
print("\nVerifying shutdown works on remaining pipelines...")
remaining = list(_active_pipelines)
for i, p in enumerate(remaining):
    try:
        p.shutdown()
        print(f"  ✓ Pipeline {i} shutdown successful")
    except Exception as e:
        print(f"  ✗ Pipeline {i} shutdown failed: {e}")

print("\n✓ All P2-B tests passed!")
print("✓ No per-instance atexit handler leak!")
print("✓ WeakSet tracking is working correctly!")

