"""term_annotation 的单元测试。

这个维度的误判方向可以是反的——括注识别一旦出错，每一次**正确**标注都会被
判成违规。所以配对逻辑的覆盖要密于其他部分。
"""

import json
from pathlib import Path

import pytest

from a_rules.config import StyleConfig
from a_rules.glossary import Glossary
from a_rules.metrics.term_annotation import Kind, term_annotation_rate
from a_rules.result import Reason

GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "examples" / "glossary_22081J.json"


@pytest.fixture(scope="module")
def real():
    return Glossary.from_file(GLOSSARY_PATH)


def gl(*terms: dict) -> Glossary:
    return Glossary.from_list(list(terms))


def cfg(**kw) -> StyleConfig:
    """默认关掉最小分母护栏——本文件多数用例只有 1–2 个术语，
    测的是判定逻辑而非护栏。护栏本身由 TestMinOccurrenceGuard 专测。

    **默认打开 term_annotation_enabled**：v1 生产默认已把本维度下线
    （CONFIG_VERSION 2.0），但本文件测的是这个维度**自身的判定逻辑**，
    不是它在 v1 里激活与否。激活语义由 test_config.py 专测。
    """
    kw.setdefault("term_min_occurrences", 1)
    kw.setdefault("term_annotation_enabled", True)
    return StyleConfig.from_dict(kw)


HEAT = {"en": "heat engine", "zh": ["热机"]}
POWER = {"en": "power density", "zh": ["功率密度"]}


# ==========================================================================
# 1. 激活与无法计算
# ==========================================================================


class TestActivation:
    def test_monolingual_not_activated(self):
        r = term_annotation_rate("热机", cfg(explain_language="zh", source_language="zh"), gl(HEAT))
        assert r.activated is False
        assert r.reason == Reason.NOT_ACTIVATED
        assert r.counts_toward_score is False

    @pytest.mark.parametrize("glossary", [None, Glossary()])
    def test_empty_glossary_uncomputable(self, glossary):
        # PRD §8.1：术语表缺失只降低灵敏度，不造成误判
        r = term_annotation_rate("热机（heat engine）", cfg(), glossary)
        assert r.activated is True and r.raw is None
        assert r.reason == Reason.EMPTY_GLOSSARY

    @pytest.mark.parametrize("text", ["", "   ", "\n\n", "```\nheat engine\n```"])
    def test_blank_text_uncomputable(self, text):
        r = term_annotation_rate(text, cfg(), gl(HEAT))
        assert r.raw is None and r.reason == Reason.EMPTY_TEXT

    def test_no_term_hit_is_not_zero(self):
        # 0/0 既不是 0% 也不是 100%——必须标为无效，否则一段不含术语的
        # 讲解会被当成「标注率 0」把曲线拽下去
        r = term_annotation_rate("这段讲解完全没有提到任何术语。", cfg(), gl(HEAT))
        assert r.raw is None and r.passed is None
        assert r.reason == Reason.NO_TERM_OCCURRENCE
        assert r.counts_toward_score is False
        assert r.details["coverage"]["total"]["hit"] == 0


# ==========================================================================
# 2. 括注配对
# ==========================================================================


