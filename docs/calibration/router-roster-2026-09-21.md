# Calibração do router no roster real — 2026-09-21

Ticket [#6](https://github.com/DoGMaTiiC/hermes-jev/issues/6). Rota usada: **TypeSafe direto**
(`TYPESAFE_API_KEY`, `backend: auto` → `typesafe`) — 152 chamadas Jev, custo em $ `None`
(rota direta reporta `usage` em tokens), 0 fail-open.

## Método

- Conjunto rotulado: `tools/calibration/labelled.json` — **76 requests** (66 cobertos, cada um
  por exatamente uma skill do roster; 10 "nenhuma"). Roster vivo: 103 skills de `~/.hermes/skills`.
- Cada request rodou pelo **pipeline shipado** (`tools/calibration/run_live.py`: `rank_wide` =
  porta 1, depois `rerank_questions` sobre o top-3, depois `router.suggest` — a decisão do CLI,
  de cache hit). Todas as respostas cruas ficaram em `tools/calibration/raw-2026-09-21.jsonl`.
- O sweep é **replay offline** sobre o JSONL (`tools/calibration/score.py`) — nenhum número
  abaixo exige nova chamada.
- Diferença vs. produção num ponto: a porta 2 foi sempre disparada, mesmo abaixo do gate, para
  o sweep ter as respostas em todos os requests. Acima do gate o comportamento é idêntico.

## Resultado nos limiares shipados (gate 0.30 / fits 0.40)

- top-1 em cobertos: **63/66 = 95%**
- silêncio indevido (missed): 3/66 = 5%
- sugestão indevida (none-set): **0/10 = 0%**

### Silêncios indevidos (missed)

| Request                                                            | Esperado                   | Gate | Escolha da porta 2         |
| ------------------------------------------------------------------ | -------------------------- | ---- | -------------------------- |
| escreve a letra e o prompt do Suno pra uma música sobre o interior | `songwriting-and-ai-music` | 0.09 | `songwriting-and-ai-music` |
| tira o jeitão de IA desse texto e deixa com voz de gente           | `humanizer`                | 0.15 | `humanizer`                |
| quanto tempo de carro de São Paulo até Campos do Jordão…           | `maps`                     | 0.15 | `maps`                     |

### Erros (sugeriu outra skill)

Nenhum: **0 em 66**. Quando a porta 2 é consultada, a escolha bate o label **66/66**.
Toda a perda (3 casos) vem da porta 1 — o gate julgou "não precisa de skill / prosa resolve".

## Sweep (célula = acurácia top-1 · silêncios · sugestões no none-set)

| gate \ fits | 0.30    | 0.35    | 0.40         | 0.45    | 0.50    |
| ----------- | ------- | ------- | ------------ | ------- | ------- |
| 0.20        | 95%·3·1 | 95%·3·1 | 95%·3·1      | 95%·3·1 | 94%·4·1 |
| 0.25        | 95%·3·0 | 95%·3·0 | 95%·3·0      | 95%·3·0 | 94%·4·0 |
| 0.30        | 95%·3·0 | 95%·3·0 | **95%·3·0*** | 95%·3·0 | 94%·4·0 |
| 0.35        | 89%·7·0 | 89%·7·0 | 89%·7·0      | 89%·7·0 | 88%·8·0 |
| 0.40        | 88%·8·0 | 88%·8·0 | 88%·8·0      | 88%·8·0 | 86%·9·0 |

\* = configuração shipada.

Leitura:

- **`fits` não é o gargalo**: 0.30–0.45 é chão plano (nada muda); só em 0.50 começa a custar
  (1 silêncio a mais). Manter **0.40** (meio da faixa plana, com folga dos dois lados).
- **`gate` tem um joelho limpo em 0.25–0.30**: abaixo de 0.25 aparece 1 sugestão no none-set
  ("o que você acha dessa ideia de startup?", gate 0.23); acima de 0.30 a acurácia cai para 89%
  (mais 4 cobertos silenciados). Manter **0.30** — é o topo do joelho, o lado conservador.
- Os gates do none-set vão de 0.03 a 0.23; os 3 silêncios indevidos estão em 0.09–0.15 —
  **dentro da faixa do none-set**. Ou seja: nenhum limiar de gate separa esses 3 sem admitir
  falso positivo. Não é caso de mexer no número; é limite do gate pra requests de prosa
  ("escreve a letra", "tira o jeitão de IA") que _também_ mapeiam numa skill.

## Latência / custo

- Latência por decisão (2 chamadas): **p50 1943 ms**, média 2051 ms, max 3771 ms.
- Custo em $: **`None`** na rota direta (por chamada, `usage` em tokens); nenhuma cobrança
  de gateway. Na rota gateway, as mesmas chamadas custariam ~$0.00002 cada.
- Gate médio: cobertos **0.696** vs none-set **0.073** — separação média de ~10x.

## Recomendação

**Manter 0.30 / 0.40.** O par shipado está no ótimo medido (95% top-1, 0% needless) e o sweep
mostra que qualquer movimento custa em um dos lados. O ganho teórico restante (3 casos) não é
alcançável por limiar: são requests que o gate lê como conversa.

## Achado colateral (ticket próprio)

O roster do router vê **103** skills; o Hermes carrega **130**. Os 27 ausentes são exatamente os
symlinks de topo apontando pra fora de `~/.hermes/skills` (26 × `~/.agents/skills`, 1 × plugin
`hermes-lcm`) — `os.walk` sem `followlinks` não os alcança. Prova direta: 6 requests deste
conjunto (`to-spec`, `to-tickets`, `wayfinder`, `handoff`, `teach`, `resolving-merge-conflicts`)
foram descartados pelo runner porque o label é invisível ao roster. Ticket: **#12**.

## Reprodutibilidade

```bash
python3 tools/calibration/run_live.py     # 76 requests, ~4 min, rota direta
python3 tools/calibration/score.py        # tabelas acima, offline, sobre o JSONL
```
