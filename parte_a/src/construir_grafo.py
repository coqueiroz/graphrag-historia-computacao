"""
PASSOS 5, 6 e 7 - LIMPEZA, RESOLUÇÃO DE ENTIDADES E EXPORTAÇÃO

Lê tudo que o Gemini extraiu (data/cache/) e:
  5. LIMPEZA: descarta labels/relações fora do schema, setas com tipos de nó
     errados (ex: INFLUENCIOU -> Local) e auto-relações (X -> X).
  6. RESOLUÇÃO DE ENTIDADES: junta nomes diferentes da mesma coisa
     ("Turing", "Alan Turing", "Alan Mathison Turing" -> um nó só).
  7. EXPORTAÇÃO: salva data/processed/grafo.json no formato do contrato
     (ExtractionResult) para a Pessoa B carregar no Neo4j.

Também gera data/processed/relatorio.json com os números para o artigo.

Rodar:  .venv/bin/python src/construir_grafo.py
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from contracts import (
    ALLOWED_LABELS, ALLOWED_RELATIONS, RELATION_SCHEMA,
    Chunk, Document, Entity, ExtractionResult, Relation,
)

BASE = Path(__file__).resolve().parent.parent / "data"

# Siglas e apelidos conhecidos que nenhuma comparação de texto acertaria sozinha.
ALIASES_MANUAIS = {
    "mit": "instituto de tecnologia de massachusetts",
    "massachusetts institute of technology": "instituto de tecnologia de massachusetts",
    "stanford": "universidade stanford",
    "universidade de stanford": "universidade stanford",
    "www": "world wide web",
    "web": "world wide web",
    "parc": "xerox parc",
    "xerox palo alto research center": "xerox parc",
    "palo alto research center": "xerox parc",
    "sistema a 0": "a 0",
    "timothy john berners lee": "tim berners lee",
    "kenneth thompson": "ken thompson",
    "kenneth lane thompson": "ken thompson",
    "timothy berners lee": "tim berners lee",
    "bell laboratories": "bell labs",
    "bell telephone laboratories": "bell labs",
    "bell telephone laboratories inc": "bell labs",
    "laboratorios bell telephone": "bell labs",
    "laboratorios bell": "bell labs",
    "c": "c linguagem de programacao",
    "linguagem c": "c linguagem de programacao",
    "linguagem de programacao c": "c linguagem de programacao",
}

TITULOS = r"\b(sir|dr|dra|prof|professor|professora|lord|lady)\b\.?"
PALAVRAS_VAZIAS = {"de", "da", "do", "das", "dos", "of", "the", "o", "a", "e"}


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------
def normalizar(nome: str) -> str:
    """'Prof. Alan M. Turing' -> 'alan m turing'"""
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    s = re.sub(TITULOS, " ", s)
    # Remove explicações entre parênteses: "Xerox PARC (Palo Alto)" -> "xerox parc"
    if not s.startswith("("):
        s = re.sub(r"\s*\([^)]*\)", " ", s)
    # Preserva linguagens cujo nome depende de símbolos (C, C++, C#, F#...)
    s = s.replace("++", " plusplus ").replace("#", " sharp ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES_MANUAIS.get(s, s)


def sem_palavras_vazias(s: str) -> str:
    return " ".join(p for p in s.split() if p not in PALAVRAS_VAZIAS)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalizar(s)).strip("_")


def mesma_entidade(a: str, b: str, label: str) -> bool:
    """Decide se dois nomes NORMALIZADOS do mesmo label são a mesma entidade."""
    if a == b or sem_palavras_vazias(a) == sem_palavras_vazias(b):
        return True
    if fuzz.ratio(a, b) >= 92:              # erros de digitação / acentuação
        return True
    if fuzz.token_sort_ratio(a, b) >= 95:   # mesmas palavras em outra ordem
        return True
    if label == "Pessoa":
        ta, tb = a.split(), b.split()
        curto, longo = (ta, tb) if len(ta) < len(tb) else (tb, ta)
        # "turing" == "alan turing" (só o sobrenome)
        if len(curto) == 1 and len(longo) > 1 and curto[0] == longo[-1]:
            return True
        # "alan turing" == "alan mathison turing": o nome curto tem MENOS palavras,
        # mesmo primeiro e último nome, e todas as palavras dele aparecem no longo.
        # (Evita juntar "Alexander Graham Bell" com "Alexander Melville Bell".)
        if (2 <= len(curto) < len(longo) and curto[0] == longo[0]
                and curto[-1] == longo[-1] and set(curto) <= set(longo)):
            return True
    return False


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def main():
    stats = Counter()
    extraidos = [json.loads(f.read_text(encoding="utf-8"))
                 for f in sorted((BASE / "cache").glob("*.json"))]
    chunks = {c["chunk_id"]: c for c in
              json.loads((BASE / "chunks" / "chunks.json").read_text(encoding="utf-8"))}

    # ---------- 5a. Entidades: limpeza básica ----------
    mencoes = []  # (nome_original, nome_normalizado, label, chunk_id)
    for ex in extraidos:
        for e in ex["entities"]:
            stats["entidades_brutas"] += 1
            if e["label"] not in ALLOWED_LABELS:
                stats["descartada_label_invalido"] += 1
                continue
            n = normalizar(e["name"])
            if len(n) < 1:
                stats["descartada_nome_vazio"] += 1
                continue
            mencoes.append((e["name"].strip(), n, e["label"], ex["chunk_id"]))

    # Se o mesmo nome veio com labels diferentes, fica o label mais votado
    votos = defaultdict(Counter)
    for _, n, label, _ in mencoes:
        votos[n][label] += 1
    label_de = {n: c.most_common(1)[0][0] for n, c in votos.items()}

    # ---------- 6. Resolução de entidades ----------
    # Agrupa nomes parecidos (dentro do mesmo label). Os nomes mais frequentes
    # viram "representantes" primeiro, e os outros se juntam a eles.
    freq = Counter(n for _, n, _, _ in mencoes)
    grupo_de = {}                         # nome_normalizado -> representante
    representantes = defaultdict(list)    # label -> [representantes]
    peso = Counter()                      # representante -> nº de menções do grupo
    for n, f in sorted(freq.items(), key=lambda x: (-x[1], -len(x[0]))):
        label = label_de[n]
        candidatos = [r for r in representantes[label] if mesma_entidade(n, r, label)]
        candidatos.sort(key=lambda r: -peso[r])
        # Se houver mais de um candidato (ex: "Turing" -> Alan, Ethel, John...),
        # só junta se um deles for claramente o dominante (3x mais menções).
        if len(candidatos) == 1 or (
            len(candidatos) > 1 and peso[candidatos[0]] >= 3 * peso[candidatos[1]]
        ):
            grupo_de[n] = candidatos[0]
            peso[candidatos[0]] += f
        else:
            peso[n] += f
            grupo_de[n] = n
            representantes[label].append(n)

    # Monta as entidades finais
    grupos = defaultdict(lambda: {"nomes": Counter(), "chunks": set()})
    for original, n, _, chunk_id in mencoes:
        g = grupos[grupo_de[n]]
        g["nomes"][original] += 1
        g["chunks"].add(chunk_id)

    entidades, id_de = {}, {}
    for rep, g in grupos.items():
        label = label_de[rep]
        # Nome principal: com inicial maiúscula; para pessoas, com nome e sobrenome
        # ("Donald Knuth", não "Knuth"); depois o mais usado; empate -> o mais longo
        nome = max(g["nomes"], key=lambda x: (
            x[:1].isupper(),
            label != "Pessoa" or len(x.split()) >= 2,
            g["nomes"][x],
            len(x),
        ))
        eid = f"{label.lower()}:{slug(nome)}"
        entidades[eid] = Entity(
            id=eid, label=label, name=nome,
            aliases=sorted(set(g["nomes"]) - {nome}),
            chunk_ids=sorted(g["chunks"]),
        )
        id_de[rep] = eid

    def resolver(nome: str) -> str | None:
        n = normalizar(nome)
        if n not in grupo_de:
            return None
        return id_de[grupo_de[n]]

    # ---------- 5b. Relações: limpeza ----------
    relacoes, vistas = [], set()
    for ex in extraidos:
        for r in ex["relations"]:
            stats["relacoes_brutas"] += 1
            if r["type"] not in ALLOWED_RELATIONS:
                stats["descartada_tipo_invalido"] += 1
                continue
            src, tgt = resolver(r["source"]), resolver(r["target"])
            if not src or not tgt:
                stats["descartada_entidade_inexistente"] += 1
                continue
            if src == tgt:
                stats["descartada_auto_relacao"] += 1
                continue
            origens, destinos = RELATION_SCHEMA[r["type"]]
            if entidades[src].label not in origens or entidades[tgt].label not in destinos:
                stats["descartada_tipos_de_no_errados"] += 1
                continue
            chave = (src, r["type"], tgt)
            if chave in vistas:
                stats["descartada_duplicada"] += 1
                continue
            vistas.add(chave)
            relacoes.append(Relation(source_id=src, target_id=tgt, type=r["type"],
                                     evidence=r["evidence"], chunk_id=ex["chunk_id"]))

    # ---------- 7. Exportação ----------
    ids_docs = {chunks[ex["chunk_id"]]["doc_id"] for ex in extraidos}
    documentos = []
    for f in sorted((BASE / "raw").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d["doc_id"] in ids_docs:
            documentos.append(Document(doc_id=d["doc_id"], title=d["title"], url=d["url"]))

    resultado = ExtractionResult(
        documents=documentos,
        chunks=[Chunk(**chunks[ex["chunk_id"]]) for ex in extraidos],
        entities=sorted(entidades.values(), key=lambda e: e.id),
        relations=relacoes,
    )
    (BASE / "processed").mkdir(parents=True, exist_ok=True)
    (BASE / "processed" / "grafo.json").write_text(
        resultado.model_dump_json(indent=2), encoding="utf-8")

    # ---------- Relatório (números para o artigo) ----------
    stats["mencoes_validas"] = len(mencoes)
    stats["nomes_distintos_antes_resolucao"] = len(freq)
    stats["entidades_finais"] = len(entidades)
    stats["relacoes_finais"] = len(relacoes)
    stats["documentos"] = len(documentos)
    stats["chunks"] = len(extraidos)
    relatorio = {
        "contagens": dict(sorted(stats.items())),
        "entidades_por_label": dict(Counter(e.label for e in entidades.values())),
        "relacoes_por_tipo": dict(Counter(r.type for r in relacoes)),
        "exemplos_de_fusao": {e.name: e.aliases for e in entidades.values() if e.aliases},
    }
    (BASE / "processed" / "relatorio.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(relatorio["contagens"], ensure_ascii=False, indent=2))
    print("\nEntidades por label:", relatorio["entidades_por_label"])
    print("Relações por tipo:  ", relatorio["relacoes_por_tipo"])
    print(f"\nGrafo salvo em {BASE / 'processed' / 'grafo.json'}")


if __name__ == "__main__":
    main()
