# Router on the Nace (Drex) backend — 2026-11-09

Route: **Nace direct** (`backend: nace`, `NACE_API_KEY`, `POST https://console.nace.ai/v1/systemone`).
Two models on the **same seeded sample**: `drex-max-v1.1` (the plugin's `nace_model` default) and
`drex-latest` (which answered as `drex-v2.0`). 120 HTTP calls in total, 0 fail-open, about
$0.003 for both models together at $0.03 per million input tokens (`drex-max-v1.1` alone: 60,442
input tokens, about $0.002).

## Method

- Labelled set: `tools/calibration/labelled.json`, unchanged.
- Roster: the **44 bundled skills of a clean Hermes Agent v0.23.0 install** (`<HERMES_HOME>/skills`
  after the first start). 34 of the 72 covered labels exist in that roster. The rest are skipped,
  as `run_live.py` already does.
- Sample: `--sample 30 --seed 325` = 24 covered + 6 none-set requests, picked with
  `random.Random(325)` from the cases left after the roster filter. Both models ran on exactly
  these 30 requests.
- Pipeline: `tools/calibration/run_live.py` as shipped (request 1 = `rank_wide`, request 2 over the
  top 3, then `router.suggest` from cache). Shipped client defaults (`timeout_s` 4.0,
  thresholds gate 0.30 / fits 0.40). Raw rows: `tools/calibration/raw-nace-drex-max-v1.1-2026-11-09.jsonl`
  and `tools/calibration/raw-nace-drex-latest-2026-11-09.jsonl`. Each row lists every HTTP call with
  its status, latency, the `model` that answered, the `request_id` and input tokens. No keys or
  headers are stored. `run_live.py` writes `ts` in the machine's local offset; after the run the
  `ts` values in both files were rewritten to UTC (`Z`). Nothing else in the rows changed.
- Scores are an offline replay: `python3 tools/calibration/score.py <raw>.jsonl`.

Repro:

```bash
read -rs NACE_API_KEY && export NACE_API_KEY   # paste your own key; not echoed, not in history
python3 tools/calibration/run_live.py /tmp/raw-nace-repro.jsonl \
  --backend nace --model drex-max-v1.1 --roster "$HERMES_HOME/skills" --sample 30 --seed 325
python3 tools/calibration/score.py /tmp/raw-nace-repro.jsonl
```

Write to a new file. `run_live.py` appends, and it skips every request already in the output
file, so pointing it at the committed JSONL would skip all 30 cases.

**Not comparable with the 2026-09-21 run** (95%, 103-skill private roster, all 76 requests):
different roster, different sample size. The two models below are comparable with each other.

## Results at the shipped thresholds (gate 0.30 / fits 0.40)

| | `drex-max-v1.1` | `drex-latest` (`drex-v2.0`) |
| --- | --- | --- |
| top-1 on covered | **23/24 = 96%** | 10/24 = 42% |
| silent misses | 1/24 | 14/24 |
| wrong skill suggested | 0 | 0 |
| needless suggestions (none-set) | **0/6** | 0/6 |
| request 2 picked the labelled skill | 24/24 | 14/24 |
| gate below 0.30 on covered | 1/24 | 5/24 |
| mean gate, covered / none-set | 0.826 / 0.014 | 0.492 / 0.083 |
| fail-open (timeout / HTTP / network) | 0 (0 / 0 / 0) | 0 (0 / 0 / 0) |
| latency per call, p50 / p95 / max | 715 / 988 / 2422 ms | 642 / 796 / 833 ms |
| latency per decision (2 calls), p50 / p95 | 1473 / 1912 ms | 1332 / 1523 ms |
| calls over the 4 s `timeout_s` | 0 | 0 |
| input tokens per decision, mean | 2015 | 1779 |
| `model` reported | `drex-max-v1.1` | `drex-v2.0` |

The labelled skill was on the request-1 shortlist in 24/24 cases for both models. `drex-v2.0`
loses in two places, which overlap in one case: request 2 answers `none_of_these` (10 cases, 9
of them above the gate) and the gate drops below 0.30 (5 cases). `drex-max-v1.1`'s only miss is the Suno
songwriting request (gate 0.22). It was also a gate miss on 2026-09-21.

The `drex-max-v1.1` sweep is flat in `fits` (0.30–0.50). Lowering the gate to 0.20 recovers the
last case (24/24, 0 needless), but 30 requests are too few to move the shipped gate. `drex-latest`
stays at 38–46% across the whole grid, so a threshold change would not rescue it.

## Decision

`nace_model` defaults to **`drex-max-v1.1`**. Users who want Nace's moving alias can set
`nace_model: drex-latest`. Both models stayed far under the 4 s `timeout_s`, so the timeout
default is unchanged.
