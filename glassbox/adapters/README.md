# glassbox/adapters — Platform & Spark Adapters

The `adapters` package provides platform-specific configuration and PySpark integration.

| Module | Role |
|---|---|
| `platforms.py` | `DatabricksAdapter`, `KubernetesAdapter`, `FabricAdapter`, `BaseAdapter`, `auto_detect_adapter()` |
| `spark.py` | `GlassBoxSparkAdapter` — UDF, mapPartitions, Structured Streaming |

**Platform auto-detection:**
```python
from glassbox.adapters.platforms import auto_detect_adapter
pipeline = auto_detect_adapter().create_pipeline()
```

**PySpark — three patterns:**
```python
adapter = GlassBoxSparkAdapter(spark)

# Pattern 1: UDF (driver-side, small DataFrames)
result = adapter.govern_dataframe(df)

# Pattern 2: mapPartitions (executor-local, scalable)
result = adapter.govern_dataframe(df, partition_mode=True)

# Pattern 3: Structured Streaming
query = adapter.govern_stream(stream_df, output_path, checkpoint)
```

See [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md) for platform-specific guides.
