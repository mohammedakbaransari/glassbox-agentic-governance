"""
GlassBox — Thread Pool Configuration & Queue Monitoring
==============================================================

Configuration:
  - Async workers: 50 * cpu_count()
  - Queue depth monitoring with alerts > 1000
  - Thread pool executor for async operations
"""

import multiprocessing
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, Callable
from collections import deque

log = logging.getLogger("glassbox.threadpool")


class ThreadPoolConfig:
    """Thread pool configuration for GlassBox."""
    
    def __init__(self, async_workers: Optional[int] = None):
        """
        Initialize thread pool config.
        
        Args:
            async_workers: Number of async workers. If None, uses 50 * cpu_count()
        """
        if async_workers is None:
            # Default: 50 workers per CPU core
            cpu_count = multiprocessing.cpu_count()
            self.async_workers = 50 * cpu_count
        else:
            self.async_workers = async_workers
        
        log.info(f"ThreadPoolConfig: async_workers={self.async_workers}")
    
    def create_executor(self, max_workers: Optional[int] = None) -> ThreadPoolExecutor:
        """Create thread pool executor."""
        workers = max_workers or self.async_workers
        return ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="glassbox-exec",
        )


class QueueDepthMonitor:
    """Monitor queue depth with alerts when threshold exceeded."""
    
    def __init__(self, max_depth_alert: int = 1000):
        """
        Initialize queue monitor.
        
        Args:
            max_depth_alert: Alert threshold (default 1000)
        """
        self.max_depth_alert = max_depth_alert
        
        # Track per-queue statistics
        self._queues: Dict[str, deque] = {}
        self._queue_stats: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        
        # Alert history
        self._alerts_triggered: Dict[str, float] = {}  # queue_id -> timestamp
        
        log.info(f"QueueDepthMonitor: alert_threshold={max_depth_alert}")
    
    def register_queue(self, queue_id: str) -> None:
        """Register a queue for monitoring."""
        with self._lock:
            if queue_id not in self._queues:
                self._queues[queue_id] = deque(maxlen=int(self.max_depth_alert * 1.5))
                self._queue_stats[queue_id] = {
                    "max_depth": 0,
                    "current_depth": 0,
                    "items_processed": 0,
                    "alerts_count": 0,
                }
                log.info(f"Registered queue: {queue_id}")
    
    def record_item(self, queue_id: str, item: Any) -> None:
        """Record item added to queue."""
        self.register_queue(queue_id)
        
        with self._lock:
            self._queues[queue_id].append(item)
            current_depth = len(self._queues[queue_id])
            stats = self._queue_stats[queue_id]
            
            # Update depth tracking
            if current_depth > stats["max_depth"]:
                stats["max_depth"] = current_depth
            
            stats["current_depth"] = current_depth
            
            # Check threshold
            if current_depth > self.max_depth_alert:
                self._trigger_alert(queue_id, current_depth)
    
    def record_completion(self, queue_id: str) -> None:
        """Record item processed/completed."""
        self.register_queue(queue_id)
        
        with self._lock:
            stats = self._queue_stats[queue_id]
            stats["items_processed"] += 1
            
            # Update current depth
            current_depth = len(self._queues[queue_id])
            if current_depth > 0:
                current_depth -= 1
            
            stats["current_depth"] = current_depth
    
    def _trigger_alert(self, queue_id: str, depth: int) -> None:
        """Trigger alert for queue depth exceeded."""
        now = time.time()
        last_alert = self._alerts_triggered.get(queue_id, 0)
        
        # Only alert once per 60 seconds per queue
        if now - last_alert > 60:
            self._alerts_triggered[queue_id] = now
            self._queue_stats[queue_id]["alerts_count"] += 1
            
            log.warning(
                f"Queue depth alert: {queue_id} depth={depth} "
                f"exceeded threshold={self.max_depth_alert}"
            )
    
    def get_stats(self, queue_id: str) -> Dict[str, Any]:
        """Get queue statistics."""
        with self._lock:
            if queue_id not in self._queue_stats:
                return {}
            
            stats = self._queue_stats[queue_id].copy()
            stats["queue_id"] = queue_id
            stats["current_depth"] = len(self._queues.get(queue_id, []))
            return stats
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get stats for all queues."""
        with self._lock:
            result = {}
            for queue_id in self._queues:
                result[queue_id] = self.get_stats(queue_id)
            return result
    
    def health_check(self) -> bool:
        """Check if any queue is critical (depth > threshold * 2)."""
        with self._lock:
            critical_threshold = self.max_depth_alert * 2
            for queue_id, depth in [
                (qid, len(q)) for qid, q in self._queues.items()
            ]:
                if depth > critical_threshold:
                    log.critical(
                        f"Queue {queue_id} CRITICAL: depth={depth} "
                        f"> threshold={critical_threshold}"
                    )
                    return False
            return True


class AsyncWorkQueue:
    """
    Async work queue with integrated monitoring.
    
    Usage:
        queue = AsyncWorkQueue(queue_id="pipeline_tasks")
        queue.submit(my_async_function, arg1, arg2)
        result = queue.get_result(future)
    """
    
    def __init__(
        self,
        queue_id: str,
        executor: Optional[ThreadPoolExecutor] = None,
        monitor: Optional[QueueDepthMonitor] = None,
    ):
        """
        Initialize async work queue.
        
        Args:
            queue_id: Identifier for this queue
            executor: ThreadPoolExecutor (creates if None)
            monitor: QueueDepthMonitor (creates if None)
        """
        self.queue_id = queue_id
        self.executor = executor or ThreadPoolExecutor(
            max_workers=50 * multiprocessing.cpu_count()
        )
        self.monitor = monitor or QueueDepthMonitor()
        
        self.monitor.register_queue(queue_id)
        
        self._futures: Dict[Any, Any] = {}
        self._lock = threading.Lock()
    
    def submit(
        self,
        fn: Callable,
        *args,
        **kwargs
    ) -> Any:
        """Submit async task."""
        future = self.executor.submit(fn, *args, **kwargs)
        
        with self._lock:
            self._futures[id(future)] = future
            self.monitor.record_item(self.queue_id, future)
        
        return future
    
    def get_result(self, future: Any, timeout: Optional[float] = None) -> Any:
        """Get result from completed future."""
        try:
            result = future.result(timeout=timeout)
            self.monitor.record_completion(self.queue_id)
            return result
        except Exception as exc:
            log.error(f"Task failed in queue {self.queue_id}: {exc}")
            self.monitor.record_completion(self.queue_id)
            raise
    
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown executor."""
        self.executor.shutdown(wait=wait)


