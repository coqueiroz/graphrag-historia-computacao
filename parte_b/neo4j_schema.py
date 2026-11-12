"""
Cria o schema LPG no Neo4j a partir de contracts.py (Pessoa B).

Modelo:
  (:Documento {doc_id, title, url})-[:TEM_CHUNK]->(:Chunk {chunk_id, doc_id, text, position, embedding})
  (:Entidade:<Label> {id, name, aliases, aliases_text, label, embedding})-[:MENCIONADO_EM]->(:Chunk)
  (:Entidade)-[:<ALLOWED_RELATION> {evidence, chunk_id}]->(:Entidade)

Todo nó de entidade tem DOIS labels: o genérico `Entidade` e o específico
(Pessoa, Instituicao, ...). Assim uma única constraint/índice fulltext/índice
vetorial cobre todas as entidades, e as consultas por tipo continuam rápidas.

Uso:
  python neo4j_schema.py            # cria tudo (idempotente, IF NOT EXISTS)
  python neo4j_schema.py --reset    # apaga constraints/índices do projeto e recria
  python neo4j_schema.py --show     # só lista o que existe no banco
"""
import argparse

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # raiz do projeto: contracts.py (compartilhado)
from contracts import ALLOWED_LABELS, ALLOWED_RELATIONS
from neo4j_config import EMBEDDING_DIM, NEO4J_DATABASE, get_driver

ENTITY = "Entidade"
DOC = "Documento"
CHUNK = "Chunk"
STRUCTURAL_RELATIONS = ["TEM_CHUNK", "MENCIONADO_EM"]


