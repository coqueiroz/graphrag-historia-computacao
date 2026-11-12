"""
Roda algoritmos do Neo4j Graph Data Science e grava os resultados como propriedades.

Projeção: nós :Entidade + as relações do schema (ALLOWED_RELATIONS), como grafo NÃO
direcionado (para centralidade e comunidades, "A trabalhou em B" liga A e B igualmente).
As relações estruturais (TEM_CHUNK, MENCIONADO_EM) ficam de fora.

Propriedades gravadas em cada :Entidade:
  pagerank      (float) importância/centralidade  -> ranquear nós no retrieval
  community     (int)   comunidade Louvain         -> agrupar contexto / resumos por tema
  grau          (int)   número de relações do schema

Uso:
  python gds_algorithms.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # raiz do projeto: contracts.py (compartilhado)
from contracts import ALLOWED_RELATIONS
from neo4j_config import NEO4J_DATABASE, get_driver

GRAPH = "entidades"


def run() -> None:
    driver = get_driver()
    with driver.session(database=NEO4J_DATABASE) as s:
        print("GDS", s.run("RETURN gds.version()").single()[0])
        s.run("CALL gds.graph.drop($g, false) YIELD graphName", g=GRAPH).consume()

        rel_proj = {r: {"type": r, "orientation": "UNDIRECTED"} for r in ALLOWED_RELATIONS}
        p = s.run("CALL gds.graph.project($g, 'Entidade', $rels) "
                  "YIELD nodeCount, relationshipCount", g=GRAPH, rels=rel_proj).single()
        print(f"Projeção: {p['nodeCount']} nós, {p['relationshipCount']} relações (não direcionadas)")

        pr = s.run("CALL gds.pageRank.write($g, {writeProperty: 'pagerank', "
                   "maxIterations: 200, tolerance: 1e-6, dampingFactor: 0.85}) "
                   "YIELD nodePropertiesWritten, ranIterations, didConverge",
                   g=GRAPH).single()
        print(f"PageRank: {pr['nodePropertiesWritten']} nós, {pr['ranIterations']} iterações, "
              f"convergiu={pr['didConverge']}")

        lv = s.run("CALL gds.louvain.write($g, {writeProperty: 'community'}) "
                   "YIELD communityCount, modularity", g=GRAPH).single()
        print(f"Louvain: {lv['communityCount']} comunidades, modularidade={lv['modularity']:.3f}")

        s.run("CALL gds.degree.write($g, {writeProperty: 'grau'})", g=GRAPH).consume()
        s.run("CALL gds.graph.drop($g) YIELD graphName", g=GRAPH).consume()

        # grau vem como float do GDS; guardar como inteiro.
        s.run("MATCH (e:Entidade) SET e.grau = toInteger(e.grau)").consume()

        print("\nTop 10 PageRank")
        for r in s.run("MATCH (e:Entidade) RETURN e.name AS n, e.label AS l, e.pagerank AS p "
                       "ORDER BY p DESC LIMIT 10"):
            print(f"  {r['p']:.3f}  {r['l']:<12} {r['n']}")

        print("\nMaiores comunidades (Louvain) e seus membros mais centrais")
        for r in s.run("""
            MATCH (e:Entidade) WHERE e.grau > 0
            WITH e.community AS c, e ORDER BY e.pagerank DESC
            WITH c, count(e) AS tam, collect(e.name)[..5] AS top
            RETURN c, tam, top ORDER BY tam DESC LIMIT 8"""):
            print(f"  #{r['c']:<5} {r['tam']:>3} nós: {', '.join(r['top'])}")
    driver.close()


if __name__ == "__main__":
    run()
