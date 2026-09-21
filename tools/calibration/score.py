"""Score the #6 calibration: shipped decision + gate/fits sweep, replayed offline.

Reads the raw JSONL produced by run_live.py, so every number below is reproducible
after the fact without spending another call.

Usage: python3 tools/calibration/score.py [raw-<date>.jsonl]
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

GATE_GRID = [0.20, 0.25, 0.30, 0.35, 0.40]
FITS_GRID = [0.30, 0.35, 0.40, 0.45, 0.50]
SHIPPED = (0.30, 0.40)
NONE_OPTION = "none_of_these"


def decide(row: dict, gate_t: float, fits_t: float) -> str | None:
    """Replay the shipped threshold logic over a recorded row."""
    if row.get("fail_open"):
        return None
    call2 = row.get("call2") or {}
    choice = call2.get("choice")
    if row["gate"] < gate_t:
        return None
    if (
        not choice
        or choice == NONE_OPTION
        or choice not in (row.get("shortlist") or [])
    ):
        return None
    fits = (call2.get("fits") or {}).get(choice)
    if fits is None or float(fits) < fits_t:
        return None
    return choice


def rates(rows: list[dict], gate_t: float, fits_t: float) -> dict:
    covered = [r for r in rows if r["kind"] == "covered" and not r.get("fail_open")]
    none_set = [r for r in rows if r["kind"] == "none" and not r.get("fail_open")]
    hits = 0
    wrong = []
    for row in covered:
        picked = decide(row, gate_t, fits_t)
        if picked == row["expected"]:
            hits += 1
        elif picked:
            wrong.append((row["request"], row["expected"], picked))
    missed = sum(1 for r in covered if decide(r, gate_t, fits_t) is None)
    needless = sum(1 for r in none_set if decide(r, gate_t, fits_t) is not None)
    return {
        "acc": hits / len(covered) if covered else 0.0,
        "hits": hits,
        "covered": len(covered),
        "missed": missed,
        "missed_rate": missed / len(covered) if covered else 0.0,
        "needless": needless,
        "none": len(none_set),
        "needless_rate": needless / len(none_set) if none_set else 0.0,
        "wrong": wrong,
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "raw-2026-09-21.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"# Router calibration — {len(rows)} requests ({path.name})\n")
    failed = [r for r in rows if r.get("fail_open")]
    print(f"fail-open rows: {len(failed)}")

    base = rates(rows, *SHIPPED)
    print(
        f"\n## Shipped thresholds (gate {SHIPPED[0]:.2f} / fits {SHIPPED[1]:.2f})\n\n"
        f"- top-1 em cobertos: **{base['hits']}/{base['covered']} = {base['acc']:.0%}**\n"
        f"- silêncio indevido (missed): {base['missed']}/{base['covered']} = {base['missed_rate']:.0%}\n"
        f"- sugestão indevida (none-set): {base['needless']}/{base['none']} = {base['needless_rate']:.0%}"
    )
    if base["wrong"]:
        print("\n### Erros (sugeriu outra skill)\n")
        for request, expected, picked in base["wrong"]:
            print(f"- `{expected}` → `{picked}` — {request}")
    covered = [r for r in rows if r["kind"] == "covered" and not r.get("fail_open")]
    silent = [
        (r["expected"], r["gate"], r["request"])
        for r in covered
        if decide(r, *SHIPPED) is None
    ]
    if silent:
        print(
            "\n### Silêncios indevidos (missed) — cobertos que não receberam sugestão\n"
        )
        for expected, gate, request in sorted(silent, key=lambda s: s[1]):
            row = next(r for r in covered if r["request"] == request)
            choice = (row.get("call2") or {}).get("choice")
            print(
                f"- `{expected}` (gate {gate:.2f}, escolha da porta 2: `{choice}`) — {request}"
            )

    print("\n## Sweep gate × fits (acc cobertos · missed · needless)\n")
    header = "| gate \\ fits | " + " | ".join(f"{f:.2f}" for f in FITS_GRID) + " |"
    print(header)
    print("| --- | " + " | ".join("---" for _ in FITS_GRID) + " |")
    for gate_t in GATE_GRID:
        cells = []
        for fits_t in FITS_GRID:
            r = rates(rows, gate_t, fits_t)
            mark = "*" if (gate_t, fits_t) == SHIPPED else ""
            cells.append(f"{r['acc']:.0%}·{r['missed']}·{r['needless']}{mark}")
        print(f"| {gate_t:.2f} | " + " | ".join(cells) + " |")
    print(
        "\n(* = configuração shipada; célula = acurácia top-1 · quantos silêncios · quantas sugestões no none-set)"
    )

    shipped_rows = [r["shipped"] for r in rows if r.get("shipped")]
    if shipped_rows:
        lat = sorted(r["latency_ms"] or 0 for r in shipped_rows)
        print(
            f"\n## Latência/custo (linhas com decisão)\n\n"
            f"- latência por decisão: p50 {statistics.median(lat):.0f} ms · max {max(lat)} ms\n"
            f"- custo por decisão: {sorted({str(r['cost']) for r in shipped_rows})}"
        )
    gates = {
        kind: [r["gate"] for r in rows if r["kind"] == kind and not r.get("fail_open")]
        for kind in ("covered", "none")
    }
    print(
        f"- gate médio: cobertos {statistics.mean(gates['covered']):.3f} · "
        f"none-set {statistics.mean(gates['none']):.3f}"
    )
    print(
        f"- gate cobertos < 0.30: {sum(1 for g in gates['covered'] if g < 0.30)}/{len(gates['covered'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
