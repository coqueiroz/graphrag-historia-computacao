# Material para o artigo — Parte C (recuperação híbrida, GraphRAG/KAG e avaliação)

> Texto-base em linguagem acadêmica para a Pessoa D consolidar no artigo SBC/IEEE. As seções de
> resultados estão **vazias de propósito**: só devem ser preenchidas com números produzidos por
> `python -m parte_c.evaluation` e revisados pela rubrica humana.

## 1. Arquitetura do motor de respostas

O motor recebe uma pergunta em português e a resolve em quatro estágios: (i) **ancoragem semântica**,
em que a pergunta é codificada pelo mesmo modelo usado na indexação (`paraphrase-multilingual-MiniLM-L12-v2`,
384 dimensões, vetores normalizados) e confrontada com o índice vetorial de entidades do Neo4j;
(ii) **expansão estrutural**, que percorre caminhos de 1 a *h* saltos (padrão *h* = 2, máximo 3) sobre
as dez relações de domínio do esquema LPG; (iii) **geração fundamentada**, em que um LLM recebe somente
as evidências recuperadas, identificadas como fontes `[F1..Fn]`; e (iv) **validação e estimativa de
confiança**, executadas de forma determinística sobre a saída do modelo. Complementarmente, um módulo
de **Text-to-Cypher** traduz a pergunta em uma consulta declarativa somente-leitura, cujo resultado é
combinado à expansão vetorial (estratégia híbrida).

## 2. Recuperação híbrida vetor + grafo

As sementes são as *k* entidades (padrão *k* = 5) mais similares à pergunta no índice
`vec_entidade_embedding`, acrescidas de até três entidades obtidas pelo índice fulltext de nomes
(`ft_entidade_nome`) a partir de nomes próprios e de termos compostos detectados na pergunta (p.ex.
"teoria da informação"). A expansão segue a consulta de referência Q8 do projeto (vetor → caminhos →
PageRank), estendida para devolver nós e arestas com identificadores de domínio, parametrizar o número
de saltos (padrão 3) e descartar caminhos com ciclos.

A pergunta é ainda mapeada, por um léxico de radicais, para os tipos de relação que menciona
(p.ex. "criou" → CRIOU, "trabalhava" → TRABALHOU_EM, "fica"/"cidade" → LOCALIZADO_EM). Cada caminho *p*
com semente *s* recebe o escore

  score(*p*) = (0,40·σ(*s*) + 0,25·σ̄(*p*) + 0,25·R(*p*) + 0,10·PR(*p*)) · 0,95^(|*p*|−1),

em que σ é a similaridade de cosseno (escala (1+cos)/2 do Neo4j), σ̄ é a média das similaridades
dos demais nós do caminho, PR é o maior PageRank normalizado do caminho e R mede o casamento entre o
caminho e as relações pedidas (média entre a cobertura dessas relações e a fração de arestas do
caminho com tipo pedido). Sem relações reconhecidas, R é omitido e os pesos passam a 0,55/0,35/0,10.
Arestas de tipos não pedidos ocupam no máximo um terço do orçamento de contexto. Essa componente
estrutural foi introduzida após o primeiro experimento, no qual a similaridade semântica sozinha
classificava mal os nós finais de cadeias multi-hop (p.ex. cidades), pouco similares ao texto da
pergunta. O PageRank atua apenas como critério de desempate de centralidade, sem ser interpretado
como indício de veracidade. A seleção é feita por **caminhos completos** até um limite
de arestas, de modo que cadeias multi-hop não sejam fragmentadas. Empates são resolvidos por número
de saltos e ordem lexicográfica dos identificadores, tornando a recuperação reprodutível.

A procedência é resolvida em lote: cada aresta carrega `chunk_id` e `evidence` (frase extraída pela
Parte A); o sistema obtém o `Chunk` e o `Documento` correspondentes e verifica se a evidência ocorre
literalmente no texto do chunk (`evidence_in_chunk`). Cada aresta vira uma fonte citável com
documento, trecho e a tripla (origem, tipo, destino).

## 3. Text-to-Cypher com política somente-leitura

