# jev-skill-router

[TypeSafe Jev](https://docs.typesafe.ai) (System One) as a skill router for
Hermes Agent: before the model call, Jev names **at most one** skill from the
live roster for the current turn, and the plugin injects a single
`<skill_relevance>` line into the user-message context. It says nothing when
nothing fits. Via the [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/modalities/evaluation)
(`typesafe-ai/jev`) — pure stdlib, no Node, no SDK.

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
hermes plugins install DoGMaTiiC/hermes-jev-skill-router
hermes jev-skill-router auto   # or: on
```

Requires `AI_GATEWAY_API_KEY` (Vercel AI Gateway key) and Hermes ≥ 0.21.

## Settings

`plugins.entries.jev-skill-router.settings` in `config.yaml`:

| Key             | Default                                    | Meaning                                              |
| --------------- | ------------------------------------------ | ---------------------------------------------------- |
| `mode`          | `off`                                      | `off` = never · `auto` = only with key · `on` = always |
| `gate`          | `0.30`                                     | Mean of the 3 request judgments; below it, silence   |
| `fits`          | `0.40`                                     | Winner's own "does it fit" judgment; below it, silence |
| `shortlist`     | `3`                                        | Candidates carried from request 1 into request 2     |
| `chunk`         | `240`                                      | Skills per Choice question (API caps one at 255)     |
| `excerpt`       | `700`                                      | SKILL.md characters each candidate brings            |
| `timeout_s`     | `4.0`                                      | Wall-clock budget per Jev call (fail-open past it)   |
| `suggest_chars` | `4000`                                     | Longer user messages are left alone                  |
| `jev_model`     | `typesafe-ai/jev`                          | Endpoint override (prefixed: the loader rejects bare `model`) |
| `jev_base_url`  | `https://ai-gateway.vercel.sh/v4/ai`       | Endpoint override (prefixed: the loader rejects bare `base_url`) |
| `roster_dir`    | `<HERMES_HOME>/skills`                     | Where SKILL.md files are scanned                     |
| `log_path`      | `<HERMES_HOME>/logs/jev-skill-router.log`  | JSONL decision log                                   |

## What leaves your machine

Per eligible turn: the request text plus skill names and one-line
descriptions; for the 3 shortlisted candidates, the description plus the
first 700 characters of SKILL.md. Never: conversation history, files,
memory, or tool output.

## Cost / latency

About 2 Jev calls per eligible turn — roughly $0.00002 and ~1 s. Ineligible
turns (slash, empty, long, already routed) and `mode: off` cost nothing.

## Fail-open, always

Missing key, timeout, HTTP error, malformed body — the hook returns nothing
and the turn proceeds exactly as today.

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

## Verify

```bash
python3 tests/test_offline.py          # offline logic + fail-open, no network
hermes plugins validate .              # catalog admission gate
hermes plugins doctor . --ci           # Hermes loader contract
```

Decision log (one JSON line per decision):

```bash
tail -f "${HERMES_HOME:-$HOME/.hermes}/logs/jev-skill-router.log"
```
