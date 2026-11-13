"""Offline tests for the Nace (Drex) backend — no network, no real keys.

Run: python3 tests/test_offline_nace.py
Transport stubbed, clock mocked, NACE_API_KEY and HERMES_HOME isolated per test.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import logging
import os
import socket
import sys
import tempfile
import types
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from jev_router import client as C
from jev_router import router as RT

_pkg = types.ModuleType("jevskillrouter_nace")
_pkg.__path__ = [str(BASE)]
sys.modules["jevskillrouter_nace"] = _pkg
_spec = importlib.util.spec_from_file_location(
    "jevskillrouter_nace", BASE / "__init__.py", submodule_search_locations=[str(BASE)]
)
plugin = importlib.util.module_from_spec(_spec)
plugin.__path__ = [str(BASE)]
sys.modules["jevskillrouter_nace"] = plugin
_spec.loader.exec_module(plugin)

KEYS = ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY", "NACE_API_KEY")
NACE_URL = "https://console.nace.ai/v1/systemone"
TS_URL = "https://api.typesafe.ai/v1/systemone"
SECRET = "nace_sk_offline_test_value_0123456789"

# Shaped like a real Drex /v1/systemone answer (legend: object).
DREX_BODY = {
    "answers": {
        "which": {
            "type": "choice",
            "choice": "pdf",
            "probabilities": {"pdf": 0.95, "xlsx": 0.04, "none_of_these": 0.01},
            "confidence": 0.95,
        },
        "gate::acts_on_user_system": {"type": "noul", "noul": 0.99, "confidence": 0.99},
        "fits::pdf": {"type": "noul", "noul": 0.97, "confidence": 0.97},
        "rating": {"type": "score", "score": 0.7, "confidence": 0.6},
    },
    "model": "drex-max-v1.1",
    "request_id": "req_0123456789abcdef0123456789abcdef",
    "usage": {"input_tokens": 4723, "output_tokens": 0},
}

Q = {
    "which": {"type": "choice", "instructions": "?", "criteria": {"pdf": "p", "xlsx": "x"}},
    "gate::acts_on_user_system": {"type": "boolean", "instructions": "?"},
    "fits::pdf": {"type": "boolean", "instructions": "?"},
}


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})
        self.hooks = []
        self.commands = []

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def set_config(self, key, value):
        self.cfg[key] = value

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_cli_command(self, name, help, setup_fn, handler_fn, description=""):
        self.commands.append((name, help, setup_fn, handler_fn, description))


class FakeResp:
    def __init__(self, body):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(code, retry_after=None):
    hdrs = {"retry-after": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://x.invalid/", code, "err", hdrs, None)


class Script:
    """Stub transport: steps are ('ok', body) | ('http', code, retry-after) | ('raise', exc)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []  # (url, lower-cased headers, raw payload bytes)

    def __call__(self, req, timeout=None):
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((req.full_url, headers, req.data))
        step = self.steps.pop(0)
        if step[0] == "ok":
            return FakeResp(step[1])
        if step[0] == "raise":
            raise step[1]
        raise http_error(step[1], step[2] if len(step) > 2 else None)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _environ_secret(name, default=None):
    return os.environ.get(name, default)


@contextlib.contextmanager
def env(keys, get_secret=None):
    """Only `keys` set among the backend keys; empty HERMES_HOME; global state reset.

    `get_secret` reads the process env unless one is passed, so a Hermes install on
    the path (its scope or multiplex state) cannot change which key the tests see.
    A `fake_secret_scope` must be nested INSIDE this block (or passed here): one
    wrapped around it is replaced. `no_secret_scope` hides the module instead.
    """
    saved = {n: os.environ.get(n) for n in KEYS + ("HERMES_HOME",)}
    with tempfile.TemporaryDirectory() as home, fake_secret_scope(get_secret or _environ_secret):
        try:
            for n in KEYS:
                os.environ.pop(n, None)
            os.environ.update(keys)
            os.environ["HERMES_HOME"] = home
            C._PACE_LAST.clear()
            C._BREAKER.clear()
            yield Path(home)
        finally:
            for n, v in saved.items():
                if v is None:
                    os.environ.pop(n, None)
                else:
                    os.environ[n] = v


