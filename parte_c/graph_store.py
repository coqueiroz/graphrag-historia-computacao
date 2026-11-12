"""
Acesso SOMENTE-LEITURA ao Neo4j para a Parte C.

Reaproveita da Pessoa B:
  - `neo4j_config.get_driver()` / `NEO4J_DATABASE`  (conexão via .env)
  - `consultas_referencia.CONSULTAS["Q8"]`            (busca híbrida de referência, executada literalmente
                                                      em `q8_reference`)
  - `consultas_referencia.REL`                        (as 10 relações de domínio)

Por que existe uma consulta "Q8 estruturada" (EXPANSION_CYPHER) além da Q8?
  A Q8 devolve strings ("A -TIPO-> B") e saltos fixos 1..2. A Pessoa D precisa de nós/arestas com IDs
  de domínio (para desenhar o subgrafo) e a Parte C precisa citar cada aresta. A consulta abaixo segue
  o MESMO método da Q8 (índice vec_entidade_embedding -> expansão por caminhos das 10 relações ->
  ordenação com PageRank), mas devolve estrutura, aceita 1..3 saltos e evita caminhos com nós repetidos.
  A Q8 original NÃO foi alterada.

Garantias de segurança desta camada:
  - toda sessão usa `default_access_mode=READ_ACCESS` e `execute_read` (o servidor recusa escrita);
  - toda transação tem timeout (`unit_of_work(timeout=...)`);
  - nenhum valor do usuário é interpolado no texto Cypher (só parâmetros; `hops` é inteiro validado);
  - o vetor de embedding nunca vai para o trace (aparece como "<vetor 384d>").
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from parte_c.config import Settings
from parte_c.schemas import ExecutedQuery

from consultas_referencia import CONSULTAS, REL  # noqa: E402  (Pessoa B)

MAX_HOPS = 3

VECTOR_SEEDS_CYPHER = """
CALL db.index.vector.queryNodes('vec_entidade_embedding', $k, $vetor) YIELD node, score
WHERE score >= $min_score
RETURN node {.id, .name, .label, .pagerank, .community} AS n, score
ORDER BY score DESC, n.id ASC
"""

FULLTEXT_SEEDS_CYPHER = """
CALL db.index.fulltext.queryNodes('ft_entidade_nome', $termos) YIELD node, score
WITH node, score AS ft ORDER BY ft DESC, node.id ASC LIMIT $n
RETURN node {.id, .name, .label, .pagerank, .community} AS n,
       vector.similarity.cosine(node.embedding, $vetor) AS sim, ft AS ft_score
"""

# {hops} é um inteiro validado (1..3); {rel} é a constante REL da Pessoa B. Nada vem do usuário.
EXPANSION_CYPHER = """
UNWIND $sementes AS s
MATCH (semente:Entidade {{id: s}})
OPTIONAL MATCH p = (semente)-[:{rel}*1..{hops}]-(viz:Entidade)
WHERE size(nodes(p)) = size(reduce(vistos = [], n IN nodes(p) |
        CASE WHEN n IN vistos THEN vistos ELSE vistos + n END))
WITH s, p, viz, vector.similarity.cosine(viz.embedding, $vetor) AS sim_viz,
     size([r IN relationships(p) WHERE type(r) IN $tipos]) AS casam
ORDER BY casam DESC, sim_viz DESC, length(p) ASC, viz.pagerank DESC, viz.id ASC
WITH s, collect(p)[..$max_paths] AS caminhos
RETURN s AS seed_id,
  [c IN caminhos | {{
     nos:  [n IN nodes(c) | n {{.id, .name, .label, .pagerank, .community,
                               sim: vector.similarity.cosine(n.embedding, $vetor)}}],
     rels: [r IN relationships(c) | {{source: startNode(r).id, target: endNode(r).id, type: type(r),
                                     evidence: r.evidence, chunk_id: r.chunk_id}}]
  }}] AS caminhos
"""

PROVENANCE_CYPHER = """
UNWIND $chunk_ids AS cid
MATCH (c:Chunk {chunk_id: cid})
OPTIONAL MATCH (d:Documento)-[:TEM_CHUNK]->(c)
RETURN c.chunk_id AS chunk_id, c.position AS position, c.text AS text,
       coalesce(d.doc_id, c.doc_id) AS doc_id, d.title AS title, d.url AS url