class TestPairing:
    def test_zh_then_en(self):
        r = term_annotation_rate("热机（heat engine）把热能转成机械能。", cfg(), gl(HEAT))
        assert r.raw == 1.0 and r.details["kinds"]["paired"] == 1

    def test_en_then_zh(self):
        r = term_annotation_rate("heat engine（热机）是核心概念。", cfg(), gl(HEAT))
        assert r.raw == 1.0 and r.details["kinds"]["paired"] == 1

    def test_annotation_not_at_string_start(self):
        # 回归：`Pattern.match(s, pos)` 已在 pos 锚定，模式里再写 `^` 会让
        # 所有 pos>0 的调用落空——每次正确标注都被判成违规，方向与真相相反
        r = term_annotation_rate("前面有很长一段铺垫文字，然后才是热机（heat engine）。", cfg(), gl(HEAT))
        assert r.raw == 1.0

    def test_multiple_annotations_in_one_text(self):
        r = term_annotation_rate(
            "热机（heat engine）的功率密度（power density）较低。", cfg(), gl(HEAT, POWER)
        )
        assert r.raw == 1.0 and r.details["occurrences_first"] == 2

    @pytest.mark.parametrize("opening, closing", [("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"), ("〔", "〕")])
    def test_bracket_variants(self, opening, closing):
        r = term_annotation_rate(f"热机{opening}heat engine{closing}。", cfg(), gl(HEAT))
        assert r.raw == 1.0

    def test_space_before_bracket_allowed(self):
        assert term_annotation_rate("热机 (heat engine) 如上。", cfg(), gl(HEAT)).raw == 1.0

    def test_bracket_with_unrelated_content_is_not_annotation(self):
        r = term_annotation_rate("热机（很重要）需要理解。", cfg(), gl(HEAT))
        assert r.raw == 0.0 and r.details["kinds"]["lone_zh"] == 1

    def test_bracket_of_a_different_term_is_not_annotation(self):
        r = term_annotation_rate("热机（power density）写错了。", cfg(), gl(HEAT, POWER))
        assert r.details["kinds"]["paired"] == 0

    def test_distant_english_is_not_paired(self):
        # 用 all 口径断言：first 口径下同一术语只留首次出现，看不到第二条
        text = "热机是核心。后文才提到 heat engine 这个词。"
        r = term_annotation_rate(text, cfg(term_count_mode="all"), gl(HEAT))
        assert r.details["kinds"] == {"paired": 0, "lone_zh": 1, "lone_en": 1}
        assert r.raw == 0.0

    def test_english_inside_annotation_not_counted_twice(self):
        r = term_annotation_rate("热机（heat engine）如上。", cfg(), gl(HEAT))
        assert r.details["occurrences_all"] == 1

    def test_unclosed_bracket_is_not_annotation(self):
        assert term_annotation_rate("热机（heat engine 后面忘了闭合", cfg(), gl(HEAT)).raw == 0.0

    def test_case_and_spacing_variants_inside_bracket(self):
        for inner in ["Heat Engine", "HEAT  ENGINE", "heat-engine"]:
            assert term_annotation_rate(f"热机（{inner}）。", cfg(), gl(HEAT)).raw == 1.0

    def test_alias_inside_bracket_counts(self):
        g = gl({"en": "photovoltaic", "zh": ["光伏"], "aliases": ["solar voltaic", "PV"]})
        assert term_annotation_rate("光伏（solar voltaic）电池。", cfg(), g).raw == 1.0

    def test_unsafe_alias_inside_bracket_counts(self):
        # unsafe 的含义是「不可作自由文本锚点」，不是「不是合法形式」
        g = gl({"en": "uranium enrichment", "zh": ["铀浓缩"], "unsafe_aliases": ["enrichment"]})
        assert term_annotation_rate("铀浓缩（enrichment）需要离心机。", cfg(), g).raw == 1.0

    def test_secondary_zh_variant_inside_bracket_counts(self):
        g = gl({"en": "turbine", "zh": ["涡轮机", "透平"]})
        assert term_annotation_rate("turbine（透平）驱动发电机。", cfg(), g).raw == 1.0


class TestAnchorIndependence:
    """`match_anchor` 治的是自由文本的歧义，括注内没有歧义。"""

    ZH_ANCHORED = {"en": "block", "zh": ["区块"], "match_anchor": "zh"}

    def test_zh_anchored_term_can_still_be_paired(self):
        # 若括注核对也受 anchor 约束，anchor=zh 的术语将永远配不成对——
        # 每次正确标注都被判违规，是方向相反的系统性误判
        r = term_annotation_rate("区块（block）之间以哈希链接。", cfg(), gl(self.ZH_ANCHORED))
        assert r.raw == 1.0 and r.details["kinds"]["paired"] == 1

    def test_zh_anchored_term_ignores_ambiguous_english(self):
        r = term_annotation_rate("这是最基本的 building block 而已。", cfg(), gl(self.ZH_ANCHORED))
        assert r.reason == Reason.NO_TERM_OCCURRENCE

    def test_en_anchored_term_can_still_be_paired(self):
        g = gl({"en": "nonce", "zh": ["随机数"], "match_anchor": "en"})
        assert term_annotation_rate("nonce（随机数）用于工作量证明。", cfg(), g).raw == 1.0


# ==========================================================================
# 3. 三值策略
# ==========================================================================


