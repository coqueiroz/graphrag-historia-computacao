"""Testes da Pessoa B que rodam sem Neo4j (validação e geração do Cypher)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts import ALLOWED_LABELS, ALLOWED_RELATIONS, Entity, ExtractionResult  # noqa: E402
from load_graph import clean, read_json  # noqa: E402
from neo4j_schema import build_statements  # noqa: E402

EXEMPLO = ROOT / "exemplo_grafo.json"


def test_schema_tem_constraint_para_cada_label():
    names = {n for n, _ in build_statements()}
    assert "uniq_entidade_id" in names
    for label in ALLOWED_LABELS:
        assert f"uniq_{label.lower()}_id" in names


def test_schema_tem_os_tres_tipos_de_indice():
    cypher = " ".join(c for _, c in build_statements())
    assert "RANGE INDEX" in cypher
    assert "FULLTEXT INDEX" in cypher
    assert "VECTOR INDEX" in cypher


def test_fulltext_de_relacoes_cobre_todos_os_tipos():
    stmt = dict(build_statements())["ft_relacao_evidence"]
    for rel in ALLOWED_RELATIONS:
        assert rel in stmt


def test_dimensao_do_vetor_configuravel():
    stmt = dict(build_statements(dim=768))["vec_chunk_embedding"]
    assert "`vector.dimensions`: 768" in stmt


def test_nomes_unicos_e_idempotentes():
    stmts = build_statements()
    assert len({n for n, _ in stmts}) == len(stmts)
    assert all("IF NOT EXISTS" in c for _, c in stmts)


def test_limpeza_do_exemplo():
    data, report = clean(read_json(EXEMPLO))
    assert len(data.entities) == 7          # 'Animal' descartado
    assert len(data.relations) == 6         # fora do schema + id inexistente descartadas
    assert all(e.label in ALLOWED_LABELS for e in data.entities)
    assert any("fora do schema" in k for k in report)
    assert any("inexistente" in k for k in report)


def test_id_repetido_e_mesclado():
    e1 = Entity(id="instituicao:usp", label="Instituicao", name="USP", chunk_ids=["c1"])
    e2 = Entity(id="instituicao:usp", label="Instituicao", name="Universidade de São Paulo", chunk_ids=["c2"])
    data, report = clean(ExtractionResult(documents=[], chunks=[], entities=[e1, e2], relations=[]))
    assert len(data.entities) == 1
    usp = data.entities[0]
    assert set(usp.chunk_ids) == {"c1", "c2"}
    assert "Universidade de São Paulo" in usp.aliases
    assert report["entidade mesclada: id repetido"] == 1
