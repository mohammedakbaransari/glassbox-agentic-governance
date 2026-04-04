"""
GlassBox Distributed Velocity Breaker — Integration Example
============================================================

Demonstrates multi-instance deployment with shared velocity state via Redis.

Scenario: E-commerce platform with 3 agent instances handling purchase decisions
  • Limit: 20 decisions per minute per agent
  • Goal: Enforce limit across all instances (not per-instance)

Before (v1.0.0):
  Instance-1 allows 20 ✓
  Instance-2 allows 20 ✓
  Instance-3 allows 20 ✓
  Total: 60 decisions (VIOLATES limit!)

After (v1.0.1):
  Instance-1 ──┐
  Instance-2 ──┼──→ Redis (shared state)
  Instance-3 ──┘
  Total: 20 decisions across all instances ✓

Author: Mohammed Akbar Ansari
"""

import redis
import time
import threading
from typing import Tuple

# Import the distributed breaker (merged into velocity_breaker.py in v1.1)
from glassbox.governance.velocity_breaker import (
    DistributedVelocityBreaker,
    create_velocity_breaker_distributed,
)


# ────────────────────────────────────────────────────────────────────────
# EXAMPLE 1: Basic Multi-Instance Setup
# ────────────────────────────────────────────────────────────────────────

def example_1_basic_multi_instance():
    """
    Multiple instances with Redis-backed velocity breaker.
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 1: Basic Multi-Instance Velocity Breaker")
    print("=" * 70)
    
    # Connect to Redis (localhost:6379 by default)
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    # Create breaker: max 20 decisions per 60 seconds, globally
    breaker = DistributedVelocityBreaker(
        redis_client=redis_client,
        max_decisions=20,
        window_seconds=60,
        cooldown_seconds=300,  # Cooldown after breach
    )
    
    # Simulate 3 instances making decisions concurrently
    def process_agent_decisions(instance_id: int, agent_id: str, num_decisions: int):
        allowed_count = 0
        blocked_count = 0
        
        for i in range(num_decisions):
            triggered, reason, count = breaker.check(agent_id)
            
            if triggered:
                print(f"  [Instance-{instance_id}] ❌ Decision {i+1}: BLOCKED - {reason}")
                blocked_count += 1
            else:
                print(f"  [Instance-{instance_id}] ✓ Decision {i+1}: ALLOWED (window: {count})")
                allowed_count += 1
            
            time.sleep(0.1)  # Small delay between decisions
        
        print(f"  [Instance-{instance_id}] Summary: {allowed_count} allowed, {blocked_count} blocked")
    
    # Launch 3 instances concurrently, each trying 10 decisions
    threads = []
    for instance_id in range(1, 4):
        thread = threading.Thread(
            target=process_agent_decisions,
            args=(instance_id, "agent_1", 10),
        )
        thread.start()
        threads.append(thread)
    
    # Wait for all to complete
    for thread in threads:
        thread.join()
    
    print("\nResult: Across 3 instances, ~20 decisions allowed before breach (distributed)")


# ────────────────────────────────────────────────────────────────────────
# EXAMPLE 2: Ecosystem-Level Limits (Fleet-Wide)
# ────────────────────────────────────────────────────────────────────────

def example_2_ecosystem_limits():
    """
    Per-agent limits AND global fleet limits.
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 2: Ecosystem (Fleet-Level) Limits")
    print("=" * 70)
    
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    # Per-agent: 10 decisions/min
    # Fleet-wide: 50 decisions/min (across all agents)
    breaker = DistributedVelocityBreaker(
        redis_client=redis_client,
        max_decisions=10,           # Per agent
        window_seconds=60,
        ecosystem_max=50,           # Fleet-wide
        ecosystem_window_seconds=60,
        cooldown_seconds=120,
    )
    
    agents = ["chatbot_1", "chatbot_2", "data_agent", "compliance_agent"]
    
    for agent_id in agents:
        # Each agent tries 15 decisions
        for i in range(15):
            triggered, reason, count = breaker.check(agent_id)
            
            if triggered:
                print(f"  {agent_id}[{i+1}]: ❌ {reason}")
            else:
                print(f"  {agent_id}[{i+1}]: ✓ Window={count}")
            
            time.sleep(0.05)
    
    print("\nResult: All agents can run, but total fleet stay under 50 decisions/min")


# ────────────────────────────────────────────────────────────────────────
# EXAMPLE 3: Fallback Behavior (Redis Failover)
# ────────────────────────────────────────────────────────────────────────

