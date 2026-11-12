# GraphRAG — Parte B (Neo4j)

Schema LPG, constraints e índices no Neo4j + carga do `grafo.json` (formato definido em `contracts.py`).

## Modelo do grafo

```
(:Documento {doc_id, title, url})-[:TEM_CHUNK]->(:Chunk {chunk_id, doc_id, text, position, embedding})
(:Entidade:<Label> {id, label, name, aliases, aliases_text, chunk_ids, embedding})-[:MENCIONADO_EM]->(:Chunk)
(:Entidade)-[:<TIPO> {evidence, chunk_id}]->(:Entidade)
```

- `<Label>` ∈ Pessoa, Instituicao, Tecnologia, Premio, Conceito, Local. Todo nó de entidade também tem o label `Entidade`.
- `<TIPO>` ∈ `ALLOWED_RELATIONS`; origem/destino validados por `RELATION_SCHEMA`.
- Uma aresta por (origem, tipo, destino, chunk): cada fato carrega sua evidência citável.

## Constraints e índices

| Tipo | Nome | Alvo |
|---|---|---|
| Unicidade | `uniq_entidade_id` + `uniq_<label>_id` | `id` das entidades |
| Unicidade | `uniq_documento_doc_id`, `uniq_chunk_chunk_id` | Documento, Chunk |
| RANGE (B-Tree do Neo4j 5) | `idx_entidade_name`, `idx_entidade_label`, `idx_chunk_doc_pos`, `idx_documento_title`, `idx_rel_<tipo>_chunk` | busca exata / por faixa |
| FULLTEXT | `ft_entidade_nome` (name + aliases), `ft_chunk_text`, `ft_relacao_evidence` | busca textual (Lucene) |
| VECTOR (cosine) | `vec_entidade_embedding`, `vec_chunk_embedding` | busca semântica, dimensão `EMBEDDING_DIM` |

## Como rodar

Comandos executados a partir da **raiz do repositório** (onde ficam o `.env` e o `grafo.json`).

Requisitos: Python 3.11+ e Neo4j 5.13+ com o plugin Graph Data Science.

```bash
pip install -r parte_b/requirements.txt
cp .env.example .env          # preencha NEO4J_PASSWORD
python parte_b/neo4j_schema.py         # cria constraints e índices (idempotente)
python parte_b/load_graph.py grafo.json --dry-run   # valida e mostra o que será descartado
python parte_b/load_graph.py grafo.json             # grava (use --wipe para limpar antes)
python parte_b/embeddings.py           # embeddings de Entidade e Chunk (vetores 384d)
python parte_b/gds_algorithms.py       # PageRank, Louvain e grau -> propriedades pagerank, community, grau
python parte_b/consultas_referencia.py # roda as 10 consultas multi-hop de referência
pytest -q parte_b/tests                # unitários + integração (pulados se o Neo4j estiver desligado)
```

Ordem obrigatória: schema → load → embeddings → gds. Se recarregar o grafo, rode os três últimos de novo.

## Embeddings (combinar com a Pessoa C)

Modelo: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dim, CPU, multilíngue).
A pergunta do usuário **tem** que ser embutida com o mesmo modelo:

```python
from embeddings import embed_query
vetor = embed_query("Quem criou o Unix?")
# CALL db.index.vector.queryNodes('vec_entidade_embedding', 5, $vetor) ...
```

O texto embutido de cada entidade (nome + tipo + apelidos + relações) fica salvo em `embedding_text`.

## Algoritmos de grafo (GDS)

`gds_algorithms.py` projeta as entidades com as 10 relações do contrato (não direcionadas) e grava:
`pagerank` (centralidade, para ranquear o contexto), `community` (Louvain, para agrupar por tema) e `grau`.

## Consultas de referência

`consultas_referencia.py` tem 10 consultas (Q1–Q10): 2 e 3 saltos, caminhos variáveis, caminho mínimo,
comunidade + PageRank, **busca híbrida vetor → expansão → ranking (Q8, modelo para a Pessoa C)**,
fulltext e citação de documento. Todas devolvem `evidencias` com `chunk_id` para citação.

Diagrama do schema e números do grafo: [schema.md](schema.md).

Para testar sem o grafo real: `python parte_b/load_graph.py parte_b/exemplo_grafo.json`.

### Neo4j portátil (Windows)

Nesta máquina o Neo4j Community 5.26 + GDS 2.13 + Java 21 estão em `tools/` (fora do git).
Ligar: `powershell -ExecutionPolicy Bypass -File iniciar_neo4j.ps1` (deixe a janela aberta).
Neo4j Browser: http://localhost:7474 — usuário `neo4j`, senha no `.env`.

## Parte C — motor de perguntas e respostas (GraphRAG/KAG)

Busca híbrida vetor + grafo, Text-to-Cypher somente-leitura, resposta citada, confiança e baseline.
Não altera o banco. LLM principal: **Ollama local** com `qwen2.5:7b` (gratuito, sem internet).
Uso rápido: `pip install -r parte_c/requirements.txt`, `ollama pull qwen2.5:7b` e
`python -m parte_c.cli -q "Quem criou o Unix?"`. Documentação: [parte_c/README.md](../parte_c/README.md);
contrato para a interface: [parte_c/docs/integracao_pessoa_d.md](../parte_c/docs/integracao_pessoa_d.md).

## Créditos

Neo4j Python Driver, Neo4j Graph Data Science, Pydantic, Sentence-Transformers.
