"""Performance monitoring and profiling for Crisp Engine.

Track operation latencies, memory usage, and cache hit rates.
Helps identify bottlenecks and optimize performance.
"""
import time
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from collections import defaultdict, deque
from dataclasses import dataclass, field
from contextlib import contextmanager

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


@dataclass
class Metric:
    """A performance metric."""
    name: str
    count: int = 0
    total_time: float = 0.0
    max_time: float = 0.0
    min_time: float = float('inf')
    
    @property
    def avg_time(self) -> float:
        return self.total_time / self.count if self.count > 0 else 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "avg_ms": round(self.avg_time * 1000, 2),
            "min_ms": round(self.min_time * 1000, 2),
            "max_ms": round(self.max_time * 1000, 2),
            "total_ms": round(self.total_time * 1000, 2),
        }


class PerformanceMonitor:
    """Monitor performance of memory operations.
    
    Tracks:
    - Operation latencies (save, search, reflect, prune)
    - Cache hit rates (hash_cache, file_states)
    - Store size and growth rate
    - Memory usage
    
    Usage:
        monitor = PerformanceMonitor(store)
        
        with monitor.track("search"):
            results = store.search(...)
        
        print(monitor.get_report())
    """
    
    def __init__(self, store):
        self.store = store
        self.metrics: Dict[str, Metric] = {}
        self.lock = threading.RLock()
        self.start_time = time.time()
        self.samples = deque(maxlen=1000)  # recent operation samples
    
    @contextmanager
    def track(self, operation: str):
        """Context manager to track operation latency."""
        start = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - start
            self._record(operation, elapsed)
    
    def _record(self, operation: str, elapsed: float):
        """Record a metric sample."""
        with self.lock:
            if operation not in self.metrics:
                self.metrics[operation] = Metric(name=operation)
            
            m = self.metrics[operation]
            m.count += 1
            m.total_time += elapsed
            m.max_time = max(m.max_time, elapsed)
            m.min_time = min(m.min_time, elapsed)
            
            # Record sample
            self.samples.append({
                "operation": operation,
                "elapsed": elapsed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    
    def get_report(self) -> Dict[str, Any]:
        """Get performance report."""
        with self.lock:
            uptime = time.time() - self.start_time
            
            # Store stats
            store_stats = self.store.get_stats()
            
            # System memory (if psutil available)
            if HAS_PSUTIL:
                process = psutil.Process()
                memory_info = process.memory_info()
                memory_data = {
                    "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
                    "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
                }
            else:
                memory_data = {"rss_mb": 0, "vms_mb": 0, "note": "psutil not installed"}
            
            return {
                "uptime_seconds": round(uptime, 2),
                "operations_per_second": round(len(self.samples) / uptime, 2) if uptime > 0 else 0,
                "metrics": [m.to_dict() for m in self.metrics.values()],
                "store": store_stats,
                "memory": memory_data,
                "recent_slowest": self._get_slowest(5),
            }
    
    def _get_slowest(self, n: int) -> List[Dict]:
        """Get N slowest operations from recent samples."""
        sorted_samples = sorted(self.samples, key=lambda s: s["elapsed"], reverse=True)
        return sorted_samples[:n]
    
    def get_cache_hit_rate(self) -> Dict[str, float]:
        """Calculate cache hit rates."""
        # For each search operation, track if hash_cache or file_states was used
        # This requires additional instrumentation
        # Placeholder implementation
        return {
            "hash_cache_hit_rate": 0.0,
            "file_state_hit_rate": 0.0,
        }
    
    def reset(self):
        """Reset all metrics."""
        with self.lock:
            self.metrics.clear()
            self.samples.clear()
            self.start_time = time.time()


# Global monitor instance (per-store)
_monitors: Dict[str, PerformanceMonitor] = {}


def get_monitor(store) -> PerformanceMonitor:
    """Get or create monitor for a store."""
    store_id = str(id(store))
    if store_id not in _monitors:
        _monitors[store_id] = PerformanceMonitor(store)
    return _monitors[store_id]


# Decorator for automatic tracking
def track_performance(operation_name: str = None):
    """Decorator to track function performance."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Get store from args (assumes first arg is store or has .store)
            store = None
            for arg in args:
                if hasattr(arg, 'store') and hasattr(arg.store, 'get_stats'):
                    store = arg.store
                    break
                elif hasattr(arg, 'get_stats'):
                    store = arg
                    break
            
            op_name = operation_name or func.__name__
            monitor = get_monitor(store) if store else None
            
            if monitor:
                with monitor.track(op_name):
                    return func(*args, **kwargs)
            else:
                start = time.time()
                try:
                    return func(*args, **kwargs)
                finally:
                    elapsed = time.time() - start
                    # Could log but no monitor
            return func(*args, **kwargs)
        return wrapper
    return decorator
