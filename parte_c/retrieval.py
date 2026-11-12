"""
C1 — Recuperação GraphRAG a partir de uma pergunta livre.

Fluxo (todas as leituras via `graph_store.Neo4jGateway`, sessão READ):
  1. vetor = embed_query(pergunta)                         (mesmo modelo/índice da Pessoa B)
  2. sementes = índice vetorial `vec_entidade_embedding`   (como a Q8)
              + até N sementes do índice fulltext `ft_entidade_nome` para nomes próprios citados
  3. expansão por caminhos de 1..hops saltos nas 10 relações de domínio (Q8 estruturada)
  4. ranking determinístico de CAMINHOS (não de arestas soltas, para não quebrar cadeias multi-hop)
  5. procedência em lote: chunk_id -> Chunk -> Documento (1 consulta, sem N+1)
  6. fontes [F1..Fn]: uma por aresta, com evidência literal + documento + relação

Fórmula de ranking (documentada também em parte_c/README.md):
  Sem relações reconhecidas na pergunta:
    score(caminho) = (0.55·σ_semente + 0.35·média(sim(pergunta, nós do caminho exceto semente))
                      + 0.10·PR_norm(maior PageRank do caminho)) · 0.95^(saltos−1)
  Com relações reconhecidas (ex.: "criou" -> CRIOU, "fica" -> LOCALIZADO_EM; ver RELATION_CUES):
    score(caminho) = (0.40·σ + 0.25·sim_média + 0.25·R + 0.10·PR_norm) · 0.95^(saltos−1)
    R = 0.5·cobertura (fração das relações pedidas que o caminho usa)
      + 0.5·pureza    (fração das arestas do caminho que são de um tipo pedido)
  - σ e sim são similaridades de cosseno do índice/função vetorial do Neo4j (escala 0..1). São medidas de
    RELEVÂNCIA SEMÂNTICA, não de verdade. R é casamento LEXICAL pergunta -> tipo de relação.
  - PageRank mede CENTRALIDADE no grafo; tem peso pequeno (desempate), nunca é tratado como factualidade.
  - Desempates: menos saltos, depois ordem alfabética dos IDs (resultado reprodutível).
  - Quando a pergunta cita relações, arestas de OUTROS tipos ocupam no máximo 1/3 do orçamento de
    arestas (reduz o "ruído" que confundia o LLM); cadeias formadas só por relações pedidas não são afetadas.
Nada aqui chama LLM nem cria fatos: só organiza o que o banco devolveu.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, Optional

from parte_c.config import Settings
from parte_c.schemas import (Edge, Mode, Node, RelationRef, RetrievalResult, Seed, Source, Subgraph)

W_SEED, W_PATH_SIM, W_PAGERANK, HOP_DECAY = 0.55, 0.35, 0.10, 0.95
# pesos quando a pergunta cita relações reconhecíveis
W_SEED_R, W_PATH_SIM_R, W_REL_R, W_PAGERANK_R = 0.40, 0.25, 0.25, 0.10
NOISE_SHARE = 1 / 3  # fração máxima do orçamento de arestas para caminhos sem nenhuma relação pedida

# Pistas lexicais (radicais, sem acento) -> tipos de relação do contrato. Deliberadamente simples e auditável.
RELATION_CUES = {
    "CRIOU": r"\bcri(ou|ad[oa]s?|ador|adora|adores|aram|ar)\b|\binvent",
    "TRABALHOU_EM": r"\btrabalh",
    "ESTUDOU_EM": r"\bestud|\bformou|\bgradu",
    "ORIENTOU": r"\borient",
    "RECEBEU": r"\breceb|\bpremi|\bpremio|\bmedalha|\bganh",
    "INFLUENCIOU": r"\binfluenc",
    "COLABOROU_COM": r"\bcolabor|\bparceri|\bjunto com\b",
    "DESENVOLVIDO_EM": r"\bdesenvolvid",
    "BASEADO_EM": r"\bbasead|\bbaseia|\bderiv",
    "LOCALIZADO_EM": r"\bfica\b|\bficava\b|\blocaliz|\bcidade|\bestado\b|\bpais\b|\bonde fica",
}


def relation_cues(question: str) -> list[str]:
    """Tipos de relação que a pergunta menciona (ex.: 'Em que cidade fica...' -> LOCALIZADO_EM)."""
    q = norm_text(question)
    return [t for t, pat in RELATION_CUES.items() if re.search(pat, q)]

# Palavras que começam com maiúscula mas não são nomes de entidades (início de pergunta etc.).
_STOP_CAPS = {
    "quem", "que", "qual", "quais", "onde", "como", "quando", "quanto", "quantos", "quantas", "por",
    "porque", "o", "a", "os", "as", "um", "uma", "em", "de", "do", "da", "dos", "das", "no", "na",
    "nos", "nas", "para", "com", "e", "ou", "se", "foi", "é", "existe", "há", "liste", "cite",
    "diga", "explique", "me", "sobre", "entre", "ao", "à", "pelo", "pela",
}


# --------------------------------------------------------------------------- utilidades
def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def extract_name_terms(question: str) -> Optional[str]:
    """Extrai nomes próprios (sequências de palavras com inicial maiúscula/siglas) e monta uma
    consulta Lucene segura: frases entre aspas unidas por OR. Retorna None se não houver nomes.
    Caracteres especiais do Lucene são removidos; o resultado vai como PARÂMETRO, nunca no Cypher."""
    tokens = re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ][0-9A-Za-zÀ-ÖØ-öø-ÿ+#'.-]*", question or "")
    phrases, cur = [], []
    for tok in tokens:
        clean = tok.strip(".'-")
        is_name = bool(clean) and (clean[0].isupper() or clean[0].isdigit() and len(clean) > 1)
        if is_name and clean.casefold() not in _STOP_CAPS:
            cur.append(clean)
        elif cur and clean.casefold() in {"de", "da", "do", "von", "van"} and is_name is False:
            cur.append(clean)  # "John von Neumann", "Universidade de Cambridge"
        else:
            if cur:
                phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    out = []
    for ph in phrases:
        while ph and ph[-1].casefold() in {"de", "da", "do", "von", "van"}:
            ph = ph[:-1]
        if ph:
            text = " ".join(ph).replace('"', "").replace("\\", "")
            # nomes de 1 letra só se forem maiúsculos (linguagens "C", "B", "S")
            if len(text) >= 2 or (len(text) == 1 and text.isupper()):
                out.append(f'"{text}"')
    return " OR ".join(dict.fromkeys(out)) or None


# Palavras que não podem começar/terminar um "termo composto" em minúsculas.
_PHRASE_STOP = _STOP_CAPS | {
    "fica", "ficava", "criou", "criada", "criado", "criador", "criadora", "trabalhou", "trabalhava",
    "desenvolvido", "desenvolvida", "desenvolveu", "baseado", "baseada", "baseia", "influenciou",
    "influenciada", "recebeu", "colaborou", "estudou", "nasceu", "qual", "outra", "outras", "outros",
    "mesma", "mesmo", "essa", "esse", "isso", "pessoa", "também", "tambem", "no", "na", "seu", "sua",
}


def extract_concept_phrases(question: str, max_n: int = 4) -> list[str]:
    """Termos compostos que podem ser nomes de entidades (ex.: 'teoria da informação',
    'arquitetura de von Neumann'): sequências de 2..max_n palavras que começam em minúscula e
    começam e terminam com palavra de conteúdo. Só servem como candidatas para o índice fulltext de nomes —
    se não existirem como nome no grafo, simplesmente não retornam nada."""
    words = [w for w in re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", question or "")]
    out = []
    for i in range(len(words)):
        for n in range(2, max_n + 1):
            seg = words[i:i + n]
            # começa em minúscula (o nome próprio puro já é tratado por extract_name_terms),
            # mas pode terminar em nome: "arquitetura de von Neumann"
            if len(seg) < n or seg[0][0].isupper():
                continue
            if seg[0].casefold() in _PHRASE_STOP or seg[-1].casefold() in _PHRASE_STOP:
                continue
            if len(seg[0]) < 3 or len(seg[-1]) < 3:
                continue
            out.append(" ".join(seg))
    return list(dict.fromkeys(out))


def build_fulltext_query(question: str) -> Optional[str]:
    """Consulta Lucene para o índice `ft_entidade_nome`: nomes próprios (peso 4) OR termos compostos
    em minúsculas (peso 1). Tudo entre aspas, sem caracteres especiais; vai como parâmetro."""
    parts = []
    names = extract_name_terms(question)
    if names:
        parts += [f"{p}^4" for p in names.split(" OR ")]
    parts += [f'"{p}"' for p in extract_concept_phrases(question)]
    return " OR ".join(parts) or None


def evidence_window(text: str, evidence: str, max_chars: int) -> tuple[bool, Optional[str]]:
    """Procura a evidência literalmente no chunk (ignorando espaços/caixa/acentos).
    Retorna (encontrada?, janela do texto original em torno dela)."""
    if not text or not evidence:
        return False, None
    t_norm, e_norm = norm_text(text), norm_text(evidence)
    probe = e_norm if len(e_norm) <= 80 else e_norm[:80]
    pos = t_norm.find(probe)
    if pos < 0:
        return False, None
    # norm_text preserva aproximadamente as posições (remove só acentos combinados e colapsa espaços);
    # a janela é um recorte de conveniência para a UI, a citação literal é `quote`.
    start = max(0, pos - max_chars // 3)
    return True, text[start:start + max_chars].strip()


def _tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", norm_text(name)) if t not in {"de", "da", "do", "the", "of"}}


def possible_duplicates(nodes: Iterable[Node]) -> list[tuple[Node, Node]]:
    """Heurística APENAS para avisar (nunca funde): mesmo label e nomes muito parecidos."""
    nodes = sorted(nodes, key=lambda n: n.id)
    pairs = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if a.label != b.label or a.id == b.id:
                continue
            na, nb = norm_text(a.name), norm_text(b.name)
            ta, tb = _tokens(a.name), _tokens(b.name)
            jacc = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
            contained = min(len(na), len(nb)) >= 4 and (na in nb or nb in na)
            pessoa = (a.label == "Pessoa" and len(ta) >= 2 and len(tb) >= 2
                      and na.split()[-1] == nb.split()[-1] and na[0] == nb[0])
            if na == nb or contained or jacc >= 0.6 or pessoa:
                pairs.append((a, b))
    return pairs


def duplicate_warnings(nodes: Iterable[Node]) -> list[str]:
    return [f"Possível duplicata de entidade (não fundida automaticamente; corrigir na origem, Parte A/B): "
            f"'{a.name}' [{a.id}] ~ '{b.name}' [{b.id}]" for a, b in possible_duplicates(nodes)]


def _node(d: dict) -> Node:
    return Node(id=d["id"], name=d.get("name") or d["id"], label=d.get("label") or "Entidade",
                pagerank=d.get("pagerank"), community=d.get("community"),
                similarity=round(d["sim"], 4) if d.get("sim") is not None else None)


# --------------------------------------------------------------------------- fontes
def build_sources(edges: list[Edge], provenance: dict[str, dict], nodes: dict[str, Node],
                  settings: Settings, start: int = 1) -> tuple[list[Source], list[str]]:
    """Uma fonte por aresta (na ordem recebida). Arestas sem chunk resolvido geram aviso."""
    sources, warnings = [], []
    for i, e in enumerate(edges, start=start):
        prov = provenance.get(e.chunk_id or "")
        if prov is None:
            warnings.append(f"Aresta {e.source} -{e.type}-> {e.target} sem Chunk/Documento resolvido "
                            f"(chunk_id={e.chunk_id!r}).")
        found, ctx = evidence_window(prov.get("text", "") if prov else "", e.evidence, settings.quote_max_chars)
        sources.append(Source(
            id=f"F{i}",
            doc_id=prov.get("doc_id") if prov else None,
            title=prov.get("title") if prov else None,
            url=prov.get("url") if prov else None,
            chunk_id=e.chunk_id,
            quote=(e.evidence or "")[: settings.quote_max_chars],
            context=ctx,
            relation=RelationRef(source_id=e.source, type=e.type, target_id=e.target,
                                 source_name=nodes[e.source].name if e.source in nodes else None,
                                 target_name=nodes[e.target].name if e.target in nodes else None),
            score=round(e.score, 4),
            evidence_in_chunk=found if prov else None,
        ))
    return sources, warnings


# --------------------------------------------------------------------------- ranking
def relation_match(rel_types: list[str], cues: list[str]) -> float:
    """R = 0.5·cobertura (relações pedidas presentes no caminho) + 0.5·pureza (arestas de tipo pedido)."""
    if not cues or not rel_types:
        return 0.0
    coverage = len(set(rel_types) & set(cues)) / len(set(cues))
    purity = sum(t in cues for t in rel_types) / len(rel_types)
    return 0.5 * coverage + 0.5 * purity


def rank_paths(seeds: dict[str, Seed], paths_by_seed: dict[str, list[dict]],
               cues: Optional[list[str]] = None) -> list[dict]:
    """Calcula o score de cada caminho e devolve lista ordenada (determinística)."""
    cues = list(cues or [])
    all_pr = [n.get("pagerank") or 0.0 for ps in paths_by_seed.values() for p in ps for n in p["nos"]]
    max_pr = max(all_pr) if all_pr and max(all_pr) > 0 else 1.0
    ranked = []
    for seed_id, paths in paths_by_seed.items():
        sigma = seeds[seed_id].similarity if seed_id in seeds else 0.0
        for p in paths:
            nos, rels = p["nos"], p["rels"]
            if not rels:
                continue
            tail = [n for n in nos[1:]]
            sims = [n["sim"] for n in tail if n.get("sim") is not None]
            sim_mean = sum(sims) / len(sims) if sims else 0.0
            pr = max((n.get("pagerank") or 0.0) for n in tail) / max_pr if tail else 0.0
            types = [r["type"] for r in rels]
            rmatch = relation_match(types, cues)
            if cues:
                base = W_SEED_R * sigma + W_PATH_SIM_R * sim_mean + W_REL_R * rmatch + W_PAGERANK_R * pr
            else:
                base = W_SEED * sigma + W_PATH_SIM * sim_mean + W_PAGERANK * pr
            score = base * HOP_DECAY ** (len(rels) - 1)
            ranked.append({"seed": seed_id, "score": score, "nos": nos, "rels": rels, "rel_match": rmatch,
                           "ids": tuple(n["id"] for n in nos)})
    ranked.sort(key=lambda r: (-round(r["score"], 9), len(r["rels"]), r["ids"]))
    return ranked


def select_edges(ranked_paths: list[dict], max_edges: int,
                 cues: Optional[list[str]] = None) -> tuple[list[Edge], list[list[str]]]:
    """Percorre os caminhos do melhor para o pior e adiciona suas arestas até o limite.
    Um caminho só entra se TODAS as suas arestas couberem (preserva cadeias multi-hop).
    Se a pergunta cita relações, caminhos sem nenhuma delas usam no máximo NOISE_SHARE do orçamento."""
    chosen: dict[tuple, Edge] = {}
    kept_paths: list[list[str]] = []
    noise_budget = int(max_edges * NOISE_SHARE) if cues else max_edges
    noise_used = 0
    for p in ranked_paths:
        new = []
        for hop, r in enumerate(p["rels"], start=1):
            e = Edge(source=r["source"], target=r["target"], type=r["type"],
                     evidence=r.get("evidence") or "", chunk_id=r.get("chunk_id"),
                     score=p["score"], hop=hop, origin="vector")
            if e.key() not in chosen:
                new.append(e)
        if len(chosen) + len(new) > max_edges:
            continue
        # arestas de tipo NÃO pedido pela pergunta contam no orçamento de ruído
        new_noise = sum(e.type not in cues for e in new) if cues else 0
        if new_noise and noise_used + new_noise > noise_budget:
            continue
        noise_used += new_noise
        for e in new:
            chosen.setdefault(e.key(), e)
        kept_paths.append(list(p["ids"]))
    edges = sorted(chosen.values(), key=lambda e: (-round(e.score, 9), e.hop, e.key()))
    return edges, kept_paths


# --------------------------------------------------------------------------- API
def _default_embed(text: str) -> list[float]:
    from embeddings import embed_query  # Pessoa B: mesmo modelo dos índices (384d)

    return embed_query(text)


def find_seeds(question: str, vetor: list[float], gateway, settings: Settings, k: Optional[int] = None,
               trace: Optional[list] = None) -> list[Seed]:
    """Sementes = top-k do índice vetorial de entidades (como a Q8) + nomes próprios via fulltext."""
    seeds: dict[str, Seed] = {}
    for d in gateway.vector_seeds(vetor, k or settings.top_k, settings.min_seed_score, trace=trace):
        seeds[d["id"]] = Seed(id=d["id"], name=d.get("name") or d["id"], label=d.get("label") or "",
                              similarity=round(float(d["score"]), 4), origin="vector")
    termos = build_fulltext_query(question) if settings.fulltext_seeds > 0 else None
    if termos:
        for d in gateway.fulltext_seeds(termos, vetor, settings.fulltext_seeds, trace=trace):
            if d["id"] in seeds:
                seeds[d["id"]].origin = "vector+fulltext"
            else:
                seeds[d["id"]] = Seed(id=d["id"], name=d.get("name") or d["id"], label=d.get("label") or "",
                                      similarity=round(float(d["score"] or 0.0), 4), origin="fulltext")
    return sorted(seeds.values(), key=lambda s: (-s.similarity, s.id))


def retrieve_graph(question: str, top_k: Optional[int] = None, hops: Optional[int] = None, *,
                   gateway=None, embed_fn: Optional[Callable[[str], list[float]]] = None,
                   settings: Optional[Settings] = None, vetor: Optional[list[float]] = None) -> RetrievalResult:
    """Recupera um subgrafo relevante e suas fontes para uma pergunta livre (sem LLM, sem escrita)."""
    settings = settings or Settings.from_env()
    k = top_k or settings.top_k
    h = hops or settings.hops
    if not 1 <= h <= 3:
        raise ValueError("hops deve estar entre 1 e 3")
    if gateway is None:
        from parte_c.graph_store import Neo4jGateway
        gateway = Neo4jGateway(settings)
    result = RetrievalResult(question=question, mode=Mode.GRAPHRAG, strategy="vector_cypher", hops=h)
    trace = result.queries

    if vetor is None:
        vetor = (embed_fn or _default_embed)(question)

    # 2. sementes: vetor (+ fulltext de nomes próprios)
    result.seeds = find_seeds(question, vetor, gateway, settings, k, trace)
    seeds = {s.id: s for s in result.seeds}
    if not seeds:
        result.warnings.append("Nenhuma entidade do grafo é semanticamente próxima da pergunta.")
        return result

    # 3-4. expansão + ranking (com as relações que a pergunta menciona, se houver)
    cues = relation_cues(question)
    paths_by_seed = gateway.expand([s.id for s in result.seeds], vetor, h, settings.max_paths_per_seed,
                                   trace=trace, tipos=cues)
    ranked = rank_paths(seeds, paths_by_seed, cues)
    edges, kept_paths = select_edges(ranked, settings.max_edges, cues)
    if cues:
        result.relation_cues = cues
    sem_viz = [s.name for s in result.seeds if not paths_by_seed.get(s.id)]
    if sem_viz:
        result.warnings.append(f"Sementes sem vizinhança no grafo: {', '.join(sem_viz)}.")

    # nós: sementes + nós dos caminhos usados
    node_map: dict[str, Node] = {}
    for ps in paths_by_seed.values():
        for p in ps:
            for n in p["nos"]:
                node_map.setdefault(n["id"], _node(n))
    used_ids = {s.id for s in result.seeds} | {e.source for e in edges} | {e.target for e in edges}
    missing = [i for i in used_ids if i not in node_map]
    if missing:
        for i, d in gateway.nodes_by_ids(missing, vetor, trace=trace).items():
            node_map[i] = _node(d)
    nodes = sorted((node_map[i] for i in used_ids if i in node_map), key=lambda n: n.id)

    # 5-6. procedência em lote + fontes
    chunk_ids = sorted({e.chunk_id for e in edges if e.chunk_id})
    prov = gateway.provenance(chunk_ids, trace=trace)
    sources, warns = build_sources(edges, prov, node_map, settings)

    result.subgraph = Subgraph(nodes=nodes, edges=edges)
    result.sources = sources
    result.warnings += warns + duplicate_warnings(nodes)
    if not edges:
        result.warnings.append("As sementes encontradas não têm relações que sustentem fatos.")
    return result


def merge_results(primary: RetrievalResult, extra_edges: list[Edge], extra_nodes: dict[str, Node],
                  provenance: dict[str, dict], settings: Settings) -> RetrievalResult:
    """Coloca arestas verificadas do Text-to-Cypher na frente das da busca vetorial (sem duplicar)
    e renumera as fontes. Usado pela estratégia 'auto'."""
    seen = set()
    merged: list[Edge] = []
    for e in list(extra_edges) + list(primary.subgraph.edges):
        if e.key() in seen:
            continue
        seen.add(e.key())
        merged.append(e)
    merged = merged[: settings.max_edges]
    node_map = {n.id: n for n in primary.subgraph.nodes}
    node_map.update({k: v for k, v in extra_nodes.items() if k not in node_map})
    used = {e.source for e in merged} | {e.target for e in merged} | {s.id for s in primary.seeds}
    prov = dict(provenance)
    prov.update({s.chunk_id: {"chunk_id": s.chunk_id, "doc_id": s.doc_id, "title": s.title, "url": s.url,
                              "text": s.context or ""} for s in primary.sources
                 if s.chunk_id and s.chunk_id not in prov})
    sources, warns = build_sources(merged, prov, node_map, settings)
    # a janela de contexto das fontes vindas da busca vetorial já foi calculada: reaproveita
    old = {(s.relation.source_id, s.relation.type, s.relation.target_id, s.chunk_id): s
           for s in primary.sources if s.relation}
    for s in sources:
        o = old.get((s.relation.source_id, s.relation.type, s.relation.target_id, s.chunk_id))
        if o is not None:
            s.context, s.evidence_in_chunk = o.context, o.evidence_in_chunk
    nodes = sorted((node_map[i] for i in used if i in node_map), key=lambda n: n.id)
    out = primary.model_copy(deep=True)
    out.subgraph = Subgraph(nodes=nodes, edges=merged)
    out.sources = sources
    out.warnings = list(dict.fromkeys(primary.warnings + warns + duplicate_warnings(nodes)))
    return out
