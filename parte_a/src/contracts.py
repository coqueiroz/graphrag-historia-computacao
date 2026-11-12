"""
CONTRATO entre os módulos do projeto.

Este arquivo define o FORMATO dos dados que a Pessoa A (extração) entrega
para a Pessoa B (Neo4j). Ninguém altera este arquivo sem avisar o grupo.
"""
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# SCHEMA DO GRAFO (domínio: História da Computação)
# ---------------------------------------------------------------------------
ALLOWED_LABELS = [
    "Pessoa",
    "Instituicao",
    "Tecnologia",
    "Premio",
    "Conceito",
    "Local",
]

ALLOWED_RELATIONS = [
    "TRABALHOU_EM",     # Pessoa -> Instituicao
    "ESTUDOU_EM",       # Pessoa -> Instituicao
    "CRIOU",            # Pessoa -> Tecnologia/Conceito
    "ORIENTOU",         # Pessoa -> Pessoa
    "RECEBEU",          # Pessoa -> Premio
    "INFLUENCIOU",      # Pessoa/Tecnologia/Conceito -> Pessoa/Tecnologia/Conceito
    "COLABOROU_COM",    # Pessoa -> Pessoa
    "DESENVOLVIDO_EM",  # Tecnologia -> Instituicao
    "BASEADO_EM",       # Tecnologia -> Tecnologia/Conceito
    "LOCALIZADO_EM",    # Instituicao -> Local
]

# De qual tipo de nó cada relação pode sair (origem) e para qual pode ir (destino).
# Relações que não respeitam isso são descartadas na limpeza.
_SEM_LOCAL = ["Pessoa", "Instituicao", "Tecnologia", "Conceito", "Premio"]
RELATION_SCHEMA = {
    "TRABALHOU_EM":    (["Pessoa"], ["Instituicao"]),
    "ESTUDOU_EM":      (["Pessoa"], ["Instituicao"]),
    "CRIOU":           (["Pessoa", "Instituicao"], ["Tecnologia", "Conceito", "Premio"]),
    "ORIENTOU":        (["Pessoa"], ["Pessoa"]),
    "RECEBEU":         (["Pessoa"], ["Premio"]),
    "INFLUENCIOU":     (_SEM_LOCAL, _SEM_LOCAL),
    "COLABOROU_COM":   (["Pessoa"], ["Pessoa"]),
    "DESENVOLVIDO_EM": (["Tecnologia", "Conceito"], ["Instituicao"]),
    "BASEADO_EM":      (["Tecnologia", "Conceito"], ["Tecnologia", "Conceito"]),
    "LOCALIZADO_EM":   (["Instituicao"], ["Local"]),
}


# ---------------------------------------------------------------------------
# FORMATOS DE DADOS
# ---------------------------------------------------------------------------
class Entity(BaseModel):
    """Uma 'bolinha' do grafo (nó)."""
    id: str = Field(description="ID canônico, ex: 'pessoa:alan_turing'")
    label: str = Field(description="Um dos ALLOWED_LABELS")
    name: str = Field(description="Nome principal, ex: 'Alan Turing'")
    aliases: list[str] = Field(default_factory=list, description="Outras formas do nome")
    chunk_ids: list[str] = Field(default_factory=list, description="Trechos onde aparece")


class Relation(BaseModel):
    """Uma 'seta' do grafo (relacionamento)."""
    source_id: str
    target_id: str
    type: str = Field(description="Um dos ALLOWED_RELATIONS")
    evidence: str = Field(description="Trecho do texto que comprova o fato")
    chunk_id: str = Field(description="De qual trecho o fato veio")


class Chunk(BaseModel):
    """Um pedaço de texto de um documento (serve para citar a fonte)."""
    chunk_id: str
    doc_id: str
    text: str
    position: int


class Document(BaseModel):
    """Um documento original (uma página da Wikipedia)."""
    doc_id: str
    title: str
    url: str


class ExtractionResult(BaseModel):
    """O que a Pessoa A entrega para a Pessoa B: o grafo inteiro."""
    documents: list[Document]
    chunks: list[Chunk]
    entities: list[Entity]
    relations: list[Relation]
