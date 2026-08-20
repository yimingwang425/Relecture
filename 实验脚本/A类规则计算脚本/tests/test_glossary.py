"""glossary.py 的单元测试。

匹配错误不会报错，只会让命中数变多或变少——直接改变 term_annotation_rate 的
数值且无声无息。所以这里的重点是**静默失配**：全角字符、换行断词、大小写、
连字符、重叠术语、掩码区域。
"""

import json

import pytest

from a_rules.glossary import (
    Glossary,
    GlossaryError,
    MatchAnchor,
    MatchSide,
    Term,
    TermSource,
)
from a_rules.text import Document


def g(*terms: dict) -> Glossary:
    return Glossary.from_list(list(terms))


def hits(glossary: Glossary, text: str) -> list[tuple[str, str]]:
    """(命中字面, 术语 en) 列表，便于断言。"""
    return [(m.text, m.term.en) for m in glossary.find(text)]


# ==========================================================================
# 1. 条目加载与校验
# ==========================================================================


class TestTermLoading:
    def test_full_entry(self):
        t = Term.from_dict(
            {
                "en": "power density",
                "zh": ["功率密度"],
                "aliases": ["areal power density"],
                "unsafe_aliases": ["density"],
                "match_anchor": "both",
                "source": "figure_caption",
                "page": 21,
                "note": "备注",
            },
            0,
        )
        assert t.en == "power density"
        assert t.preferred_zh == "功率密度"
        assert t.source is TermSource.FIGURE_CAPTION
        assert t.page == 21

    def test_plain_string_is_degraded_entry(self):
        # 降级输入：纯字符串列表。无中文译名，translate_only 无法判定
        t = Term.from_dict("gradient descent", 0)
        assert t.en == "gradient descent"
        assert t.has_zh is False
        assert t.preferred_zh is None

    def test_zh_first_item_is_preferred(self):
        # 判据 R3：匹配时全部接受，展示只取第一项
        t = Term.from_dict({"en": "turbine", "zh": ["涡轮机", "透平"]}, 0)
        assert t.preferred_zh == "涡轮机"
        assert t.zh_forms() == ("涡轮机", "透平")

    def test_zh_accepts_bare_string(self):
        assert Term.from_dict({"en": "biomass", "zh": "生物质"}, 0).zh == ("生物质",)

    def test_duplicate_forms_deduped_preserving_order(self):
        t = Term.from_dict({"en": "x", "zh": ["甲", "乙", "甲"]}, 0)
        assert t.zh == ("甲", "乙")

    @pytest.mark.parametrize(
        "data, msg",
        [
            ({}, "缺少 en"),
            ({"en": "x", "nonsense": 1}, "未知字段"),
            ({"en": "x", "match_anchor": "middle"}, "match_anchor"),
            ({"en": "x", "source": "footnote"}, "source"),
            ({"en": "x", "page": "26"}, "page"),
            ({"en": "x", "zh": [""]}, "空字符串"),
            ({"en": "x", "zh": [1]}, "必须是字符串"),
            ({"en": "x", "match_anchor": "zh"}, "永远无法命中"),
            ({"en": "x", "aliases": ["a"], "unsafe_aliases": ["a"]}, "互斥"),
            ({"en": "x", "unsafe_aliases": ["x"]}, "不能同时列入"),
        ],
    )
    def test_invalid_entries_rejected(self, data, msg):
        with pytest.raises(GlossaryError, match=msg):
            Term.from_dict(data, 0)

    def test_forms_are_normalized_on_load(self):
        # 文档侧已归一化；术语表若不归一，全角括号/零宽字符会造成静默失配
        t = Term.from_dict({"en": "ＰＯＷＥＲ　density", "zh": ["功率​密度"]}, 0)
        assert t.en == "POWER density"
        assert t.zh == ("功率密度",)

    def test_anchor_zh_hides_english_forms(self):
        t = Term.from_dict({"en": "block", "zh": ["区块"], "match_anchor": "zh"}, 0)
        assert t.en_forms() == ()
        assert t.zh_forms() == ("区块",)

    def test_anchor_en_hides_chinese_forms(self):
        t = Term.from_dict({"en": "nonce", "zh": ["随机数"], "match_anchor": "en"}, 0)
        assert t.en_forms() == ("nonce",)
        assert t.zh_forms() == ()

    def test_unsafe_aliases_never_in_match_forms(self):
        t = Term.from_dict(
            {"en": "uranium enrichment", "aliases": ["U enrichment"], "unsafe_aliases": ["enrichment"]},
            0,
        )
        assert "enrichment" not in t.en_forms()
        assert t.en_forms() == ("uranium enrichment", "U enrichment")


