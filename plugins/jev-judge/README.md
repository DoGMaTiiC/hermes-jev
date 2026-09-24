# jev-judge

[TypeSafe Jev](https://docs.typesafe.ai) (System One) as a judgment layer for
Hermes Agent: a **pre-tool gate** and a **jev_ask** tool. Jev answers typed
questions (boolean / choice / score) with probabilities — no prose, no parsing.

Adapted from [pi-jev](https://github.com/y0usaf/pi-jev) (Pi coding agent) to
Hermes hooks. Three routes, picked by the `backend` setting (`auto` by
default: `TYPESAFE_API_KEY` wins, else `AI_GATEWAY_API_KEY`, else `OPENROUTER_API_KEY`):

| Route             | Endpoint                                                          | Key                  | Questions                      | Confidence                             | Cost                            |
| ----------------- | ----------------------------------------------------------------- | -------------------- | ------------------------------ | -------------------------------------- | ------------------------------- |
| TypeSafe direto   | `POST https://api.typesafe.ai/v1/systemone` (`model: jev-latest`) | `TYPESAFE_API_KEY`   | `noul` / `choice` / `score`    | inline per answer                      | none (`usage` in tokens)        |
| Vercel AI Gateway | `POST {jev_base_url}/evaluation-model` (`typesafe-ai/jev`)        | `AI_GATEWAY_API_KEY` | `boolean` / `choice` / `score` | `providerMetadata.typesafe.confidence` | `providerMetadata.gateway.cost` |
| OpenRouter        | `POST {openrouter_base_url}/decisions` (`~typesafe/jev-latest`, alpha Decisions API) | `OPENROUTER_API_KEY` | `noul` / `choice` / `score`    | inline per answer (`confidence`)        | none (usage in tokens; billed to your OpenRouter key) |

Yes/no questions are `boolean` internally and mapped to `noul` on the
TypeSafe wire; answers come back normalized (`{probability}` /
`{choice, probabilities, confidence}` / `{score, probabilities, confidence}`).
Pure stdlib, no Node needed.

Exercitada ao vivo nos dois backends (2026-09-21, mesma bateria de 3 calls):

| Tool call                                          | TypeSafe direto                                              | Gateway                     |
| -------------------------------------------------- | ------------------------------------------------------------ | --------------------------- |
| `rm -rf ~/projetos/hermes-jev && git push --force` | destructive 0.95 · exfiltration 0.84 · impact 2.63 (1181 ms) | 0.95 · 0.83 · 2.60 (464 ms) |
| `ls -la ~/projetos/hermes-jev/plugins`             | clear (1010 ms)                                              | clear (411 ms)              |
| `curl -X POST … -d @~/.hermes/.env`                | exfiltration 0.97 · impact 2.98 (866 ms)                     | 0.97 · 2.98 (352 ms)        |

## Install

```bash
hermes plugins install DoGMaTiiC/hermes-jev/plugins/jev-judge
```

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

No key for the selected backend, timeout (3s), HTTP error, malformed body —
the gate returns nothing and the tool call proceeds exactly as today. Logic
is in `gate.judge()`; every question is atomic and thresholds are applied in
code, never by the model.

## Under rate limit

The gateway free tier limits per model; TypeSafe direto has no gateway
limiter. On 429/529 the client reads `Retry-After` (seconds or HTTP date;
garbage and non-finite values like `nan`/`inf` are ignored) and retries
**once** if the wait fits in `retry_max_wait_s` (2.0s) — never in a loop.
After `breaker_threshold` (3) consecutive 429/529s the endpoint goes silent
for `breaker_cooldown_s` (120s); any success resets the count. The breaker
counts one rate-limit event per call, even when the retry is limited too.
Outgoing calls are spaced `min_interval_s` (0.25s) apart per process, and
identical calls share one cached judgment for `cache_seconds` (300s).

Wall-clock budget: `timeout_s` bounds each attempt, so one call with a
retry can take up to ~2×`timeout_s` + `retry_max_wait_s` (~8s at defaults).

## Settings

`plugins.entries.jev-judge.settings` in `config.yaml`:

| Key                                    | Default                                  | Meaning                                                                                             |
| -------------------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `mode`                                 | `shadow`                                 | `shadow` = log only · `enforce` = escalate triggered calls                                          |
| `tools`                                | `[terminal, write_file, patch]`          | Tools the gate judges                                                                               |
| `timeout_s`                            | `3.0`                                    | Per-attempt timeout (worst case per call: 2×`timeout_s` + `retry_max_wait_s`)                       |
| `cache_seconds`                        | `300`                                    | Identical calls judged once per window                                                              |
| `backend`                              | `auto`                                   | `auto` = TypeSafe key, else gateway, else OpenRouter · `typesafe`/`gateway`/`openrouter` forces one |
| `typesafe_model` / `typesafe_base_url` | `jev-latest` / `https://api.typesafe.ai` | TypeSafe direto endpoint overrides                                                                  |
| `openrouter_model` / `openrouter_base_url` | `~typesafe/jev-latest` / `https://openrouter.ai/api/alpha` | OpenRouter endpoint overrides (decisions POST to `<base>/decisions`)                         |
| `retry_max_wait_s`                     | `2.0`                                    | Retry once on 429/529 only if Retry-After waits at most this                                        |
| `breaker_threshold`                    | `3`                                      | Consecutive 429/529s before going silent                                                            |
| `breaker_cooldown_s`                   | `120`                                    | Silence window after the breaker opens                                                              |
| `min_interval_s`                       | `0.25`                                   | Minimum gap between outgoing Jev calls, per process                                                 |
| `destructive_threshold`                | `0.90`                                   | P(true) that triggers                                                                               |
| `exfiltration_threshold`               | `0.70`                                   | P(true) that triggers                                                                               |
| `impact_threshold`                     | `2.5`                                    | Score that triggers (0–3 scale)                                                                     |
| `jev_model` / `jev_base_url`           | `typesafe-ai/jev` / AI Gateway           | Gateway endpoint overrides (prefixed: the loader rejects bare `model`/`base_url`, root is reserved) |
| `log_path`                             | `<HERMES_HOME>/logs/jev-judge.log`       | JSONL decision log                                                                                  |

Needs `TYPESAFE_API_KEY` (direct), `AI_GATEWAY_API_KEY` (Vercel AI
Gateway) and/or `OPENROUTER_API_KEY` (OpenRouter alpha Decisions API). No key
at all: the plugin loads and stays silent (fail-open).

## What leaves your machine

Per gated call: the tool name plus its arguments — long values truncated to
600 chars, key-shaped strings masked. Nothing else: no conversation history,
no files, no memory, no tool output. `jev_ask` sends exactly the state and
questions the model provides.
The destination is the selected backend: TypeSafe direct, the Vercel AI
Gateway, or OpenRouter's alpha Decisions API (billed to your OpenRouter key).

## Verify

```bash
python3 tests/test_offline.py          # offline logic + fail-open, no network
hermes plugins doctor . --ci           # Hermes loader contract
```

Decision log (one JSON line per judgment):

```bash
tail -f "${HERMES_HOME:-$HOME/.hermes}/logs/jev-judge.log"
```

## Prior art

Jev plugins for Hermes are a growing family — the catalog already lists
`hermes-jev`/Nerve (keeltrace), `jev-approvals` and `jev-curator` (anpicasso), four `jev-*`
routers (Pinutss), `jev` (ourines) and `jev-typesafe` (ajensenwaud). This plugin occupies the
narrowest slot in that family: a standalone `pre_tool_call` gate with shadow-by-default, its
own thresholds fixed in code, one JSONL line per decision, and fail-open on every error path.
It deliberately does **not** plug into Hermes' native smart-approval path (that is
`jev-approvals`' job) and it never fails closed.

## Related

- Upstream Hermes work in flight: provider credential listing, bundled skill
  routing plugin, computer-use decision lane (`gh search prs --repo
NousResearch/hermes-agent "jev OR typesafe"`). This plugin is the tool-gate
  counterpart; built to be publishable to the community catalog.
