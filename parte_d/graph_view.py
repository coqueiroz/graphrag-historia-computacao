"""Grafo interativo (vis-network via CDN) para st.components.v1.html. Sem dependência de pyvis."""
import html, json

CORES = {"Pessoa": "#4C78A8", "Instituicao": "#F58518", "Tecnologia": "#54A24B",
         "Premio": "#E45756", "Conceito": "#B279A2", "Local": "#9D9D9D"}


def render(nodes, edges, altura=520):
    vn = [{"id": n["id"], "label": n["name"], "title": f"{n['label']}: {n['name']}",
           "color": CORES.get(n["label"], "#888"), "size": 14 + 40 * n.get("pagerank", 0),
           "borderWidth": 4 if n.get("seed") else 1, "font": {"color": "#222"}} for n in nodes]
    ve = [{"from": e["source"], "to": e["target"], "label": e["type"], "arrows": "to",
           "title": html.escape(e.get("evidence", "")), "width": 3 if e.get("on_path") else 1,
           "color": "#d62728" if e.get("on_path") else "#aaa", "font": {"size": 9, "align": "middle"}} for e in edges]
    return f"""<div id="g" style="height:{altura}px;border:1px solid #ddd;border-radius:6px"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js"></script>
<script>new vis.Network(document.getElementById('g'),{{nodes:new vis.DataSet({json.dumps(vn)}),edges:new vis.DataSet({json.dumps(ve)})}},
{{physics:{{solver:'forceAtlas2Based',stabilization:{{iterations:150}}}},interaction:{{hover:true,tooltipDelay:100}}}});</script>"""


def legenda():
    return " ".join(f'<span style="color:{c}">●</span> {l}' for l, c in CORES.items())
