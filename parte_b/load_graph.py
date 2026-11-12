"""
Carrega o grafo.json (ExtractionResult da Pessoa A) no Neo4j.

Passos:
  1. Valida o JSON com o modelo pydantic do contrato.
  2. Limpa: descarta entidades com label fora de ALLOWED_LABELS e relações que
     violam ALLOWED_RELATIONS / RELATION_SCHEMA ou apontam para ids inexistentes.
  3. Grava em lotes com UNWIND + MERGE (idempotente: rodar duas vezes não duplica).

Uso:
  python load_graph.py grafo.json
  python load_graph.py grafo.json --wipe        # apaga todos os nós antes
  python load_graph.py grafo.json --dry-run     # só valida e mostra o relatório
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # raiz do projeto: contracts.py (compartilhado)
from contracts import (ALLOWED_LABELS, ALLOWED_RELATIONS, RELATION_SCHEMA,
                       ExtractionResult)

BATCH = 1000


# ---------------------------------------------------------------------------
# Validação / limpeza (não depende do banco -> testável)
# ---------------------------------------------------------------------------
def clean(result: ExtractionResult) -> tuple[ExtractionResult, Counter]:
    report: Counter = Counter()

    # Entidades: label válido + id único (se repetir, junta aliases e chunks).
    by_id: dict = {}
    for e in result.entities:
        if e.label not in ALLOWED_LABELS:
            report[f"entidade descartada: label '{e.label}'"] += 1
            continue
        if e.id in by_id:
            prev = by_id[e.id]
            if prev.label != e.label:
                report["entidade descartada: id repetido com label diferente"] += 1
                continue
            prev.aliases = sorted((set(prev.aliases) | set(e.aliases) | {e.name}) - {prev.name})
            prev.chunk_ids = sorted(set(prev.chunk_ids) | set(e.chunk_ids))
            report["entidade mesclada: id repetido"] += 1
            continue
        by_id[e.id] = e.model_copy(deep=True)

    chunk_ids = {c.chunk_id for c in result.chunks}
    doc_ids = {d.doc_id for d in result.documents}
    chunks = []
    for c in result.chunks:
        if c.doc_id not in doc_ids:
            report["chunk sem documento (mantido, sem TEM_CHUNK)"] += 1
        chunks.append(c)

    # Relações: tipo permitido, ids existentes, origem/destino conforme RELATION_SCHEMA.
    rels, seen = [], set()
    for r in result.relations:
        if r.type not in ALLOWED_RELATIONS:
            report[f"relação descartada: tipo '{r.type}'"] += 1
            continue
        s, t = by_id.get(r.source_id), by_id.get(r.target_id)
        if s is None or t is None:
            report["relação descartada: id de entidade inexistente"] += 1
            continue
        if r.source_id == r.target_id:
            report["relação descartada: auto-laço"] += 1
            continue
        src_ok, tgt_ok = RELATION_SCHEMA[r.type]
        if s.label not in src_ok or t.label not in tgt_ok:
            report[f"relação descartada: {s.label}-[{r.type}]->{t.label} fora do schema"] += 1
            continue
        key = (r.source_id, r.type, r.target_id, r.chunk_id)
        if key in seen:
            report["relação descartada: duplicada"] += 1
            continue
        seen.add(key)
        if r.chunk_id not in chunk_ids:
            report["relação com chunk_id inexistente (mantida)"] += 1
        rels.append(r)

    cleaned = ExtractionResult(documents=result.documents, chunks=chunks,
                               entities=list(by_id.values()), relations=rels)
    return cleaned, report


def read_json(path: str | Path) -> ExtractionResult:
    with open(path, encoding="utf-8") as f:
        return ExtractionResult.model_validate(json.load(f))


# ---------------------------------------------------------------------------
# Escrita no Neo4j
# ---------------------------------------------------------------------------
def _batches(rows: list, n: int = BATCH):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def write(session, data: ExtractionResult) -> None:
    docs = [d.model_dump() for d in data.documents]
    for b in _batches(docs):
        session.run("""
            UNWIND $rows AS row
            MERGE (d:Documento {doc_id: row.doc_id})
            SET d.title = row.title, d.url = row.url
        """, rows=b)

    chunks = [c.model_dump() for c in data.chunks]
    for b in _batches(chunks):
        session.run("""
            UNWIND $rows AS row
            MERGE (c:Chunk {chunk_id: row.chunk_id})
            SET c.doc_id = row.doc_id, c.text = row.text, c.position = row.position
            WITH c, row
            MATCH (d:Documento {doc_id: row.doc_id})
            MERGE (d)-[:TEM_CHUNK]->(c)
        """, rows=b)

    # Entidades agrupadas por label: o label não pode ser parâmetro no Cypher,
    # então é interpolado — seguro porque já foi validado contra ALLOWED_LABELS.
    by_label = defaultdict(list)
    for e in data.entities:
        row = e.model_dump()
        row["aliases_text"] = " | ".join(e.aliases)
        by_label[e.label].append(row)
    for label, rows in by_label.items():
        assert label in ALLOWED_LABELS
        for b in _batches(rows):
            session.run(f"""
                UNWIND $rows AS row
                MERGE (n:Entidade {{id: row.id}})
                SET n:{label}, n.label = row.label, n.name = row.name,
                    n.aliases = row.aliases, n.aliases_text = row.aliases_text,
                    n.chunk_ids = row.chunk_ids
                WITH n, row
                UNWIND row.chunk_ids AS cid
                MATCH (c:Chunk {{chunk_id: cid}})
                MERGE (n)-[:MENCIONADO_EM]->(c)
            """, rows=b)

    # Relações agrupadas por tipo (mesma razão). Uma aresta por (origem, tipo,
    # destino, chunk) -> cada aresta carrega sua própria evidência citável.
    by_type = defaultdict(list)
    for r in data.relations:
        by_type[r.type].append(r.model_dump())
    for rtype, rows in by_type.items():
        assert rtype in ALLOWED_RELATIONS
        for b in _batches(rows):
            session.run(f"""
                UNWIND $rows AS row
                MATCH (s:Entidade {{id: row.source_id}})
                MATCH (t:Entidade {{id: row.target_id}})
                MERGE (s)-[r:{rtype} {{chunk_id: row.chunk_id}}]->(t)
                SET r.evidence = row.evidence
            """, rows=b)


def stats(session) -> None:
    print("\nNÓS POR LABEL")
    for r in session.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC"):
        print(f"  {r['l']:<14} {r['c']}")
    print("\nRELAÇÕES POR TIPO")
    for r in session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"):
        print(f"  {r['t']:<16} {r['c']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="caminho do grafo.json")
    ap.add_argument("--wipe", action="store_true", help="apaga todos os nós antes de carregar")
    ap.add_argument("--dry-run", action="store_true", help="só valida, não grava")
    a = ap.parse_args()

    raw = read_json(a.path)
    data, report = clean(raw)
    print(f"Entrada: {len(raw.documents)} docs, {len(raw.chunks)} chunks, "
          f"{len(raw.entities)} entidades, {len(raw.relations)} relações")
    print(f"Após limpeza: {len(data.entities)} entidades, {len(data.relations)} relações")
    for k, v in sorted(report.items()):
        print(f"  {v:>5}  {k}")
    if a.dry_run:
        return

    from neo4j_config import NEO4J_DATABASE, get_driver
    driver = get_driver()
    with driver.session(database=NEO4J_DATABASE) as s:
        if a.wipe:
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
            print("Banco limpo.")
        write(s, data)
        stats(s)
    driver.close()


if __name__ == "__main__":
    main()