O LLM recebe o esquema real do contrato do projeto (rótulos, direções permitidas por relação,
propriedades), as entidades candidatas encontradas pelos índices e seis exemplos revisados a partir das
consultas de referência (Q1–Q4, Q6, Q9). A saída é estruturada (JSON Schema): consulta, parâmetros e
justificativa. Como a filtragem por lista de palavras proibidas não é suficiente para garantir
segurança, adotou-se defesa em camadas: (1) restrições no prompt; (2) validação por *allowlist* após
remoção dos literais de texto — cláusulas, procedimentos (apenas busca em índices), funções, rótulos,
tipos de relação e propriedades permitidos, limite superior obrigatório em caminhos variáveis (≤ 4),
proibição de valores da pergunta escritos como literais e imposição de `LIMIT`; (3) `EXPLAIN` no
servidor, exigindo tipo de consulta `r` e ausência de operadores de escrita no plano; (4) execução em
transação de leitura com *timeout* e teto de linhas, opcionalmente com usuário sem privilégio de
escrita; (5) verificação de que toda aresta declarada no resultado existe no grafo, recuperando a
evidência original. Erros não relacionados à segurança admitem uma única tentativa de correção com
mensagem sanitizada; qualquer outra falha leva ao *fallback* para a recuperação vetorial + grafo.

Para viabilizar modelos locais de pequeno porte, dois erros recorrentes observados com um modelo de
7B parâmetros são corrigidos por transformações determinísticas, aplicadas antes de toda a cadeia de
validação e registradas no resultado: (a) a declaração ausente da variável de caminho usada em
`relationships()`; e (b) a direção invertida de relações, tratada repetindo-se uma consulta válida
porém vazia com relações não direcionadas — os tipos continuam restringindo a correspondência e as
evidências devolvidas preservam a direção armazenada. Essas transformações apenas acrescentam uma
atribuição de caminho ou removem a orientação de setas, sem introduzir cláusulas, e não alteram a
garantia de somente-leitura.

## 4. Geração fundamentada e mitigação de alucinações

O contexto é delimitado por marcadores e declarado ao modelo como **dado não confiável**, mitigando
injeção de instruções contidas nos documentos. O prompt exige que toda afirmação seja acompanhada de
citações `[Fn]`, proíbe conhecimento externo, entidades, datas e URLs não presentes nas fontes, e
instrui a abstenção quando a evidência é insuficiente. O modelo devolve JSON com `status`, `answer` e
a lista de afirmações (`claims`) com suas fontes. A validação pós-geração é determinística:
citações inexistentes são removidas e contabilizadas; cada afirmação é considerada suportada se
todos os nomes próprios e números que contém aparecem nas fontes que cita (tolerando-se uma variação
de nome quando ao menos 60% das palavras de conteúdo coincidem), ou, para afirmações sem nomes, se ao
menos metade de suas palavras de conteúdo aparece nelas. Se menos de 60% das afirmações forem
suportadas, a resposta é substituída por uma abstenção explícita. Uma versão anterior, mais
permissiva, aceitava afirmações com nomes ausentes quando havia grande sobreposição de palavras, o que
no primeiro experimento deixou passar respostas do baseline apoiadas em conhecimento externo ao corpus. Essa verificação é lexical e, portanto, conservadora para
nomes e números, mas incapaz de detectar paráfrases incorretas de relações — limitação discutida na
Seção 8.

## 5. Indicador de confiança

A confiança é heurística e explicitamente não calibrada (`heuristico_nao_calibrado`). Combina
quatro fatores observáveis — cobertura de afirmações suportadas (0,35), procedência documental das
fontes citadas (0,25), relevância semântica das sementes ou trechos (0,20) e consistência (0,20;
conectividade das arestas citadas às sementes no subgrafo e diversidade de documentos) — com
penalidades por citações inexistentes, afirmações sem suporte, *fallback* do Text-to-Cypher,
entidades possivelmente duplicadas e contradições reconhecidas. Abstenções têm teto 0,25 e respostas
sem LLM, 0,60. O valor não deve ser interpretado como probabilidade de acerto; sua calibração contra
os julgamentos humanos do experimento é trabalho previsto.

## 6. Baseline RAG vetorial

O baseline recupera os *k* = 5 trechos (`:Chunk`) mais similares no índice `vec_chunk_embedding`, com o
mesmo codificador de consultas, e usa `TEM_CHUNK` apenas para obter título e URL do documento. Não
utiliza entidades, relações de domínio, caminhos, PageRank, comunidades nem Text-to-Cypher. Usa o
mesmo LLM, o mesmo prompt, a mesma validação e o mesmo teto de contexto (14.000 caracteres) que o
GraphRAG. A diferença inevitável é a granularidade: o baseline fornece trechos longos de texto,
enquanto o GraphRAG fornece fatos estruturados com a frase de evidência.

