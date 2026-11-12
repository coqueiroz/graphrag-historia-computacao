# Integração Parte C → Pessoa D (interface)

## Chamada

```python
from parte_c import responder_pergunta, fechar

r = responder_pergunta(
    "Quem criou o Unix?",
    modo="graphrag",        # "graphrag" | "baseline"
    estrategia=None,        # None = .env (padrão "auto") | "auto" | "vector" | "text2cypher"
    top_k=None, hops=None,  # opcionais (hops 1..3)
    incluir_trace=False,    # True => r["trace"] com consultas Cypher executadas e tempos
)
```

* **Síncrona**, nunca levanta exceção: erros viram `status="error"` com mensagem em `warnings`.
* Retorna um **dict serializável em JSON** (modelo `QAResponse` em `parte_c/schemas.py`).
* Driver Neo4j, modelo de embeddings e cliente LLM são reaproveitados entre chamadas no mesmo processo.
  No Streamlit, isso funciona naturalmente (o módulo fica carregado). `fechar()` é opcional.
* Nunca contém senha, chave, embeddings, prompts internos ou stacktrace.

## Campos do retorno

| Campo | Tipo | Significado |
|---|---|---|
| `question` | str | pergunta recebida |
| `mode` | `"graphrag"` \| `"baseline"` | modo usado |
| `status` | `"answered"` \| `"insufficient_evidence"` \| `"retrieval_only"` \| `"error"` | ver abaixo |
| `answer` | str | texto com citações `[F1]`, `[F2]`… (todas existem em `sources`) |
| `confidence` | obj | `score` (0–1), `level` (`low`/`medium`/`high`), `method` (`heuristico_nao_calibrado`), `factors`, `explanation` |
| `sources[]` | lista | fontes enviadas ao LLM: `id` (`F1`), `doc_id`, `title`, `url`, `chunk_id`, `quote` (trecho literal), `context` (janela do chunk), `relation` (`source_id`, `type`, `target_id`, `source_name`, `target_name`) — `null` no baseline —, `score`, `evidence_in_chunk`, `cited` (a resposta cita esta fonte?) |
| `subgraph.nodes[]` | lista | `id` (ID de domínio, ex. `pessoa:ken_thompson`), `name`, `label`, `pagerank`, `community`, `similarity` |
| `subgraph.edges[]` | lista | `source`, `target`, `type`, `evidence`, `chunk_id`, `score`, `hop`, `origin` (`vector`/`text2cypher`). **Corresponde exatamente às fontes** (1 aresta ↔ 1 fonte). Vazio no baseline. |
| `generated_cypher` | obj | `query` (a consulta **efetivamente executada**, ou a rejeitada), `parameters`, `status` (`executed`/`rejected`/`fallback`/`not_applicable`), `rationale`, `attempts`, `repairs` (reparos automáticos aplicados à consulta do LLM), `error`, `rows` |
| `retrieval` | obj | `strategy`, `hops`, `top_k`, `seeds[]` (`id`, `name`, `label`, `similarity`, `origin`), `n_sources`, `n_edges` |
| `claims[]` | lista | afirmações da resposta: `text`, `sources`, `supported` (validação determinística), `support_reason` |
| `latency_ms` | float | tempo total |
| `llm` | obj | `provider`, `model`, `ms`, `usage` (vazio sem LLM) |
| `warnings[]` | lista | avisos legíveis (duplicatas, fallback, afirmações sem suporte…) |
| `trace` | obj \| null | só com `incluir_trace=True`: `consultas[]` (`name`, `query`, `parameters`, `rows`, `ms`), `tempos_ms`, `config` |

### Status
* `answered` — resposta gerada **e** validada (ao menos uma afirmação com suporte nas fontes citadas).
* `insufficient_evidence` — abstenção honesta: "Não encontrei informação suficiente nos documentos consultados…".
* `retrieval_only` — sem LLM configurado/disponível: `answer` lista as evidências mais relevantes.
* `error` — banco fora do ar, entrada inválida etc. `answer` traz mensagem amigável.

## Exemplo de retorno (ILUSTRATIVO — não são dados reais do banco nem pontuações medidas)

```json
{
  "question": "Quem criou o Unix?",
  "mode": "graphrag",
  "status": "answered",
  "answer": "O Unix foi criado por Ken Thompson [F1] e Dennis Ritchie [F2].",
  "confidence": {"score": 0.0, "level": "low|medium|high", "method": "heuristico_nao_calibrado",
                 "factors": {"cobertura": 0.0, "procedencia": 0.0, "relevancia": 0.0, "consistencia": 0.0,
                             "penalidades": {}},
                 "explanation": "Confiança ... — heurística não calibrada ..."},
  "sources": [{"id": "F1", "doc_id": "unix", "title": "Unix", "url": "https://pt.wikipedia.org/wiki/Unix",
               "chunk_id": "unix#0", "quote": "... originalmente criado por Ken Thompson, Dennis Ritchie ...",
               "relation": {"source_id": "pessoa:ken_thompson", "type": "CRIOU", "target_id": "tecnologia:unix",
                            "source_name": "Ken Thompson", "target_name": "Unix"},
               "cited": true}],
  "subgraph": {"nodes": [{"id": "pessoa:ken_thompson", "name": "Ken Thompson", "label": "Pessoa"}],
               "edges": [{"source": "pessoa:ken_thompson", "target": "tecnologia:unix", "type": "CRIOU",
                          "evidence": "...", "chunk_id": "unix#0", "hop": 1, "origin": "vector"}]},
  "generated_cypher": {"query": "MATCH ... LIMIT 50", "parameters": {"t": "tecnologia:unix"}, "status": "executed"},
  "retrieval": {"strategy": "hybrid (text2cypher + vector_cypher)", "hops": 2, "seeds": []},
  "latency_ms": 0,
  "warnings": []
}
```

## Exemplo para Streamlit (copiar e adaptar)

Arquivo pronto: [`docs/exemplo_streamlit_pessoa_d.py`](exemplo_streamlit_pessoa_d.py). Rodar na raiz:

```powershell
pip install streamlit pyvis
streamlit run docs/exemplo_streamlit_pessoa_d.py
```

Sugestões para a UI:
* Mostrar `answer` e, ao lado, as fontes com `cited=True` em destaque (título, link `url`, `quote`).
* Badge de confiança com `level` + tooltip com `explanation` (deixar claro que não é probabilidade).
* Desenhar `subgraph` (nós coloridos por `label`; arestas rotuladas por `type`; destacar arestas cujas
  fontes foram citadas). Os IDs são estáveis entre execuções.
* Mostrar `generated_cypher.query` e `status` num expander "Como a resposta foi obtida".
* Para a comparação: chamar os dois modos com a mesma pergunta e exibir lado a lado.
