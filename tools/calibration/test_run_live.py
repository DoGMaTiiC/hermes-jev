"""Offline tests for run_live's Recorder — no network, no keys.

Run: python3 tools/calibration/test_run_live.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("run_live", HERE / "run_live.py")
run_live = importlib.util.module_from_spec(spec)
sys.modules["run_live"] = run_live
spec.loader.exec_module(run_live)

BODY = {"answers": {}, "model": "drex-max-v1.1", "request_id": "req_test", "usage": {"input_tokens": 7}}


class FakeResp:
    status = 200
    headers = {"x-request-id": "req_test"}

    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw


class FakeClient:
    def __init__(self, step):
        self.step = step
        self._urlopen = self.transport

    def transport(self, req, timeout=None):
        if isinstance(self.step, Exception):
            raise self.step
        return FakeResp(self.step)


def request():
    return urllib.request.Request(
        "https://x.invalid/v1/systemone",
        data=json.dumps({"questions": {}}).encode(),
        headers={"Authorization": "Bearer secret-value", "Content-Type": "application/json"},
    )


def http_429():
    return urllib.error.HTTPError(
        "https://x.invalid/v1/systemone", 429, "busy", {"retry-after": "1"}, io.BytesIO(b"{}")
    )


def test_dump_written_and_redacted():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dump.json"
        rec = run_live.Recorder(FakeClient(BODY), str(path))
        assert json.loads(rec(request()).read()) == BODY
        dumped = json.loads(path.read_text())
        assert dumped["response"]["status"] == 200, dumped
        assert dumped["request"]["headers"]["Authorization"] == "Bearer <redacted>", dumped
        assert "secret-value" not in path.read_text()
    print("ok  --dump-first writes the first call with Authorization redacted")


def test_unwritable_dump_keeps_success():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing-dir" / "dump.json"
        rec = run_live.Recorder(FakeClient(BODY), str(path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            resp = rec(request())
            again = rec(request())
        assert json.loads(resp.read()) == BODY and json.loads(again.read()) == BODY
        assert [c["status"] for c in rec.calls] == [200, 200], rec.calls
        assert rec.calls[0]["request_id"] == "req_test", rec.calls
        assert err.getvalue().count("--dump-first: could not write") == 1, err.getvalue()
        assert not path.exists()
    print("ok  unwritable --dump-first: 200 still returned, one warning on stderr")


def test_unwritable_dump_keeps_http_error():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing-dir" / "dump.json"
        rec = run_live.Recorder(FakeClient(http_429()), str(path))
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                rec(request())
            except urllib.error.HTTPError as exc:
                assert exc.code == 429, exc.code  # the client's 429 retry path still sees it
            else:
                raise AssertionError("HTTPError was swallowed")
        assert rec.calls == [{"status": 429, "error": "http", "ms": rec.calls[0]["ms"]}], rec.calls
    print("ok  unwritable --dump-first: HTTP 429 still re-raised as HTTPError")


def test_model_goes_to_the_resolved_backend():
    import argparse
    real = run_live.resolve_backend
    try:
        for picked, field in (("nace", "nace_model"), ("openrouter", "openrouter_model"),
                              ("typesafe", "typesafe_model"), ("gateway", "model"), (None, "model")):
            run_live.resolve_backend = lambda backend, _p=picked: _p
            client = run_live.build_client(argparse.Namespace(backend="auto", model="m-x"))
            assert getattr(client, field) == "m-x", (picked, field, vars(client))
    finally:
        run_live.resolve_backend = real
    print("ok  --backend auto: --model goes to the model field of the backend auto picks")


if __name__ == "__main__":
    for fn in (
        test_dump_written_and_redacted,
        test_unwritable_dump_keeps_success,
        test_unwritable_dump_keeps_http_error,
        test_model_goes_to_the_resolved_backend,
    ):
        fn()
    print("\nall run_live offline tests passed")
