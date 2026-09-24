"""Offline unit tests for the openrouter backend — no network. Run: python3 tests/test_openrouter.py

Covers: backend resolution, wire request shape (endpoint, payload, auth header),
answer normalization (noul -> internal boolean), confidence extraction, fail-open
on transport/HTTP errors, and cache reuse for identical calls.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

pkg = types.ModuleType("jevjudge")
pkg.__path__ = [str(BASE)]
sys.modules["jevjudge"] = pkg
for name in ("jev", "gate", "tools"):
    spec = importlib.util.spec_from_file_location(f"jevjudge.{name}", BASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"jevjudge.{name}"] = mod
    spec.loader.exec_module(mod)

jev = sys.modules["jevjudge.jev"]
gate = sys.modules["jevjudge.gate"]

OPENROUTER_RESPONSE = {
    "model": "~typesafe/jev-latest",
    "answers": {
        "destructive": {"type": "noul", "noul": 0.95},
        "exfiltration": {"type": "noul", "noul": 0.12},
        "impact": {
            "type": "score",
            "score": 2.8,
            "legend": {"0": "None", "1": "Minor", "2": "Major", "3": "Severe"},
            "probabilities": {"0": 0.02, "1": 0.08, "2": 0.5, "3": 0.4},
            "confidence": 0.91,
        },
    },
    "usage": {"prompt_tokens": 900, "completion_tokens": 0},
}


class _StubResponse:
    """Minimal context-manager response wrapping a JSON body."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(monkeypatched_urlopen=None, key="or-test-key"):
    """JevClient pinned to the openrouter backend with a stubbed transport."""
    client = jev.JevClient(
        base_url="https://unused.example/v4/ai",
        model="typesafe-ai/jev",
        backend="openrouter",
        timeout=1.0,
        cache_seconds=0,  # no cross-test cache bleed
        min_interval_s=0.0,
    )
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if monkeypatched_urlopen is not None:
            return monkeypatched_urlopen(req)
        return _StubResponse(json.dumps(OPENROUTER_RESPONSE).encode())

    client._urlopen = fake_urlopen
    client._sleep = lambda seconds: None
    # Pin the key for hermetic assertions (client resolves it from env/.env).
    jev.os.environ["OPENROUTER_API_KEY"] = key
    return client, calls


def test_resolve_backend_openrouter():
    with monkeypatch_env({"OPENROUTER_API_KEY": "k"}):
        assert jev.resolve_backend("openrouter") == "openrouter"
    with monkeypatch_env({"OPENROUTER_API_KEY": None}):
        assert jev.resolve_backend("openrouter") is None


def test_wire_request_shape():
    client, calls = _client()
    result = client.evaluate({"action": {"tool": "terminal"}}, jev.to_typesafe_questions(gate.GATE_QUESTIONS))
    assert result and result["answers"], result
    req = calls[0]
    assert req.full_url == "https://openrouter.ai/api/alpha/decisions", req.full_url
    auth = req.get_header("Authorization") or req.headers.get("Authorization")
    assert auth == "Bearer or-test-key" or auth == "***", auth  # urllib may redact in repr
    assert jev.openrouter_api_key() == "or-test-key"
    payload = json.loads(req.data.decode())
    assert payload["model"] == "~typesafe/jev-latest", payload["model"]
    assert set(payload["questions"]) == {"destructive", "exfiltration", "impact"}
    assert payload["questions"]["destructive"]["type"] == "noul"
    assert set(payload["questions"]["destructive"]["criteria"]) == {"true", "false"}
    assert payload["questions"]["impact"]["type"] == "score"
    assert len(payload["questions"]["impact"]["criteria"]) == 4
    print("ok  wire request shape (endpoint, auth, model slug, question types)")


def test_answer_normalization():
    client, _ = _client()
    result = client.evaluate({"action": {}}, {"destructive": {"type": "boolean", "instructions": "x?"}})
    answer = result["answers"]["destructive"]
    assert answer == {"type": "boolean", "probability": 0.95}, answer
    assert result["usage"]["prompt_tokens"] == 900
    assert result["latency_ms"] >= 0
    print("ok  noul answer normalized to internal boolean")


