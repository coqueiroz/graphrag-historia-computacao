"""
Constrói `parte_c/avaliacao/perguntas.jsonl` a partir de uma especificação de perguntas + caminhos-ouro,
VALIDANDO cada aresta contra `grafo.json` (os mesmos dados carregados no Neo4j pela Pessoa B).

Nada é inventado: se uma aresta-ouro não existir no grafo, o script falha. As evidências e chunk_ids
do gabarito são copiados do próprio grafo. `validacao_humana` começa como "pendente": a Pessoa D (ou o
grupo) deve conferir cada pergunta/gabarito lendo as fontes antes de usar os números no artigo.

Uso:  python parte_c/avaliacao/construir_gabarito.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (id, tipo, pergunta, resposta esperada, palavras-chave [grupos de alternativas], arestas-ouro (origem, TIPO, destino))
SPEC = [
    # ----------------------------------------------------------------- diretas (1 salto)
    ("D01", "direta", "Quem criou o Unix?",
     "Ken Thompson e Dennis Ritchie.",
     [["Thompson"], ["Ritchie"]],
     [("pessoa:ken_thompson", "CRIOU", "tecnologia:unix"), ("pessoa:dennis_ritchie", "CRIOU", "tecnologia:unix")]),
    ("D02", "direta", "Onde o Unix foi desenvolvido?", "No Bell Labs.", [["Bell"]],
     [("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs")]),
    ("D03", "direta", "Que linguagem de programação John McCarthy criou?", "Lisp.", [["Lisp"]],
     [("pessoa:john_mccarthy", "CRIOU", "tecnologia:lisp")]),
    ("D04", "direta", "Em que linguagem o COBOL foi baseado?", "No FLOW-MATIC.", [["Flow-Matic", "FLOW-MATIC", "Flow Matic"]],
     [("tecnologia:cobol", "BASEADO_EM", "tecnologia:flow_matic")]),
    ("D05", "direta", "Onde fica o Bell Labs?", "Em Murray Hill.", [["Murray Hill"]],
     [("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill")]),
    # ----------------------------------------------------------------- 2 saltos
    ("M01", "2_saltos", "Em que cidade fica a instituição onde o Unix foi desenvolvido?",
     "Murray Hill (Unix -> Bell Labs -> Murray Hill).", [["Murray Hill"]],
     [("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs"),
      ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill")]),
    ("M02", "2_saltos", "Quem criou a linguagem na qual o COBOL foi baseado?",
     "Grace Hopper (COBOL baseado em FLOW-MATIC, criado por Grace Hopper).", [["Hopper"]],
     [("tecnologia:cobol", "BASEADO_EM", "tecnologia:flow_matic"),
      ("pessoa:grace_hopper", "CRIOU", "tecnologia:flow_matic")]),
    ("M03", "2_saltos", "Em que instituições trabalhou o criador da linguagem Go?",
     "Google e Bell Labs.", [["Google"], ["Bell"]],
     [("pessoa:ken_thompson", "CRIOU", "tecnologia:go"),
      ("pessoa:ken_thompson", "TRABALHOU_EM", "instituicao:google"),
      ("pessoa:ken_thompson", "TRABALHOU_EM", "instituicao:bell_labs")]),
    ("M04", "2_saltos", "Que outras tecnologias foram desenvolvidas na mesma instituição que o Unix?",
     "No Bell Labs também foram desenvolvidos C, B, C++, S, SNOBOL, AWK e AMPL.", [["C++", "AWK", "SNOBOL", "AMPL"]],
     [("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs"),
      ("tecnologia:c_plusplus", "DESENVOLVIDO_EM", "instituicao:bell_labs"),
      ("tecnologia:awk", "DESENVOLVIDO_EM", "instituicao:bell_labs")]),
    ("M05", "2_saltos", "Quem criou o sistema no qual o Linux é baseado?",
     "O Linux é baseado no Unix, criado por Ken Thompson e Dennis Ritchie.", [["Thompson", "Ritchie"]],
     [("tecnologia:linux", "BASEADO_EM", "tecnologia:unix"), ("pessoa:ken_thompson", "CRIOU", "tecnologia:unix"),
      ("pessoa:dennis_ritchie", "CRIOU", "tecnologia:unix")]),
    ("M06", "2_saltos", "Em que conceito se baseia a linguagem criada por John McCarthy?",
     "No cálculo lambda (McCarthy criou Lisp, baseado no cálculo lambda).", [["lambda"]],
     [("pessoa:john_mccarthy", "CRIOU", "tecnologia:lisp"), ("tecnologia:lisp", "BASEADO_EM", "conceito:calculo_lambda")]),
    ("M07", "2_saltos", "Que tecnologias foram desenvolvidas no centro de pesquisa da Xerox onde Alan Kay trabalhou?",
     "Smalltalk, Ethernet, a impressora a laser e o modelo cliente-servidor (Xerox PARC).",
     [["Smalltalk", "Ethernet", "laser"]],
     [("pessoa:alan_kay", "TRABALHOU_EM", "instituicao:xerox_parc"),
      ("tecnologia:smalltalk", "DESENVOLVIDO_EM", "instituicao:xerox_parc"),
      ("tecnologia:ethernet", "DESENVOLVIDO_EM", "instituicao:xerox_parc")]),
    ("M08", "2_saltos", "Que computadores foram influenciados pela máquina criada por John Mauchly?",
     "O ENIAC (criado por Mauchly) influenciou EDVAC, ORDVAC, SEAC e UNIVAC.", [["EDVAC", "ORDVAC", "SEAC", "UNIVAC"]],
     [("pessoa:john_mauchly", "CRIOU", "tecnologia:eniac"), ("tecnologia:eniac", "INFLUENCIOU", "tecnologia:edvac")]),
    ("M09", "2_saltos", "Com quem colaborou o criador da World Wide Web?",
     "Tim Berners-Lee colaborou com Robert Cailliau, Rosemary Leith e Nigel Shadbolt.", [["Cailliau", "Leith", "Shadbolt"]],
     [("pessoa:tim_berners_lee", "CRIOU", "tecnologia:world_wide_web"),
      ("pessoa:tim_berners_lee", "COLABOROU_COM", "pessoa:robert_cailliau")]),
    ("M10", "2_saltos", "Que prêmios recebeu Ken Thompson, colaborador de Dennis Ritchie?",
     "Prêmio Turing, Medalha Richard W. Hamming, Medalha Nacional de Tecnologia e Inovação, Prêmio Japão.",
     [["Turing"]],
     [("pessoa:dennis_ritchie", "COLABOROU_COM", "pessoa:ken_thompson"),
      ("pessoa:ken_thompson", "RECEBEU", "premio:premio_turing")]),
    # ----------------------------------------------------------------- 3 saltos
    ("T01", "3_saltos", "Em que cidade fica a instituição onde trabalhava quem criou o Unix?",
     "Murray Hill (Unix <- Ken Thompson/Dennis Ritchie -> Bell Labs -> Murray Hill).", [["Murray Hill"]],
     [("pessoa:ken_thompson", "CRIOU", "tecnologia:unix"), ("pessoa:ken_thompson", "TRABALHOU_EM", "instituicao:bell_labs"),
      ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill")]),
    ("T02", "3_saltos", "Em que cidade fica a instituição onde foi desenvolvido o sistema no qual o Linux se baseia?",
     "Murray Hill (Linux -> Unix -> Bell Labs -> Murray Hill).", [["Murray Hill"]],
     [("tecnologia:linux", "BASEADO_EM", "tecnologia:unix"), ("tecnologia:unix", "DESENVOLVIDO_EM", "instituicao:bell_labs"),
      ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill")]),
    ("T03", "3_saltos", "Que prêmios recebeu a pessoa que criou a linguagem na qual o COBOL foi baseado?",
     "Grace Hopper recebeu a Medalha Nacional de Tecnologia e a Medalha Presidencial de Liberdade.",
     [["Medalha Nacional de Tecnologia", "Presidencial"]],
     [("tecnologia:cobol", "BASEADO_EM", "tecnologia:flow_matic"), ("pessoa:grace_hopper", "CRIOU", "tecnologia:flow_matic"),
      ("pessoa:grace_hopper", "RECEBEU", "premio:medalha_presidencial_de_liberdade")]),
    ("T04", "3_saltos", "Em que cidade fica a instituição onde trabalhava o criador da teoria da informação?",
     "Claude Shannon trabalhou no Bell Labs (Murray Hill) e no MIT (Cambridge/Boston).",
     [["Murray Hill", "Cambridge", "Boston", "Princeton"]],
     [("pessoa:claude_shannon", "CRIOU", "conceito:teoria_da_informacao"),
      ("pessoa:claude_shannon", "TRABALHOU_EM", "instituicao:bell_labs"),
      ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill")]),
    ("T05", "3_saltos", "Em que estado fica o instituto onde trabalhava o criador da arquitetura de von Neumann?",
     "Nova Jersey (Instituto de Estudos Avançados de Princeton).", [["Jersey", "Jérsei", "Jersei"]],
     [("pessoa:john_von_neumann", "CRIOU", "conceito:arquitetura_de_von_neumann"),
      ("pessoa:john_von_neumann", "TRABALHOU_EM", "instituicao:instituto_de_estudos_avancados_de_princeton"),
      ("instituicao:instituto_de_estudos_avancados_de_princeton", "LOCALIZADO_EM", "local:nova_jersey")]),
    ("T06", "3_saltos", "Que prêmios recebeu o colaborador de Dennis Ritchie que também criou a linguagem B?",
     "Ken Thompson: Prêmio Turing, Medalha Richard W. Hamming, entre outros.", [["Turing", "Hamming"]],
     [("pessoa:dennis_ritchie", "COLABOROU_COM", "pessoa:ken_thompson"), ("pessoa:ken_thompson", "CRIOU", "tecnologia:b"),
      ("pessoa:ken_thompson", "RECEBEU", "premio:premio_turing")]),
    ("T07", "3_saltos", "Em que cidade fica a instituição onde foi desenvolvida a linguagem que a linguagem C influenciou?",
     "Murray Hill (C influenciou C++, desenvolvida no Bell Labs, em Murray Hill).", [["Murray Hill"]],
     [("tecnologia:c_linguagem_de_programacao", "INFLUENCIOU", "tecnologia:c_plusplus"),
      ("tecnologia:c_plusplus", "DESENVOLVIDO_EM", "instituicao:bell_labs"),
      ("instituicao:bell_labs", "LOCALIZADO_EM", "local:murray_hill")]),
    ("T08", "3_saltos", "Em que cidade fica a universidade onde trabalhava o criador do Lisp?",
     "Palo Alto (John McCarthy -> Universidade Stanford -> Palo Alto). Também aceito: Cambridge (MIT) — o "
     "trecho lisp#2 diz que o LISP 1.5 foi 'desenvolvido por McCarthy e outros do MIT', mas o grafo não tem "
     "a aresta McCarthy TRABALHOU_EM MIT (lacuna de extração). Pergunta ambígua.",
     [["Palo Alto", "Cambridge"]],
     [("pessoa:john_mccarthy", "CRIOU", "tecnologia:lisp"),
      ("pessoa:john_mccarthy", "TRABALHOU_EM", "instituicao:universidade_stanford"),
      ("instituicao:universidade_stanford", "LOCALIZADO_EM", "local:palo_alto")]),
    ("T09", "3_saltos", "Quem colaborou com o criador do Lisp e que tecnologias essa pessoa criou?",
     "Marvin Minsky (criou SNARC, Logo, Perceptrons, entre outros).", [["Minsky"]],
     [("pessoa:john_mccarthy", "CRIOU", "tecnologia:lisp"), ("pessoa:marvin_minsky", "COLABOROU_COM", "pessoa:john_mccarthy"),
      ("pessoa:marvin_minsky", "CRIOU", "tecnologia:snarc")]),
    ("T10", "3_saltos", "Em que cidade fica o laboratório onde trabalhou o criador do HTTP?",
     "Genebra (Tim Berners-Lee -> CERN -> Genebra).", [["Genebra"]],
     [("pessoa:tim_berners_lee", "CRIOU", "tecnologia:http"), ("pessoa:tim_berners_lee", "TRABALHOU_EM", "instituicao:cern"),
      ("instituicao:cern", "LOCALIZADO_EM", "local:genebra")]),
    # ----------------------------------------------------------------- sem resposta no corpus
    ("U01", "sem_resposta", "Qual é a capital da Austrália?",
     "Sem resposta no corpus (a Austrália só é citada na biografia de Alan Kay).", [], []),
    ("U02", "sem_resposta", "Quem criou a linguagem Rust?", "Sem resposta no corpus.", [], []),
    ("U03", "sem_resposta", "Em que ano Linus Torvalds recebeu o Prêmio Turing?",
     "Sem resposta no corpus (premissa não sustentada; Linus Torvalds não aparece no grafo).", [], []),
    ("U04", "sem_resposta", "Qual era o salário de Ken Thompson no Bell Labs?", "Sem resposta no corpus.", [], []),
    ("U05", "sem_resposta", "Quantos transistores tinha o processador Intel 4004?", "Sem resposta no corpus.", [], []),
]


def main() -> int:
    g = json.loads((ROOT / "grafo.json").read_text(encoding="utf-8"))
    ents = {e["id"] for e in g["entities"]}
    by_triple: dict[tuple, list[dict]] = {}
    for r in g["relations"]:
        by_triple.setdefault((r["source_id"], r["type"], r["target_id"]), []).append(r)
    out, erros = [], []
    for qid, tipo, pergunta, esperado, kws, gold in SPEC:
        paths = []
        for s, t, o in gold:
            if s not in ents or o not in ents:
                erros.append(f"{qid}: entidade inexistente em {s} -{t}-> {o}")
                continue
            rels = by_triple.get((s, t, o))
            if not rels:
                erros.append(f"{qid}: aresta inexistente {s} -{t}-> {o}")
                continue
            for r in rels:
                paths.append({"source_id": s, "type": t, "target_id": o, "chunk_id": r["chunk_id"],
                              "evidence": r["evidence"]})
        out.append({
            "id": qid, "question": pergunta, "type": tipo, "answerable": tipo != "sem_resposta",
            "expected_answer": esperado, "expected_keywords": kws,
            "gold_paths": paths, "gold_sources": sorted({p["chunk_id"] for p in paths}),
            "validacao_humana": "pendente", "notas": "",
        })
    if erros:
        print("\n".join(erros))
        return 1
    dest = Path(__file__).resolve().parent / "perguntas.jsonl"
    with dest.open("w", encoding="utf-8") as f:
        for row in out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tipos = {}
    for r in out:
        tipos[r["type"]] = tipos.get(r["type"], 0) + 1
    print(f"{len(out)} perguntas gravadas em {dest.relative_to(ROOT)}: {tipos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