# ==========================================================================
# 2. 匹配
# ==========================================================================


class TestMatching:
    def test_empty_text_and_empty_glossary(self):
        assert g({"en": "x"}).find("") == ()
        assert Glossary().find("任意文本") == ()
        assert Glossary().is_empty is True

    def test_english_and_chinese_both_matched(self):
        gl = g({"en": "heat engine", "zh": ["热机"]})
        assert hits(gl, "热机（heat engine）的效率") == [("热机", "heat engine"), ("heat engine", "heat engine")]

    def test_longest_form_wins_over_prefix(self):
        # steam turbine 不能被 turbine 截断，blockchain 不能被 block 截断
        gl = g({"en": "steam turbine", "zh": ["汽轮机"]}, {"en": "turbine", "zh": ["涡轮机"]})
        assert hits(gl, "a steam turbine here") == [("steam turbine", "steam turbine")]
        gl2 = g({"en": "blockchain", "zh": ["区块链"]}, {"en": "block", "zh": ["区块"]})
        assert hits(gl2, "blockchain technology") == [("blockchain", "blockchain")]
        assert hits(gl2, "区块链技术") == [("区块链", "blockchain")]

    def test_case_insensitive_english(self):
        gl = g({"en": "heat engine"})
        for variant in ["Heat Engine", "HEAT ENGINE", "heat ENGINE"]:
            assert hits(gl, f"a {variant} works") == [(variant, "heat engine")]

    def test_chinese_matching_is_case_and_boundary_free(self):
        gl = g({"en": "biomass", "zh": ["生物质"]})
        assert hits(gl, "利用生物质发电") == [("生物质", "biomass")]

    def test_word_boundary_prevents_substring_match(self):
        # 没有边界的话 AI 会命中 chain、said、maintain
        gl = g({"en": "AI"})
        assert hits(gl, "the chain remains") == []
        assert hits(gl, "an AI system") == [("AI", "AI")]

    def test_no_match_inside_longer_word(self):
        gl = g({"en": "block"})
        assert hits(gl, "blocking blockade") == []

    @pytest.mark.parametrize("variant", ["peer-to-peer", "peer to peer", "peer  to  peer", "peer\nto\npeer"])
    def test_whitespace_and_hyphen_are_interchangeable(self, variant):
        # 模型可能换行断词或改写连字符；不兼容会造成静默漏匹配
        gl = g({"en": "peer-to-peer", "zh": ["点对点"]})
        assert len(gl.find(f"a {variant} network")) == 1

    def test_line_break_inside_english_term(self):
        gl = g({"en": "gradient descent"})
        assert len(gl.find("use gradient\ndescent here")) == 1

    def test_unsafe_alias_is_not_matched(self):
        gl = g({"en": "uranium enrichment", "unsafe_aliases": ["enrichment"]})
        assert hits(gl, "cultural enrichment programs") == []
        assert hits(gl, "uranium enrichment needed") == [("uranium enrichment", "uranium enrichment")]

    def test_anchor_zh_ignores_english_occurrence(self):
        # block 的英文裸形有歧义：building block 若被计入分母会压低标注率
        gl = g({"en": "block", "zh": ["区块"], "match_anchor": "zh"})
        assert hits(gl, "the basic building block of transactions") == []
        assert hits(gl, "每个区块都链接前一个") == [("区块", "block")]

    def test_anchor_en_ignores_chinese_occurrence(self):
        gl = g({"en": "nonce", "zh": ["随机数"], "match_anchor": "en"})
        assert hits(gl, "一个随机数") == []
        assert hits(gl, "the nonce value") == [("nonce", "nonce")]

    def test_aliases_are_matched(self):
        gl = g({"en": "photovoltaic", "zh": ["光伏"], "aliases": ["solar voltaic", "PV"]})
        # 判据 R2：课件写 solar voltaic，模型几乎必然输出 photovoltaic，两者都要命中
        assert len(gl.find("solar voltaic cells")) == 1
        assert len(gl.find("photovoltaic cells")) == 1
        assert len(gl.find("PV cells")) == 1

    def test_offsets_round_trip(self):
        text = "先讲热机，再讲 heat engine 的效率"
        for m in g({"en": "heat engine", "zh": ["热机"]}).find(text):
            assert text[m.start : m.end] == m.text

    def test_matches_sorted_by_position(self):
        gl = g({"en": "biomass", "zh": ["生物质"]}, {"en": "geothermal", "zh": ["地热"]})
        found = gl.find("地热与生物质，geothermal and biomass")
        assert [m.start for m in found] == sorted(m.start for m in found)

    def test_side_is_reported(self):
        gl = g({"en": "heat engine", "zh": ["热机"]})
        sides = {m.side for m in gl.find("热机 heat engine")}
        assert sides == {MatchSide.EN, MatchSide.ZH}

    def test_repeated_occurrences_all_returned(self):
        # 不折叠——first/all 口径由维度模块按配置决定
        gl = g({"en": "biomass", "zh": ["生物质"]})
        assert len(gl.find("生物质、生物质、生物质")) == 3

    def test_symbol_terminated_form_still_matches(self):
        gl = g({"en": "C++"})
        assert len(gl.find("written in C++ here")) == 1


