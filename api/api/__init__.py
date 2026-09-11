"""产品服务端。同仓库的 explain / a_rules 走路径注入，与 f1_samples/_paths.py 同一约定。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "explain", REPO / "实验脚本" / "A类规则计算脚本"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
