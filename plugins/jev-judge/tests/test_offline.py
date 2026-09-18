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
        def __init__(self, base_url="", model="", timeout=0.0, cache_seconds=0,
                     backend="auto", typesafe_model="", typesafe_base_url="",
                     retry_max_wait_s=0.0, breaker_threshold=0,
                     breaker_cooldown_s=0.0, min_interval_s=0.0):
            seen.update(
                base_url=base_url,
                model=model,
                timeout=timeout,
                cache_seconds=cache_seconds,
                backend=backend,
                typesafe_model=typesafe_model,
                typesafe_base_url=typesafe_base_url,
                retry_max_wait_s=retry_max_wait_s,
                breaker_threshold=breaker_threshold,
                breaker_cooldown_s=breaker_cooldown_s,
                min_interval_s=min_interval_s,
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
            "backend": "auto",
            "typesafe_model": "jev-latest",
            "typesafe_base_url": "https://api.typesafe.ai",
            "retry_max_wait_s": 2.0,
            "breaker_threshold": 3,
            "breaker_cooldown_s": 120,
            "min_interval_s": 0.25,
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


# --- Dual backend (ticket 10): transporte stubado, relógio mockado, sem rede ---

import email.utils as _email_utils
import os as _os
import tempfile as _tempfile
import time as _time
import urllib.error as _urlerror


class _FakeResp:
    def __init__(self, body):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code, retry_after=None):
    hdrs = {}
    if retry_after is not None:
        hdrs["retry-after"] = retry_after
    return _urlerror.HTTPError("https://x.invalid/", code, "limited", hdrs, None)


