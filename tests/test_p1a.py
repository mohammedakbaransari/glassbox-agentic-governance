#!/usr/bin/env python3
"""Quick test of P1-A refactor"""

from glassbox.governance.audit_logger import AuditLogger


def main() -> None:
    logger = AuditLogger(log_dir="/tmp", echo=False, max_memory_records=100)
    print("Instantiation successful")

    if isinstance(logger, AuditLogger):
        print("isinstance(logger, AuditLogger) = True")
    else:
        print("isinstance(logger, AuditLogger) = False")

    methods = ["log", "get_by_id", "get_by_status", "get_all", "summary_stats", "record_decision"]
    for method in methods:
        if hasattr(logger, method):
            print(f"Method '{method}' exists")
        else:
            print(f"Method '{method}' missing")

    record = {
        "decision_id": "test-1",
        "agent_id": "agent-001",
        "timestamp": 1234567890.0,
        "final_status": "PASS",
        "payload": {},
    }
    logger.record_decision(record)
    print("record_decision() works")

    result = logger.get_by_id("test-1")
    if result and result.get("decision_id") == "test-1":
        print("get_by_id() works")
    else:
        print("get_by_id() failed")

    logger.log(record)
    print("log() method works")

    stats = logger.summary_stats()
    if stats["total"] >= 2:
        print(f"summary_stats() works: total={stats['total']}")
    else:
        print(f"summary_stats() failed: {stats}")

    print("\nAll P1-A tests passed!")


if __name__ == "__main__":
    main()

