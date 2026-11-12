# Módulo A — Pipeline Text-to-Graph (NER + RE)

Transforma páginas da Wikipedia sobre **História da Computação** em um grafo de
conhecimento (entidades + relações) pronto para ser carregado no Neo4j.

```
Wikipedia ──► coleta ──► chunking ──► extração (Gemini) ──► limpeza + resolução ──► grafo.json
  27 páginas   data/raw   data/chunks    data/cache            de entidades         data/processed
```

## Como rodar

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Crie o arquivo `.env` com sua chave (grátis em https://aistudio.google.com):

```
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODELS=gemini-3.5-flash-lite,gemini-3.8-flash,gemini-3.5-flash,gemini-3.1-flash-lite
```

Depois rode os passos em ordem:

```bash
.venv/bin/python src/coleta.py           # 1. baixa as páginas da Wikipedia
.venv/bin/python src/chunking.py         # 2. corta em trechos de ~3000 caracteres
.venv/bin/python src/extracao.py         # 3. Gemini extrai entidades e relações
.venv/bin/python src/construir_grafo.py  # 4. limpa, resolve entidades e exporta
```

A extração guarda cada resposta em `data/cache/`. Se ela for interrompida (por
exemplo, quando a API do Gemini fica sobrecarregada), basta rodar de novo: os
trechos já extraídos são pulados.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `src/contracts.py` | **Contrato com os outros módulos**: labels, relações permitidas e formato dos dados |
| `src/coleta.py` | Baixa o texto puro de 27 páginas da Wikipedia em português |
| `src/chunking.py` | Divide cada página em trechos com sobreposição; cada trecho tem um `chunk_id` |
| `src/extracao.py` | Envia cada trecho ao Gemini com o schema fechado e saída JSON estruturada |
| `src/construir_grafo.py` | Limpeza, resolução de entidades e exportação no formato do contrato |
| `data/processed/grafo.json` | **Entrega para o Módulo B** (Neo4j) |
| `data/processed/relatorio.json` | Estatísticas do pipeline (usadas no artigo) |

## Schema do grafo

**Labels:** `Pessoa`, `Instituicao`, `Tecnologia`, `Premio`, `Conceito`, `Local`

| Relação | Origem → Destino |
|---|---|
| `TRABALHOU_EM`, `ESTUDOU_EM` | Pessoa → Instituicao |
| `CRIOU` | Pessoa/Instituicao → Tecnologia/Conceito/Premio |
| `ORIENTOU`, `COLABOROU_COM` | Pessoa → Pessoa |
| `RECEBEU` | Pessoa → Premio |
| `INFLUENCIOU` | qualquer (exceto Local) → qualquer (exceto Local) |
| `DESENVOLVIDO_EM` | Tecnologia/Conceito → Instituicao |
| `BASEADO_EM` | Tecnologia/Conceito → Tecnologia/Conceito |
| `LOCALIZADO_EM` | Instituicao → Local |

## Formato da entrega (`grafo.json`)

```json
{
  "documents": [{"doc_id": "alan_turing", "title": "Alan Turing", "url": "..."}],
  "chunks":    [{"chunk_id": "alan_turing#0", "doc_id": "alan_turing", "text": "...", "position": 0}],
  "entities":  [{"id": "pessoa:alan_turing", "label": "Pessoa", "name": "Alan Turing",
                 "aliases": ["Turing"], "chunk_ids": ["alan_turing#0"]}],
  "relations": [{"source_id": "pessoa:alan_turing", "target_id": "instituicao:bletchley_park",
                 "type": "TRABALHOU_EM", "evidence": "frase do texto", "chunk_id": "alan_turing#1"}]
}
```

Sugestão de carga no Neo4j (Módulo B): `(:Documento)-[:TEM_CHUNK]->(:Chunk)-[:MENCIONA]->(:Entidade)`,
usando `chunk_ids` das entidades. Isso permite citar o trecho original de cada fato.

## Decisões técnicas

- **Redução de alucinação na extração:** schema fechado no prompt, `temperature=0`,
  saída JSON validada com Pydantic, campo `evidence` obrigatório (frase do texto que
  comprova cada relação) e descarte de relações cujos tipos de nó violam o schema.
- **Resolução de entidades** em camadas: normalização (acentos, títulos como "Sir"/"Prof."),
  dicionário de siglas (MIT, WWW...), similaridade de string (`rapidfuzz`) dentro do
  mesmo label e regra de sobrenome para pessoas ("Turing" → "Alan Turing", só quando
  não há ambiguidade).
- **Tolerância a falhas da API:** novas tentativas com espera crescente, alternância entre
  modelos e cache por trecho.

## Bibliotecas utilizadas

`google-genai` (API do Gemini), `pydantic` (validação), `rapidfuzz` (similaridade de
strings), `requests`, `python-dotenv`. Textos: Wikipedia em português (CC BY-SA 4.0).