def build_statements(dim: int = EMBEDDING_DIM) -> list[tuple[str, str]]:
    """Retorna (nome, cypher) de cada constraint/índice. Nomes estáveis = idempotência."""
    stmts: list[tuple[str, str]] = []

    # ---- 1. Constraints de unicidade (cada uma cria um índice RANGE por baixo) ----
    stmts.append(("uniq_entidade_id",
                  f"CREATE CONSTRAINT uniq_entidade_id IF NOT EXISTS "
                  f"FOR (n:{ENTITY}) REQUIRE n.id IS UNIQUE"))
    # Uma por label específico: permite MATCH (p:Pessoa {id: ...}) usar índice.
    for label in ALLOWED_LABELS:
        name = f"uniq_{label.lower()}_id"
        stmts.append((name,
                      f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                      f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"))
    stmts.append(("uniq_documento_doc_id",
                  f"CREATE CONSTRAINT uniq_documento_doc_id IF NOT EXISTS "
                  f"FOR (d:{DOC}) REQUIRE d.doc_id IS UNIQUE"))
    stmts.append(("uniq_chunk_chunk_id",
                  f"CREATE CONSTRAINT uniq_chunk_chunk_id IF NOT EXISTS "
                  f"FOR (c:{CHUNK}) REQUIRE c.chunk_id IS UNIQUE"))

    # ---- 2. Índices RANGE (o "B-Tree" do Neo4j 5; BTREE foi substituído por RANGE) ----
    stmts += [
        ("idx_entidade_name",
         f"CREATE RANGE INDEX idx_entidade_name IF NOT EXISTS FOR (n:{ENTITY}) ON (n.name)"),
        ("idx_entidade_label",
         f"CREATE RANGE INDEX idx_entidade_label IF NOT EXISTS FOR (n:{ENTITY}) ON (n.label)"),
        # Propriedades escritas pelo GDS (gds_algorithms.py): ranking e filtro por comunidade.
        ("idx_entidade_pagerank",
         f"CREATE RANGE INDEX idx_entidade_pagerank IF NOT EXISTS FOR (n:{ENTITY}) ON (n.pagerank)"),
        ("idx_entidade_community",
         f"CREATE RANGE INDEX idx_entidade_community IF NOT EXISTS FOR (n:{ENTITY}) ON (n.community)"),
        ("idx_chunk_doc_pos",
         f"CREATE RANGE INDEX idx_chunk_doc_pos IF NOT EXISTS FOR (c:{CHUNK}) ON (c.doc_id, c.position)"),
        ("idx_documento_title",
         f"CREATE RANGE INDEX idx_documento_title IF NOT EXISTS FOR (d:{DOC}) ON (d.title)"),
    ]
    # Índice em propriedade de relacionamento: achar fatos a partir do chunk citado.
    for rel in ALLOWED_RELATIONS:
        name = f"idx_rel_{rel.lower()}_chunk"
        stmts.append((name,
                      f"CREATE RANGE INDEX {name} IF NOT EXISTS "
                      f"FOR ()-[r:{rel}]-() ON (r.chunk_id)"))

    # ---- 3. Índices FULLTEXT (Lucene) ----
    stmts += [
        ("ft_entidade_nome",
         f"CREATE FULLTEXT INDEX ft_entidade_nome IF NOT EXISTS "
         f"FOR (n:{ENTITY}) ON EACH [n.name, n.aliases_text]"),
        ("ft_chunk_text",
         f"CREATE FULLTEXT INDEX ft_chunk_text IF NOT EXISTS "
         f"FOR (c:{CHUNK}) ON EACH [c.text]"),
        ("ft_relacao_evidence",
         f"CREATE FULLTEXT INDEX ft_relacao_evidence IF NOT EXISTS "
         f"FOR ()-[r:{'|'.join(ALLOWED_RELATIONS)}]-() ON EACH [r.evidence]"),
    ]

    # ---- 4. Índices VECTOR (Neo4j >= 5.13) ----
    vec_opts = (f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, "
                f"`vector.similarity_function`: 'cosine'}}}}")
    stmts += [
        ("vec_entidade_embedding",
         f"CREATE VECTOR INDEX vec_entidade_embedding IF NOT EXISTS "
         f"FOR (n:{ENTITY}) ON (n.embedding) {vec_opts}"),
        ("vec_chunk_embedding",
         f"CREATE VECTOR INDEX vec_chunk_embedding IF NOT EXISTS "
         f"FOR (c:{CHUNK}) ON (c.embedding) {vec_opts}"),
    ]
    return stmts


def project_names() -> set[str]:
    return {name for name, _ in build_statements()}


def drop_all(session) -> None:
    names = project_names()
    for rec in session.run("SHOW CONSTRAINTS YIELD name"):
        if rec["name"] in names:
            session.run(f"DROP CONSTRAINT {rec['name']} IF EXISTS")
            print(f"  - drop constraint {rec['name']}")
    for rec in session.run("SHOW INDEXES YIELD name, owningConstraint WHERE owningConstraint IS NULL"):
        if rec["name"] in names:
            session.run(f"DROP INDEX {rec['name']} IF EXISTS")
            print(f"  - drop index {rec['name']}")


def show(session) -> None:
    print("\nCONSTRAINTS")
    for r in session.run("SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
                         "RETURN name, type, labelsOrTypes, properties ORDER BY name"):
        print(f"  {r['name']:<28} {r['type']:<12} {r['labelsOrTypes']} {r['properties']}")
    print("\nINDEXES")
    for r in session.run("SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state "
                         "RETURN name, type, entityType, labelsOrTypes, properties, state ORDER BY type, name"):
        lt = r["labelsOrTypes"] or []
        lt = lt if len(lt) <= 3 else lt[:3] + ["..."]
        print(f"  {r['name']:<28} {r['type']:<9} {r['entityType']:<12} {lt} {r['properties']} [{r['state']}]")


def create_schema(reset: bool = False) -> None:
    driver = get_driver()
    driver.verify_connectivity()
    with driver.session(database=NEO4J_DATABASE) as s:
        if reset:
            print("Removendo schema anterior...")
            drop_all(s)
        print(f"Criando schema (embedding dim = {EMBEDDING_DIM})...")
        for name, cypher in build_statements():
            s.run(cypher)
            print(f"  + {name}")
        s.run("CALL db.awaitIndexes(300)")
        show(s)
    driver.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="apaga e recria constraints/índices do projeto")
    ap.add_argument("--show", action="store_true", help="só mostra o schema atual")
    ap.add_argument("--print", action="store_true", help="só imprime o Cypher, sem conectar")
    a = ap.parse_args()
    if a.print:
        for _, c in build_statements():
            print(c + ";")
    elif a.show:
        d = get_driver()
        with d.session(database=NEO4J_DATABASE) as s:
            show(s)
        d.close()
    else:
        create_schema(reset=a.reset)
