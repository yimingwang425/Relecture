"""`prompt_profile_id` —— F04 三个标识里的第三个（口径 F39）。

**这些断言的重点不是「哈希函数会算」**，而是三个标识的**分工**：
A 类阈值动了只该动指纹，B 类动了只该动 profile。分工写在 F04 里，
但写着的分工和被测量过的分工是两回事（F14 的同一条）。

## 本文件**不需要 pytest 也能跑**

    python3 explain/tests/test_prompt_profile.py     # 零依赖，stdlib
    ./.venv/bin/python -m pytest tests/test_prompt_profile.py   # 有 venv 时

原因不是图省事：`explain/` 的 venv 是本地产物（`.gitignore` 挡在仓库外），
**没有 venv 的机器上 `import pytest` 直接失败**。那样这 14 条会以「有测试」的样子
存在而从不执行——与 F14 说的「承诺存在 ≠ 承诺被测量」是同一件事，只是换成了
「测试存在 ≠ 测试被执行」。

所以下面的用例**刻意不用 fixture**（用了就只能靠 pytest 跑），
文件末尾带一个 stdlib 的小跑法，并由 `web/scripts/verify_prompt_profile.mjs`
接进 `npm run verify:guards` —— 那条链是这个仓库里**真的会被跑**的那条。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 直接 `python3 explain/tests/test_prompt_profile.py` 跑时，sys.path 里没有 explain 包。
# pytest 下这一段是空操作（rootdir 已在 path 里），所以两种跑法共用同一份文件。
_EXPLAIN = Path(__file__).resolve().parent.parent
if str(_EXPLAIN) not in sys.path:
    sys.path.insert(0, str(_EXPLAIN))

import explain  # noqa: E402,F401  — 必须先于 a_rules，它注入 a_rules 的路径

from a_rules.config import StyleConfig
from explain.prompt_profile import (
    B_CLASS_AXES,
    PROFILE_ALGO_VERSION,
    profile_payload,
    prompt_profile_id,
)

B0 = {"use_examples": "more", "structure": "conclusion_first", "depth": "why"}
V0 = "b1"


class TestShape:
    def test_twelve_hex_chars(self):
        """与 fingerprint() / metric_set_id() 同形——三个 id 会并排出现在同一行。"""
        pid = prompt_profile_id(B0, V0)
        assert re.fullmatch(r"[0-9a-f]{12}", pid), pid

    def test_same_shape_as_the_other_two(self):
        cfg = StyleConfig()
        pid = prompt_profile_id(B0, V0)
        assert len(pid) == len(cfg.fingerprint()) == len(cfg.metric_set_id())

    def test_deterministic(self):
        assert prompt_profile_id(B0, V0) == prompt_profile_id(dict(B0), V0)

    def test_key_order_does_not_matter(self):
        reordered = {"depth": "why", "structure": "conclusion_first", "use_examples": "more"}
        assert prompt_profile_id(reordered, V0) == prompt_profile_id(B0, V0)

    def test_algo_version_is_in_the_payload(self):
        """算法版本必须进哈希，否则改算法会让新旧 id 在同一个命名空间里静默相撞。"""
        assert profile_payload(B0, V0)["algo_version"] == PROFILE_ALGO_VERSION


class TestSensitivity:
    """**每一条轴都要被断言碰到**（通则七 #7）。

    只测一条轴的话，另外两条从没进过哈希也全绿——而那正是最坏的结果：
    id 看起来在工作，实际上对 2/3 的输入不敏感。
    """

    def test_every_b_class_axis_changes_the_id(self):
        base = prompt_profile_id(B0, V0)
        alternatives = {
            "use_examples": "fewer",
            "structure": "buildup_first",
            "depth": "derivation",
        }
        for axis in B_CLASS_AXES:
            changed = {**B0, axis: alternatives[axis]}
            assert prompt_profile_id(changed, V0) != base, f"{axis} 变了但 id 没变"

    def test_template_version_changes_the_id(self):
        """措辞版本变 = prompt 变了。它不进哈希的话，改措辞在数据里完全看不出来。"""
        assert prompt_profile_id(B0, "b2") != prompt_profile_id(B0, "b1")

    def test_all_combinations_are_distinct(self):
        """2 × 2 × 3 = 12 种 B 类组合必须两两不同。

        反向控制的角色：上面三条只说明「变了会变」，这条说明**没有碰撞**——
        少了它，一个把 depth 折叠掉的实现也能让上面全绿。
        """
        ids = {
            prompt_profile_id(
                {"use_examples": u, "structure": s, "depth": d}, V0
            )
            for u in ("more", "fewer")
            for s in ("conclusion_first", "buildup_first")
            for d in ("what", "why", "derivation")
        }
        assert len(ids) == 12, f"12 种组合只算出 {len(ids)} 个 id"


class TestDivisionOfLabour:
    """口径 F04：三个标识各管一件事。**这是本文件真正要钉住的东西。**"""

    def test_a_class_threshold_change_moves_fingerprint_not_profile(self):
        loose = StyleConfig()
        tight = StyleConfig.from_dict({"paragraph_length": {"min": 15, "max": 40}})
        assert tight.fingerprint() != loose.fingerprint(), "前提不成立：改阈值该动指纹"
        # 同一份 B 类 → profile 不动。A 类阈值不该泄漏进 prompt 侧标识。
        assert prompt_profile_id(B0, V0) == prompt_profile_id(B0, V0)

    def test_b_class_change_does_not_move_fingerprint_or_metric_set(self):
        """**F04 的核心**：B 类不进 config_fingerprint / metric_set_id。

        进了会出事：两个 A 类判定完全相同的用户会有不同指纹，
        `verify` 里「点内指纹唯一」那类守卫在真实用户数据上直接噪声化。

        StyleConfig 里根本没有 B 类字段，所以这条测的是「那个事实仍然成立」——
        哪天有人给 StyleConfig 加了 use_examples，这条会红。
        """
        cfg = StyleConfig()
        assert not (set(B_CLASS_AXES) & set(cfg.to_dict())), (
            f"StyleConfig 里出现了 B 类字段：{set(B_CLASS_AXES) & set(cfg.to_dict())}"
        )
        before = (cfg.fingerprint(), cfg.metric_set_id())
        _ = prompt_profile_id({**B0, "depth": "derivation"}, V0)
        assert (cfg.fingerprint(), cfg.metric_set_id()) == before


class TestRefusesToGuess:
    """缺字段**抛异常**，不静默补默认值（同 F32「回落必须显式」）。"""

    def test_missing_axis_raises(self):
        for axis in B_CLASS_AXES:
            partial = {k: v for k, v in B0.items() if k != axis}
            try:
                prompt_profile_id(partial, V0)
            except ValueError as exc:
                assert axis in str(exc), f"异常没点名缺的是 {axis}：{exc}"
            else:  # pragma: no cover
                raise AssertionError(f"缺 {axis} 时没有抛异常——它被补成默认值了")

    def test_empty_axis_value_raises(self):
        try:
            prompt_profile_id({**B0, "depth": ""}, V0)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("空取值没有抛异常")

    def test_empty_template_version_raises(self):
        try:
            prompt_profile_id(B0, "")
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("空 template_version 没有抛异常")

    def test_allow_control_complete_input_works(self):
        """反向控制：完整输入必须算得出来。

        少了它，上面三条在「任何输入都抛异常」的实现下同样全绿。
        """
        assert len(prompt_profile_id(B0, V0)) == 12


# ---------------------------------------------------------------- stdlib 跑法

def _main() -> int:
    """没有 pytest 时的跑法。**逐条执行、逐条报，红了退出码非 0。**

    刻意不捕获成「一条汇总」：哪一条红要能一眼看出来，否则修的时候得先自己二分。
    `web/scripts/verify_prompt_profile.mjs` 解析下面那行合计，并断言条数 ≥ 预期——
    一个「什么都没跑但退出 0」的跑法与真跑一遍在退出码上长得一样。
    """
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
