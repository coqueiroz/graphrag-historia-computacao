"""C1 — recuperação estruturada (sem Neo4j: FakeGateway sobre grafo.json + embedding fake)."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes_parte_c import FakeGateway, fake_embed  # noqa: E402

from parte_c import graph_store  # noqa: E402
from parte_c.config import Settings  # noqa: E402
from parte_c.retrieval import (duplicate_warnings, evidence_window, extract_name_terms,  # noqa: E402
                               retrieve_graph)
from parte_c.schemas import Node  # noqa: E402

S = Settings(min_seed_score=0.5)
BIG = Settings(min_seed_score=0.5, max_paths_per_seed=500, max_edges=400)


def run(q, settings=S, **kw):
    return retrieve_graph(q, gateway=FakeGateway(), embed_fn=fake_embed, settings=settings, **kw)


# ----------------------------------------------------------------------------- termos fulltext
def test_extrai_nomes_proprios_como_consulta_lucene_segura():
    assert extract_name_terms("Quem criou o Unix?") == '"Unix"'
    assert extract_name_terms("Que linguagem John McCarthy criou?") == '"John McCarthy"'
    assert extract_name_terms("Onde trabalhou John von Neumann?") == '"John von Neumann"'
    assert extract_name_terms("quem criou a linguagem c?") is None
    t = extract_name_terms('Quem é Dennis "Ritchie" \\ (C) OR *?')
    assert '\\' not in t and t.count('"') % 2 == 0          # aspas balanceadas, sem escape cru


def test_janela_de_evidencia_ignora_acentos_e_espacos():
    found, ctx = evidence_window("Texto antes.  A   Máquina Analítica foi criada. Depois.",
                                 "a maquina analitica foi criada", 200)
    assert found and "Analítica" in ctx
    assert evidence_window("abc", "xyz", 100) == (False, None)


# ----------------------------------------------------------------------------- recuperação
def test_pergunta_livre_devolve_nos_arestas_e_fontes_estruturadas():
    r = run("Quem criou o Unix?")
    assert any(s.id == "tecnologia:unix" for s in r.seeds)
    criou = [e for e in r.subgraph.edges if e.type == "CRIOU" and e.target == "tecnologia:unix"]
    assert {e.source for e in criou} >= {"pessoa:ken_thompson", "pessoa:dennis_ritchie"}
    ids = {n.id for n in r.subgraph.nodes}
    for e in r.subgraph.edges:                     # toda aresta aponta para nós presentes
        assert e.source in ids and e.target in ids
    assert [s.id for s in r.sources] == [f"F{i}" for i in range(1, len(r.sources) + 1)]
    unix_src = [s for s in r.sources if s.relation.target_id == "tecnologia:unix" and s.relation.type == "CRIOU"]
    # cada fonte aponta para o Documento dono do chunk (o fato pode vir do artigo do Unix ou do Ritchie)
    assert unix_src and all(s.title and s.doc_id == s.chunk_id.split("#")[0] and s.url for s in unix_src)
    assert all(s.evidence_in_chunk for s in unix_src)
    assert len(r.sources) == len(r.subgraph.edges) <= S.max_edges


def test_resultado_e_json_sem_embeddings_e_deterministico():
    a = run("Quem criou o Unix?").model_dump(mode="json")
    b = run("Quem criou o Unix?").model_dump(mode="json")
    assert a["subgraph"] == b["subgraph"] and a["sources"] == b["sources"]
    texto = json.dumps(a)
    assert "embedding" not in texto
    assert "<vetor" in texto or not a["queries"]   # vetor nunca vai inteiro para o trace


def test_multi_hop_conecta_duas_relacoes():
    """Resposta depende de unir DESENVOLVIDO_EM (Unix->Bell Labs) e LOCALIZADO_EM (Bell Labs->Murray Hill)."""
    r = run("Em que cidade fica a instituição onde o Unix foi desenvolvido?", settings=BIG, hops=2)
    keys = {(e.source, e.type, e.target) for e in r.subgraph.edges}
    assert ("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs") in keys
    assert ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill") in keys
    loc = next(e for e in r.subgraph.edges if e.type == "LOCALIZADO_EM" and e.source == "instituicao:bell_labs")
    assert loc.hop == 2 or any(s.id == "instituicao:bell_labs" for s in r.seeds)


def test_tres_saltos():
    r = run("Unix", settings=BIG, hops=3)
    assert max(e.hop for e in r.subgraph.edges) == 3


def test_semente_sem_vizinhanca_nao_quebra():
    fg = FakeGateway()
    isolada = next(e for i, e in fg.ents.items() if not fg.adj.get(i))
    r = retrieve_graph(isolada["name"], top_k=1, gateway=fg, embed_fn=fake_embed,
                       settings=Settings(min_seed_score=0.5, fulltext_seeds=0))
    assert r.seeds and not r.subgraph.edges
    assert any("sem vizinhança" in w for w in r.warnings)


def test_sem_sementes_devolve_vazio_com_aviso():
    r = run("xyzzy plugh", settings=Settings(min_seed_score=0.99, fulltext_seeds=0))
    assert not r.seeds and not r.sources and r.warnings


def test_hops_invalido():
    with pytest.raises(ValueError):
        run("Unix", hops=5)


def test_avisa_duplicatas_sem_fundir():
    nodes = [Node(id="pessoa:ken_thompson", name="Ken Thompson", label="Pessoa"),
             Node(id="pessoa:kenneth_thompson", name="Kenneth Thompson", label="Pessoa"),
             Node(id="instituicao:bell_labs", name="Bell Labs", label="Instituicao"),
             Node(id="instituicao:at_t_bell_labs", name="AT&T Bell Labs", label="Instituicao"),
             Node(id="pessoa:alan_turing", name="Alan Turing", label="Pessoa")]
    w = " ".join(duplicate_warnings(nodes))
    assert "kenneth_thompson" in w and "at_t_bell_labs" in w and "alan_turing" not in w


def test_ranking_usa_pagerank_so_como_desempate():
    from parte_c.retrieval import W_PAGERANK, W_PATH_SIM, W_SEED
    assert W_PAGERANK < W_PATH_SIM < W_SEED and abs(W_SEED + W_PATH_SIM + W_PAGERANK - 1) < 1e-9


# ----------------------------------------------------------------------------- somente leitura
WRITE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b", re.I)


def test_consultas_da_parte_c_sao_somente_leitura():
    for name in dir(graph_store):
        if name.endswith("_CYPHER"):
            assert not WRITE.search(getattr(graph_store, name)), name


class _Spy:
    """Driver falso que registra como as sessões são abertas."""
    def __init__(self):
        self.kwargs = []

    def session(self, **kw):
        self.kwargs.append(kw)
        spy = self

        class Sess:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute_read(self, fn):
                spy.used_execute_read = True
                return []

            def execute_write(self, fn):  # pragma: no cover
                raise AssertionError("escrita não permitida")
        return Sess()

    def close(self):
        pass


def test_gateway_abre_sessoes_em_modo_read():
    import neo4j
    spy = _Spy()
    gw = graph_store.Neo4jGateway(Settings(), driver=spy)
    gw.vector_seeds([0.0] * 384, 3, 0.5)
    gw.chunk_search([0.0] * 384, 3)
    assert spy.used_execute_read
    assert all(k["default_access_mode"] == neo4j.READ_ACCESS for k in spy.kwargs)


# ----------------------------------------------------------------------------- melhorias (2ª rodada)
def test_pistas_de_relacao_na_pergunta():
    from parte_c.retrieval import relation_cues
    assert relation_cues("Em que cidade fica a instituição onde trabalhava quem criou o Unix?") ==         ["CRIOU", "TRABALHOU_EM", "LOCALIZADO_EM"]
    assert relation_cues("Que prêmios recebeu Ken Thompson?") == ["RECEBEU"]
    assert relation_cues("Fale sobre o Unix") == []


def test_termos_compostos_em_minusculas_viram_candidatos_fulltext():
    from parte_c.retrieval import build_fulltext_query
    q = build_fulltext_query("Em que cidade fica a instituição onde trabalhava o criador da teoria da informação?")
    assert '"teoria da informação"' in q
    q2 = build_fulltext_query("Em que estado fica o instituto do criador da arquitetura de von Neumann?")
    assert '"arquitetura de von Neumann"' in q2
    assert build_fulltext_query("Que linguagem a linguagem C influenciou?").startswith('"C"^4')


def test_relation_match():
    from parte_c.retrieval import relation_match
    assert relation_match(["CRIOU", "TRABALHOU_EM"], ["CRIOU", "TRABALHOU_EM"]) == 1.0
    assert relation_match(["COLABOROU_COM"], ["CRIOU"]) == 0.0
    assert relation_match(["CRIOU", "COLABOROU_COM"], ["CRIOU"]) == 0.75


def test_ruido_limitado_quando_a_pergunta_cita_relacoes():
    s = Settings(min_seed_score=0.5, max_paths_per_seed=500, max_edges=30)
    r = run("Onde o Unix foi desenvolvido?", settings=s)
    fora = [e for e in r.subgraph.edges if e.type not in r.relation_cues]
    assert r.relation_cues == ["DESENVOLVIDO_EM"] and len(fora) <= 10
    assert ("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs") in {(e.source, e.type, e.target) for e in r.subgraph.edges}
