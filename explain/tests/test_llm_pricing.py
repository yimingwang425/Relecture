"""定价与用量核算。**`drift` 的数值行为必须逐字节不变**（提升后的回归护栏）。

`explain/llm.py` 是从 `漂移实验/drift/models.py` 提升上来的。提升的理由是
定价表带 8/31 到期日——复制一份就等于两个到期日，一个按时回落、另一个不会，
**且两边都不报错**。本文件钉住的就是「提升没有改数」。
"""

from __future__ import annotations

from datetime import date

import pytest

import explain  # noqa: F401
from explain.llm import PRICING, Usage, cost_usd, rates_for


class TestIntroductoryPricing:
    def test_sonnet_intro_before_expiry(self):
        assert rates_for("claude-sonnet-5", date(2026, 8, 20)) == (2.00, 10.00)

    def test_sonnet_falls_back_after_expiry(self):
        # 到期日之后自动回落，不需要有人记得改表
        assert rates_for("claude-sonnet-5", date(2026, 9, 1)) == (3.00, 15.00)

    def test_expiry_day_is_inclusive(self):
        assert rates_for("claude-sonnet-5", date(2026, 8, 31)) == (2.00, 10.00)

    def test_every_model_with_intro_price_has_an_expiry(self):
        # 没有到期日的优惠价会一直用下去——那正是「报不准单位经济」的形态
        for name, entry in PRICING.items():
            if "intro_input" in entry:
                assert entry.get("intro_until"), name


class TestCostComposition:
    def test_three_input_kinds_do_not_overlap(self):
        """`input_tokens` 是**未命中缓存的余量**，与读/写不重叠。

        重叠的话成本会被系统性高估，而它看起来完全正常——
        第 0 周专门核对过这件事（42 次调用合计仅 84 个未命中 token）。
        """
        u = Usage(input_tokens=1000)
        only_miss = cost_usd("claude-sonnet-5", u, date(2026, 8, 20))
        assert only_miss == pytest.approx(1000 * 2.0 / 1_000_000)

    def test_cache_write_is_1_25x_and_read_is_0_1x(self):
        on = date(2026, 8, 20)
        write = cost_usd("claude-sonnet-5", Usage(cache_creation_input_tokens=1000), on)
        read = cost_usd("claude-sonnet-5", Usage(cache_read_input_tokens=1000), on)
        base = 1000 * 2.0 / 1_000_000
        assert write == pytest.approx(base * 1.25)
        assert read == pytest.approx(base * 0.10)

    def test_unknown_model_costs_zero_without_raising(self):
        # 成本缺失不该中断实验——但它必须是 0 而不是崩溃或瞎猜
        assert cost_usd("some-other-model", Usage(output_tokens=100)) == 0.0

    def test_fake_prefix_is_priced_like_the_real_model(self):
        """假跑也要走通成本核算链路。

        否则假跑时成本恒为 0，成本日志到真实调用才第一次被检验——
        而那时已经在花钱了。
        """
        on = date(2026, 8, 20)
        u = Usage(output_tokens=1000)
        assert cost_usd("fake:claude-sonnet-5", u, on) == cost_usd("claude-sonnet-5", u, on)


class TestDriftStillWorks:
    def test_drift_reexports_the_same_objects(self):
        """`drift/models.py` 退化为再导出，其定价对象必须是**同一个**。

        不是「值相等」而是「同一个对象」——值相等允许两份副本存在，
        而两份副本正是本次提升要消掉的东西。
        """
        import sys
        from pathlib import Path

        drift_dir = Path(__file__).resolve().parents[2] / "实验脚本" / "漂移实验"
        sys.path.insert(0, str(drift_dir))
        try:
            from drift.models import PRICING as drift_pricing
            from drift.models import cost_usd as drift_cost
        finally:
            sys.path.remove(str(drift_dir))
        assert drift_pricing is PRICING
        assert drift_cost is cost_usd


class TestModelProtocolSurface:
    """真实模型必须满足 `Model` 协议——**不调 API 也能查**。

    写这条是因为 `AnthropicModel.request_signature` 曾被一次批量改名弄成
    `requestsignature`，而**全部 59 条测试照样全绿**：它们走的都是假模型，
    真实模型的方法名从未被任何断言碰过。错误直到第一次真实调用才暴露——
    那时已经在花钱了。

    这正是通则七 #4 的反面用法：不是「把被测逻辑换成平凡实现看会不会红」，
    而是「**被测对象根本没进过测试**」。
    """

    def test_anthropic_model_has_the_protocol_methods(self):
        from explain.llm import AnthropicModel

        m = AnthropicModel()
        for name in ("complete", "request_signature", "name", "params"):
            assert hasattr(m, name), f"AnthropicModel 缺少 {name}"

    def test_request_signature_needs_no_network(self):
        """签名只取本地内容，不该碰网络——sanity check 依赖这一点。"""
        from explain.llm import AnthropicModel

        m = AnthropicModel()
        msgs = [{"role": "user", "content": "hi"}]
        assert m.request_signature("sys", msgs) == m.request_signature("sys", msgs)
        assert m.request_signature("sys", msgs) != m.request_signature("other", msgs)

    def test_fake_and_real_expose_the_same_surface(self):
        """假模型与真实模型的接口必须一致，否则假跑验证不了真实路径。"""
        from explain.fake import FakeExplainModel
        from explain.llm import AnthropicModel

        real, fake = dir(AnthropicModel()), dir(FakeExplainModel())
        for name in ("complete", "request_signature", "name", "params"):
            assert name in real and name in fake, name
