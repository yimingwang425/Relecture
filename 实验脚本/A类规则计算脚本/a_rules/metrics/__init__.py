"""四个 A 类维度。签名一致、无状态、可独立调用。"""

from .chinese_ratio import chinese_ratio
from .paragraph_length import avg_paragraph_length
from .quote_length import max_quote_length
from .term_annotation import term_annotation_rate

__all__ = [
    "chinese_ratio",
    "term_annotation_rate",
    "avg_paragraph_length",
    "max_quote_length",
]
