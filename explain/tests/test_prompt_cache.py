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
from explain.style_prompt import render_style_rules


class TestSystemCacheability:
    def test_retrieval_era_system_is_below_the_cache_floor(self, config, retriever):
        """**这是本轮最重要的一条实测**：接检索后 system 撑不到可缓存长度。

        第 0 周 system 里有 5K token 的课件全文，断点当然生效；
        现在课件全文根本不在上下文里。若报告照抄「已启用 prompt caching」，
        那句话是错的——PRD §7.3 的论断前提已经不成立。
        """
        system = build_system(render_style_rules(config), retriever.fetch("q"), "22.081J")
        status = system_cache_status(system)
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
