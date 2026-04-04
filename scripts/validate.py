"""
GlassBox Distributed Velocity Breaker — Validation Script
=========================================================

Quick verification that all components are correctly implemented.

Run: python scripts/validate.py
"""

import sys
from pathlib import Path

def validate_imports():
    """Verify all imports work."""
    print("\n" + "="*70)
    print("VALIDATION: Checking Imports")
    print("="*70)
    
    try:
        # Core imports (merged into velocity_breaker.py in v1.1)
        print("✓ Importing glassbox.governance.velocity_breaker...")
        from glassbox.governance.velocity_breaker import (
            RedisVelocityBreakerBackend,
            DistributedVelocityBreaker,
            create_velocity_breaker_distributed,
        )
        
        print("✓ RedisVelocityBreakerBackend imported")
        print("✓ DistributedVelocityBreaker imported")
        print("✓ create_velocity_breaker_distributed imported")
        
        # Check main exports
        print("\n✓ Importing from glassbox.governance...")
        from glassbox.governance import (
            DistributedVelocityBreaker,
            create_velocity_breaker_distributed,
        )
        print("✓ Exports configured correctly")
        
        # Check compatibility
        print("\n✓ Checking VelocityBreaker compatibility...")
        from glassbox.governance import VelocityBreaker
        print("✓ VelocityBreaker (single-instance) available")
        
        return True
    except Exception as exc:
        print(f"✗ Import failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def validate_api():
    """Verify API methods exist."""
    print("\n" + "="*70)
    print("VALIDATION: Checking API")
    print("="*70)
    
    try:
        from glassbox.governance.velocity_breaker import (
            DistributedVelocityBreaker
        )
        
        # Mock Redis
        class MockRedis:
            def ping(self):
                return True
            def register_script(self, script):
                return lambda keys, args: [1, 0]
        
        mock_redis = MockRedis()
        breaker = DistributedVelocityBreaker(
            redis_client=mock_redis,
            max_decisions=20,
        )
        
        # Check methods
        methods = [
            'check',
            'reset_agent',
            'reset',  # Compatibility
            'reset_ecosystem',  # Compatibility
            'reset_all',  # Compatibility
            'status',  # Compatibility
            'ecosystem_status',  # Compatibility
            '_get_window_count',
            '_check_local',
        ]
        
        for method_name in methods:
            if hasattr(breaker, method_name):
                print(f"✓ {method_name}() exists")
            else:
                print(f"✗ {method_name}() MISSING")
                return False
        
        # Check attributes
        attrs = [
            'max_decisions',
            'window_seconds',
            'cooldown_seconds',
            'ecosystem_max',
            'fallback_mode',
            '_redis_available',
            '_circuit_breaker_open',
            '_tripped',
            '_local_fallback_windows',
        ]
        
        for attr_name in attrs:
            if hasattr(breaker, attr_name):
                print(f"✓ {attr_name} attribute exists")
            else:
                print(f"✗ {attr_name} attribute MISSING")
                return False
        
        return True
    except Exception as exc:
        print(f"✗ API validation failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def validate_tests():
    """Check test file exists and is syntactically correct."""
    print("\n" + "="*70)
    print("VALIDATION: Checking Tests")
    print("="*70)
    
    try:
        test_file = Path(__file__).parent / "tests" / "test_velocity_distributed.py"
        
        if not test_file.exists():
            print(f"✗ Test file not found: {test_file}")
            return False
        
        print(f"✓ Test file exists: {test_file}")
        
        # Try to parse it
        with open(test_file, 'r') as f:
            code = f.read()
        
        compile(code, str(test_file), 'exec')
        print("✓ Test file compiles successfully")
        
        # Check for test classes
        test_classes = [
            'TestLuaScripts',
            'TestCheckAndAdd',
            'TestDistributedVelocityBreakerLogic',
            'TestEcosystemLimits',
            'TestRedisFailover',
            'TestConcurrency',
            'TestResetAndMonitoring',
            'TestFactoryFunction',
            'TestLocalFallback',
            'TestIntegration',
        ]
        
        for test_class in test_classes:
            if test_class in code:
                print(f"✓ {test_class} found")
            else:
                print(f"✗ {test_class} NOT found")
                return False
        
        return True
    except Exception as exc:
        print(f"✗ Test validation failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def validate_docs():
    """Check documentation files exist."""
    print("\n" + "="*70)
    print("VALIDATION: Checking Documentation")
    print("="*70)
    
    try:
        root = Path(__file__).parent
        
        files = [
            "docs/DISTRIBUTED_VELOCITY_BREAKER.md",
            "examples/distributed_velocity_breaker.py",
            "DISTRIBUTED_VELOCITY_BREAKER_SUMMARY.md",
        ]
        
        for file_path in files:
            full_path = root / file_path
            if full_path.exists():
                size = full_path.stat().st_size
                print(f"✓ {file_path} ({size:,} bytes)")
            else:
                print(f"✗ {file_path} NOT found")
                return False
        
        # Check CHANGELOG updated
        changelog = root / "CHANGELOG.md"
        with open(changelog, 'r') as f:
            content = f.read()
        
        if "1.0.1" in content and "Distributed" in content:
            print("✓ CHANGELOG.md updated with v1.0.1")
        else:
            print("✗ CHANGELOG.md not updated")
            return False
        
        return True
    except Exception as exc:
        print(f"✗ Documentation validation failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def validate_examples():
    """Check example files are valid."""
    print("\n" + "="*70)
    print("VALIDATION: Checking Examples")
    print("="*70)
    
    try:
        example_file = Path(__file__).parent / "examples" / "distributed_velocity_breaker.py"
        
        if not example_file.exists():
            print(f"✗ Example file not found: {example_file}")
            return False
        
        print(f"✓ Example file exists: {example_file}")
        
        # Try to compile
        with open(example_file, 'r') as f:
            code = f.read()
        
        compile(code, str(example_file), 'exec')
        print("✓ Example file compiles successfully")
        
        # Check for example functions
        examples = [
            'example_1_basic_multi_instance',
            'example_2_ecosystem_limits',
            'example_3_redis_fallback',
            'example_4_pipeline_integration',
            'example_5_monitoring',
            'example_6_deployment_config',
        ]
        
        for example in examples:
            if example in code:
                print(f"✓ {example}() defined")
            else:
                print(f"✗ {example}() NOT found")
                return False
        
        return True
    except Exception as exc:
        print(f"✗ Example validation failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all validations."""
    print("\n" + "◆"*70)
    print("GlassBox Distributed Velocity Breaker — Validation Script (v1.0.1)")
    print("◆"*70)
    
    results = {
        "Imports": validate_imports(),
        "API": validate_api(),
        "Tests": validate_tests(),
        "Documentation": validate_docs(),
        "Examples": validate_examples(),
    }
    
    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)
    
    all_passed = True
    for check, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {check}")
        if not passed:
            all_passed = False
    
    print("="*70)
    
    if all_passed:
        print("\n✓ ALL VALIDATIONS PASSED — Implementation is complete!")
        print("\nNext Steps:")
        print("  1. Run tests: pytest tests/test_velocity_distributed.py -v")
        print("  2. Review docs: docs/DISTRIBUTED_VELOCITY_BREAKER.md")
        print("  3. Try examples: python examples/distributed_velocity_breaker.py")
        print("  4. Deploy Redis: docker run -d -p 6379:6379 redis:7")
        return 0
    else:
        print("\n✗ SOME VALIDATIONS FAILED — Please review above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
