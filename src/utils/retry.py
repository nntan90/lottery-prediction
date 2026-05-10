"""
retry.py — Retry decorator with exponential backoff for crawl operations.

Usage:
    from src.utils.retry import retry_with_backoff

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def fetch_results(target_date):
        ...
"""

import time
import functools
from typing import Callable, TypeVar, Any

T = TypeVar("T")


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Decorator: retry a function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (not including initial call)
        base_delay: Initial delay in seconds (doubles each retry)
        max_delay: Maximum delay cap in seconds
        exceptions: Tuple of exception types to catch and retry

    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        print(f"  ⚠️ Attempt {attempt + 1}/{max_retries + 1} failed: {e}")
                        print(f"     Retrying in {delay:.1f}s...")
                        time.sleep(delay)
                    else:
                        print(f"  ❌ All {max_retries + 1} attempts failed for {func.__name__}")
            raise last_exception
        return wrapper
    return decorator