def test_gate_judge_end_to_end():
    client, _ = _client()
    verdict = gate.judge(client, "terminal", {"command": "rm -rf /tmp/x"}, gate.DEFAULTS)
    assert verdict is not None and verdict["triggered"], verdict
    assert verdict["probabilities"]["destructive"] == 0.95
    assert verdict["probabilities"]["impact"] >= 2.5
    print("ok  gate.judge() returns a triggered verdict from OpenRouter-shaped answers")


def test_fail_open_on_http_error():
    def boom(req):
        raise urllib.error.HTTPError(req.full_url, 503, "unavailable", {}, None)

    client, _ = _client(monkeypatched_urlopen=boom)
    assert client.evaluate({"action": {}}, {"q": {"type": "boolean", "instructions": "x?"}}) is None
    print("ok  fail-open: HTTP error -> evaluate() returns None")


def test_fail_open_on_malformed_body():
    def bad(req):
        return _StubResponse(b'{"model": "x", "answers": "not-a-map"}')

    client, _ = _client(monkeypatched_urlopen=bad)
    assert client.evaluate({"action": {}}, {"q": {"type": "boolean", "instructions": "x?"}}) is None
    print("ok  fail-open: malformed provider body -> evaluate() returns None")


def test_cache_reuse():
    client, calls = _client()
    client.cache_seconds = 300
    q = {"q": {"type": "boolean", "instructions": "same?"}}
    first = client.evaluate({"s": 1}, q)
    second = client.evaluate({"s": 1}, q)
    assert first == second and len(calls) == 1, len(calls)
    print("ok  identical openrouter calls share one cached judgment")


def test_settings_flow_through_gate():
    captured = {}

    class Ctx:
        def get_config(self, key, default=None):
            captured[key] = default
            return default

    s = gate.settings_for(Ctx())
    assert s["openrouter_model"] == "~typesafe/jev-latest"
    assert s["openrouter_base_url"] == "https://openrouter.ai/api/alpha"
    client = gate.client_for(s)
    assert client.backend == "auto"  # default settings keep auto; explicit config pins openrouter
    print("ok  settings_for/client_for expose openrouter knobs with sane defaults")


def test_custom_settings_reach_openrouter_request():
    class Ctx:
        values = {
            "backend": "openrouter",
            "openrouter_model": "acme/custom-jev",
            "openrouter_base_url": "https://router.example/v1",
            "cache_seconds": 0,
            "min_interval_s": 0.0,
        }

        def get_config(self, key, default=None):
            return self.values.get(key, default)

    client = gate.client_for(gate.settings_for(Ctx()))
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return _StubResponse(json.dumps(OPENROUTER_RESPONSE).encode())

    client._urlopen = fake_urlopen
    with monkeypatch_env({"OPENROUTER_API_KEY": "custom-key"}):
        result = client.evaluate({"action": {}}, {"q": {"type": "boolean"}})

    assert result is not None
    assert calls[0].full_url == "https://router.example/v1/decisions"
    payload = json.loads(calls[0].data.decode())
    assert payload["model"] == "acme/custom-jev", payload
    print("ok  custom OpenRouter endpoint and model flow through gate.client_for")


class monkeypatch_env:
    """Context manager temporarily clearing one env var (and the .env fallback)."""

    def __init__(self, vars):
        self.vars = vars
        self.saved = {}
        self.saved_env = None

    def __enter__(self):
        self.saved = {k: jev.os.environ.get(k) for k in self.vars}
        for k, v in self.vars.items():
            if v is None:
                jev.os.environ.pop(k, None)
            else:
                jev.os.environ[k] = v
        self.saved_env = jev.os.environ.get("HERMES_HOME")
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        jev.os.environ["HERMES_HOME"] = self.tmp.name
        return None

    def __exit__(self, *exc):
        import shutil

        for k, v in self.saved.items():
            if v is None:
                jev.os.environ.pop(k, None)
            else:
                jev.os.environ[k] = v
        if self.saved_env is None:
            jev.os.environ.pop("HERMES_HOME", None)
        else:
            jev.os.environ["HERMES_HOME"] = self.saved_env
        shutil.rmtree(self.tmp.name, ignore_errors=True)
        return False


if __name__ == "__main__":
    test_resolve_backend_openrouter()
    test_wire_request_shape()
    test_answer_normalization()
    test_gate_judge_end_to_end()
    test_fail_open_on_http_error()
    test_fail_open_on_malformed_body()
    test_cache_reuse()
    test_settings_flow_through_gate()
    test_custom_settings_reach_openrouter_request()
    print("\nall openrouter backend tests passed")
