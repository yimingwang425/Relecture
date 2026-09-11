"""产品服务端。

同仓库的兄弟包走路径注入，与 `f1_samples/_paths.py` 同一约定：

    explain      —— prompt 组装、风格轴、pipeline
    a_rules      —— A 类判定与 StyleConfig（explain 依赖它）
    retrieval    —— compose（组文本口径）与 selection（模型/维度的唯一出处）

`slicer` 与 `parse_service` 是 `pip install -e` 装进来的（它们有自己的第三方依赖），
不在这里注入——两种方式并存是既有事实，不是本次引入的。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (
    REPO / "explain",
    REPO / "实验脚本" / "A类规则计算脚本",
    REPO / "retrieval",
):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
