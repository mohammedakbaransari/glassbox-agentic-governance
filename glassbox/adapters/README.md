# glassbox/adapters — Platform & Spark Adapters

The `adapters` package provides platform-specific configuration and PySpark integration.

| Module | Role |
|---|---|
| `platforms.py` | `DatabricksAdapter`, `KubernetesAdapter`, `FabricAdapter`, `BaseAdapter`, `auto_detect_adapter()` |
| `spark.py` | `GlassBoxSparkAdapter` — UDF, mapPartitions, Structured Streaming |

---

## Quick Start: Platform Detection

```python
from glassbox.adapters.platforms import auto_detect_adapter

# Auto-detect platform (Databricks > K8s > Fabric > VM)
adapter = auto_detect_adapter()
pipeline = adapter.create_pipeline()

# Execute in platform-native way
result = pipeline.process({"amount": 5000, "user_id": 123})
print(f"Disposition: {result.disposition}")
```

---

## Platform-Specific Adapters

### Adapter 1: Databricks

```python
from glassbox.adapters.platforms import DatabricksAdapter
from glassbox.governance.pipeline import GovernancePipeline

# Databricks auto-detects workspace/cluster
adapter = DatabricksAdapter()
pipeline = adapter.create_pipeline()

# Use Databricks Job Clusters for scale
config = {
    "job_cluster_key": "governance-cluster",
    "spark_version": "14.3.x-scala2.12",
    "num_workers": 4,
    "node_type_id": "i3.xlarge"
}

adapter.configure_cluster(config)

# Execute decision on cluster
loan_data = spark.read.parquet("/data/loan_applications")
decisions = pipeline.batch_execute(loan_data)
decisions.write.parquet("/out/decisions")
```

**Databricks-specific features:**
- Automatic Photon optimization for queries
- Unity Catalog integration for audit logging
- Secrets retrieved from Databricks Secrets store
- Auto-scaling for large batch jobs

### Adapter 2: Kubernetes (K8s)

```python
from glassbox.adapters.platforms import KubernetesAdapter
from glassbox.governance.pipeline import GovernancePipeline

adapter = KubernetesAdapter()
pipeline = adapter.create_pipeline()

# Optionally configure K8s specifics
adapter.configure(
    namespace="governance",
    service_account="glassbox-svc",
    requests={"memory": "512Mi", "cpu": "250m"},
    limits={"memory": "2Gi", "cpu": "1000m"}
)

# Execute in Kubernetes pod
result = pipeline.process({"order_id": "ORD-123", "amount": 50000})

# Auto-restart on failure; logs to stdout captured by K8s
import logging
logging.basicConfig(level=logging.INFO)
```

**K8s-specific features:**
- Horizontal Pod Autoscaling (HPA) for load
- ConfigMaps for policy distributions
- Secrets API for sensitive data
- Liveness probes for health checks

### Adapter 3: Fabric (Microsoft)

```python
from glassbox.adapters.platforms import FabricAdapter
from glassbox.governance.pipeline import GovernancePipeline

adapter = FabricAdapter(
    workspace_url="https://app.powerbi.com/",
    fabric_capacity_id="capacity-xyz"
)
pipeline = adapter.create_pipeline()

# Execute in Fabric compute
tenants_data = load_from_fabric_lakehouse("tenants")
decisions = pipeline.batch_execute(tenants_data)
write_to_fabric_lakehouse(decisions, "decisions")
```

**Fabric-specific features:**
- Power BI integration for dashboards
- OneLake for unified storage
- Fabric compute capacity reuse
- Automatic query optimization

### Adapter 4: VM / Local

```python
from glassbox.adapters.platforms import BaseAdapter
from glassbox.governance.pipeline import GovernancePipeline

# Default adapter for VMs, containers, local dev
adapter = BaseAdapter()
pipeline = adapter.create_pipeline()

# Standard execution
result = pipeline.process({"decision": "approve_credit", "score": 750})
print(f"Result: {result.disposition}")
```

---

## PySpark Integration

### Pattern 1: UDF (Driver-Side Processing)

