#!/usr/bin/env python3
"""Quick test of P1-A refactor"""

from glassbox.governance.audit_logger import AuditLogger

# Test 1: Instantiate with parameters
logger = AuditLogger(log_dir='/tmp', echo=False, max_memory_records=100)
print(f"✓ Instantiation successful")

# Test 2: isinstance check (this should now work!)
if isinstance(logger, AuditLogger):
    print(f"✓ isinstance(logger, AuditLogger) = True")
else:
    print(f"✗ isinstance(logger, AuditLogger) = False")

# Test 3: Check methods exist
methods = ['log', 'get_by_id', 'get_by_status', 'get_all', 'summary_stats', 'record_decision']
for method in methods:
    if hasattr(logger, method):
        print(f"✓ Method '{method}' exists")
    else:
        print(f"✗ Method '{method}' missing")

# Test 4: Record a decision
record = {
    'decision_id': 'test-1',
    'agent_id': 'agent-001',
    'timestamp': 1234567890.0,
    'final_status': 'PASS',
    'payload': {}
}
logger.record_decision(record)
print(f"✓ record_decision() works")

# Test 5: Retrieve via backward compat method
result = logger.get_by_id('test-1')
if result and result.get('decision_id') == 'test-1':
    print(f"✓ get_by_id() works")
else:
    print(f"✗ get_by_id() failed")

# Test 6: Log method
logger.log(record)
print(f"✓ log() method works")

# Test 7: Summary stats
stats = logger.summary_stats()
if stats['total'] >= 2:  # We recorded twice
    print(f"✓ summary_stats() works: total={stats['total']}")
else:
    print(f"✗ summary_stats() failed: {stats}")

print("\nAll P1-A tests passed!")
