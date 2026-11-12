# Execuções da avaliação (Parte C)

| Pasta | O que é | Usar no artigo? |
|---|---|---|
| `20261112-155230` | **Execução OFICIAL**: grafo final (após a fusão "Ken Thompson"/"Kenneth Thompson"), versão 2 do motor. 30 perguntas × GraphRAG/baseline, Qwen2.5-7B (Ollama), Apple M5. | **Sim** (números do artigo) |
| `20261111-222956` | Versão 2 do motor sobre o grafo anterior (com a duplicata de Ken Thompson). Inclui `comparacao_com_20261111-214354.txt`. | Só como histórico |
| `20261111-214354` | Versão 1 do motor (2 saltos, ranking só semântico). Mantida para documentar a evolução. | Só como "versão anterior" |

Todas as métricas em `resumo.md`/`metricas.csv` são **automáticas e aproximadas**. A anotação humana
(correção 0–2, suporte 0–2, alucinação, multi-hop) é feita na planilha gerada a partir da execução oficial
e, quando concluída, deve ser salva como `20261112-155230/anotacao_humana.csv`.

Para comparar execuções com o gabarito atual, re-pontue as `respostas.jsonl` com
`parte_c.evaluation.score_response` (as métricas gravadas usam o gabarito vigente no momento da execução).
