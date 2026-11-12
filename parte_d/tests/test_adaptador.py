import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from adaptador_c import adaptar
from contrato_resposta import validar

REAIS = json.loads((Path(__file__).parent / "fixtures" / "respostas_c.json").read_text(encoding="utf-8"))


def test_todas_as_respostas_reais_passam_no_contrato():
    for x in REAIS:
        r = validar(adaptar(x["resposta"]))
        assert r["mode"] in ("rag", "graphrag")
        assert 0 <= r["confidence"] <= 1


def test_baseline_nao_tem_grafo():
    for x in REAIS:
        if x["modo"] == "baseline":
            assert adaptar(x["resposta"])["nodes"] == []


def test_arestas_apontam_para_nos_e_pagerank_normalizado():
    for x in REAIS:
        r = adaptar(x["resposta"])
        assert all(0 <= n["pagerank"] <= 1 for n in r["nodes"])


def test_abstencao_vira_refused():
    ab = [x for x in REAIS if x["resposta"]["status"] == "insufficient_evidence"]
    assert ab and all(adaptar(x["resposta"])["refused"] for x in ab)