# Global instances
_thread_pool_config: Optional[ThreadPoolConfig] = None
_queue_monitor: Optional[QueueDepthMonitor] = None


def get_thread_pool_config() -> ThreadPoolConfig:
    """Get or create global thread pool config."""
    global _thread_pool_config
    if _thread_pool_config is None:
        _thread_pool_config = ThreadPoolConfig()
    return _thread_pool_config


def get_queue_monitor() -> QueueDepthMonitor:
    """Get or create global queue monitor."""
    global _queue_monitor
    if _queue_monitor is None:
        _queue_monitor = QueueDepthMonitor(max_depth_alert=1000)
    return _queue_monitor


def create_async_queue(queue_id: str) -> AsyncWorkQueue:
    """Create async work queue with global config/monitor."""
    config = get_thread_pool_config()
    monitor = get_queue_monitor()
    executor = config.create_executor()
    
    return AsyncWorkQueue(
        queue_id=queue_id,
        executor=executor,
        monitor=monitor,
    )


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    print("="*70)
    print("GlassBox v1.0.1 — Thread Pool & Queue Monitoring")
    print("="*70)
    
    # Create config
    config = ThreadPoolConfig()
    print(f"\n✓ Thread Pool Config: {config.async_workers} workers")
    
    # Create monitor
    monitor = QueueDepthMonitor(max_depth_alert=10)  # Low for demo
    print(f"✓ Queue Monitor: Alert at > 10 items")
    
    # Create queue
    queue = create_async_queue("demo_queue")
    print(f"✓ Created async queue: demo_queue")
    
    # Submit tasks
    def sample_task(x):
        time.sleep(0.1)
        return x * 2
    
    print(f"\n✓ Submitting 15 tasks...")
    futures = []
    for i in range(15):
        future = queue.submit(sample_task, i)
        futures.append(future)
    
    # Check stats
    stats = monitor.get_stats("demo_queue")
    print(f"\nQueue Statistics:")
    print(f"  Current depth: {stats['current_depth']}")
    print(f"  Max depth: {stats['max_depth']}")
    print(f"  Alerts triggered: {stats['alerts_count']}")
    
    # Health check
    is_healthy = monitor.health_check()
    print(f"  Health check: {'✓ Healthy' if is_healthy else '✗ Critical'}")
    
    # Get results
    print(f"\n✓ Collecting results...")
    results = []
    for future in futures:
        result = queue.get_result(future)
        results.append(result)
    
    print(f"  Results: {results}")
    print(f"  All stats: {monitor.get_all_stats()}")
    
    queue.shutdown()
    print(f"\n✓ Demo complete")
