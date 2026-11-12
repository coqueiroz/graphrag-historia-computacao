import sys
from pathlib import Path

# parte_b/ (módulos da Parte B) e a raiz do projeto (contracts.py) no caminho de importação.
_AQUI = Path(__file__).resolve()
for _p in (_AQUI.parents[2], _AQUI.parents[1]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
