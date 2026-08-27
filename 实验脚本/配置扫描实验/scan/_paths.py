"""注入同仓库的 a_rules / explain / retrieval 路径。

与 `漂移实验/drift/_paths.py` 同一约定：实验是一次性脚本，
为它引入打包与安装步骤不划算。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
A_RULES = REPO / "实验脚本" / "A类规则计算脚本"
EXPLAIN = REPO / "explain"
DRIFT = REPO / "实验脚本" / "漂移实验"
RETRIEVAL = REPO / "retrieval"

for _p in (A_RULES, EXPLAIN, DRIFT, RETRIEVAL):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
