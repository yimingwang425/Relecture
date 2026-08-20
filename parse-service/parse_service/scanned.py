"""扫描件判据（逐页）。

**本模块不 import PyMuPDF**：输入是一组已经测好的数字，输出是页类型。
规则因此可以脱离真实文件单测，也可以在阈值调整后对历史 evidence 重算，
不必重新解析文件。

## 判据为什么是「不着墨文本占比」

任务最初给的判据是「内嵌字体数 ≈ 0 且存在铺满版面的全页图」。
在 4 份 OCW 课件共 123 页上实测，**这两个信号都不可判别**：

- **图像覆盖率**：扫描页落在 [0.775, 0.844]，非扫描页落在 [0.000, 0.865]。
  非扫描侧的最大值比扫描侧的最大值还高，区间完全被包住，不存在能分开的阈值。
  机制原因是扫描图有白边——15.316 的扫描图落版矩形 (53.2, 75.2)–(805.9, 621.0)
  在 805×621 的页面上只覆盖 0.82，够不到「铺满」；而讲义里贴一张满宽配图同样能到 0.8+。
- **内嵌字体数**：123 页中 45 页为 0，其中仅 19 页属扫描件，精确率 42%。
  base-14 标准字体（Helvetica / Times-Roman / ArialMT）按 PDF 规范本就不内嵌，
  而 15.316 的 OCR 层用的正是 Helvetica / Times-Roman。

**能判别的是不着墨文本占比**：15.316 的 17 个有文本页全部为 1.000，
其余 104 个有文本页最大 0.066，间隔宽 0.934。

这不是巧合而是机制性的：OCR 工具把识别出的文字用 **Tr 3（不可见渲染模式）**
叠在扫描图上——让人看到的是图、让机器搜到的是字。这是 OCR 文本层的定义性特征。

## 已知漏判方向

见 README 口径表第一条：**白字 / 裁剪路径实现的 OCR 层本判据漏判**，
且当前判据的实测基础只有 **1 份文件、1 个 OCR 工具（Acrobat Capture 3.0）**。
``PageEvidence.white_fill_text_ratio`` 为此留了观测字段，但**不接入规则**。

决策日期：2026-08-20
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SCAN_RULESET_VERSION",
    "INVISIBLE_TEXT_RATIO_MIN",
    "R1_COVERAGE_MIN",
    "R2_COVERAGE_MIN",
    "TEXT_CHAR_MIN",
    "PageSignals",
    "classify_page",
    "document_verdict",
]

#: 扫描件判据口径版本。下方任一阈值或 ``classify_page`` 的逻辑改动都必须同步改这个数。
SCAN_RULESET_VERSION = "scan-1.0"

#: R1 的判别项：不着墨文本字符占比下限。
#: 来源：**实测依据**（2026-08-20，4 份 OCW 共 123 页）。
#: 非扫描侧最大 0.066，扫描侧最小 1.000，间隔宽 0.934。取 0.80 落在间隔内偏扫描侧，
#: 为「扫描页夹带少量数字文本（页码、水印）」留余量。
INVISIBLE_TEXT_RATIO_MIN = 0.80

#: R1 的佐证项：图像并集覆盖率下限。
#: 来源：**实测依据（弱，非判别项）**。扫描页实测 [0.775, 0.844]，0.50 远在下界之下。
#: 它**不是判别器**——判别全靠不着墨文本占比；它只挡「整页不着墨文本但根本没有图」
#: 的畸形 PDF。刻意不取 0.775 贴边：该维度类间重叠已被证明，贴边等于把一个
#: 不可判别的量当判别器用。
R1_COVERAGE_MIN = 0.50

#: R2（图页）的覆盖率下限。来源：**占位待定**。
#: 它决定「一张图配一行标题的幻灯片」算不算可用。
#: 确定路径：第三步的图注标注集建成后，按「跳过该页是否丢失术语」反推。
R2_COVERAGE_MIN = 0.25

#: 「有可用文本」的字符数下限。来源：**占位待定**，同 R2_COVERAGE_MIN。
#: 现值只保证 15.316 p8/p14（0 字符）与 14.129 p33–p36（2 字符，内容是页码「33」）
#: 被判不可用，这 6 页确实没有可用文本。
TEXT_CHAR_MIN = 10


@dataclass(frozen=True, slots=True)
class PageSignals:
    """判据的输入。字段与 ``schema.PageEvidence`` 一一对应。"""

    image_coverage_union: float
    image_coverage_max: float
    invisible_text_ratio: float
    text_char_count: int
    embedded_font_count: int
    font_count: int
    white_fill_text_ratio: float  # 观测项，不参与判定


def classify_page(sig: PageSignals) -> tuple[str, str]:
    """返回 ``(kind, rule_fired)``。规则按序命中即停。

    R1 是唯一会给出「扫描件」结论的规则；R2/R3 给出的是「这页没有可用文本」，
    两者都导致 ``usable=false``，但**不可混为一谈**——
    R1 说的是「这页的文字是 OCR 噪声」，R2/R3 说的是「这页压根没有文字」。
    对文档的处置相同，对日后复盘漏判则完全不同。
    """
    has_text = sig.text_char_count >= TEXT_CHAR_MIN

    # R1 · OCR 文本层：有文本，但绝大多数文本不着墨，且页面被图覆盖
    if (
        sig.text_char_count > 0
        and sig.invisible_text_ratio >= INVISIBLE_TEXT_RATIO_MIN
        and sig.image_coverage_union >= R1_COVERAGE_MIN
    ):
        return "scanned_ocr", "R1_ocr_layer"

    # R2 · 图页：没有可用文本，但版面上有相当面积的图
    if not has_text and sig.image_coverage_union >= R2_COVERAGE_MIN:
        return "image_only", "R2_image_only"

    # R3 · 空页
    if not has_text:
        return "empty", "R3_empty"

    # R4 · 数字文本页
    return "digital_text", "R4_digital_text"


def document_verdict(kinds: list[str]) -> tuple[str, str, int, int]:
    """文档级结论：``(verdict, reason, usable_count, unusable_count)``。

    **只有两值。** 「可用页占比低于某个数就拒绝上传」是把产品决策塞进解析层——
    服务的职责是如实报告哪些页不可用，**放行并告知，不替用户拒绝**。
    UI 文案由调用方按 ``unusable_page_count`` 自行决定（PRD §10 的兜底文案）。
    """
    usable = sum(1 for k in kinds if k == "digital_text")
    unusable = len(kinds) - usable

    if usable == 0:
        counts: dict[str, int] = {}
        for k in kinds:
            counts[k] = counts.get(k, 0) + 1
        detail = "、".join(f"{v} 页 {k}" for k, v in sorted(counts.items()))
        return "unusable", f"无可用页（共 {len(kinds)} 页：{detail}）", usable, unusable

    if unusable == 0:
        return "usable", f"{usable} 页全部可用", usable, unusable

    return (
        "usable",
        f"{usable}/{len(kinds)} 页可用，{unusable} 页不可用（页号见 unusable_pages）",
        usable,
        unusable,
    )
