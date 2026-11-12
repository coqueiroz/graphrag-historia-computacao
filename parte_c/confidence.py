"""
C4 — Indicador de confiança HEURÍSTICO e explicável (método: "heuristico_nao_calibrado").

Não é probabilidade de acerto. Mede o quanto a resposta está APOIADA em evidência recuperada.
Depende só de dados observáveis (validação determinística, procedência, similaridade, grafo);
nenhum valor vem de "autoavaliação" do LLM.

Fatores (0..1) e pesos:
  cobertura    0.35  fração de afirmações com suporte verificado nas fontes citadas
  procedencia  0.25  fontes citadas com Documento+Chunk resolvidos (1) e evidência literal no chunk (+)
  relevancia   0.20  similaridade semântica média das 3 melhores sementes/chunks, reescalada:
                     clamp((s - 0.70) / (0.90 - 0.70)); s na escala do Neo4j (1+cos)/2
  consistencia 0.20  GraphRAG: 0.5·(arestas citadas conectadas às sementes no subgrafo)
                              + 0.5·min(1, documentos distintos citados / 2)
                     baseline: min(1, documentos distintos citados / 2)
Fator ausente (ex.: cobertura no modo sem LLM) é retirado e os pesos restantes são renormalizados.

Penalidades (subtraídas): citação inexistente −0.10 cada (máx −0.20); afirmação sem suporte −0.10 cada
(máx −0.30); fallback do Text-to-Cypher −0.05; possível duplicata de entidade −0.05;
resposta que admite contradição nas fontes −0.10.
Tetos: abstenção (insufficient_evidence) <= 0.25; sem LLM (retrieval_only) <= 0.60; erro/sem fontes = 0.
Níveis: alta >= 0.75 > média >= 0.50 > baixa.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Optional

from parte_c.schemas import Claim, Confidence, CypherStatus, Edge, Mode, Seed, Source, Status

WEIGHTS = {"cobertura": 0.35, "procedencia": 0.25, "relevancia": 0.20, "consistencia": 0.20}
REL_LO, REL_HI = 0.70, 0.90
HIGH, MEDIUM = 0.75, 0.50
CAP_ABSTAIN, CAP_RETRIEVAL_ONLY = 0.25, 0.60


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def level_of(score: float) -> str:
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def _connected_fraction(edges: list[Edge], cited_edges: list[Edge], seeds: Iterable[Seed]) -> Optional[float]:
    if not cited_edges:
        return None
    adj = defaultdict(set)
    for e in edges:
        adj[e.source].add(e.target)
        adj[e.target].add(e.source)
    seen = set(s.id for s in seeds)
    if not seen:  # Text-to-Cypher puro: considera conectado o que estiver no mesmo componente
        seen = {cited_edges[0].source}
    q = deque(seen)
    while q:
        n = q.popleft()
        for m in adj[n]:
            if m not in seen:
                seen.add(m)
                q.append(m)
    return sum(1 for e in cited_edges if e.source in seen or e.target in seen) / len(cited_edges)


def compute_confidence(*, mode: Mode, status: Status, sources: list[Source], claims: list[Claim],
                       seeds: list[Seed], edges: list[Edge], answer: str = "",
                       invalid_citations: Optional[list[str]] = None, cypher_status: Optional[CypherStatus] = None,
                       warnings: Optional[list[str]] = None) -> Confidence:
    warnings = warnings or []
    invalid_citations = invalid_citations or []
    if status == Status.ERROR or not sources:
        return Confidence(score=0.0, level="low", factors={"fontes": 0},
                          explanation="Sem evidência recuperada (ou erro): nenhuma confiança no suporte.")

    cited = [s for s in sources if s.cited] or sources
    factors: dict[str, Optional[float]] = {}

    # (a) cobertura
    if status == Status.RETRIEVAL_ONLY:
        factors["cobertura"] = None
    else:
        factors["cobertura"] = (sum(c.supported for c in claims) / len(claims)) if claims else 0.0

    # (b) procedência
    def prov(s: Source) -> float:
        if not (s.doc_id and s.chunk_id):
            return 0.0
        return 1.0 if s.evidence_in_chunk or s.relation is None else 0.5
    factors["procedencia"] = sum(prov(s) for s in cited) / len(cited)

    # (c) relevância semântica (não é verdade factual!)
    sims = sorted((s.similarity for s in seeds), reverse=True)[:3] if mode == Mode.GRAPHRAG and seeds \
        else sorted((s.score for s in sources), reverse=True)[:3]
    raw_rel = sum(sims) / len(sims) if sims else 0.0
    factors["relevancia"] = _clamp((raw_rel - REL_LO) / (REL_HI - REL_LO))

    # (d) consistência
    n_docs = len({s.doc_id for s in cited if s.doc_id})
    doc_part = min(1.0, n_docs / 2)
    if mode == Mode.GRAPHRAG:
        by_key = {(e.source, e.type, e.target, e.chunk_id): e for e in edges}
        cited_edges = [by_key[k] for s in cited if s.relation
                       for k in [(s.relation.source_id, s.relation.type, s.relation.target_id, s.chunk_id)]
                       if k in by_key]
        conn = _connected_fraction(edges, cited_edges, seeds)
        factors["consistencia"] = doc_part if conn is None else 0.5 * conn + 0.5 * doc_part
    else:
        factors["consistencia"] = doc_part

    available = {k: v for k, v in factors.items() if v is not None}
    wsum = sum(WEIGHTS[k] for k in available)
    base = sum(WEIGHTS[k] * v for k, v in available.items()) / wsum if wsum else 0.0

    penalties: dict[str, float] = {}
    if invalid_citations:
        penalties["citacoes_inexistentes"] = min(0.20, 0.10 * len(invalid_citations))
    n_unsup = sum(not c.supported for c in claims) if status != Status.RETRIEVAL_ONLY else 0
    if n_unsup:
        penalties["afirmacoes_sem_suporte"] = min(0.30, 0.10 * n_unsup)
    if cypher_status in (CypherStatus.FALLBACK, CypherStatus.REJECTED):
        penalties["fallback_text2cypher"] = 0.05
    if any("duplicata" in w.lower() for w in warnings):
        penalties["entidades_duplicadas"] = 0.05
    if "contradi" in (answer or "").lower():
        penalties["contradicao_nas_fontes"] = 0.10

    score = _clamp(base - sum(penalties.values()))
    cap = None
    if status == Status.INSUFFICIENT_EVIDENCE:
        cap = CAP_ABSTAIN
    elif status == Status.RETRIEVAL_ONLY:
        cap = CAP_RETRIEVAL_ONLY
    if cap is not None:
        score = min(score, cap)
    score = round(score, 3)

    nomes = {"cobertura": "afirmações com suporte", "procedencia": "procedência documental",
             "relevancia": "relevância semântica", "consistencia": "consistência (caminhos/documentos)"}
    partes = [f"{nomes[k]} {v:.0%}" for k, v in available.items()]
    if penalties:
        partes.append("penalidades: " + ", ".join(f"{k.replace('_', ' ')} −{v:.2f}" for k, v in penalties.items()))
    if cap is not None:
        partes.append("teto aplicado: " + ("abstenção" if status == Status.INSUFFICIENT_EVIDENCE else "sem LLM"))
    explanation = (f"Confiança {level_of(score)} ({score:.2f}) — heurística não calibrada, mede apoio em "
                   f"evidências e não probabilidade de acerto. " + "; ".join(partes) + ".")

    out_factors = {k: (round(v, 3) if v is not None else None) for k, v in factors.items()}
    out_factors.update({"pesos": WEIGHTS, "penalidades": penalties, "similaridade_bruta": round(raw_rel, 4),
                        "documentos_distintos": n_docs, "afirmacoes": len(claims),
                        "afirmacoes_suportadas": sum(c.supported for c in claims)})
    return Confidence(score=score, level=level_of(score), factors=out_factors, explanation=explanation)
