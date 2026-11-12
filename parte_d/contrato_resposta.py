"""Contrato Pessoa C -> Pessoa D. Toda resposta do motor (GraphRAG ou RAG puro) tem este formato."""
from typing import Any

CAMPOS = {"mode", "answer", "refused", "confidence", "cypher", "nodes", "edges", "sources", "latency_ms"}
# nodes:   {id, label, name, pagerank?, community?, seed?: bool}
# edges:   {source, target, type, evidence?, chunk_id?, on_path?: bool}
# sources: {n, chunk_id, doc, url?, text}   (n = número da citação [n] no texto da resposta)
# confidence: float 0..1 (explicar o cálculo no artigo)
# RAG puro: nodes/edges = [] e cypher = None.


def validar(r: dict[str, Any]) -> dict[str, Any]:
    faltando = CAMPOS - r.keys()
    if faltando:
        raise ValueError(f"resposta do motor sem campos: {sorted(faltando)}")
    if not 0 <= r["confidence"] <= 1:
        raise ValueError("confidence deve estar em [0, 1]")
    ids = {n["id"] for n in r["nodes"]}
    for e in r["edges"]:
        if e["source"] not in ids or e["target"] not in ids:
            raise ValueError(f"aresta aponta para nó ausente: {e}")
    return r
