import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from worldfork.provider import OpenAIInterpreter
from worldfork.server import MAX_BODY_BYTES, RunStore, create_server


def experiment(name="Test run", run_id=None):
    return {
        "id": run_id or uuid.uuid4().hex, "created_at": "2026-01-01T00:00:00Z",
        "scenario": {"name": name}, "recommended_policy": "optimized",
        "policies": [{"id": "optimized", "service_rate": 83.5}],
        "checks": [{"name": "capacity", "passed": True}],
    }


class StubEngine:
    @staticmethod
    def bootstrap():
        return {"defaults": {"name": "Test"}, "version": "test"}

    @staticmethod
    def validate_scenario(data):
        if set(data) - {"name"}:
            raise ValueError("Unknown scenario fields.")
        return {"name": data.get("name", "Test run")}

    @staticmethod
    def run_experiment(scenario):
        return experiment(scenario["name"])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.web = root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>WorldFork</title>", encoding="utf-8")
        (self.web / "app.js").write_text("'use strict';", encoding="utf-8")
        (root / "secret.txt").write_text("private-file", encoding="utf-8")
        self.server = create_server(
            port=0, data_dir=root / "data", web_dir=self.web,
            engine_module=StubEngine, provider=OpenAIInterpreter(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.token = self.server.csrf_token
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        merged = {}
        if data is not None:
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            merged["Content-Type"] = "application/json"
        else:
            body = None
        if headers:
            merged.update(headers)
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            raw = response.read()
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = raw
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def post(self, path, data, headers=None):
        merged = {"X-WorldFork-Token": self.token, "Origin": self.origin}
        merged.update(headers or {})
        return self.request("POST", path, data, merged)

    def test_health_bootstrap_and_security_headers(self):
        status, headers, result = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(result["csrf_token"], self.token)
        self.assertFalse(result["capabilities"]["language_model"])
        self.assertIsNone(result["capabilities"]["model"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("GET", "/api/health")[2]["status"], "ok")

    def test_run_roundtrip_history_and_export(self):
        status, _, run = self.post("/api/runs", {"name": "Reproducible experiment"})
        self.assertEqual(status, 201)
        status, _, history = self.request("GET", "/api/runs")
        self.assertEqual(history["runs"][0]["id"], run["id"])
        self.assertEqual(history["runs"][0]["service_rate"], 83.5)
        status, _, fetched = self.request("GET", "/api/runs/" + run["id"])
        self.assertEqual(fetched, run)
        status, headers, exported = self.request("GET", "/api/runs/" + run["id"] + "/export")
        self.assertEqual(status, 200)
        self.assertEqual(exported, run)
        self.assertIn("attachment;", headers["Content-Disposition"])

    def test_cross_origin_dns_rebinding_and_missing_token_rejected(self):
        attempts = [
            {"Host": f"attacker.example:{self.port}"},
            {"Origin": "https://attacker.example"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
            {"X-WorldFork-Token": "wrong"},
            {"X-WorldFork-Token": "é"},
        ]
        for headers in attempts:
            with self.subTest(headers=headers):
                self.assertEqual(self.post("/api/runs", {}, headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/runs", {})[0], 403)
        self.assertEqual(self.request("GET", "/api/bootstrap", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.server.store.list(), [])

    def test_non_browser_client_with_token_is_supported(self):
        self.assertEqual(self.request("POST", "/api/runs", {}, {"X-WorldFork-Token": self.token})[0], 201)

    def test_json_and_size_limits(self):
        for body in (b'{"name":"a","name":"b"}', b'{"name":NaN}', b'[]', b'{broken', b'\xff'):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/runs", body)[0], 400)
        self.assertEqual(self.post("/api/runs", b"x" * (MAX_BODY_BYTES + 1))[0], 413)
        self.assertEqual(self.post("/api/runs", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post("/api/runs", {"bad": "value"})[0], 422)

    def test_static_allowlist_and_path_traversal(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertIn("javascript", self.request("GET", "/app.js")[1]["Content-Type"])
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/%2e%2e%5csecret.txt", "/secret.txt", "/worldfork/server.py", "/.worldfork/runs.sqlite3", "/.git/config"):
            status, _, payload = self.request("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn("private-file", str(payload))

    def test_symlink_asset_cannot_escape_web_root(self):
        asset = self.web / "app.js"
        asset.unlink()
        try:
            asset.symlink_to(Path(self.temp.name) / "secret.txt")
        except OSError:
            self.skipTest("Creating symbolic links requires additional privileges on this platform.")
        self.assertEqual(self.request("GET", "/app.js")[0], 404)

    def test_unknown_run_and_invalid_id(self):
        for path in ("/api/runs/" + "a" * 32, "/api/runs/not-a-run", "/api/runs/'OR1=1", "/api/runs/" + "b" * 32 + "/other"):
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_unconfigured_interpretation_is_explicit(self):
        status, _, payload = self.post("/api/interpret", {"prompt": "Help with demand"})
        self.assertEqual(status, 503)
        self.assertIn("not configured", payload["error"])
        self.assertEqual(self.post("/api/interpret", {"prompt": "", "extra": 1})[0], 422)
        self.assertEqual(self.post("/api/interpret", {"prompt": "x" * 2001})[0], 422)
        self.assertEqual(self.post("/api/interpret", {"prompt": "\ud800"})[0], 422)

    def test_invalid_model_unicode_returns_sanitized_bad_gateway(self):
        model_output = {
            "scenario": {
                "name": "Scenario", "demand_multiplier": 1.15, "disruption": 0.45,
                "reserve_units": 20, "objective": "balanced", "seed": 42, "worlds": 128,
            },
            "interpretation": "Private malformed detail \ud800",
        }
        response = json.dumps({"status": "completed", "output_text": json.dumps(model_output)}).encode()
        opener = SimpleNamespace(open=lambda request, timeout: io.BytesIO(response))
        self.server.provider = OpenAIInterpreter("test-key", opener=opener, validator=lambda data: data)
        status, _, payload = self.post("/api/interpret", {"prompt": "A scenario"})
        self.assertEqual(status, 502)
        self.assertNotIn("Private malformed", payload["error"])

    def test_interpretation_is_separate_from_running(self):
        class StubProvider:
            configured = True
            model = "test-model"
            def interpret(self, prompt):
                return {"scenario": {"name": "Translated"}, "interpretation": "Assumptions", "provider": "test"}
        self.server.provider = StubProvider()
        status, _, payload = self.post("/api/interpret", {"prompt": "A scenario"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["scenario"]["name"], "Translated")
        self.assertEqual(self.server.store.list(), [])

    def test_parallel_computations_are_rejected(self):
        self.server.work_slot.acquire()
        try:
            self.assertEqual(self.post("/api/runs", {})[0], 503)
        finally:
            self.server.work_slot.release()

    def test_mutation_rate_limit(self):
        for _ in range(12):
            self.assertEqual(self.post("/api/runs", {"unknown": True})[0], 422)
        status, headers, _ = self.post("/api/runs", {})
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "60")

    def test_internal_exceptions_are_sanitized(self):
        with patch.object(StubEngine, "run_experiment", side_effect=RuntimeError("private-secret-and-path")):
            status, _, payload = self.post("/api/runs", {})
        self.assertEqual(status, 500)
        self.assertNotIn("private-secret", json.dumps(payload))
        self.assertEqual(self.post("/api/runs", {})[0], 201)

    def test_public_binding_is_rejected(self):
        for host in ("0.0.0.0", "example.com", "192.168.1.1", "::"):
            with self.assertRaises(ValueError):
                create_server(host=host)

    def test_retention_and_persistence(self):
        for number in range(105):
            self.server.store.save(experiment(name=str(number)))
        runs = RunStore(Path(self.temp.name) / "data").list()
        self.assertEqual(len(runs), 100)
        self.assertEqual(runs[0]["name"], "104")
        self.assertEqual(runs[-1]["name"], "5")

    def test_duplicate_length_header_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("POST", "/api/runs")
            connection.putheader("X-WorldFork-Token", self.token)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "2")
            connection.putheader("Content-Length", "2")
            connection.endheaders(b"{}")
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
