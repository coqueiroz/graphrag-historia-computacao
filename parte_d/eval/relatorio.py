"""Transforma o resumo.csv da avaliação OFICIAL da Parte C em tabela LaTeX e gráfico para o artigo.
Uso: python eval/relatorio.py CAMINHO/evaluation/resultados/20261111-222956
Gera artigo/tabela_resultados.tex e artigo/fig_acerto.png. Os números são os da Parte C (automáticos, aproximados)."""
import csv, sys
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(__doc__)
pasta = Path(sys.argv[1])
raiz = Path(__file__).parent.parent / "artigo"
rows = list(csv.DictReader(open(pasta / "resumo.csv", encoding="utf-8-sig"), delimiter=";"))
ORDEM = ["direta", "2_saltos", "3_saltos", "sem_resposta", "TODAS"]
NOME = {"direta": "1 salto (direta)", "2_saltos": "2 saltos", "3_saltos": "3 saltos", "sem_resposta": "Armadilhas", "TODAS": "Todas"}
MODO = {"baseline": "RAG puro", "graphrag": "GraphRAG"}
rows.sort(key=lambda r: (ORDEM.index(r["tipo"]), r["modo"]))
COLS = [("tipo", "Faixa"), ("modo", "Modo"), ("n", "N"), ("respondidas", "Resp."), ("abstencoes", "Abst."),
        ("acerto_palavras", "Acerto"), ("abstencao_correta", "Abst. correta"), ("recall_fontes_ouro", "Recall fontes"),
        ("taxa_sem_suporte", "Sem suporte"), ("latencia_ms", "Lat. (s)")]


def cel(r, c):
    v = r[c]
    if c == "tipo": return NOME[v]
    if c == "modo": return MODO[v]
    if c == "latencia_ms": return f"{float(v)/1000:.1f}" if v else "--"
    return v or "--"


corpo = "\n".join(" & ".join(cel(r, c) for c, _ in COLS) + " \\\\" for r in rows)
(raiz / "tabela_resultados.tex").write_text(
    "\\begin{table}[h]\\centering\\small\\caption{Resultados automáticos (execução oficial da Parte C).}\n"
    "\\begin{tabular}{llrrrrrrrr}\\toprule\n" + " & ".join(n for _, n in COLS) + " \\\\\\midrule\n" + corpo +
    "\n\\bottomrule\\end{tabular}\\end{table}\n", encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
faixas = [t for t in ORDEM if t != "sem_resposta"]
val = {m: [float(next(r for r in rows if r["tipo"] == t and r["modo"] == m)["acerto_palavras"] or 0) for t in faixas] for m in MODO}
x = range(len(faixas))
fig, ax = plt.subplots(figsize=(5.5, 3))
ax.bar([i - 0.2 for i in x], val["baseline"], 0.4, label="RAG puro")
ax.bar([i + 0.2 for i in x], val["graphrag"], 0.4, label="GraphRAG")
ax.set_xticks(list(x)); ax.set_xticklabels([NOME[t] for t in faixas]); ax.set_ylabel("Acerto (palavras-chave)"); ax.set_ylim(0, 1)
ax.legend(); fig.tight_layout(); fig.savefig(raiz / "fig_acerto.png", dpi=200)
print("gerado: tabela_resultados.tex e fig_acerto.png")
