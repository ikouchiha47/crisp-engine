"""Error handling and resilience for Crisp Engine.

Provides:
- Graceful degradation when storage is unavailable
- Retry logic for transient failures
- Circuit breaker pattern for unstable backends
- Comprehensive logging
"""
import json
import logging
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Type
from datetime import datetime, timezone
from functools import wraps
import time


# Configure logging
logger = logging.getLogger("crisp_engine")
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.WARNING)


class MemoryError(Exception):
    """Base exception for memory system."""
    pass


class StorageUnavailableError(MemoryError):
    """Storage backend is unavailable."""
    pass


class DeduplicationError(MemoryError):
    """Error during deduplication."""
    pass


class ValidationError(MemoryError):
    """Data validation failed."""
    pass


class CircuitBreaker:
    """Circuit breaker pattern for unstable operations.
    
    After N consecutive failures, circuit opens and calls fail fast
    for a cooldown period. After cooldown, circuit half-opens to test
    if service recovered.
    """
    
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half-open
    
    def __call__(self, func: Callable):
        """Decorator to wrap function with circuit breaker."""
        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.state == "open":
                if time.time() - self.last_failure_time > self.cooldown_seconds:
                    self.state = "half-open"
                    logger.info("Circuit breaker half-open, testing service")
                else:
                    raise StorageUnavailableError(
                        f"Circuit breaker open for {self.cooldown_seconds}s"
                    )
            
            try:
                result = func(*args, **kwargs)
                if self.state == "half-open":
                    self.state = "closed"
                    self.failure_count = 0
                    logger.info("Circuit breaker closed - service recovered")
                return result
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                
                if self.failure_count >= self.failure_threshold:
                    self.state = "open"
                    logger.error(f"Circuit breaker opened after {self.failure_count} failures")
                
                raise
        
        return wrapper


def retry(max_attempts: int = 3, delay: float = 0.5, backoff: float = 2.0):
    """Retry decorator with exponential backoff."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except (OSError, IOError) as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        logger.error(f"Failed after {max_attempts} attempts: {e}")
                        raise
                    
                    logger.warning(f"Attempt {attempt} failed, retrying in {current_delay}s: {e}")
                    time.sleep(current_delay)
                    current_delay *= backoff
            
            raise MemoryError("Should not reach here")
        return wrapper
    return decorator


def safe_operation(default_return=None, log_errors=True):
    """Decorator for safe operations that shouldn't crash the system."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logger.error(f"Error in {func.__name__}: {e}")
                    logger.debug(traceback.format_exc())
                return default_return
        return wrapper
    return decorator


class ErrorCollector:
    """Collect errors for batch reporting."""
    
    def __init__(self):
        self.errors: List[Dict[str, Any]] = []
    
    def capture(self, exc: Exception, context: Dict[str, Any] = None):
        """Capture an error with context."""
        error_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exception": exc.__class__.__name__,
            "message": str(exc),
            "context": context or {},
        }
        self.errors.append(error_entry)
        
        # Log it
        logger.error(f"{error_entry['exception']}: {error_entry['message']}")
    
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    def get_report(self) -> Dict[str, Any]:
        return {
            "total_errors": len(self.errors),
            "errors": self.errors,
        }
    
    def clear(self):
        self.errors.clear()


# Global error collector
collector = ErrorCollector()


def log_exception(exc: Exception, context: str = ""):
    """Log exception with context."""
    collector.capture(exc, {"context": context})
    logger.error(f"{context}: {exc}")


def validate_store_health(base_path: str) -> Dict[str, Any]:
    """Check if memory store is healthy and writable."""
    result = {
        "healthy": True,
        "checks": {},
        "errors": [],
    }
    
    path = Path(base_path)
    
    # Check 1: Directory exists and is writable
    try:
        if not path.exists():
            result["errors"].append(f"Store directory does not exist: {base_path}")
            result["healthy"] = False
        elif not path.is_dir():
            result["errors"].append(f"Store path is not a directory: {base_path}")
            result["healthy"] = False
        elif not os.access(path, os.W_OK):
            result["errors"].append(f"Store directory not writable: {base_path}")
            result["healthy"] = False
        else:
            result["checks"]["writable"] = True
    except Exception as e:
        result["errors"].append(f"Error checking directory: {e}")
        result["healthy"] = False
    
    # Check 2: Can create temp file
    try:
        test_file = path / ".healthcheck"
        test_file.write_text("ok")
        test_file.unlink()
        result["checks"]["can_create_files"] = True
    except Exception as e:
        result["errors"].append(f"Cannot create files: {e}")
        result["healthy"] = False
    
    # Check 3: Subdirectories exist
    required_dirs = ["layers", "cache", "config"]
    for d in required_dirs:
        if not (path / d).exists():
            result["warnings"] = result.get("warnings", [])
            result["warnings"].append(f"Missing directory: {d}")
    
    return result
