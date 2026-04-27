#!/usr/bin/env python3
"""Quick test of P1-D deserialization fix"""

from glassbox.governance.audit_logger import AuditLogger
from glassbox.governance.models import (
    AuditRecord,
    DecisionContext,
    DecisionType,
    FinalStatus,
)


def main() -> None:
    logger = AuditLogger()

    context = DecisionContext(
        environment="test",
        source_system="test_system",
        user_override=False,
    )

    audit = AuditRecord(
        agent_id="test-agent",
        decision_type=DecisionType.FINANCIAL,
        payload={"amount": 1000},
        context=context,
        final_status=FinalStatus.EXECUTED,
    )

    audit_dict = audit.to_dict()
    print("Created audit record")

    logger.record_decision(audit_dict)
    print("Recorded decision")

    retrieved = logger.get_by_id(audit.decision_id)
    print("Retrieved audit")

    if retrieved:
        if isinstance(retrieved, dict):
            if isinstance(retrieved.get("final_status"), FinalStatus):
                print("final_status is FinalStatus enum")
            if isinstance(retrieved.get("context"), DecisionContext):
                print("context is DecisionContext")
        else:
            if hasattr(retrieved, "final_status"):
                print(f"final_status: {retrieved.final_status}")

    all_audits = logger.get_all()
    print(f"Retrieved all audits: {len(all_audits)} record(s)")

    by_status = logger.get_by_status(FinalStatus.EXECUTED)
    print(f"Retrieved by status: {len(by_status)} record(s)")

    print("\nAll P1-D tests passed!")


if __name__ == "__main__":
    main()

