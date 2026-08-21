"""术语提取的口径测试。

**不断言召回率的具体数值**——那个数由 `scripts/phase2_recall.py` 报告，
写进测试等于把一个会随语料变的数固化成断言。
这里只钉住**口径与误差方向**：判据的拒绝行为、图注通道的构造约束、
以及「判定先于召回」的顺序保护。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from parse_service.pdf import parse_pdf
from slicer import slice_document

from glossary import extract
from glossary.channels import FIGURE_PAGE_MIN_BLOCKS, is_figure_page
from glossary.criteria import check
from glossary.text import candidate_phrases, normalize_phrase, singularize, tokenize

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "实验脚本" / "测试集"
FILE = "MIT22_081JF10_lec01b.pdf"

pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="语料不存在")


@pytest.fixture(scope="module")
def sliceset():
    return slice_document(parse_pdf((CORPUS / FILE).read_bytes(), FILE))


@pytest.fixture(scope="module")
def glossary(sliceset):
    return extract(sliceset, "22.081J", FILE)


class TestText:
    def test_ligatures_normalized(self):
        """连字不归一会把 difficult 切成 dif + cult。缺陷修复，不是口径。"""
        assert tokenize("difﬁcult") == ["difficult"]
        assert "dif" not in candidate_phrases("difﬁcult")

    def test_singularize_conservative(self):
        assert singularize("rods") == "rod"
        assert singularize("products") == "product"
        assert singularize("gas") == "gas"        # ss/us/is 不动
        assert singularize("analysis") == "analysis"

    def test_stopwords_are_boundaries(self):
        """停用词是短语边界，跨过它的组合不是名词短语。"""
        assert "heat of engine" not in candidate_phrases("heat of engine")


class TestCriteria:
    @pytest.mark.parametrize("phrase", ["CO2", "235U", "330", "MW", "kWh"])
    def test_g4_rejects_units_and_formulas(self, phrase):
        assert not check(phrase).passed

    @pytest.mark.parametrize("phrase", ["MIT", "Cape Wind", "EIA-DoE", "Flickr"])
    def test_g5_rejects_proper_nouns(self, phrase):
        assert not check(phrase).passed

    @pytest.mark.parametrize("phrase", ["energy", "water", "cost"])
    def test_g1_rejects_criteria_named_examples(self, phrase):
        """判据文件 G1 行明确举的三个反例。"""
        assert not check(phrase).passed

    @pytest.mark.parametrize(
        "phrase", ["biomass", "photovoltaic", "alternator", "pressurizer", "turbine"]
    )
    def test_g1_keeps_gold_single_word_terms(self, phrase):
        """金标准的 8 条单词术语不得被常用词表误拒。"""
        assert check(phrase).passed

    def test_g2_g3_not_claimed(self, glossary):
        """G2/G3 需语义判断，规则层做不到——任何条目都不得宣称已满足。"""
        for t in glossary.terms:
            assert "G2/G3 未经语义验证" in t.confidence_reason


class TestFigureChannel:
    def test_p1_is_mandatory(self, sliceset):
        """块数不足一律不是图表页，无论其余条件命中多少。

        降为可选会把 p7（OUTLINE，8 块）误判成图表页。
        """
        from glossary.channels import _page_units

        pages = _page_units(sliceset)
        for idx, units in pages.items():
            ok, fired = is_figure_page(units, 0)
            if len(units) < FIGURE_PAGE_MIN_BLOCKS:
                assert not ok, f"p{idx} 只有 {len(units)} 块却被判为图表页"
                assert fired == []

    def test_bullet_list_page_not_figure_page(self, sliceset):
        """p7 是 OUTLINE 项目符号页，短块无标点，但不是图。"""
        from glossary.channels import _page_units

        units = _page_units(sliceset)[7]
        assert not is_figure_page(units, 0)[0]

    def test_diagram_page_detected(self, sliceset):
        """p27 是压水堆示意图，31 块、词数中位 1、2 档字号。"""
        from glossary.channels import _page_units

        ok, fired = is_figure_page(_page_units(sliceset)[27], 0)
        assert ok and {"P1", "P2", "P3", "P4"} <= set(fired)

    def test_adjacency_absent_on_diagram_pages(self, sliceset):
        """图注通道不能依赖 image_adjacency——关键页上信号不存在。

        这条钉住的是「为什么不用邻接」，是本模块最核心的设计前提。
        """
        from glossary.channels import _adjacent_count, _page_units

        pages = _page_units(sliceset)
        for idx in (20, 27):
            assert _adjacent_count(pages[idx]) == 0, f"p{idx} 竟有邻接块，前提需复核"

    #: 已知未修的误收：章节面包屑「Energy usage / Resource usage breakdowns」
    #: 出现在图表页上，整块 5 词、无句末标点，F1–F5 全部通过。
    #: **本次刻意不修**——它已进过人工判定（判为误收），改动提取会让那份判定作废，
    #: 而重跑一轮就是 README §1.2 说的第 6 轮迭代。修法见 TODO。
    KNOWN_BREADCRUMB_LEAK = {"energy usage resource usage breakdown"}

    def test_no_full_window_splitting(self, glossary):
        """图注候选不做全窗口切分——否则会产出三段拼接垃圾。

        白名单里只有一条已知未修的面包屑泄漏；**多出任何一条都要红**。
        """
        bad = {t.en for t in glossary.terms if len(t.en.split()) > 4}
        assert bad <= self.KNOWN_BREADCRUMB_LEAK, f"出现新的超长拼接：{bad - self.KNOWN_BREADCRUMB_LEAK}"

    def test_known_leak_still_present(self, glossary):
        """反向钉住：白名单不得在缺陷已修后继续挂着。

        若这条红了，说明泄漏已被修掉，应同时删掉白名单——
        留着一个永远为真的豁免，就是 R17 说的「防护措施空转」。
        """
        got = {t.en for t in glossary.terms}
        assert self.KNOWN_BREADCRUMB_LEAK <= got, "泄漏已修，请删除白名单与本测试"


class TestOutput:
    def test_sources_separated(self, glossary):
        """PRD §8.1 要求两条通道分开报——混在一起看不出新通道有没有用。"""
        assert {t.source for t in glossary.terms} <= {"body_text", "figure_caption"}
        assert any(t.source == "figure_caption" for t in glossary.terms)

    def test_every_term_has_provenance(self, glossary):
        """v1 不做用户增删界面，但输出必须为将来留结构。"""
        for t in glossary.terms:
            assert t.heuristics and t.evidence and t.pages
            assert t.confidence in ("high", "medium", "low")

    def test_zh_is_empty(self, glossary):
        assert all(t.zh == [] for t in glossary.terms)

    def test_roundtrip(self, glossary):
        from glossary.schema import Glossary

        assert Glossary.model_validate(json.loads(glossary.model_dump_json())) == glossary


class TestPhaseOrdering:
    def test_phase2_refuses_without_adjudication(self, tmp_path, monkeypatch):
        """召回率必须在判定完成之后才能算——这是方法的一部分，不是流程习惯。"""
        import scripts.phase2_recall as p2

        monkeypatch.setattr(p2, "ADJ", tmp_path / "missing.json")
        assert p2.main() == 1

    def test_phase2_refuses_on_blank_verdict(self, tmp_path, monkeypatch):
        import scripts.phase2_recall as p2

        f = tmp_path / "adj.json"
        f.write_text(json.dumps({"new_findings": [{"en": "x", "verdict": None}]}), encoding="utf-8")
        monkeypatch.setattr(p2, "ADJ", f)
        assert p2.main() == 1