## 7. Protocolo experimental

* **Conjunto**: 30 perguntas construídas sobre o corpus (`parte_c/avaliacao/perguntas.jsonl`): 5 diretas,
  10 de dois saltos, 10 de três saltos e 5 sem resposta no corpus (incluindo uma premissa falsa e uma
  pergunta cujo tema aparece no corpus sem a resposta). As arestas-ouro e seus `chunk_id` foram
  extraídos e validados automaticamente contra o grafo (`parte_c/avaliacao/construir_gabarito.py`);
  a validação humana de cada item está registrada no campo `validacao_humana`.
* **Execução**: os dois modos respondem às mesmas perguntas, com a mesma configuração registrada em
  `config.json`. A geração usa um modelo aberto executado localmente — Qwen2.5-7B-Instruct
  (quantização Q4_K_M, servido pelo Ollama) —, com temperatura 0 e janela de contexto fixa de 12.288
  tokens, em uma GPU de consumo (NVIDIA RTX 4050 Laptop, 6 GB). A execução local elimina custo por
  consulta e dependência de serviço externo, e a temperatura nula favorece a reprodutibilidade;
  as respostas brutas são preservadas (`respostas.jsonl`). O mesmo modelo e os mesmos prompts são
  usados no GraphRAG e no baseline.
* **Métricas automáticas**: *recall* de fontes-ouro; *recall* de arestas-ouro (GraphRAG);
  acerto por palavras-chave esperadas; abstenção adequada; taxa de afirmações sem suporte
  (alucinação operacional); ocorrência de citações inexistentes; confiança; latência.
* **Métricas humanas** (`anotacao_humana.csv`): correção (0–2), suporte nas fontes (0–2), alucinação
  (sim/não), acerto multi-hop (sim/não). Recomenda-se dupla anotação em amostra para medir concordância.

## 8. Limitações

* Qualidade do grafo herdada das Partes A/B: entidades duplicadas (p.ex. *Ken Thompson*/*Kenneth
  Thompson*; variações de *Bell Labs*; *Nova Jérsei*/*Nova Jersey*), relação com direção invertida
  (*B* BASEADO_EM *C*) e 43% de entidades isoladas. O sistema apenas sinaliza duplicatas.
* A verificação de suporte é lexical; não detecta inversões de papel entre entidades corretamente
  nomeadas.
* Limiares de relevância e pesos da confiança não foram calibrados.
* O Text-to-Cypher depende da qualidade do LLM; com um modelo local de 7B parâmetros, parte das
  consultas geradas monta cadeias incorretas ou escolhe a entidade errada e aciona o *fallback*.
  Os resultados não devem ser generalizados para modelos maiores sem nova medição.
* A edição Community do Neo4j não oferece controle de acesso por papéis; nesse caso a garantia de
  somente-leitura repousa nas camadas 2–5.
* **Risco de sobreajuste:** as melhorias da segunda versão (pistas de relação, três saltos, termos
  compostos no fulltext, ajustes de prompt e de validação) foram desenvolvidas a partir da análise de
  erros nas mesmas 30 perguntas usadas na avaliação. Os ganhos entre as versões não devem ser lidos
  como estimativa de desempenho em perguntas novas; um conjunto de validação independente é trabalho
  futuro.
* **Falsos negativos conhecidos do validador** (afetam os dois modos, não corrigidos na execução
  reportada): siglas de duas letras (p.ex. "GE") nunca são reconhecidas nas fontes, e nomes que apenas
  repetem a pergunta (p.ex. "o criador da linguagem Go") são exigidos na fonte citada. Causaram ao
  menos uma abstenção indevida em cada modo (M03 no GraphRAG, D02 no baseline).
* **Respostas incompletas:** o modelo de 7B por vezes omite itens presentes no contexto (p.ex. um dos
  criadores do Unix, um dos prêmios), o que a métrica por palavras-chave conta como erro.
* **Amostra pequena e execução única:** 30 perguntas, um modelo, uma execução; não há teste de
  significância estatística.

## 9. Resultados

