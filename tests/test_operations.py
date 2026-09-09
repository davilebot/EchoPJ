import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from service.operations import OperationsMonitor, backup_status, percentile


class OperationsTests(unittest.TestCase):
    def test_recent_traffic_has_bounded_route_metrics_and_percentiles(self):
        monitor = OperationsMonitor(max_observations=100)
        for status, duration in ((200, 10), (200, 20), (404, 30), (503, 80)):
            monitor.begin()
            monitor.finish("/api/search", "POST", status, duration)
        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["requests_total"], 4)
        self.assertEqual(snapshot["in_flight"], 0)
        self.assertEqual(snapshot["window"]["server_errors"], 1)
        self.assertEqual(snapshot["window"]["client_errors"], 1)
        self.assertEqual(snapshot["window"]["latency_ms"]["p95"], 80)
        self.assertEqual(snapshot["window"]["top_routes"], [{"route": "POST /api/search", "requests": 4}])
        self.assertEqual(percentile([], .95), 0)

    def test_backup_status_detects_ok_stale_missing_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            self.assertEqual(backup_status(str(path))["status"], "unknown")
            path.write_text("not-json")
            self.assertEqual(backup_status(str(path))["status"], "invalid")
            path.write_text(json.dumps({
                "status": "ok", "checked_at": datetime.now(timezone.utc).isoformat(), "retained": 8,
            }))
            self.assertEqual(backup_status(str(path))["status"], "ok")
            path.write_text(json.dumps({
                "status": "ok", "checked_at": (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat(),
            }))
            self.assertEqual(backup_status(str(path))["status"], "stale")


if __name__ == "__main__":
    unittest.main()
