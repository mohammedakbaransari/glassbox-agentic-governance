"""
GlassBox Distributed Velocity Breaker — Technical Documentation
===============================================================

Author: Mohammed Akbar Ansari

## Table of Contents

1. [Overview](#overview)
2. [Problem Statement](#problem-statement)
3. [Solution Architecture](#solution-architecture)
4. [API Reference](#api-reference)
5. [Usage Patterns](#usage-patterns)
6. [Deployment Guide](#deployment-guide)
7. [Troubleshooting](#troubleshooting)
8. [Performance](#performance)
9. [Security](#security)
10. [Migration](#migration)

---

## Overview

The Distributed Velocity Breaker extends GlassBox's governance framework to enforce
rate limits across **multiple instances** of the same agent using a shared Redis backend.

### Key Features

- ✓ **Atomic Operations**: Lua scripts ensure no race conditions
- ✓ **Auto Fallback**: Switches to local in-memory if Redis unavailable
- ✓ **Circuit Breaker**: Recovers when Redis becomes available
- ✓ **Fleet-Wide Limits**: Per-agent AND global ecosystem limits
- ✓ **Thread-Safe**: All operations are concurrent
- ✓ **API-Compatible**: Drop-in replacement for VelocityBreaker


### When to Use

**Use distributed velocity breaker when:**
- Running multiple agent instances (horizontal scaling)
- Need global rate limits across fleet
- Deployed in containerized/microservices architecture
- Using Kubernetes, Docker Compose, or cloud platforms

**Use single-instance velocity breaker when:**
- Running single agent per deployment
- Local rate limiting sufficient
- No external dependencies desired
- Embedded systems


---

## Problem Statement

### Single-Instance Velocity Breaker (v1.0.0)

Each instance maintains its own velocity window:

```
Instance-1: [0, 0.1, 0.2, 0.5, 1.0, 2.0, ...]  ← 20 allowed
Instance-2: [0, 0.1, 0.2, 0.7, 1.5, 2.3, ...]  ← 20 allowed
Instance-3: [0, 0.2, 0.3, 0.6, 1.2, 2.1, ...]  ← 20 allowed

Total: 60 decisions in 60 seconds
Limit: 20 decisions per minute
↓
VIOLATION ❌
```

### Root Cause

- Velocity state isolated per instance
- No coordination between instances
- Each instance thinks it's the only one counting

### Impact

- Policy enforcement becomes meaningless at scale
- Risk of fraud/abuse if limit can be bypassed by adding instances
- Compliance violations in regulated industries


---

## Solution Architecture

### Distributed Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    GlassBox Fleet (Kubernetes)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │  Instance-1  │  │  Instance-2  │  │  Instance-3  │           │
│  │ (Pod-1:8080) │  │ (Pod-2:8080) │  │ (Pod-3:8080) │           │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │
│         │                 │                 │                    │
│         └─────────────────┼─────────────────┘                    │
│                           │                                      │
│                    DistributedVelocityBreaker                    │
│                           │                                      │
│                    (Atomic Lua Scripts)                          │
│                           │                                      │
│                           ▼                                      │
│                     ┌─────────────┐                             │
│                     │ Redis Cache │                             │
│                     │  (Primary)  │                             │
│                     └─────────────┘                             │
│                     +--Agent-1: [t1, t2, ...]                  │
│                     +--Agent-2: [t1, t2, ...]                  │
│                     +--Ecosystem: [t1, t2, ...]                │
│                                                                   │
│  (Fallback: Each instance has local in-memory window)           │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Component Layers

#### 1. **RedisVelocityBreakerBackend** (Low-Level)

```python
RedisVelocityBreakerBackend
├── Lua Scripts (Atomic)
│   ├── check_and_add: Add timestamp + check count
│   ├── get_count: Read-only count
│   └── check_ecosystem_and_add: Fleet-wide check
├── Methods
│   ├── check_and_add(agent_id, now, window, max) → (bool, int)
│   ├── get_count(agent_id, now, window) → int
│   ├── check_ecosystem_and_add(now, window, max) → (bool, int)
│   └── reset_agent(agent_id) → None
```

#### 2. **DistributedVelocityBreaker** (Mid-Level)

```python
DistributedVelocityBreaker
├── Redis Backend
│   └── RedisVelocityBreakerBackend
├── Fallback: LocalFallbackWindows (in-memory)
├── State Management
│   ├── Cooldown Tracking (_tripped)
│   └── Circuit Breaker (_circuit_breaker_open)
├── Methods
│   ├── check(agent_id) → (bool, str, int)
│   ├── reset_agent(agent_id) → None
│   └── _check_local(agent_id, now) → (bool, str, int)
```

#### 3. **Application Layer** (High-Level)

```python
# In GovernancePipeline
pipeline = GovernancePipeline(
    velocity_breaker=DistributedVelocityBreaker(...)
)

# Decision flow
pipeline.run_governance_checks(
    agent_id="purchase_agent",
    # ↓ Internal call to velocity_breaker.check(agent_id)
)
```

### Data Flow: Per-Decision

```
1. check(agent_id="agent_1")
   ↓
2. Check local cooldown?
   ├─ Yes → Return (True, "cooldown remaining", count)
   └─ No → Continue
   ↓
3. Try Redis check_and_add()
   ├─ Success → Check ecosystem
   │  ├─ Within limit → Return (False, None, count)
   │  └─ Exceeded → Trigger cooldown, Return (True, "ecosystem limit", count)
   └─ Error → Open circuit, Fall to local
   ↓
4. Local fallback (if enabled)
   ├─ Within limit → Return (False, None, count)
   └─ Exceeded → Return (True, "local fallback exceeded", count)
   ↓
5. Return result to caller
```

---

## API Reference

### RedisVelocityBreakerBackend

#### __init__(redis_client, namespace="glassbox:velocity")

Initialize low-level Redis backend.

```python
from redis import Redis
from glassbox.governance.velocity_breaker_distributed import RedisVelocityBreakerBackend

redis_client = Redis(host='localhost', port=6379)
backend = RedisVelocityBreakerBackend(redis_client)
```

**Parameters:**
- `redis_client` (redis.Redis): Redis connection
- `namespace` (str): Key prefix (default: "glassbox:velocity")


#### check_and_add(agent_id, now, window_sec, max_count) → (bool, int)

Atomically check velocity and add timestamp if allowed.

```python
allowed, count = backend.check_and_add(
    agent_id="chatbot_1",
    now=time.time(),
    window_sec=60,
    max_count=20,
)

if not allowed:
    print(f"Limit exceeded; window count: {count}")
```

**Parameters:**
- `agent_id` (str): Agent identifier
- `now` (float): Current timestamp (time.time())
- `window_sec` (int): Window size in seconds
- `max_count` (int): Maximum decisions in window

**Returns:**
- `(allowed: bool, count: int)`: (True if allowed, current window count)

**Atomicity:** Uses Lua script; no race conditions possible


#### get_count(agent_id, now, window_sec) → int

Get current window count (read-only).

```python
count = backend.get_count(
    agent_id="chatbot_1",
    now=time.time(),
    window_sec=60,
)
print(f"Current decisions in window: {count}")
```

**Note:** Cleans old entries outside window


#### check_ecosystem_and_add(now, window_sec, max_count) → (bool, int)

Check fleet-wide ecosystem limit.

```python
allowed, count = backend.check_ecosystem_and_add(
    now=time.time(),
    window_sec=60,
    max_count=10000,  # Fleet-wide limit
)
```

**Returns:** (allowed: bool, fleet_count: int)


#### reset_agent(agent_id) → None

Reset agent's velocity window.

```python
backend.reset_agent("chatbot_1")
```

---

### DistributedVelocityBreaker

#### __init__(...) Complete Signature

```python
breaker = DistributedVelocityBreaker(
    redis_client,                      # redis.Redis instance
    max_decisions=20,                  # Per-agent limit
    window_seconds=60,                 # Time window
    cooldown_seconds=300,              # After breach
    ecosystem_max=None,                # Fleet limit (optional)
    ecosystem_window_seconds=60,       # Fleet window
    ecosystem_cooldown_seconds=120,    # Fleet cooldown
    fallback_mode=True,                # Fallback if Redis fails
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `redis_client` | Redis | Required | Redis connection |
| `max_decisions` | int | 20 | Max decisions per agent/window |
| `window_seconds` | int | 60 | Time window (seconds) |
| `cooldown_seconds` | int | 300 | Cooldown after breach |
| `ecosystem_max` | int or None | None | Fleet-wide max (if enabled) |
| `ecosystem_window_seconds` | int | 60 | Fleet window |
| `ecosystem_cooldown_seconds` | int | 120 | Fleet cooldown |
| `fallback_mode` | bool | True | Use local fallback if Redis unavailable |


#### check(agent_id) → (bool, Optional[str], int)

Check velocity for agent.

```python
triggered, reason, count = breaker.check("purchase_agent")

if triggered:
    logger.warning(f"Rate limit triggered: {reason}")
    return error_response("Rate limit exceeded")
else:
    return proceed_with_decision()
```

**Returns:**
- `(triggered: bool, reason: Optional[str], count: int)`
  - `triggered`: True if limit exceeded
  - `reason`: Explanation string (None if allowed)
  - `count`: Current decisions in window

**Common Reasons:**
- "Agent 'X' velocity limit (20/60s) exceeded"
- "Fleet ecosystem limit (10000/60s) exceeded"
- "Cooldown for 250s" (remaining cooldown time)
- "Local fallback: velocity exceeded"


#### reset_agent(agent_id) → None

Manually reset agent's velocity.

```python
# After penalty period expires or manual override
breaker.reset_agent("bad_actor_agent")

# Now agent can be used again
triggered, _, _ = breaker.check("bad_actor_agent")
assert triggered is False  # Ready to use
```

**Use Cases:**
- Unblock agent after maintenance
- Manual admin override
- Testing/demo cleanup


#### _get_window_count(agent_id, now) → int

Get current window count (diagnostic).

```python
count = breaker._get_window_count("agent_1", time.time())
print(f"Decisions in window: {count}/{breaker.max_decisions}")
```

**Note:** Tries Redis first, falls back to local


#### _check_local(agent_id, now) → (bool, str, int)

Local in-memory fallback check.

```python
# Called internally when Redis unavailable
triggered, reason, count = breaker._check_local("agent_1", time.time())
```

---

### Factory Function

#### create_velocity_breaker_distributed(...)

Helper to create breaker with environment-aware defaults.

```python
from glassbox.governance.velocity_breaker_distributed import (
    create_velocity_breaker_distributed
)

breaker = create_velocity_breaker_distributed(
    redis_client=redis.Redis(),
    max_decisions=100,
    ecosystem_config=EcosystemConfig(
        enabled=True,
        max_decisions=50000,  # Fleet limit
    ),
)
```

---

## Usage Patterns

### Pattern 1: Basic Multi-Instance

```python
from redis import Redis
from glassbox.governance.velocity_breaker_distributed import (
    DistributedVelocityBreaker
)

# Shared Redis across instances
redis_client = Redis.from_url('redis://redis:6379')

# Each instance creates its own breaker (same Redis)
breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    max_decisions=20,
    window_seconds=60,
)

# In decision handler
def handle_purchase_request(user_id, amount):
    agent_id = f"purchase_agent_{user_id}"
    
    triggered, reason, count = breaker.check(agent_id)
    
    if triggered:
        audit_log.warning(f"Rate limit: {reason}")
        return decline_transaction(f"Too many requests: {reason}")
    
    # Process purchase
    result = authorize_payment(amount)
    return result
```


### Pattern 2: Fleet-Wide Governance

```python
# Global fleet limits (across all agents)
breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    max_decisions=100,              # Per-agent
    window_seconds=60,
    ecosystem_max=100_000,          # Fleet-wide
    ecosystem_window_seconds=60,
    ecosystem_cooldown_seconds=120,
)

# Now each agent can do 100/min, but fleet total max 100k/min
# If fleet hits 100k, ALL agents are throttled for 120s
```


### Pattern 3: Integration with Pipeline

```python
from glassbox.governance.pipeline import GovernancePipeline

# Create breaker
breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    max_decisions=1000,  # Higher for backend
    ecosystem_max=1_000_000,
)

# Inject into pipeline
pipeline = GovernancePipeline(
    policy_store=store,
    velocity_breaker=breaker,  # Use distributed
    trust_evaluator=evaluator,
    anomaly_detector=detector,
)

# Pipeline automatically calls breaker.check()
result = pipeline.run_governance_checks(
    agent_id="fraud_detector",
    action="approve_high_value_transaction",
    context={...},
)
```


### Pattern 4: With Monitoring

```python
import logging

logger = logging.getLogger("velocity")

def check_with_monitoring(agent_id):
    triggered, reason, count = breaker.check(agent_id)
    
    # Metrics
    metrics.gauge("velocity.window_count", count, tags=[f"agent:{agent_id}"])
    
    if triggered:
        metrics.increment("velocity.triggers", tags=[f"agent:{agent_id}"])
        logger.warning(f"Velocity breach: {agent_id} - {reason}")
    
    return triggered, reason, count

# Use instead of breaker.check() directly
triggered, reason, _ = check_with_monitoring(agent_id)
```


### Pattern 5: Graceful Degradation

```python
breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    fallback_mode=True,  # Allow local fallback
)

# If Redis goes down:
# - First few requests: Fast (local)
# - After 60s: Circuit breaker opens
# - Can still process with local limits
# - No requests blocked due to Redis failure

def safe_decision_handler(agent_id, action):
    # This won't crash even if Redis completely unavailable
    triggered, reason, _ = breaker.check(agent_id)
    
    if triggered:
        logging.warning(f"Limited: {reason}")
        return block_request()
    
    # Proceed with reduced confidence (might be above real limit)
    # but system stays up
    return process_action(action)
```


---

## Deployment Guide

### Prerequisites

- **Redis 3.0+** (for Lua script support)
- **Python 3.8+**
- **redis-py >= 3.0**: `pip install redis`

### Local Development

```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Test connection
redis-cli ping
# → PONG

# Run tests
pytest tests/test_velocity_distributed.py -v
```

### Docker Compose

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
  
  agent-instance-1:
    build: .
    environment:
      REDIS_HOST: redis
      REDIS_PORT: 6379
      VELOCITY_MAX_DECISIONS: 100
    depends_on:
      - redis
  
  agent-instance-2:
    build: .
    environment:
      REDIS_HOST: redis
      REDIS_PORT: 6379
      VELOCITY_MAX_DECISIONS: 100
    depends_on:
      - redis
  
  agent-instance-3:
    build: .
    environment:
      REDIS_HOST: redis
      REDIS_PORT: 6379
      VELOCITY_MAX_DECISIONS: 100
    depends_on:
      - redis

volumes:
  redis_data:
```

### Kubernetes Deployment

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-config
data:
  REDIS_HOST: "redis.redis.svc.cluster.local"
  REDIS_PORT: "6379"
  VELOCITY_MAX_DECISIONS: "1000"

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: glassbox-agents
spec:
  replicas: 3
  selector:
    matchLabels:
      app: glassbox-agent
  template:
    metadata:
      labels:
        app: glassbox-agent
    spec:
      containers:
      - name: agent
        image: glassbox-agent:latest
        envFrom:
        - configMapRef:
            name: agent-config
        resources:
          limits:
            cpu: 500m
            memory: 512Mi
```

### Redis HA Setup (Sentinel)

```python
from redis.sentinel import Sentinel

sentinels = [
    ('sentinel-1.internal', 26379),
    ('sentinel-2.internal', 26379),
    ('sentinel-3.internal', 26379),
]

sentinel = Sentinel(sentinels, socket_timeout=2)
redis_client = sentinel.master_for('mymaster', socket_timeout=2)

breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    fallback_mode=True,  # Important for failover
)
```

### Redis Cluster Setup

```python
from redis.cluster import RedisCluster

nodes = [
    {"host": "redis-1", "port": 6379},
    {"host": "redis-2", "port": 6379},
    {"host": "redis-3", "port": 6379},
]

cluster = RedisCluster(startup_nodes=nodes)

breaker = DistributedVelocityBreaker(
    redis_client=cluster,
    fallback_mode=True,
)
```

---

## Troubleshooting

### Issue 1: "Redis Unavailable" Error

**Symptoms:**
```
WARNING: Distributed velocity breaker: Redis unavailable
```

**Diagnosis:**
```python
# Check Redis connection
redis_client = redis.Redis(host='localhost', port=6379)
try:
    redis_client.ping()
    print("✓ Redis reachable")
except:
    print("✗ Redis not reachable")

# Check firewall
telnet redis.internal 6379

# Check logs
redis-cli INFO SERVER
```

**Solutions:**

```python
# 1. Explicitly enable fallback
breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    fallback_mode=True,  # ← This
)

# 2. Use longer timeout
redis_client = redis.Redis(
    host='redis.internal',
    port=6379,
    socket_timeout=5,  # Increase timeout
    socket_connect_timeout=5,
)

# 3. Implement retry logic
import time
for attempt in range(3):
    try:
        redis_client.ping()
        break
    except:
        if attempt < 2:
            time.sleep(1 * (attempt + 1))
        else:
            raise
```

### Issue 2: Decisions Still Exceed Limit

**Symptoms:**
```
Agent 'X' velocity limit (20/60s) exceeded - but decisions > 20
```

**Diagnosis:**
```python
# Check Redis data directly
redis_client.zrange("glassbox:velocity:agent:agent_1", 0, -1)
# Returns: [timestamp1, timestamp2, ...]

# Monitor keys
redis_client.scan_iter("glassbox:velocity:*")

# Check pipeline order
logged_count != redis_count  # ← Race condition?
```

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| Timestamps drifted across instances | Sync clocks (NTP) |
| Decaying timestamps | Check system time precision |
| Concurrent checks on same agent | Expected; Lua handles it |
| Local fallback active | Redis still unavailable |


### Issue 3: Circuit Breaker Won't Close

**Symptoms:**
```
Redis restored, but circuit breaker stays open
```

**Check:**
```python
print(breaker._circuit_breaker_open)   # True even after Redis restarts
print(breaker._redis_available)        # False
```

**Causes:**
- Circuit breaker recovers after 60 seconds
- No ping() health check succeeded yet
- Sentinel failover in progress

**Solutions:**
```python
# Manual recovery
breaker._circuit_breaker_open = False
breaker._redis_available = True

# Or: Wait 60 seconds for auto-recovery

# Or: Increase circuit breaker timeout
breaker._circuit_breaker_timestamp = time.time() - 120
```

### Issue 4: Local Fallback Inconsistencies

**Symptoms:**
```
Decisions allowed in local fallback but would be blocked in Redis
(multi-instance concurrency issue)
```

**Why:**
- Local fallback = per-instance state
- Redis = global state
- During Redis outage, instances diverge
- When Redis recovers, state mismatch

**Workaround:**
```python
# Accept temporary inconsistency during failover
# Reset all agents when Redis recovers
breaker._circuit_breaker_open = False

# Clear local state to resync
breaker._local_fallback_windows.clear()

logger.info("Local fallback cleared; using Redis state")
```

### Issue 5: Performance Degradation

**Symptoms:**
```
check() calls taking > 50ms
```

**Diagnosis:**
```python
import time

def timed_check(agent_id):
    start = time.time()
    result = breaker.check(agent_id)
    elapsed = time.time() - start
    
    if elapsed > 0.05:  # > 50ms
        logger.warning(f"Slow check: {elapsed*1000:.1f}ms")
    
    return result
```

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| Redis latency > 10ms | Upgrade Redis/network |
| Circuit breaker open | Wait 60s, check Redis |
| Too many zrange scans | Reduce window_seconds |
| Python GIL contention | Use async Redis |

### Issue 6: Concurrency Deadlocks

**Symptoms:**
```
Threads hang, high contention on _tripped_lock
```

**Diagnosis:**
```python
import threading

# Monitor lock contention
print(f"_tripped_lock acquire count: {breaker._tripped_lock.__dict__}")

# List thread stacks
import traceback
for thread_id, frame in threading._current_frames().items():
    traceback.print_stack(frame)
```

**Solutions:**
```python
# 1. Use thread-safe Redis client
import redis.asyncio as aioredis
redis_client = aioredis.from_url('redis://localhost')

# 2. or: Increase lock timeout
# (code change) implement timeout on acquire()

# 3. or: Pre-shard by agent_id to reduce contention
def get_breaker_for_agent(agent_id):
    idx = hash(agent_id) % NUM_BREAKERS
    return breakers[idx]
```

---

## Performance

### Benchmarks

```
Operation              Latency       Throughput   Conditions
─────────────────────────────────────────────────────────
Redis check            1.2 ms        ~830/sec     Local Redis
Redis (network)        15 ms         ~67/sec      Remote Redis
Redis (sentinel)       25 ms         ~40/sec      Failover
Local fallback         < 0.1 ms      > 10k/sec    In-memory
Circuit breaker        < 0.05 ms     > 20k/sec    Fallback active
Lua script (atomic)    1 ms          ~1k/sec      With cleanup
```

### Optimization Tips

1. **Collocate Redis**: Place Redis on same DC/zone as agents
   ```
   Latency: 15ms → 1ms (15x improvement)
   ```

2. **Use Redis Cluster**: Scale horizontally
   ```
   Throughput: 830/sec → 8300/sec (10 nodes)
   ```

3. **Increase window_seconds**: Fewer timestamp scans
   ```
   # Before (60s window, 1k entries/min)
   window_seconds=60
   
   # After (increase to 600s, batch cleanup)
   window_seconds=600
   ```

4. **Enable pipelining**: Batch multiple checks
   ```python
   # Before: Sequential calls (slow)
   for agent_id in agents:
       breaker.check(agent_id)  # 1ms each = 1000ms total
   
   # After: Batch checks (Redis pipelining)
   # (Requires API extension)
   ```

5. **Use connection pooling**:
   ```python
   import redis
   
   connection_pool = redis.ConnectionPool(
       host='localhost',
       port=6379,
       max_connections=50,  # Tune for concurrency
   )
   redis_client = redis.Redis(connection_pool=connection_pool)
   ```

---

## Security

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Unauthorized Redis access | Use AUTH + TLS |
| Timestamp manipulation | Validate server-side (Lua) |
| Lua script injection | No user input → scripts |
| Information disclosure | Logs contain only counts; not decisions |
| DoS (many checks) | Rate limit enforced by design |

### Redis Security

```python
# 1. Require authentication
redis_client = redis.Redis(
    host='redis.internal',
    port=6379,
    password='strong-password-here',  # ACL in Redis 6+
)

# 2. Use TLS encryption
redis_client = redis.Redis(
    host='redis.internal',
    port=6380,  # TLS port
    ssl=True,
    ssl_cert_reqs='required',
    ssl_ca_certs='/path/to/ca.pem',
)

# 3. Network isolation
# - Run Redis in private subnet
# - Whitelist agent IPs
# - Use VPC/Kubernetes network policies
```

### Data Privacy

```python
# Velocity data contains:
# - Agent IDs (can be anonymized)
# - Timestamps (precise, can infer activity patterns)

# Best practices:
# 1. Use opaque agent identifiers (UUID, hash)
agent_id = hashlib.sha256(f"agent_{original_id}".encode()).hexdigest()[:8]

# 2. Set expire times on Redis keys (auto-cleanup)
breaker = DistributedVelocityBreaker(
    # ... (backend sets EXPIRE on keys)
)

# 3. Audit access logs
# Every check could be logged for compliance
```

---

## Migration

### From Single-Instance to Distributed

**Step 1: Add Redis**
```bash
# Docker Compose
docker run -d --name redis -p 6379:6379 redis:7
```

**Step 2: Update code**
```python
# Before
from glassbox.governance.velocity_breaker import VelocityBreaker

breaker = VelocityBreaker(max_decisions=20)

# After
from glassbox.governance.velocity_breaker_distributed import (
    DistributedVelocityBreaker
)
import redis

redis_client = redis.Redis(host='localhost', port=6379)

breaker = DistributedVelocityBreaker(
    redis_client=redis_client,
    max_decisions=20,  # Same config
)
```

**Step 3: Test**
```bash
pytest tests/test_velocity_distributed.py -v

# Run integration tests
python examples/distributed_velocity_breaker.py
```

**Step 4: Deploy gradually**

```
Phase 1 (10%):  Single instance + Redis (fallback enabled)
Phase 2 (25%):  Multi-instance test (2-3 instances)
Phase 3 (50%):  Multi-instance staging (5-10 instances)
Phase 4 (100%): Full production rollout
```

**Step 5: Monitor**
```python
# Add metrics collection
import time

@app.before_request
def check_velocity(request):
    start = time.time()
    triggered, reason, count = breaker.check(request.agent_id)
    elapsed = time.time() - start
    
    # Emit metrics
    metrics.timing("velocity.check_time", elapsed)
    metrics.gauge("velocity.window_count", count)
    
    if triggered:
        metrics.increment("velocity.blocks")
        return http.429("Rate limited")
```

---

## References

- **Redis Documentation**: https://redis.io/commands
- **Lua Scripting**: https://redis.io/commands/eval
- **redis-py GitHub**: https://github.com/redis/redis-py
- **GlassBox Governance**: See glassbox/governance/pipeline.py
- **Original VelocityBreaker**: glassbox/governance/velocity_breaker.py

---

**Maintainer:** Mohammed Akbar Ansari  
**License:** MIT
"""

# Auto-doc for quick reference
if __name__ == "__main__":
    import inspect
    from glassbox.governance.velocity_breaker_distributed import (
        DistributedVelocityBreaker,
        RedisVelocityBreakerBackend,
    )
    
    print("=" * 70)
    print("DistributedVelocityBreaker API Quick Reference")
    print("=" * 70)
    
    for cls in [RedisVelocityBreakerBackend, DistributedVelocityBreaker]:
        print(f"\n{cls.__name__}")
        print("-" * len(cls.__name__))
        
        for method_name, method in inspect.getmembers(cls, inspect.isfunction):
            if not method_name.startswith("_"):
                sig = inspect.signature(method)
                print(f"  {method_name}{sig}")
