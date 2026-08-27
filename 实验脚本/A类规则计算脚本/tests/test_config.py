"""config.py 的单元测试。

重点不在「合法配置能不能加载」，而在「非法配置会不会在加载时炸掉」——
配置错误若能静默通过，§8.2 的 20 轮曲线跑完才发现，代价是整轮实验作废。
"""

import json

import pytest

from a_rules.config import (
    CONFIG_VERSION,
    ConfigError,
    LengthRange,
    StyleConfig,
    MinRatio,
    TermCountMode,
    TermPolicy,
)


class TestDefaults:
    def test_defaults_match_prd(self):
        c = StyleConfig()
        assert c.explain_language == "zh"
        assert c.source_language == "en"
        assert c.chinese_ratio.min == 0.85  # 单侧下限，见 D41
        assert c.term_policy is TermPolicy.TRANSLATE_WITH_SOURCE
        assert c.term_annotation_threshold == 0.9  # PRD §8.1「≥ 90%」
        assert c.term_count_mode is TermCountMode.FIRST  # 口径 D02
        assert c.max_quote_chars == 100  # PRD §12.1
        assert c.exclude_annotated_terms is True  # 口径 D15

    def test_empty_dict_and_none_give_defaults(self):
        assert StyleConfig.from_dict({}) == StyleConfig()
        assert StyleConfig.from_dict(None) == StyleConfig()

    def test_paragraph_defaults_recalibrated(self):
        # 30–120 来自六轮真实讲解实测（均值 41–75），非拍脑袋
        assert StyleConfig().paragraph_length.min == 30
        assert StyleConfig().paragraph_length.max == 120

    def test_partial_config_keeps_other_defaults(self):
        c = StyleConfig.from_dict({"max_quote_chars": 50})
        assert c.max_quote_chars == 50
        assert c.chinese_ratio.min == 0.85


class TestLanguageNormalization:
    @pytest.mark.parametrize("raw", ["zh", "ZH", "zh-CN", "zh_Hans", "  zh-TW  ", "Zh-Hant"])
    def test_variants_collapse_to_primary_subtag(self, raw):
        assert StyleConfig.from_dict({"explain_language": raw}).explain_language == "zh"

    def test_region_difference_does_not_falsely_activate(self):
        # zh-CN 讲解 + zh 课件 是单语场景；不归一会被误判为跨语言并激活维度
        c = StyleConfig.from_dict({"explain_language": "zh-CN", "source_language": "zh"})
        assert c.is_bilingual is False
        assert c.activates_term_policy is False

    @pytest.mark.parametrize("bad", ["", "   ", None, 123, [], {}])
    def test_invalid_language_rejected(self, bad):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"explain_language": bad})


class TestActivation:
    def test_bilingual_zh_activates_three_by_default(self):
        """**v1 默认三维**（CONFIG_VERSION 2.0 / PRD §3.2）。

        跨语言只是术语维度的必要条件，不是充分条件——还要显式开启。
        """
        c = StyleConfig.from_dict({"explain_language": "zh", "source_language": "en"})
        assert c.describe_activation() == {
            "chinese_ratio": True,
            "term_annotation": False,
            "paragraph_length": True,
            "quote_length": True,
        }

    def test_bilingual_zh_activates_all_four_when_term_enabled(self):
        c = StyleConfig.from_dict(
            {"explain_language": "zh", "source_language": "en", "term_annotation_enabled": True}
        )
        assert c.describe_activation() == {
            "chinese_ratio": True,
            "term_annotation": True,
            "paragraph_length": True,
            "quote_length": True,
        }

    def test_monolingual_deactivates_cross_language_dimensions(self):
        # PRD §8.1：单语场景下不激活，否则单语用户的得分会被虚高拉起
        c = StyleConfig.from_dict({"explain_language": "en", "source_language": "en"})
        assert c.activates_chinese_ratio is False
        assert c.activates_term_policy is False

    def test_resident_dimensions_always_active(self):
        for pair in [("zh", "en"), ("en", "en"), ("ja", "de")]:
            c = StyleConfig.from_dict({"explain_language": pair[0], "source_language": pair[1]})
            assert c.activates_paragraph_length is True
            assert c.activates_quote_length is True

    def test_non_chinese_explain_language_deactivates_ratio(self):
        # 本模块度量的是汉字占比；讲解语言不是中文时这个数字没有意义
        c = StyleConfig.from_dict(
            {"explain_language": "ja", "source_language": "en", "term_annotation_enabled": True}
        )
        assert c.is_bilingual is True
        assert c.activates_chinese_ratio is False
        assert c.activates_term_policy is True


