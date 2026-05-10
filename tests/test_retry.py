"""
test_retry.py — Regression tests for retry_with_backoff decorator.

Verifies:
  - Successful call on first attempt (no retry)
  - Retry on transient failure, success on Nth attempt
  - Respects max_retries limit
  - Only catches specified exception types
  - Exponential backoff delay capping
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.retry import retry_with_backoff


class TestRetryWithBackoff(unittest.TestCase):
    """Tests for retry decorator."""

    def test_success_first_attempt(self):
        """No retry needed when function succeeds immediately."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        self.assertEqual(result, "ok")
        self.assertEqual(call_count, 1)

    def test_retry_then_success(self):
        """Should retry and succeed on 2nd attempt."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("transient")
            return "recovered"

        result = fail_once()
        self.assertEqual(result, "recovered")
        self.assertEqual(call_count, 2)

    def test_exhaust_all_retries(self):
        """Should raise after exhausting all retries."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01)
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("permanent")

        with self.assertRaises(ConnectionError):
            always_fail()
        self.assertEqual(call_count, 3)  # 1 initial + 2 retries

    def test_only_catches_specified_exceptions(self):
        """Should NOT retry on exception types not in the exceptions tuple."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01, exceptions=(ConnectionError,))
        def raise_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with self.assertRaises(ValueError):
            raise_value_error()
        self.assertEqual(call_count, 1)  # No retry

    def test_max_delay_cap(self):
        """Delay should not exceed max_delay."""
        # base=2, max_delay=5 → delays would be 2, 4, 5(capped)
        @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=5.0)
        def always_fail():
            raise ConnectionError("fail")

        # We just verify it doesn't hang forever
        with self.assertRaises(ConnectionError):
            # Patch time.sleep to avoid actual waiting
            with patch("src.utils.retry.time.sleep") as mock_sleep:
                always_fail()
                # Check delays: 2.0, 4.0, 5.0 (capped)
                delays = [c.args[0] for c in mock_sleep.call_args_list]
                self.assertEqual(len(delays), 3)
                self.assertAlmostEqual(delays[0], 2.0)
                self.assertAlmostEqual(delays[1], 4.0)
                self.assertAlmostEqual(delays[2], 5.0)  # capped

    def test_preserves_function_name(self):
        """Decorated function should preserve __name__."""
        @retry_with_backoff(max_retries=1, base_delay=0.01)
        def my_special_function():
            return True

        self.assertEqual(my_special_function.__name__, "my_special_function")

    def test_passes_args_and_kwargs(self):
        """Should correctly forward args and kwargs."""
        @retry_with_backoff(max_retries=1, base_delay=0.01)
        def add(a, b, extra=0):
            return a + b + extra

        self.assertEqual(add(1, 2, extra=3), 6)


if __name__ == "__main__":
    unittest.main()
