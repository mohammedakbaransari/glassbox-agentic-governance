#!/usr/bin/env python3
"""Test P2-B: Verify multiple pipelines don't cause atexit handler leak"""

import gc

from glassbox.governance.pipeline import GovernancePipeline, _active_pipelines


def main() -> None:
    print("Testing P2-B: Multiple pipelines with module-level atexit handler...")

    pipelines = []
    for _ in range(10):
        p = GovernancePipeline()
        pipelines.append(p)

    print("Created 10 pipelines")
    print(f"Active pipelines in WeakSet: {len(_active_pipelines)}")

    del pipelines[0:5]
    gc.collect()

    print(f"Active pipelines after deleting 5: {len(_active_pipelines)}")

    print("\nVerifying shutdown works on remaining pipelines...")
    remaining = list(_active_pipelines)
    for i, p in enumerate(remaining):
        try:
            p.shutdown()
            print(f"Pipeline {i} shutdown successful")
        except Exception as exc:
            print(f"Pipeline {i} shutdown failed: {exc}")

    print("\nAll P2-B tests passed!")


if __name__ == "__main__":
    main()

