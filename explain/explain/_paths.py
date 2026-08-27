"""让 explain 能 import 同仓库里的 a_rules。

a_rules 是零第三方依赖的度量模块，住在 `实验脚本/A类规则计算脚本/`。
路径注入而非打包安装，与 `漂移实验/drift/_paths.py` 同一约定——
若日后整体上服务端，两处一起改为正式依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

A_RULES = Path(__file__).resolve().parent.parent.parent / "实验脚本" / "A类规则计算脚本"

if A_RULES.is_dir() and str(A_RULES) not in sys.path:
    sys.path.insert(0, str(A_RULES))
