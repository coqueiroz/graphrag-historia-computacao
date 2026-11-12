"""
C7 — Experimento comparativo GraphRAG x baseline (RAG vetorial puro).

Roda as perguntas de `parte_c/avaliacao/perguntas.jsonl` nos dois modos, com os MESMOS parâmetros, e grava em
`parte_c/avaliacao/resultados/<data-hora>/`:
  respostas.jsonl       resposta completa (contrato JSON) de cada pergunta x modo
  metricas.csv          uma linha por pergunta x modo com as métricas automáticas
  resumo.csv / resumo.md médias por modo e por tipo de pergunta
  anotacao_humana.csv   planilha para a rubrica humana (preencher à mão)
  config.json           parâmetros usados (sem segredos)

Métricas automáticas (APROXIMADAS; conferir amostra manualmente antes de publicar):
  recall_fontes_ouro   fração dos chunk_ids do gabarito presentes nas fontes recuperadas
  recall_arestas_ouro  (GraphRAG) fração das arestas-ouro presentes no subgrafo devolvido
  acerto_palavras      1 se cada grupo de palavras-chave esperado aparece na resposta (só respondíveis)
  abstencao_correta    respondível -> status answered; sem resposta -> insufficient_evidence
  taxa_sem_suporte     afirmações sem suporte / afirmações (alucinação operacional detectada)
  citacoes_invalidas   1 se o LLM citou [Fn] inexistente (removida pelo validador)
  latencia_ms, confianca
NUNCA preencher números à mão: se o Neo4j/LLM não estiverem disponíveis, o script falha ou marca o erro.

Uso:
  python -m parte_c.evaluation                       # todas as perguntas, graphrag + baseline
  python -m parte_c.evaluation --limite 3 --no-llm   # ensaio rápido sem gastar API
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Optional

from parte_c.config import ROOT, Settings
from parte_c.retrieval import norm_text

PERGUNTAS = ROOT / "parte_c" / "avaliacao" / "perguntas.jsonl"
METRICAS = ["recall_fontes_ouro", "recall_arestas_ouro", "acerto_palavras", "abstencao_correta",
            "taxa_sem_suporte", "citacoes_invalidas", "confianca", "latencia_ms"]


def load_questions(path: Path = PERGUNTAS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_response(q: dict, r: dict) -> dict:
    """Métricas automáticas de UMA resposta (dict do contrato) contra o gabarito."""
    src_chunks = {s.get("chunk_id") for s in r.get("sources", [])}
    gold_chunks = set(q.get("gold_sources") or [])
    edges = {(e["source"], e["type"], e["target"]) for e in r.get("subgraph", {}).get("edges", [])}
    gold_edges = {(p["source_id"], p["type"], p["target_id"]) for p in q.get("gold_paths") or []}
    claims = r.get("claims") or []
    status = r.get("status")
    m: dict = {
        "recall_fontes_ouro": (len(src_chunks & gold_chunks) / len(gold_chunks)) if gold_chunks else None,
        "recall_arestas_ouro": (len(edges & gold_edges) / len(gold_edges))
        if gold_edges and r.get("mode") == "graphrag" else None,
        "abstencao_correta": int((status == "answered") if q.get("answerable") else
                                 (status == "insufficient_evidence")) if status in ("answered", "insufficient_evidence") else None,
        "taxa_sem_suporte": (sum(not c.get("supported") for c in claims) / len(claims))
        if claims and status == "answered" else None,
        "citacoes_invalidas": int(any("Citações inexistentes" in w for w in r.get("warnings", []))),
        "confianca": r.get("confidence", {}).get("score"),
        "latencia_ms": r.get("latency_ms"),
    }
    kws = q.get("expected_keywords") or []
    if q.get("answerable") and kws and status == "answered":
        ans = norm_text(r.get("answer", ""))
        m["acerto_palavras"] = int(all(any(norm_text(k) in ans for k in grupo) for grupo in kws))
    elif q.get("answerable") and kws and status in ("insufficient_evidence", "retrieval_only"):
        m["acerto_palavras"] = 0 if status == "insufficient_evidence" else None
    else:
        m["acerto_palavras"] = None
    return m


def _avg(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(mean(vals), 4) if vals else None


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["modo"], "TODAS"), []).append(r)
        groups.setdefault((r["modo"], r["tipo"]), []).append(r)
    for (modo, tipo), rs in sorted(groups.items()):
        line = {"modo": modo, "tipo": tipo, "n": len(rs),
                "erros": sum(r["status"] == "error" for r in rs),
                "respondidas": sum(r["status"] == "answered" for r in rs),
                "abstencoes": sum(r["status"] == "insufficient_evidence" for r in rs)}
        for k in METRICAS:
            line[k] = _avg(r.get(k) for r in rs)
        out.append(line)
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)


def run(modos: list[str], limite: Optional[int] = None, ids: Optional[list[str]] = None, no_llm: bool = False,
        saida: Optional[Path] = None, estrategia: Optional[str] = None, **inject) -> Path:
    from parte_c.service import fechar, responder_pergunta

    settings = inject.pop("settings", None) or Settings.from_env()
    qs = load_questions(inject.pop("perguntas", PERGUNTAS))
    if ids:
        qs = [q for q in qs if q["id"] in ids]
    if limite:
        qs = qs[:limite]
    out_dir = saida or ROOT / "parte_c" / "avaliacao" / "resultados" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    kw = {"settings": settings, "incluir_trace": True, "estrategia": estrategia, **inject}
    if no_llm:
        kw["llm"] = None

    rows, anot = [], []
    with (out_dir / "respostas.jsonl").open("w", encoding="utf-8") as fresp:
        for i, q in enumerate(qs, 1):
            for modo in modos:
                t0 = time.perf_counter()
                r = responder_pergunta(q["question"], modo, **kw)
                fresp.write(json.dumps({"id": q["id"], "modo": modo, "resposta": r}, ensure_ascii=False) + "\n")
                m = score_response(q, r)
                rows.append({"id": q["id"], "tipo": q["type"], "modo": modo, "status": r["status"],
                             "nivel_confianca": r["confidence"]["level"], **m,
                             "n_fontes": len(r["sources"]), "n_arestas": len(r["subgraph"]["edges"]),
                             "estrategia": r.get("retrieval", {}).get("strategy"),
                             "cypher_status": r.get("generated_cypher", {}).get("status")})
                anot.append({"id": q["id"], "modo": modo, "pergunta": q["question"],
                             "resposta_esperada": q["expected_answer"], "resposta_sistema": r["answer"],
                             "correcao_0a2": "", "suporte_nas_fontes_0a2": "", "alucinacao_sim_nao": "",
                             "multi_hop_correto_sim_nao": "", "observacoes": ""})
                print(f"[{i}/{len(qs)}] {q['id']} {modo:8s} {r['status']:22s} "
                      f"conf={r['confidence']['score']:.2f} {time.perf_counter() - t0:.1f}s", flush=True)
    fechar()
    _write_csv(out_dir / "metricas.csv", rows)
    resumo = summarize(rows)
    _write_csv(out_dir / "resumo.csv", resumo)
    _write_csv(out_dir / "anotacao_humana.csv", anot)
    cols = ["modo", "tipo", "n", "erros", "respondidas", "abstencoes"] + METRICAS
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join("" if l[c] is None else str(l[c]) for c in cols) + " |" for l in resumo]
    (out_dir / "resumo.md").write_text(
        "# Resumo automático (métricas aproximadas — conferir amostra manualmente)\n\n" + "\n".join(md) + "\n",
        encoding="utf-8")
    (out_dir / "config.json").write_text(json.dumps(
        {"modos": modos, "n_perguntas": len(qs), "no_llm": no_llm, "estrategia": estrategia or settings.strategy,
         "config": settings.public(), "data": datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Avaliação GraphRAG x baseline (Parte C)")
    ap.add_argument("--modos", nargs="+", default=["graphrag", "baseline"], choices=["graphrag", "baseline"])
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--estrategia", choices=["auto", "vector", "text2cypher"], default=None)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--saida", type=Path, default=None)
    a = ap.parse_args(argv)
    out = run(a.modos, a.limite, a.ids, a.no_llm, a.saida, a.estrategia)
    print(f"\nResultados em: {out}")
    print((out / "resumo.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