"""

NODES_BY_IDS_CYPHER = """
MATCH (e:Entidade) WHERE e.id IN $ids
RETURN e {.id, .name, .label, .pagerank, .community} AS n,
       CASE WHEN $vetor IS NULL THEN null ELSE vector.similarity.cosine(e.embedding, $vetor) END AS sim
"""

# Confere se arestas citadas por uma consulta gerada EXISTEM e devolve a evidência real do banco.
VERIFY_EDGES_CYPHER = f"""
UNWIND $edges AS e
MATCH (a:Entidade {{id: e.source}})-[r:{REL}]-(b:Entidade {{id: e.target}})
WHERE type(r) = e.type AND (e.chunk_id IS NULL OR r.chunk_id = e.chunk_id)
RETURN DISTINCT startNode(r).id AS source, endNode(r).id AS target, type(r) AS type,
       r.evidence AS evidence, r.chunk_id AS chunk_id
"""

# Baseline: SÓ Chunk + índice vetorial de chunks; TEM_CHUNK apenas para título/url do documento.
CHUNK_SEARCH_CYPHER = """
CALL db.index.vector.queryNodes('vec_chunk_embedding', $k, $vetor) YIELD node AS c, score
OPTIONAL MATCH (d:Documento)-[:TEM_CHUNK]->(c)
RETURN c.chunk_id AS chunk_id, c.position AS position, c.text AS text,
       coalesce(d.doc_id, c.doc_id) AS doc_id, d.title AS title, d.url AS url, score
