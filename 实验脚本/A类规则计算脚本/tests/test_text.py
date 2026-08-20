"""text.py 的单元测试。

预处理层是四个维度的共同地基，它的边界情况处理错了，四个指标会同时偏，
且偏差方向不可预测。因此这里的覆盖要比维度模块本身更密。
"""

import pytest

from a_rules.text import (
    MASK,
    Block,
    BlockKind,
    CharCount,
    Document,
    count_chars,
    normalize,
    split_sentences,
    strip_mask,
    strip_quote_markers,
)


# ==========================================================================
# 1. 归一化
# ==========================================================================


class TestNormalize:
    def test_empty(self):
        assert normalize("") == ""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("a\r\nb", "a\nb"),
            ("a\rb", "a\nb"),
            ("a\r\n\r\nb", "a\n\nb"),
            ("a b", "a\nb"),
            ("a b", "a\nb"),
        ],
    )
    def test_line_endings_unified(self, raw, expected):
        assert normalize(raw) == expected

    @pytest.mark.parametrize("ch", ["​", "‌", "‍", "﻿", "­", "⁠"])
    def test_invisible_chars_removed(self, ch):
        # 零宽字符若残留，会把 "gradient descent" 切成两半，术语匹配直接失效
        assert normalize(f"gradient{ch} descent") == "gradient descent"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("（gradient descent）", "(gradient descent)"),  # 全角括号 → 半角
            ("ＡＢＣ", "ABC"),  # 全角字母 → 半角
            ("１２３", "123"),  # 全角数字 → 半角
            ("梯度，下降", "梯度,下降"),  # 全角逗号 → 半角（NFKC 兼容分解）
            ("对吗？", "对吗?"),
            ("注意！", "注意!"),
            ("　", " "),  # 全角空格 → 半角
        ],
    )
    def test_nfkc_fullwidth_to_halfwidth(self, raw, expected):
        assert normalize(raw) == expected

    def test_cjk_full_stop_preserved(self):
        # 。「」 不是兼容字符，NFKC 不动它们，也不该动——引用检测依赖直角引号
        assert normalize("这是一句话。「引用」") == "这是一句话。「引用」"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("﹁引用﹂", "「引用」"),
            ("﹃引用﹄", "『引用』"),
            ("〝引用〞", "“引用”"),
            ("前—后", "前—后"),
            ("前–后", "前—后"),
            ("前―后", "前—后"),
            ("省略⋯", "省略..."),
        ],
    )
    def test_cjk_punctuation_canonicalized(self, raw, expected):
        assert normalize(raw) == expected

    def test_idempotent(self):
        raw = "梯度下降（gradient descent）：ＡＢ　１２﹁引用﹂\r\n第二行"
        once = normalize(raw)
        assert normalize(once) == once


# ==========================================================================
# 2. 字数口径
# ==========================================================================


class TestCountChars:
    def test_empty(self):
        assert count_chars("").total == 0

    def test_pure_chinese(self):
        c = count_chars("梯度下降法")
        assert (c.han, c.latin_words, c.digit_groups) == (5, 0, 0)
        assert c.total == 5

    def test_pure_english_counts_words_not_letters(self):
        # 口径决策：英文按词计。按字母计会让 "gradient descent" 顶 16 个汉字，
        # 段落字数指标随中英比例剧烈抖动，与 §8.2 要观察的漂移信号混淆。
        c = count_chars("gradient descent works")
        assert (c.han, c.latin_words) == (0, 3)

    def test_hyphenated_and_apostrophe_are_one_word(self):
        assert count_chars("state-of-the-art").latin_words == 1
        assert count_chars("don't").latin_words == 1

    def test_digit_group_counts_as_one(self):
        assert count_chars("3.14").digit_groups == 1
        assert count_chars("1,024").digit_groups == 1
        assert count_chars("1 2 3").digit_groups == 3

    def test_punctuation_and_whitespace_excluded(self):
        assert count_chars("，。！？,.!?  \n\t*#>-|").total == 0

    def test_mixed(self):
        c = count_chars("学习率 learning rate 设为 0.01")
        assert (c.han, c.latin_words, c.digit_groups) == (5, 2, 1)
        assert c.total == 8

    def test_traditional_chinese_counted(self):
        assert count_chars("學習機器").han == 4

    @pytest.mark.parametrize("text", ["ひらがな", "カタカナ", "한글입니다"])
    def test_kana_and_hangul_not_counted_as_han(self, text):
        # 同属 CJK 但不是中文；计入会虚高「中文字符占比」
        assert count_chars(text).han == 0

    def test_cjk_extension_a_counted(self):
        assert count_chars("㐀㐁").han == 2

    def test_mask_char_excluded(self):
        assert count_chars(f"中文{MASK * 20}").total == 2

    def test_addition(self):
        assert (CharCount(1, 2, 3) + CharCount(10, 20, 30)) == CharCount(11, 22, 33)


