# jev-judge

[TypeSafe Jev](https://docs.typesafe.ai) (System One) as a judgment layer for
Hermes Agent: a **pre-tool gate** and a **jev_ask** tool. Jev answers typed
questions (boolean / choice / score) with probabilities — no prose, no parsing.

Adapted from [pi-jev](https://github.com/y0usaf/pi-jev) (Pi coding agent) to
Hermes hooks. Via the [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/modalities/evaluation)
(`typesafe-ai/jev`, $0.04/M input, output free) — pure stdlib, no Node needed.

## What it does

**Gate** (`pre_tool_call`, shadow by default): before `terminal`, `write_file`
and `patch` run, one Jev request judges them — _destructive? exfiltration?
impact if unwanted?_ Every decision lands in a JSONL log.

| Mode               | On a triggered call                                            |
| ------------------ | -------------------------------------------------------------- |
| `shadow` (default) | nothing happens; the decision is logged                        |
| `enforce`          | escalates to Hermes' human-approval gate (`approve` directive) |

**Tool** (`jev_ask`): the model can ask for a typed judgment itself — e.g.
`{"urgent": {"type": "boolean", "instructions": "Does this convey urgency?"}}`.

## Fail-open, always

No key, timeout (3s), HTTP 429/5xx, malformed body — the gate returns nothing
and the tool call proceeds exactly as today. Logic is in `gate.judge()`; every
question is atomic and thresholds are applied in code, never by the model.

## Settings

`plugins.entries.jev-judge.settings` in `config.yaml`:

| Key                      | Default                            | Meaning                                                    |
| ------------------------ | ---------------------------------- | ---------------------------------------------------------- |
| `mode`                   | `shadow`                           | `shadow` = log only · `enforce` = escalate triggered calls |
| `tools`                  | `[terminal, write_file, patch]`    | Tools the gate judges                                      |
| `timeout_s`              | `3.0`                              | Wall-clock budget per Jev call                             |
| `cache_seconds`          | `120`                              | Identical calls judged once per window                     |
| `destructive_threshold`  | `0.90`                             | P(true) that triggers                                      |
| `exfiltration_threshold` | `0.70`                             | P(true) that triggers                                      |
| `impact_threshold`       | `2.5`                              | Score that triggers (0–3 scale)                            |
| `jev_model` / `jev_base_url` | `typesafe-ai/jev` / AI Gateway | Endpoint overrides (prefixed: the loader rejects bare `model`/`base_url`, root is reserved) |
| `log_path`               | `<HERMES_HOME>/logs/jev-judge.log` | JSONL decision log                                         |

Requires `AI_GATEWAY_API_KEY` (Vercel AI Gateway). Fail-open without it.

## What leaves your machine

Per gated call: the tool name plus its arguments — long values truncated to
600 chars, key-shaped strings masked. Nothing else: no conversation history,
no files, no memory, no tool output. `jev_ask` sends exactly the state and
questions the model provides.

## Verify

```bash
python3 tests/test_offline.py          # offline logic + fail-open, no network
hermes plugins doctor . --ci           # Hermes loader contract
```

Decision log (one JSON line per judgment):

```bash
tail -f "${HERMES_HOME:-$HOME/.hermes}/logs/jev-judge.log"
```

## Related

- Upstream Hermes work in flight: provider credential listing, bundled skill
  routing plugin, computer-use decision lane (`gh search prs --repo
NousResearch/hermes-agent "jev OR typesafe"`). This plugin is the tool-gate
  counterpart; built to be publishable to the community catalog.