@contextlib.contextmanager
def nace_client(keys=None, steps=(), get_secret=None, **kw):
    keys = {"NACE_API_KEY": SECRET} if keys is None else keys
    kw.setdefault("min_interval_s", 0)
    kw.setdefault("backend", "nace")
    with env(keys, get_secret):
        client = C.JevClient(**kw)
        clock = Clock()
        client._clock = clock.monotonic
        client._sleep = clock.sleep
        script = Script(steps)
        client._urlopen = script
        yield client, script, clock


@contextlib.contextmanager
def fake_secret_scope(get_secret):
    """Patch get_secret on Hermes' real module when it is importable, else add a stub.

    Only `agent.secret_scope` is stubbed: the rest of a real `agent` package (for
    example the credential pool) must keep working.
    """
    try:
        import agent.secret_scope as real  # type: ignore
    except ImportError:
        real = None
    if real is not None:
        saved = real.get_secret
        real.get_secret = get_secret
        try:
            yield
        finally:
            real.get_secret = saved
        return
    added = []
    if "agent" not in sys.modules:
        pkg = types.ModuleType("agent")
        pkg.__path__ = []
        sys.modules["agent"] = pkg
        added.append("agent")
    stub = types.ModuleType("agent.secret_scope")
    stub.get_secret = get_secret
    old_stub = sys.modules.get("agent.secret_scope")
    sys.modules["agent.secret_scope"] = stub
    try:
        yield
    finally:
        if old_stub is None:
            sys.modules.pop("agent.secret_scope", None)
        else:
            sys.modules["agent.secret_scope"] = old_stub
        for name in added:
            sys.modules.pop(name, None)


@contextlib.contextmanager
def no_secret_scope():
    """`from agent.secret_scope import ...` raises ImportError, as outside Hermes."""
    missing = object()
    saved = sys.modules.get("agent.secret_scope", missing)
    sys.modules["agent.secret_scope"] = None
    try:
        yield
    finally:
        if saved is missing:
            sys.modules.pop("agent.secret_scope", None)
        else:
            sys.modules["agent.secret_scope"] = saved


