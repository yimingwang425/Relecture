"""让 drift 能 import 同级目录下的 a_rules。

刻意用路径注入而非打包安装：a_rules 是零依赖模块，漂移实验是一次性脚本，
为一个实验引入打包与安装步骤不划算。若日后 a_rules 进服务端，改为正式依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

A_RULES = Path(__file__).resolve().parent.parent.parent / "A类规则计算脚本"

if str(A_RULES) not in sys.path:
    sys.path.insert(0, str(A_RULES))
