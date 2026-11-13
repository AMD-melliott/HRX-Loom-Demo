#!/usr/bin/env python3
"""
Regression checks for the HRX Loom demo.

Covers the two things most likely to go quietly wrong:
  1. the catalog extraction (devlab vs summary) silently losing models
  2. the demo presenting an unmeasured number as if it were measured

Run from the project root:
    python3 tests/test_demo.py
"""

import json
import pathlib
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO_PORT = 5099
STUB_PORT = 8099


def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


class TestCatalog(unittest.TestCase):
    """The extraction itself - no server involved."""

    @classmethod
    def setUpClass(cls):
        path = ROOT / "data" / "catalog.json"
        if not path.exists():
            raise unittest.SkipTest("data/catalog.json not built")
        cls.cat = json.loads(path.read_text(encoding="utf-8"))

    def test_all_models_present(self):
        self.assertEqual(self.cat["model_count"], len(self.cat["models"]))
        self.assertGreaterEqual(self.cat["model_count"], 50)

    def test_devlab_is_the_real_source(self):
        """The whole reason build_catalog prefers devlab.

        If a future change reads performance.metrics first, this count
        collapses and the leaderboard empties out.
        """
        self.assertGreater(self.cat["origin_tally"].get("devlab", 0), 400)

    def test_most_models_have_measurements(self):
        measured = [m for m in self.cat["models"] if m["measured_workloads"] > 0]
        self.assertGreaterEqual(len(measured), 49,
                                "models lost their metrics - check the devlab fallback")

    def test_missing_speedup_is_none_not_parity(self):
        """A gap must never be backfilled with 1.0 or 0."""
        for m in self.cat["models"]:
            for w, v in m["metrics"].items():
                if v["origin"] is None:
                    self.assertIsNone(v["speedup"],
                                      f"{m['name']}/{w}: no origin but a speedup present")

    def test_regressions_are_retained(self):
        """Ports slower than llama.cpp must survive into the output.

        A benchmark view showing only wins is not evidence; if this hits
        zero, someone has filtered the losses out.
        """
        slow = [m for m in self.cat["models"] if m["regressions"]]
        self.assertGreater(len(slow), 0)

    def test_geomean_ignores_missing(self):
        for m in self.cat["models"]:
            if m["measured_workloads"] == 0:
                self.assertIsNone(m["geomean_speedup"])
            else:
                self.assertIsNotNone(m["geomean_speedup"])

    def test_evidence_urls_present(self):
        with_url = [m for m in self.cat["models"] if m["evidence"]["url"]]
        self.assertGreaterEqual(len(with_url), 50,
                                "per-number provenance lost")


class TestServerModes(unittest.TestCase):
    """Mode detection and the live path, against the stub."""

    demo = None
    stub = None

    @classmethod
    def setUpClass(cls):
        env_port = str(DEMO_PORT)
        cls.demo = subprocess.Popen(
            [sys.executable, str(ROOT / "app.py")],
            cwd=str(ROOT),
            env={**__import__("os").environ,
                 "DEMO_PORT": env_port,
                 "LOOM_SERVE_URL": f"http://127.0.0.1:{STUB_PORT}"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_for(f"http://127.0.0.1:{DEMO_PORT}/api/status"):
            raise unittest.SkipTest("demo backend did not start")

    @classmethod
    def tearDownClass(cls):
        for p in (cls.demo, cls.stub):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()

    def _start_stub(self, *extra):
        """Start a stub, and prove it is OURS.

        Without the port-free check below, a stub that fails to bind (because
        the previous one has not released the port yet) exits silently while
        wait_for happily succeeds against the OLD server - so a test asking
        for --no-usage would quietly be answered by a stub that still sends
        usage, and pass or fail for the wrong reason.
        """
        self._stop_stub()   # always start from a known-clean port
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "fake_loom_serve.py"),
             "--port", str(STUB_PORT), *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.__class__.stub = proc
        self.assertTrue(wait_for(f"http://127.0.0.1:{STUB_PORT}/v1/models"),
                        "stub did not start")
        self.assertIsNone(proc.poll(), "stub exited immediately (bind failure?)")

    @staticmethod
    def _port_free(port, timeout=10):
        """True once nothing answers /v1/models on this port."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                http_json(f"http://127.0.0.1:{port}/v1/models", timeout=1)
                time.sleep(0.2)
            except Exception:
                return True
        return False

    def _stop_stub(self):
        proc = self.__class__.stub
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.__class__.stub = None
        # Do not return until the port has actually gone quiet.
        self.assertTrue(self._port_free(STUB_PORT),
                        "stub port still answering after shutdown")

    def test_01_replay_when_no_server(self):
        self._stop_stub()
        s = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/status")
        self.assertEqual(s["mode"], "replay")
        self.assertIsNone(s["server_models"])

    def test_02_probe_in_replay_does_not_fabricate(self):
        self._stop_stub()
        d = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/probe", {"concurrency": 2})
        self.assertEqual(d["mode"], "replay")
        # Crucially: no latency or throughput keys at all.
        for k in ("latency_p50", "latency_p99", "mean_output_tok_s"):
            self.assertNotIn(k, d, f"replay response leaked a {k} field")

    def test_03_goes_live_when_server_appears(self):
        self._start_stub()
        s = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/status")
        self.assertEqual(s["mode"], "live")
        self.assertIn("Qwen/Qwen2.5-0.5B-Instruct", s["server_models"])

    def test_04_live_probe_measures(self):
        self._start_stub()
        d = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/probe",
                      {"concurrency": 3, "prompt_tokens": 100, "max_tokens": 32})
        self.assertEqual(d["mode"], "live")
        self.assertEqual(d["failed"], 0)
        self.assertEqual(d["requests"], 3)
        self.assertIsNotNone(d["latency_p50"])
        self.assertIsNotNone(d["mean_output_tok_s"])
        self.assertGreater(d["mean_output_tok_s"], 0)

    def test_05_concurrency_is_capped(self):
        self._start_stub()
        d = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/probe",
                      {"concurrency": 999, "prompt_tokens": 50, "max_tokens": 16})
        self.assertLessEqual(d["concurrency"], d["max_workers"],
                             "worker cap not enforced")

    def test_06_absent_usage_yields_none_not_zero(self):
        """A server that reports no token counts must give '—', not 0 tok/s."""
        self._stop_stub()
        self._start_stub("--no-usage")
        d = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/probe",
                      {"concurrency": 2, "prompt_tokens": 50, "max_tokens": 16})
        self.assertEqual(d["mode"], "live")
        self.assertIsNone(d["mean_output_tok_s"],
                          "missing usage was turned into a measured-looking number")
        self.assertIsNone(d["aggregate_output_tok_s"])
        self.assertIsNotNone(d["latency_p50"], "latency is still measurable")
        self._stop_stub()

    def test_07_failures_are_counted_not_hidden(self):
        self._stop_stub()
        self._start_stub("--fail-rate", "1.0")
        d = http_json(f"http://127.0.0.1:{DEMO_PORT}/api/probe",
                      {"concurrency": 3, "prompt_tokens": 50, "max_tokens": 16})
        self.assertEqual(d["failed"], 3)
        self.assertIsNone(d["latency_p50"], "no successes, so no latency to report")
        self._stop_stub()


if __name__ == "__main__":
    unittest.main(verbosity=2)
