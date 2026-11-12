"""C4 (confiança) e C5 (baseline vetorial puro) — sem Neo4j e sem LLM real."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes_parte_c import FakeGateway, fake_embed  # noqa: E402

from parte_c import graph_store  # noqa: E402
from parte_c.baseline import retrieve_baseline  # noqa: E402
from parte_c.confidence import compute_confidence  # noqa: E402
from parte_c.config import Settings  # noqa: E402
from parte_c.schemas import (Claim, CypherStatus, Edge, Mode, RelationRef, Seed, Source,  # noqa: E402
                             Status)

S = Settings()


# ----------------------------------------------------------------------------- baseline
def test_baseline_usa_somente_chunks():
    gw = FakeGateway()
    r = retrieve_baseline("Quem criou o Unix?", gateway=gw, embed_fn=fake_embed, settings=S)
    assert gw.calls == ["chunk_search"]                       # nada de entidades/expansão/text2cypher
    assert r.mode == Mode.BASELINE and r.subgraph.edges == [] and r.seeds == []
    assert [s.id for s in r.sources] == [f"F{i}" for i in range(1, S.baseline_top_k + 1)]
    assert all(s.relation is None and s.title and s.chunk_id and s.quote for s in r.sources)
    assert any(s.doc_id == "unix" for s in r.sources)
    assert sum(len(s.quote) for s in r.sources) <= S.max_context_chars


def test_consulta_do_baseline_nao_usa_relacoes_de_dominio_nem_gds():
    q = graph_store.CHUNK_SEARCH_CYPHER
    assert "vec_chunk_embedding" in q and "TEM_CHUNK" in q
    for proibido in ["vec_entidade_embedding", "pagerank", "community", "CRIOU", "TRABALHOU_EM", "Entidade", "*"]:
        assert proibido not in q


def test_baseline_sem_resultado():
    gw = FakeGateway()
    gw.chunk_search = lambda *a, **k: []
    r = retrieve_baseline("x", gateway=gw, embed_fn=fake_embed, settings=S)
    assert not r.sources and r.warnings


# ----------------------------------------------------------------------------- confiança
def _src(i, doc, cited=True, ev=True, rel=True):
    return Source(id=f"F{i}", doc_id=doc, title=doc, chunk_id=f"{doc}#0", quote="q", cited=cited,
                  evidence_in_chunk=ev, score=0.85,
                  relation=RelationRef(source_id=f"a{i}", type="CRIOU", target_id=f"b{i}") if rel else None)


def _edges(n):
    return [Edge(source=f"a{i}", target=f"b{i}", type="CRIOU", chunk_id=f"d{i}#0") for i in range(1, n + 1)]


SEEDS = [Seed(id="a1", name="A", label="Pessoa", similarity=0.88), Seed(id="a2", name="B", label="Pessoa", similarity=0.86)]


def test_evidencia_forte_da_confianca_alta():
    srcs = [_src(1, "d1"), _src(2, "d2")]
    claims = [Claim(text="x", sources=["F1"], supported=True), Claim(text="y", sources=["F2"], supported=True)]
    c = compute_confidence(mode=Mode.GRAPHRAG, status=Status.ANSWERED, sources=srcs, claims=claims,
                           seeds=SEEDS, edges=_edges(2))
    assert c.level == "high" and c.score >= 0.75
    assert c.method == "heuristico_nao_calibrado" and "não probabilidade" in c.explanation
    assert set(c.factors) >= {"cobertura", "procedencia", "relevancia", "consistencia", "penalidades"}


def test_evidencia_fraca_da_confianca_baixa():
    srcs = [_src(1, "d1", ev=False)]
    claims = [Claim(text="x", sources=["F1"], supported=True), Claim(text="y", sources=["F1"], supported=False),
              Claim(text="z", sources=[], supported=False)]
    seeds = [Seed(id="a1", name="A", label="Pessoa", similarity=0.72)]
    c = compute_confidence(mode=Mode.GRAPHRAG, status=Status.ANSWERED, sources=srcs, claims=claims, seeds=seeds,
                           edges=_edges(1), invalid_citations=["F9"], cypher_status=CypherStatus.FALLBACK,
                           warnings=["Possível duplicata de entidade ..."])
    assert c.level == "low"
    assert set(c.factors["penalidades"]) == {"citacoes_inexistentes", "afirmacoes_sem_suporte",
                                             "fallback_text2cypher", "entidades_duplicadas"}


def test_sem_evidencia_confianca_zero():
    c = compute_confidence(mode=Mode.GRAPHRAG, status=Status.INSUFFICIENT_EVIDENCE, sources=[], claims=[],
                           seeds=[], edges=[])
    assert c.score == 0 and c.level == "low"


def test_abstencao_e_sem_llm_tem_teto():
    srcs = [_src(1, "d1"), _src(2, "d2")]
    claims = [Claim(text="x", sources=["F1"], supported=True)]
    ab = compute_confidence(mode=Mode.GRAPHRAG, status=Status.INSUFFICIENT_EVIDENCE, sources=srcs, claims=claims,
                            seeds=SEEDS, edges=_edges(2))
    ro = compute_confidence(mode=Mode.GRAPHRAG, status=Status.RETRIEVAL_ONLY, sources=srcs, claims=claims,
                            seeds=SEEDS, edges=_edges(2))
    assert ab.score <= 0.25 and ab.level == "low"
    assert ro.score <= 0.60 and ro.factors["cobertura"] is None


def test_baseline_usa_documentos_como_consistencia():
    srcs = [_src(1, "d1", rel=False), _src(2, "d1", rel=False)]
    claims = [Claim(text="x", sources=["F1"], supported=True)]
    c = compute_confidence(mode=Mode.BASELINE, status=Status.ANSWERED, sources=srcs, claims=claims, seeds=[], edges=[])
    assert c.factors["consistencia"] == 0.5 and c.factors["documentos_distintos"] == 1


def test_confianca_e_deterministica():
    kw = dict(mode=Mode.GRAPHRAG, status=Status.ANSWERED, sources=[_src(1, "d1")],
              claims=[Claim(text="x", sources=["F1"], supported=True)], seeds=SEEDS, edges=_edges(1))
    assert compute_confidence(**kw) == compute_confidence(**kw)
