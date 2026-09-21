"""Live runner for the #6 router calibration — shipped pipeline, raw rows to JSONL.

Per labelled request, in ONE process (so the client's 300 s cache makes step 3 free):

1. ``router.rank_wide``  — request 1 exactly as shipped (choice over the whole roster
   + the three gate booleans).
2. ``client.evaluate``   — request 2 over the shipped top-3 shortlist, always issued
   even below the gate: the sweep needs the door-2 answers for every request.
3. ``router.suggest``    — the shipped decision itself (same two calls, cache hits),
   so each row carries the exact CLI-equivalent skill/gate/probability/latency/cost.

Usage: python3 tools/calibration/run_live.py [raw-<date>.jsonl]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent / "plugins" / "jev-skill-router"
sys.path.insert(0, str(PLUGIN))

from jev_router import roster as roster_mod
from jev_router import router as router_mod
from jev_router.client import JevClient

LABELLED = HERE / "labelled.json"
ROSTER_DIR = Path.home() / ".hermes" / "skills"


def main() -> int:
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "raw-2026-09-21.jsonl"
    cases = json.loads(LABELLED.read_text(encoding="utf-8"))
    skills = roster_mod.load_roster([ROSTER_DIR])
    by_name = {s.name: s for s in skills}
    unknown = sorted(
        {c["expected"] for c in cases if c["expected"] and c["expected"] not in by_name}
    )
    if unknown:
        print(f"labels not in roster (skipped): {unknown}")
    cases = [c for c in cases if not c["expected"] or c["expected"] in by_name]

    done = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["request"])

    client = JevClient()  # shipped defaults: backend auto (typesafe key wins)
    print(f"{len(cases)} cases · {len(skills)} skills · backend={client._resolve()[0]}")
    for index, case in enumerate(cases, 1):
        request = case["request"]
        if request in done:
            print(f"[{index:2}/{len(cases)}] cached (already in {raw_path.name})")
            continue
        started = time.monotonic()
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
