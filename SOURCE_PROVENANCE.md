# De onde vem cada peça

Reviewer do catálogo: este arquivo diz de onde veio cada peça auditada
(contrato em `contracts/tool-gate-v1.json`, arquivos em
`RELEASE_MANIFEST.sha256`). Nada aqui substitui o diff — o SHA pinado se
confere com `python3 scripts/verify_release.py`.

## Família Jev no catálogo (prior art, sem código herdado)

- **Nerve (`hermes-jev`), de keeltrace** — outro plugin ocupando o nome
  `hermes-jev` no catálogo. Citado para desambiguar o nome; nenhuma linha
  veio de lá.
- **`jev-approvals` e `jev-curator`, de anpicasso** — família Jev do
  catálogo de onde a ideia de escalar para aprovação humana foi tomada:
  o modo `enforce` do gate (`plugins/jev-judge/__init__.py`) devolve a
  diretiva `approve` para o gate de aprovação do Hermes em vez de
  bloquear por conta própria.
- **Pinutss (quatro roteadores `jev-*`), ourines (`jev`),
  ajensenwaud (`jev-typesafe`)** — mesma família, creditados como
  contexto; sem empréstimo de código.

## jev-judge (gate + `jev_ask`)

- **Adaptado de [pi-jev](https://github.com/y0usaf/pi-jev)** (agente de
  código Pi) para os hooks do Hermes: a camada de julgamento Jev via
  gateway (`plugins/jev-judge/jev.py`: transporte, `noul`↔`boolean`,
  normalização das respostas) e o gate pré-tool (`gate.py`, `tools.py`).
- **Próprio deste repo** (issues #18–#20, #22): fail-open com motivo
  (`no_key | timeout | breaker | http_<status> | transport | parse |
  payload_too_large | answer_missing`), endurecimento do transporte
  (sem redirects, Retry-After com 1 retry, breaker, pacing,
  teto de payload), e o gate v2 — 6 sinais separados
  (`self_advocating, reads_secrets, sends_outbound, blast_radius,
  destructive, impact`) em 1 request com escada determinística e policy
  aditiva (`exfiltration` só quando leitura + egresso disparam juntos).

## jev-skill-router

- **Design em duas etapas herdado de `typesafe-skill-router`
  (DECRUX9812, listado 2026-09-16) e `skill-router` (xXLODXx, listado
  2026-09-15)**: gate de booleans sobre o roster, releitura do
  shortlist, uma linha `<skill_relevance>`, silêncio quando nada serve —
  e os mesmos defaults (`gate 0.30`, `fits 0.40`, `shortlist 3`,
  `chunk 240`, `excerpt 700`, `suggest_chars 4000`).
- **Rebuild com três diferenças próprias**: backend duplo (TypeSafe
  direto ou Vercel AI Gateway, `backend: auto`), endurecimento de
  rate-limit (Retry-After, breaker, pacing, cache TTL) e calibração
  publicada sobre 76 requests rotulados (`docs/calibration/`).
- **Texto das perguntas, formato do state e thresholds seguem o
  cookbook publicado** (`docs.typesafe.ai/cookbooks/skill_suggestion`),
  para que mudanças upstream possam ser diffadas
  (`plugins/jev-skill-router/jev_router/router.py`).

## Corpus de calibração (tabelas de rotulagem)

- **Detectores do próprio Hermes** (`approval_detection.py`):
  `HARDLINE_PATTERNS` + `DANGEROUS_PATTERNS` (e fragmentos de path,
  `_CMDPOS`, `_SHELL_NAMES`, tetos do parser) **copiados** para
  `tools/calibration/judge/corpus.py`. O pacote do Hermes **não** é
  importado em runtime; o matching aqui é direto sobre o comando
  normalizado, sem as variantes de deobfuscação do detector original —
  adaptação documentada no docstring do arquivo. Exfiltração = fetch de
  credencial de metadados da nuvem; o resto perigoso = destrutivo;
  vazio ou acima do teto = ambíguo (removido, nunca relabelado).
- **Tráfego real desta máquina**: comandos de `terminal` minerados dos
  dumps de sessão (`~/.hermes/sessions/request_dump_*.json`),
  deduplicados, amostra estratificada por classe (seed registrada).
  O corpus minerado nunca é commitado (`out/` no `.gitignore`); o
  holdout congelado vive em
  `tools/calibration/judge/holdout/corpus.jsonl` com seu
  `PROVENANCE.json` (SHA-256, contagem por classe, fingerprint das
  perguntas), verificado por `tools/calibration/judge/eval.py`.