# ==========================================================================
# 3. 块级切分
# ==========================================================================


def kinds(doc: Document) -> list[str]:
    return [b.kind.value for b in doc.blocks]


class TestSegmentation:
    def test_empty_and_blank(self):
        assert Document.parse("").blocks == ()
        assert Document.parse("   \n\n\t\n").blocks == ()

    def test_single_paragraph(self):
        doc = Document.parse("梯度下降是一种优化方法。")
        assert kinds(doc) == ["paragraph"]

    def test_paragraphs_split_on_blank_line(self):
        doc = Document.parse("第一段。\n\n第二段。\n\n\n\n第三段。")
        assert kinds(doc) == ["paragraph"] * 3

    def test_soft_wrapped_lines_stay_one_paragraph(self):
        doc = Document.parse("第一行没有空行\n第二行接着写\n第三行还是同一段")
        assert kinds(doc) == ["paragraph"]
        assert doc.blocks[0].count().total == 21

    def test_heading_is_separate_block(self):
        doc = Document.parse("# 标题\n正文内容。")
        assert kinds(doc) == ["heading", "paragraph"]

    @pytest.mark.parametrize("level", range(1, 7))
    def test_heading_levels(self, level):
        doc = Document.parse(f"{'#' * level} 标题")
        assert kinds(doc) == ["heading"]

    def test_hash_without_space_is_not_heading(self):
        assert kinds(Document.parse("#标签内容")) == ["paragraph"]

    def test_consecutive_list_items_merge_into_one_block(self):
        # 口径决策：连续列表项合并为一段。逐条计段会把段落均值人为拉低。
        doc = Document.parse("- 第一条\n- 第二条\n- 第三条")
        assert kinds(doc) == ["list"]

    def test_list_items_separated_by_blank_line_still_one_block(self):
        doc = Document.parse("- 第一条\n\n- 第二条")
        assert kinds(doc) == ["list"]

    @pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "2)"])
    def test_list_markers(self, marker):
        assert kinds(Document.parse(f"{marker} 一个列表项")) == ["list"]

    def test_list_ends_at_blank_line_before_paragraph(self):
        doc = Document.parse("- 第一条\n- 第二条\n\n这是新的一段。")
        assert kinds(doc) == ["list", "paragraph"]

    def test_paragraph_breaks_when_list_starts(self):
        doc = Document.parse("正文一段。\n- 列表项")
        assert kinds(doc) == ["paragraph", "list"]

    def test_blockquote_lines_merge_into_one_block(self):
        doc = Document.parse("> 第一行引用\n> 第二行引用\n\n正文。")
        assert kinds(doc) == ["quote", "paragraph"]

    def test_table_block(self):
        doc = Document.parse("| A | B |\n| --- | --- |\n| 1 | 2 |\n\n正文。")
        assert kinds(doc) == ["table", "paragraph"]

    def test_fenced_code_block(self):
        doc = Document.parse("正文。\n\n```python\nprint(1)\n```\n\n更多正文。")
        assert kinds(doc) == ["paragraph", "code", "paragraph"]

    def test_tilde_fence(self):
        doc = Document.parse("~~~\nprint(1)\n~~~")
        assert kinds(doc) == ["code"]

    def test_unterminated_fence_extends_to_eof(self):
        # 流式输出被截断的常见形态；若不当作代码，半截代码会被计进中文占比
        doc = Document.parse("正文。\n\n```python\nprint(1)\n还有中文注释")
        assert kinds(doc) == ["paragraph", "code"]
        assert doc.blocks[1].count().total == 0

    def test_longer_closing_fence_accepted(self):
        doc = Document.parse("````\ncode\n````")
        assert kinds(doc) == ["code"]

    def test_indented_code_block(self):
        doc = Document.parse("正文。\n\n    print(1)\n    print(2)\n\n更多正文。")
        assert kinds(doc) == ["paragraph", "code", "paragraph"]

    def test_indented_line_after_paragraph_is_not_code(self):
        # 段落续行常被缩进；误判为代码会把正文从所有统计里抹掉
        doc = Document.parse("正文第一行\n    正文续行也算同一段")
        assert kinds(doc) == ["paragraph"]

    def test_line_numbers(self):
        doc = Document.parse("# 标题\n\n正文。\n\n- 列表")
        assert [b.line_no for b in doc.blocks] == [1, 3, 5]

    def test_offsets_slice_back_to_source(self):
        doc = Document.parse("# 标题\n\n正文一段。\n\n> 引用")
        for b in doc.blocks:
            assert doc.normalized[b.start : b.end] == b.text


