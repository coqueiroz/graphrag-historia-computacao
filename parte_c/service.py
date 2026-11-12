"""
C6 — Contrato de integração para a Pessoa D.

    from parte_c import responder_pergunta
    r = responder_pergunta("Quem criou o Unix?", modo="graphrag")   # ou modo="baseline"
    r["status"], r["answer"], r["confidence"], r["sources"], r["subgraph"], r["generated_cypher"]

- Síncrona, entrada simples, retorno = dict serializável em JSON (schema `QAResponse` em schemas.py).
- Nunca levanta exceção para a UI: falhas viram status "error" com mensagem sanitizada em `warnings`.
- Nunca devolve senha, chave, embeddings, prompts internos ou stacktrace.
- Driver Neo4j e cliente LLM são criados uma vez e reaproveitados (chame `fechar()` ao encerrar).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from parte_c.config import Settings
from parte_c.schemas import (Claim, Confidence, CypherStatus, GeneratedCypher, Mode, QAResponse, RetrievalResult,
                             Status, Subgraph)

log = logging.getLogger("parte_c")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"gateway": None, "llm": None, "llm_loaded": False, "llm_error": None}
_UNSET = object()
MAX_QUESTION_CHARS = 1000


def _get_gateway(settings: Settings):
    with _LOCK:
        if _STATE["gateway"] is None:
            from parte_c.graph_store import Neo4jGateway
            gw = Neo4jGateway(settings)
            gw.verify()
            _STATE["gateway"] = gw
        return _STATE["gateway"]


def _get_llm(settings: Settings):
    with _LOCK:
        if not _STATE["llm_loaded"]:
            from parte_c.llm_adapter import LLMError, get_llm
            try:
                _STATE["llm"] = get_llm(settings)
            except LLMError as e:
                _STATE["llm"], _STATE["llm_error"] = None, str(e)
            _STATE["llm_loaded"] = True
        return _STATE["llm"], _STATE["llm_error"]


def fechar() -> None:
    """Fecha a conexão com o Neo4j (opcional; útil ao encerrar a aplicação)."""
    with _LOCK:
        if _STATE["gateway"] is not None:
            _STATE["gateway"].close()
        _STATE.update({"gateway": None, "llm": None, "llm_loaded": False, "llm_error": None})


def _error_response(question: str, mode: str, msg: str, t0: float) -> dict:
    try:
        m = Mode(mode)
    except ValueError:
        m = Mode.GRAPHRAG
    return QAResponse(
        question=question, mode=m, status=Status.ERROR,
        answer="Não foi possível responder agora. " + msg,
        confidence=Confidence(score=0.0, level="low", explanation="Erro: nenhuma evidência avaliada."),
        latency_ms=round((time.perf_counter() - t0) * 1000, 1), warnings=[msg],
    ).model_dump(mode="json")


def _trim_to_used(retrieval: RetrievalResult, used_ids: set[str]) -> RetrievalResult:
    """Mantém só as fontes enviadas ao LLM e o subgrafo correspondente a elas."""
    sources = [s for s in retrieval.sources if s.id in used_ids]
    keys = {(s.relation.source_id, s.relation.type, s.relation.target_id, s.chunk_id)
            for s in sources if s.relation}
    edges = [e for e in retrieval.subgraph.edges if e.key() in keys]
    node_ids = {e.source for e in edges} | {e.target for e in edges} | {s.id for s in retrieval.seeds}
    nodes = [n for n in retrieval.subgraph.nodes if n.id in node_ids]
    out = retrieval.model_copy(deep=True)
    out.sources, out.subgraph = sources, Subgraph(nodes=nodes, edges=edges)
    return out


def _graphrag_retrieval(question, vetor, strategy, top_k, hops, gateway, llm, settings):
    from parte_c.retrieval import build_sources, find_seeds, merge_results, retrieve_graph
    from parte_c.text_to_cypher import text_to_cypher

    if strategy not in ("vector", "text2cypher", "auto"):
        raise ValueError("estrategia deve ser 'vector', 'text2cypher' ou 'auto'")

    if strategy == "vector":
        r = retrieve_graph(question, top_k, hops, gateway=gateway, settings=settings, vetor=vetor)
        return r, None

    if strategy == "text2cypher":
        trace: list = []
        seeds = find_seeds(question, vetor, gateway, settings, top_k, trace)
        t2c = text_to_cypher(question, llm=llm, gateway=gateway, settings=settings, candidates=seeds, vetor=vetor)
        if t2c.ok:
            from parte_c.retrieval import duplicate_warnings
            nodes = sorted(t2c.nodes.values(), key=lambda n: n.id)
            sources, warns = build_sources(t2c.edges, t2c.provenance, t2c.nodes, settings)
            r = RetrievalResult(question=question, mode=Mode.GRAPHRAG, strategy="text2cypher", hops=None,
                                seeds=seeds, subgraph=Subgraph(nodes=nodes, edges=t2c.edges), sources=sources,
                                queries=trace + t2c.queries,
                                warnings=t2c.warnings + warns + duplicate_warnings(nodes))
        else:
            r = retrieve_graph(question, top_k, hops, gateway=gateway, settings=settings, vetor=vetor)
            r.strategy = "vector_cypher (fallback do text2cypher)"
            r.queries = trace + t2c.queries + r.queries
            r.warnings.insert(0, f"Text-to-Cypher não utilizado ({t2c.generated.error}); usada a busca vetorial + grafo (Q8).")
            if t2c.generated.status == CypherStatus.EXECUTED:
                t2c.generated.status = CypherStatus.FALLBACK
        r.generated_cypher = t2c.generated
        return r, t2c

    # auto: busca vetorial+grafo sempre; Text-to-Cypher adicionado quando há LLM
    r = retrieve_graph(question, top_k, hops, gateway=gateway, settings=settings, vetor=vetor)
    if llm is None:
        return r, None
    t2c = text_to_cypher(question, llm=llm, gateway=gateway, settings=settings, candidates=r.seeds, vetor=vetor)
    if t2c.ok:
        merged = merge_results(r, t2c.edges, t2c.nodes, t2c.provenance, settings)
        merged.strategy = "hybrid (text2cypher + vector_cypher)"
        merged.queries = r.queries + t2c.queries
        merged.warnings += t2c.warnings
        r = merged
    else:
        r.queries += t2c.queries
        if t2c.generated.status == CypherStatus.REJECTED:
            r.warnings.append(f"Consulta gerada rejeitada pela política somente-leitura ({t2c.generated.error}); "
                              "usada só a busca vetorial + grafo.")
    r.generated_cypher = t2c.generated
    return r, t2c


def responder_pergunta(pergunta: str, modo: str = "graphrag", *, estrategia: Optional[str] = None,
                       top_k: Optional[int] = None, hops: Optional[int] = None, incluir_trace: bool = False,
                       gateway=None, llm=_UNSET, embed_fn: Optional[Callable[[str], list[float]]] = None,
                       settings: Optional[Settings] = None) -> dict:
    """Responde uma pergunta em português.

    modo:        "graphrag" (padrão) ou "baseline" (RAG vetorial puro, para comparação)
    estrategia:  só no graphrag — "auto" (padrão: vetor+grafo e, se houver LLM, Text-to-Cypher),
                 "vector" (só vetor+grafo, como a Q8) ou "text2cypher" (com fallback para vetor+grafo)
    top_k, hops: sobrepõem o .env (hops 1..3)
    incluir_trace: inclui consultas Cypher executadas (com parâmetros não sensíveis) e tempos
    gateway/llm/embed_fn/settings: injeção de dependências (testes); a UI não precisa passar nada.
    """
    t0 = time.perf_counter()
    pergunta = (pergunta or "").strip()
    if not pergunta:
        return _error_response(pergunta, modo, "Pergunta vazia.", t0)
    if len(pergunta) > MAX_QUESTION_CHARS:
        return _error_response(pergunta[:200], modo, f"Pergunta longa demais (máx. {MAX_QUESTION_CHARS} caracteres).", t0)
    if modo not in ("graphrag", "baseline"):
        return _error_response(pergunta, "graphrag", "modo deve ser 'graphrag' ou 'baseline'.", t0)
    if hops is not None and not (isinstance(hops, int) and 1 <= hops <= 3):
        return _error_response(pergunta, modo, "hops deve ser 1, 2 ou 3.", t0)

    settings = settings or Settings.from_env()
    warnings: list[str] = []
    try:
        gw = gateway or _get_gateway(settings)
    except Exception as e:  # noqa: BLE001
        from parte_c.text_to_cypher import sanitize_error
        log.warning("Neo4j indisponível: %s", sanitize_error(e))
        return _error_response(pergunta, modo, "Banco Neo4j indisponível (verifique se a instância está ligada e o .env).", t0)

    if llm is _UNSET:
        llm, llm_err = _get_llm(settings)
        if llm_err:
            warnings.append(f"LLM indisponível: {llm_err}. Respondendo só com as evidências.")

    try:
        from parte_c.confidence import compute_confidence
        from parte_c.generation import build_context, cited_ids, generate_answer, extractive_answer
        from parte_c.llm_adapter import LLMError

        t_ret = time.perf_counter()
        if embed_fn is None:
            from embeddings import embed_query as embed_fn  # Pessoa B
        vetor = embed_fn(pergunta)
        t_emb = time.perf_counter()

        if modo == "baseline":
            from parte_c.baseline import retrieve_baseline
            retrieval = retrieve_baseline(pergunta, top_k, gateway=gw, settings=settings, vetor=vetor)
        else:
            retrieval, _ = _graphrag_retrieval(pergunta, vetor, estrategia or settings.strategy, top_k, hops,
                                               gw, llm, settings)
        t_ret_end = time.perf_counter()

        context, used = build_context(retrieval, settings)
        if len(used) < len(retrieval.sources):
            warnings.append(f"{len(retrieval.sources) - len(used)} fonte(s) de menor relevância ficaram fora do "
                            f"contexto (teto de {settings.max_context_chars} caracteres).")
        retrieval = _trim_to_used(retrieval, {s.id for s in used})

        try:
            gen = generate_answer(pergunta, retrieval, llm, settings, used_sources=retrieval.sources, context=context)
        except LLMError as e:
            warnings.append(f"Falha do LLM ({e}); exibindo só as evidências recuperadas.")
            if retrieval.sources:
                ans, claims = extractive_answer(pergunta, retrieval.sources, retrieval.mode)
                gen = {"status": Status.RETRIEVAL_ONLY, "answer": ans, "claims": claims, "warnings": [], "llm": {}}
            else:
                from parte_c.generation import ABSTAIN_MSG
                gen = {"status": Status.INSUFFICIENT_EVIDENCE, "answer": ABSTAIN_MSG, "claims": [],
                       "warnings": [], "llm": {}}
        t_gen = time.perf_counter()

        status: Status = gen["status"]
        answer: str = gen["answer"]
        claims: list[Claim] = gen["claims"]
        cited = set(cited_ids(answer))
        for s in retrieval.sources:
            s.cited = s.id in cited

        all_warnings = list(dict.fromkeys(warnings + retrieval.warnings + gen.get("warnings", [])))
        gc: GeneratedCypher = retrieval.generated_cypher
        if modo == "baseline":
            gc = GeneratedCypher(status=CypherStatus.NOT_APPLICABLE)
        # o fallback só penaliza quando o Text-to-Cypher era a estratégia pedida (no "auto" ele é opcional)
        t2c_pedido = modo == "graphrag" and (estrategia or settings.strategy) == "text2cypher"
        conf = compute_confidence(
            mode=retrieval.mode, status=status, sources=retrieval.sources, claims=claims, seeds=retrieval.seeds,
            edges=retrieval.subgraph.edges, answer=answer, invalid_citations=gen.get("invalid_citations"),
            cypher_status=gc.status if t2c_pedido else None, warnings=all_warnings)

        resp = QAResponse(
            question=pergunta, mode=retrieval.mode, status=status, answer=answer, confidence=conf,
            sources=retrieval.sources, subgraph=retrieval.subgraph, generated_cypher=gc,
            retrieval={
                "strategy": retrieval.strategy, "hops": retrieval.hops,
                "relation_cues": retrieval.relation_cues,
                "top_k": top_k or (settings.baseline_top_k if modo == "baseline" else settings.top_k),
                "seeds": [s.model_dump() for s in retrieval.seeds],
                "n_sources": len(retrieval.sources), "n_edges": len(retrieval.subgraph.edges),
            },
            claims=claims, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            llm=gen.get("llm") or {}, warnings=all_warnings,
        )
        if incluir_trace:
            resp.trace = {
                "config": settings.public(),
                "tempos_ms": {"embedding": round((t_emb - t_ret) * 1000, 1),
                              "recuperacao": round((t_ret_end - t_emb) * 1000, 1),
                              "geracao": round((t_gen - t_ret_end) * 1000, 1)},
                "consultas": [q.model_dump() for q in retrieval.queries],
                "contexto_chars": len(context),
            }
        return resp.model_dump(mode="json")
    except Exception as e:  # noqa: BLE001 - a UI nunca recebe stacktrace
        from parte_c.text_to_cypher import sanitize_error
        log.exception("erro em responder_pergunta")
        return _error_response(pergunta, modo, f"Erro interno ({sanitize_error(e)}).", t0)
