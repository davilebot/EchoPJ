import unittest

from service.limits import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def test_limits_each_key_and_reports_retry_time(self):
        limiter = SlidingWindowRateLimiter(requests=2, window_seconds=60)
        self.assertEqual(limiter.consume("org:search", now=10).remaining, 1)
        self.assertEqual(limiter.consume("org:search", now=20).remaining, 0)
        blocked = limiter.consume("org:search", now=30)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after, 40)
        self.assertTrue(limiter.consume("other:search", now=30).allowed)
        self.assertTrue(limiter.consume("org:search", now=71).allowed)

    def test_rejects_invalid_configuration_and_can_be_cleared(self):
        with self.assertRaises(ValueError):
            SlidingWindowRateLimiter(requests=0)
        limiter = SlidingWindowRateLimiter(requests=1)
        self.assertTrue(limiter.consume("key", now=1).allowed)
        self.assertFalse(limiter.consume("key", now=2).allowed)
        limiter.clear()
        self.assertTrue(limiter.consume("key", now=2).allowed)


if __name__ == "__main__":
    unittest.main()
