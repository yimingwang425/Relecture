"""扫描件判据的规则测试。

刻意**不碰真实文件**：规则的输入是一组数字，边界行为应当能脱离 PDF 单独钉住。
真实语料上的回归在 ``test_corpus.py``。
"""

from __future__ import annotations

import pytest

from parse_service.scanned import (
    INVISIBLE_TEXT_RATIO_MIN,
    R1_COVERAGE_MIN,
    R2_COVERAGE_MIN,
    TEXT_CHAR_MIN,
    PageSignals,
    classify_page,
    document_verdict,
)


def signals(**kw) -> PageSignals:
    base = dict(
        image_coverage_union=0.0,
        image_coverage_max=0.0,
        invisible_text_ratio=0.0,
        text_char_count=500,
        embedded_font_count=2,
        font_count=3,
        white_fill_text_ratio=0.0,
    )
    base.update(kw)
    return PageSignals(**base)


class TestR1ScannedOcr:
    def test_ocr_layer_page(self):
        """15.316 的典型页：全部文本不着墨 + 大面积图。"""
        kind, rule = classify_page(
            signals(invisible_text_ratio=1.0, image_coverage_union=0.82, text_char_count=70)
        )
        assert kind == "scanned_ocr" and rule == "R1_ocr_layer"

    def test_at_threshold_fires(self):
        kind, _ = classify_page(
            signals(
                invisible_text_ratio=INVISIBLE_TEXT_RATIO_MIN,
                image_coverage_union=R1_COVERAGE_MIN,
            )
        )
        assert kind == "scanned_ocr"

    def test_just_below_invisible_threshold_does_not_fire(self):
        kind, _ = classify_page(
            signals(invisible_text_ratio=INVISIBLE_TEXT_RATIO_MIN - 0.01, image_coverage_union=0.9)
        )
        assert kind == "digital_text"

    def test_invisible_text_without_image_is_not_scanned(self):
        """佐证项的职责：挡住「整页不着墨文本但根本没有图」的畸形 PDF。"""
        kind, _ = classify_page(signals(invisible_text_ratio=1.0, image_coverage_union=0.0))
        assert kind == "digital_text"

    def test_real_digital_page_with_large_figure_is_not_scanned(self):
        """14.129 p32 的实测值：覆盖率 0.865 高于任何扫描页，但文本是着墨的。

        这正是「铺满版面」不可判别的反例——覆盖率单独用会误判。
        """
        kind, _ = classify_page(
            signals(
                invisible_text_ratio=0.066,
                image_coverage_union=0.865,
                text_char_count=58,
                embedded_font_count=0,
            )
        )
        assert kind == "digital_text"

    def test_zero_embedded_fonts_alone_is_not_scanned(self):
        """11.201 是纯数字课件，却有 4 页内嵌字体数为 0（base-14 标准字体不内嵌）。"""
        kind, _ = classify_page(signals(embedded_font_count=0, text_char_count=445))
        assert kind == "digital_text"

    def test_white_fill_is_observation_only(self):
        """白字占比不接规则：深色底幻灯片的正常白字会让它有噪声（实测 14.129 有页达 0.919）。"""
        kind, _ = classify_page(
            signals(white_fill_text_ratio=0.919, image_coverage_union=0.71, text_char_count=37)
        )
        assert kind == "digital_text"


class TestR2R3NoUsableText:
    def test_image_only_page(self):
        """14.129 p33：覆盖率 0.755，全页只有一个页码「33」。"""
        kind, rule = classify_page(signals(text_char_count=2, image_coverage_union=0.755))
        assert kind == "image_only" and rule == "R2_image_only"

    def test_scan_page_without_text_layer(self):
        """15.316 p8/p14：无任何文本，覆盖率 0.44 / 0.36。

        R1 因无文本不触发，由 R2 接住——同样 usable=false，误差方向安全。
        """
        kind, rule = classify_page(signals(text_char_count=0, image_coverage_union=0.44))
        assert kind == "image_only" and rule == "R2_image_only"

    def test_empty_page(self):
        kind, rule = classify_page(signals(text_char_count=0, image_coverage_union=0.0))
        assert kind == "empty" and rule == "R3_empty"

    def test_text_char_min_boundary(self):
        assert classify_page(signals(text_char_count=TEXT_CHAR_MIN))[0] == "digital_text"
        assert classify_page(signals(text_char_count=TEXT_CHAR_MIN - 1))[0] == "empty"

    def test_r2_coverage_boundary(self):
        assert (
            classify_page(signals(text_char_count=0, image_coverage_union=R2_COVERAGE_MIN))[0]
            == "image_only"
        )
        assert (
            classify_page(
                signals(text_char_count=0, image_coverage_union=R2_COVERAGE_MIN - 0.01)
            )[0]
            == "empty"
        )

    def test_r1_wins_over_r2_when_both_could_match(self):
        """少量不着墨文本 + 大图：R1 先命中，结论是「OCR 噪声」而非「没有文字」。

        两者都 usable=false，但日后复盘漏判时含义完全不同，不可混为一谈。
        """
        kind, rule = classify_page(
            signals(text_char_count=5, invisible_text_ratio=1.0, image_coverage_union=0.8)
        )
        assert kind == "scanned_ocr" and rule == "R1_ocr_layer"


class TestDocumentVerdict:
    def test_all_usable(self):
        verdict, reason, usable, unusable = document_verdict(["digital_text"] * 15)
        assert verdict == "usable" and usable == 15 and unusable == 0
        assert "15 页全部可用" in reason

    def test_no_usable_page(self):
        verdict, reason, usable, unusable = document_verdict(
            ["scanned_ocr"] * 17 + ["image_only"] * 2
        )
        assert verdict == "unusable" and usable == 0 and unusable == 19
        assert "17 页 scanned_ocr" in reason and "2 页 image_only" in reason

    def test_partially_unusable_is_still_usable(self):
        """**放行并告知，不替用户拒绝。** 服务不做「占比低于某数就拒绝」的产品决策。"""
        verdict, reason, usable, unusable = document_verdict(
            ["digital_text"] * 46 + ["image_only"] * 4
        )
        assert verdict == "usable" and usable == 46 and unusable == 4
        assert "46/50" in reason

    def test_single_usable_page_still_usable(self):
        """哪怕只有 1 页可用也放行——阈值一旦引入就是凭空造数。"""
        verdict, _, usable, _ = document_verdict(["digital_text"] + ["scanned_ocr"] * 99)
        assert verdict == "usable" and usable == 1

    def test_empty_document(self):
        verdict, _, usable, unusable = document_verdict([])
        assert verdict == "unusable" and usable == 0 and unusable == 0