# ==========================================================================
# 4. 掩码
# ==========================================================================


class TestMasking:
    def test_masked_string_is_char_aligned_with_normalized(self):
        # 核心不变量：维度模块靠这一点在掩码串上跑正则、再回原文取证
        raw = "正文 `code` 与 ```\nblock\n``` 和 $x^2$ 以及 https://a.b/c"
        doc = Document.parse(raw)
        assert len(doc.masked) == len(doc.normalized)
        for i, ch in enumerate(doc.normalized):
            if ch == "\n":
                assert doc.masked[i] == "\n"

    def test_block_text_and_masked_same_length(self):
        doc = Document.parse("正文 `code`。\n\n```py\nx = 1\n```")
        for b in doc.blocks:
            assert len(b.text) == len(b.masked)

    def test_code_block_content_excluded_from_counts(self):
        with_code = Document.parse("中文正文四字。\n\n```python\n# 这段中文注释在代码块里\nprint('hello world')\n```")
        without = Document.parse("中文正文四字。")
        assert with_code.count() == without.count()

    def test_inline_code_excluded(self):
        assert Document.parse("变量 `learning_rate` 控制步长").count().latin_words == 0

    def test_multi_backtick_inline_code(self):
        assert Document.parse("符号 ``a`b`` 见上").count().latin_words == 0

    def test_lone_backtick_is_not_masked(self):
        assert Document.parse("反引号 ` 单独出现 word").count().latin_words == 1

    def test_inline_math_excluded(self):
        assert Document.parse("公式 $w_t - \\eta g_t$ 如上").count().latin_words == 0

    def test_block_math_excluded(self):
        assert Document.parse("如下：\n\n$$\nE = mc^2\n$$").count().latin_words == 0

    def test_bare_url_excluded(self):
        assert Document.parse("见 https://example.com/deep/link 一文").count().latin_words == 0

    def test_markdown_link_keeps_label_drops_url(self):
        # 显示文字是给用户看的正文，URL 不是——只掩后者
        c = Document.parse("见 [course notes](https://example.com/notes) 一文").count()
        assert c.latin_words == 2

    def test_newlines_preserved_in_mask(self):
        doc = Document.parse("```\na\nb\nc\n```")
        assert doc.masked.count("\n") == doc.normalized.count("\n")

    def test_backtick_inside_code_block_does_not_pair_outside(self):
        doc = Document.parse("```\nlet s = `tpl`\n```\n\n正文 word 保留")
        assert doc.count().latin_words == 1


# ==========================================================================
# 5. Document API
# ==========================================================================