def example_3_redis_fallback():
    """
    What happens if Redis becomes unavailable?
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 3: Redis Failover Behavior")
    print("=" * 70)
    
    # Simulate: Connect to a Redis that might not exist
    redis_client = redis.Redis(
        host='localhost',
        port=6379,
        socket_connect_timeout=1,
        decode_responses=True,
    )
    
    # With fallback_mode=True (default), uses local in-memory if Redis fails
    breaker = DistributedVelocityBreaker(
        redis_client=redis_client,
        max_decisions=5,
        window_seconds=60,
        fallback_mode=True,  # Enable fallback to local
    )
    
    print("\nScenario: Redis becomes unavailable after 3 decisions")
    print("Expected: Fallback to local in-memory, allow ~5 more decisions\n")
    
    for i in range(10):
        triggered, reason, count = breaker.check("agent_1")
        
        if triggered:
            print(f"  Decision {i+1}: ❌ BLOCKED - {reason}")
        else:
            print(f"  Decision {i+1}: ✓ ALLOWED")
        
        # Simulate Redis going down after 3 decisions
        if i == 3:
            print("    [!] Redis connection lost")
            breaker._redis_available = False
        
        time.sleep(0.1)


# ────────────────────────────────────────────────────────────────────────
# EXAMPLE 4: Integration with GlassBox Pipeline
# ────────────────────────────────────────────────────────────────────────

def example_4_pipeline_integration():
    """
    How to integrate into GlassBox policy engine.
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 4: Pipeline Integration")
    print("=" * 70)
    
    from glassbox.governance.pipeline import GovernancePipeline
    
    # Create distributed breaker
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    velocity_breaker = create_velocity_breaker_distributed(
        redis_client=redis_client,
        max_decisions=100,  # Higher for pipeline
        window_seconds=60,
        fallback_mode=True,
    )
    
    # Integrate into pipeline
    pipeline = GovernancePipeline(
        policy_store=...,
        velocity_breaker=velocity_breaker,  # Use distributed breaker
        trust_evaluator=...,
    )
    
    # Every decision goes through distributed velocity check
    result = pipeline.run_governance_checks(
        agent_id="purchase_agent",
        action="approve_transaction",
        context={...},
    )
    
    print(f"Pipeline result: {result}")


# ────────────────────────────────────────────────────────────────────────
# EXAMPLE 5: Manual Reset & Monitoring
# ────────────────────────────────────────────────────────────────────────

def example_5_monitoring():
    """
    Monitor and reset velocity state.
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 5: Monitoring and Reset")
    print("=" * 70)
    
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    breaker = DistributedVelocityBreaker(
        redis_client=redis_client,
        max_decisions=5,
        window_seconds=60,
    )
    
    agent_id = "test_agent"
    
    # Fill velocity window
    print("\n1. Filling velocity window...")
    for i in range(5):
        triggered, reason, count = breaker.check(agent_id)
        print(f"   Decision {i+1}: count={count}, triggered={triggered}")
    
    # Check current count
    print(f"\n2. Current window count: {breaker._get_window_count(agent_id, time.time())}")
    
    # Reset
    print("\n3. Resetting velocity window...")
    breaker.reset_agent(agent_id)
    print(f"   Window count after reset: {breaker._get_window_count(agent_id, time.time())}")
    
    # Can now make decisions again
    triggered, reason, count = breaker.check(agent_id)
    print(f"   Next decision: count={count}, triggered={triggered} ✓")


# ────────────────────────────────────────────────────────────────────────
# EXAMPLE 6: Deployment Configuration
# ────────────────────────────────────────────────────────────────────────

def example_6_deployment_config():
    """
    Production deployment patterns.
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 6: Production Deployment Configuration")
    print("=" * 70)
    
    # Pattern 1: Single Redis with sentinel/cluster
    print("\n[Pattern 1] Redis Sentinel (HA)")
    from redis.sentinel import Sentinel
    sentinels = [('sentinel.internal', 26379)]
    sentinel = Sentinel(sentinels)
    redis_client = sentinel.master_for('glassbox-main', socket_timeout=2)
    
    breaker_with_sentinel = DistributedVelocityBreaker(
        redis_client=redis_client,
        max_decisions=1000,
        ecosystem_max=10000,
        fallback_mode=True,
    )
    print("✓ Configured with Sentinel failover")
    
    # Pattern 2: Redis Cluster (scaling)
    print("\n[Pattern 2] Redis Cluster (Scaling)")
    from redis.cluster import RedisCluster
    cluster_nodes = [
        {"host": "redis-node-1", "port": 6379},
        {"host": "redis-node-2", "port": 6379},
        {"host": "redis-node-3", "port": 6379},
    ]
    cluster = RedisCluster(startup_nodes=cluster_nodes)
    
    breaker_with_cluster = DistributedVelocityBreaker(
        redis_client=cluster,
        max_decisions=1000,
        fallback_mode=True,
    )
    print("✓ Configured with Redis Cluster")
    
    # Pattern 3: Environment-based config
    print("\n[Pattern 3] Environment-Based Config")
    import os
    
    def create_velocity_breaker_from_env():
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", 6379))
        max_decisions = int(os.getenv("VELOCITY_MAX_DECISIONS", 100))
        
        redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True,
        )
        
        return DistributedVelocityBreaker(
            redis_client=redis_client,
            max_decisions=max_decisions,
            fallback_mode=True,
        )
    
    breaker_from_env = create_velocity_breaker_from_env()
    print("✓ Configured from environment variables")
    
    print("\nExample commands to set:")
    print("  export REDIS_HOST=redis.internal")
    print("  export VELOCITY_MAX_DECISIONS=500")


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("GlassBox Distributed Velocity Breaker — Integration Examples")
    print("=" * 70)
    print("\nRequires: redis-py library")
    print("\nInstall: pip install redis")
    print("Run Redis: docker run -d -p 6379:6379 redis:latest")
    
    try:
        # Try Example 1 (basic)
        example_1_basic_multi_instance()
        
        # Example 3 (fallback is safer without full Redis)
        example_3_redis_fallback()
        
        # Example 5 (monitoring)
        example_5_monitoring()
        
        # Example 6 (configs)
        example_6_deployment_config()
        
        print("\n" + "=" * 70)
        print("✓ All examples completed")
        print("=" * 70)
    
    except Exception as exc:
        print(f"\n⚠ Example error (Redis may not be running): {exc}")
        print("\nTo run full examples, start Redis:")
        print("  docker run -d -p 6379:6379 redis:latest")
