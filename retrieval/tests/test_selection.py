"""选型结论与 SQL 的一致性守卫。

维度是个跨文件重复的数：Python 侧的选型常量、SQL 的列定义、SQL 的检索函数参数、
SQL 的 ANN 索引可行性。**改选型时漏改任何一处，都要等到接线才报错。**
这几条断言让漏改在测试阶段就变红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from retrieval.selection import (
    PGVECTOR_ANN_MAX_DIM,
    SELECTED_DIMENSIONS,
    SELECTED_MODE,
    SELECTED_MODEL,
    SELECTED_VARIANT,
)

SQL = Path(__file__).resolve().parents[1] / "sql" / "001_slices.sql"


@pytest.fixture(scope="module")
def sql_text() -> str:
    return SQL.read_text(encoding="utf-8")


def test_embedding_column_matches_selection(sql_text):
    dims = re.findall(r"embedding\s+vector\((\d+)\)", sql_text)
    assert dims, "未找到 embedding 列定义"
    assert all(int(d) == SELECTED_DIMENSIONS for d in dims), (
        f"SQL 的 embedding 维度 {dims} 与选型 {SELECTED_DIMENSIONS} 不一致"
    )


def test_match_function_param_matches_selection(sql_text):
    params = re.findall(r"p_query\s+vector\((\d+)\)", sql_text)
    assert params, "未找到 match_slices 的 p_query 参数"
    assert all(int(p) == SELECTED_DIMENSIONS for p in params), (
        f"检索函数参数维度 {params} 与选型 {SELECTED_DIMENSIONS} 不一致"
    )


def test_ann_index_only_when_dimension_allows(sql_text):
    """维度超过 pgvector 的 ANN 上限时，hnsw 索引建不出来，必须已被删掉。"""
    has_index = "using hnsw" in sql_text
    if SELECTED_DIMENSIONS > PGVECTOR_ANN_MAX_DIM:
        assert not has_index, (
            f"选型 {SELECTED_DIMENSIONS} 维超过 pgvector ANN 上限 {PGVECTOR_ANN_MAX_DIM}，"
            "SQL 里的 hnsw 索引会建失败，须删掉并接受精确扫描"
        )
    else:
        assert has_index, "维度在上限内却没有建 ANN 索引"


def test_selection_is_a_real_openai_model(sql_text):
    assert SELECTED_MODEL in ("text-embedding-3-small", "text-embedding-3-large")
    if SELECTED_MODEL.endswith("small"):
        assert SELECTED_DIMENSIONS <= 1536
    else:
        assert SELECTED_DIMENSIONS <= 3072


def test_no_stale_dimension_literals(sql_text):
    """SQL 里不该残留其他维度的字面量——它们是漏改的痕迹。"""
    stale = {
        d for d in re.findall(r"vector\((\d+)\)", sql_text) if int(d) != SELECTED_DIMENSIONS
    }
    assert not stale, f"SQL 里残留了其他维度 {stale}"


def test_selected_variant_and_mode_are_valid():
    from retrieval.compose import VARIANTS
    from retrieval.search import MODES

    assert SELECTED_VARIANT in VARIANTS
    assert SELECTED_MODE in MODES