class TestPolicies:
    CASES = {
        "paired": "热机（heat engine）如上。",
        "lone_zh": "热机如上。",
        "lone_en": "The heat engine works.",
    }

    @pytest.mark.parametrize(
        "policy, compliant_case",
        [
            ("translate_with_source", "paired"),
            ("translate_only", "lone_zh"),
            ("keep_original", "lone_en"),
        ],
    )
    def test_each_policy_accepts_exactly_one_form(self, policy, compliant_case):
        # 三者互斥是刻意的：paired 既翻译了（违反 keep_original）又带了原文
        # （违反 translate_only），否则「合规」的定义会随策略重叠
        for case, text in self.CASES.items():
            r = term_annotation_rate(text, cfg(term_policy=policy), gl(HEAT))
            assert r.raw == (1.0 if case == compliant_case else 0.0), (policy, case)

    def test_target_string_names_policy_and_mode(self):
        r = term_annotation_rate("热机", cfg(term_policy="translate_only"), gl(HEAT))
        assert "translate_only" in r.target and "first" in r.target


# ==========================================================================
# 4. 计数口径
# ==========================================================================


class TestCountMode:
    TEXT = "热机（heat engine）很重要。后面又提到热机，再提一次热机。"

    def test_first_mode_only_counts_first_occurrence(self):
        # 标注只需首现一次；全量计数会惩罚正常写作（口径 D02）
        r = term_annotation_rate(self.TEXT, cfg(term_count_mode="first"), gl(HEAT))
        assert r.raw == 1.0 and r.details["occurrences_first"] == 1

    def test_all_mode_counts_every_occurrence(self):
        r = term_annotation_rate(self.TEXT, cfg(term_count_mode="all"), gl(HEAT))
        assert r.details["occurrences_all"] == 3
        assert r.raw == pytest.approx(1 / 3)

    def test_both_rates_always_reported(self):
        # 两种口径并行输出，差值反映模型是否反复堆砌括注
        r = term_annotation_rate(self.TEXT, cfg(term_count_mode="first"), gl(HEAT))
        assert r.details["rate_first"] == 1.0
        assert r.details["rate_all"] == pytest.approx(1 / 3)

    def test_first_mode_keeps_first_not_best(self):
        # 首现未标注 → 不合规，即使后文补了标注也不救
        text = "热机很重要。后面才写热机（heat engine）。"
        assert term_annotation_rate(text, cfg(term_count_mode="first"), gl(HEAT)).raw == 0.0


# ==========================================================================
# 5. 覆盖统计与取证
# ==========================================================================


class TestCoverageAndEvidence:
    def test_coverage_split_by_source(self):
        g = gl(
            {"en": "heat engine", "zh": ["热机"], "source": "body_text"},
            {"en": "condenser", "zh": ["冷凝器"], "source": "figure_caption"},
            {"en": "pressurizer", "zh": ["稳压器"], "source": "figure_caption"},
        )
        cov = term_annotation_rate("热机（heat engine）如上。", cfg(), g).details["coverage"]
        assert cov["body_text"] == {"total": 1, "hit": 1, "rate": 1.0}
        assert cov["figure_caption"] == {"total": 2, "hit": 0, "rate": 0.0}
        assert cov["total"]["rate"] == pytest.approx(1 / 3)

    def test_violations_carry_evidence(self):
        r = term_annotation_rate("热机把热能转成机械能。", cfg(), gl(HEAT))
        v = r.details["violations"][0]
        assert v["term"] == "heat engine"
        assert v["preferred_zh"] == "热机"
        assert v["kind"] == "lone_zh"
        assert v["line"] == 1
        assert "热机" in v["preview"]

    def test_violation_line_number(self):
        r = term_annotation_rate("第一行。\n\n第二段提到热机。", cfg(), gl(HEAT))
        assert r.details["violations"][0]["line"] == 3

    def test_terms_in_code_block_excluded(self):
        r = term_annotation_rate(
            "热机（heat engine）如上。\n\n```python\n# 热机 heat engine\n```", cfg(), gl(HEAT)
        )
        assert r.details["occurrences_all"] == 1 and r.raw == 1.0

    def test_result_is_json_serializable(self):
        r = term_annotation_rate("热机（heat engine）。", cfg(), gl(HEAT))
        json.dumps(r.to_dict(), ensure_ascii=False)


