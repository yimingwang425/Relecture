"""evaluator 与 CLI 的单元测试。"""

import json
from pathlib import Path

import pytest

from a_rules.__main__ import _cn_number, _sort_key, main
from a_rules.config import CONFIG_VERSION, StyleConfig
from a_rules.evaluator import DIMENSIONS, evaluate
from a_rules.glossary import Glossary

GLOSSARY = Path(__file__).resolve().parent.parent / "examples" / "glossary_22081J.json"
TEXT = "热机（heat engine）把燃料的化学能转成机械能，这是火力发电的基础环节。"


@pytest.fixture(scope="module")
def real():
    return Glossary.from_file(GLOSSARY)


class TestEvaluate:
    def test_runs_all_four_dimensions(self):
        ev = evaluate(TEXT)
        assert len(ev.dimensions) == len(DIMENSIONS) == 4
        assert {d.name for d in ev.dimensions} == {
            "chinese_ratio", "term_annotation", "paragraph_length", "quote_length"
        }

    def test_by_name(self):
        assert evaluate(TEXT).by_name("quote_length").name == "quote_length"
        with pytest.raises(KeyError):
            evaluate(TEXT).by_name("nope")

    def test_records_config_fingerprint_and_version(self):
        cfg = StyleConfig()
        ev = evaluate(TEXT, cfg)
        assert ev.config_fingerprint == cfg.fingerprint()
        assert ev.config_version == CONFIG_VERSION

    def test_records_glossary_version_and_freeze(self, real):
        ev = evaluate(TEXT, StyleConfig(), real)
        assert ev.glossary_version == "1.1" and ev.glossary_frozen is True

    def test_no_glossary_leaves_version_none(self):
        assert evaluate(TEXT).glossary_version is None

    def test_scored_excludes_inactive_and_uncomputable(self):
        # 单语配置下两个跨语言维度不激活
        ev = evaluate(TEXT, StyleConfig.from_dict({"explain_language": "zh", "source_language": "zh"}))
        assert {d.name for d in ev.scored} == {"paragraph_length", "quote_length"}

    def test_all_activated_passed_is_none_when_nothing_scored(self):
        # 「没有维度可判」与「全部通过」是两回事，混同会让空样本表现为满分
        ev = evaluate("", StyleConfig())
        assert ev.scored == () and ev.all_activated_passed is None

    def test_all_activated_passed_false_if_any_fails(self):
        ev = evaluate("word " * 50, StyleConfig())
        assert ev.by_name("chinese_ratio").passed is False
        assert ev.all_activated_passed is False

    def test_no_mixed_score_exposed(self):
        # PRD §8.1：不做单一混合分
        keys = evaluate(TEXT).to_dict().keys()
        assert not any("score" in k and k != "scored_count" for k in keys)

    def test_serializable(self, real):
        json.dumps(evaluate(TEXT, StyleConfig(), real).to_dict(), ensure_ascii=False)


class TestRoundOrdering:
    @pytest.mark.parametrize("token, expected", [("一", 1), ("三", 3), ("九", 9), ("十", 10), ("十二", 12), ("二十", 20), ("二十五", 25)])
    def test_chinese_numerals(self, token, expected):
        assert _cn_number(token) == expected

    @pytest.mark.parametrize("token", ["", "甲", "百", "一二三"])
    def test_unparseable_returns_none(self, token):
        assert _cn_number(token) is None

    def test_chinese_round_files_sort_by_round(self):
        # 直接 sorted() 会得到「第一轮、第三轮、第二轮」——中文数字按码位排，
        # 漂移曲线的 x 轴会静默错乱
        names = [Path(f"第{c}轮.txt") for c in "六一四三五二"]
        assert [p.stem for p in sorted(names, key=_sort_key)] == [f"第{c}轮" for c in "一二三四五六"]

    def test_arabic_numerals_sort_numerically(self):
        names = [Path(f"round{n}.md") for n in (10, 2, 1)]
        assert [p.stem for p in sorted(names, key=_sort_key)] == ["round1", "round2", "round10"]

    def test_non_numeric_falls_back_to_name(self):
        names = [Path("beta.txt"), Path("alpha.txt")]
        assert [p.stem for p in sorted(names, key=_sort_key)] == ["alpha", "beta"]


