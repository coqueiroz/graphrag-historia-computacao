"""
PASSO 3 - CHUNKING
Corta cada documento em pedaços menores ("chunks") para o LLM ler um de cada vez.
Cada pedaço guarda de qual documento veio -> permite citar a fonte depois.

Rodar:  .venv/bin/python src/chunking.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "data"
TAMANHO = 3000      # caracteres por chunk (~750 tokens)
SOBREPOSICAO = 300  # repete um pedacinho entre chunks para não cortar frases no meio do contexto


def cortar(texto: str) -> list[str]:
    """Junta parágrafos até chegar perto de TAMANHO, sem quebrar parágrafo no meio."""
    paragrafos = [p.strip() for p in texto.split("\n") if p.strip()]
    chunks, atual = [], ""
    for p in paragrafos:
        if atual and len(atual) + len(p) > TAMANHO:
            chunks.append(atual)
            atual = atual[-SOBREPOSICAO:]  # sobreposição com o chunk anterior
        atual = (atual + "\n" + p).strip()
    if atual:
        chunks.append(atual)
    return chunks


def main():
    (BASE / "chunks").mkdir(parents=True, exist_ok=True)
    todos = []
    for arquivo in sorted((BASE / "raw").glob("*.json")):
        doc = json.loads(arquivo.read_text(encoding="utf-8"))
        for i, texto in enumerate(cortar(doc["text"])):
            todos.append({
                "chunk_id": f"{doc['doc_id']}#{i}",
                "doc_id": doc["doc_id"],
                "text": texto,
                "position": i,
            })
    saida = BASE / "chunks" / "chunks.json"
    saida.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(todos)} chunks salvos em {saida}")


if __name__ == "__main__":
    main()
