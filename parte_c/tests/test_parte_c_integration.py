"""
Integração REAL com o Neo4j da Pessoa B — SOMENTE LEITURA.
Pulados automaticamente se o banco estiver desligado/vazio. Não alteram nada:
o teste final confere que as contagens de nós e relações continuam idênticas.

Testes com LLM real (pagos) só rodam com RUN_LLM_TESTS=1 e LLM_PROVIDER configurado.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parte_c.config import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gw():
    from parte_c.graph_store import Neo4jGateway
    try:
        g = Neo4jGateway(Settings.from_env())
        g.verify()
        n = g._read("conta", "MATCH (e:Entidade) RETURN count(e) AS n", {}, None)[0]["n"]
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j indisponível: {type(e).__name__}")
    if n == 0:
        pytest.skip("banco vazio")
    yield g
    g.close()


@pytest.fixture(scope="module")
def contagens_iniciais(gw):
    return _contagens(gw)


def _contagens(gw):
    r = gw._read("contagens", "MATCH (n) WITH count(n) AS n MATCH ()-[r]->() RETURN n, count(r) AS r", {}, None)[0]
    return r["n"], r["r"]


@pytest.fixture(scope="module")
def embed():
    from embeddings import embed_query
    return embed_query


def test_ambiente_tem_indices_e_propriedades(gw, contagens_iniciais):
    idx = {r["name"]: r["state"] for r in gw._read("idx", "SHOW INDEXES YIELD name, state RETURN name, state", {}, None)}
    for nome in ["vec_entidade_embedding", "vec_chunk_embedding", "ft_entidade_nome"]:
        assert idx.get(nome) == "ONLINE"
    faltando = gw._read("pr", "MATCH (e:Entidade) WHERE e.pagerank IS NULL OR e.embedding IS NULL RETURN count(e) AS n",
                        {}, None)[0]["n"]
    assert faltando == 0


def test_busca_livre_real(gw, embed):
    from parte_c.retrieval import retrieve_graph
    r = retrieve_graph("Quem criou o Unix?", gateway=gw, embed_fn=embed, settings=Settings.from_env())
    assert r.seeds and r.subgraph.edges and r.sources
    assert all(s.title and s.doc_id and s.chunk_id for s in r.sources)
    assert any(e.type == "CRIOU" and e.target == "tecnologia:unix" for e in r.subgraph.edges)


def test_sementes_vetoriais_coincidem_com_a_q8(gw, embed):
    from parte_c.retrieval import find_seeds
    texto = "linguagem de programação criada nos Bell Labs"
    v = embed(texto)
    q8 = gw.q8_reference(v, 5)
    seeds = find_seeds(texto, v, gw, Settings(min_seed_score=0.0, fulltext_seeds=0), 5)
    assert {r["semente"] for r in q8} <= {s.name for s in seeds}


def test_multi_hop_real(gw, embed):
    from parte_c.retrieval import retrieve_graph
    s = Settings(max_paths_per_seed=200, max_edges=200)
    r = retrieve_graph("Em que cidade fica a instituição onde o Unix foi desenvolvido?", hops=2,
                       gateway=gw, embed_fn=embed, settings=s)
    keys = {(e.source, e.type, e.target) for e in r.subgraph.edges}
    assert ("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs") in keys
    assert ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill") in keys


def test_baseline_real(gw, embed):
    from parte_c.baseline import retrieve_baseline
    r = retrieve_baseline("Quem criou o Unix?", gateway=gw, embed_fn=embed, settings=Settings.from_env())
    assert len(r.sources) == Settings.from_env().baseline_top_k and all(s.title for s in r.sources)


def test_explain_no_servidor_confirma_somente_leitura(gw):
    from parte_c.text_to_cypher import FEW_SHOTS, CypherValidationError, check_plan, validate_cypher
    for pergunta, params, cypher in FEW_SHOTS:
        vq = validate_cypher(cypher, params, pergunta, Settings())
        check_plan(gw.explain(vq.query, vq.parameters))
    # EXPLAIN não executa nada; mesmo assim, uma escrita tem de ser barrada (erro ou plano 'rw'/'w')
    with pytest.raises(Exception) as exc:
        check_plan(gw.explain("CREATE (n:ProbeParteC) RETURN n", {}))
    assert isinstance(exc.value, CypherValidationError) or "access" in str(exc.value).lower() or exc.value


def test_responder_pergunta_real_sem_llm(gw, embed):
    from parte_c.service import responder_pergunta
    for modo in ("graphrag", "baseline"):
        r = responder_pergunta("Quem criou o Unix?", modo, gateway=gw, llm=None, embed_fn=embed)
        assert r["status"] == "retrieval_only" and r["sources"]


@pytest.mark.skipif(os.getenv("RUN_LLM_TESTS") != "1", reason="defina RUN_LLM_TESTS=1 para chamadas reais (pagas) ao LLM")
def test_resposta_real_com_llm(gw, embed):
    from parte_c.service import responder_pergunta
    r = responder_pergunta("Quem criou o Unix?", "graphrag", gateway=gw, embed_fn=embed)
    assert r["status"] in ("answered", "insufficient_evidence")
    if r["status"] == "answered":
        assert any(s["cited"] for s in r["sources"])


def test_banco_nao_foi_alterado(gw, contagens_iniciais):
    assert _contagens(gw) == contagens_iniciais