class TestMaskedTextIntegration:
    def test_terms_inside_code_block_not_matched(self):
        gl = g({"en": "solar constant", "zh": ["太阳常数"]})
        doc = Document.parse("正文提到太阳常数。\n\n```python\nsolar_constant = 1361  # 太阳常数\n```")
        found = gl.find(doc.masked)
        assert len(found) == 1
        assert found[0].text == "太阳常数"

    def test_terms_inside_inline_code_not_matched(self):
        gl = g({"en": "nonce"})
        assert g({"en": "nonce"}).find(Document.parse("变量 `nonce` 是").masked) == ()

    def test_terms_inside_url_not_matched(self):
        gl = g({"en": "biomass", "zh": ["生物质"]})
        assert gl.find(Document.parse("见 https://x.com/biomass 一文").masked) == ()

    def test_offsets_map_back_to_normalized_text(self):
        gl = g({"en": "heat engine", "zh": ["热机"]})
        doc = Document.parse("`code` 之后讲热机（heat engine）。")
        for m in gl.find(doc.masked):
            assert doc.normalized[m.start : m.end] == m.text


# ==========================================================================
# 3. 术语表加载
# ==========================================================================


class TestGlossaryLoading:
    def test_from_list_of_strings(self):
        gl = Glossary.from_list(["gradient descent", "learning rate"])
        assert len(gl) == 2
        assert gl.stats()["without_zh"] == 2

    def test_from_dict_with_metadata(self):
        gl = Glossary.from_dict(
            {"course": "22.081J", "course_title": "Sustainable Energy", "terms": [{"en": "biomass"}]}
        )
        assert gl.course == "22.081J"
        assert len(gl) == 1

    def test_from_dict_none_gives_empty(self):
        assert Glossary.from_dict(None).is_empty is True

    def test_bare_list_accepted_at_root(self):
        assert len(Glossary.from_dict([{"en": "biomass"}])) == 1

    @pytest.mark.parametrize(
        "data, msg",
        [
            ({"terms": [{"en": "a"}], "nonsense": 1}, "未知字段"),
            ({}, "缺少 terms"),
            ({"terms": {}}, "必须是列表"),
            ("string", "必须是对象或列表"),
        ],
    )
    def test_invalid_root_rejected(self, data, msg):
        with pytest.raises(GlossaryError, match=msg):
            Glossary.from_dict(data)

    def test_duplicate_en_rejected(self):
        with pytest.raises(GlossaryError, match="重复出现"):
            Glossary.from_list([{"en": "biomass"}, {"en": "Biomass"}])

    def test_from_json_and_malformed(self):
        assert len(Glossary.from_json('[{"en": "biomass"}]')) == 1
        with pytest.raises(GlossaryError, match="不是合法 JSON"):
            Glossary.from_json("{oops}")

    def test_from_file(self, tmp_path):
        p = tmp_path / "glossary.json"
        p.write_text(json.dumps({"terms": [{"en": "biomass", "zh": ["生物质"]}]}), encoding="utf-8")
        assert Glossary.from_file(p).terms[0].preferred_zh == "生物质"

    def test_missing_file(self, tmp_path):
        with pytest.raises(GlossaryError, match="不存在"):
            Glossary.from_file(tmp_path / "nope.json")