ORDER BY score DESC, chunk_id ASC
"""

SCHEMA_INFO_CYPHER = """
CALL db.labels() YIELD label RETURN collect(label) AS labels
"""


def _safe_params(params: dict) -> dict:
    """Remove vetores/listas enormes do trace."""
    out = {}
    for k, v in params.items():
        if isinstance(v, list) and v and all(isinstance(x, float) for x in v[:5]) and len(v) > 16:
            out[k] = f"<vetor {len(v)}d>"
        else:
            out[k] = v
    return out


class Neo4jGateway:
    """Todas as leituras que a Parte C faz no Neo4j. Nenhum método escreve."""

    def __init__(self, settings: Optional[Settings] = None, driver=None, readonly_driver=None):
        from neo4j_config import NEO4J_DATABASE, get_driver  # Pessoa B

        self.settings = settings or Settings.from_env()
        self.database = NEO4J_DATABASE
        self._driver = driver or get_driver()
        self._ro_driver = readonly_driver
        if self._ro_driver is None and self.settings.readonly_user:
            from neo4j import GraphDatabase
            from neo4j_config import NEO4J_URI

            self._ro_driver = GraphDatabase.driver(
                NEO4J_URI, auth=(self.settings.readonly_user, self.settings.readonly_password))

    # ------------------------------------------------------------------ infraestrutura
    def close(self) -> None:
        for d in (self._driver, self._ro_driver):
            if d is not None:
                d.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def _read(self, name: str, query: str, params: dict, trace: Optional[list],
              transform: Optional[Callable] = None, driver=None, max_rows: Optional[int] = None):
        import neo4j

        timeout = self.settings.query_timeout_s

        @neo4j.unit_of_work(timeout=timeout)
        def work(tx):
            result = tx.run(query, params)
            if transform is not None:
                return transform(result)
            rows = []
            for rec in result:
                rows.append(rec.data())
                if max_rows is not None and len(rows) >= max_rows:
                    break
            return rows

        t0 = time.perf_counter()
        drv = driver or self._driver
        with drv.session(database=self.database, default_access_mode=neo4j.READ_ACCESS) as s:
            out = s.execute_read(work)
        if trace is not None:
            trace.append(ExecutedQuery(
                name=name, query=query.strip(), parameters=_safe_params(params),
                rows=len(out) if isinstance(out, list) else 0,
                ms=round((time.perf_counter() - t0) * 1000, 1)))
        return out

    # ------------------------------------------------------------------ GraphRAG
    def vector_seeds(self, vetor: list[float], k: int, min_score: float, trace=None) -> list[dict]:
        rows = self._read("sementes_vetoriais", VECTOR_SEEDS_CYPHER,
                          {"k": k, "vetor": vetor, "min_score": min_score}, trace)
        return [{**r["n"], "score": r["score"]} for r in rows]

    def fulltext_seeds(self, termos: str, vetor: list[float], n: int, trace=None) -> list[dict]:
        rows = self._read("sementes_fulltext", FULLTEXT_SEEDS_CYPHER,
                          {"termos": termos, "vetor": vetor, "n": n}, trace)
        return [{**r["n"], "score": r["sim"], "ft_score": r["ft_score"]} for r in rows]

    def expand(self, seed_ids: list[str], vetor: list[float], hops: int, max_paths: int,
               trace=None, tipos: Optional[list[str]] = None) -> dict[str, list[dict]]:
        """`tipos`: relações citadas na pergunta (ver retrieval.relation_cues); caminhos que as usam
        têm prioridade no corte de `max_paths`. Lista vazia = ordenação só por similaridade (como antes)."""
        if not isinstance(hops, int) or not 1 <= hops <= MAX_HOPS:
            raise ValueError(f"hops deve ser inteiro entre 1 e {MAX_HOPS}")
        query = EXPANSION_CYPHER.format(rel=REL, hops=hops)
        rows = self._read("expansao_caminhos", query,
                          {"sementes": seed_ids, "vetor": vetor, "max_paths": max_paths,
                           "tipos": list(tipos or [])}, trace)
        return {r["seed_id"]: r["caminhos"] for r in rows}

    def provenance(self, chunk_ids: list[str], trace=None) -> dict[str, dict]:
        if not chunk_ids:
            return {}
        rows = self._read("procedencia_chunks", PROVENANCE_CYPHER, {"chunk_ids": chunk_ids}, trace)
        return {r["chunk_id"]: r for r in rows}

    def nodes_by_ids(self, ids: list[str], vetor: Optional[list[float]] = None, trace=None) -> dict[str, dict]:
        if not ids:
            return {}
        rows = self._read("nos_por_id", NODES_BY_IDS_CYPHER, {"ids": ids, "vetor": vetor}, trace)
        return {r["n"]["id"]: {**r["n"], "sim": r["sim"]} for r in rows}

    def verify_edges(self, edges: list[dict], trace=None) -> list[dict]:
        if not edges:
            return []
        return self._read("verificacao_arestas", VERIFY_EDGES_CYPHER, {"edges": edges}, trace)

    def q8_reference(self, vetor: list[float], k: int = 5, trace=None) -> list[dict]:
        """Executa a Q8 da Pessoa B literalmente (para comparação/teste de fidelidade)."""
        return self._read("Q8_referencia", CONSULTAS["Q8"]["cypher"], {"k": k, "vetor": vetor}, trace)

    # ------------------------------------------------------------------ baseline
    def chunk_search(self, vetor: list[float], k: int, trace=None) -> list[dict]:
        return self._read("busca_vetorial_chunks", CHUNK_SEARCH_CYPHER, {"k": k, "vetor": vetor}, trace)

    # ------------------------------------------------------------------ Text-to-Cypher
    def explain(self, query: str, params: dict) -> dict:
        """EXPLAIN não executa a consulta; devolve o tipo ('r' = só leitura) e os operadores do plano."""
        def transform(result):
            summary = result.consume()
            ops: list[str] = []

            def walk(plan):
                if not plan:
                    return
                op = plan.get("operatorType") if isinstance(plan, dict) else getattr(plan, "operator_type", None)
                if op:
                    ops.append(str(op))
                children = plan.get("children", []) if isinstance(plan, dict) else getattr(plan, "children", [])
                for ch in children or []:
                    walk(ch)

            walk(summary.plan)
            return {"query_type": summary.query_type, "operators": ops}

        return self._read("explain_texto_cypher", "EXPLAIN " + query, params, None,
                          transform=transform, driver=self._ro_driver)

    def run_generated(self, query: str, params: dict, max_rows: int, trace=None) -> list[dict]:
        return self._read("texto_para_cypher", query, params, trace,
                          driver=self._ro_driver, max_rows=max_rows)


GraphGateway = Any  # qualquer objeto com os métodos acima (o Neo4jGateway ou um fake de teste)
