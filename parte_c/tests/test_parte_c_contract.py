"""C6 — contrato `responder_pergunta` (JSON estável para a Pessoa D) e smoke test da CLI, sem Neo4j/LLM reais."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes_parte_c import FakeGateway, fake_embed  # noqa: E402

from parte_c.config import Settings  # noqa: E402
from parte_c.llm_adapter import FakeLLM, LLMUnavailable  # noqa: E402
from parte_c.service import responder_pergunta  # noqa: E402

S = Settings(min_seed_score=0.5, strategy="auto")
CAMPOS = {"question", "mode", "status", "answer", "confidence", "sources", "subgraph", "generated_cypher",
          "retrieval", "claims", "latency_ms", "llm", "warnings", "trace"}


def ask(q="Quem criou o Unix?", modo="graphrag", llm=None, gw=None, **kw):
    return responder_pergunta(q, modo, gateway=gw or FakeGateway(), llm=llm, embed_fn=fake_embed, settings=S, **kw)


def answer_from_context(system, user, schema):
    """LLM fake que 'lê' o contexto e cita as fontes de CRIOU -> Unix que existirem."""
    import re
    ids = re.findall(r"\[(F\d+)\] fato: ([^\n]+?) \(pessoa:[^)]+\) -\[CRIOU\]-> Unix", user)
    parts = [f"{nome} criou o Unix [{fid}]." for fid, nome in ids[:2]]
    return {"status": "answered", "answer": " ".join(parts),
            "claims": [{"text": f"{nome} criou o Unix", "sources": [fid]} for fid, nome in ids[:2]]}


def test_contrato_json_completo_e_serializavel():
    r = ask(llm=FakeLLM([{"can_answer": False, "query": "", "parameters": [], "rationale": "-"}, answer_from_context]),
            incluir_trace=True)
    assert set(r) == CAMPOS
    json.dumps(r)                                                   # serializável
    assert r["status"] == "answered" and r["mode"] == "graphrag"
    assert set(r["confidence"]) == {"score", "level", "method", "factors", "explanation"}
    assert r["confidence"]["method"] == "heuristico_nao_calibrado"
    src_ids = {s["id"] for s in r["sources"]}
    for s in r["sources"]:
        assert set(s) >= {"id", "doc_id", "title", "url", "chunk_id", "quote", "relation", "cited"}
    assert any(s["cited"] for s in r["sources"])
    import re
    assert set(re.findall(r"F\d+", r["answer"])) <= src_ids        # toda citação aponta para uma fonte
    # subgrafo corresponde exatamente às fontes usadas
    edge_keys = {(e["source"], e["type"], e["target"], e["chunk_id"]) for e in r["subgraph"]["edges"]}
    src_keys = {(s["relation"]["source_id"], s["relation"]["type"], s["relation"]["target_id"], s["chunk_id"])
                for s in r["sources"]}
    assert edge_keys == src_keys
    node_ids = {n["id"] for n in r["subgraph"]["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in r["subgraph"]["edges"])
    assert r["generated_cypher"]["status"] == "fallback"
    texto = json.dumps(r)
    for proibido in ["NEO4J_PASSWORD", "REGRAS OBRIGATÓRIAS", "Traceback"]:
        assert proibido not in texto

    def sem_vetores(x):                                             # nenhum embedding (lista longa de floats)
        if isinstance(x, list):
            assert not (len(x) > 50 and all(isinstance(v, float) for v in x))
            for v in x:
                sem_vetores(v)
        elif isinstance(x, dict):
            for v in x.values():
                sem_vetores(v)
    sem_vetores(r)
    assert r["trace"]["consultas"] and all("<vetor" in json.dumps(q["parameters"]) or "vetor" not in q["parameters"]
                                           for q in r["trace"]["consultas"])


def test_sem_llm_retorna_retrieval_only():
    r = ask(llm=None)
    assert r["status"] == "retrieval_only" and r["sources"] and r["confidence"]["score"] <= 0.6
    assert r["generated_cypher"]["status"] == "not_applicable"


def test_llm_indisponivel_degrada_para_evidencias():
    r = ask(llm=FakeLLM([LLMUnavailable("sem chave"), LLMUnavailable("sem chave")]))
    assert r["status"] == "retrieval_only" and any("Falha do LLM" in w for w in r["warnings"])


def test_baseline_mesmo_contrato():
    r = ask(modo="baseline", llm=None)
    assert set(r) == CAMPOS and r["mode"] == "baseline"
    assert r["subgraph"] == {"nodes": [], "edges": []}
    assert r["generated_cypher"]["status"] == "not_applicable" and r["generated_cypher"]["query"] is None
    assert all(s["relation"] is None for s in r["sources"])


def test_pergunta_sem_suporte_abstem():
    gw = FakeGateway()
    gw.vector_seeds = lambda *a, **k: []
    gw.fulltext_seeds = lambda *a, **k: []
    llm = FakeLLM([])
    r = ask("Qual é a capital da Austrália?", llm=llm, gw=gw, estrategia="vector")
    assert r["status"] == "insufficient_evidence" and r["confidence"]["level"] == "low" and not llm.calls


def test_entradas_invalidas_viram_erro_estruturado():
    for q, modo, kw in [("", "graphrag", {}), ("x" * 2000, "graphrag", {}), ("ok", "outro", {}),
                        ("ok", "graphrag", {"hops": 9})]:
        r = responder_pergunta(q, modo, gateway=FakeGateway(), llm=None, embed_fn=fake_embed, settings=S, **kw)
        assert r["status"] == "error" and r["warnings"] and set(r) == CAMPOS


def test_excecao_interna_nao_vaza_stacktrace():
    gw = FakeGateway()

    def boom(*a, **k):
        raise RuntimeError("falhou em bolt://neo4j:segredo@host")
    gw.expand = boom
    r = ask(gw=gw, estrategia="vector")
    assert r["status"] == "error" and "segredo" not in json.dumps(r) and "Traceback" not in json.dumps(r)


def test_estrategia_text2cypher_executada():
    t2c = {"can_answer": True, "rationale": "criadores do Unix", "parameters": [{"name": "t", "value": "tecnologia:unix"}],
           "query": "MATCH p = (pe:Pessoa)-[:CRIOU]->(t:Entidade {id: $t}) RETURN pe.name AS criador, "
                    "[r IN relationships(p) | {source_id: startNode(r).id, type: type(r), target_id: endNode(r).id, "
                    "chunk_id: r.chunk_id}] AS evidencias"}
    rows = [{"criador": "Ken Thompson", "evidencias": [
        {"source_id": "pessoa:ken_thompson", "type": "CRIOU", "target_id": "tecnologia:unix", "chunk_id": None}]}]
    r = ask(llm=FakeLLM([t2c, answer_from_context]), gw=FakeGateway(generated_rows=rows), estrategia="text2cypher")
    assert r["generated_cypher"]["status"] == "executed" and r["retrieval"]["strategy"] == "text2cypher"
    assert r["status"] == "answered" and all(e["origin"] == "text2cypher" for e in r["subgraph"]["edges"])


def test_estrategia_auto_mescla_text2cypher_na_frente():
    t2c = {"can_answer": True, "rationale": "x", "parameters": [{"name": "t", "value": "tecnologia:unix"}],
           "query": "MATCH p = (t:Entidade {id: $t})-[:DESENVOLVIDO_EM]->(i:Instituicao)-[:LOCALIZADO_EM]->(l:Local) "
                    "RETURN l.name AS cidade, [r IN relationships(p) | {source_id: startNode(r).id, type: type(r), "
                    "target_id: endNode(r).id, chunk_id: r.chunk_id}] AS evidencias"}
    rows = [{"cidade": "Murray Hill", "evidencias": [
        {"source_id": "tecnologia:unix", "type": "DESENVOLVIDO_EM", "target_id": "instituicao:bell_labs", "chunk_id": None},
        {"source_id": "instituicao:bell_labs", "type": "LOCALIZADO_EM", "target_id": "local:murray_hill", "chunk_id": None}]}]
    r = ask("Em que cidade fica a instituição onde o Unix foi desenvolvido?", llm=FakeLLM([t2c, answer_from_context]),
            gw=FakeGateway(generated_rows=rows))
    assert r["retrieval"]["strategy"].startswith("hybrid")
    assert r["sources"][1]["relation"]["type"] == "LOCALIZADO_EM"
    assert r["subgraph"]["edges"][0]["origin"] == "text2cypher"
    assert len({(e["source"], e["type"], e["target"], e["chunk_id"]) for e in r["subgraph"]["edges"]}) == \
        len(r["subgraph"]["edges"])                                 # sem duplicatas após a mescla


def test_cli_smoke_sem_llm(monkeypatch, capsys):
    from parte_c import cli, service
    real = service.responder_pergunta
    monkeypatch.setattr(service, "responder_pergunta",
                        lambda q, m, **kw: real(q, m, **{**kw, "gateway": FakeGateway(), "embed_fn": fake_embed,
                                                         "settings": S}))
    code = cli.main(["-q", "Quem criou o Unix?", "--no-llm", "--mode", "both", "--show-trace"])
    out = capsys.readouterr().out
    assert code == 0 and "[GRAPHRAG]" in out and "[BASELINE]" in out and "[F1]" in out and "Trace" in out
