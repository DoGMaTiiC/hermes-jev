"""Live runner for the #6 router calibration — shipped pipeline, raw rows to JSONL.

Per labelled request, in ONE process (so the client's 300 s cache makes step 3 free):

1. ``router.rank_wide``  — request 1 exactly as shipped (choice over the whole roster
   + the three gate booleans).
2. ``client.evaluate``   — request 2 over the shipped top-3 shortlist, always issued
   even below the gate: the sweep needs the door-2 answers for every request.
3. ``router.suggest``    — the shipped decision itself (same two calls, cache hits),
   so each row carries the exact CLI-equivalent skill/gate/probability/latency/cost.

Usage: python3 tools/calibration/run_live.py [raw-<date>.jsonl]
       [--backend auto|typesafe|openrouter|gateway|nace] [--model ID]
       [--roster DIR] [--sample N --seed S] [--dump-first PATH]

With no flags it behaves as before (shipped client defaults, ~/.hermes/skills, every
labelled case). ``--sample N`` keeps the covered:none ratio of 24:6 per 30 cases,
picked with ``random.Random(seed)`` so two models can run on the same sample.
Each row also records, per HTTP call, the status, latency, error kind and, when the
provider returns them, ``model``, ``request_id`` and input tokens. Keys and request
headers are never written to the JSONL. ``--dump-first`` writes the first request and
response to PATH with the Authorization value redacted.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent / "plugins" / "jev-skill-router"
sys.path.insert(0, str(PLUGIN))

from jev_router import roster as roster_mod
from jev_router import router as router_mod
from jev_router import client as client_mod
from jev_router.client import JevClient

LABELLED = HERE / "labelled.json"
ROSTER_DIR = Path.home() / ".hermes" / "skills"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("raw", nargs="?", default=str(HERE / "raw-2026-09-21.jsonl"))
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--model", default=None, help="model for the chosen backend")
    parser.add_argument("--roster", default=str(ROSTER_DIR))
    parser.add_argument("--sample", type=int, default=0, help="0 = every labelled case")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dump-first", default=None, help="write the first request/response here")
    return parser.parse_args(argv)


def sample_cases(cases: list[dict], size: int, seed: int) -> list[dict]:
    """Same seed + same roster = same sample, whatever the backend or model."""
    if size <= 0:
        return cases
    covered = [c for c in cases if c["kind"] == "covered"]
    none_set = [c for c in cases if c["kind"] == "none"]
    want_none = min(len(none_set), round(size * 6 / 30))
    rng = random.Random(seed)
    picked = rng.sample(covered, min(len(covered), size - want_none))
    picked += rng.sample(none_set, want_none)
    return sorted(picked, key=lambda c: cases.index(c))


def build_client(args: argparse.Namespace) -> JevClient:
    kw = {"backend": args.backend}
    if args.model:
        field = {"typesafe": "typesafe_model", "openrouter": "openrouter_model",
                 "nace": "nace_model"}.get(args.backend.strip().lower(), "model")
        kw[field] = args.model
    return JevClient(**kw)


class Recorder:
    """Wraps the client's transport: per-call status, latency, ids. Never stores keys."""

    def __init__(self, client: JevClient, dump_path: str | None):
        self.inner = client._urlopen
        self.calls: list[dict] = []
        self.dump_path = Path(dump_path) if dump_path else None
        client._urlopen = self

    def __call__(self, req, timeout=None):
        started = time.monotonic()
        call: dict = {}
        try:
            resp = self.inner(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            call.update(status=exc.code, error="http")
            body = exc.read() if exc.fp else b""
            self._dump(req, exc.code, exc.headers, body)
            raise
        except (socket.timeout, TimeoutError) as exc:
            call.update(status=None, error="timeout")
            raise
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            timed_out = isinstance(reason, (socket.timeout, TimeoutError))
            call.update(status=None, error="timeout" if timed_out else "network")
            raise
        finally:
            call["ms"] = round((time.monotonic() - started) * 1000)
            self.calls.append(call)
        raw = resp.read()
        call["status"] = getattr(resp, "status", 200)
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if isinstance(body, dict):
            for field in ("model", "request_id"):
                if isinstance(body.get(field), str):
                    call[field] = body[field]
            usage = body.get("usage")
            if isinstance(usage, dict) and "input_tokens" in usage:
                call["input_tokens"] = usage["input_tokens"]
        self._dump(req, call["status"], resp.headers, raw)
        return _Replay(raw)

    def _dump(self, req, status, headers, raw: bytes) -> None:
        if not self.dump_path or self.dump_path.exists():
            return
        sent = {k: v for k, v in req.header_items()}
        for name in list(sent):
            if name.lower() == "authorization":
                sent[name] = sent[name].split(" ", 1)[0] + " <redacted>"
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw[:2000].decode("utf-8", "replace")
        record = {
            "request": {"method": req.get_method(), "url": req.full_url, "headers": sent,
                        "body": json.loads(req.data)},
            "response": {"status": status,
                         "x-request-id": (headers or {}).get("x-request-id"),
                         "body": parsed},
        }
        self.dump_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


class _Replay:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    raw_path = Path(args.raw)
    cases = json.loads(LABELLED.read_text(encoding="utf-8"))
    skills = roster_mod.load_roster([Path(args.roster).expanduser()])
    by_name = {s.name: s for s in skills}
    unknown = sorted(
        {c["expected"] for c in cases if c["expected"] and c["expected"] not in by_name}
    )
    if unknown:
        print(f"labels not in roster (skipped): {unknown}")
    cases = [c for c in cases if not c["expected"] or c["expected"] in by_name]
    cases = sample_cases(cases, args.sample, args.seed)

    done = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["request"])

    client = build_client(args)  # no flags: shipped defaults, backend auto
    recorder = Recorder(client, args.dump_first)
    backend = client._resolve()[0]
    model = {"typesafe": client.typesafe_model, "openrouter": client.openrouter_model,
             "gateway": client.model, "nace": client.nace_model}.get(backend or "")
    print(f"{len(cases)} cases · {len(skills)} skills · backend={backend} · model={model}")
    for index, case in enumerate(cases, 1):
        request = case["request"]
        if request in done:
            print(f"[{index:2}/{len(cases)}] cached (already in {raw_path.name})")
            continue
        started = time.monotonic()
        recorder.calls.clear()
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "request": request,
            "kind": case["kind"],
            "expected": case["expected"],
        }
        ranked = router_mod.rank_wide(client, request, skills)
        if ranked is None:
            row["fail_open"] = True
        else:
            row.update(gate=ranked["gate"], shortlist=ranked["shortlist"])
            names = ranked["shortlist"]
            if names:
                answers = client.evaluate(
                    router_mod.document(request),
                    router_mod.rerank_questions(names, by_name),
                )
                if answers:
                    which = answers["answers"].get("which") or {}
                    row["call2"] = {
                        "choice": which.get("choice"),
                        "probabilities": which.get("probabilities"),
                        "confidence": (answers.get("confidence") or {}).get("which"),
                        "fits": {
                            n: (answers["answers"].get(f"fits::{n}") or {}).get(
                                "probability"
                            )
                            for n in names
                        },
                    }
            shipped = router_mod.suggest(client, request, skills)
            row["shipped"] = (
                None
                if shipped is None
                else {
                    "skill": shipped.skill,
                    "gate": shipped.gate,
                    "probability": shipped.probability,
                    "confidence": shipped.confidence,
                    "latency_ms": shipped.latency_ms,
                    "cost": shipped.cost,
                    "calls": shipped.calls,
                }
            )
        row["wall_ms"] = round((time.monotonic() - started) * 1000)
        if args.backend != "auto" or args.model or args.sample:
            row.update(backend=backend, model_requested=model, roster=len(skills),
                       calls=list(recorder.calls))
        with raw_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        verdict = (
            "fail-open"
            if row.get("fail_open")
            else (
                f"{(row.get('shipped') or {}).get('skill')} "
                f"(gate {row['gate']:.2f}, p {(row.get('shipped') or {}).get('probability')})"
                if row.get("shipped")
                else f"silence (gate {row['gate']:.2f})"
            )
        )
        print(
            f"[{index:2}/{len(cases)}] {verdict} · {row['wall_ms']} ms · {request[:46]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
