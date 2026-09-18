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
for name in ("jev", "gate"):
    spec = importlib.util.spec_from_file_location(
        f"jevjudge.{name}", BASE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"jevjudge.{name}"] = mod
    spec.loader.exec_module(mod)

jev = sys.modules["jevjudge.jev"]
gate = sys.modules["jevjudge.gate"]


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
    long = jev.redact("x" * 5000, limit=100)
    assert long.startswith("x" * 100) and "+4900 chars" in long, long[:120]
    print("ok  redact mascarou chave e truncou")


def test_build_state_truncates():
    state = gate.build_state("terminal", {"command": "a" * 900, "extra": 1})
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
        test_build_state_truncates,
        test_judge_triggers,
        test_judge_clear_and_custom_threshold,
        test_fail_open,
        test_log_writes_jsonl,
    ):
        fn()
    print("\ntodos os testes offline passaram")