```python
from glassbox.adapters.spark import GlassBoxSparkAdapter
from glassbox.governance.pipeline import GovernancePipeline

spark = (SparkSession.builder
    .appName("governance")
    .getOrCreate())

adapter = GlassBoxSparkAdapter(spark)
pipeline = GovernancePipeline()

# Define UDF-based governance
@adapter.create_udf(pipeline)
def govern_decision(amount, vendor_id, user_id):
    """Governance UDF runs on driver for each row (slow for large data)"""
    payload = {"amount": amount, "vendor_id": vendor_id}
    result = pipeline.process(payload)
    return result.disposition

# Use in Spark SQL
spark.udf.register("govern_decision", govern_decision)
df_governed = spark.sql("""
    SELECT *,
           govern_decision(amount, vendor_id, user_id) AS disposition
    FROM procurement_requests
""")

df_governed.show()
```

**When to use:** Small DataFrames (< 100M rows), simple policies, debugging

**Limitations:** 
- Slow: serialization/deserialization overhead
- Driver bottleneck: single thread processes all rows
- Memory: driver memory limits throughput

### Pattern 2: mapPartitions (Executor-Side Processing)

```python
# Governance runs on executors in parallel (fast for large data)
def govern_partition(partition_iterator):
    """Each executor processes its partition independently"""
    pipeline = GovernancePipeline()  # Initialize per executor
    
    for row in partition_iterator:
        payload = {
            "amount": row.amount,
            "vendor_id": row.vendor_id,
            "user_id": row.user_id
        }
        result = pipeline.process(payload)
        
        # Emit governed row
        yield (row.amount, row.vendor_id, row.user_id, result.disposition)

# Apply partition-by-partition
df = spark.read.parquet("/data/requests")
df_governed = df.rdd.mapPartitions(govern_partition).toDF(
    ["amount", "vendor_id", "user_id", "disposition"]
)

df_governed.write.mode("overwrite").parquet("/out/decisions")
```

**When to use:** Large DataFrames (> 100M rows), batch jobs, production

**Benefits:**
- Parallel: each executor processes independently
- Efficient: minimal serialization
- Scalable: O(num_partitions), not O(rows)

### Pattern 3: Structured Streaming

```python
# Real-time governance for streaming data
adapter = GlassBoxSparkAdapter(spark)
pipeline = adapter.create_pipeline()

# Read stream from Kafka
stream = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:9092") \
    .option("subscribe", "procurement-requests") \
    .load()

# Parse JSON
from pyspark.sql.functions import col, from_json
parsed = stream.select(
    from_json(col("value").cast("string"), 
              "amount INT, vendor_id STRING, user_id STRING").alias("data")
).select("data.*")

# Governance UDF on stream
governed = adapter.govern_stream(
    parsed,
    output_path="/out/stream-decisions",
    checkpoint_location="/tmp/checkpoint",
    window_minutes=5
)

# Write results back to Kafka
query = governed.writeStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:9092") \
    .option("topic", "governance-decisions") \
    .option("checkpointLocation", "/tmp/checkpoint") \
    .start()

query.awaitTermination()
```

**When to use:** Real-time decision streams, continuous governance

---

## Performance Characteristics

| Operation | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| UDF pattern | 1–10 ms/row | 100–1K rows/sec | Driver-side; serial |
| mapPartitions | 0.1–1 ms/row | 1K–100K rows/sec | Executor-side; parallel |
| Streaming | 100–500 ms/micro-batch | 1K–10K rows/sec | With 5-min windows |
| Databricks cluster | — | 10K–100K rows/sec | With auto-scaling |
| K8s with HPA | — | 1K–10K rows/sec | Pod scaling latency |

**Tuning:**
```python
# Increase partitions for parallelism
df = spark.read.parquet("/data/large_file").repartition(100)

# Tune batch interval for streaming
query = governed.writeStream \
    .trigger(processingTime="10 seconds") \  # Batch every 10s
    .start()
```

---

## Common Errors

### Error: "Driver out of memory; UDF failed"

**Symptom:**
```
PythonException: Driver memory exceeded (4GB limit)
All UDFs fail; jobs crash
```