class TestThreshold:
    @pytest.mark.parametrize("threshold, expected", [(0.5, True), (0.5000001, False)])
    def test_passed_at_boundary(self, threshold, expected):
        text = "热机（heat engine）与功率密度。"
        r = term_annotation_rate(text, cfg(term_annotation_threshold=threshold), gl(HEAT, POWER))
        assert r.raw == 0.5
        assert r.passed is expected

    def test_counts_toward_score_only_when_computed(self):
        computed = term_annotation_rate("热机（heat engine）。", cfg(), gl(HEAT))
        assert computed.counts_toward_score is True


# ==========================================================================
# 6. 真实术语表
# ==========================================================================


class TestRealGlossary:
    def test_fully_annotated_passage_scores_one(self, real):
        text = (
            "热机（heat engine）把燃料的化学能转成机械能，"
            "而它的功率密度（power density）远高于可再生能源。"
            "核裂变（nuclear fission）反应堆则用燃料棒（fuel rod）承载天然铀（natural uranium）。"
        )
        r = term_annotation_rate(text, cfg(), real)
        assert r.raw == 1.0 and r.passed is True

    def test_unannotated_passage_scores_zero(self, real):
        r = term_annotation_rate("热机的功率密度低于核裂变反应堆。", cfg(), real)
        assert r.raw == 0.0 and r.passed is False

    def test_figure_caption_terms_are_reachable(self, real):
        r = term_annotation_rate("冷凝器（condenser）把乏汽冷凝成水。", cfg(), real)
        assert r.details["coverage"]["figure_caption"]["hit"] == 1

    def test_steam_turbine_not_shadowed_by_turbine(self, real):
        r = term_annotation_rate("汽轮机（steam turbine）驱动发电机。", cfg(), real)
        assert r.raw == 1.0
        assert r.details["occurrences_all"] == 1


# ==========================================================================
# 7. 最小分母护栏
# ==========================================================================


class TestMinOccurrenceGuard:
    """分母太小时指标没有分辨率，却会把一个极端值贡献进漂移曲线。"""

    def test_below_threshold_is_low_confidence(self):
        # 实测第四轮只有 1 个首现术语，结果只能是 0% 或 100%
        r = term_annotation_rate("热机把热能转成机械能。", cfg(term_min_occurrences=5), gl(HEAT))
        assert r.raw is None and r.passed is None
        assert r.reason == Reason.LOW_CONFIDENCE
        assert r.counts_toward_score is False

    def test_details_still_reported_when_low_confidence(self):
        # 不出数值，但证据要留——否则无法判断为什么分母小
        r = term_annotation_rate("热机（heat engine）。", cfg(term_min_occurrences=5), gl(HEAT))
        assert r.details["occurrences_first"] == 1
        assert r.details["rate_first"] == 1.0

    def test_zero_occurrence_keeps_its_own_reason(self):
        r = term_annotation_rate("完全没有术语的一段话。", cfg(term_min_occurrences=5), gl(HEAT))
        assert r.reason == Reason.NO_TERM_OCCURRENCE

    def test_at_threshold_is_computed(self):
        g = Glossary.from_list([{"en": f"term{i}", "zh": [f"术语{i}"]} for i in range(5)])
        text = "、".join(f"术语{i}" for i in range(5))
        r = term_annotation_rate(text, cfg(term_min_occurrences=5), g)
        assert r.raw == 0.0 and r.reason is None

    def test_threshold_is_configurable_and_in_fingerprint(self):
        a = StyleConfig.from_dict({"term_min_occurrences": 5})
        b = StyleConfig.from_dict({"term_min_occurrences": 1})
        assert a.fingerprint() != b.fingerprint()


class TestAcceptedAnnotations:
    """括注侧的显式放行，与匹配侧的 match_anchor 分属两套机制。"""

    STEAM = {"en": "steam turbine", "zh": ["汽轮机"], "accepted_annotations": ["turbine"]}

    def test_accepted_annotation_makes_pair(self):
        # 实测：模型写「汽轮机(turbine)」，turbine 自身是另一条独立术语
        assert term_annotation_rate("蒸汽推动汽轮机（turbine）。", cfg(), gl(self.STEAM)).raw == 1.0

    def test_accepted_annotation_never_matches_free_text(self):
        # 只在括注内生效；自由文本里的 turbine 不属于 steam turbine
        r = term_annotation_rate("a turbine spins fast", cfg(), gl(self.STEAM))
        assert r.reason == Reason.NO_TERM_OCCURRENCE

    def test_no_generic_head_word_rule(self):
        # 逐条显式列举：未列举的中心词不通过，否则 descent 会通过梯度下降的检查
        g = gl({"en": "gradient descent", "zh": ["梯度下降"]})
        assert term_annotation_rate("梯度下降（descent）。", cfg(), g).raw == 0.0

    def test_real_glossary_steam_turbine_accepts_turbine(self, real):
        assert term_annotation_rate("汽轮机（turbine）驱动发电机。", cfg(), real).raw == 1.0


