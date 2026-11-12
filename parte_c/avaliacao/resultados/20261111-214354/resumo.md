# Resumo automático (métricas aproximadas — conferir amostra manualmente)

| modo | tipo | n | erros | respondidas | abstencoes | recall_fontes_ouro | recall_arestas_ouro | acerto_palavras | abstencao_correta | taxa_sem_suporte | citacoes_invalidas | confianca | latencia_ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 2_saltos | 10 | 0 | 4 | 6 | 0.25 |  | 0.3 | 0.4 | 0.0 | 0 | 0.4933 | 9575.29 |
| baseline | 3_saltos | 10 | 0 | 4 | 6 | 0.35 |  | 0.3 | 0.4 | 0.375 | 0 | 0.4073 | 9286.15 |
| baseline | TODAS | 30 | 0 | 12 | 18 | 0.32 |  | 0.4 | 0.5667 | 0.125 | 0 | 0.4665 | 9089.97 |
| baseline | direta | 5 | 0 | 4 | 1 | 0.4 |  | 0.8 | 0.8 | 0.0 | 0 | 0.7476 | 9799.0 |
| baseline | sem_resposta | 5 | 0 | 0 | 5 |  |  |  | 1 |  | 0 | 0.25 | 7017.94 |
| graphrag | 2_saltos | 10 | 0 | 9 | 1 | 0.95 | 0.95 | 0.7 | 0.9 | 0.0 | 0 | 0.7443 | 25787.69 |
| graphrag | 3_saltos | 10 | 0 | 6 | 4 | 0.7667 | 0.6667 | 0.6 | 0.6 | 0.0 | 0 | 0.5911 | 25309.98 |
| graphrag | TODAS | 30 | 0 | 20 | 10 | 0.8867 | 0.8467 | 0.72 | 0.8333 | 0.0 | 0 | 0.6133 | 22792.22 |
| graphrag | direta | 5 | 0 | 5 | 0 | 1.0 | 1.0 | 1 | 1 | 0.0 | 0 | 0.8088 | 24487.08 |
| graphrag | sem_resposta | 5 | 0 | 0 | 5 |  |  |  | 1 |  | 0 | 0.2 | 10070.9 |