class _Script:
    """Transporte stubado: sequência de ('ok', body) | ('http', code, retry-after)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []  # (url, headers minúsculos, payload)

    def __call__(self, req, timeout=None):
        headers = {k.lower(): v for k, v in req.header_items()}
        payload = json.loads(req.data.decode())
        self.calls.append((req.full_url, headers, payload))
        kind = self.steps.pop(0)
        if kind[0] == "ok":
            return _FakeResp(kind[1])
        raise _http_error(kind[1], kind[2] if len(kind) > 2 else None)


class _Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


_TS_BODY = {
    "answers": {
        "destructive": {"type": "noul", "noul": 0.97},
        "pick": {
            "type": "choice",
            "choice": "b",
            "probabilities": {"a": 0.1, "b": 0.9},
            "confidence": 0.8,
        },
        "impact": {
            "type": "score",
            "score": 1.6,
            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.5, "3": 0.2},
            "confidence": 0.78,
        },
    },
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

_GW_BODY = {
    "answers": {"destructive": {"type": "boolean", "probability": 0.97}},
    "providerMetadata": {
        "typesafe": {"confidence": {"destructive": 0.99}},
        "gateway": {"cost": "0.00002"},
    },
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

_QUESTIONS = {
    "destructive": {
        "type": "boolean",
        "instructions": "Is it destructive?",
        "criteria": {"true": "loss", "false": "safe"},
    },
    "pick": {
        "type": "choice",
        "instructions": "Which?",
        "criteria": {"a": "first", "b": "second"},
    },
    "impact": {
        "type": "score",
        "instructions": "How bad?",
        "criteria": ["low", "high"],
    },
}


def _dual_client(keys, **kw):
    """Cliente com HERMES_HOME vazio, chaves controladas e estado global zerado."""
    steps = kw.pop("_steps", None) or []
    kw.setdefault("min_interval_s", 0)
    with _tempfile.TemporaryDirectory() as home:
        saved = {
            n: _os.environ.get(n) for n in ("TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY", "HERMES_HOME")
        }
        try:
            for n in ("TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY"):
                _os.environ.pop(n, None)
            for n, v in keys.items():
                _os.environ[n] = v
            _os.environ["HERMES_HOME"] = home
            jev._PACE_LAST.clear()
            jev._BREAKER.clear()
            client = jev.JevClient(**kw)
            clock = _Clock()
            client._clock = clock.monotonic
            client._sleep = clock.sleep
            script = _Script(steps)
            client._urlopen = script
            yield client, script, clock
        finally:
            for n, v in saved.items():
                if v is None:
                    _os.environ.pop(n, None)
                else:
                    _os.environ[n] = v


def _run_dual(keys, **kw):
    """Devolve o context manager; entrar com _enter, sair com _leave."""
    import contextlib

    return contextlib.contextmanager(_dual_client)(keys, **kw)


def _enter(ctx):
    entered = ctx.__enter__()
    entered[0]._test_ctx = ctx
    return entered


def _leave(client):
    client._test_ctx.__exit__(None, None, None)


def test_typesafe_noul_mapping():
    client, script, _ = _enter(_run_dual({"TYPESAFE_API_KEY": "ts-key"}, _steps=[("ok", _TS_BODY)]))
    try:
        res = client.evaluate({"action": "x"}, _QUESTIONS)
        assert res is not None, "typesafe com chave devia responder"
        assert len(script.calls) == 1
        url, headers, payload = script.calls[0]
        assert url == "https://api.typesafe.ai/v1/systemone", url
        assert headers.get("authorization") == "Bearer ts-key", headers
        assert "ai-model-id" not in headers, headers
        assert payload["model"] == "jev-latest", payload
        assert payload["questions"]["destructive"] == {
            "type": "noul",
            "instructions": "Is it destructive?",
            "criteria": {"true": "loss", "false": "safe"},
        }, payload["questions"]["destructive"]
        assert payload["questions"]["pick"]["type"] == "choice"
        assert res["answers"]["destructive"] == {"type": "boolean", "probability": 0.97}, res
        assert res["answers"]["pick"]["choice"] == "b", res
        assert res["answers"]["impact"]["score"] == 1.6, res
        assert res["confidence"] == {"pick": 0.8, "impact": 0.78}, res["confidence"]
        assert res["cost"] is None, res
        assert res["usage"] == {"input_tokens": 10, "output_tokens": 5}, res
    finally:
        _leave(client)
    print("ok  typesafe direto: boolean vira noul, confiança inline, custo None")


def test_gateway_confidence_and_cost():
    client, script, _ = _enter(_run_dual({"AI_GATEWAY_API_KEY": "gw-key"}, _steps=[("ok", _GW_BODY)]))
    try:
        res = client.evaluate({"action": "x"}, _QUESTIONS)
        assert res is not None
        url, headers, payload = script.calls[0]
        assert url == "https://ai-gateway.vercel.sh/v4/ai/evaluation-model", url
        assert headers.get("ai-model-id") == "typesafe-ai/jev", headers
        assert "model" not in payload, payload
        assert payload["questions"]["destructive"]["type"] == "boolean", payload
        assert res["answers"] == _GW_BODY["answers"], res
        assert res["confidence"] == {"destructive": 0.99}, res
        assert res["cost"] == "0.00002", res
    finally:
        _leave(client)
    print("ok  gateway: confiança via providerMetadata, custo repassado")


def _selection_matrix():
    return [
        ({"TYPESAFE_API_KEY": "t", "AI_GATEWAY_API_KEY": "g"}, "auto", "typesafe"),
        ({"AI_GATEWAY_API_KEY": "g"}, "auto", "gateway"),
        ({"TYPESAFE_API_KEY": "t"}, "auto", "typesafe"),
        ({}, "auto", None),
        ({"TYPESAFE_API_KEY": "", "AI_GATEWAY_API_KEY": "g"}, "auto", "gateway"),
        ({"AI_GATEWAY_API_KEY": "g"}, "typesafe", None),
        ({"TYPESAFE_API_KEY": "t"}, "gateway", None),
        ({"TYPESAFE_API_KEY": "t", "AI_GATEWAY_API_KEY": "g"}, "gateway", "gateway"),
        ({"TYPESAFE_API_KEY": "t", "AI_GATEWAY_API_KEY": "g"}, "bogus", "typesafe"),
    ]


def test_backend_selection():
    want_url = {
        "typesafe": "https://api.typesafe.ai/v1/systemone",
        "gateway": "https://ai-gateway.vercel.sh/v4/ai/evaluation-model",
    }
    for keys, backend, want in _selection_matrix():
        steps = [("ok", _GW_BODY)] if want else []
        client, script, _ = _enter(_run_dual(keys, backend=backend, _steps=steps))
        try:
            assert jev.resolve_backend(backend) == want, (keys, backend, want)
            res = client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}})
            if want is None:
                assert res is None and script.calls == [], (keys, backend)
            else:
                assert res is not None and len(script.calls) == 1, (keys, backend)
                assert script.calls[0][0] == want_url[want], script.calls[0][0]
        finally:
            _leave(client)
    # Sem chave e com transporte que explodiria se tocado: silêncio total.
    client, script, _ = _enter(_run_dual({}))
    try:
        assert client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert script.calls == []
    finally:
        _leave(client)
    print("ok  backend auto|typesafe|gateway resolve e silencia sem chave")


def test_retry_after_seconds():
    client, script, clock = _enter(
        _run_dual({"AI_GATEWAY_API_KEY": "g"}, _steps=[("http", 429, "1"), ("ok", _GW_BODY)])
    )
    try:
        res = client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}})
        assert res is not None and len(script.calls) == 2, script.calls
        assert clock.sleeps == [1.0], clock.sleeps
    finally:
        _leave(client)
    print("ok  retry honrou Retry-After em segundos (1 retry)")


def test_retry_after_http_date():
    when = _email_utils.formatdate(_time.time() + 1, usegmt=True)
    client, script, clock = _enter(
        _run_dual({"AI_GATEWAY_API_KEY": "g"}, _steps=[("http", 429, when), ("ok", _GW_BODY)])
    )
    try:
        res = client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}})
        assert res is not None and len(script.calls) == 2, script.calls
        assert len(clock.sleeps) == 1 and 0 < clock.sleeps[0] <= 2.0, clock.sleeps
    finally:
        _leave(client)
    print("ok  retry honrou Retry-After como data HTTP")


def test_retry_after_garbage():
    client, script, clock = _enter(
        _run_dual({"AI_GATEWAY_API_KEY": "g"}, _steps=[("http", 429, "banana")])
    )
    try:
        assert jev.retry_after_s("banana", _time.time()) is None
        assert jev.retry_after_s("", _time.time()) is None
        assert jev.retry_after_s(None, _time.time()) is None
        assert client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 1 and clock.sleeps == [], (script.calls, clock.sleeps)
    finally:
        _leave(client)
    print("ok  Retry-After lixo: sem retry, fail-open")


def test_retry_after_too_long():
    client, script, clock = _enter(
        _run_dual(
            {"AI_GATEWAY_API_KEY": "g"},
            retry_max_wait_s=2.0,
            _steps=[("http", 429, "30")],
        )
    )
    try:
        assert client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 1 and clock.sleeps == [], (script.calls, clock.sleeps)
    finally:
        _leave(client)
    print("ok  espera acima do teto: sem retry, fail-open")


def test_retry_529():
    client, script, clock = _enter(
        _run_dual({"TYPESAFE_API_KEY": "t"}, _steps=[("http", 529, "1"), ("ok", _TS_BODY)])
    )
    try:
        res = client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}})
        assert res is not None and len(script.calls) == 2, script.calls
        assert res["answers"]["destructive"] == {"type": "boolean", "probability": 0.97}, res
    finally:
        _leave(client)
    print("ok  529 tratado como rate limit (1 retry)")


def test_retry_only_once():
    client, script, clock = _enter(
        _run_dual(
            {"AI_GATEWAY_API_KEY": "g"},
            _steps=[("http", 429, "1"), ("http", 429, "1"), ("ok", _GW_BODY)],
        )
    )
    try:
        assert client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 2, script.calls  # nunca em loop
    finally:
        _leave(client)
    print("ok  retry único: segundo 429 não tenta de novo")


def test_breaker_opens_and_recovers():
    client, script, clock = _enter(
        _run_dual(
            {"AI_GATEWAY_API_KEY": "g"},
            breaker_threshold=3,
            breaker_cooldown_s=120,
            _steps=[("http", 429, "lixo")] * 3,
        )
    )
    try:
        for i in range(3):
            assert client.evaluate({"n": i}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 3, script.calls
        # Breaker aberto: silêncio sem tocar o transporte.
        assert client.evaluate({"n": 99}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 3, script.calls
        # Cooldown passou: volta a tentar; sucesso zera o contador.
        clock.t += 121
        script.steps.append(("ok", _GW_BODY))
        res = client.evaluate({"n": 100}, {"q": {"type": "boolean", "instructions": "?"}})
        assert res is not None and len(script.calls) == 4, script.calls
        script.steps.extend([("http", 429, "lixo")] * 2)
        assert client.evaluate({"n": 101}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert client.evaluate({"n": 102}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 6, script.calls  # 2 falhas pós-sucesso não abrem
    finally:
        _leave(client)
    print("ok  breaker abriu após 3, recuperou após cooldown, sucesso zerou")


def test_min_interval_spacing():
    client, script, clock = _enter(
        _run_dual(
            {"AI_GATEWAY_API_KEY": "g"},
            min_interval_s=0.25,
            _steps=[("ok", _GW_BODY), ("ok", _GW_BODY)],
        )
    )
    try:
        assert client.evaluate({"n": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is not None
        assert client.evaluate({"n": 2}, {"q": {"type": "boolean", "instructions": "?"}}) is not None
        assert clock.sleeps == [0.25], clock.sleeps
    finally:
        _leave(client)
    print("ok  min_interval_s espaçou chamadas (clock mockado)")


def test_silence_without_key():
    client, script, _ = _enter(_run_dual({}, _steps=[("ok", _GW_BODY)]))
    try:
        assert client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert script.calls == [], script.calls
    finally:
        _leave(client)
    print("ok  sem chave nenhuma: silencioso, transporte intocado")


def test_retry_after_nonfinite():
    now = _time.time()
    assert jev.retry_after_s("nan", now) is None
    assert jev.retry_after_s("inf", now) is None
    assert jev.retry_after_s("-inf", now) is None
    client, script, clock = _enter(
        _run_dual({"AI_GATEWAY_API_KEY": "g"}, _steps=[("http", 429, "nan")])
    )
    try:
        assert client.evaluate({"a": 1}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 1 and clock.sleeps == [], (script.calls, clock.sleeps)
    finally:
        _leave(client)
    print("ok  Retry-After nan/inf: sem retry, fail-open")


def test_breaker_counts_once_per_evaluate():
    client, script, clock = _enter(
        _run_dual(
            {"AI_GATEWAY_API_KEY": "g"},
            breaker_threshold=4,
            _steps=[("http", 429, "1"), ("http", 429, "1")] * 3,
        )
    )
    try:
        for i in range(3):
            assert client.evaluate({"n": i}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        # 3 evaluates x 2 attempts; breaker counts 1 per evaluate, still closed.
        assert len(script.calls) == 6, script.calls
        script.steps.append(("http", 429, "lixo"))
        assert client.evaluate({"n": 99}, {"q": {"type": "boolean", "instructions": "?"}}) is None
        assert len(script.calls) == 7, script.calls
    finally:
        _leave(client)
    print("ok  breaker conta 1 por evaluate (429+429 sem retry duplo)")


def test_boolean_criteria_passthrough():
    out = jev.to_typesafe_questions(
        {
            "q": {
                "type": "boolean",
                "instructions": "?",
                "criteria": {"true": "loss", "false": "safe"},
            }
        }
    )
    assert out == {
        "q": {"type": "noul", "instructions": "?", "criteria": {"true": "loss", "false": "safe"}}
    }, out
    plain = jev.to_typesafe_questions({"q": {"type": "boolean", "instructions": "?"}})
    assert plain == {"q": {"type": "noul", "instructions": "?"}}, plain
    print("ok  criteria propagado no boolean->noul")


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
        test_typesafe_noul_mapping,
        test_gateway_confidence_and_cost,
        test_backend_selection,
        test_retry_after_seconds,
        test_retry_after_http_date,
        test_retry_after_garbage,
        test_retry_after_too_long,
        test_retry_529,
        test_retry_only_once,
        test_breaker_opens_and_recovers,
        test_min_interval_spacing,
        test_silence_without_key,
        test_retry_after_nonfinite,
        test_breaker_counts_once_per_evaluate,
        test_boolean_criteria_passthrough,
    ):
        fn()
    print("\ntodos os testes offline passaram")