class TestMinRatio:
    """单侧下限：要检测的是「开始说英文」，不存在「中文太多」。"""

    @pytest.mark.parametrize("value, expected", [(0.85, True), (0.86, True), (1.0, True), (0.84, False), (0.0, False)])
    def test_lower_bound_only(self, value, expected):
        assert MinRatio(0.85).contains(value) is expected

    def test_no_upper_bound(self):
        # 上界会与 term_annotation 重复计量：括注英文本就压低中文占比
        assert MinRatio(0.85).contains(1.0) is True

    def test_float_boundary_compensated(self):
        assert MinRatio(0.1 + 0.2).contains(0.3) is True

    def test_percent_instead_of_fraction_is_rejected_with_hint(self):
        with pytest.raises(ConfigError, match="0.85"):
            MinRatio(85)

    @pytest.mark.parametrize("value", [-0.1, 1.1, 85])
    def test_out_of_range(self, value):
        with pytest.raises(ConfigError):
            MinRatio(value)

    def test_missing_min_rejected(self):
        with pytest.raises(ConfigError, match="缺少 min"):
            StyleConfig.from_dict({"chinese_ratio": {}})

    def test_old_band_schema_rejected(self):
        # v1.0 冻结时改的口径；旧写法必须报错而非静默按默认值跑
        with pytest.raises(ConfigError, match="未知字段"):
            StyleConfig.from_dict({"chinese_ratio": {"target": 0.85, "tolerance": 0.05}})

    def test_not_an_object(self):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"chinese_ratio": 0.85})


class TestLengthRange:
    def test_contains_is_closed_interval(self):
        r = LengthRange(60, 180)
        assert r.contains(60) and r.contains(180) and r.contains(120)
        assert not r.contains(59) and not r.contains(181)

    def test_max_below_min_rejected(self):
        with pytest.raises(ConfigError, match="max 必须"):
            LengthRange(180, 60)

    def test_min_below_one_rejected(self):
        with pytest.raises(ConfigError):
            LengthRange(0, 180)

    @pytest.mark.parametrize("bad", [{"min": 1.5, "max": 10}, {"min": True, "max": 10}, {"min": "60", "max": 180}])
    def test_non_integer_rejected(self, bad):
        with pytest.raises(ConfigError, match="必须是整数"):
            StyleConfig.from_dict({"paragraph_length": bad})

    def test_missing_key_rejected(self):
        with pytest.raises(ConfigError, match="缺少 max"):
            StyleConfig.from_dict({"paragraph_length": {"min": 60}})


class TestEnums:
    @pytest.mark.parametrize("value", ["keep_original", "translate_with_source", "translate_only"])
    def test_all_three_term_policies_accepted(self, value):
        # PRD §8.1：术语处理是三值参数，不是布尔项
        assert StyleConfig.from_dict({"term_policy": value}).term_policy.value == value

    def test_invalid_term_policy_lists_allowed_values(self):
        with pytest.raises(ConfigError, match="keep_original"):
            StyleConfig.from_dict({"term_policy": "translate"})

    @pytest.mark.parametrize("value", ["first", "all"])
    def test_count_modes(self, value):
        assert StyleConfig.from_dict({"term_count_mode": value}).term_count_mode.value == value

    def test_invalid_count_mode(self):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"term_count_mode": "every"})


