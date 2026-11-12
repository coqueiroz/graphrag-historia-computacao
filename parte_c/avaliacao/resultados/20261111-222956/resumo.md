# Resumo automático (métricas aproximadas — conferir amostra manualmente)

| modo | tipo | n | erros | respondidas | abstencoes | recall_fontes_ouro | recall_arestas_ouro | acerto_palavras | abstencao_correta | taxa_sem_suporte | citacoes_invalidas | confianca | latencia_ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 2_saltos | 10 | 0 | 5 | 5 | 0.25 |  | 0.4 | 0.5 | 0.0 | 0 | 0.5502 | 13833.09 |
| baseline | 3_saltos | 10 | 0 | 5 | 5 | 0.35 |  | 0.3 | 0.5 | 0.0667 | 0 | 0.5207 | 15072.67 |
| baseline | TODAS | 30 | 0 | 13 | 17 | 0.32 |  | 0.4 | 0.6 | 0.0256 | 0 | 0.5008 | 13372.67 |
| baseline | direta | 5 | 0 | 3 | 2 | 0.4 |  | 0.6 | 0.6 | 0.0 | 0 | 0.6132 | 12558.86 |
| baseline | sem_resposta | 5 | 0 | 0 | 5 |  |  |  | 1 |  | 0 | 0.25 | 9865.64 |
| graphrag | 2_saltos | 10 | 0 | 9 | 1 | 1.0 | 1.0 | 0.8 | 0.9 | 0.0 | 0 | 0.7342 | 33972.81 |
| graphrag | 3_saltos | 10 | 0 | 10 | 0 | 1.0 | 1.0 | 1 | 1 | 0.0 | 0 | 0.7927 | 36589.13 |
| graphrag | TODAS | 30 | 0 | 24 | 6 | 1.0 | 1.0 | 0.88 | 0.9667 | 0.0 | 0 | 0.6788 | 30513.5433 |
| graphrag | direta | 5 | 0 | 5 | 0 | 1.0 | 1.0 | 0.8 | 1 | 0.0 | 0 | 0.8188 | 27292.1 |
| graphrag | sem_resposta | 5 | 0 | 0 | 5 |  |  |  | 1 |  | 0 | 0.2 | 14665.28 |
