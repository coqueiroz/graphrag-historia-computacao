"""
EXEMPLO mínimo para a Pessoa D (não é a interface final). Rodar na raiz do projeto:
    pip install streamlit pyvis
    streamlit run docs/exemplo_streamlit_pessoa_d.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # raiz do projeto

import streamlit as st  # noqa: E402

from parte_c import responder_pergunta  # noqa: E402

CORES = {"Pessoa": "#4C78A8", "Instituicao": "#F58518", "Tecnologia": "#54A24B", "Conceito": "#B279A2",
         "Premio": "#E45756", "Local": "#72B7B2"}

st.set_page_config(page_title="GraphRAG — História da Computação", layout="wide")
st.title("GraphRAG — História da Computação")
pergunta = st.text_input("Pergunta", "Quem criou o Unix?")
modo = st.radio("Modo", ["graphrag", "baseline", "comparar"], horizontal=True)


@st.cache_data(show_spinner=False)
def perguntar(q: str, m: str) -> dict:
    return responder_pergunta(q, m, incluir_trace=True)


def mostrar(r: dict) -> None:
    c = r["confidence"]
    st.markdown(f"**Status:** `{r['status']}` · **Confiança:** {c['level']} ({c['score']:.2f}) · {r['latency_ms']:.0f} ms")
    st.caption(c["explanation"])
    st.write(r["answer"])
    for w in r["warnings"]:
        st.warning(w)
    with st.expander(f"Fontes ({len(r['sources'])})", expanded=True):
        for s in r["sources"]:
            rel = s.get("relation")
            fato = f"{rel['source_name']} —{rel['type']}→ {rel['target_name']}" if rel else "trecho"
            marca = "⭐ " if s["cited"] else ""
            st.markdown(f"{marca}**[{s['id']}]** {fato} — *{s['title']}* (`{s['chunk_id']}`) "
                        + (f"[link]({s['url']})" if s.get("url") else ""))
            st.caption(s["quote"][:400])
    if r["subgraph"]["edges"]:
        with st.expander("Subgrafo", expanded=True):
            try:
                from pyvis.network import Network
                net = Network(height="450px", width="100%", directed=True)
                for n in r["subgraph"]["nodes"]:
                    net.add_node(n["id"], label=n["name"], title=f"{n['label']} · {n['id']}",
                                 color=CORES.get(n["label"], "#999"))
                citadas = {(s["relation"]["source_id"], s["relation"]["type"], s["relation"]["target_id"])
                           for s in r["sources"] if s["cited"] and s.get("relation")}
                for e in r["subgraph"]["edges"]:
                    net.add_edge(e["source"], e["target"], label=e["type"], title=e["evidence"],
                                 width=3 if (e["source"], e["type"], e["target"]) in citadas else 1)
                st.components.v1.html(net.generate_html(), height=470)
            except ImportError:
                st.json(r["subgraph"])
    gc = r["generated_cypher"]
    if gc["status"] != "not_applicable":
        with st.expander(f"Text-to-Cypher: {gc['status']}"):
            if gc.get("query"):
                st.code(gc["query"], language="cypher")
            st.write(gc.get("error") or gc.get("rationale") or "")


if st.button("Perguntar") and pergunta.strip():
    if modo == "comparar":
        a, b = st.columns(2)
        with a:
            st.subheader("GraphRAG")
            mostrar(perguntar(pergunta, "graphrag"))
        with b:
            st.subheader("Baseline (RAG vetorial)")
            mostrar(perguntar(pergunta, "baseline"))
    else:
        mostrar(perguntar(pergunta, modo))
