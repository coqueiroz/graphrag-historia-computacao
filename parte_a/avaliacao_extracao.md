# Avaliação da Extração (Módulo A)

## Metodologia

- **Amostra:** 5 trechos de 5 páginas diferentes, escolhidos para cobrir tipos de
  texto variados (biografia, tecnologia, instituição):
  `alan_turing#1`, `unix#0`, `bell_labs#0`, `grace_hopper#0`, `xerox_parc#0`.
- **Precisão:** cada relação do grafo final extraída desses trechos foi conferida
  contra o texto original. Ela é correta se o texto afirma o fato, com a direção e o
  tipo de relação certos.
- **Cobertura (recall):** listamos à mão os fatos do texto que se encaixam no schema e
  verificamos se aparecem no grafo final, extraídos desse trecho ou de outro, já que
  fatos repetidos entre trechos são guardados uma única vez.
- **Anotação:** feita com apoio de LLM e revisada pelo grupo.

## Resultados

| Trecho | Relações extraídas | Corretas | Fatos esperados | Encontrados no grafo |
|---|---|---|---|---|
| alan_turing#1 | 3 | 3 | 4 | 3 |
| unix#0 | 6 | 6 | 13 | 12 |
| bell_labs#0 | 12 | 12 | 20 | 12 |
| grace_hopper#0 | 15 | 15 | 17 | 15 |
| xerox_parc#0 | 7 | 6 | 15 | 10 |
| **Total** | **43** | **42** | **69** | **52** |

| Métrica | Valor |
|---|---|
| **Precisão das relações** | **97,7%** (42/43) |
| **Cobertura (recall)** | **75,4%** (52/69) |

**Resumo:** o pipeline **quase não inventa fatos** (alta precisão), mas **deixa de
extrair cerca de 1 em cada 4**. Para GraphRAG, essa é a troca desejada: o objetivo
é reduzir alucinações, e uma relação errada no grafo vira uma resposta errada com
"cara de fato verificado".

## Análise de erros

**Único erro de precisão: direção invertida.** O texto diz que InterPress é
"precursora do PostScript", mas o modelo gerou `InterPress -BASEADO_EM-> PostScript`.
O certo seria o contrário.

**Fatos não extraídos (17), em quatro padrões:**
1. **Listas longas** (7 de 17): na frase "desenvolveram a radioastronomia, o
   transistor, o laser, a célula fotovoltaica, o CCD, a teoria da informação, o Unix
   e as linguagens B, C, C++...", o modelo extraiu as linguagens, mas ignorou as
   invenções de hardware e física. O mesmo aconteceu com o Alto e a interface gráfica
   na lista de invenções do Xerox PARC.
2. **Relações implícitas** (7 de 17): "financiou o laboratório *com* Sumner Tainter"
   (colaboração), "trabalhou no Mark I *no time de* Howard Aiken", "*ajudou a
   desenvolver* os computadores de Manchester", "prêmio *pela invenção do* telefone"
   e "começou a *reescrever o Multics*" não viraram relações.
3. **Localização e vínculo institucional** (2 de 17): "Xerox PARC em Palo Alto" e
   "pesquisadores do PARC: Butler Lampson" não foram extraídos como
   `LOCALIZADO_EM` e `TRABALHOU_EM`.
4. **Direção invertida** (1 de 17): o fato certo `PostScript -BASEADO_EM-> InterPress`
   não existe no grafo, porque foi extraído ao contrário (o erro de precisão acima).

**Problemas de resolução de entidades encontrados e corrigidos durante a avaliação:**
- "Xerox Palo Alto Research Center (PARC)" estava separado de "Xerox PARC". A correção
  remove o conteúdo entre parênteses na normalização e adiciona um alias.
- "A-0" estava separado de "Sistema A-0". A correção adiciona um alias.

## Trabalhos futuros

- Dividir frases com listas longas antes da extração, ou pedir explicitamente
  "extraia todos os itens de listas".
- Uma segunda passada com o LLM perguntando "que fatos ficaram de fora?" (*gleaning*,
  técnica usada no Microsoft GraphRAG).
- Avaliação com amostra maior e mais de um anotador humano.

## Números gerais do pipeline (`data/processed/relatorio.json`)

| Etapa | Valor |
|---|---|
| Documentos / trechos | 27 / 86 |
| Entidades extraídas (bruto) → nomes distintos → entidades finais | 980 → 666 → 637 |
| Relações extraídas (bruto) → relações finais | 442 → 364 |
| Descartadas: tipos de nó errados | 13 |
| Descartadas: duplicadas | 59 |
| Descartadas: auto-relação ou entidade inexistente | 2 |
