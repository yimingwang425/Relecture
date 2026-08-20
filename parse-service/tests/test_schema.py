"""契约测试。schema 是 Python 与 TS 之间的接口，定错了两侧一起返工。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from parse_service.schema import (
    SCHEMA_VERSION,
    DocumentMeta,
    FontInfo,
    ImageAdjacency,
    Page,
    PageEvidence,
    ParseResult,
    ParserInfo,
    PdfSpan,
    PdfTextBlockOrigin,
    PptxShapeOrigin,
    TextBlock,
)


def _evidence(**kw) -> PageEvidence:
    base = dict(
        image_coverage_union=0.5,
        image_coverage_max=0.5,
        invisible_text_ratio=0.0,
        text_char_count=100,
        embedded_font_count=1,
        font_count=2,
        white_fill_text_ratio=0.0,
        rule_fired="R4_digital_text",
    )
    base.update(kw)
    return PageEvidence(**base)


def _block(origin) -> TextBlock:
    return TextBlock(
        block_id="p1-b1",
        page_index=1,
        order=1,
        text="Heat engine",
        bbox_pt=[0, 0, 10, 10],
        bbox_norm=[0, 0, 0.1, 0.1],
        font=FontInfo(resolved=True),
        image_adjacency=ImageAdjacency(),
        origin=origin,
    )


class TestStrictness:
    def test_unknown_field_rejected(self):
        """未知字段严格报错，不静默忽略——静默忽略意味着字段名拼错也能跑通。"""
        with pytest.raises(ValidationError):
            PageEvidence(**{**_evidence().model_dump(), "typo_field": 1})

    def test_models_are_frozen(self):
        with pytest.raises(ValidationError):
            _evidence().text_char_count = 5

    def test_bbox_must_have_four_numbers(self):
        payload = _block(PdfTextBlockOrigin(block_number=0, line_count=1, spans=[])).model_dump()
        with pytest.raises(ValidationError):
            TextBlock.model_validate({**payload, "bbox_pt": [0, 0, 10]})


class TestOriginUnion:
    """两种来源各自的原始信号必须都装得下，且不互相污染。"""

    def test_pdf_origin_roundtrip(self):
        span = PdfSpan(
            text="Heat engine",
            size_pt=13.06,
            font="NimbusSanL-Regu",
            flags=4,
            bold=False,
            italic=False,
            serif=True,
            mono=False,
            color="#ff0000",
            alpha=255,
            bbox_pt=[0, 0, 10, 10],
        )
        block = _block(PdfTextBlockOrigin(block_number=3, line_count=2, spans=[span]))
        restored = TextBlock.model_validate(json.loads(block.model_dump_json()))
        assert restored.origin.kind == "pdf_textblock"
        assert restored.origin.spans[0].font == "NimbusSanL-Regu"
        assert restored == block

    def test_pptx_origin_roundtrip(self):
        """PPTX 解析器本次未交付，但契约形状必须已经能容纳它。"""
        origin = PptxShapeOrigin(
            shape_id=5,
            shape_name="Content Placeholder 2",
            shape_type="PLACEHOLDER",
            is_placeholder=True,
            placeholder_type="BODY",
            placeholder_idx=1,
            from_layout=False,
            z_order=2,
            paragraph_index=0,
            list_level=1,
        )
        block = _block(origin)
        restored = TextBlock.model_validate(json.loads(block.model_dump_json()))
        assert restored.origin.kind == "pptx_shape"
        assert restored.origin.list_level == 1
        assert restored.origin.from_layout is False

    def test_discriminator_rejects_mismatched_payload(self):
        with pytest.raises(ValidationError):
            TextBlock.model_validate(
                {
                    **_block(
                        PdfTextBlockOrigin(block_number=0, line_count=1, spans=[])
                    ).model_dump(),
                    "origin": {"kind": "pptx_shape", "block_number": 1},
                }
            )


class TestFontResolution:
    def test_pptx_unresolved_font_is_distinguishable(self):
        """必须能区分「没加粗」与「不知道有没有加粗」。"""
        unknown = FontInfo(resolved=False)
        not_bold = FontInfo(bold=False, resolved=True)
        assert unknown.bold is None
        assert not_bold.bold is False
        assert unknown.bold is not not_bold.bold


class TestParseResult:
    def test_full_roundtrip(self):
        page = Page(
            index=1,
            size_pt=[805.0, 621.0],
            kind="digital_text",
            usable=True,
            evidence=_evidence(),
            blocks=[_block(PdfTextBlockOrigin(block_number=0, line_count=1, spans=[]))],
        )
        result = ParseResult(
            document=DocumentMeta(
                content_sha256="abc",
                source_format="pdf",
                filename="x.pdf",
                byte_size=1,
                page_count=1,
                parser=ParserInfo(
                    service_version="0.1.0",
                    pymupdf="1.28.2",
                    scan_ruleset_version="scan-1.0",
                    adjacency_ruleset_version="adj-1.0",
                ),
                usable_page_count=1,
                unusable_page_count=0,
                verdict="usable",
                verdict_reason="1 页全部可用",
            ),
            pages=[page],
        )
        assert ParseResult.model_validate(json.loads(result.model_dump_json())) == result
        assert result.schema_version == SCHEMA_VERSION

    def test_json_schema_exports(self):
        """TS 侧靠它生成类型，必须可导出且含两种 origin。"""
        schema = ParseResult.model_json_schema()
        defs = schema["$defs"]
        assert "PdfTextBlockOrigin" in defs
        assert "PptxShapeOrigin" in defs
        assert "PageEvidence" in defs
        json.dumps(schema)  # 必须可序列化