class TestCli:
    def test_single_file_human_output(self, tmp_path, capsys):
        f = tmp_path / "a.md"
        f.write_text(TEXT, encoding="utf-8")
        assert main(["--text", str(f), "--glossary", str(GLOSSARY)]) == 0
        out = capsys.readouterr().out
        assert "chinese_ratio" in out and "计分" in out

    def test_jsonl_one_record_per_file(self, tmp_path, capsys):
        for i in (1, 2):
            (tmp_path / f"r{i}.md").write_text(TEXT, encoding="utf-8")
        assert main(["--dir", str(tmp_path), "--jsonl"]) == 0
        lines = [l for l in capsys.readouterr().out.strip().splitlines() if l]
        assert len(lines) == 2
        assert json.loads(lines[0])["source"] == "r1.md"

    def test_after_marker_strips_prompt_header(self, tmp_path, capsys):
        # 不剥掉会把提示词计进统计——提示词里往往写着术语要求，虚高命中数。
        # 术语维度 v1 默认不激活（CONFIG_VERSION 2.0），故显式开启；
        # 这条断言必须留在术语维度上——提示词头部虚高的正是术语命中数。
        f = tmp_path / "a.md"
        f.write_text("Prompt：请用中文并给出 heat engine\n输出：\n热机（heat engine）如上。", encoding="utf-8")
        cfg_path = tmp_path / "c.json"
        cfg_path.write_text('{"term_annotation_enabled": true}', encoding="utf-8")
        main(["--text", str(f), "--after", "输出：", "--json",
              "--config", str(cfg_path), "--glossary", str(GLOSSARY)])
        rec = json.loads(capsys.readouterr().out)[0]
        assert rec["dimensions"]["term_annotation"]["details"]["occurrences_all"] == 1

    def test_stdin(self, monkeypatch, capsys):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(TEXT))
        assert main(["--stdin", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["source"] == "<stdin>"

    @pytest.mark.parametrize("argv", [["--text", "/nope/x.md"], ["--dir", "/nope/"]])
    def test_missing_input_returns_error_code(self, argv, capsys):
        assert main(argv) == 2

    def test_bad_config_returns_error_code(self, tmp_path, capsys):
        c = tmp_path / "c.json"
        c.write_text('{"nonsense": 1}', encoding="utf-8")
        f = tmp_path / "a.md"
        f.write_text(TEXT, encoding="utf-8")
        assert main(["--text", str(f), "--config", str(c)]) == 2
        assert "未知字段" in capsys.readouterr().err

    def test_empty_dir_returns_error_code(self, tmp_path):
        assert main(["--dir", str(tmp_path)]) == 2


class TestV1ThreeDimensions:
    """PRD §3.2「风格遵循度的 v1 定义」在聚合层的落地。"""

    TEXT = "热机（heat engine）把热能转成机械能，这段文字用来自检，长度足够算出段落均值。"

    def test_only_three_dimensions_counted(self):
        ev = evaluate(self.TEXT)
        assert [d.name for d in ev.scored] == [
            "chinese_ratio",
            "paragraph_length",
            "quote_length",
        ]

    def test_term_dimension_still_computed_but_not_counted(self):
        # 仍然计算是为了让 details 能作观察项进日志；代价接近零
        d = evaluate(self.TEXT).by_name("term_annotation")
        assert d.activated is False and d.counts_toward_score is False

    def test_term_failure_cannot_fail_the_round(self):
        """**反向验证**：术语全部未标注的文本，在 v1 下仍应达标。

        这条会红，就说明术语维度实际上还在参与达标判定——
        而它的分母残缺 68%，PRD §8.1 已明确不得计入。
        """
        from a_rules.glossary import Glossary

        gl = Glossary.from_list([{"en": "heat engine", "zh": ["热机"]}])
        text = "热机把热能转成机械能，这段文字用来自检，长度足够算出段落均值，且完全没有英文括注。"
        assert evaluate(text, glossary=gl).all_activated_passed is True

    def test_metric_set_id_and_counted_dimensions_in_payload(self):
        # 每轮埋点必须带得走这两个字段，否则跨批次合并时无从核对
        d = evaluate(self.TEXT).to_dict()
        assert d["config_version"] == "2.0"
        assert d["metric_set_id"] == StyleConfig().metric_set_id()
        assert d["counted_dimensions"] == ["chinese_ratio", "paragraph_length", "quote_length"]
