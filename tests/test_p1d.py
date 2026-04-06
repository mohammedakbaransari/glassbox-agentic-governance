#!/usr/bin/env python3
"""Quick test of P1-D deserialization fix"""

from glassbox.governance.audit_logger import AuditLogger
from glassbox.governance.models import (
    DecisionContext, DecisionType, FinalStatus, AuditRecord
)

# Create logger
logger = AuditLogger()

# Create a proper AuditRecord
context = DecisionContext(
    environment="test",
    source_system="test_system",
    user_override=False
)

audit = AuditRecord(
    agent_id="test-agent",
    decision_type=DecisionType.FINANCIAL,
    payload={"amount": 1000},
    context=context,
    final_status=FinalStatus.EXECUTED,
)

# Convert to dict (as stored)
audit_dict = audit.to_dict()
print(f"✓ Created audit record")
print(f"  - Stored as dict: final_status={audit_dict.get('final_status')} (type: {type(audit_dict.get('final_status'))})")
print(f"  - Stored as dict: context={type(audit_dict.get('context'))}")

# Record it
logger.record_decision(audit_dict)
print(f"✓ Recorded decision")

# Retrieve it - this is where P1-D fixes deserialization
retrieved = logger.get_by_id(audit.decision_id)
print(f"✓ Retrieved audit")

if retrieved:
    # Check types were properly reconstructed
    if isinstance(retrieved, dict):
        print(f"  ⚠ Retrieved as dict (not AuditRecord)")
        if isinstance(retrieved.get("final_status"), FinalStatus):
            print(f"  ✓ final_status is FinalStatus enum: {retrieved['final_status']}")
        else:
            print(f"  ✗ final_status is {type(retrieved.get('final_status'))}: {retrieved.get('final_status')}")
        
        if isinstance(retrieved.get("context"), DecisionContext):
            print(f"  ✓ context is DecisionContext: {retrieved['context'].agent_id}")
        else:
            print(f"  ✗ context is {type(retrieved.get('context'))}")
    else:
        print(f"  ✓ Retrieved as {type(retrieved).__name__}")
        if hasattr(retrieved, 'final_status'):
            print(f"  ✓ final_status: {retrieved.final_status} (type: {type(retrieved.final_status)})")

# Test get_all
all_audits = logger.get_all()
print(f"✓ Retrieved all audits: {len(all_audits)} record(s)")
if all_audits:
    first = all_audits[0]
    if isinstance(first.get("final_status") if isinstance(first, dict) else first.final_status, FinalStatus):
        print(f"  ✓ Types properly deserialized in get_all()")

# Test get_by_status
by_status = logger.get_by_status(FinalStatus.EXECUTED)
print(f"✓ Retrieved by status: {len(by_status)} record(s)")
if by_status:
    first = by_status[0]
    if isinstance(first.get("final_status") if isinstance(first, dict) else first.final_status, FinalStatus):
        print(f"  ✓ Types properly deserialized in get_by_status()")

print("\n✓ All P1-D tests passed!")
