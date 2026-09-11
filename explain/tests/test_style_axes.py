"""四条风格轴的**产品定义**与 B 类渲染 —— 2026-09-11 从 `f1_samples/` 搬上来。

**本文件测的不是「函数会算」，是「搬家没有改变任何一个字节」。**

搬家改的是出处，不该改任何输出。改了的话后果分两层：

- **A 类**：`config_fingerprint` 变 → 配置扫描实验那 180 轮已落盘数据对应不上任何现存代码；
- **B 类**：措辞变 → `pairs.json` 里 16 个样例与生产分叉，
  **用户按样例选完拿到的就不是他选的那个**，F18/F19 的前提垮掉。

第二层有一个现成的锚：`pairs.json` 的每个样例都记着它生成时用的
`prompt.a_class_rules` 与 `prompt.b_class_rules` **原文**。所以这里不比对我手抄的
期望值，而是**比对当初真的发出去的那一份**（口径 F25 的同一条：结论要锚定版本）。

跑法与 `test_prompt_profile.py` 相同：不用 fixture，可 `python3` 直跑，
由 `web/scripts/verify_explain_py.mjs` 接进 `npm run verify:guards`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EXPLAIN = Path(__file__).resolve().parent.parent
if str(_EXPLAIN) not in sys.path:
    sys.path.insert(0, str(_EXPLAIN))

import explain  # noqa: E402,F401  — 必须先于 a_rules，它注入 a_rules 的路径

from a_rules.config import StyleConfig  # noqa: E402
from explain.style_axes import (  # noqa: E402
    AXES,
    DEFAULTS,
    DETAIL_LADDER,
    DETAIL_OVERRIDES,
    ENFORCED,
    style_config_for,
)
from explain.style_prompt import (  # noqa: E402
    B_CLASS_FRAGMENTS,
    render_all_style_rules,
    render_b_class_rules,
    render_style_rules,
)

REPO = _EXPLAIN.parent
PAIRS_JSON = REPO / "f1_samples" / "pairs.json"

#: 搬家**之前**实测的三个指纹。搬家不允许改变它们中的任何一个。
#: 这三个数是 2026-09-11 搬家前从 `scan.points` 那条路径上取的。
FINGERPRINTS_BEFORE_MOVE = {
    "P0": "a223ca42e77a",
    "P2": "702c48759329",
    "P3": "4e0fbd6bec46",
}


def _samples():
    data = json.loads(PAIRS_JSON.read_text(encoding="utf-8"))
    for pair in data["pairs"]:
        for s in pair["samples"]:
            yield s


def _style_of(sample) -> dict[str, str]:
    """`pairs.json` 里 style 是 `{axis: {value, enforced}}`，取回扁平取值。"""
    return {axis: v["value"] for axis, v in sample["style"].items()}


class TestMoveChangedNothing:
    """搬家的唯一判据：**输出逐字节不变**。"""

    def test_fingerprints_unchanged(self):
        for tier, expected in FINGERPRINTS_BEFORE_MOVE.items():
            got = style_config_for(tier).fingerprint()
            assert got == expected, f"{tier} 的指纹从 {expected} 变成了 {got} —— 搬家改了数值"

    def test_p0_has_no_literal_range(self):
        """P0 的覆盖项必须是空字典。

        30–120 是 `StyleConfig` 的**默认值**，不是任何地方的字面量（口径 F13）。
        写成 `{"paragraph_length": {"min": 30, "max": 120}}` 会凭空造出第二处出处，
        而它与默认值漂开时没有任何东西会红。
        """
        assert DETAIL_OVERRIDES["P0"] == {}
        assert style_config_for("P0").paragraph_length.to_dict() == (
            StyleConfig().paragraph_length.to_dict()
        )

    def test_a_class_matches_every_sample_it_generated(self):
        """16 个样例各自记着生成时的 A 类原文，逐个比。

        这是 F19 的锚：比对的不是我写下的期望值，**是当初真的发出去的那一份**。
        """
        n = 0
        for s in _samples():
            cfg = style_config_for(_style_of(s)["detail_level"])
            expected = s["prompt"]["a_class_rules"]
            assert render_style_rules(cfg) == expected, (
                f"{s['sample_id']} 的 A 类渲染与它生成时的原文不一致 —— "
                "样例与生产已分叉（口径 F19）"
            )
            n += 1
        assert n == 16, f"只比对了 {n} 个样例，应为 16"

    def test_b_class_matches_every_sample_it_generated(self):
        n = 0
        for s in _samples():
            expected = s["prompt"]["b_class_rules"]
            assert render_b_class_rules(_style_of(s)) == expected, (
                f"{s['sample_id']} 的 B 类渲染与它生成时的原文不一致"
            )
            n += 1
        assert n == 16

    def test_fragments_match_the_recorded_table(self):
        """`pairs.json` 顶层存了当时那份 `B_CLASS_FRAGMENTS`，逐项比。

        上面两条比的是**渲染结果**，这条比的是**原料**——一句被改写、另一句被
        反向改写回去，渲染结果可能仍然相等，而原料表会红。
        """
        recorded = json.loads(PAIRS_JSON.read_text(encoding="utf-8"))["b_class_prompt_fragments"]
        assert recorded == B_CLASS_FRAGMENTS, "B 类措辞与 pairs.json 记录的那份不一致"


class TestJoinShape:
    """A + B 的拼法。**样例就是按这个形状生成的。**"""

    def test_join_is_a_newline_b(self):
        cfg = style_config_for("P0")
        assert render_all_style_rules(cfg, DEFAULTS) == (
            render_style_rules(cfg) + "\n" + render_b_class_rules(DEFAULTS)
        )

    def test_sample_generator_uses_the_same_join(self):
        """`generate_pairs.py` 必须调 `render_all_style_rules`，不自己拼。

        自己拼的话，拼法就有两处出处：生产改了拼法而样例没改（或反过来），
        **没有任何东西会报错**，只是用户按样例选完拿到的不是他选的那个。
        """
        src = (REPO / "f1_samples" / "generate_pairs.py").read_text(encoding="utf-8")
        assert "render_all_style_rules(" in src, "generate_pairs.py 没有调用生产的拼接函数"
        assert 'a_class + "\\n" + b_class' not in src, "generate_pairs.py 里还留着自己拼的那一行"


class TestDomains:
    def test_b_class_fragment_keys_equal_axes(self):
        """B 类取值域现在有两处形态：`AXES` 与 `B_CLASS_FRAGMENTS` 的键。

        两处存在 ≠ 两处一致（口径 F33）。少一个取值的表现是
        `render_b_class_rules` 抛 KeyError —— 那要到有用户选中它时才发生。
        """
        for axis in ("use_examples", "structure", "depth"):
            assert set(B_CLASS_FRAGMENTS[axis]) == set(AXES[axis]), axis

    def test_detail_tiers_match_axes(self):
        assert set(DETAIL_OVERRIDES) == set(AXES["detail_level"])
        assert DETAIL_LADDER == AXES["detail_level"]

    def test_p1_is_rejected_not_defaulted(self):
        """P1 已退出（口径 F23）。**抛异常，不回落默认值。**

        回落会让「用户选了 P3」和「库里有一个我们不认识的值」在生成结果里
        长得一模一样（同 F32 的「回落必须显式」）。
        """
        try:
            style_config_for("P1")
        except ValueError as exc:
            assert "P1" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("P1 没有被拒 —— 它被当成某个默认档位处理了")

    def test_only_detail_level_is_enforced(self):
        """四条轴里只有一条被 A 类审查执行（PRD §8.5 未校准前，B 类只进 prompt）。"""
        assert ENFORCED["detail_level"] is True
        assert not any(ENFORCED[a] for a in ("use_examples", "structure", "depth"))


class TestDiscrimination:
    """判据自身的分辨力：上面那些「相等」得是**能不相等**的。"""

    def test_changing_a_fragment_changes_the_render(self):
        st = dict(DEFAULTS)
        base = render_b_class_rules(st)
        moved = render_b_class_rules({**st, "depth": "derivation"})
        assert moved != base, "换一条轴的取值渲染却没变 —— 上面那些逐字节断言是空转的"

    def test_changing_tier_changes_the_a_class_render(self):
        assert render_style_rules(style_config_for("P0")) != render_style_rules(
            style_config_for("P3")
        ), "P0 与 P3 渲染出同一段 A 类规则 —— 档位没有进 prompt"

    def test_every_tier_renders_its_own_range(self):
        """三个档位两两不同。少了它，一个恒返回 P0 的实现也能让上一条过。"""
        rendered = {t: render_style_rules(style_config_for(t)) for t in DETAIL_OVERRIDES}
        assert len(set(rendered.values())) == len(rendered)


def _main() -> int:
    import traceback

    ok = bad = 0
    for cls_name in sorted(n for n in globals() if n.startswith("Test")):
        cls = globals()[cls_name]
        if not isinstance(cls, type):
            continue
        inst = cls()
        for t in sorted(n for n in dir(inst) if n.startswith("test_")):
            try:
                getattr(inst, t)()
            except Exception:
                bad += 1
                print(f"  \033[31m✗\033[0m {cls_name}.{t}")
                traceback.print_exc()
            else:
                ok += 1
                print(f"  \033[32m✓\033[0m {cls_name}.{t}")
    colour = "\033[32m" if bad == 0 else "\033[31m"
    print(f"\n{colour}{ok} 绿 / {bad} 红\033[0m\n")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