Execução oficial: `parte_c/avaliacao/resultados/20261111-222956` (11/11/2026, Qwen2.5-7B-Instruct via Ollama,
temperatura 0, *k* = 5, até 3 saltos, até 30 arestas, teto de contexto de 14.000 caracteres, estratégia
híbrida). **Métricas automáticas e aproximadas** — a anotação humana (`anotacao_humana.csv`) e a
validação humana do gabarito ainda estão pendentes; os números devem ser revistos após essa etapa.

**Tabela 1 — Acerto por palavras-chave nas perguntas respondíveis (n = 25)**

| Tipo | n | GraphRAG | Baseline |
|---|---|---|---|
| Diretas | 5 | 4 (80%) | 3 (60%) |
| 2 saltos | 10 | 8 (80%) | 4 (40%) |
| 3 saltos | 10 | 10 (100%) | 3 (30%) |
| **Total** | **25** | **22 (88%)** | **10 (40%)** |

Comparação pareada: o GraphRAG acertou 14 perguntas em que o baseline errou; o baseline acertou 2 em
que o GraphRAG errou (D01 e M10, ambas respostas incompletas do GraphRAG).

**Tabela 2 — Comportamento e custo (30 perguntas)**

| Métrica | GraphRAG | Baseline |
|---|---|---|
| Abstenção correta nas 5 perguntas sem resposta | 5/5 | 5/5 |
| Respostas entregues nas 25 respondíveis | 24 | 13 |
| Afirmações sem suporte entre as respostas entregues | 0% | 2,6% |
| Citações inexistentes geradas | 0 | 0 |
| Arestas-ouro presentes no contexto | 100% | — |
| Latência média por pergunta | 30,5 s | 13,4 s |

No GraphRAG, o Text-to-Cypher teve a consulta executada em 15 das 30 perguntas (6 delas após reparo
determinístico), foi rejeitado pela política em 1 e recorreu à recuperação vetorial em 14 (incluindo
as 5 perguntas sem resposta, em todas as quais o modelo declarou a pergunta não respondível pelo
esquema).

**Evolução entre versões (mesmo gabarito):** a primeira versão (`20261111-214354`, 2 saltos, ranking
só semântico, validação mais permissiva) obteve 18/25 no GraphRAG (3 saltos: 6/10) e 11/25 no
baseline, com 12% de afirmações sem suporte no baseline. A segunda versão elevou o GraphRAG a 22/25
(3 saltos: 10/10) e reduziu as afirmações sem suporte do baseline para 2,6%; a queda de 11 para 10
acertos no baseline reflete sobretudo respostas que dependiam de conhecimento externo ao corpus e
passaram a ser recusadas. Ver ressalva de sobreajuste na Seção 8.

**Análise qualitativa (para a discussão):**
* A vantagem do GraphRAG concentra-se nas perguntas multi-hop, em que a resposta exige combinar fatos
  de documentos diferentes (p.ex. Unix → Bell Labs → Murray Hill); o baseline recupera trechos
  relevantes a um dos elos, mas raramente a todos.
* Na primeira versão, o baseline acertou perguntas como T01 e T04 citando uma cidade ausente dos
  trechos recuperados (conhecimento paramétrico do modelo); a validação mais rígida passou a recusar
  essas respostas. É um exemplo do custo, em cobertura, da exigência de fundamentação.
* Um caso do gabarito (T08) mostrou-se ambíguo: o corpus sustenta tanto Stanford/Palo Alto (aresta
  extraída) quanto MIT/Cambridge (presente apenas no texto, sem aresta no grafo), o que também evidencia
  uma lacuna de extração da Parte A.
* O custo do GraphRAG é a latência (≈2,3× a do baseline), dominada pelas chamadas extras ao LLM do
  Text-to-Cypher.

*Fontes: `parte_c/avaliacao/resultados/20261111-222956/resumo.md`, `metricas.csv` e
`comparacao_com_20261111-214354.txt`.*

## 10. Referências de software
Neo4j 5.26 (índices vetoriais e fulltext, Cypher); Neo4j Python Driver 6.x; Neo4j Graph Data Science
(PageRank, Louvain — executados pela Parte B); Sentence-Transformers; Pydantic; Ollama;
Qwen2.5-7B-Instruct (Alibaba); httpx; pytest. Opcional, não usado nos experimentos: Anthropic Python SDK.
