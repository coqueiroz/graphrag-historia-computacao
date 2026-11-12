"""
PASSO 4 - EXTRAÇÃO (NER + RE)
Manda cada chunk para o Gemini e pede de volta:
  - ENTIDADES (NER): as "bolinhas"  -> ex: Alan Turing (Pessoa)
  - RELAÇÕES  (RE):  as "setas"     -> ex: Alan Turing -TRABALHOU_EM-> Bletchley Park

Cada resposta é salva em data/cache/<chunk>.json. Se rodar de novo, os chunks
já extraídos são pulados (não gasta a cota da API à toa).

Rodar:  .venv/bin/python src/extracao.py            (todos os chunks)
        .venv/bin/python src/extracao.py --limite 2 (só os 2 primeiros, para teste)
"""
import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

from contracts import ALLOWED_LABELS, ALLOWED_RELATIONS

BASE = Path(__file__).resolve().parent.parent / "data"
CACHE = BASE / "cache"


# ---------------------------------------------------------------------------
# Formato que o Gemini é OBRIGADO a devolver (structured output)
# ---------------------------------------------------------------------------
class EntidadeExtraida(BaseModel):
    name: str
    label: str


class RelacaoExtraida(BaseModel):
    source: str
    type: str
    target: str
    evidence: str


class RespostaExtracao(BaseModel):
    entities: list[EntidadeExtraida]
    relations: list[RelacaoExtraida]


PROMPT = f"""Você é um sistema de extração de conhecimento para construir um grafo
sobre História da Computação.

Leia o TEXTO e extraia:

1. ENTIDADES, cada uma com um destes labels (use exatamente um):
   {", ".join(ALLOWED_LABELS)}

2. RELAÇÕES entre essas entidades, usando SOMENTE estes tipos:
   - TRABALHOU_EM    (Pessoa -> Instituicao)
   - ESTUDOU_EM      (Pessoa -> Instituicao)
   - CRIOU           (Pessoa -> Tecnologia ou Conceito)
   - ORIENTOU        (Pessoa -> Pessoa; orientador acadêmico)
   - RECEBEU         (Pessoa -> Premio)
   - INFLUENCIOU     (qualquer -> qualquer)
   - COLABOROU_COM   (Pessoa -> Pessoa)
   - DESENVOLVIDO_EM (Tecnologia -> Instituicao)
   - BASEADO_EM      (Tecnologia -> Tecnologia ou Conceito)
   - LOCALIZADO_EM   (Instituicao -> Local)

REGRAS:
- Extraia SOMENTE fatos explícitos no texto. Não use conhecimento próprio. Não invente.
- Use o nome mais completo da entidade (ex: "Alan Turing", não "Turing").
- Instituições pelo nome oficial (ex: "Universidade de Cambridge").
- "source" e "target" de cada relação precisam aparecer na lista de entidades.
- "evidence" deve ser a frase do texto (copiada) que comprova a relação.
- Ignore datas, números e eventos genéricos que não se encaixem nos labels.
- RESPEITE os tipos de origem -> destino indicados entre parênteses acima.
  Ex: INFLUENCIOU nunca aponta para um Local; LOCALIZADO_EM só sai de Instituicao.
- CRIOU só vale para quem de fato inventou/projetou algo. Quem usou, escreveu sobre
  ou programou para uma máquina NÃO a criou.
- Se nenhum tipo de relação descreve o fato com precisão, NÃO crie a relação.
  É melhor extrair menos relações do que relações erradas.

Exemplo:
TEXTO: "Dennis Ritchie criou a linguagem C nos Bell Labs, em Nova Jersey."
SAÍDA: entities = [Dennis Ritchie (Pessoa), C (Tecnologia), Bell Labs (Instituicao), Nova Jersey (Local)]
       relations = [Dennis Ritchie -CRIOU-> C, C -DESENVOLVIDO_EM-> Bell Labs,
                    Bell Labs -LOCALIZADO_EM-> Nova Jersey]
"""


def extrair(client, modelos: list[str], texto: str) -> dict:
    """Chama o Gemini, com novas tentativas se a API pedir para esperar.
    Se um modelo estiver sobrecarregado (erro 503), tenta o próximo da lista."""
    for tentativa in range(6):
        modelo = modelos[tentativa % len(modelos)]
        try:
            resp = client.models.generate_content(
                model=modelo,
                contents=PROMPT + "\n\nTEXTO:\n" + texto,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RespostaExtracao,
                    temperature=0,  # respostas mais estáveis/repetíveis
                ),
            )
            return RespostaExtracao.model_validate_json(resp.text).model_dump()
        except Exception as e:  # limite de uso (429), erro temporário ou JSON inválido
            espera = 10 * (tentativa + 1)
            print(f"    erro no {modelo} ({str(e)[:70]}...) -> esperando {espera}s", flush=True)
            time.sleep(espera)
    return None  # desiste deste chunk por agora; rodar o script de novo tenta outra vez


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", type=int, default=None, help="processa só N chunks")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"].strip(),
        http_options=types.HttpOptions(timeout=60_000),  # desiste da chamada após 60s
    )
    modelos = os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite").split(",")
    CACHE.mkdir(parents=True, exist_ok=True)

    chunks = json.loads((BASE / "chunks" / "chunks.json").read_text(encoding="utf-8"))
    if args.limite:
        chunks = chunks[: args.limite]

    pulados = 0
    for i, chunk in enumerate(chunks, 1):
        arquivo = CACHE / (chunk["chunk_id"].replace("#", "__") + ".json")
        if arquivo.exists():
            print(f"[{i}/{len(chunks)}] {chunk['chunk_id']}: já extraído (cache)")
            continue
        resultado = extrair(client, modelos, chunk["text"])
        if resultado is None:
            pulados += 1
            print(f"[{i}/{len(chunks)}] {chunk['chunk_id']}: PULADO (API indisponível)", flush=True)
            continue
        resultado["chunk_id"] = chunk["chunk_id"]
        arquivo.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{i}/{len(chunks)}] {chunk['chunk_id']}: "
              f"{len(resultado['entities'])} entidades, {len(resultado['relations'])} relações", flush=True)
        time.sleep(4)  # respeita o limite do plano gratuito

    print("\nExtração concluída. Resultados em", CACHE)
    if pulados:
        print(f"ATENÇÃO: {pulados} chunks pulados. Rode o script de novo para completá-los.")


if __name__ == "__main__":
    main()