@contextlib.contextmanager
def captured_logs():
    """Every record from the plugin's loggers, at DEBUG, as formatted text."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


# 1
def test_nace_settings_defaults():
    s = plugin._settings(FakeCtx({}))
    assert s["nace_model"] == "drex-max-v1.1", s
    assert s["nace_base_url"] == "https://console.nace.ai", s
    s = plugin._settings(FakeCtx({"nace_model": "drex-latest", "nace_base_url": "https://n.invalid"}))
    assert (s["nace_model"], s["nace_base_url"]) == ("drex-latest", "https://n.invalid"), s
    assert C.NACE_CLIENT == f"hermes-jev-skill-router/{C.PLUGIN_VERSION}"
    manifest = (BASE / "plugin.yaml").read_text()
    assert f"version: {C.PLUGIN_VERSION}\n" in manifest, "PLUGIN_VERSION out of step with plugin.yaml"
    print("ok  nace settings defaults (drex-max-v1.1 @ console.nace.ai), client version = manifest")


# 2
def test_nace_backend_matrix():
    cases = [
        ({"NACE_API_KEY": "n"}, "auto", "nace"),
        ({k: k.lower() for k in KEYS}, "auto", "typesafe"),
        ({"OPENROUTER_API_KEY": "o", "NACE_API_KEY": "n"}, "auto", "openrouter"),
        ({"AI_GATEWAY_API_KEY": "g", "NACE_API_KEY": "n"}, "auto", "gateway"),
        ({"TYPESAFE_API_KEY": "t", "AI_GATEWAY_API_KEY": "g"}, "nace", None),
        ({k: k.lower() for k in KEYS}, "nace", "nace"),
        ({"NACE_API_KEY": "  "}, "auto", None),
        ({"NACE_API_KEY": "n"}, " NACE ", "nace"),
    ]
    for keys, backend, want in cases:
        with env(keys):
            assert C.resolve_backend(backend) == want, (keys, backend, want)
    # Forced nace without its key: no call at all, even with every other key present.
    with nace_client(keys={"TYPESAFE_API_KEY": "t", "AI_GATEWAY_API_KEY": "g"},
                     steps=[("ok", DREX_BODY)]) as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is None
        assert script.calls == [], script.calls
    # Forced nace with every key: only the Nace endpoint and key are used.
    with nace_client(keys={**{k: k.lower() for k in KEYS}, "NACE_API_KEY": SECRET},
                     steps=[("ok", DREX_BODY)]) as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is not None
        url, headers, _ = script.calls[0]
        assert url == NACE_URL and headers["authorization"] == f"Bearer {SECRET}", (url, headers)
    print("ok  nace backend: auto-last, forced, silent without its key, other keys ignored")


# 3
def test_nace_wire_shape():
    with nace_client(steps=[("ok", DREX_BODY)]) as (client, script, _):
        assert client.evaluate({"request": "merge two PDFs"}, Q) is not None
        url, headers, raw = script.calls[0]
        assert url == NACE_URL, url
        assert headers["authorization"] == f"Bearer {SECRET}", headers
        assert headers["content-type"] == "application/json", headers
        assert headers["x-nace-client"] == C.NACE_CLIENT, headers
        assert headers["x-nace-legend"] == "object", headers
        assert "ai-model-id" not in headers, headers
        payload = json.loads(raw)
        assert set(payload) == {"state", "model", "questions"}, payload
        assert payload["model"] == "drex-max-v1.1", payload
        assert payload["state"] == {"request": "merge two PDFs"}, payload
        assert payload["questions"]["gate::acts_on_user_system"] == {"type": "noul", "instructions": "?"}
        assert payload["questions"]["which"] == Q["which"], payload
        assert SECRET.encode() not in raw, "key leaked into the payload"
    with nace_client(steps=[("ok", DREX_BODY)], nace_model="drex-latest",
                     nace_base_url="https://n.invalid/") as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is not None
        assert script.calls[0][0] == "https://n.invalid/v1/systemone", script.calls[0][0]
        assert json.loads(script.calls[0][2])["model"] == "drex-latest"
    print("ok  nace wire: /v1/systemone, Bearer, X-Nace-Client, X-Nace-Legend, model, noul; key not in payload")


# 4
def test_nace_real_shaped_body():
    with nace_client(steps=[("ok", DREX_BODY)]) as (client, script, _):
        res = client.evaluate({"request": "x"}, Q)
        a = res["answers"]
        assert a["which"]["choice"] == "pdf", a
        assert a["which"]["probabilities"] == DREX_BODY["answers"]["which"]["probabilities"], a
        assert a["gate::acts_on_user_system"] == {"type": "boolean", "probability": 0.99}, a
        assert a["fits::pdf"] == {"type": "boolean", "probability": 0.97}, a
        assert a["rating"]["score"] == 0.7, a
        assert set(a) == set(DREX_BODY["answers"]), a  # ids come back byte for byte
        assert res["confidence"]["which"] == 0.95, res["confidence"]
        assert res["cost"] is None, res
        assert res["usage"] == DREX_BODY["usage"], res
        assert res["model"] == "drex-max-v1.1", res
        assert res["request_id"] == DREX_BODY["request_id"], res
    # Non-string attribution fields are dropped, never invented.
    body = {**DREX_BODY, "model": 7, "request_id": None}
    with nace_client(steps=[("ok", body)]) as (client, script, _):
        res = client.evaluate({"request": "x"}, Q)
        assert res is not None and "model" not in res and "request_id" not in res, res
    print("ok  nace real-shaped body: ids kept, noul normalized, confidence inline, model + request_id")


# 5
def test_nace_malformed_bodies_fail_open():
    for body in ([], "oops", {"answers": []}, {"answers": "x"}, {"answers": None}):
        with nace_client(steps=[("ok", body)]) as (client, script, _):
            assert client.evaluate({"request": "x"}, Q) is None, body
    body = {"answers": {"fits::pdf": {"type": "noul", "noul": "nan"}}, "model": "drex-max-v1.1"}
    with nace_client(steps=[("ok", body)]) as (client, script, _):
        res = client.evaluate({"request": "x"}, Q)
        assert res is not None and "fits::pdf" not in res["answers"], res  # never 0.0
    with nace_client(steps=[("raise", ValueError("not json"))]) as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is None
    print("ok  nace malformed bodies fail open (list, non-map answers, noul nan, bad JSON)")


# 6
def test_nace_http_errors_and_breaker():
    for code in (401, 402, 422, 500):
        with nace_client(steps=[("http", code, "1")]) as (client, script, clock):
            assert client.evaluate({"request": "x"}, Q) is None, code
            assert len(script.calls) == 1 and clock.sleeps == [], (code, script.calls)
            assert NACE_URL not in C._BREAKER, (code, C._BREAKER)
    with nace_client(steps=[("http", 529, "2"), ("ok", DREX_BODY)]) as (client, script, clock):
        assert client.evaluate({"request": "x"}, Q) is not None
        assert len(script.calls) == 2 and clock.sleeps == [2.0], (script.calls, clock.sleeps)
    with nace_client(keys={"NACE_API_KEY": SECRET, "TYPESAFE_API_KEY": "t"},
                     steps=[("http", 429, "30")] * 3) as (client, script, _):
        for i in range(3):
            assert client.evaluate({"request": i}, Q) is None
        assert client._breaker_open(NACE_URL), C._BREAKER
        assert client.evaluate({"request": 99}, Q) is None
        assert len(script.calls) == 3, script.calls  # open: transport untouched
        assert not client._breaker_open(TS_URL), C._BREAKER  # typesafe unaffected
        ts = C.JevClient(backend="typesafe", min_interval_s=0)
        ts._urlopen = Script([("ok", DREX_BODY)])
        assert ts.evaluate({"request": 99}, Q) is not None
    print("ok  nace 401/402/422 no retry no breaker; 529 retries once; breaker per Nace URL only")


# 7
def test_nace_timeout_fail_open():
    for exc in (socket.timeout("timed out"), TimeoutError("timed out"),
                urllib.error.URLError(socket.timeout("timed out"))):
        with nace_client(steps=[("raise", exc)], timeout=0.05) as (client, script, _):
            assert client.evaluate({"request": "x"}, Q) is None, exc
            assert len(script.calls) == 1, script.calls
            assert NACE_URL not in C._BREAKER, C._BREAKER
    print("ok  nace timeout fails open, no retry, breaker untouched")


# 8
def test_nace_cache_key_by_model():
    with nace_client(steps=[("ok", DREX_BODY), ("ok", DREX_BODY)]) as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is not None
        assert client.evaluate({"request": "x"}, Q) is not None
        assert len(script.calls) == 1, script.calls  # cached
        client.nace_model = "drex-latest"
        assert client.evaluate({"request": "x"}, Q) is not None
        assert len(script.calls) == 2, script.calls  # new model, no stale answer
    print("ok  nace cache key includes nace_model")


# 9
def test_nace_key_via_get_secret():
    asked = []

    def get_secret(name, default=None):
        asked.append(name)
        return f"  {SECRET}\n" if name == "NACE_API_KEY" else default

    with env({}), fake_secret_scope(get_secret):
        assert C.nace_api_key() == SECRET
        assert C.resolve_backend("auto") == "nace"
        assert asked and set(asked) == {"NACE_API_KEY"}, asked

    class UnscopedSecretError(RuntimeError):
        pass

    def refuse(name, default=None):
        raise UnscopedSecretError(name)

    # A refusal is silence even when the process env holds a key.
    with env({"NACE_API_KEY": "from-env"}), fake_secret_scope(refuse):
        assert C.nace_api_key() == ""
        assert C.resolve_backend("nace") is None
        assert C.resolve_backend("auto") is None
    with env({"NACE_API_KEY": "from-env"}), fake_secret_scope(lambda n, d=None: None):
        assert C.nace_api_key() == ""
    with env({"NACE_API_KEY": "from-env"}), no_secret_scope():
        assert C.nace_api_key() == "from-env"  # outside Hermes: process env
    with env({}) as home:
        (home / ".env").write_text("NACE_API_KEY=from-dotenv\n")
        assert C.nace_api_key() == ""  # NACE_API_KEY comes from Hermes, not a .env read
        with no_secret_scope():
            assert C.nace_api_key() == ""  # outside Hermes the plugin does not read .env either
    print("ok  NACE_API_KEY via agent.secret_scope.get_secret; refusal is silent")


def _roster(home):
    for name, desc in (("pdf", "Fill, merge and split PDF files and forms."),
                       ("xlsx", "Create and edit Excel spreadsheets."),
                       ("deploy", "Deploy the site.")):
        d = home / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\nBody.\n")
    return RT.roster_mod.load_roster([home / "skills"])


# 10
def test_nace_suggest_end_to_end():
    first = {
        "answers": {
            "which": {"type": "choice", "choice": "pdf",
                      "probabilities": {"pdf": 0.9, "xlsx": 0.06, "deploy": 0.04}, "confidence": 0.9},
            "gate::acts_on_user_system": {"type": "noul", "noul": 0.95, "confidence": 0.95},
            "gate::would_follow_documented_procedure": {"type": "noul", "noul": 0.8, "confidence": 0.8},
            "gate::prose_suffices": {"type": "noul", "noul": 0.05, "confidence": 0.95},
        },
        "model": "drex-max-v1.1", "request_id": "req_first",
        "usage": {"input_tokens": 100},
    }
    second = {
        "answers": {
            "which": {"type": "choice", "choice": "pdf",
                      "probabilities": {"pdf": 0.93, "xlsx": 0.03, "deploy": 0.02, "none_of_these": 0.02},
                      "confidence": 0.93},
            "fits::pdf": {"type": "noul", "noul": 0.96, "confidence": 0.96},
            "fits::xlsx": {"type": "noul", "noul": 0.1, "confidence": 0.9},
            "fits::deploy": {"type": "noul", "noul": 0.02, "confidence": 0.98},
        },
        "model": "drex-max-v1.1", "request_id": "req_second",
        "usage": {"input_tokens": 200},
    }
    with nace_client(steps=[("ok", first), ("ok", second)]) as (client, script, _):
        home = Path(os.environ["HERMES_HOME"])
        skills = _roster(home)
        res = RT.suggest(client, "merge two PDFs and fill in the form", skills)
        assert res is not None and res.skill == "pdf", res
        assert res.calls == 2 and len(script.calls) == 2, script.calls
        assert res.model == "drex-max-v1.1" and res.request_id == "req_second", res
        assert res.cost is None, res
        # Through the plugin's decide(): the log line carries model + request_id, never the key.
        settings = plugin._settings(FakeCtx({"mode": "on", "backend": "nace",
                                             "roster_dir": str(home / "skills")}))
        client._cache.clear()
        script.steps.extend([("ok", first), ("ok", second)])
        plugin._roster_cache.update(at=0.0, key=None, skills=[])
        result, info = plugin.decide(settings, "merge two PDFs and fill in the form", client=client)
        assert result is not None and info == {"outcome": "suggested"}, info
        log = (home / "logs" / "jev-skill-router.log").read_text()
        line = json.loads(log.strip().splitlines()[-1])
        assert line["skill"] == "pdf" and line["model"] == "drex-max-v1.1", line
        assert line["request_id"] == "req_second", line
        assert SECRET not in log, "key leaked into the decision log"
    print("ok  nace suggest end to end: picks pdf, log line has model + request_id, no key")


# 11
def test_nace_status_and_check_never_print_key():
    with env({"NACE_API_KEY": SECRET}) as home:
        (home / "skills").mkdir()
        ctx = FakeCtx({"mode": "off", "backend": "nace", "roster_dir": str(home / "skills"),
                       "log_path": str(home / "logs" / "r.log")})
        plugin.register(ctx)
        _, _, setup_fn, handler_fn, _ = ctx.commands[0]
        parser = argparse.ArgumentParser()
        setup_fn(parser)
        for action, want_rc in (("status", 0), ("check", 0)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert handler_fn(parser.parse_args([action])) == want_rc, action
            text = out.getvalue()
            assert "NACE_API_KEY=present" in text, (action, text)
            assert SECRET not in text, (action, "key printed")
        os.environ.pop("NACE_API_KEY")
        for action, want_rc in (("status", 0), ("check", 1)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert handler_fn(parser.parse_args([action])) == want_rc, action
            assert "NACE_API_KEY=missing" in out.getvalue(), (action, out.getvalue())
    print("ok  status/check print NACE_API_KEY=present|missing, never the value")


# 12
def test_nace_base_url_https_only():
    for url in ("https://console.nace.ai", "https://n.invalid:8443/x", "http://localhost:8080",
                "http://127.0.0.1", "http://[::1]:3000", "HTTP://LOCALHOST"):
        assert C.nace_base_url_ok(url), url
    for url in ("http://console.nace.ai", "http://10.0.0.5", "http://localhost.evil.test",
                "ftp://console.nace.ai", "https://", "console.nace.ai", "", "http://[::1"):
        assert not C.nace_base_url_ok(url), url
    with captured_logs() as logs:
        with nace_client(steps=[("ok", DREX_BODY)],
                         nace_base_url="http://console.nace.ai") as (client, script, _):
            assert client.evaluate({"request": "x"}, Q) is None
            assert script.calls == [], script.calls  # refused before sending the key
    assert SECRET not in logs.getvalue(), "key in logs"
    with nace_client(steps=[("ok", DREX_BODY)],
                     nace_base_url="http://127.0.0.1:9999") as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is not None
        assert script.calls[0][0] == "http://127.0.0.1:9999/v1/systemone", script.calls
    # check flags the refused URL; status still prints it.
    with env({"NACE_API_KEY": SECRET}) as home:
        (home / "skills").mkdir()
        ctx = FakeCtx({"backend": "nace", "nace_base_url": "http://console.nace.ai",
                       "roster_dir": str(home / "skills"), "log_path": str(home / "l.log")})
        plugin.register(ctx)
        _, _, setup_fn, handler_fn, _ = ctx.commands[0]
        parser = argparse.ArgumentParser()
        setup_fn(parser)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert handler_fn(parser.parse_args(["check"])) == 1
        assert "https:// required" in out.getvalue() and SECRET not in out.getvalue(), out.getvalue()
    print("ok  nace_base_url: https:// only (http:// for loopback); otherwise no call, check fails")


# 13
def test_nace_key_with_whitespace_or_control_rejected():
    for bad in ("nace_sk_ab\ncd", "nace_sk_ab cd", "nace_sk_ab\rX-Evil: 1", "nace_sk_\x00ab",
                "nace_sk_ab\tcd", "nace_sk_\x7fab", "nace_sk_\u2028ab", "nace_sk_é"):
        # Delivered through get_secret: the process env cannot hold a NUL byte.
        def get_secret(name, default=None, value=bad):
            return value if name == "NACE_API_KEY" else default

        with captured_logs() as logs, nace_client(
            keys={}, steps=[("ok", DREX_BODY)], get_secret=get_secret
        ) as (client, script, _):
            from agent.secret_scope import get_secret as active  # type: ignore
            assert active("NACE_API_KEY") == bad, repr(bad)  # the malformed key is what the client sees
            assert C.nace_api_key() == "", repr(bad)
            assert C.resolve_backend("nace") is None, repr(bad)
            assert client.evaluate({"request": "x"}, Q) is None, repr(bad)
            assert script.calls == [], (repr(bad), script.calls)
        assert "nace_sk_" not in logs.getvalue(), (repr(bad), logs.getvalue())  # no key, escaped or not
    with env({"NACE_API_KEY": f"  {SECRET}\n"}):
        assert C.nace_api_key() == SECRET  # surrounding whitespace is stripped first
    print("ok  NACE_API_KEY with whitespace/control characters: silent, no call, not logged")


# 14
def test_nace_cache_key_by_base_url():
    with nace_client(steps=[("ok", DREX_BODY), ("ok", DREX_BODY)]) as (client, script, _):
        assert client.evaluate({"request": "x"}, Q) is not None
        client.nace_base_url = "https://n2.invalid"
        assert client.evaluate({"request": "x"}, Q) is not None
        assert [c[0] for c in script.calls] == [NACE_URL, "https://n2.invalid/v1/systemone"], script.calls
    print("ok  nace cache key includes nace_base_url")


# 15
def test_other_backends_carry_no_attribution():
    attributed = {**DREX_BODY, "model": "x", "request_id": "req_x"}
    gw_body = {"answers": {"which": {"choice": "pdf", "probabilities": {"pdf": 1.0}}},
               "providerMetadata": {}, "model": "x", "request_id": "req_x"}
    for keys, body in (({"TYPESAFE_API_KEY": "t"}, attributed),
                       ({"OPENROUTER_API_KEY": "o"}, attributed),
                       ({"AI_GATEWAY_API_KEY": "g"}, gw_body)):
        with nace_client(keys=keys, steps=[("ok", body)], backend="auto") as (client, script, _):
            res = client.evaluate({"request": "x"}, Q)
            assert res is not None and len(script.calls) == 1, keys
            assert "model" not in res and "request_id" not in res, (keys, res)
            assert "x-nace-client" not in script.calls[0][1], (keys, script.calls[0][1])
    print("ok  typesafe/openrouter/gateway results carry no model/request_id, no Nace headers")


if __name__ == "__main__":
    for fn in (
        test_nace_settings_defaults,
        test_nace_backend_matrix,
        test_nace_wire_shape,
        test_nace_real_shaped_body,
        test_nace_malformed_bodies_fail_open,
        test_nace_http_errors_and_breaker,
        test_nace_timeout_fail_open,
        test_nace_cache_key_by_model,
        test_nace_key_via_get_secret,
        test_nace_suggest_end_to_end,
        test_nace_status_and_check_never_print_key,
        test_nace_base_url_https_only,
        test_nace_key_with_whitespace_or_control_rejected,
        test_nace_cache_key_by_base_url,
        test_other_backends_carry_no_attribution,
    ):
        fn()
    print("\nall Nace offline tests passed")
