"""Offline tests for jev-judge — no network, no keys. Run: python3 tests/test_offline.py"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Load the plugin as a package (dir name has a hyphen, so alias it).
pkg = types.ModuleType("jevjudge")
pkg.__path__ = [str(BASE)]
sys.modules["jevjudge"] = pkg
for name in ("jev", "gate", "tools"):
    spec = importlib.util.spec_from_file_location(
        f"jevjudge.{name}", BASE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"jevjudge.{name}"] = mod
    spec.loader.exec_module(mod)

jev = sys.modules["jevjudge.jev"]
gate = sys.modules["jevjudge.gate"]
toolsmod = sys.modules["jevjudge.tools"]


class StubClient:
    """Returns a canned evaluate() result, or None to simulate failure."""

    def __init__(self, answers=None, confidence=None, latency=123, cost="0.00002"):
        self.answers = answers
        self.confidence = confidence or {}
        self.latency = latency
        self.cost = cost

    def evaluate(self, state, questions):
        if self.answers is None:
            return None
        return {
            "answers": self.answers,
            "confidence": self.confidence,
            "cost": self.cost,
            "usage": {},
            "latency_ms": self.latency,
        }


def test_redact():
    fake_key = (
        "sk-" + "a" * 24
    )  # composed so source scanners don't flag a real-looking key
    masked = jev.redact(f"token {fake_key} here")
    assert "[REDACTED]" in masked and fake_key not in masked, masked
    long = jev.redact("lorem ipsum " * 500, limit=100)
    assert long.startswith(("lorem ipsum " * 500)[:100]) and "+5900 chars" in long, long[:120]
    print("ok  redact mascarou chave e truncou")


def test_redact_pocs():
    # Every string is composed below so no secret-shaped literal sits in source.
    jwt = "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + "." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
    akia = "AK" + "IA" + "IOSFODNN7" + "EXAMPLE"
    pem = (
        "-----BEGIN " + "PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n"
        "-----END " + "PRIVATE KEY-----"
    )
    basic = "Authorization: " + "Bas" + "ic " + "dXNl" + "cjpwYXNzd29yZA=="
    ghp = "gh" + "p_" + "x" * 30
    sk_short = "sk-" + "abc123"
    pat = "github_" + "pat_" + "y" * 22
    slack = "xo" + "xb-123456789012-" + "z" * 24
    google = "AI" + "za" + "SyB12345678901234567890"
    vercel = "vc" + "k_live_" + "z" * 24
    blob = "A" * 32
    for secret in (jwt, akia, pem, basic, ghp, sk_short, pat, slack, google, vercel, blob):
        masked = jev.redact("leak " + secret + " end")
        assert "[REDACTED]" in masked and secret not in masked, (secret, masked)
    bearer = "Bear" + "er " + jwt
    masked = jev.redact("auth " + bearer)
    assert "[REDACTED]" in masked and jwt not in masked, masked
    assert jev.redact("hello world") == "hello world"
    print("ok  redact cobriu PoCs (bearer/jwt, akia, pem, basic, gh, sk curta, generico)")


def test_jev_ask_uses_settings():
    import tempfile

    seen = {}

    class RecClient:
        def __init__(self, base_url="", model="", timeout=0.0, cache_seconds=0):
            seen.update(
                base_url=base_url,
                model=model,
                timeout=timeout,
                cache_seconds=cache_seconds,
            )

        def evaluate(self, state, questions):
            return {
                "answers": {"q": {"probability": 0.9}},
                "confidence": {},
                "cost": "0",
                "usage": {},
                "latency_ms": 1,
            }

    class FakeCtx:
        def __init__(self, cfg):
            self.cfg = dict(cfg)

        def get_config(self, key, default=None):
            return self.cfg.get(key, default)

    old_client, old_ctx = gate.JevClient, toolsmod._CTX
    with tempfile.TemporaryDirectory() as d:
        log = str(Path(d) / "ask.log")
        toolsmod.bind(
            FakeCtx(
                {
                    "jev_model": "custom/model",
                    "jev_base_url": "https://example.invalid/v9",
                    "timeout_s": 9.0,
                    "cache_seconds": 9,
                    "log_path": log,
                }
            )
        )
        gate.JevClient = RecClient
        try:
            out = json.loads(
                toolsmod.jev_ask(
                    {
                        "state": "x",
                        "questions": {"q": {"type": "boolean", "instructions": "?"}},
                    }
                )
            )
        finally:
            gate.JevClient = old_client
            toolsmod.bind(old_ctx)
        assert out["answers"] == {"q": {"probability": 0.9}}, out
        assert seen == {
            "base_url": "https://example.invalid/v9",
            "model": "custom/model",
            "timeout": 9.0,
            "cache_seconds": 9,
        }, seen
        line = json.loads(Path(log).read_text().strip())
        assert line["source"] == "ask" and line["outcome"] == "ok", line
    print("ok  jev_ask usa settings (model, base_url, timeout, log)")


def test_build_state_truncates():
    state = gate.build_state("terminal", {"command": "run tests " * 90, "extra": 1})
    cmd = state["action"]["arguments"]["command"]
    assert len(cmd) < 700 and "chars)" in cmd, cmd[-40:]
    print("ok  build_state truncou argumento longo")


def test_judge_triggers():
    stub = StubClient(
        answers={
            "destructive": {"type": "boolean", "probability": 0.97},
            "exfiltration": {"type": "boolean", "probability": 0.10},
            "impact": {"type": "score", "score": 2.9},
        },
        confidence={"destructive": 0.99},
    )
    v = gate.judge(stub, "terminal", {"command": "rm -rf /tmp/x"}, {})
    assert v["triggered"] == ["destructive", "impact"], v
    assert v["thresholds"]["destructive_threshold"] == 0.90
    print("ok  judge disparou destructive + impact")


def test_judge_clear_and_custom_threshold():
    stub = StubClient(
        answers={
            "destructive": {"type": "boolean", "probability": 0.80},
            "exfiltration": {"type": "boolean", "probability": 0.0},
            "impact": {"type": "score", "score": 0.4},
        }
    )
    assert gate.judge(stub, "terminal", {"command": "ls"}, {})["triggered"] == []
    v = gate.judge(stub, "terminal", {"command": "ls"}, {"destructive_threshold": 0.75})
    assert v["triggered"] == ["destructive"], v
    print("ok  judge limpo por padrão, dispara com limiar customizado")


def test_fail_open():
    assert (
        gate.judge(StubClient(answers=None), "terminal", {"command": "ls"}, {}) is None
    )
    print("ok  fail-open: cliente sem resposta devolve None")


def test_log_writes_jsonl(tmpdir=None):
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "jev.log"
        gate.log_decision(str(path), {"source": "gate", "outcome": "clear"})
        line = json.loads(path.read_text().strip())
        assert line["source"] == "gate" and "ts" in line, line
    print("ok  log_decision escreveu JSONL válido")


if __name__ == "__main__":
    for fn in (
        test_redact,
        test_redact_pocs,
        test_jev_ask_uses_settings,
        test_build_state_truncates,
        test_judge_triggers,
        test_judge_clear_and_custom_threshold,
        test_fail_open,
        test_log_writes_jsonl,
    ):
        fn()
    print("\ntodos os testes offline passaram")
