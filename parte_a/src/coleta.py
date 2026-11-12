"""
PASSO 2 - COLETA
Baixa páginas da Wikipedia em português (só o texto puro) e salva em data/raw/.

Rodar:  .venv/bin/python src/coleta.py
"""
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

PAGINAS = [
    # Pessoas
    "Alan Turing", "John von Neumann", "Edsger Dijkstra", "Donald Knuth",
    "Ada Lovelace", "Charles Babbage", "Grace Hopper", "Dennis Ritchie",
    "Ken Thompson", "Claude Shannon", "Alan Kay", "Tim Berners-Lee",
    "John McCarthy", "Marvin Minsky",
    # Tecnologias / conceitos
    "ENIAC", "Unix", "C (linguagem de programação)", "Lisp",
    "World Wide Web", "Máquina de Turing", "Arquitetura de von Neumann",
    # Instituições / prêmios / lugares
    "Bell Labs", "Xerox PARC", "Prêmio Turing", "Bletchley Park",
    "Instituto de Tecnologia de Massachusetts", "Universidade Stanford",
]

API = "https://pt.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "GraphRAG-ProjetoAcademico/0.1"}
LIMITE_CARACTERES = 8000   # corta páginas muito longas (economiza chamadas ao LLM)
PASTA = Path(__file__).resolve().parent.parent / "data" / "raw"


def slug(texto: str) -> str:
    """'Máquina de Turing' -> 'maquina_de_turing'"""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")


def baixar(titulo: str) -> dict | None:
    params = {
        "action": "query", "format": "json", "prop": "extracts",
        "explaintext": 1, "redirects": 1, "titles": titulo,
    }
    for tentativa in range(5):
        resp = requests.get(API, params=params, headers=HEADERS, timeout=30)
        if resp.status_code != 429:  # 429 = "muitas requisições", espera e tenta de novo
            break
        espera = 5 * (tentativa + 1)
        print(f"    (Wikipedia pediu para esperar, aguardando {espera}s...)")
        time.sleep(espera)
    resp.raise_for_status()
    pagina = next(iter(resp.json()["query"]["pages"].values()))
    if "missing" in pagina or not pagina.get("extract"):
        return None

    texto = pagina["extract"]
    # Remove seções finais que só têm lixo (referências, links etc.)
    texto = re.split(r"\n== (Referências|Ver também|Ligações externas|Bibliografia|Notas) ==", texto)[0]
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()[:LIMITE_CARACTERES]

    titulo_real = pagina["title"]
    return {
        "doc_id": slug(titulo_real),
        "title": titulo_real,
        "url": "https://pt.wikipedia.org/wiki/" + titulo_real.replace(" ", "_"),
        "text": texto,
    }


def main():
    PASTA.mkdir(parents=True, exist_ok=True)
    ok = 0
    for titulo in PAGINAS:
        if (PASTA / f"{slug(titulo)}.json").exists():  # já baixada antes
            ok += 1
            print(f"  = já existe: {titulo}")
            continue
        doc = baixar(titulo)
        if doc is None:
            print(f"  ✗ não encontrada: {titulo}")
            continue
        (PASTA / f"{doc['doc_id']}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok += 1
        print(f"  ✓ {doc['title']:45} {len(doc['text']):>6} caracteres")
        time.sleep(1.5)  # gentileza com a API da Wikipedia
    print(f"\n{ok}/{len(PAGINAS)} páginas salvas em {PASTA}")


if __name__ == "__main__":
    main()