**Cause:** Processing large DataFrame with UDF (all data goes through driver)

**Solution:**
```python
# Option 1: Use mapPartitions instead (executor-side)
df_governed = df.rdd.mapPartitions(govern_partition).toDF(...)

# Option 2: Increase driver memory
spark = SparkSession.builder \
    .config("spark.driver.memory", "16g") \
    .getOrCreate()

# Option 3: Process smaller batches
batch_size = 100_000
for i in range(0, df.count(), batch_size):
    batch = df.limit(batch_size).offset(i)
    governed = apply_udf_to_batch(batch)
```

### Error: "Executor lost; mapPartitions job failed"

**Symptom:**
```
lost 2 executors in stage
Task failed: executor lost
```

**Cause:** Executor crashed; partition processing failed

**Solution:**
```python
# Option 1: Increase executor memory/cores
spark = SparkSession.builder \
    .config("spark.executor.memory", "4g") \
    .config("spark.executor.cores", 4) \
    .getOrCreate()

# Option 2: Reduce partition size (more, smaller partitions)
df = df.repartition(200)  # More partitions → smaller workload per partition

# Option 3: Add error handling in mapPartitions
def safe_govern_partition(partition_iterator):
    pipeline = GovernancePipeline()
    
    for row in partition_iterator:
        try:
            result = pipeline.process({"amount": row.amount})
            yield (row, result.disposition)
        except Exception as e:
            logging.error(f"Governance failed for {row}: {e}")
            yield (row, "error")  # Fallback decision
```

### Error: "Platform adapter not detected"

**Symptom:**
```python
adapter = auto_detect_adapter()
# Returns: BaseAdapter (default)
# Expected: DatabricksAdapter or KubernetesAdapter
```

**Cause:** Running in environment adapter cannot recognize

**Solution:**
```python
# Option 1: Explicitly specify adapter
from glassbox.adapters.platforms import DatabricksAdapter
adapter = DatabricksAdapter()  # Force Databricks

# Option 2: Check environment variables
import os
if "DATABRICKS_HOST" in os.environ:
    adapter = DatabricksAdapter()
elif "KUBERNETES_SERVICE_HOST" in os.environ:
    adapter = KubernetesAdapter()
else:
    adapter = BaseAdapter()
```

### Error: "Streaming query died in background"

**Symptom:**
```
StreamingQueryException: Query terminated
No logs indicating failure
Governance ceased; no new decisions processed
```

**Cause:** Checkpoint corrupted, storage unreachable, or internal error

**Solution:**
```python
# Option 1: Monitor stream health
query = governed.writeStream.start()

while query.isActive:
    if query.lastProgress is None:
        print("No progress; stream may be stalled")
    
    if query.status.isDataAvailable == False:
        print("No new data; check source")
    
    time.sleep(60)

# Option 2: Clear checkpoint and restart
import shutil
shutil.rmtree("/tmp/checkpoint")  # Clear corrupted state
query = governed.writeStream \
    .option("checkpointLocation", "/tmp/checkpoint") \
    .start()  # Restart; reprocess from latest

# Option 3: Add exception handler
from pyspark.sql import StreamingQuery

try:
    query.awaitTermination()
except Exception as e:
    logging.error(f"Streaming failed: {e}")
    notify_ops(f"Governance stream failure: {e}")
    # Re-trigger or fallback
```

---

## Multi-Tenant Spark Jobs

```python
# Process multiple tenants in parallel
tenants = ["tenant-a", "tenant-b", "tenant-c"]

for tenant_id in tenants:
    # Each tenant gets its own pipeline config
    config = load_tenant_config(tenant_id)
    pipeline = GovernancePipeline(**config)
    
    # Read tenant data
    df = spark.read.parquet(f"/data/{tenant_id}/requests")
    
    # Govern with tenant-specific policy
    governed = adapter.govern_dataframe(df, tenant_id=tenant_id)
    
    # Write results
    governed.write.mode("overwrite").parquet(f"/out/{tenant_id}/decisions")
```

---

See [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md) for platform-specific deployment guides and [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#platform-adapters) for technical details.
