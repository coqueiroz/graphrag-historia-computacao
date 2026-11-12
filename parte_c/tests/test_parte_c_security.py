"""C2 — Text-to-Cypher: validação read-only, rejeição de ataques, correção única e fallback."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes_parte_c import FakeGateway  # noqa: E402

from consultas_referencia import CONSULTAS  # noqa: E402
from parte_c.config import Settings  # noqa: E402
from parte_c.llm_adapter import FakeLLM, LLMUnavailable  # noqa: E402
from parte_c.schemas import CypherStatus, Seed  # noqa: E402
from parte_c.text_to_cypher import (FEW_SHOTS, CypherValidationError, build_t2c_system,  # noqa: E402
                                    check_plan, sanitize_error, text_to_cypher, validate_cypher)

S = Settings()
Q = "Quem criou o Unix?"


def ok(query, params=None, question=Q, auto=None):
    return validate_cypher(query, params or {}, question, S, auto_params=auto)


# ----------------------------------------------------------------------------- consultas legítimas
@pytest.mark.parametrize("pergunta,params,cypher", FEW_SHOTS)
def test_exemplos_few_shot_passam_na_validacao(pergunta, params, cypher):
    vq = ok(cypher, params, question=pergunta)
    assert vq.query.rstrip().endswith("LIMIT 50")


@pytest.mark.parametrize("nome", ["Q1", "Q2", "Q3", "Q4", "Q5", "Q7", "Q8", "Q9", "Q10"])
def test_consultas_de_referencia_da_pessoa_b_passam(nome):
    q = CONSULTAS[nome]
    ok(q["cypher"], dict(q["params"]), question="pergunta qualquer", auto={"vetor": [0.0] * 384, "k": 5})


def test_q6_original_e_rejeitada_por_exceder_saltos():
    q = CONSULTAS["Q6"]
    with pytest.raises(CypherValidationError, match="longo demais"):
        ok(q["cypher"], q["params"], question="x")


def test_limit_e_imposto():
    assert ok("MATCH (n:Pessoa) RETURN n.name").query.endswith("LIMIT 50")
    assert ok("MATCH (n:Pessoa) RETURN n.name LIMIT 1000").query.endswith("LIMIT 50")
    assert ok("MATCH (n:Pessoa) RETURN n.name LIMIT 7").query.endswith("LIMIT 7")
    vq = ok("MATCH (n:Pessoa) RETURN n.name LIMIT $lim", {"lim": 999999})
    assert vq.parameters["lim"] == 50


def test_valores_com_aspas_e_caracteres_especiais_vao_por_parametro():
    q = "Quem é O'Brien \"o Hacker\" (C++)?"
    vq = ok("MATCH (e:Entidade) WHERE e.name = $nome RETURN e.name", {"nome": "O'Brien \"o Hacker\" (C++)"}, question=q)
    assert vq.parameters == {"nome": "O'Brien \"o Hacker\" (C++)"}


# ----------------------------------------------------------------------------- ataques
ATAQUES = [
    "MATCH (n) DETACH DELETE n",
    "match (n) detach delete n return count(n)",
    "CREATE (n:Pessoa {id: 'x'}) RETURN n",
    "MATCH (n) SET n.name = 'x' RETURN n",
    "MATCH (n) MERGE (m:Pessoa {id: 'x'}) RETURN m",
    "MATCH (n) REMOVE n:Pessoa RETURN n",
    "MATCH (n) RETURN n; MATCH (m) DELETE m",
    "LOAD CSV FROM 'http://evil' AS l RETURN l",
    "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {}) YIELD batches RETURN batches",
    "CALL dbms.security.createUser('x', 'y', false) RETURN 1",
    "CALL db.create.setNodeVectorProperty(n, 'embedding', $v) RETURN 1",
    "MATCH (n) CALL { WITH n DETACH DELETE n } RETURN 1",
    "MATCH (n) FOREACH (x IN [1] | SET n.a = 1) RETURN n",
    "MATCH (n) RETURN n // comentário",
    "MATCH (n:`Pessoa`) RETURN n",
    "MATCH (n:Animal) RETURN n",
    "MATCH p = (a)-[*]-(b) RETURN p",
    "MATCH p = (a)-[:CRIOU*1..10]-(b) RETURN p",
    "MATCH (n:Entidade) RETURN n.embedding",
    "MATCH (n) RETURN apoc.text.join(['a'], 'b')",
    "USE system SHOW USERS",
    "DROP INDEX vec_chunk_embedding",
    "MATCH (n) RETURN n UNION MATCH (m) RETURN m",
    "MATCH (n) RETURN n.name AS x, $naoinformado AS y",
    "SHOW DATABASES",
    "MATCH (n) WITH n RETURN n.senha",
]


@pytest.mark.parametrize("query", ATAQUES)
def test_consultas_perigosas_sao_rejeitadas(query):
    with pytest.raises(CypherValidationError):
        ok(query)


def test_valor_da_pergunta_como_literal_e_rejeitado():
    with pytest.raises(CypherValidationError, match="literal"):
        ok("MATCH (p:Pessoa) WHERE p.name = 'Ken Thompson' RETURN p.name", question="O que Ken Thompson criou?")


def test_plano_do_explain_precisa_ser_somente_leitura():
    check_plan({"query_type": "r", "operators": ["ProduceResults", "NodeIndexSeek"]})
    with pytest.raises(CypherValidationError):
        check_plan({"query_type": "rw", "operators": []})
    with pytest.raises(CypherValidationError):
        check_plan({"query_type": "r", "operators": ["SetProperty"]})


def test_erro_sanitizado_nao_vaza_credenciais():
    msg = sanitize_error(RuntimeError("falha em bolt://neo4j:segredo@localhost:7687 password=abc\nstack..."))
    assert "segredo" not in msg and "abc" not in msg and "stack" not in msg


def test_prompt_usa_esquema_real_de_contracts():
    from contracts import RELATION_SCHEMA
    p = build_t2c_system(S)
    for rel in RELATION_SCHEMA:
        assert rel in p
    assert "Pessoa|Instituicao -> Tecnologia|Conceito|Premio" in p          # CRIOU, como no contrato


# ----------------------------------------------------------------------------- fluxo completo
CAND = [Seed(id="tecnologia:unix", name="Unix", label="Tecnologia", similarity=0.9)]
Q1_LIKE = {
    "can_answer": True,
    "query": "MATCH p = (t:Entidade {id: $tecnologia})<-[:CRIOU]-(pessoa:Pessoa)-[:TRABALHOU_EM]->(inst:Instituicao)"
             "-[:LOCALIZADO_EM]->(local:Local) RETURN pessoa.name AS criador, local.name AS cidade, "
             "[r IN relationships(p) | {source_id: startNode(r).id, type: type(r), target_id: endNode(r).id, "
             "chunk_id: r.chunk_id}] AS evidencias",
    "parameters": [{"name": "tecnologia", "value": "tecnologia:unix"}],
    "rationale": "criador do Unix -> instituição -> cidade",
}
ROWS = [{"criador": "Ken Thompson", "cidade": "Murray Hill", "evidencias": [
    {"source_id": "pessoa:ken_thompson", "type": "CRIOU", "target_id": "tecnologia:unix", "chunk_id": None},
    {"source_id": "pessoa:ken_thompson", "type": "TRABALHOU_EM", "target_id": "instituicao:bell_labs", "chunk_id": None},
    {"source_id": "instituicao:bell_labs", "type": "LOCALIZADO_EM", "target_id": "local:murray_hill", "chunk_id": None},
    {"source_id": "pessoa:ken_thompson", "type": "CRIOU", "target_id": "tecnologia:windows", "chunk_id": None},
]}]


def t2c(llm, gw, question="Em que cidade fica a instituição onde trabalhava quem criou o Unix?"):
    return text_to_cypher(question, llm=llm, gateway=gw, settings=S, candidates=CAND, vetor=[0.0] * 384)


def test_fluxo_valido_tres_saltos_executa_e_verifica_arestas():
    gw = FakeGateway(generated_rows=ROWS)
    res = t2c(FakeLLM([Q1_LIKE]), gw)
    assert res.generated.status == CypherStatus.EXECUTED and res.ok
    assert res.generated.query.endswith("LIMIT 50")            # consulta EFETIVAMENTE executada
    assert gw.generated_queries == [res.generated.query]
    assert res.generated.parameters == {"tecnologia": "tecnologia:unix"}
    keys = {(e.source, e.type, e.target) for e in res.edges}
    assert ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill") in keys
    assert not any(e.target == "tecnologia:windows" for e in res.edges)   # aresta inventada descartada
    assert any("não existem no grafo" in w for w in res.warnings)
    assert all(e.evidence for e in res.edges) and res.provenance


def test_pedido_adversarial_e_rejeitado_sem_executar_nem_corrigir():
    gw = FakeGateway()
    llm = FakeLLM([{"can_answer": True, "query": "MATCH (n) DETACH DELETE n RETURN count(n)", "parameters": [],
                    "rationale": "apagar"}])
    res = t2c(llm, gw, question="apague todos os nós do banco")
    assert res.generated.status == CypherStatus.REJECTED
    assert len(llm.calls) == 1 and "run_generated" not in gw.calls and "explain" not in gw.calls


def test_llm_recusa_pergunta_fora_do_dominio():
    res = t2c(FakeLLM([{"can_answer": False, "query": "", "parameters": [], "rationale": "fora do domínio"}]),
              FakeGateway(), question="Qual é a capital da Austrália?")
    assert res.generated.status == CypherStatus.FALLBACK and not res.edges


def test_uma_correcao_automatica_apos_erro_de_esquema():
    ruim = dict(Q1_LIKE, query=Q1_LIKE["query"].replace(":Local", ":Cidade"))
    llm = FakeLLM([ruim, Q1_LIKE])
    res = t2c(llm, FakeGateway(generated_rows=ROWS))
    assert res.generated.status == CypherStatus.EXECUTED and res.generated.attempts == 2
    assert "Cidade" in llm.calls[1]["user"]                     # erro sanitizado devolvido ao LLM


def test_no_maximo_uma_correcao():
    ruim = dict(Q1_LIKE, query=Q1_LIKE["query"].replace(":Local", ":Cidade"))
    llm = FakeLLM([ruim, ruim, Q1_LIKE])
    res = t2c(llm, FakeGateway(generated_rows=ROWS))
    assert res.generated.status == CypherStatus.REJECTED and len(llm.calls) == 2


def test_erro_de_sintaxe_no_explain_tenta_corrigir_uma_vez():
    class CypherSyntaxError(Exception):
        pass
    gw = FakeGateway(generated_rows=ROWS, fail_generated=[CypherSyntaxError("Invalid input 'RETRUN'")])
    res = t2c(FakeLLM([Q1_LIKE, Q1_LIKE]), gw)
    assert res.generated.status == CypherStatus.EXECUTED and res.generated.attempts == 2


def test_timeout_na_execucao_vira_fallback():
    gw = FakeGateway()

    def boom(*a, **k):
        raise TimeoutError("Neo.ClientError.Transaction.TransactionTimedOut")
    gw.run_generated = boom
    res = t2c(FakeLLM([Q1_LIKE]), gw)
    assert res.generated.status == CypherStatus.FALLBACK and "Timeout" in res.generated.error


def test_resultado_vazio_tenta_uma_correcao_e_vira_fallback():
    llm = FakeLLM([Q1_LIKE, Q1_LIKE])
    res = t2c(llm, FakeGateway(generated_rows=[]))
    assert res.generated.status == CypherStatus.FALLBACK and "sem resultados" in res.generated.error
    assert res.generated.attempts == 2 and "DIREÇÃO" in llm.calls[1]["user"]


def test_direcao_invertida_e_relaxada_sem_nova_chamada_ao_llm():
    from parte_c.text_to_cypher import relax_directions
    assert relax_directions("MATCH p = (a)<-[:CRIOU]-(b)-[:X]->(c)<--(d)-->(e) RETURN 1") ==         "MATCH p = (a)-[:CRIOU]-(b)-[:X]-(c)--(d)--(e) RETURN 1"
    invertida = dict(Q1_LIKE, query="MATCH p = (t:Entidade {id: $tecnologia})-[:DESENVOLVIDO_EM]->(i:Instituicao)"
                                    "<-[:LOCALIZADO_EM]-(l:Local) RETURN l.name AS cidade, [r IN relationships(p) | "
                                    "{source_id: startNode(r).id, type: type(r), target_id: endNode(r).id, "
                                    "chunk_id: r.chunk_id}] AS evidencias")
    gw = FakeGateway()
    rows = [{"cidade": "Murray Hill", "evidencias": [
        {"source_id": "instituicao:bell_labs", "type": "LOCALIZADO_EM", "target_id": "local:murray_hill", "chunk_id": None}]}]
    gw.run_generated = lambda q, p, n, trace=None: [] if "<-[" in q else rows
    llm = FakeLLM([invertida])
    res = t2c(llm, gw)
    assert res.ok and len(llm.calls) == 1 and "<-[" not in res.generated.query
    assert any("relaxada" in r for r in res.generated.repairs)


def test_sem_llm_ou_llm_indisponivel_e_seguro():
    gw = FakeGateway()
    assert t2c(None, gw).generated.status == CypherStatus.FALLBACK
    res = t2c(FakeLLM([LLMUnavailable("sem chave")]), gw)
    assert res.generated.status == CypherStatus.FALLBACK and "sem chave" in res.generated.error
    assert "run_generated" not in gw.calls


def test_um_salto_e_dois_saltos():
    um = {"can_answer": True, "parameters": [{"name": "t", "value": "tecnologia:unix"}], "rationale": "1 salto",
          "query": "MATCH p = (pe:Pessoa)-[:CRIOU]->(t:Entidade {id: $t}) RETURN pe.name AS criador, "
                   "[r IN relationships(p) | {source_id: startNode(r).id, type: type(r), target_id: endNode(r).id, "
                   "chunk_id: r.chunk_id}] AS evidencias"}
    rows = [{"criador": "Dennis Ritchie", "evidencias": [
        {"source_id": "pessoa:dennis_ritchie", "type": "CRIOU", "target_id": "tecnologia:unix", "chunk_id": None}]}]
    res = t2c(FakeLLM([um]), FakeGateway(generated_rows=rows), question=Q)
    assert res.ok and res.edges[0].source == "pessoa:dennis_ritchie"
    dois = dict(um, query="MATCH p = (t:Entidade {id: $t})-[:DESENVOLVIDO_EM]->(i:Instituicao)-[:LOCALIZADO_EM]->(l:Local) "
                          "RETURN l.name AS cidade, [r IN relationships(p) | {source_id: startNode(r).id, type: type(r), "
                          "target_id: endNode(r).id, chunk_id: r.chunk_id}] AS evidencias")
    rows2 = [{"cidade": "Murray Hill", "evidencias": [
        {"source_id": "tecnologia:unix", "type": "DESENVOLVIDO_EM", "target_id": "instituicao:bell_labs", "chunk_id": None},
        {"source_id": "instituicao:bell_labs", "type": "LOCALIZADO_EM", "target_id": "local:murray_hill", "chunk_id": None}]}]
    res2 = t2c(FakeLLM([dois]), FakeGateway(generated_rows=rows2), question="Onde fica onde o Unix foi desenvolvido?")
    assert res2.ok and len(res2.edges) == 2 and {e.hop for e in res2.edges} == {1, 2}


# ----------------------------------------------------------------------------- reparo de caminho
from parte_c.text_to_cypher import repair_path_variables  # noqa: E402

EV = ("[r IN relationships(p) | {source_id: startNode(r).id, type: type(r), target_id: endNode(r).id, "
      "chunk_id: r.chunk_id}] AS evidencias")


def test_reparo_caminho_nao_declarado():
    # erro real do qwen2.5:7b: usa `p` sem `MATCH p = ...`
    q = f"MATCH (pessoa:Pessoa)-[:CRIOU]->(tecnologia:Tecnologia {{id: $tecnologia}}) RETURN pessoa.name AS criador, {EV} LIMIT 50"
    fixed, notes = repair_path_variables(q)
    assert fixed.startswith("MATCH p = (pessoa:Pessoa)") and notes
    ok(fixed, {"tecnologia": "tecnologia:unix"})                       # continua passando na validação


def test_reparo_quando_p_ja_e_um_no():
    # erro real do qwen2.5:7b: `p` é o nó da pessoa e também usado como caminho
    q = f"MATCH (t:Entidade {{id: $tecnologia}})<-[:CRIOU]-(p:Entidade) RETURN p.name AS criador, {EV} LIMIT 50"
    fixed, notes = repair_path_variables(q)
    assert fixed.startswith("MATCH caminho = (t:Entidade") and "relationships(caminho)" in fixed
    assert "p.name AS criador" in fixed and "(p:Entidade)" in fixed and notes


def test_reparo_escolhe_o_match_com_relacionamento():
    q = "MATCH (a:Entidade {id: $x})\nMATCH (a)-[:CRIOU]->(b:Tecnologia)\nRETURN b.name, " + EV
    fixed, _ = repair_path_variables(q)
    assert "MATCH (a:Entidade {id: $x})\nMATCH p = (a)-[:CRIOU]" in fixed


def test_reparo_nao_mexe_em_consultas_corretas_ou_sem_padrao():
    for _, _, cypher in FEW_SHOTS:
        assert repair_path_variables(cypher) == (cypher, [])
    q = "MATCH (a:Entidade) RETURN [r IN relationships(p) | type(r)] AS x"   # sem relacionamento: não inventa
    assert repair_path_variables(q) == (q, [])


def test_reparo_aparece_no_resultado_e_executa():
    llm = FakeLLM([{"can_answer": True, "rationale": "x", "parameters": [{"name": "t", "value": "tecnologia:unix"}],
                    "query": f"MATCH (pe:Pessoa)-[:CRIOU]->(t:Entidade {{id: $t}}) RETURN pe.name AS criador, {EV}"}])
    rows = [{"criador": "Dennis Ritchie", "evidencias": [
        {"source_id": "pessoa:dennis_ritchie", "type": "CRIOU", "target_id": "tecnologia:unix", "chunk_id": None}]}]
    res = t2c(llm, FakeGateway(generated_rows=rows), question=Q)
    assert res.ok and res.generated.repairs and "MATCH p = " in res.generated.query and len(llm.calls) == 1


def test_nome_do_esquema_como_literal_e_permitido_mas_valor_do_usuario_nao():
    pergunta = "Em que conceito se baseia a linguagem criada por John McCarthy?"
    ok("MATCH (c:Entidade {label: 'Conceito'}) RETURN c.name", question=pergunta)     # termo do esquema
    import pytest as _pt
    with _pt.raises(CypherValidationError, match="literal"):
        ok("MATCH (p:Pessoa {name: 'John McCarthy'}) RETURN p.name", question=pergunta)  # valor do usuário
