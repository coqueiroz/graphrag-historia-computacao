"""
Dublês de teste da Parte C (sem Neo4j e sem LLM).

- `fake_embed`   : embedding determinístico por "saco de palavras" com hashing (384d, normalizado).
                   NÃO é o modelo real; serve só para os testes serem reprodutíveis e offline.
- `FakeGateway`  : implementa os mesmos métodos de `parte_c.graph_store.Neo4jGateway` sobre os dados
                   REAIS de `grafo.json` (os mesmos que a Pessoa B carregou no Neo4j), em memória.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIM = 384
_STOP = {"o", "a", "os", "as", "de", "do", "da", "dos", "das", "em", "no", "na", "que", "quem", "qual",
         "quais", "e", "um", "uma", "foi", "por", "para", "com", "onde", "como", "se", "ao", "the", "of"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _toks(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _norm(text)) if t not in _STOP and len(t) > 1]


def fake_embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for t in _toks(text):
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def neo4j_cosine(a: list[float], b: list[float]) -> float:
    """Mesma escala do Neo4j para similaridade de cosseno: (1 + cos) / 2."""
    return (1 + sum(x * y for x, y in zip(a, b))) / 2


@lru_cache(maxsize=1)
def load_graph():
    return json.loads((ROOT / "grafo.json").read_text(encoding="utf-8"))


class FakeGateway:
    def __init__(self, generated_rows=None, explain_result=None, fail_generated=None):
        g = load_graph()
        self.docs = {d["doc_id"]: d for d in g["documents"]}
        self.chunks = {c["chunk_id"]: c for c in g["chunks"]}
        self.ents = {e["id"]: e for e in g["entities"]}
        self.rels = g["relations"]
        self.adj = defaultdict(list)
        for i, r in enumerate(self.rels):
            self.adj[r["source_id"]].append((i, r["target_id"]))
            self.adj[r["target_id"]].append((i, r["source_id"]))
        # PageRank fake = grau normalizado (determinístico)
        deg = {k: len(v) for k, v in self.adj.items()}
        mx = max(deg.values())
        self.pr = {k: deg.get(k, 0) / mx for k in self.ents}
        self.emb = {k: fake_embed(f"{e['name']} {' '.join(e.get('aliases', []))}") for k, e in self.ents.items()}
        self.chunk_emb = {k: fake_embed(f"{self.docs[c['doc_id']]['title']}: {c['text']}")
                          for k, c in self.chunks.items()}
        self.generated_rows = generated_rows if generated_rows is not None else []
        self.explain_result = explain_result or {"query_type": "r", "operators": ["ProduceResults"]}
        self.fail_generated = list(fail_generated or [])
        self.calls: list[str] = []
        self.generated_queries: list[str] = []

    # ------------------------------------------------------------------ helpers
    def _node(self, i, vetor=None):
        e = self.ents[i]
        d = {"id": i, "name": e["name"], "label": e["label"], "pagerank": self.pr[i], "community": 0}
        if vetor is not None:
            d["sim"] = neo4j_cosine(self.emb[i], vetor)
        return d

    def _rel(self, idx):
        r = self.rels[idx]
        return {"source": r["source_id"], "target": r["target_id"], "type": r["type"],
                "evidence": r["evidence"], "chunk_id": r["chunk_id"]}

    # ------------------------------------------------------------------ API do gateway
    def close(self):
        pass

    @staticmethod
    def _t(trace, name, params, rows):
        if trace is not None:
            from parte_c.graph_store import _safe_params
            from parte_c.schemas import ExecutedQuery
            trace.append(ExecutedQuery(name=name, query=f"<fake {name}>", parameters=_safe_params(params), rows=rows))

    def vector_seeds(self, vetor, k, min_score, trace=None):
        self.calls.append("vector_seeds")
        scored = sorted(((neo4j_cosine(v, vetor), i) for i, v in self.emb.items()), key=lambda x: (-x[0], x[1]))
        out = [{**self._node(i), "score": s} for s, i in scored[:k] if s >= min_score]
        self._t(trace, "sementes_vetoriais", {"k": k, "vetor": vetor, "min_score": min_score}, len(out))
        return out

    def fulltext_seeds(self, termos, vetor, n, trace=None):
        self.calls.append("fulltext_seeds")
        phrases = [_norm(p) for p in re.findall(r'"([^"]+)"', termos)]
        hits = []
        for i, e in self.ents.items():
            names = [_norm(e["name"])] + [_norm(a) for a in e.get("aliases", [])]
            best = max((len(p) for p in phrases for nm in names if p and (p == nm or re.search(rf"\b{re.escape(p)}\b", nm))),
                       default=0)
            if best:
                exact = any(p == nm for p in phrases for nm in names)
                hits.append((best + (100 if exact else 0), i))
        hits.sort(key=lambda x: (-x[0], x[1]))
        return [{**self._node(i), "score": neo4j_cosine(self.emb[i], vetor), "ft_score": float(s)}
                for s, i in hits[:n]]

    def expand(self, seed_ids, vetor, hops, max_paths, trace=None, tipos=None):
        self.calls.append("expand")
        out = {}
        for s in seed_ids:
            paths = []

            def dfs(node, nodes, rels):
                if rels:
                    paths.append((list(nodes), list(rels)))
                if len(rels) == hops:
                    return
                for idx, nb in self.adj[node]:
                    if nb in nodes or idx in rels:
                        continue
                    dfs(nb, nodes + [nb], rels + [idx])

            dfs(s, [s], [])
            tipos_ = set(tipos or [])
            paths.sort(key=lambda p: (-sum(self.rels[i]["type"] in tipos_ for i in p[1]),
                                      -neo4j_cosine(self.emb[p[0][-1]], vetor), len(p[1]),
                                      -self.pr[p[0][-1]], p[0][-1]))
            out[s] = [{"nos": [self._node(n, vetor) for n in ns], "rels": [self._rel(i) for i in rs]}
                      for ns, rs in paths[:max_paths]]
        return out

    def provenance(self, chunk_ids, trace=None):
        self.calls.append("provenance")
        out = {}
        for cid in chunk_ids:
            c = self.chunks.get(cid)
            if c:
                d = self.docs.get(c["doc_id"], {})
                out[cid] = {"chunk_id": cid, "position": c["position"], "text": c["text"],
                            "doc_id": c["doc_id"], "title": d.get("title"), "url": d.get("url")}
        return out

    def nodes_by_ids(self, ids, vetor=None, trace=None):
        self.calls.append("nodes_by_ids")
        return {i: self._node(i, vetor) for i in ids if i in self.ents}

    def verify_edges(self, edges, trace=None):
        self.calls.append("verify_edges")
        out = []
        for e in edges:
            for r in self.rels:
                same_dir = r["source_id"] == e["source"] and r["target_id"] == e["target"]
                rev_dir = r["source_id"] == e["target"] and r["target_id"] == e["source"]
                if (same_dir or rev_dir) and r["type"] == e["type"] and (
                        e.get("chunk_id") is None or r["chunk_id"] == e["chunk_id"]):
                    d = {"source": r["source_id"], "target": r["target_id"], "type": r["type"],
                         "evidence": r["evidence"], "chunk_id": r["chunk_id"]}
                    if d not in out:
                        out.append(d)
        return out

    def q8_reference(self, vetor, k=5, trace=None):
        return []

    def chunk_search(self, vetor, k, trace=None):
        self.calls.append("chunk_search")
        self._t(trace, "busca_vetorial_chunks", {"k": k, "vetor": vetor}, k)
        scored = sorted(((neo4j_cosine(v, vetor), i) for i, v in self.chunk_emb.items()), key=lambda x: (-x[0], x[1]))
        rows = []
        for s, cid in scored[:k]:
            c = self.chunks[cid]
            d = self.docs.get(c["doc_id"], {})
            rows.append({"chunk_id": cid, "position": c["position"], "text": c["text"], "doc_id": c["doc_id"],
                         "title": d.get("title"), "url": d.get("url"), "score": s})
        return rows

    def explain(self, query, params):
        self.calls.append("explain")
        if self.fail_generated:
            err = self.fail_generated.pop(0)
            if err:
                raise err
        return self.explain_result

    def run_generated(self, query, params, max_rows, trace=None):
        self.calls.append("run_generated")
        self._t(trace, "texto_para_cypher", params, len(self.generated_rows[:max_rows]))
        self.generated_queries.append(query)
        return self.generated_rows[:max_rows]
