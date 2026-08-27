"""让 drift 能 import 同仓库里的 a_rules 与 explain。

刻意用路径注入而非打包安装：a_rules 是零依赖模块，漂移实验是一次性脚本，
为一个实验引入打包与安装步骤不划算。若日后 a_rules 进服务端，改为正式依赖。

`explain` 于 2026-08-22 加入：模型抽象与定价表提升到了 `explain/llm.py`，
本目录只保留再导出。理由见 `models.py` 头部。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent.parent
A_RULES = _REPO / "实验脚本" / "A类规则计算脚本"
EXPLAIN = _REPO / "explain"

for _p in (A_RULES, EXPLAIN):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
