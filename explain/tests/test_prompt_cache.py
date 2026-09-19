"""prompt caching 的可行性检查。

**「未生效」与「未命中」是两件事。** 接检索后 system 只剩风格约束与课件清单，
可能低于最小可缓存长度——此时 `cache_control` 静默无效、不报错。
这是「会静默改变数字的问题」（通则六），必须显式报出来。
"""

from __future__ import annotations

import explain  # noqa: F401
from explain.prompt import (
    MIN_CACHEABLE_TOKENS,
    build_system,
    estimate_system_tokens,
    system_cache_status,
)
from explain.style_prompt import AXES, TONES, render_all_style_rules, render_style_rules

#: 生产发的是 A 类 + B 类 + 语气（`api/api/main.py::render_all_style_rules`）。
#: 这里必须用同一个渲染函数：旧测试量的是只有 A 类的那个形状，**生产一次都没发过**，
#: 于是它报的 token 数不是任何一次真实请求的 token 数（判据与生产同源）。
B_CLASS = {axis: AXES[axis][0] for axis in ("use_examples", "structure", "depth")}


class TestSystemCacheability:
    def test_the_production_system_clears_the_cache_floor(self, config, retriever):
        """接检索后 system **越过**了最小可缓存长度。**这条断言 2026-09-19 翻过来了。**

        原来断的是反面：「接检索后 system 撑不到可缓存长度」。它当时是对的，
        但量的是只有 A 类规则的形状（约 1,039 token）——而生产发的是 A+B+语气。
        「怎么说话」那一节（s2/s3 去 AI 味）加进 system 之后，生产形状落到
        1,079–1,159：**36 种 B 类 × 语气组合没有一种低于 1,024**。
        论断翻了，断言就得跟着翻，否则 PRD §7.3 那句「缓存不生效」会一直留在报告里
        而没有任何东西在核对它。

        ⚠️ **余量只有 ~55 token，而这个估算器自己是粗口径**（见 `estimate_system_tokens`：
        只用来判量级，真实值以 API 的 usage 为准）。所以这条说的是「按估算已过线」，
        不是「缓存必然命中」。**删掉 system 里任何一节之前先回来看这条。**
        """
        system = build_system(
            render_all_style_rules(config, B_CLASS), retriever.fetch("q"), "22.081J")
        status = system_cache_status(system)
        assert status["system_tokens_estimated"] >= MIN_CACHEABLE_TOKENS
        assert status["system_cache_expected_effective"] is True

    def test_every_style_combination_clears_the_floor(self, config, retriever):
        """36 种组合**逐个**过线，不是「拿一种代表全部」。

        风格是用户选的，不同组合的 system 长度不同（B 类三条 + 语气一行）。
        只量一种等于赌其余 35 种也过线；余量只有几十 token 时这个赌不该打。
        """
        ctx = retriever.fetch("q")
        sizes = [
            system_cache_status(build_system(
                render_all_style_rules(
                    config,
                    {"use_examples": e, "structure": st, "depth": d},
                    tone=tone,
                ), ctx, "22.081J"))["system_tokens_estimated"]
            for e in AXES["use_examples"]
            for st in AXES["structure"]
            for d in AXES["depth"]
            for tone in TONES
        ]
        assert len(sizes) == 36
        assert min(sizes) >= MIN_CACHEABLE_TOKENS, f"最小 {min(sizes)}"

    def test_a_short_system_still_reports_not_effective(self):
        """**反向验证**：主断言翻成 True 之后，这个判定有了恒为 True 的可能。

        一个永远给同一个答案的检查不是检查。注意**不能拿「只有 A 类规则」的形状
        当反向控制**——它现在是 1,039，也在线上了（翻转前它是低于下限的那一个，
        2026-09-19 实测它跟着「怎么说话」那一节一起过了线）。所以用一段真的短的。
        """
        status = system_cache_status("只讲重点。")
        assert status["system_tokens_estimated"] < MIN_CACHEABLE_TOKENS
        assert status["system_cache_expected_effective"] is False
        assert "不生效" in status["note"]

    def test_a_full_courseware_system_would_be_cacheable(self):
        """**反向验证**：把课件全文塞回 system，判定必须翻转。

        不翻转说明这条检查恒为 False——一个永远给同一个答案的检查
        不是检查（通则七 #5：它在什么情况下会触发？）。
        """
        big = "热机的效率受卡诺极限约束。" * 400
        assert system_cache_status(big)["system_cache_expected_effective"] is True

    def test_estimate_counts_cjk_as_one_token(self):
        # 口径粗糙但确定：只用来判断量级，真实值由 API 的 usage 给出
        assert estimate_system_tokens("热机效率") == 4
        assert estimate_system_tokens("abcdefgh") == 2

    def test_system_is_deterministic(self, config, retriever):
        """system 必须逐字节稳定，否则缓存每轮失效。

        非确定性的常见来源是集合遍历顺序与时间戳——两者都不会报错，
        只会让缓存命中率悄悄归零。
        """
        ctx = retriever.fetch("q")
        rules = render_style_rules(config)
        assert build_system(rules, ctx, "22.081J") == build_system(rules, ctx, "22.081J")


class TestStyleRulesParameterisation:
    def test_rules_track_the_config(self):
        from a_rules.config import StyleConfig

        loose = StyleConfig.from_dict({"explain_language": "zh", "source_language": "en"})
        tight = StyleConfig.from_dict(
            {"explain_language": "zh", "source_language": "en",
             "paragraph_length": {"min": 15, "max": 40}}
        )
        assert "30–120 字" in render_style_rules(loose)
        assert "15–40 字" in render_style_rules(tight)

    def test_language_rule_is_banded_not_a_raw_threshold(self):
        """阈值不直接报给模型——它不会去数自己的汉字占比。

        但档位必须**随配置变**，否则 P4 那个点在 prompt 侧根本没有偏离，
        测出来的会是「判定变严」这个平凡结论（口径 X05）。
        """
        from a_rules.config import StyleConfig

        base = {"explain_language": "zh", "source_language": "en"}
        low = render_style_rules(StyleConfig.from_dict({**base, "chinese_ratio": {"min": 0.85}}))
        high = render_style_rules(StyleConfig.from_dict({**base, "chinese_ratio": {"min": 0.95}}))
        assert low != high
        assert "0.95" not in high and "0.85" not in low

    def test_term_rule_absent_in_v1(self):
        from a_rules.config import StyleConfig

        cfg = StyleConfig.from_dict({"explain_language": "zh", "source_language": "en"})
        assert "English original" not in render_style_rules(cfg)

    def test_term_rule_returns_if_dimension_is_re_enabled(self):
        # 重启术语维度时 prompt 侧自动跟上，不需要有人记得改这里
        from a_rules.config import StyleConfig

        cfg = StyleConfig.from_dict(
            {"explain_language": "zh", "source_language": "en", "term_annotation_enabled": True}
        )
        assert "English original" in render_style_rules(cfg)

    def test_monolingual_drops_the_language_rule(self):
        from a_rules.config import StyleConfig

        cfg = StyleConfig.from_dict({"explain_language": "zh", "source_language": "zh"})
        assert "讲解语言" not in render_style_rules(cfg)
