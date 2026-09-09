import contextlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ops.load_test_v2 import main, parse_args, summarize


class ProbeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        if self.path == "/api/auth/login":
            self.send_response(200)
            self.send_header("Set-Cookie", "echopjs_session=test-session; HttpOnly")
        elif self.path == "/api/search" and self.headers.get("Cookie") == "echopjs_session=test-session":
            self.send_response(200)
            self.send_header("X-Response-Time-Ms", "12.5")
        else:
            self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self):
        authenticated = self.headers.get("Cookie") == "echopjs_session=test-session"
        if self.path == "/health/ready" or (self.path == "/api/dashboard" and authenticated):
            self.send_response(200)
            self.send_header("X-Response-Time-Ms", "3.0")
        else:
            self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_):
        return


class LoadTestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def run_probe(self, scenario):
        arguments = [
            "--base-url", self.base_url, "--scenario", scenario,
            "--requests", "6", "--concurrency", "2",
        ]
        if scenario != "health":
            arguments.extend(["--username", "user@example.com", "--organization-id", "1"])
        output = io.StringIO()
        with patch.dict(os.environ, {"ECHOPJS_LOAD_PASSWORD": "secret"}), contextlib.redirect_stdout(output):
            result = main(arguments)
        return result, json.loads(output.getvalue())

    def test_health_and_authenticated_scenarios_pass_without_exposing_credentials(self):
        for scenario in ("health", "dashboard", "search"):
            result, report = self.run_probe(scenario)
            self.assertEqual(result, 0)
            self.assertTrue(report["passed"])
            self.assertEqual(report["status_counts"], {"200": 6})
            self.assertNotIn("secret", json.dumps(report))
            self.assertNotIn("user@example.com", json.dumps(report))

    def test_bounded_arguments_reject_unsafe_search_volume(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--base-url", self.base_url, "--scenario", "search", "--requests", "21", "--username", "u", "--organization-id", "1"])

    def test_summary_counts_http_and_network_failures(self):
        report = summarize([
            {"status": 200, "elapsed_ms": 10.0, "server_ms": 5.0},
            {"status": 503, "elapsed_ms": 20.0, "server_ms": 8.0},
            {"status": 0, "elapsed_ms": 30.0, "server_ms": None},
        ], 1.0)
        self.assertEqual(report["status_counts"], {"200": 1, "503": 1, "network_error": 1})
        self.assertAlmostEqual(report["error_rate_percent"], 66.67)
