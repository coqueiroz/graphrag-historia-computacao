"""
Consultas Cypher multi-hop de REFERÊNCIA (Pessoa B).

Para que servem:
  - Pessoa C: exemplos few-shot para o text-to-Cypher e modelo da busca híbrida (Q8).
  - Pessoa D: "gabarito" de perguntas multi-hop para a avaliação (RAG vetorial x GraphRAG).
  - Artigo: demonstram travessia de 2+ saltos, uso de GDS (pagerank/community) e citação.

Toda consulta devolve `evidencias` (texto + chunk_id de cada aresta usada) para que a
resposta possa citar o subgrafo.

Uso:
  python consultas_referencia.py            # roda todas e imprime
  python consultas_referencia.py Q3         # roda só uma
"""
import sys

from neo4j_config import NEO4J_DATABASE, get_driver

# Os 10 tipos de relação do contrato, para caminhos genéricos.
REL = "TRABALHOU_EM|ESTUDOU_EM|CRIOU|ORIENTOU|RECEBEU|INFLUENCIOU|COLABOROU_COM|DESENVOLVIDO_EM|BASEADO_EM|LOCALIZADO_EM"

CONSULTAS = {
    "Q1": {
        "pergunta": "Em que cidade fica a instituição onde trabalhava quem criou {tecnologia}?",
        "saltos": 3,
        "params": {"tecnologia": "tecnologia:unix"},
        "cypher": """
MATCH p = (t:Entidade {id: $tecnologia})<-[:CRIOU]-(pessoa:Pessoa)
          -[:TRABALHOU_EM]->(inst:Instituicao)-[:LOCALIZADO_EM]->(local:Local)
RETURN t.name AS tecnologia, pessoa.name AS criador, inst.name AS instituicao, local.name AS cidade,
       [r IN relationships(p) | {tipo: type(r), evidencia: r.evidence, chunk: r.chunk_id}] AS evidencias
""",
    },
    "Q2": {
        "pergunta": "Quais colaboradores de {pessoa} receberam algum prêmio, e qual?",
        "saltos": 2,
        "params": {"pessoa": "pessoa:ken_thompson"},
        "cypher": """
MATCH p = (a:Pessoa {id: $pessoa})-[:COLABOROU_COM]-(c:Pessoa)-[:RECEBEU]->(pr:Premio)
RETURN c.name AS colaborador, collect(DISTINCT pr.name) AS premios,
       collect({tipo: type(relationships(p)[0]), evidencia: relationships(p)[0].evidence, chunk: relationships(p)[0].chunk_id})
     + collect({tipo: 'RECEBEU', evidencia: relationships(p)[1].evidence, chunk: relationships(p)[1].chunk_id}) AS evidencias
""",
    },
    "Q3": {
        "pergunta": "Quem passou pelas mesmas instituições que {pessoa}?",
        "saltos": 2,
        "params": {"pessoa": "pessoa:alan_turing"},
        "cypher": """
MATCH (a:Pessoa {id: $pessoa})-[r1:TRABALHOU_EM|ESTUDOU_EM]->(i:Instituicao)
      <-[r2:TRABALHOU_EM|ESTUDOU_EM]-(b:Pessoa)
WHERE a <> b
RETURN i.name AS instituicao, collect(DISTINCT b.name) AS pessoas,
       collect(DISTINCT {evidencia: r1.evidence, chunk: r1.chunk_id})
     + collect(DISTINCT {evidencia: r2.evidence, chunk: r2.chunk_id}) AS evidencias
ORDER BY size(pessoas) DESC
""",
    },
    "Q4": {
        "pergunta": "Que tecnologias derivam (direta ou indiretamente) de {tecnologia}, e onde foram desenvolvidas?",
        "saltos": "1..3 + 1",
        "params": {"tecnologia": "tecnologia:unix"},
        "cypher": """
MATCH p = (deriv:Tecnologia)-[:BASEADO_EM*1..3]->(base:Entidade {id: $tecnologia})
OPTIONAL MATCH (deriv)-[d:DESENVOLVIDO_EM]->(inst:Instituicao)
RETURN deriv.name AS tecnologia, length(p) AS distancia, collect(DISTINCT inst.name) AS desenvolvida_em,
       [r IN relationships(p) | {tipo: type(r), evidencia: r.evidence, chunk: r.chunk_id}] AS evidencias
ORDER BY distancia
""",
    },
    "Q5": {
        "pergunta": "Qual a cadeia de orientação/formação acadêmica até {pessoa}?",
        "saltos": "1..4",
        "params": {"pessoa": "pessoa:john_von_neumann"},
        "cypher": """
MATCH p = (orientador:Pessoa)-[:ORIENTOU*1..4]->(a:Pessoa {id: $pessoa})
OPTIONAL MATCH (orientador)-[:TRABALHOU_EM]->(inst:Instituicao)
RETURN [n IN nodes(p) | n.name] AS cadeia, collect(DISTINCT inst.name) AS instituicoes_do_orientador,
       [r IN relationships(p) | {tipo: type(r), evidencia: r.evidence, chunk: r.chunk_id}] AS evidencias
""",
    },
    "Q6": {
        "pergunta": "Como {origem} e {destino} estão conectados?",
        "saltos": "caminho mínimo",
        "params": {"origem": "pessoa:alan_turing", "destino": "pessoa:tim_berners_lee"},
        "cypher": f"""
MATCH (a:Entidade {{id: $origem}}), (b:Entidade {{id: $destino}})
MATCH p = shortestPath((a)-[:{REL}*..8]-(b))
RETURN [n IN nodes(p) | n.name + ' (' + n.label + ')'] AS caminho, length(p) AS saltos,
       [r IN relationships(p) | {{tipo: type(r), de: startNode(r).name, para: endNode(r).name,
                                 evidencia: r.evidence, chunk: r.chunk_id}}] AS evidencias
""",
    },
    "Q7": {
        "pergunta": "Quem são as figuras mais centrais da mesma comunidade de {pessoa}? (Louvain + PageRank)",
        "saltos": "comunidade GDS",
        "params": {"pessoa": "pessoa:grace_hopper"},
        "cypher": """
MATCH (a:Entidade {id: $pessoa})
MATCH (m:Entidade {community: a.community}) WHERE m <> a
RETURN a.community AS comunidade, m.name AS membro, m.label AS tipo, round(m.pagerank, 3) AS pagerank
ORDER BY m.pagerank DESC LIMIT 10
""",
    },
    "Q8": {
        "pergunta": "BUSCA HÍBRIDA (modelo p/ Pessoa C): vetor -> expansão 2 saltos -> ranking por PageRank -> citações",
        "saltos": "vetor + 1..2",
        "params": {"texto": "linguagem de programação criada nos Bell Labs", "k": 5},
        "embed": "texto",
        "cypher": f"""
CALL db.index.vector.queryNodes('vec_entidade_embedding', $k, $vetor) YIELD node AS semente, score
MATCH p = (semente)-[:{REL}*1..2]-(viz:Entidade)
WITH semente, score, viz, p
ORDER BY score DESC, viz.pagerank DESC
WITH semente, score, collect(DISTINCT p)[..15] AS caminhos
UNWIND caminhos AS c
UNWIND relationships(c) AS r
WITH semente, score, collect(DISTINCT r) AS rels
RETURN semente.name AS semente, round(score, 3) AS similaridade,
       [r IN rels | startNode(r).name + ' -' + type(r) + '-> ' + endNode(r).name] AS fatos,
       [r IN rels | {{evidencia: r.evidence, chunk: r.chunk_id}}] AS evidencias
ORDER BY similaridade DESC
""",
    },
    "Q9": {
        "pergunta": "Busca por nome com FULLTEXT (tolera apelidos) e vizinhança imediata",
        "saltos": 1,
        "params": {"termo": "Thompson"},
        "cypher": """
CALL db.index.fulltext.queryNodes('ft_entidade_nome', $termo) YIELD node, score
WITH node, score ORDER BY score DESC LIMIT 3
OPTIONAL MATCH (node)-[r]-(v:Entidade)
RETURN node.name AS entidade, node.aliases AS aliases, round(score, 2) AS score,
       collect(type(r) + ' ' + v.name)[..10] AS vizinhos
""",
    },
    "Q10": {
        "pergunta": "De quais documentos/trechos vem o que se sabe sobre {pessoa}? (citação da fonte)",
        "saltos": 2,
        "params": {"pessoa": "pessoa:dennis_ritchie"},
        "cypher": """
MATCH (a:Entidade {id: $pessoa})-[:MENCIONADO_EM]->(c:Chunk)<-[:TEM_CHUNK]-(d:Documento)
RETURN d.title AS documento, d.url AS url, c.chunk_id AS chunk, left(c.text, 120) + '...' AS trecho
ORDER BY d.title, c.position
""",
    },
}


def run(names: list[str] | None = None) -> None:
    driver = get_driver()
    with driver.session(database=NEO4J_DATABASE) as s:
        for name, q in CONSULTAS.items():
            if names and name not in names:
                continue
            params = dict(q["params"])
            if "embed" in q:
                from embeddings import embed_query
                params["vetor"] = embed_query(params[q["embed"]])
            print(f"\n=== {name} ({q['saltos']} saltos) — {q['pergunta'].format(**q['params'])}")
            rows = list(s.run(q["cypher"], **params))
            print(f"    {len(rows)} linha(s)")
            for r in rows[:5]:
                d = r.data()
                ev = d.pop("evidencias", None)
                print("   ", d)
                if ev:
                    print(f"      evidências: {len(ev)}, ex.: {ev[0]}")
    driver.close()


if __name__ == "__main__":
    run(sys.argv[1:] or None)
