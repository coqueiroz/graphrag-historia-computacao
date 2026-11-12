"""
C5 — Baseline RAG vetorial PURO (para comparar com o GraphRAG).

Recupera SOMENTE nós :Chunk pelo índice `vec_chunk_embedding`, com o MESMO `embed_query` da Pessoa B.
`TEM_CHUNK` é usado apenas para obter título/url do Documento.
NÃO usa: relações de domínio, caminhos multi-hop, PageRank, community, Text-to-Cypher ou entidades.

Condições comparáveis ao GraphRAG: mesmo LLM, mesmo prompt de geração/validação, mesmo teto de contexto
(MAX_CONTEXT_CHARS). Diferença inevitável: as fontes do baseline são trechos longos de texto; as do
GraphRAG são fatos (arestas) com a frase de evidência.
"""
from __future__ import annotations

from typing import Callable, Optional

from parte_c.config import Settings
from parte_c.schemas import Mode, RetrievalResult, Source, Subgraph


def retrieve_baseline(question: str, top_k: Optional[int] = None, *, gateway=None,
                      embed_fn: Optional[Callable[[str], list[float]]] = None,
                      settings: Optional[Settings] = None, vetor: Optional[list[float]] = None) -> RetrievalResult:
    settings = settings or Settings.from_env()
    k = top_k or settings.baseline_top_k
    if gateway is None:
        from parte_c.graph_store import Neo4jGateway
        gateway = Neo4jGateway(settings)
    if vetor is None:
        if embed_fn is None:
            from embeddings import embed_query as embed_fn  # Pessoa B
        vetor = embed_fn(question)
    result = RetrievalResult(question=question, mode=Mode.BASELINE, strategy="chunk_vector")
    rows = gateway.chunk_search(vetor, k, trace=result.queries)
    # cada chunk recebe uma fatia igual do teto de contexto (o mesmo teto do GraphRAG)
    per_chunk = max(300, settings.max_context_chars // max(1, k) - 200)
    for i, r in enumerate(rows, start=1):
        text = r.get("text") or ""
        result.sources.append(Source(
            id=f"F{i}", doc_id=r.get("doc_id"), title=r.get("title"), url=r.get("url"),
            chunk_id=r.get("chunk_id"), quote=text[:per_chunk], score=round(float(r.get("score") or 0.0), 4),
            evidence_in_chunk=True,
        ))
    result.subgraph = Subgraph()
    if not rows:
        result.warnings.append("Nenhum trecho recuperado pelo índice vetorial de chunks.")
    return result