class TestStats:
    def test_counts_by_source(self):
        gl = Glossary.from_list(
            [
                {"en": "a", "source": "body_text"},
                {"en": "b", "source": "figure_caption"},
                {"en": "c", "source": "figure_caption"},
            ]
        )
        s = gl.stats()
        assert (s["total"], s["body_text"], s["figure_caption"]) == (3, 1, 2)

    def test_anchor_counts(self):
        gl = Glossary.from_list(
            [{"en": "a", "zh": ["甲"], "match_anchor": "zh"}, {"en": "b", "match_anchor": "en"}]
        )
        assert gl.stats()["anchor_zh_only"] == 1
        assert gl.stats()["anchor_en_only"] == 1

    def test_by_source(self):
        gl = Glossary.from_list([{"en": "a"}, {"en": "b", "source": "figure_caption"}])
        assert [t.en for t in gl.by_source(TermSource.FIGURE_CAPTION)] == ["b"]


# ==========================================================================
# 4. 真实术语表回归
# ==========================================================================


@pytest.fixture(scope="module")
def real():
    from pathlib import Path

    return Glossary.from_file(
        Path(__file__).resolve().parent.parent / "examples" / "glossary_22081J.json"
    )


class TestRealGlossary:
    def test_loads_with_expected_shape(self, real):
        s = real.stats()
        assert (s["total"], s["body_text"], s["figure_caption"]) == (44, 27, 17)
        assert s["without_zh"] == 0

    def test_turbine_preferred_translation(self, real):
        # 判据 R4：首选取通用译名「涡轮机」，而非更生僻的「透平」
        turbine = next(t for t in real if t.en == "turbine")
        assert turbine.preferred_zh == "涡轮机"
        assert "透平" in turbine.zh

    def test_steam_turbine_not_shadowed_by_turbine(self, real):
        assert hits(real, "汽轮机是核心部件") == [("汽轮机", "steam turbine")]

    def test_nonstandard_courseware_spelling_matches(self, real):
        # 课件写 solar voltaic，模型输出 photovoltaic，两者都要命中同一条
        assert real.find("solar voltaic cell")[0].term.en == "photovoltaic"
        assert real.find("photovoltaic cell")[0].term.en == "photovoltaic"

    def test_unsafe_alias_enrichment_not_matched(self, real):
        assert real.find("cultural enrichment") == ()
        assert len(real.find("uranium enrichment")) == 1

    def test_annotated_form_yields_adjacent_zh_and_en_hits(self, real):
        found = real.find("热机（heat engine）的效率")
        assert [m.side for m in found] == [MatchSide.ZH, MatchSide.EN]
        assert all(m.term.en == "heat engine" for m in found)
