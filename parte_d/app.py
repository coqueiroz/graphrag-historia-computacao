"""UI Streamlit (Pessoa D). Rodar: streamlit run app.py   |  ENGINE=real usa graphrag_engine.ask (Pessoa C)."""
import os
import streamlit as st
import streamlit.components.v1 as components

import graph_view

if os.getenv("ENGINE", "mock") == "real":
    from engine_real import ask
else:
    from mock_engine import ask

st.set_page_config(page_title="GraphRAG — História da Computação", layout="wide")
st.title("GraphRAG — História da Computação")
EXEMPLOS = ["Em que cidade fica a instituição onde trabalhava quem criou o Unix?",
            "Quem criou o Unix?", "Em que instituições trabalhou o criador da linguagem Go?",
            "Qual a capital da Austrália?"]  # última = pergunta-armadilha (deve recusar)

with st.sidebar:
    modo = st.radio("Modo", ["GraphRAG", "RAG vetorial puro", "Comparar lado a lado"])
    ex = st.selectbox("Exemplos", [""] + EXEMPLOS)
    st.markdown(graph_view.legenda(), unsafe_allow_html=True)
    st.caption("Borda grossa = nó semente · aresta vermelha = caminho usado · tamanho = PageRank")

pergunta = st.text_input("Pergunta", value=ex)


def mostrar(r, chave):
    if r.get("status") == "error":
        st.error(r["answer"])
    elif r["refused"]:
        st.warning(r["answer"])
    else:
        st.markdown(r["answer"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Confiança (heurística)", f"{r['confidence']:.0%}", help=r.get("conf_explanation") or None)
    c2.metric("Nós / arestas", f"{len(r['nodes'])} / {len(r['edges'])}")
    c3.metric("Latência", f"{r['latency_ms']} ms")
    st.progress(r["confidence"])
    st.caption("Confiança é uma heurística não calibrada, não uma probabilidade." + (f" Nível: {r['conf_level']}." if r.get("conf_level") else ""))
    for w in r.get("warnings", []):
        st.caption(f"⚠ {w}")
    if r["nodes"]:
        components.html(graph_view.render(r["nodes"], r["edges"]), height=540)
    if r["cypher"]:
        with st.expander(f"Cypher gerado ({r.get('cypher_status') or 'n/d'})", expanded=True):
            st.code(r["cypher"], language="cypher")
    with st.expander(f"Fontes citadas ({len(r['sources'])})"):
        for s in r["sources"]:
            marca = "✅ citada · " if s.get("cited") else ""
            link = f" · [fonte]({s['url']})" if s.get("url") else ""
            st.markdown(f"**[{s.get('id', s['n'])}] {s['doc']}** · {marca}`{s['chunk_id']}`{link}")
            st.caption(s["text"])
    if r.get("claims"):
        with st.expander("Afirmações verificadas"):
            st.dataframe([{"afirmação": c["text"], "fontes": ", ".join(c["sources"]), "com suporte": c["supported"]} for c in r["claims"]])
    if r["edges"]:
        with st.expander("Arestas e evidências"):
            st.dataframe([{"de": e["source"], "relação": e["type"], "para": e["target"],
                           "evidência": e.get("evidence", ""), "chunk": e.get("chunk_id", "")} for e in r["edges"]])


if pergunta:
    if modo == "Comparar lado a lado":
        a, b = st.columns(2)
        with a:
            st.subheader("RAG vetorial puro"); mostrar(ask(pergunta, "rag"), "a")
        with b:
            st.subheader("GraphRAG"); mostrar(ask(pergunta, "graphrag"), "b")
    else:
        mostrar(ask(pergunta, "rag" if modo.startswith("RAG") else "graphrag"), "x")
