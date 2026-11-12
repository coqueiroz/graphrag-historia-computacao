"""
Configuração da Parte C (LLM e limites), lida do `.env` / variáveis de ambiente.

A conexão com o Neo4j NÃO é configurada aqui: reutilizamos `neo4j_config.py` da Pessoa B.
Nenhum segredo é guardado em atributos impressos: `Settings.public()` devolve só o que é seguro mostrar.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Raiz do projeto (onde estão neo4j_config.py, embeddings.py, consultas_referencia.py).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Módulos da Pessoa B (neo4j_config, embeddings, consultas_referencia) ficam em parte_b/.
PARTE_B = ROOT / "parte_b"
if str(PARTE_B) not in sys.path:
    sys.path.insert(0, str(PARTE_B))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv é opcional
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ---- LLM (geração de texto; NÃO confundir com o modelo de embeddings da Parte B) ----
    llm_provider: str = "ollama"        # ollama (principal) | anthropic (opcional) | none (sem LLM)
    llm_model: str = ""                 # vazio -> padrão do provedor (ollama: qwen2.5:7b)
    llm_effort: str = "medium"          # só anthropic: low | medium | high | xhigh | max
    llm_max_tokens: int = 4000
    llm_timeout_s: float = 120.0
    llm_server_fallback: bool = True    # só anthropic: fallback do servidor em caso de recusa
    ollama_url: str = "http://localhost:11434"
    ollama_num_ctx: int = 12288          # janela de contexto do Ollama (padrão dele é só 4096)

    # ---- Recuperação GraphRAG ----
    strategy: str = "auto"              # vector | text2cypher | auto
    top_k: int = 5                      # sementes vindas do índice vetorial
    hops: int = 3                       # saltos de expansão (1..3); 3 cobre as perguntas de 3 saltos
    max_paths_per_seed: int = 60
    max_edges: int = 30                 # arestas (fatos) mantidas no contexto
    min_seed_score: float = 0.55        # similaridade mínima de uma semente vetorial
    fulltext_seeds: int = 3             # sementes extras pelo índice fulltext de nomes

    # ---- Baseline ----
    baseline_top_k: int = 5

    # ---- Limites de segurança / tamanho ----
    query_timeout_s: float = 15.0       # timeout de transação no Neo4j
    t2c_max_rows: int = 50
    t2c_max_hops: int = 4
    max_context_chars: int = 14000      # mesmo teto para GraphRAG e baseline
    quote_max_chars: int = 600

    # ---- Usuário opcional somente-leitura no Neo4j (recomendado para Text-to-Cypher) ----
    readonly_user: str = field(default="", repr=False)
    readonly_password: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower() or "ollama",
            llm_model=os.getenv("LLM_MODEL", "").strip(),
            llm_effort=os.getenv("LLM_EFFORT", "medium").strip().lower(),
            llm_max_tokens=_int("LLM_MAX_TOKENS", 4000),
            llm_timeout_s=_float("LLM_TIMEOUT_S", 120.0),
            llm_server_fallback=os.getenv("LLM_SERVER_FALLBACK", "1").strip() not in ("0", "false", "no"),
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
            ollama_num_ctx=_int("OLLAMA_NUM_CTX", 12288),
            strategy=os.getenv("GRAPHRAG_STRATEGY", "auto").strip().lower(),
            top_k=_int("GRAPHRAG_TOP_K", 5),
            hops=_int("GRAPHRAG_HOPS", 3),
            max_paths_per_seed=_int("GRAPHRAG_MAX_PATHS_PER_SEED", 60),
            max_edges=_int("GRAPHRAG_MAX_EDGES", 30),
            min_seed_score=_float("GRAPHRAG_MIN_SEED_SCORE", 0.55),
            fulltext_seeds=_int("GRAPHRAG_FULLTEXT_SEEDS", 3),
            baseline_top_k=_int("BASELINE_TOP_K", 5),
            query_timeout_s=_float("NEO4J_QUERY_TIMEOUT_S", 15.0),
            t2c_max_rows=_int("T2C_MAX_ROWS", 50),
            t2c_max_hops=_int("T2C_MAX_HOPS", 4),
            max_context_chars=_int("MAX_CONTEXT_CHARS", 14000),
            quote_max_chars=_int("QUOTE_MAX_CHARS", 600),
            readonly_user=os.getenv("NEO4J_READONLY_USER", ""),
            readonly_password=os.getenv("NEO4J_READONLY_PASSWORD", ""),
        )

    def public(self) -> dict:
        """Configuração sem segredos (pode ir para logs/trace)."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model or "(padrão do provedor)",
            "strategy": self.strategy,
            "top_k": self.top_k,
            "hops": self.hops,
            "max_edges": self.max_edges,
            "baseline_top_k": self.baseline_top_k,
            "max_context_chars": self.max_context_chars,
            "readonly_user_configurado": bool(self.readonly_user),
        }
