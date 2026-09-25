# Corpus do judge (#21)

Pipeline offline que transforma os dumps de sessão desta máquina em corpus
rotulado para medir o gate do judge (consumido pelo evaluator do #22).

## Rodar

```bash
# gera o corpus a partir dos dumps locais (seed 21, até 50/classe)
python3 tools/calibration/judge/corpus.py

# outras sementes / tamanhos
python3 tools/calibration/judge/corpus.py --seed 42 --per-class 25

# testes (fixtures pequenas; nunca tocam nos dumps reais)
python3 tools/calibration/judge/test_corpus.py
```

## O que sai

- `out/corpus.jsonl` — uma linha por caso: `id` (sha curto), `request`,
  `kind` (`safe|destructive|exfil|ambiguous`), `label_source`, `notes`.
- `out/PROVENANCE.json` — contagem por classe, seed, SHA-256 do corpus e
  fingerprint das perguntas (mesmo formato que o #22 verifica).

`out/` está no `.gitignore`: o corpus minerado nunca é commitado.

## Pipeline

1. **Mineração** — comandos de `terminal` nos dumps
   (`~/.hermes/sessions/request_dump_*.json`), deduplicados.
2. **Rotulagem** — padrões copiados dos detectores do Hermes
   (`approval_detection.py`: `HARDLINE_PATTERNS` + `DANGEROUS_PATTERNS`;
   pacote do Hermes não é importado). Exfiltração = fetch de credencial de
   metadados da nuvem; o resto perigoso = destrutivo; vazio ou acima do
   teto do parser = ambíguo.
3. **Amostra** — estratificada por classe, embaralhada com a seed registrada.
4. **Curadoria** — drop-never-relabel: ambíguo é removido, nunca relabelado
   (`label_source: curated` fica reservado a adições manuais futuras).
