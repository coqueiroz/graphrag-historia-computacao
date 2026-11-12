import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Raiz do projeto integrado (onde ficam a pasta 'parte_c' e o '.env'): a pasta acima de parte_d/.
# Pode ser sobrescrita com a variável de ambiente GRAPHRAG_REPO.
DEFAULT_REPO_C = Path(__file__).resolve().parent.parent
REPO_C = Path(os.getenv("GRAPHRAG_REPO", DEFAULT_REPO_C)).resolve()

if str(REPO_C) not in sys.path:
    sys.path.insert(0, str(REPO_C))

# Carrega o .env do repositório da C (Neo4j + Ollama) antes de importar parte_c
load_dotenv(REPO_C / ".env")

from parte_c import responder_pergunta  # noqa: E402
from adaptador_c import adaptar  # noqa: E402


def ask(pergunta: str, mode: str = "graphrag") -> dict:
    return adaptar(responder_pergunta(pergunta, modo="baseline" if mode == "rag" else "graphrag"))