"""
Gera embeddings para os nós Entidade e Chunk e grava na propriedade `embedding`
(usada pelos índices vetoriais vec_entidade_embedding e vec_chunk_embedding).

Modelo: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 dim, roda em CPU,
bom em português). A Pessoa C TEM que usar o mesmo modelo para embutir a pergunta:
    from embeddings import embed_query
    vetor = embed_query("Quem orientou Alan Turing?")

Texto embutido de cada entidade (também salvo em `embedding_text`, para rastreabilidade):
    "<name> (<label>). Também conhecido como: <aliases>. <TIPO> <vizinho>; ..."
Incluir as relações dá contexto ao vetor (ex.: "Bell Labs" fica perto de "Unix").

Uso:
  python embeddings.py              # gera só para quem ainda não tem embedding
  python embeddings.py --all        # regera tudo
"""
import argparse
import os

from neo4j_config import EMBEDDING_DIM, NEO4J_DATABASE, get_driver

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
BATCH = 64
MAX_RELS_IN_TEXT = 15

_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        dim = _model.get_sentence_embedding_dimension()
        if dim != EMBEDDING_DIM:
            raise SystemExit(f"Modelo gera {dim} dimensões, mas EMBEDDING_DIM={EMBEDDING_DIM}. "
                             f"Ajuste o .env e recrie o schema (python neo4j_schema.py --reset).")
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    vecs = get_model().encode(texts, batch_size=BATCH, normalize_embeddings=True,
                              show_progress_bar=len(texts) > BATCH)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """Para a Pessoa C: embute a pergunta do usuário no mesmo espaço dos nós."""
    return embed([text])[0]


def entity_text(name: str, label: str, aliases: list[str], rels: list[str]) -> str:
    parts = [f"{name} ({label})."]
    if aliases:
        parts.append("Também conhecido como: " + ", ".join(aliases) + ".")
    if rels:
        parts.append("; ".join(rels[:MAX_RELS_IN_TEXT]) + ".")
    return " ".join(parts)


ENTITY_QUERY = """
MATCH (e:Entidade) {where}
OPTIONAL MATCH (e)-[r]->(o:Entidade)
WITH e, collect(type(r) + ' ' + o.name) AS out
OPTIONAL MATCH (e)<-[r2]-(i:Entidade)
WITH e, out, collect(i.name + ' ' + type(r2) + ' ' + e.name) AS inc
RETURN elementId(e) AS eid, e.name AS name, e.label AS label,
       coalesce(e.aliases, []) AS aliases, out + inc AS rels
"""

WRITE_QUERY = """
UNWIND $rows AS row
MATCH (n) WHERE elementId(n) = row.eid
SET n.embedding_text = row.text, n.embedding_model = $model
WITH n, row
CALL db.create.setNodeVectorProperty(n, 'embedding', row.vec)
"""


def _write(session, rows: list[dict]) -> None:
    for i in range(0, len(rows), 500):
        session.run(WRITE_QUERY, rows=rows[i:i + 500], model=MODEL_NAME)


def run(regenerate_all: bool = False) -> None:
    where = "" if regenerate_all else "WHERE e.embedding IS NULL"
    driver = get_driver()
    with driver.session(database=NEO4J_DATABASE) as s:
        ents = list(s.run(ENTITY_QUERY.format(where=where)))
        if ents:
            texts = [entity_text(r["name"], r["label"], r["aliases"], r["rels"]) for r in ents]
            print(f"Embutindo {len(texts)} entidades com {MODEL_NAME}...")
            vecs = embed(texts)
            _write(s, [{"eid": r["eid"], "text": t, "vec": v} for r, t, v in zip(ents, texts, vecs)])

        cwhere = "" if regenerate_all else "WHERE c.embedding IS NULL"
        chunks = list(s.run(f"MATCH (c:Chunk) {cwhere} "
                            f"OPTIONAL MATCH (d:Documento)-[:TEM_CHUNK]->(c) "
                            f"RETURN elementId(c) AS eid, c.text AS text, d.title AS title"))
        if chunks:
            # Prefixar o título do documento ajuda chunks curtos/ambíguos.
            texts = [f"{r['title']}: {r['text']}" if r["title"] else r["text"] for r in chunks]
            print(f"Embutindo {len(texts)} chunks...")
            vecs = embed(texts)
            _write(s, [{"eid": r["eid"], "text": t, "vec": v} for r, t, v in zip(chunks, texts, vecs)])

        print("Com embedding:",
              s.run("MATCH (e:Entidade) WHERE e.embedding IS NOT NULL RETURN count(e)").single()[0], "entidades,",
              s.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c)").single()[0], "chunks")
    driver.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="regera embeddings de todos os nós")
    run(regenerate_all=ap.parse_args().all)
