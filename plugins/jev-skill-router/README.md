# jev-skill-router

[TypeSafe Jev](https://docs.typesafe.ai) (System One) as a skill router for
Hermes Agent: before the model call, Jev names **at most one** skill from the
live roster for the current turn, and the plugin injects a single
`<skill_relevance>` line into the user-message context. It says nothing when
nothing fits. Four routes, picked by the `backend` setting (`auto` by
default: `TYPESAFE_API_KEY` wins, else `OPENROUTER_API_KEY`, else
`AI_GATEWAY_API_KEY`, else `NACE_API_KEY`) — pure stdlib, no Node, no SDK.

| Route             | Endpoint                                                          | Key                  | Questions                      | Confidence                             | Cost                            |
| ----------------- | ----------------------------------------------------------------- | -------------------- | ------------------------------ | -------------------------------------- | ------------------------------- |
| TypeSafe direto   | `POST https://api.typesafe.ai/v1/systemone` (`model: jev-latest`) | `TYPESAFE_API_KEY`   | `noul` / `choice` / `score`    | inline per answer                      | none (`usage` in tokens)        |
| OpenRouter        | `POST https://openrouter.ai/api/alpha/decisions` (`~typesafe/jev-latest`) | `OPENROUTER_API_KEY` | `noul` / `choice` / `score` | inline per answer                      | provider billing / usage        |
| Vercel AI Gateway | `POST {jev_base_url}/evaluation-model` (`typesafe-ai/jev`)        | `AI_GATEWAY_API_KEY` | `boolean` / `choice` / `score` | `providerMetadata.typesafe.confidence` | `providerMetadata.gateway.cost` |
| Nace (Drex)       | `POST https://console.nace.ai/v1/systemone` (`model: drex-max-v1.1`) | `NACE_API_KEY`    | `noul` / `choice` / `score`    | inline per answer                      | none (`usage` in tokens)        |

Yes/no questions are `boolean` internally and mapped to `noul` on the
TypeSafe wire; answers come back normalized.

Nace Drex speaks the same System One wire format, so the Nace route reuses the
TypeSafe request and normalization. It adds its own key, model default and base
URL, sends `X-Nace-Client: hermes-jev-skill-router/<version>` (so Nace can
attribute the traffic) and `X-Nace-Legend: object`, and has its own breaker
and pacing slot. On this route, `suggested` decision log lines (and
`suggest --json`) also carry the `model` that answered and Nace's
`request_id`; silent lines do not. `auto` reaches Nace only when no TypeSafe,
OpenRouter or gateway credential exists, Hermes' OpenRouter credential pool
included, so set `backend: nace` to use it.

Exercitada ao vivo em 2026-09-21 nos dois backends da época (TypeSafe direto
e gateway, mesmo state/perguntas) — a rota OpenRouter veio depois (#16):
TypeSafe direto — `suggest` → `xlsx`, gate 0.57, p 1.00, 1431 ms, `cost: null`,
usage em tokens; gateway — 732 ms, `cost: 0`. Respostas normalizadas iguais
(`noul`→`{probability}`, confiança inline 1.0/0.83 vs 1/0.81).

## What it does

**Hook** (`pre_llm_call`, opt-in): two Jev requests per eligible turn.
Request 1 ranks the whole roster with one Choice plus three gate booleans
(does this turn want a skill at all — the third one inverted). Request 2
re-reads the top three with each candidate's SKILL.md excerpt plus one
absolute `fits` judgment per candidate. Two thresholds, at most one skill
name back. The model stays in charge: the line says to ignore it when it
does not fit.

Rosters above the API's 255-choice cap are chunked (240 per chunk, each with
a `none_of_these` option). Slash commands, empty messages, long pastes and
already-routed turns are left alone.

## Install

```bash
hermes plugins install DoGMaTiiC/hermes-jev/plugins/jev-skill-router
hermes jev-skill-router auto   # or: on
```

Requires one of `TYPESAFE_API_KEY` (direct), `OPENROUTER_API_KEY` (OpenRouter
Decisions API), `AI_GATEWAY_API_KEY` (Vercel AI Gateway key), or
`NACE_API_KEY` (Nace Drex, a `nace_sk_…` key from console.nace.ai) and Hermes ≥
0.21. The OpenRouter route also checks Hermes' OpenRouter credential pool when
`OPENROUTER_API_KEY` is not set. No key at all: the plugin loads and stays silent.

Nace setup: open the `.env` file of the Hermes profile you use and add the line
`NACE_API_KEY=nace_sk_...` on its own line. `hermes config env-path` prints that file;
for a named profile, add `-p <profile>` after `hermes` in each command below. Typing the
key into an editor keeps it out of your shell history.

```bash
"${EDITOR:-nano}" "$(hermes config env-path)"   # add NACE_API_KEY=nace_sk_... and save
hermes config set plugins.entries.jev-skill-router.settings.backend nace   # recommended over auto
hermes jev-skill-router on
hermes jev-skill-router suggest "merge two PDFs and fill in the form" --json   # shows model + request_id
```

`NACE_API_KEY` is read through Hermes' scoped secrets
(`agent.secret_scope.get_secret`), and from the process environment when the
plugin runs outside Hermes. It is sent only in the `Authorization` header;
a key with inner whitespace or control characters is ignored (the route stays silent).

## Settings

`plugins.entries.jev-skill-router.settings` in `config.yaml`:

| Key                  | Default                                   | Meaning                                                                                                        |
| -------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `mode`               | `off`                                     | `off` = never · `auto` = only with key · `on` = always                                                         |
| `gate`               | `0.30`                                    | Mean of the 3 request judgments; below it, silence                                                             |
| `fits`               | `0.40`                                    | Winner's own "does it fit" judgment; below it, silence                                                         |
| `shortlist`          | `3`                                       | Candidates carried from request 1 into request 2                                                               |
| `chunk`              | `240`                                     | Skills per Choice question (API caps one at 255)                                                               |
| `excerpt`            | `700`                                     | SKILL.md characters each candidate brings                                                                      |
| `timeout_s`          | `4.0`                                     | Per-attempt timeout (worst case per call: 2×`timeout_s` + `retry_max_wait_s`)                                  |
| `cache_seconds`      | `300`                                     | Identical calls answered from cache per window                                                                 |
| `backend`            | `auto`                                    | `auto` = TypeSafe key wins, else OpenRouter, else gateway, else Nace · `typesafe`/`openrouter`/`gateway`/`nace` forces one |
| `typesafe_model`     | `jev-latest`                              | TypeSafe direto model                                                                                          |
| `typesafe_base_url`  | `https://api.typesafe.ai`                 | TypeSafe direto endpoint override                                                                              |
| `openrouter_model`   | `~typesafe/jev-latest`                    | OpenRouter Decisions API model                                                                                 |
| `openrouter_base_url`| `https://openrouter.ai/api/alpha`         | OpenRouter Decisions API base URL                                                                              |
| `nace_model`         | `drex-max-v1.1`                           | Nace Drex model (`drex-latest` follows Nace's alias; see `docs/calibration/router-nace-2026-11-09.md`)          |
| `nace_base_url`      | `https://console.nace.ai`                 | Nace endpoint override. Must be `https://` (`http://` only for `localhost`, `127.0.0.1`, `::1`); any other URL makes the Nace route silent and the key is never sent |
| `retry_max_wait_s`   | `2.0`                                     | Retry once on 429/529 only if Retry-After waits at most this                                                   |
| `breaker_threshold`  | `3`                                       | Consecutive 429/529s before going silent                                                                       |
| `breaker_cooldown_s` | `120`                                     | Silence window after the breaker opens                                                                         |
| `min_interval_s`     | `0.25`                                    | Minimum gap between outgoing Jev calls, per process                                                            |
| `suggest_chars`      | `4000`                                    | Longer user messages are left alone                                                                            |
| `jev_model`          | `typesafe-ai/jev`                         | Gateway override (prefixed: the loader rejects bare `model`)                                                   |
| `jev_base_url`       | `https://ai-gateway.vercel.sh/v4/ai`      | Endpoint override (bare `base_url` is inert — reserved roots are only `model`/`plugins`/`security`/`settings`) |
| `roster_dir`         | `<HERMES_HOME>/skills`                    | Where SKILL.md files are scanned                                                                               |
| `log_path`           | `<HERMES_HOME>/logs/jev-skill-router.log` | JSONL decision log                                                                                             |

## What leaves your machine

Per eligible turn: the request text plus skill names and one-line
descriptions; for the 3 shortlisted candidates, the description plus the
first 700 characters of SKILL.md. Never: conversation history, files,
memory, or tool output.

It goes only to the backend you selected: `api.typesafe.ai` (TypeSafe direto),
`openrouter.ai` (OpenRouter), the Vercel AI Gateway, or `console.nace.ai`
(Nace Drex, with the `X-Nace-Client` header naming this plugin and its version).

Hard limits (#19), same as the judge: a payload above 65536 bytes is
refused **without sending**; 3xx is never followed and proxy env vars are
ignored.

## Cost / latency

About 2 Jev calls per eligible turn — roughly $0.00002 on the gateway
(TypeSafe direto bills in tokens, no per-call $) and ~1 s. On Nace, Drex
bills input tokens ($0.03 per million on 2026-11-09): about 2,000 tokens per
decision on the 44-skill bundled roster, ~$0.00006, ~1.5 s for both calls. Ineligible
turns (slash, empty, long, already routed) and `mode: off` cost nothing.

## Calibration (measured, not guessed)

Thresholds were calibrated on this machine's live roster — **76 labelled requests**
(66 covered by exactly one skill, 10 covered by none), shipped pipeline, TypeSafe
direto: **top-1 95%**, **needless 0/10**, and the second door picked the right skill
**66/66** — every loss came from the gate. Sweep over gate 0.20–0.40 × fits 0.30–0.50
puts the shipped pair at the knee (gate 0.25–0.30, any fits in 0.30–0.45 flat).
Full tables, misses and repro commands: [`docs/calibration/router-roster-2026-09-21.md`](../../docs/calibration/router-roster-2026-09-21.md).

Nace route, 2026-11-09, 30-request seeded sample on the 44 bundled skills of a
clean Hermes v0.23.0 (not comparable with the numbers above): `drex-max-v1.1`
top-1 **23/24**, needless 0/6; `drex-latest` 10/24, needless 0/6; 0 fail-open on
either. See [`docs/calibration/router-nace-2026-11-09.md`](../../docs/calibration/router-nace-2026-11-09.md).

## Fail-open, always

Missing key for the selected backend, timeout, HTTP error, malformed body —
the hook returns nothing and the turn proceeds exactly as today.

## Under rate limit

The gateway free tier limits per model; TypeSafe direto has no gateway
limiter. On 429/529 the client reads `Retry-After` (seconds or HTTP date;
garbage and non-finite values like `nan`/`inf` are ignored) and retries
**once** if the wait fits in `retry_max_wait_s` (2.0s) — never in a loop.
After `breaker_threshold` (3) consecutive 429/529s the endpoint goes silent
for `breaker_cooldown_s` (120s); any success resets the count. The breaker
counts one rate-limit event per call, even when the retry is limited too.
Outgoing calls are spaced `min_interval_s` (0.25s) apart per process, and
identical calls share one cached answer for `cache_seconds` (300s).

Wall-clock budget: `timeout_s` bounds each attempt, so one call with a
retry can take up to ~2×`timeout_s` + `retry_max_wait_s` (~10s at defaults).

## Off switch

```bash
hermes jev-skill-router off
```

## Commands

```bash
hermes jev-skill-router on|off|auto
hermes jev-skill-router status
hermes jev-skill-router suggest "deploy the site" [--json]
hermes jev-skill-router check
```

## Prior art

Skill routing is an established slot in the catalog, and it came first: **`typesafe-skill-router`**
(DECRUX9812, listed 2026-09-16) and **`skill-router`** (xXLODXx, listed 2026-09-15). This plugin
follows the same two-stage design — gate booleans over the roster, then a re-read of the shortlist,
one `<skill_relevance>` line, silence when nothing fits — and the same defaults (`gate 0.30`,
`fits 0.40`, `shortlist 3`, `chunk 240`, `excerpt 700`, `suggest_chars 4000`). It is a rebuild
with three differences: a **dual backend** (TypeSafe direct or Vercel AI Gateway, `backend: auto`),
**rate-limit hardening** (Retry-After retry, breaker, per-process pacing, TTL cache) and a
**published calibration** on a labelled request set (`docs/calibration/`). MIT, like the rest of
the family.

The wider Jev family in the catalog — Nerve (`hermes-jev`) by keeltrace, `jev-approvals` and
`jev-curator` by anpicasso, Pinutss' four `jev-*` routers, `jev` by ourines, `jev-typesafe` by
ajensenwaud — is credited where its ideas were borrowed.

## Verify

```bash
python3 tests/test_offline.py          # offline logic + fail-open, no network
python3 tests/test_offline_backlog.py
python3 tests/test_offline_nace.py     # Nace backend, stubbed transport
hermes plugins validate .              # catalog admission gate
hermes plugins doctor . --ci           # Hermes loader contract
```

Decision log (one JSON line per decision):

```bash
tail -f "${HERMES_HOME:-$HOME/.hermes}/logs/jev-skill-router.log"
```
