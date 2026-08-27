"""日志字段。PRD §9.1：「**从第一天起**将每次调用的 token 数与成本写入日志。」"""

from __future__ import annotations

import json
from datetime import date

import explain  # noqa: F401
from explain.fake import FakeExplainModel
from explain.log import pricing_record, round_record
from explain.pipeline import Trajectory, run_round
from explain.review import RuleReviewer
from explain.style_prompt import render_style_rules


def _record(config, retriever, **kw):
    r = run_round(
        round_index=1,
        question="热机效率是什么？",
        trajectory=Trajectory(),
        retriever=retriever,
        model=FakeExplainModel(),
        reviewer=RuleReviewer(config),
        style_rules=render_style_rules(config),
        course_label="22.081J",
        review_enabled=True,
    )
    return round_record(
        r, config=config, config_point_id="P0", condition="with_review",
        replicate=1, trace_id="t-1", **kw
    )


class TestRoundRecord:
    def test_is_json_serialisable(self, config, retriever):
        json.dumps(_record(config, retriever), ensure_ascii=False)

    def test_four_token_kinds_recorded_separately(self, config, retriever):
        """三类输入互不重叠，合并之后就再也算不回成本结构。

        第 0 周正是靠这个分解才发现「输出占 68%，检索只作用于输入侧」——
        那条结论直接改写了成本优化的优先级。
        """
        usage = _record(config, retriever)["round_usage"]
        assert set(usage) == {
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        }

    def test_both_config_identifiers_present(self, config, retriever):
        # 指纹管「阈值变没变」，metric_set_id 管「指标集是不是同一个」
        rec = _record(config, retriever)
        assert rec["config_fingerprint"] == config.fingerprint()
        assert rec["metric_set_id"] == config.metric_set_id()
        assert rec["config_version"] == "2.0"

    def test_first_token_latency_is_null_not_faked(self, config, retriever):
        """不流式就测不了首字延迟。**写 null，不塞一个假数。**

        塞一个数进去，PRD §9.2 的「首字 < 3s」就会有一个看起来有依据、
        实际没测过的数字——那正是假断言的形态（通则七 #1）。
        """
        assert _record(config, retriever)["first_token_latency_ms"] is None

    def test_gap_fields_present_but_null(self, config, retriever):
        rec = _record(config, retriever)
        assert rec["gap_verdict"] is None and rec["gap_implementation"] == "null"

    def test_observations_are_separated_from_metrics(self, config, retriever):
        """观察项不进 `final_evaluation`——混进去会让「风格遵循度」悄悄变成别的数。"""
        rec = _record(config, retriever)
        assert "total_chars" in rec["observations"]
        assert "total_chars" not in rec["final_evaluation"]["dimensions"]

    def test_retrieval_hit_gold_computed_when_gold_given(self, config, retriever):
        gold = [{"source_file": "MIT22_081JF10_lec01b.pdf", "page_index": 20}]
        assert _record(config, retriever, gold_pages=gold)["observations"]["retrieval_hit_gold"] is True
        miss = [{"source_file": "MIT22_081JF10_lec01b.pdf", "page_index": 99}]
        assert _record(config, retriever, gold_pages=miss)["observations"]["retrieval_hit_gold"] is False

    def test_hit_gold_is_null_when_no_gold(self, config, retriever):
        # 「没有金标准」与「没命中」不是一回事
        assert _record(config, retriever)["observations"]["retrieval_hit_gold"] is None


class TestPricingRecord:
    def test_carries_rates_and_expiry(self):
        rec = pricing_record("claude-sonnet-5", date(2026, 8, 20))
        assert rec["pricing_rates_per_mtok"] == {"input": 2.0, "output": 10.0}
        assert rec["pricing_intro_until"] == "2026-08-31"

    def test_after_expiry_rates_change_but_expiry_still_recorded(self):
        """到期后价目回落，**到期日仍要记**。

        只写一个数字的话，事后无法判断它按哪套价算的——
        而 §9.1 承诺的正是「能报出具体数字与结构」。
        """
        rec = pricing_record("claude-sonnet-5", date(2026, 9, 1))
        assert rec["pricing_rates_per_mtok"] == {"input": 3.0, "output": 15.0}
        assert rec["pricing_intro_until"] == "2026-08-31"
