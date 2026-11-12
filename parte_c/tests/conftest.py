import sys
from pathlib import Path

# Raiz do projeto (contracts.py, parte_c) e módulos da Parte B (parte_b/) no caminho de importação.
_RAIZ = Path(__file__).resolve().parents[2]
for _p in (_RAIZ, _RAIZ / "parte_b", Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: testes da Parte C contra o Neo4j real (somente leitura; pulados se o banco estiver desligado)")
