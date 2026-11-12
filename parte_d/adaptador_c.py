"""Converte o QAResponse REAL da Parte C (parte_c/schemas.py) no formato interno da UI.
Entrada: dict de responder_pergunta(). Saída: dict com os campos de contrato_resposta.py + extras opcionais."""
import re


def _n(fonte_id: str) -> int:
    m = re.fullmatch(r"F(\d+)", fonte_id or "")
    return int(m.group(1)) if m else 0


def adaptar(qa: dict) -> dict:
    conf = qa.get("confidence") or {}
    gc = qa.get("generated_cypher") or {}
    sub = qa.get("subgraph") or {"nodes": [], "edges": []}
    fontes = qa.get("sources") or []
    sementes = {s["id"] for s in (qa.get("retrieval") or {}).get("seeds", [])}
    citadas = {(f["relation"]["source_id"], f["relation"]["type"], f["relation"]["target_id"])
               for f in fontes if f.get("cited") and f.get("relation")}
    pr_max = max([n.get("pagerank") or 0 for n in sub["nodes"]] or [0]) or 1.0
    return {
        "mode": "rag" if qa.get("mode") == "baseline" else "graphrag",
        "status": qa.get("status", "error"),
        "answer": qa.get("answer", ""),
        "refused": qa.get("status") == "insufficient_evidence",
        "confidence": float(conf.get("score", 0.0)),
        "conf_level": conf.get("level"),
        "conf_explanation": conf.get("explanation", ""),
        "cypher": gc.get("query"),
        "cypher_status": gc.get("status"),
        "nodes": [{"id": n["id"], "label": n["label"], "name": n["name"], "seed": n["id"] in sementes,
                   "pagerank": round((n.get("pagerank") or 0) / pr_max, 3), "community": n.get("community")}
                  for n in sub["nodes"]],
        "edges": [{"source": e["source"], "target": e["target"], "type": e["type"],
                   "evidence": e.get("evidence", ""), "chunk_id": e.get("chunk_id"),
                   "on_path": (e["source"], e["type"], e["target"]) in citadas} for e in sub["edges"]],
        "sources": [{"n": _n(f["id"]), "id": f["id"], "chunk_id": f.get("chunk_id"), "doc": f.get("title") or f.get("doc_id"),
                     "url": f.get("url"), "text": f.get("quote", ""), "cited": bool(f.get("cited"))} for f in fontes],
        "claims": qa.get("claims") or [],
        "warnings": qa.get("warnings") or [],
        "latency_ms": int(qa.get("latency_ms", 0)),
    }
