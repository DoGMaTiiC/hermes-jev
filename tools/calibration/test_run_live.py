"""Offline tests for run_live's Recorder — no network, no keys.

Run: python3 tools/calibration/test_run_live.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
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


NACE = {"backend": "nace", "model_requested": "drex-max-v1.1", "roster": 44,
        "sample": 30, "seed": 325}


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_resume_matching_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raw.jsonl"
        assert run_live.resumable_requests(path, NACE) == set()  # no file yet
        write_rows(path, [{"request": "a", **NACE}, {"request": "b", **NACE}])
        assert run_live.resumable_requests(path, NACE) == {"a", "b"}
    print("ok  resume: rows with the same backend/model/roster are skipped")


def test_resume_refuses_other_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raw.jsonl"
        for other in ({**NACE, "backend": "typesafe"}, {**NACE, "model_requested": "drex-latest"},
                      {**NACE, "roster": 103}, {**NACE, "seed": 326}, {**NACE, "sample": None}):
            write_rows(path, [{"request": "a", **NACE}, {"request": "b", **other}])
            try:
                run_live.resumable_requests(path, NACE)
            except ValueError as exc:
                assert "line 2" in str(exc) and "new output path" in str(exc), exc
            else:
                raise AssertionError(f"resumed a file written with {other}")
    print("ok  resume: refuses a file written with another backend, model, roster, sample or seed")


def test_resume_refuses_unidentified_rows():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raw.jsonl"
        write_rows(path, [{"request": "a", "kind": "covered", "expected": "pdf"}])  # 2026-09-21 shape
        try:
            run_live.resumable_requests(path, NACE)
        except ValueError as exc:
            assert "does not record" in str(exc), exc
        else:
            raise AssertionError("resumed rows that do not record their settings")
    print("ok  resume: refuses rows that do not record backend/model/roster")


def test_resume_names_file_and_line_of_a_broken_row():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raw.jsonl"
        path.write_text(json.dumps({"request": "a", **NACE}) + '\n{"request": "b", "ki', encoding="utf-8")
        try:
            run_live.resumable_requests(path, NACE)
        except ValueError as exc:
            assert str(path) in str(exc) and "line 2 is not a JSON row" in str(exc), exc
        else:
            raise AssertionError("resumed a file with a cut-off row")
        path.write_bytes(b"\xff\xfe not utf-8\n")
        try:
            run_live.resumable_requests(path, NACE)
        except ValueError as exc:
            assert str(path) in str(exc) and "not UTF-8" in str(exc), exc
        else:
            raise AssertionError("resumed a file that is not UTF-8")
    print("ok  resume: a cut-off or non-UTF-8 file is refused with its path and line")


# --- main() end to end: fake transport, temp roster, three labelled cases ---------------

CLIENT = sys.modules["jev_router.client"]
BACKEND_KEYS = ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY", "NACE_API_KEY")
CASES = [
    {"kind": "covered", "expected": "pdf", "request": "merge two PDFs"},
    {"kind": "covered", "expected": "xlsx", "request": "sum column B of the sheet"},
    {"kind": "none", "expected": None, "request": "what is a moving average"},
]


@contextlib.contextmanager
def offline_run():
    """Only NACE_API_KEY set, empty HERMES_HOME, a counting transport that never connects.

    Yields (tmp dir, roster dir, calls list). Every call fails like a dropped network, so
    the router fails open and main() still writes one row per case.
    """
    calls: list[str] = []

    def transport(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.URLError("offline test")

    saved_env = {n: os.environ.get(n) for n in BACKEND_KEYS + ("HERMES_HOME",)}
    saved = (CLIENT._urlopen, run_live.LABELLED, sys.modules.get("agent"),
             sys.modules.get("agent.secret_scope"))
    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = lambda name, default=None: os.environ.get(name, default)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        roster = root / "skills"
        for name in ("pdf", "xlsx"):
            (roster / name).mkdir(parents=True)
            (roster / name / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name} files\n---\n\nBody.\n", encoding="utf-8"
            )
        labelled = root / "labelled.json"
        labelled.write_text(json.dumps(CASES), encoding="utf-8")
        try:
            for n in BACKEND_KEYS:
                os.environ.pop(n, None)
            os.environ["NACE_API_KEY"] = "nace_sk_offline_test_value"
            os.environ["HERMES_HOME"] = str(root / "home")
            sys.modules["agent"] = types.ModuleType("agent")
            sys.modules["agent.secret_scope"] = scope
            CLIENT._urlopen = transport
            CLIENT._PACE_LAST.clear()
            CLIENT._BREAKER.clear()
            run_live.LABELLED = labelled
            yield root, roster, calls
        finally:
            CLIENT._urlopen, run_live.LABELLED = saved[0], saved[1]
            for mod, value in (("agent", saved[2]), ("agent.secret_scope", saved[3])):
                if value is None:
                    sys.modules.pop(mod, None)
                else:
                    sys.modules[mod] = value
            for n, v in saved_env.items():
                if v is None:
                    os.environ.pop(n, None)
                else:
                    os.environ[n] = v


def run_main(argv: list[str]) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = run_live.main(argv)
    return code, err.getvalue()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_main_refuses_mismatched_file_before_any_call():
    with offline_run() as (root, roster, calls):
        path = root / "raw.jsonl"
        write_rows(path, [{"request": CASES[0]["request"], **NACE, "backend": "typesafe"}])
        before = path.read_text(encoding="utf-8")
        code, err = run_main([str(path), "--backend", "nace", "--model", "drex-max-v1.1",
                              "--roster", str(roster)])
        assert code == 2 and "refusing to resume" in err, (code, err)
        assert calls == [], calls
        assert path.read_text(encoding="utf-8") == before
    print("ok  main: a file from another run exits 2 before any HTTP call")


def test_main_no_flag_run_then_resumes_without_calls():
    with offline_run() as (root, roster, calls):
        path = root / "raw.jsonl"
        path.write_text("", encoding="utf-8")  # empty file: nothing to resume, run everything
        argv = [str(path), "--roster", str(roster)]  # no --backend/--model/--sample
        code, err = run_main(argv)
        assert code == 0, err
        assert calls, "the first run made no HTTP call"
        first = rows(path)
        assert [r["request"] for r in first] == [c["request"] for c in CASES], first
        want = {"backend": "nace", "model_requested": CLIENT.DEFAULT_NACE_MODEL, "roster": 2,
                "sample": None, "seed": None}
        assert all({k: r[k] for k in want if k in r} == want for r in first), first
        calls.clear()
        code, err = run_main(argv)
        assert code == 0, err
        assert calls == [], calls
        assert rows(path) == first
    print("ok  main: an empty file runs; a no-flag rerun resumes it with 0 new calls")


def test_main_refuses_another_seed():
    with offline_run() as (root, roster, calls):
        path = root / "raw.jsonl"
        base = [str(path), "--backend", "nace", "--roster", str(roster), "--sample", "3"]
        code, err = run_main(base + ["--seed", "325"])
        assert code == 0 and rows(path)[0]["seed"] == 325, err
        calls.clear()
        code, err = run_main(base + ["--seed", "326"])
        assert code == 2 and "'seed': 325" in err, (code, err)
        assert calls == [], calls
    print("ok  main: a --seed 326 run refuses a --seed 325 file before any HTTP call")


if __name__ == "__main__":
    for fn in (
        test_dump_written_and_redacted,
        test_unwritable_dump_keeps_success,
        test_unwritable_dump_keeps_http_error,
        test_model_goes_to_the_resolved_backend,
        test_resume_matching_config,
        test_resume_refuses_other_config,
        test_resume_refuses_unidentified_rows,
        test_resume_names_file_and_line_of_a_broken_row,
        test_main_refuses_mismatched_file_before_any_call,
        test_main_no_flag_run_then_resumes_without_calls,
        test_main_refuses_another_seed,
    ):
        fn()
    print("\nall run_live offline tests passed")
