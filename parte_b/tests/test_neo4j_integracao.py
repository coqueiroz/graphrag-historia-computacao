"""Testes de integração: rodam contra o Neo4j do .env e são pulados se o banco estiver desligado."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import ALLOWED_LABELS, RELATION_SCHEMA  # noqa: E402
from neo4j_config import EMBEDDING_DIM, NEO4J_DATABASE, get_driver  # noqa: E402
from neo4j_schema import build_statements  # noqa: E402


@pytest.fixture(scope="module")
def s():
    try:
        driver = get_driver()
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j indisponível: {e}")
    with driver.session(database=NEO4J_DATABASE) as session:
        if session.run("MATCH (e:Entidade) RETURN count(e)").single()[0] == 0:
            pytest.skip("banco vazio: rode load_graph.py antes")
        yield session
    driver.close()


def one(s, q, **p):
    return s.run(q, **p).single()[0]


def test_todos_os_indices_existem_e_estao_online(s):
    online = {r["name"] for r in s.run("SHOW INDEXES YIELD name, state WHERE state = 'ONLINE'")}
    online |= {r["name"] for r in s.run("SHOW CONSTRAINTS YIELD name")}
    faltando = {n for n, _ in build_statements()} - online
    assert not faltando


def test_ids_unicos(s):
    assert one(s, "MATCH (e:Entidade) WITH e.id AS id, count(*) AS c WHERE c > 1 RETURN count(id)") == 0


def test_toda_entidade_tem_label_valido(s):
    rows = s.run("MATCH (e:Entidade) RETURN DISTINCT e.label AS l")
    assert {r["l"] for r in rows} <= set(ALLOWED_LABELS)


def test_relacoes_respeitam_schema(s):
    for rtype, (src, tgt) in RELATION_SCHEMA.items():
        bad = one(s, f"MATCH (a)-[:{rtype}]->(b) "
                     f"WHERE NOT a.label IN $src OR NOT b.label IN $tgt RETURN count(*)", src=src, tgt=tgt)
        assert bad == 0, rtype


def test_toda_relacao_tem_evidencia(s):
    assert one(s, "MATCH (:Entidade)-[r]->(:Entidade) "
                  "WHERE r.evidence IS NULL OR r.chunk_id IS NULL RETURN count(r)") == 0


def test_embeddings_com_dimensao_certa(s):
    assert one(s, "MATCH (e:Entidade) WHERE e.embedding IS NULL RETURN count(e)") == 0
    assert one(s, "MATCH (e:Entidade) RETURN size(e.embedding) LIMIT 1") == EMBEDDING_DIM


def test_gds_escreveu_propriedades(s):
    assert one(s, "MATCH (e:Entidade) WHERE e.pagerank IS NULL OR e.community IS NULL RETURN count(e)") == 0


def test_busca_vetorial_responde(s):
    v = one(s, "MATCH (e:Entidade) WHERE e.embedding IS NOT NULL RETURN e.embedding LIMIT 1")
    n = one(s, "CALL db.index.vector.queryNodes('vec_entidade_embedding', 3, $v) "
               "YIELD node RETURN count(node)", v=v)
    assert n == 3


def test_consultas_de_referencia_rodam(s):
    from consultas_referencia import CONSULTAS
    for name, q in CONSULTAS.items():
        params = dict(q["params"])
        if "embed" in q:
            params["vetor"] = one(s, "MATCH (e:Entidade) RETURN e.embedding LIMIT 1")
        s.run(q["cypher"], **params).consume()  # não pode dar erro de sintaxe
