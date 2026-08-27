"""共用夹具。"""

from __future__ import annotations

import pytest

import explain  # noqa: F401  — 必须先于 a_rules，它注入 a_rules 的路径

from a_rules.config import StyleConfig
from explain.context import ContextSlice, SkippedPageRef
from explain.retrieval_adapter import InMemoryRetriever

#: 三维默认配置（v1）。跨语言，故 chinese_ratio 激活、term_annotation 不激活。
BILINGUAL = {"explain_language": "zh", "source_language": "en"}


@pytest.fixture
def config() -> StyleConfig:
    return StyleConfig.from_dict(dict(BILINGUAL))


SLICE_A = ContextSlice(
    source_file="MIT22_081JF10_lec01b.pdf",
    slice_id="s020",
    page_indices=(20,),
    title="REAL HEAT ENGINES",
    text="A heat engine converts thermal energy into mechanical work. "
    "The Carnot efficiency is eta = 1 - Te/Ti, where temperatures are absolute.",
    char_count=142,
    score=0.51,
)
SLICE_B = ContextSlice(
    source_file="MIT22_081JF10_lec01b.pdf",
    slice_id="s021",
    page_indices=(21,),
    title="WASTE HEAT",
    text="Waste heat is rejected to the environment through the condenser.",
    char_count=63,
    score=0.44,
)


@pytest.fixture
def retriever() -> InMemoryRetriever:
    return InMemoryRetriever(
        default_slices=(SLICE_A, SLICE_B),
        skipped_pages=(
            SkippedPageRef(
                source_file="MIT22_081JF10_lec01b.pdf",
                index=33,
                kind="image_only",
                reason="不着墨文本占比过低",
            ),
        ),
    )
