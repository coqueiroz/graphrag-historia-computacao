"""C7 — conjunto de perguntas e script de avaliação (sem Neo4j/LLM reais)."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes_parte_c import FakeGateway, fake_embed, load_graph  # noqa: E402

from parte_c.config import Settings  # noqa: E402
from parte_c.evaluation import load_questions, run, score_response  # noqa: E402


def test_perguntas_tem_distribuicao_e_gabarito_verificavel():
    qs = load_questions()
    tipos = {}
    for q in qs:
        tipos[q["type"]] = tipos.get(q["type"], 0) + 1
        assert set(q) >= {"id", "question", "type", "expected_answer", "gold_paths", "gold_sources", "answerable",
                          "validacao_humana"}
    assert tipos == {"direta": 5, "2_saltos": 10, "3_saltos": 10, "sem_resposta": 5}
    g = load_graph()
    reais = {(r["source_id"], r["type"], r["target_id"], r["chunk_id"]) for r in g["relations"]}
    for q in qs:
        for p in q["gold_paths"]:
            assert (p["source_id"], p["type"], p["target_id"], p["chunk_id"]) in reais, q["id"]
        assert q["answerable"] == bool(q["gold_paths"])


def test_metricas_de_uma_resposta():
    q = {"answerable": True, "expected_keywords": [["Murray Hill"]], "gold_sources": ["a#0", "b#0"],
         "gold_paths": [{"source_id": "x", "type": "T", "target_id": "y"}]}
    r = {"mode": "graphrag", "status": "answered", "answer": "Fica em Murray Hill [F1].",
         "sources": [{"chunk_id": "a#0"}], "subgraph": {"edges": [{"source": "x", "type": "T", "target": "y"}]},
         "claims": [{"supported": True}, {"supported": False}], "warnings": [],
         "confidence": {"score": 0.5}, "latency_ms": 10}
    m = score_response(q, r)
    assert m["recall_fontes_ouro"] == 0.5 and m["recall_arestas_ouro"] == 1.0 and m["acerto_palavras"] == 1
    assert m["abstencao_correta"] == 1 and m["taxa_sem_suporte"] == 0.5
    q2 = {"answerable": False, "expected_keywords": [], "gold_sources": [], "gold_paths": []}
    m2 = score_response(q2, dict(r, status="insufficient_evidence", claims=[]))
    assert m2["abstencao_correta"] == 1 and m2["recall_fontes_ouro"] is None


def test_execucao_ponta_a_ponta_gera_arquivos(tmp_path):
    out = run(["graphrag", "baseline"], limite=3, no_llm=True, saida=tmp_path,
              settings=Settings(min_seed_score=0.5), gateway=FakeGateway(), embed_fn=fake_embed)
    for nome in ["respostas.jsonl", "metricas.csv", "resumo.csv", "resumo.md", "anotacao_humana.csv", "config.json"]:
        assert (out / nome).exists(), nome
    linhas = list(csv.DictReader((out / "metricas.csv").open(encoding="utf-8-sig"), delimiter=";"))
    assert len(linhas) == 6 and {l["modo"] for l in linhas} == {"graphrag", "baseline"}
    cfg = json.loads((out / "config.json").read_text(encoding="utf-8"))
    assert cfg["no_llm"] is True and "password" not in json.dumps(cfg).lower()
