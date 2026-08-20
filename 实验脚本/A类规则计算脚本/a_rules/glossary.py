"""术语表：加载、校验、匹配。

只负责「在一段文本里找出术语出现在哪」，不负责判断标注是否合规——
后者是 `metrics/term_annotation.py` 的事。计数口径（first / all）同理，
本模块一律返回全部命中，由维度模块按配置折叠。

匹配对象是**掩码文本**（`Document.masked`）。掩码字符 U+0000 不匹配任何
一条模式，代码块、行内代码、公式、URL 里的术语因此自动不计入——调用方
传掩码串即可，本模块无需知道掩码规则。

三条来自判据的硬约束（`examples/glossary_criteria.md`）：

- **R3**：`zh` 第一项是首选译名，其余为可接受变体。匹配时全部接受，
  展示时只取第一项。
- **G2-en / G2-zh**：歧义分英文侧与中文侧分别判定。`match_anchor` 指明
  该条用哪一侧做锚点——这是正确性前提而非优化：`block` 若用英文锚点，
  「基本构成单元（building block）」会被误计入分母，压低标注率。
- **R1**：有日常义的裸形进 `unsafe_aliases`，一律不参与匹配。

决策日期：2026-08-19
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from .text import normalize

__all__ = [
    "GlossaryError",
    "MatchAnchor",
    "MatchSide",
    "TermSource",
    "Term",
    "TermMatch",
    "Glossary",
]


class GlossaryError(ValueError):
    """术语表非法。与配置一样，在加载时抛出，不留到度量阶段。"""


class MatchAnchor(str, Enum):
    """用哪一侧做匹配锚点（判据 G2-en / G2-zh）。"""

    EN = "en"
    ZH = "zh"
    BOTH = "both"


class MatchSide(str, Enum):
    """一次命中来自哪一侧。"""

    EN = "en"
    ZH = "zh"


class TermSource(str, Enum):
    """术语在课件中的出处（口径 D29）。

    用于分开报告两个数：术语表相对课件的**覆盖缺口**，与讲解文本相对
    术语表的**分母缺口**。仅见于图注的术语正文未出现，模型讲解时大概率
    也不会提，本就进不了分母——两者不可混为一谈。
    """

    BODY_TEXT = "body_text"
    FIGURE_CAPTION = "figure_caption"


_ALLOWED_TERM_KEYS = {
    "en", "zh", "aliases", "unsafe_aliases", "accepted_annotations",
    "match_anchor", "source", "page", "note", "origin",
}
_ALLOWED_ROOT_KEYS = {
    "terms", "course", "course_title", "source_file", "criteria",
    "criteria_version", "language", "coverage_note", "schema_note",
    "version", "frozen", "frozen_at", "changelog",
}


def _clean_forms(values: Any, where: str) -> tuple[str, ...]:
    """归一化一组字面形式，去重且保序。

    必须走 `text.normalize`——文档侧已经归一化过，术语表若不归一，
    全角括号、零宽字符、NFKC 差异都会造成静默失配。这类失配不会报错，
    只会让命中数变少，是会直接改变指标数值的一类错误。
    """
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise GlossaryError(f"{where} 必须是字符串或字符串列表，得到 {values!r}")
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            raise GlossaryError(f"{where} 的元素必须是字符串，得到 {v!r}")
        form = normalize(v).strip()
        if not form:
            raise GlossaryError(f"{where} 含空字符串")
        if form not in out:
            out.append(form)
    return tuple(out)


def _canonical_en(value: str) -> str:
    r"""英文形式的比较用规范形：空白与连字符折叠为单空格，大小写不敏感。

    与 `_english_pattern_for` 的 `[\s-]+` 规则保持一致——两处若不一致，
    自由文本能匹配到的形式在括注里核对不上，反之亦然。
    """
    return re.sub(r"[\s\-]+", " ", normalize(value).strip()).casefold()


def _canonical_zh(value: str) -> str:
    return re.sub(r"\s+", "", normalize(value).strip())


@dataclass(frozen=True, slots=True)
class Term:
    """一条术语。"""

    en: str
    zh: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    unsafe_aliases: tuple[str, ...] = ()
    #: 只在括注内被接受的额外形式，**永不参与自由文本匹配**。
    #: 匹配侧受 match_anchor 约束，括注侧用本字段，两侧分属不同机制。
    #: 逐条显式列举，不做「接受中心词」的通用规则——通用规则会让
    #: `descent` 通过 `梯度下降` 的检查。
    accepted_annotations: tuple[str, ...] = ()
    match_anchor: MatchAnchor = MatchAnchor.BOTH
    source: TermSource = TermSource.BODY_TEXT
    page: int | None = None
    note: str | None = None

    @property
    def preferred_zh(self) -> str | None:
        """首选译名（判据 R3）。展示与生成一律用它。"""
        return self.zh[0] if self.zh else None

    @property
    def has_zh(self) -> bool:
        """无中文译名时 `translate_only` 策略无法判定，需降级处理。"""
        return bool(self.zh)

    def en_forms(self) -> tuple[str, ...]:
        """参与匹配的英文形式。`unsafe_aliases` 一律排除（判据 R1）。"""
        if self.match_anchor is MatchAnchor.ZH:
            return ()
        return (self.en, *self.aliases)

    def zh_forms(self) -> tuple[str, ...]:
        if self.match_anchor is MatchAnchor.EN:
            return ()
        return self.zh

    def is_en_form(self, candidate: str) -> bool:
        """candidate 是否为本术语的某个英文形式。**不受 match_anchor 约束。**

        用于括注内容核对：`区块（block）` 里的 `block` 处在括号中，没有歧义，
        而 `match_anchor` 治的是自由文本里的歧义。若这里也受 anchor 约束，
        anchor=zh 的术语将永远无法被判定为「已标注」——每次正确标注都会被
        判成违规，是方向相反的系统性误判。

        `unsafe_aliases` 在此**计入**：unsafe 的含义是「不可作自由文本锚点」，
        不是「不是合法形式」。括注里出现它同样算标注到位。
        """
        target = _canonical_en(candidate)
        return any(
            _canonical_en(f) == target
            for f in (self.en, *self.aliases, *self.unsafe_aliases)
        )

    def is_zh_form(self, candidate: str) -> bool:
        """candidate 是否为本术语的某个中文形式。同样不受 match_anchor 约束。"""
        target = _canonical_zh(candidate)
        return any(_canonical_zh(f) == target for f in self.zh)

    def accepts_annotation(self, content: str, side: "MatchSide") -> bool:
        """括注内容是否被本术语接受。

        `side` 是被标注方所在的一侧：中文术语后跟括注 → 括注内应为英文形式，
        反之亦然。`accepted_annotations` 在两个方向上都接受，因为它记的是
        「这个术语可以被标注成什么」，与方向无关。
        """
        if side is MatchSide.ZH and self.is_en_form(content):
            return True
        if side is MatchSide.EN and self.is_zh_form(content):
            return True
        en_target, zh_target = _canonical_en(content), _canonical_zh(content)
        return any(
            _canonical_en(f) == en_target or _canonical_zh(f) == zh_target
            for f in self.accepted_annotations
        )

    @classmethod
    def from_dict(cls, data: Any, index: int) -> "Term":
        where = f"terms[{index}]"
        if isinstance(data, str):
            # 降级输入：纯字符串列表。只有英文形，translate_only 无法判定。
            return cls(en=normalize(data).strip())
        if not isinstance(data, dict):
            raise GlossaryError(f"{where} 必须是对象或字符串，得到 {data!r}")

        unknown = set(data) - _ALLOWED_TERM_KEYS
        if unknown:
            raise GlossaryError(
                f"{where} 出现未知字段 {sorted(unknown)}；"
                f"可用字段：{sorted(_ALLOWED_TERM_KEYS)}"
            )
        if "en" not in data:
            raise GlossaryError(f"{where} 缺少 en")

        en = _clean_forms(data["en"], f"{where}.en")
        if len(en) != 1:
            raise GlossaryError(f"{where}.en 必须是单个字符串")

        anchor = data.get("match_anchor", MatchAnchor.BOTH.value)
        try:
            match_anchor = MatchAnchor(anchor)
        except ValueError:
            allowed = ", ".join(m.value for m in MatchAnchor)
            raise GlossaryError(
                f"{where}.match_anchor 必须是 {allowed} 之一，得到 {anchor!r}"
            ) from None

        source = data.get("source", TermSource.BODY_TEXT.value)
        try:
            term_source = TermSource(source)
        except ValueError:
            allowed = ", ".join(m.value for m in TermSource)
            raise GlossaryError(
                f"{where}.source 必须是 {allowed} 之一，得到 {source!r}"
            ) from None

        zh = _clean_forms(data.get("zh"), f"{where}.zh")
        aliases = _clean_forms(data.get("aliases"), f"{where}.aliases")
        unsafe = _clean_forms(data.get("unsafe_aliases"), f"{where}.unsafe_aliases")
        accepted = _clean_forms(data.get("accepted_annotations"), f"{where}.accepted_annotations")

        overlap = set(aliases) & set(unsafe)
        if overlap:
            raise GlossaryError(
                f"{where} 的 {sorted(overlap)} 同时出现在 aliases 与 unsafe_aliases；"
                f"unsafe 的含义是不参与匹配，两者互斥"
            )
        if en[0] in unsafe:
            raise GlossaryError(f"{where}.en 不能同时列入 unsafe_aliases")
        if match_anchor is MatchAnchor.ZH and not zh:
            raise GlossaryError(
                f"{where} 的 match_anchor 是 zh 但没有 zh 形式，该条永远无法命中"
            )

        page = data.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int)):
            raise GlossaryError(f"{where}.page 必须是整数或 null，得到 {page!r}")

        return cls(
            en=en[0],
            zh=zh,
            aliases=aliases,
            unsafe_aliases=unsafe,
            accepted_annotations=accepted,
            match_anchor=match_anchor,
            source=term_source,
            page=page,
            note=data.get("note"),
        )


@dataclass(frozen=True, slots=True)
class TermMatch:
    """一次命中。`start`/`end` 是传入文本上的偏移量，可直接回原文取证。"""

    term: Term
    start: int
    end: int
    text: str
    side: MatchSide


def _english_pattern_for(form: str) -> str:
    """英文形式转正则片段。

    空白与连字符统一为 `[\\s-]+`：模型输出可能把 `peer-to-peer` 写成
    `peer to peer`，也可能因换行把 `gradient descent` 断成两行。
    不做这层兼容会造成静默漏匹配——命中数变少，指标随之偏移。
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", form) if p]
    return r"[\s\-]+".join(parts)