class TestGlossaryVersionRecorded:
    def test_version_in_details(self, real):
        # §8.2 期间术语表冻结，没有版本号就无法证明二十轮用的是同一份表
        r = term_annotation_rate("热机（heat engine）。", cfg(), real)
        assert r.details["glossary_version"] == "1.1"
        assert r.details["glossary"]["frozen"] is True


# ==========================================================================
# 8. 首现的作用域是整段对话（口径 D44）
# ==========================================================================


class TestConversationScope:
    """旧口径按单轮重置，等于要求同一术语每轮重复标注——那正是 PRD §2
    把「太啰嗦」列为痛点所要避免的行为。指标方向错了，不只是作用域窄了。"""

    def test_already_annotated_term_leaves_the_denominator(self):
        text = "热机把热能转成机械能。"
        assert term_annotation_rate(text, cfg(), gl(HEAT)).raw == 0.0
        r = term_annotation_rate(text, cfg(), gl(HEAT), annotated_terms=frozenset({"heat engine"}))
        assert r.raw is None and r.reason == Reason.NO_TERM_OCCURRENCE

    def test_unannotated_term_stays_in_the_denominator(self):
        # 传的是「已正确标注过」而非「已出现过」：若按后者排除，一个始终没被
        # 标注的术语会在几轮后永远逃出检查，义务凭空消失
        r = term_annotation_rate("热机很重要。", cfg(), gl(HEAT), annotated_terms=frozenset())
        assert r.raw == 0.0

    def test_new_term_still_counted_when_others_are_excluded(self):
        r = term_annotation_rate(
            "热机与功率密度。", cfg(), gl(HEAT, POWER),
            annotated_terms=frozenset({"heat engine"}),
        )
        assert r.details["occurrences_first"] == 1
        assert r.details["violations"][0]["term"] == "power density"

    def test_newly_annotated_terms_reported_for_accumulation(self):
        r = term_annotation_rate("热机（heat engine）与功率密度。", cfg(), gl(HEAT, POWER))
        assert r.details["newly_annotated_terms"] == ["heat engine"]

    def test_already_annotated_not_repeated_in_newly(self):
        r = term_annotation_rate(
            "热机（heat engine）。", cfg(), gl(HEAT),
            annotated_terms=frozenset({"heat engine"}),
        )
        assert r.details["newly_annotated_terms"] == []

    def test_legacy_caliber_reported_side_by_side(self):
        # 新旧口径并排输出，不覆盖——口径修正的过程本身要可追溯
        r = term_annotation_rate(
            "热机与功率密度（power density）。", cfg(), gl(HEAT, POWER),
            annotated_terms=frozenset({"heat engine"}),
        )
        assert r.raw == 1.0                                   # 新口径：热机已标注过，不再计
        assert r.details["rate_round_scoped_legacy"] == 0.5   # 旧口径：仍要求本轮重标
        assert r.details["prior_annotated_terms"] == 1

    def test_empty_prior_set_reproduces_legacy(self):
        text = "热机与功率密度（power density）。"
        r = term_annotation_rate(text, cfg(), gl(HEAT, POWER))
        assert r.raw == r.details["rate_round_scoped_legacy"]

    def test_all_mode_also_honors_scope(self):
        r = term_annotation_rate(
            "热机、热机、功率密度。", cfg(term_count_mode="all"), gl(HEAT, POWER),
            annotated_terms=frozenset({"heat engine"}),
        )
        assert r.details["occurrences_all"] == 1

    def test_evaluate_threads_the_scope(self):
        from a_rules.evaluator import evaluate
        ev = evaluate(
            "热机把热能转成机械能，这是火力发电的基础环节，值得展开说明一下。",
            cfg(), gl(HEAT), annotated_terms=frozenset({"heat engine"}),
        )
        assert ev.by_name("term_annotation").reason == Reason.NO_TERM_OCCURRENCE
        assert ev.by_name("chinese_ratio").raw is not None  # 其余维度不受影响