class TestScalarValidation:
    @pytest.mark.parametrize("value", [-0.1, 1.5, 90])
    def test_threshold_out_of_range(self, value):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"term_annotation_threshold": value})

    def test_threshold_percent_hint(self):
        with pytest.raises(ConfigError, match="0.9"):
            StyleConfig.from_dict({"term_annotation_threshold": 90})

    @pytest.mark.parametrize("value", [0, -1])
    def test_max_quote_chars_must_be_positive(self, value):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"max_quote_chars": value})

    @pytest.mark.parametrize("value", [100.5, "100", True, None])
    def test_max_quote_chars_must_be_int(self, value):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"max_quote_chars": value})

    @pytest.mark.parametrize("value", ["yes", 1, None])
    def test_exclude_annotated_terms_must_be_bool(self, value):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"exclude_annotated_terms": value})


class TestStrictUnknownKeys:
    def test_typo_in_root_key_is_rejected(self):
        # 静默忽略拼错的键 = 实验用了默认值却以为用了自定义值
        with pytest.raises(ConfigError, match="max_quote_char"):
            StyleConfig.from_dict({"max_quote_char": 100})

    def test_error_lists_available_fields(self):
        with pytest.raises(ConfigError, match="term_policy"):
            StyleConfig.from_dict({"nonsense": 1})


class TestSerialization:
    def test_roundtrip(self):
        original = StyleConfig.from_dict(
            {
                "explain_language": "zh",
                "source_language": "en",
                "chinese_ratio": {"min": 0.9},
                "term_policy": "translate_only",
                "term_count_mode": "all",
                "term_annotation_threshold": 0.95,
                "paragraph_length": {"min": 80, "max": 200},
                "max_quote_chars": 80,
                "exclude_annotated_terms": False,
            }
        )
        assert StyleConfig.from_dict(original.to_dict()) == original

    def test_to_dict_is_json_serializable(self):
        json.dumps(StyleConfig().to_dict(), ensure_ascii=False)

    def test_from_json(self):
        assert StyleConfig.from_json('{"max_quote_chars": 42}').max_quote_chars == 42

    def test_malformed_json(self):
        with pytest.raises(ConfigError, match="不是合法 JSON"):
            StyleConfig.from_json("{not json}")

    def test_from_file(self, tmp_path):
        p = tmp_path / "style.json"
        p.write_text('{"term_policy": "keep_original"}', encoding="utf-8")
        assert StyleConfig.from_file(p).term_policy is TermPolicy.KEEP_ORIGINAL

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="不存在"):
            StyleConfig.from_file(tmp_path / "nope.json")

    def test_root_must_be_object(self):
        with pytest.raises(ConfigError):
            StyleConfig.from_json("[1, 2, 3]")


class TestFingerprint:
    def test_stable_across_instances(self):
        assert StyleConfig().fingerprint() == StyleConfig().fingerprint()

    def test_changes_when_any_field_changes(self):
        base = StyleConfig().fingerprint()
        for override in [
            {"max_quote_chars": 99},
            {"term_policy": "translate_only"},
            {"chinese_ratio": {"min": 0.86}},
            {"paragraph_length": {"min": 31, "max": 120}},
            {"exclude_annotated_terms": False},
            {"term_count_mode": "all"},
        ]:
            assert StyleConfig.from_dict(override).fingerprint() != base, override

    def test_insensitive_to_key_order(self):
        a = StyleConfig.from_dict({"max_quote_chars": 50, "term_policy": "translate_only"})
        b = StyleConfig.from_dict({"term_policy": "translate_only", "max_quote_chars": 50})
        assert a.fingerprint() == b.fingerprint()

    def test_language_variants_share_fingerprint(self):
        a = StyleConfig.from_dict({"explain_language": "zh-CN"})
        b = StyleConfig.from_dict({"explain_language": "zh"})
        assert a.fingerprint() == b.fingerprint()


class TestImmutability:
    def test_config_is_frozen(self):
        with pytest.raises(Exception):
            StyleConfig().max_quote_chars = 1  # type: ignore[misc]


class TestConfigVersion:
    def test_roundtrip_tolerates_version_stamp(self):
        # 它是输出戳记不是设置项，接受但忽略——D19 严格校验的唯一豁免
        assert StyleConfig.from_dict(StyleConfig().to_dict()) == StyleConfig()

    def test_defaults_version_in_output(self):
        # 版本号是给人看的标签；「两轮用了同一份配置」由 fingerprint 保证
        assert StyleConfig().to_dict()["defaults_version"] == CONFIG_VERSION

    def test_version_does_not_replace_fingerprint(self):
        a, b = StyleConfig(), StyleConfig.from_dict({"max_quote_chars": 50})
        assert a.to_dict()["defaults_version"] == b.to_dict()["defaults_version"]
        assert a.fingerprint() != b.fingerprint()


