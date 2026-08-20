"""共享文本预处理层。

四个 A 类维度（中文字符占比 / 术语英文标注率 / 平均段落字数 / 单段最长引用字数）
全部建立在本模块之上，任何归一化、分块、掩码、字数口径都只在这里实现一次，
维度模块不得重复造轮子。

三条核心约定（口径理由与决策日期见 README）：

1. 归一化：换行统一 → 零宽字符剔除 → NFKC → 中文标点变体归一。
   之后所有偏移量（offset）均指向 ``Document.normalized``，而非用户传入的原始串。
2. 掩码：代码块、行内代码、数学公式、URL 一律替换为 ``MASK``（U+0000），
   长度与换行位置保持不变。因此掩码串与归一化串**逐字符对齐**，
   维度模块可以直接在掩码串上跑正则，再用同一组偏移量回原文取证。
3. 字数：汉字按字符计，连续英文串按 1 词计，连续数字串按 1 计，标点与空白不计。

决策日期：2026-08-19
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "MASK",
    "BlockKind",
    "Block",
    "CharCount",
    "Document",
    "Span",
    "normalize",
    "count_chars",
    "strip_mask",
    "strip_quote_markers",
    "split_sentences",
]

#: 掩码字符。选 U+0000 是因为它不会出现在任何正常讲解文本里，
#: 且不匹配汉字/英文/数字的任何一条计数正则，天然从所有统计中消失。
MASK = "\x00"


# --------------------------------------------------------------------------
# 1. 归一化
# --------------------------------------------------------------------------

#: 零宽与不可见字符：模型输出与 PDF 复制粘贴都可能带入，会打断术语字符串匹配。
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad]")

#: 行分隔符变体统一为 \n（NFKC 不处理这两个码位）。
_LINE_SEP_RE = re.compile("[\u2028\u2029]")

#: NFKC 处理不到的中文标点变体。NFKC 已负责全角→半角（）＝()、Ａ→A、１→1、
#: ，→,、！→! 等兼容分解，这里只补 NFKC 不覆盖的部分。
_PUNCT_TABLE = str.maketrans(
    {
        "﹁": "「",  # 竖排直角引号 → 标准直角引号
        "﹂": "」",
        "﹃": "『",
        "﹄": "』",
        "〝": "“",  # 竖排双引号 → 标准弯引号
        "〞": "”",
        "〟": "”",
        "―": "—",  # 破折号变体统一（U+2015 / U+2012 / U+2013 → U+2014）
        "‒": "—",
        "–": "—",
        "⋯": "...",  # NFKC 把 … 拆成 ...，此处对齐，避免两种省略号并存
    }
)


def normalize(raw: str) -> str:
    """把任意来源的文本压到唯一的规范形式。

    顺序不可调换：先统一换行（NFKC 不动 \\r），再剔除零宽字符（否则会残留在
    NFKC 结果里继续打断匹配），最后做 NFKC 与中文标点归一。
    """
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _LINE_SEP_RE.sub("\n", text)
    text = _INVISIBLE_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_PUNCT_TABLE)


# --------------------------------------------------------------------------
# 2. 字数口径
# --------------------------------------------------------------------------

#: 汉字：中日韩统一表意文字主区 + 扩展 A + 兼容表意文字 + 星平面扩展区。
#: 刻意**不含**日文假名（U+3040-30FF）与韩文谚文（U+AC00-D7AF）——
#: 它们同属 CJK 却不是中文，计入会虚高「中文字符占比」。
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")

#: 连续英文串按 1 词计；内部的连字符与撇号（state-of-the-art、don't）不切开。
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*")

#: 连续数字串按 1 计，小数点与千分位不切开。
_DIGIT_GROUP_RE = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True, slots=True)
class CharCount:
    """一段文本的字数构成。``total`` 即本项目统一口径下的「字数」。"""

    han: int = 0
    latin_words: int = 0
    digit_groups: int = 0

    @property
    def total(self) -> int:
        return self.han + self.latin_words + self.digit_groups

    def __add__(self, other: "CharCount") -> "CharCount":
        return CharCount(
            han=self.han + other.han,
            latin_words=self.latin_words + other.latin_words,
            digit_groups=self.digit_groups + other.digit_groups,
        )


def count_chars(text: str) -> CharCount:
    """按项目统一口径统计字数。掩码字符不匹配任何一条正则，自动被排除。"""
    if not text:
        return CharCount()
    return CharCount(
        han=len(_HAN_RE.findall(text)),
        latin_words=len(_LATIN_WORD_RE.findall(text)),
        digit_groups=len(_DIGIT_GROUP_RE.findall(text)),
    )


def strip_mask(text: str) -> str:
    """去掉掩码字符，用于生成人类可读的取证片段。"""
    return text.replace(MASK, "")


# --------------------------------------------------------------------------
# 3. 块级切分
# --------------------------------------------------------------------------


class BlockKind(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST = "list"
    QUOTE = "quote"
    CODE = "code"
    TABLE = "table"


#: 计入「中文字符占比」「术语标注率」的块：正文性质的内容。
COUNTABLE_KINDS: tuple[BlockKind, ...] = (
    BlockKind.PARAGRAPH,
    BlockKind.HEADING,
    BlockKind.LIST,
    BlockKind.QUOTE,
    BlockKind.TABLE,
)

#: 计入「平均段落字数」的块。标题是结构不是散文，表格是数据不是散文，
#: 两者都会把段落均值往下拽，故排除；连续列表项已在解析阶段合并为单块。
PARAGRAPH_KINDS: tuple[BlockKind, ...] = (BlockKind.PARAGRAPH, BlockKind.LIST)

_BLANK_RE = re.compile(r"^[ \t]*$")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_QUOTE_RE = re.compile(r"^ {0,3}>")
_LIST_RE = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_TABLE_RE = re.compile(r"^ {0,3}\|")
_INDENTED_CODE_RE = re.compile(r"^(?: {4,}|\t)[^ \t]")
_CONTINUATION_RE = re.compile(r"^[ \t]+\S")

#: 开启新块的结构性行——段落与列表遇到它们必须断开。
_STRUCTURAL = (_FENCE_OPEN_RE, _HEADING_RE, _QUOTE_RE, _TABLE_RE)


def _starts_structural(line: str) -> bool:
    return any(pattern.match(line) for pattern in _STRUCTURAL)


@dataclass(frozen=True, slots=True)
class Block:
    """一个块级片段。``start``/``end`` 是 ``Document.normalized`` 上的偏移量。"""

    kind: BlockKind
    start: int
    end: int
    line_no: int  # 1-indexed 起始行号，用于定位与取证
    text: str  # 归一化文本切片（原样，带 markdown 标记）
    masked: str  # 对应的掩码切片，与 text 逐字符等长

    def count(self) -> CharCount:
        return count_chars(self.masked)


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    text: str


def _line_spans(text: str) -> list[tuple[int, int]]:
    """返回每行的 (起始偏移, 结束偏移)，结束偏移不含换行符。"""
    spans: list[tuple[int, int]] = []
    pos = 0
    length = len(text)
    while pos <= length:
        nl = text.find("\n", pos)
        if nl == -1:
            spans.append((pos, length))
            break
        spans.append((pos, nl))
        pos = nl + 1
    return spans


def _segment(normalized: str) -> list[tuple[BlockKind, int, int, int]]:
    """把归一化文本切成 (kind, start, end, line_no) 列表。"""
    if not normalized:
        return []

    spans = _line_spans(normalized)
    lines = [normalized[s:e] for s, e in spans]
    n = len(lines)
    out: list[tuple[BlockKind, int, int, int]] = []

    i = 0
    prev_blank = True  # 文首等价于「前面有空行」
    while i < n:
        line = lines[i]
        if _BLANK_RE.match(line):
            prev_blank = True
            i += 1
            continue

        start = spans[i][0]
        line_no = i + 1

        fence = _FENCE_OPEN_RE.match(line)
        if fence:
            marker = fence.group(1)
            char, need = marker[0], len(marker)
            close = re.compile(rf"^ {{0,3}}{re.escape(char)}{{{need},}}[ \t]*$")
            j = i + 1
            # 未闭合的围栏一直延伸到文末——模型流式输出被截断时的常见形态，
            # 必须当作代码处理，否则半截代码会被计进中文占比。
            while j < n and not close.match(lines[j]):
                j += 1
            end = spans[j][1] if j < n else spans[n - 1][1]
            out.append((BlockKind.CODE, start, end, line_no))
            i = j + 1
            prev_blank = False
            continue

        if _HEADING_RE.match(line):
            out.append((BlockKind.HEADING, start, spans[i][1], line_no))
            i += 1
            prev_blank = False
            continue

        if _QUOTE_RE.match(line):
            j = i
            while j < n and _QUOTE_RE.match(lines[j]):
                j += 1
            out.append((BlockKind.QUOTE, start, spans[j - 1][1], line_no))
            i = j
            prev_blank = False
            continue

        if _TABLE_RE.match(line):
            j = i
            while j < n and _TABLE_RE.match(lines[j]):
                j += 1
            out.append((BlockKind.TABLE, start, spans[j - 1][1], line_no))
            i = j
            prev_blank = False
            continue

        if _LIST_RE.match(line):
            j, last = i + 1, i
            while j < n:
                nxt = lines[j]
                if _BLANK_RE.match(nxt):
                    # 空行只有在后面还接着列表项/缩进续行时才留在同一列表块内
                    k = j
                    while k < n and _BLANK_RE.match(lines[k]):
                        k += 1
                    if k < n and (_LIST_RE.match(lines[k]) or _CONTINUATION_RE.match(lines[k])):
                        j = k
                        continue
                    break
                if _starts_structural(nxt):
                    break
                last, j = j, j + 1
            out.append((BlockKind.LIST, start, spans[last][1], line_no))
            i = last + 1
            prev_blank = False
            continue

        if prev_blank and _INDENTED_CODE_RE.match(line):
            j = i
            last = i
            while j < n and (_INDENTED_CODE_RE.match(lines[j]) or _BLANK_RE.match(lines[j])):
                if not _BLANK_RE.match(lines[j]):
                    last = j
                j += 1
            out.append((BlockKind.CODE, start, spans[last][1], line_no))
            i = last + 1
            prev_blank = False
            continue

        j = i + 1
        while j < n and not _BLANK_RE.match(lines[j]) and not _starts_structural(lines[j]) and not _LIST_RE.match(lines[j]):
            j += 1
        out.append((BlockKind.PARAGRAPH, start, spans[j - 1][1], line_no))
        i = j
        prev_blank = False

    return out


# --------------------------------------------------------------------------
# 4. 掩码
# --------------------------------------------------------------------------

_INLINE_CODE_RE = re.compile(r"(`{1,3})[^\n]*?\1")
_MATH_BLOCK_RE = re.compile(r"\$\$[\s\S]+?\$\$")
_MATH_INLINE_RE = re.compile(r"\$[^$\n]+?\$")
_LINK_DEST_RE = re.compile(r"\]\(\s*([^()\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)\]<>\"'，。；]+")

#: 顺序有讲究：代码块先掩掉，其中的反引号/美元符号变成 MASK 后不再参与配对，
#: 避免块内符号与块外符号错误配对造成过度掩码。
_INLINE_MASKS: tuple[tuple[re.Pattern[str], int], ...] = (
    (_INLINE_CODE_RE, 0),
    (_MATH_BLOCK_RE, 0),
    (_MATH_INLINE_RE, 0),
    (_LINK_DEST_RE, 1),  # 只掩 URL，保留 [显示文字]
    (_BARE_URL_RE, 0),
)


def _mask_range(buf: list[str], start: int, end: int) -> None:
    """把 [start, end) 内的非换行字符替换为掩码，换行保留以维持行号。"""
    for k in range(start, end):
        if buf[k] != "\n":
            buf[k] = MASK


def _build_masked(normalized: str, blocks: list[tuple[BlockKind, int, int, int]]) -> str:
    buf = list(normalized)
    for kind, start, end, _ in blocks:
        if kind is BlockKind.CODE:
            _mask_range(buf, start, end)
    for pattern, group in _INLINE_MASKS:
        current = "".join(buf)
        for m in pattern.finditer(current):
            _mask_range(buf, m.start(group), m.end(group))
    return "".join(buf)


# --------------------------------------------------------------------------
# 5. Document
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Document:
    """预处理结果。维度模块的唯一输入。"""

    raw: str
    normalized: str
    masked: str
    blocks: tuple[Block, ...]

    @classmethod
    def parse(cls, raw: str) -> "Document":
        normalized = normalize(raw)
        segments = _segment(normalized)
        masked = _build_masked(normalized, segments)
        blocks = tuple(
            Block(
                kind=kind,
                start=start,
                end=end,
                line_no=line_no,
                text=normalized[start:end],
                masked=masked[start:end],
            )
            for kind, start, end, line_no in segments
        )
        return cls(raw=raw, normalized=normalized, masked=masked, blocks=blocks)

    def blocks_of(self, *kinds: BlockKind) -> tuple[Block, ...]:
        wanted = set(kinds)
        return tuple(b for b in self.blocks if b.kind in wanted)

    def countable_blocks(self) -> tuple[Block, ...]:
        return self.blocks_of(*COUNTABLE_KINDS)

    def paragraph_blocks(self) -> tuple[Block, ...]:
        return self.blocks_of(*PARAGRAPH_KINDS)

    def countable_masked(self) -> str:
        """可计量正文的掩码文本，块间以换行分隔。"""
        return "\n".join(b.masked for b in self.countable_blocks())

    def count(self) -> CharCount:
        return count_chars(self.countable_masked())

    @property
    def is_blank(self) -> bool:
        """没有任何可计量内容——空串、纯空白、或只有代码块。

        这类样本不能返回 0，必须由上层标记为无效，否则会被当成「纯英文」误判。
        """
        return self.count().total == 0

    def is_masked(self, pos: int) -> bool:
        return 0 <= pos < len(self.masked) and self.masked[pos] == MASK

    def line_of(self, pos: int) -> int:
        """偏移量对应的 1-indexed 行号。"""
        return self.normalized.count("\n", 0, max(pos, 0)) + 1

    def preview(self, start: int, end: int, width: int = 30) -> str:
        """取证片段：去掩码、压空白、超长截断。

        切的是掩码串而非归一化串——取证要展示的是「被计入统计的那部分内容」，
        代码、公式、URL 既然没参与计数，就不该出现在证据里。
        """
        snippet = " ".join(strip_mask(self.masked[start:end]).split())
        return snippet if len(snippet) <= width else snippet[:width] + "…"


# --------------------------------------------------------------------------
# 6. 其他共享工具
# --------------------------------------------------------------------------

_QUOTE_MARKER_RE = re.compile(r"^ {0,3}>[ \t]?", re.MULTILINE)


def strip_quote_markers(text: str) -> str:
    """剥掉 markdown 引用块的 ``>`` 前缀，保留内容与换行结构。"""
    return _QUOTE_MARKER_RE.sub("", text)


#: 英文句号只在「后接空白或文末、且前面不是数字」时才算句末，
#: 否则 0.01、e.g. 这类写法会被切碎。
_SENTENCE_END_RE = re.compile(
    r"(?:[。！？!?；;…]+|(?<![0-9])\.(?=\s|$))[”』」)）]*|\n+"
)


def split_sentences(text: str, offset: int = 0) -> tuple[Span, ...]:
    """按中英文句末标点与换行切句。

    当前四个维度都不需要它，这里先落地是为了 PRD §8.1 的「冗余句式」维度，
    以及 §9.2 方案 C 的句子级并行审查——两者都要求以句为单位增量调用。
    """
    spans: list[Span] = []
    pos = 0
    for m in _SENTENCE_END_RE.finditer(text):
        chunk = text[pos : m.end()]
        if chunk.strip():
            spans.append(Span(offset + pos, offset + m.end(), chunk))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        spans.append(Span(offset + pos, offset + len(text), tail))
    return tuple(spans)
