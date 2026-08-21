"""AI 课件讲解助手 · F2 第三步：术语提取。

每门课一次性提取。不做 F3 的审查接线，不做用户界面。

两条通道（PRD §8.1）：正文通道靠排版与词频信号；**图注通道独立实现**，
靠「无句法结构的孤立名词短语簇」——不靠 `image_adjacency`，
因为贡献全部金标准图注术语的页上一张位图都没有（图是矢量绘制）。
"""

from .extract import extract
from .schema import EXTRACTOR_VERSION, RULESET_VERSION, Glossary, Term

__all__ = ["extract", "Glossary", "Term", "EXTRACTOR_VERSION", "RULESET_VERSION"]