class TestMetricSetId:
    """口径 X10 · 指标集标识。

    与指纹分工：**指纹管「阈值变没变」，metric_set_id 管「指标集是不是同一个」**。
    配置扫描实验里指纹按设计逐点不同，单靠指纹的守卫会被若干个合法指纹撑破。
    """

    BILINGUAL = {"explain_language": "zh", "source_language": "en"}

    def test_v1_three_dimensions(self):
        c = StyleConfig.from_dict(self.BILINGUAL)
        assert [n for n, on in c.describe_activation().items() if on] == [
            "chinese_ratio",
            "paragraph_length",
            "quote_length",
        ]

    def test_threshold_changes_fingerprint_but_not_metric_set(self):
        """配置扫描的核心性质：换阈值 ≠ 换指标集。

        没有这条性质，扫描实验的每个配置点都会被当成不同的指标集，
        跨点比较无从谈起。
        """
        a = StyleConfig.from_dict(self.BILINGUAL)
        b = StyleConfig.from_dict({**self.BILINGUAL, "paragraph_length": {"min": 15, "max": 40}})
        assert a.fingerprint() != b.fingerprint()
        assert a.metric_set_id() == b.metric_set_id()

    def test_enabling_term_dimension_changes_metric_set(self):
        """**反向验证**（通则七 #4）：把术语维度开回来，守卫必须变红。

        不变红就说明这个标识挡不住「四维口径的 D02/D44 历史批次
        被画进 v1 三维的图里」——而那正是它唯一的职责。
        """
        three = StyleConfig.from_dict(self.BILINGUAL)
        four = StyleConfig.from_dict({**self.BILINGUAL, "term_annotation_enabled": True})
        assert three.metric_set_id() != four.metric_set_id()

    def test_monolingual_is_a_different_metric_set(self):
        # 单语场景下 chinese_ratio 不激活，只剩两维——它与三维批次同样不可合并
        mono = StyleConfig.from_dict({"explain_language": "zh", "source_language": "zh"})
        assert mono.metric_set_id() != StyleConfig.from_dict(self.BILINGUAL).metric_set_id()

    def test_version_participates(self):
        # 指标集相同但 CONFIG_VERSION 不同的两批数据仍不可合并
        import a_rules.config as cfgmod

        c = StyleConfig.from_dict(self.BILINGUAL)
        before = c.metric_set_id()
        original = cfgmod.CONFIG_VERSION
        try:
            cfgmod.CONFIG_VERSION = "9.9"
            assert c.metric_set_id() != before
        finally:
            cfgmod.CONFIG_VERSION = original


class TestTermDimensionSwitch:
    def test_default_is_off(self):
        # PRD §3.2：v1 的风格遵循度按维度枚举，不含术语处理
        assert StyleConfig().term_annotation_enabled is False

    def test_bilingual_alone_is_not_enough(self):
        c = StyleConfig.from_dict({"explain_language": "zh", "source_language": "en"})
        assert c.is_bilingual is True and c.activates_term_policy is False

    def test_enabled_but_monolingual_still_off(self):
        # 跨语言仍是必要条件（PRD §8.1：单语场景下该参数 N/A）
        c = StyleConfig.from_dict(
            {"explain_language": "en", "source_language": "en", "term_annotation_enabled": True}
        )
        assert c.activates_term_policy is False

    @pytest.mark.parametrize("bad", ["yes", 1, None, []])
    def test_non_boolean_rejected(self, bad):
        with pytest.raises(ConfigError):
            StyleConfig.from_dict({"term_annotation_enabled": bad})

    def test_round_trips_through_to_dict(self):
        c = StyleConfig.from_dict({"term_annotation_enabled": True})
        assert StyleConfig.from_dict(c.to_dict()).term_annotation_enabled is True
