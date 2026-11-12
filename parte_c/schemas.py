"""
Contratos de dados da Parte C (Pydantic). Tudo aqui é serializável em JSON.

- `RetrievalResult`  : o que a recuperação (GraphRAG ou baseline) devolve, ANTES do LLM.
- `QAResponse`       : o contrato estável entregue à Pessoa D por `responder_pergunta()`.

Identidade pública dos nós = `Entidade.id` do domínio (ex.: 'pessoa:ken_thompson'),
nunca `elementId()`. Embeddings nunca são incluídos.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Mode(str, Enum):
    GRAPHRAG = "graphrag"
    BASELINE = "baseline"


class Status(str, Enum):
    ANSWERED = "answered"                          # resposta gerada e validada
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # abstenção honesta
    RETRIEVAL_ONLY = "retrieval_only"              # sem LLM configurado: só evidências
    ERROR = "error"                                # falha (mensagem sanitizada em warnings)


class CypherStatus(str, Enum):
    EXECUTED = "executed"            # gerado, validado e executado (resultado usado)
    REJECTED = "rejected"            # gerado, mas reprovado na validação; não executado
    FALLBACK = "fallback"            # falhou/rejeitado -> usada a recuperação vetorial (Q8)
    NOT_APPLICABLE = "not_applicable"  # estratégia não usa Text-to-Cypher (ou modo baseline)


# --------------------------------------------------------------------------- grafo
class Node(BaseModel):
    id: str
    name: str
    label: str
    pagerank: Optional[float] = None
    community: Optional[int] = None
    similarity: Optional[float] = Field(None, description="cosseno(pergunta, embedding do nó)")


class Edge(BaseModel):
    source: str
    target: str
    type: str
    evidence: str = ""
    chunk_id: Optional[str] = None
    score: float = 0.0
    hop: int = 1
    origin: str = Field("vector", description="vector | text2cypher")

    def key(self) -> tuple:
        return (self.source, self.type, self.target, self.chunk_id or "")


class Seed(BaseModel):
    id: str
    name: str
    label: str
    similarity: float
    origin: str = Field("vector", description="vector | fulltext | text2cypher")


class RelationRef(BaseModel):
    source_id: str
    type: str
    target_id: str
    source_name: Optional[str] = None
    target_name: Optional[str] = None


class Source(BaseModel):
    """Uma fonte citável [Fn]. Sempre aponta para um Chunk real do banco."""
    id: str                                 # "F1", "F2", ...
    doc_id: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    chunk_id: Optional[str] = None
    quote: str = Field("", description="trecho literal (evidência da aresta ou texto do chunk)")
    context: Optional[str] = Field(None, description="janela do chunk em torno da evidência")
    relation: Optional[RelationRef] = None
    score: float = 0.0
    evidence_in_chunk: Optional[bool] = Field(
        None, description="a evidência foi encontrada literalmente no texto do chunk?")
    cited: bool = Field(False, description="a resposta final cita esta fonte?")


class Subgraph(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class ExecutedQuery(BaseModel):
    """Trace de uma consulta Cypher executada (parâmetros sem vetores/segredos)."""
    name: str
    query: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rows: int = 0
    ms: float = 0.0


class GeneratedCypher(BaseModel):
    query: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: CypherStatus = CypherStatus.NOT_APPLICABLE
    rationale: Optional[str] = None
    attempts: int = 0
    repairs: list[str] = Field(default_factory=list, description="reparos determinísticos aplicados à consulta do LLM")
    error: Optional[str] = None
    rows: int = 0


class RetrievalResult(BaseModel):
    question: str
    mode: Mode
    strategy: str
    hops: Optional[int] = None
    relation_cues: list[str] = Field(default_factory=list, description="relações citadas na pergunta")
    seeds: list[Seed] = Field(default_factory=list)
    subgraph: Subgraph = Field(default_factory=Subgraph)
    sources: list[Source] = Field(default_factory=list)
    generated_cypher: GeneratedCypher = Field(default_factory=GeneratedCypher)
    queries: list[ExecutedQuery] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- resposta
class Claim(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list)
    supported: bool = False
    support_reason: str = ""


class Confidence(BaseModel):
    score: float
    level: str                      # low | medium | high
    method: str = "heuristico_nao_calibrado"
    factors: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""


class QAResponse(BaseModel):
    question: str
    mode: Mode
    status: Status
    answer: str
    confidence: Confidence
    sources: list[Source] = Field(default_factory=list)
    subgraph: Subgraph = Field(default_factory=Subgraph)
    generated_cypher: GeneratedCypher = Field(default_factory=GeneratedCypher)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    latency_ms: float = 0.0
    llm: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    trace: Optional[dict[str, Any]] = None
