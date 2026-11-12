"""
CLI da Parte C — perguntas livres no terminal.

Exemplos (PowerShell, na raiz do projeto):
  python -m parte_c.cli -q "Quem criou o Unix?"
  python -m parte_c.cli -q "Em que cidade fica a instituição onde trabalhava quem criou o Unix?" --hops 3 --show-trace
  python -m parte_c.cli -q "Quem criou o Unix?" --mode baseline
  python -m parte_c.cli -q "Quem criou o Unix?" --mode both
  python -m parte_c.cli -q "Quem criou o Unix?" --json > resposta.json
  python -m parte_c.cli -q "Quem criou o Unix?" --no-llm          # só recuperação, sem gastar API
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap


def _print(resp: dict, show_trace: bool) -> None:
    p = print
    c = resp["confidence"]
    p("=" * 100)
    p(f"[{resp['mode'].upper()}] {resp['question']}")
    p(f"status: {resp['status']} | confiança: {c['level']} ({c['score']:.2f}) | {resp['latency_ms']:.0f} ms"
      + (f" | LLM: {resp['llm'].get('provider')}/{resp['llm'].get('model')}" if resp.get("llm") else ""))
    p("-" * 100)
    for line in resp["answer"].splitlines():
        p(textwrap.fill(line, 100) if line.strip() else "")
    p("-" * 100)
    p(textwrap.fill(c["explanation"], 100))
    ret = resp.get("retrieval", {})
    if ret.get("seeds"):
        p(f"\nSementes ({ret.get('strategy')}, hops={ret.get('hops')}):")
        for s in ret["seeds"]:
            p(f"  - {s['name']} [{s['id']}] sim={s['similarity']:.3f} ({s['origin']})")
    gc = resp.get("generated_cypher") or {}
    if gc.get("status") and gc["status"] != "not_applicable":
        p(f"\nText-to-Cypher: {gc['status']}" + (f" — {gc['error']}" if gc.get("error") else ""))
        if gc.get("query"):
            p(textwrap.indent(gc["query"], "    "))
            p(f"    parâmetros: {gc.get('parameters')}")
    if resp["sources"]:
        p(f"\nFontes ({len(resp['sources'])}; * = citada na resposta):")
        for s in resp["sources"]:
            rel = s.get("relation")
            fato = (f"{rel['source_name'] or rel['source_id']} -[{rel['type']}]-> {rel['target_name'] or rel['target_id']}"
                    if rel else "trecho")
            p(f" {'*' if s.get('cited') else ' '}[{s['id']}] {fato}")
            p(f"       doc: {s.get('title')} | chunk: {s.get('chunk_id')} | {s.get('url') or ''}")
            p(f"       \"{(s.get('quote') or '')[:160]}\"")
    sg = resp.get("subgraph", {})
    if sg.get("edges"):
        p(f"\nSubgrafo: {len(sg['nodes'])} nós, {len(sg['edges'])} arestas (máx. salto = "
          f"{max(e['hop'] for e in sg['edges'])})")
    if resp["warnings"]:
        p("\nAvisos:")
        for w in resp["warnings"]:
            p(textwrap.fill(w, 100, initial_indent="  - ", subsequent_indent="    "))
    if show_trace and resp.get("trace"):
        t = resp["trace"]
        p(f"\nTrace: tempos {t['tempos_ms']} | contexto {t['contexto_chars']} chars")
        for q in t["consultas"]:
            p(f"  · {q['name']} ({q['rows']} linhas, {q['ms']} ms) params={q['parameters']}")
            if show_trace:
                p(textwrap.indent(q["query"], "      "))


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Perguntas ao GraphRAG (Parte C)")
    ap.add_argument("-q", "--question", required=True, help="pergunta em português")
    ap.add_argument("--mode", choices=["graphrag", "baseline", "both"], default="graphrag")
    ap.add_argument("--strategy", choices=["auto", "vector", "text2cypher"], default=None)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--hops", type=int, choices=[1, 2, 3], default=None)
    ap.add_argument("--no-llm", action="store_true", help="não chama LLM (modo retrieval_only)")
    ap.add_argument("--show-trace", action="store_true", help="mostra consultas executadas e tempos")
    ap.add_argument("--json", action="store_true", help="imprime o JSON completo do contrato")
    a = ap.parse_args(argv)

    from parte_c.service import fechar, responder_pergunta

    kwargs = {"estrategia": a.strategy, "top_k": a.top_k, "hops": a.hops, "incluir_trace": a.show_trace or a.json}
    if a.no_llm:
        kwargs["llm"] = None
    modes = ["graphrag", "baseline"] if a.mode == "both" else [a.mode]
    out = [responder_pergunta(a.question, m, **kwargs) for m in modes]
    fechar()
    if a.json:
        print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=2))
    else:
        for r in out:
            _print(r, a.show_trace)
    return 0 if all(r["status"] != "error" for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