class TestDocumentApi:
    @pytest.mark.parametrize("raw", ["", "   ", "\n\n\t\n", "```\nprint(1)\n```", "   \n\n$$x$$"])
    def test_is_blank_for_no_countable_content(self, raw):
        # 决策：这类样本必须由上层标为无效，返回 0.0 会被误读成「纯英文」
        assert Document.parse(raw).is_blank is True

    @pytest.mark.parametrize("raw", ["中文", "english", "123", "# 标题"])
    def test_is_not_blank(self, raw):
        assert Document.parse(raw).is_blank is False

    def test_countable_excludes_code_includes_prose_kinds(self):
        doc = Document.parse("# 标题\n\n正文。\n\n- 列表\n\n> 引用\n\n| 表 |\n\n```\ncode\n```")
        assert {b.kind for b in doc.countable_blocks()} == {
            BlockKind.HEADING,
            BlockKind.PARAGRAPH,
            BlockKind.LIST,
            BlockKind.QUOTE,
            BlockKind.TABLE,
        }

    def test_paragraph_blocks_exclude_heading_quote_table(self):
        doc = Document.parse("# 标题\n\n正文。\n\n- 列表\n\n> 引用\n\n| 表 |")
        assert {b.kind for b in doc.paragraph_blocks()} == {BlockKind.PARAGRAPH, BlockKind.LIST}

    def test_blocks_of_filters(self):
        doc = Document.parse("# 标题\n\n正文。")
        assert len(doc.blocks_of(BlockKind.HEADING)) == 1
        assert len(doc.blocks_of(BlockKind.CODE)) == 0

    def test_line_of(self):
        doc = Document.parse("第一行\n第二行\n第三行")
        assert doc.line_of(0) == 1
        assert doc.line_of(doc.normalized.index("第二行")) == 2
        assert doc.line_of(doc.normalized.index("第三行")) == 3

    def test_is_masked(self):
        doc = Document.parse("正文 `code` 结束")
        assert doc.is_masked(doc.normalized.index("code")) is True
        assert doc.is_masked(0) is False
        assert doc.is_masked(-1) is False
        assert doc.is_masked(10**6) is False

    def test_preview_strips_mask_and_collapses_whitespace(self):
        doc = Document.parse("引用   开始 `code`   结束")
        assert doc.preview(0, len(doc.normalized)) == "引用 开始 结束"

    def test_preview_truncates(self):
        doc = Document.parse("一" * 100)
        preview = doc.preview(0, len(doc.normalized), width=10)
        assert preview == "一" * 10 + "…"

    def test_raw_preserved(self):
        raw = "全角（括号）\r\n第二行"
        doc = Document.parse(raw)
        assert doc.raw == raw
        assert doc.normalized != raw


# ==========================================================================
# 6. 其他共享工具
# ==========================================================================


class TestHelpers:
    def test_strip_mask(self):
        assert strip_mask(f"a{MASK}b{MASK}") == "ab"

    def test_strip_quote_markers(self):
        assert strip_quote_markers("> 第一行\n> 第二行") == "第一行\n第二行"

    def test_strip_quote_markers_without_space(self):
        assert strip_quote_markers(">紧贴引用") == "紧贴引用"

    def test_strip_quote_markers_nested(self):
        assert strip_quote_markers("> > 嵌套") == "> 嵌套"

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("第一句。第二句。", 2),
            ("第一句！第二句？第三句；", 3),
            ("One. Two! Three?", 3),
            ("没有句末标点", 1),
            ("", 0),
            ("   \n  ", 0),
            ("第一行\n第二行", 2),
            ("他说：「这样对吗？」然后走了。", 2),
        ],
    )
    def test_split_sentences_count(self, text, expected):
        assert len(split_sentences(text)) == expected

    @pytest.mark.parametrize("text", ["学习率 0.01 很小。", "误差降到 3.14 以下。"])
    def test_decimal_point_does_not_split(self, text):
        # 英文句号只在「后接空白或文末、且前面不是数字」时才算句末
        assert len(split_sentences(text)) == 1

    def test_split_sentences_offsets(self):
        text = "第一句。第二句。"
        spans = split_sentences(text)
        for span in spans:
            assert text[span.start : span.end] == span.text

    def test_split_sentences_offset_shift(self):
        spans = split_sentences("第一句。", offset=100)
        assert spans[0].start == 100
