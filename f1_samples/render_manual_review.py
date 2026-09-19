"""从 pairs.json 生成 manual_review.md（B 类人工核对清单）。

    ../实验脚本/A类规则计算脚本/.venv/bin/python render_manual_review.py

B 类三条轴（举例 / 结构 / 深度）无法自动核——PRD §8.5 的 judge 校准没做，
在校准之前用模型判 B 类，等于**把未经验证的测量工具的输出当作指标**。
这份清单是它的替代。

**这个文件是生成物，会被覆盖。** 核对结论写在 `manual_review_result.md`，
那个文件手写、脚本不碰——生成物和人工结论混在一起，重跑一次结论就没了。
"""

from __future__ import annotations

import json
from pathlib import Path

import _paths  # noqa: F401

from style_axes import AXES, DEFAULTS

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))

AXIS_CN = {
    "detail_level": "详细程度", "use_examples": "举例",
    "structure": "结构", "depth": "深度",
}

lines: list[str] = []
w = lines.append

w("# F1 样例 · B 类人工核对清单")
w("")
w("> **生成物，会被 `render_manual_review.py` 覆盖。**")
w("> 核对结论写进 [`manual_review_result.md`](manual_review_result.md)，那个文件脚本不碰。")
w("")
w(f"- 样例来源：`pairs.json`（生成于 {DATA['generated_at']}，deck sha256 `{DATA['deck']['sha256']}`）")
w(f"- 模型：`{DATA['model']['id']}`（取自 `explain.llm.AnthropicModel` 的默认值——"
  f"生产讲解用哪个模型，样例就必须用哪个，否则用户选到的不是他真会得到的东西）")
# 2026-09-19 起**每对一个问题**（此前 8 对共用一个，用户连读 12 段几乎一样的内容）。
w("- 提问：每对各不相同，见下面每一节的标题行")
w(f"- 非目标维度的默认值：{DEFAULTS}")
w("")
w("## 为什么 B 类只能人工核")
w("")
w("四条轴里只有**详细程度**被 A 类规则执行（`paragraph_length`）。举例 / 结构 / 深度")
w("只进 prompt、不进判定（每个样例的 `enforced` 字段都标了 `false`），")
w("因为 PRD §8.5 的 judge 可靠性校准尚未完成。**宁可指标覆盖面窄，不可指标来源不可信。**")
w("")
w("## 每对怎么核")
w("")
w("四个勾选项，前两个是 F01 的两面：")
w("")
w("1. **目标维度上确实不同，且方向正确** —— 如 `more` 那侧确实例子更多，不是反过来")
w("2. **非目标 B 类维度上尽量一致** —— 目标是深度的那对，两侧的举例密度与结构应当接近")
w("3. **两个样例讲的事实一致，没有一个讲错** —— 事实错误会让用户的选择变成对错误的选择")
w("4. **无超长引用** —— 自动核过（全部 ≤ 100 字），人工再扫一遍")
w("")
w("任意一条不过 → **重新生成那一对，不要修改样例文本**（F01 的使用纪律）：")
w("")
w("```bash")
w("env $(grep -v '^#' ../实验脚本/配置扫描实验/.env | xargs) \\")
w("  ../实验脚本/A类规则计算脚本/.venv/bin/python generate_pairs.py --pairs <pair_id>")
w("```")
w("")
w("---")

for pair in DATA["pairs"]:
    axis = pair["target_dimension"]
    a, b = pair["samples"]
    w("")
    w(f"## {pair['pair_id']} —— 目标维度：**{AXIS_CN[axis]}**（`{axis}`）")
    w("")
    w(f"**这一对的提问**：{pair['question']}　·　"
      f"喂了 {len(pair['fed_blocks'])} 块（"
      + "、".join(f"{b['id']}·{b['chars']}字符" for b in pair["fed_blocks"]) + "）")
    w("")
    w(f"`{a['variant_label']}` vs `{b['variant_label']}`"
      f"　·　非目标维度两侧相同：" +
      "、".join(f"{AXIS_CN[k]}={a['style'][k]['value']}" for k in AXES if k != axis))
    w("")
    if axis != "detail_level":
        frag = DATA["b_class_prompt_fragments"][axis]
        w("两侧 prompt 措辞（差异只有这一句）：")
        w("")
        w(f"- `{a['variant_label']}`：{frag[a['variant_label']]}")
        w(f"- `{b['variant_label']}`：{frag[b['variant_label']]}")
        w("")
    w("- [ ] 1 目标维度上确实不同，且方向正确")
    w("- [ ] 2 非目标 B 类维度上尽量一致")
    w("- [ ] 3 两个样例讲的事实一致，没有一个讲错")
    w("- [ ] 4 无超长引用")
    w("")
    for s in (a, b):
        w(f"<details><summary><b>{s['sample_id']}</b> · {s['variant_label']}"
          f" · 第 {s['attempt']} 次生成 · {len(s['text'])} 字符</summary>")
        w("")
        w(s["text"].strip())
        w("")
        w("</details>")
        w("")

(HERE / "manual_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"写入 manual_review.md：{len(DATA['pairs'])} 对")
