"""注入同仓库的 a_rules / explain / 配置扫描实验 路径。

与 `配置扫描实验/scan/_paths.py`、`漂移实验/drift/_paths.py` 同一约定。
本目录是生产资产而非实验，但包路径注入的问题一样——为它引入打包与安装步骤不划算。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
A_RULES = REPO / "实验脚本" / "A类规则计算脚本"
EXPLAIN = REPO / "explain"
#: 档位 → 区间的唯一出处（口径 F13）。本目录不重写这四组数字。
SCAN = REPO / "实验脚本" / "配置扫描实验"

for _p in (A_RULES, EXPLAIN, SCAN):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
