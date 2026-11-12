"""Motor FALSO sobre o grafo.json real, só para desenvolver a UI antes do motor da Pessoa C.
Sem LLM e sem Neo4j: acha entidades citadas na pergunta, expande 2 saltos e monta um resumo com citações.
Troque por `from graphrag_engine import ask` quando o C entregar."""
import json, re, time, unicodedata
from collections import defaultdict, deque
from pathlib import Path

from contrato_resposta import validar

D = json.loads((Path(__file__).resolve().parents[1] / "grafo.json").read_text(encoding="utf-8"))
ENT = {e["id"]: e for e in D["entities"]}
CHUNK = {c["chunk_id"]: c for c in D["chunks"]}
DOC = {d["doc_id"]: d for d in D["documents"]}
ADJ = defaultdict(list)  # id -> [(vizinho, relação)]
for r in D["relations"]:
    ADJ[r["source_id"]].append((r["target_id"], r))
    ADJ[r["target_id"]].append((r["source_id"], r))


def _norm(s):
    return " " + re.sub(r"[^a-z0-9+#]+", " ", unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()).strip() + " "


NOMES = sorted(((_norm(n), e["id"]) for e in ENT.values() for n in [e["name"], *e["aliases"]] if len(n) > 3),
               key=lambda x: -len(x[0]))


def _sementes(pergunta):
    q, achadas = _norm(pergunta), []
    for nome, eid in NOMES:
        if nome in q and eid not in achadas and ADJ[eid]:
            achadas.append(eid)
            q = q.replace(nome, " ")
    return achadas[:3]


def _vizinhanca(sementes, saltos=2, limite=22):
    dist, pai, fila = {s: 0 for s in sementes}, {}, deque(sementes)
    while fila:
        u = fila.popleft()
        if dist[u] == saltos:
            continue
        for v, r in sorted(ADJ[u], key=lambda x: -len(ADJ[x[0]])):
            if v not in dist and len(dist) < limite:
                dist[v], pai[v] = dist[u] + 1, (u, r)
                fila.append(v)
    return dist, pai


def ask(pergunta: str, mode: str = "graphrag") -> dict:
    t0 = time.time()
    base = {"mode": mode, "nodes": [], "edges": [], "sources": [], "cypher": None}
    sem = _sementes(pergunta)
    if not sem:
        return validar({**base, "answer": "Não encontrei nos documentos informação suficiente para responder.",
                        "refused": True, "confidence": 0.05, "latency_ms": int((time.time() - t0) * 1000)})
    if mode == "rag":  # baseline: só trechos, sem grafo
        chunks = list(dict.fromkeys(c for s in sem for c in ENT[s]["chunk_ids"]))[:3]
        src = [{"n": i + 1, "chunk_id": c, "doc": DOC[CHUNK[c]["doc_id"]]["title"], "text": CHUNK[c]["text"][:300]}
               for i, c in enumerate(chunks)]
        return validar({**base, "answer": "[MOCK RAG] Trechos mais similares: " + " ".join(f"[{s['n']}]" for s in src),
                        "refused": False, "confidence": 0.55, "sources": src,
                        "latency_ms": int((time.time() - t0) * 1000)})
    dist, pai = _vizinhanca(sem)
    no_caminho = set(sem)
    for v in dist:
        while v in pai:
            no_caminho.add(v); v = pai[v][0]
    nodes = [{"id": i, "label": ENT[i]["label"], "name": ENT[i]["name"], "seed": i in sem,
              "pagerank": round(len(ADJ[i]) / 26, 3)} for i in dist]  # grau normalizado no lugar do PageRank
    edges, vistos = [], set()
    for v, (u, r) in pai.items():
        k = (r["source_id"], r["target_id"], r["type"])
        if k not in vistos:
            vistos.add(k)
            edges.append({"source": r["source_id"], "target": r["target_id"], "type": r["type"],
                          "evidence": r["evidence"], "chunk_id": r["chunk_id"], "on_path": True})
    src, linhas = [], []
    for e in edges[:6]:
        c = e["chunk_id"]
        if c not in [s["chunk_id"] for s in src]:
            src.append({"n": len(src) + 1, "chunk_id": c, "doc": DOC[CHUNK[c]["doc_id"]]["title"],
                        "url": DOC[CHUNK[c]["doc_id"]]["url"], "text": CHUNK[c]["text"][:300]})
        n = [s["chunk_id"] for s in src].index(c) + 1
        linhas.append(f"{ENT[e['source']]['name']} —{e['type']}→ {ENT[e['target']]['name']} [{n}]")
    cy = ("MATCH p=(s:Entidade)-[*1..2]-(v:Entidade)\nWHERE s.id IN $sementes\n"
          "RETURN p ORDER BY v.pagerank DESC LIMIT 25  // MOCK")
    return validar({**base, "answer": "[MOCK] Fatos encontrados no subgrafo: " + "; ".join(linhas) + ".",
                    "refused": False, "confidence": round(min(0.95, 0.4 + 0.1 * len(src) + 0.1 * len(sem)), 2),
                    "cypher": cy, "nodes": nodes, "edges": edges, "sources": src,
                    "latency_ms": int((time.time() - t0) * 1000)})
