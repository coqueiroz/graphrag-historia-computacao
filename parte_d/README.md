# Parte D — Interface, avaliação e artigo

Comandos executados **dentro de `parte_d/`**.

## Interface (Streamlit)
```bash
pip install -r requirements.txt
streamlit run app.py                                   # motor FALSO (mock) sobre grafo.json, sem Neo4j
# motor real (Parte C): Neo4j no ar com o grafo carregado + Ollama com o modelo do .env
ENGINE=real streamlit run app.py                       # o motor é encontrado em ../parte_c automaticamente
```
- `adaptador_c.py` converte o `QAResponse` da Parte C (`parte_c/schemas.py`) no formato da UI (`contrato_resposta.py`).
- `engine_real.py` chama `parte_c.responder_pergunta` (modo "baseline" = RAG puro).
- A confiança exibida é a heurística NÃO calibrada da Parte C; a UI avisa isso.

## Avaliação
A avaliação (30 perguntas, gabarito, métricas, execuções) é da Parte C: `../parte_c/avaliacao/`.
```bash
python eval/relatorio.py ../parte_c/avaliacao/resultados/20261112-155230   # -> artigo/tabela_resultados.tex e fig_acerto.png
```
Pendente (grupo): validar o gabarito (`validacao_humana`) e preencher `anotacao_humana.csv`.

## Artigo
`artigo/main.tex` é um esqueleto: troque o preâmbulo pelo template oficial (SBC ou IEEE) e preencha os `\todo{}`.
Texto-base da Parte C: `../parte_c/docs/parte_c_artigo.md`. Artigo final: `../docs/artigo/`.

## Testes
```bash
pytest -q tests        # adaptador testado com 4 respostas reais da Parte C (tests/fixtures)
```