def _needs_word_boundary(form: str) -> bool:
    """首尾是字母数字时才加 `\\b`；`C++` 这类符号结尾加了永远匹配不上。"""
    return bool(form) and form[0].isalnum() and form[-1].isalnum()


def _build_pattern(forms: Sequence[tuple[str, Term]], side: MatchSide) -> re.Pattern[str] | None:
    """把一侧的全部形式编成一条正则。

    按长度降序排列是关键：正则的 alternation 在每个位置按顺序尝试，
    长的排前面即得到最长优先匹配，`steam turbine` 不会被 `turbine` 截断，
    `blockchain` 不会被 `block` 截断。`finditer` 从上次命中末尾继续，
    区间天然不重叠，无需后处理。
    """
    if not forms:
        return None
    ordered = sorted(forms, key=lambda x: (-len(x[0]), x[0]))
    branches = []
    for i, (form, _) in enumerate(ordered):
        if side is MatchSide.EN:
            body = _english_pattern_for(form)
            if _needs_word_boundary(form):
                body = rf"\b{body}\b"
        else:
            body = re.escape(form)
        branches.append(f"(?P<g{i}>{body})")
    flags = re.IGNORECASE if side is MatchSide.EN else 0
    return re.compile("|".join(branches), flags)


@dataclass(frozen=True, slots=True)
class Glossary:
    """一门课的术语表。"""

    terms: tuple[Term, ...] = ()
    course: str | None = None
    course_title: str | None = None
    criteria_version: str | None = None
    #: 术语表版本。所有实验输出必须记录它——§8.2 期间术语表冻结，
    #: 没有版本号就无法证明二十轮用的是同一份表。
    version: str | None = None
    frozen: bool = False

    _en_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False, compare=False)
    _zh_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False, compare=False)
    _en_index: tuple[Term, ...] = field(default=(), init=False, repr=False, compare=False)
    _zh_index: tuple[Term, ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        seen: dict[str, int] = {}
        for i, term in enumerate(self.terms):
            key = term.en.casefold()
            if key in seen:
                raise GlossaryError(f"术语 {term.en!r} 重复出现（terms[{seen[key]}] 与 terms[{i}]）")
            seen[key] = i

        en_forms = [(f, t) for t in self.terms for f in t.en_forms()]
        zh_forms = [(f, t) for t in self.terms for f in t.zh_forms()]
        en_ordered = sorted(en_forms, key=lambda x: (-len(x[0]), x[0]))
        zh_ordered = sorted(zh_forms, key=lambda x: (-len(x[0]), x[0]))

        object.__setattr__(self, "_en_pattern", _build_pattern(en_forms, MatchSide.EN))
        object.__setattr__(self, "_zh_pattern", _build_pattern(zh_forms, MatchSide.ZH))
        object.__setattr__(self, "_en_index", tuple(t for _, t in en_ordered))
        object.__setattr__(self, "_zh_index", tuple(t for _, t in zh_ordered))

    # ---------------- 查询 ----------------

    @property
    def is_empty(self) -> bool:
        """术语表为空 → 术语维度不激活（PRD §8.1）。"""
        return not self.terms

    def __len__(self) -> int:
        return len(self.terms)

    def __iter__(self) -> Iterable[Term]:
        return iter(self.terms)

    def by_source(self, source: TermSource) -> tuple[Term, ...]:
        return tuple(t for t in self.terms if t.source is source)

    def stats(self) -> dict[str, Any]:
        """按来源分列的条数，供两个数分开报告（口径 D29）。"""
        body = len(self.by_source(TermSource.BODY_TEXT))
        figure = len(self.by_source(TermSource.FIGURE_CAPTION))
        return {
            "version": self.version,
            "frozen": self.frozen,
            "total": len(self.terms),
            "body_text": body,
            "figure_caption": figure,
            "without_zh": sum(1 for t in self.terms if not t.has_zh),
            "anchor_en_only": sum(1 for t in self.terms if t.match_anchor is MatchAnchor.EN),
            "anchor_zh_only": sum(1 for t in self.terms if t.match_anchor is MatchAnchor.ZH),
        }

    def find(self, text: str) -> tuple[TermMatch, ...]:
        """找出全部命中，按位置排序。

        传 `Document.masked` 即可让代码块/公式/URL 里的术语自动不计入。
        不折叠重复——计数口径（first / all）由维度模块按配置决定。
        """
        if not text or self.is_empty:
            return ()
        matches: list[TermMatch] = []
        for pattern, index, side in (
            (self._en_pattern, self._en_index, MatchSide.EN),
            (self._zh_pattern, self._zh_index, MatchSide.ZH),
        ):
            if pattern is None:
                continue
            for m in pattern.finditer(text):
                term = index[int(m.lastgroup[1:])]  # type: ignore[union-attr]
                matches.append(
                    TermMatch(term=term, start=m.start(), end=m.end(), text=m.group(), side=side)
                )
        matches.sort(key=lambda x: (x.start, -x.end))
        return tuple(matches)

    # ---------------- 加载 ----------------

    @classmethod
    def from_terms(cls, terms: Iterable[Term], **meta: Any) -> "Glossary":
        return cls(terms=tuple(terms), **meta)

    @classmethod
    def from_list(cls, data: Sequence[Any]) -> "Glossary":
        """从条目列表加载。元素可以是对象，也可以是纯字符串（降级输入）。"""
        return cls(terms=tuple(Term.from_dict(item, i) for i, item in enumerate(data)))

    @classmethod
    def from_dict(cls, data: Any) -> "Glossary":
        if data is None:
            return cls()
        if isinstance(data, list):
            return cls.from_list(data)
        if not isinstance(data, dict):
            raise GlossaryError(f"术语表必须是对象或列表，得到 {type(data).__name__}")
        unknown = set(data) - _ALLOWED_ROOT_KEYS
        if unknown:
            raise GlossaryError(
                f"术语表根出现未知字段 {sorted(unknown)}；可用字段：{sorted(_ALLOWED_ROOT_KEYS)}"
            )
        if "terms" not in data:
            raise GlossaryError("术语表缺少 terms")
        if not isinstance(data["terms"], list):
            raise GlossaryError("terms 必须是列表")
        return cls(
            terms=tuple(Term.from_dict(item, i) for i, item in enumerate(data["terms"])),
            course=data.get("course"),
            course_title=data.get("course_title"),
            criteria_version=data.get("criteria_version"),
            version=data.get("version"),
            frozen=bool(data.get("frozen", False)),
        )

    @classmethod
    def from_json(cls, text: str) -> "Glossary":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GlossaryError(f"术语表不是合法 JSON：{exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: str | Path) -> "Glossary":
        p = Path(path)
        if not p.is_file():
            raise GlossaryError(f"术语表文件不存在：{p}")
        return cls.from_json(p.read_text(encoding="utf-8"))
