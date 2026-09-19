"""风格配置 → 讲解 prompt 片段。**这是 F1 的接口形状。**

PRD §5.1 要求「配置以结构化参数存储，非自然语言描述（可程序化检查的前提）」。
F1 产出 `StyleConfig`，本模块把它渲染成模型能读的约束，审查侧则用同一份
`StyleConfig` 去判定——**同一个对象驱动两侧**，这就是「参数化注入」的含义。

## 口径 X05 · 配置同时驱动 prompt 与判定，两侧同步

配置扫描实验里这条必须写死，否则结论会被读错：

- 若**只收紧判定、不改 prompt**，触发率上升是**平凡结论**（判据变严了当然更容易违规）
- 两侧同步变化，测的才是真问题：**模型能不能遵守一个偏离常态的指令**

## 只渲染激活的维度

未激活的维度不进 prompt。理由不只是省 token——**要求模型做一件不计分的事，
既花钱又会污染归因**。v1 的术语维度已下线（PRD §3.2），
所以 prompt 里也不再要求术语标注；第 0 周那份 system prompt 里的
「专业术语首次出现时用『中文译名（English original）』标注」这一句，
在 v1 下必须删掉，否则模型会花输出 token 做一件审查根本不看的事。

## B 类三条轴也在这里渲染（2026-09-11 从 `f1_samples/` 搬上来）

原来本模块只渲染 A 类，B 类（举例 / 结构 / 深度）的措辞住在 `f1_samples/style_axes.py`，
理由是「**不判定的东西先不焊进生产模块**」（当时的约束 1）。

**那条理由只支持「不判定」，不支持「不进 prompt」**（口径 F45）。PRD §8.5 未校准
说的是不做 B 类**判定**；而 B 类不进 prompt 的后果是：F1 那 6 步成对比较里有 4 步
（举例 1 步、结构 1 步、深度 2 步）在采集一个对输出毫无影响的东西——
用户认真比较了四次，而那四次的结果从不改变他拿到的任何一个字。

⚠️ **搬家是移动，不是重写。** 措辞逐字节不变、拼接顺序（A + `"\n"` + B）也不变——
`pairs.json` 里 16 个样例就是按这个形状生成的（口径 F18/F19），
措辞或拼法一变，用户按样例选完拿到的就不是他选的那个。
`explain/tests/test_style_axes.py` 与 `web/scripts/verify_style_axes.mjs` 两处钉着这一点。

## 阈值不直接报给模型

`chinese_ratio ≥ 0.95` 这种数字对模型没有可操作性——它不会去数自己的汉字占比。
按阈值分档渲染成可执行的指令，档位与阈值的对应关系写死在本模块，
**随配置变化，但不是把配置字面量贴进 prompt**。

决策日期：2026-08-22
"""

from __future__ import annotations

from a_rules.config import StyleConfig

from .style_axes import AXES

__all__ = [
    "render_style_rules",
    "render_b_class_rules",
    "render_all_style_rules",
    "LANGUAGE_BANDS",
    "B_CLASS_FRAGMENTS",
    "TONES",
    "DEFAULT_TONE",
    "TONE_FRAGMENTS",
    "render_tone_rule",
    "PROMPT_TEMPLATE_VERSION",
    "PROMPT_TEMPLATE_DIGEST",
]

#: 讲解语言一致性的分档。阈值 → 指令强度。
#:
#: 分档是刻意的：模型无法执行「汉字占比不低于 0.95」，但能执行
#: 「除专有名词外不要出现英文」。档位边界取自 v1.0 默认值 0.85 与
#: 配置扫描实验的 P4 点 0.95，中间留一档。
LANGUAGE_BANDS: tuple[tuple[float, str], ...] = (
    (0.95, "全程用中文讲解。**除专有名词与公式符号外不要出现英文**，"
           "需要给出英文原文时用括号紧跟在中文之后，不要整句英文。"),
    (0.90, "全程用中文讲解。英文只在给出专业名词原文时出现，且用括号紧跟中文之后。"),
    (0.00, "用中文讲解。英文可以出现在专业名词和必要的原文引用里。"),
)


def _language_rule(threshold: float) -> str:
    for floor, text in LANGUAGE_BANDS:
        if threshold >= floor:
            return text
    return LANGUAGE_BANDS[-1][1]


def render_style_rules(config: StyleConfig) -> str:
    """渲染成 markdown 列表。只含**激活**的维度。

    输出对同一份配置是确定的——它进 system prompt，而 system 要能被缓存，
    任何非确定性（集合遍历顺序、时间戳）都会让缓存每轮失效。
    """
    rules: list[str] = []

    # 详细程度（常驻）
    rng = config.paragraph_length
    rules.append(
        f"- 段落长度：每段控制在 {rng.min}–{rng.max} 字。"
        f"这是**平均**要求，不要靠拆行凑数——把内容讲完整，再决定分几段。"
    )

    # 讲解语言一致性（条件激活）
    if config.activates_chinese_ratio:
        rules.append(f"- 讲解语言：{_language_rule(config.chinese_ratio.min)}")

    # 引用长度（常驻，合规约束）
    rules.append(
        f"- 引用课件原文时，**单段引用不超过 {config.max_quote_chars} 字**，"
        f"其余内容用自己的话讲。这是版权约束，不可协商。"
    )

    # 术语处理（v1 下线；开启时才渲染）
    if config.activates_term_policy:
        rules.append(
            "- 专业术语首次出现时，按「中文译名（English original）」标注英文原文。"
        )

    return "\n".join(rules)


# ---------------------------------------------------------------- B 类

#: **B 类三条轴的措辞写死在这里**，并原样进 pairs.json，供人工核对时对照
#: （任务 D）。措辞变了，样例的可比性就变了——它和档位区间一样是口径。
B_CLASS_FRAGMENTS: dict[str, dict[str, str]] = {
    "use_examples": {
        "more": "多举例子：每讲一个要点，配一个具体例子或类比帮助理解。",
        "fewer": "少举例子：把要点本身讲清楚即可，不额外补例子或类比。",
    },
    "structure": {
        "conclusion_first": (
            "先给结论：开头第一段就说出最重要的那句判断，后面再解释它为什么成立。"
        ),
        "buildup_first": (
            "先铺垫后收结论：按「问题 → 机制 → 结论」的顺序展开，"
            "把最重要的那句判断放在最后。"
        ),
    },
    "depth": {
        "what": (
            "只讲是什么：说清楚这个概念是什么、拿来做什么用。"
            "不解释它为什么这样设计，也不做推导。"
        ),
        "why": (
            "讲到为什么：在说清是什么之外，解释这个机制为什么成立、为什么这样设计。"
        ),
        "derivation": (
            "讲到推导：在解释为什么的基础上，把关键结论一步步推出来，"
            "写出中间步骤和用到的假设。"
        ),
    },
}

#: B 类措辞的**版本号**。它是 `prompt_profile_id` 的构成字段之一
#: （口径 F04 / F39）：埋点事件里存「B 类三条轴的取值 + 这个版本号」，
#: 分析时由 `explain.prompt_profile.prompt_profile_id()` 算出 id。
#:
#: ⚠️ **上面的措辞改了，这个版本号必须一起改。** 不改的话，同一个
#: `prompt_profile_id` 会盖住两份不同的 prompt——而那正是 F04 要这个标识
#: 回答的问题（「讲解质量变了，是阈值变了还是 prompt 变了」）失效的方式，
#: 且它**不会报错**。
PROMPT_TEMPLATE_VERSION: str = "b1"

#: 上一行那个版本号对应的**措辞摘要**：`B_CLASS_FRAGMENTS` 块正文
#: （压掉空白后）的 sha256 前 12 位。
#:
#: 靠人记得同时改版本号是不行的（F13 那个坑的又一个入口），所以摘要在这里，
#: 由 `web/scripts/verify_style_axes.mjs` 重算并比对：措辞一动摘要就对不上，
#: `npm run verify:axes` 直接变红，逼人把版本号一起改。
#:
#: 它对块内的**任何**字符变化都敏感（包括缩进以外的注释），宁可多红一次也不漏。
#: 真要改措辞：先改措辞 → 跑 verify:axes 看它红并抄下新摘要 → 同时改
#: PROMPT_TEMPLATE_VERSION 与 web/lib/style/axes.ts 的同名常量。
PROMPT_TEMPLATE_DIGEST: str = "292b62f6b396"


# ---------------------------------------------------------------- 语气（2026-09-19）

#: 语气档。**不是第四条 B 类轴**：它不进 `prompt_profile_id` 的 `b_class`、不被 A 类判定，
#: 由用户在开场标签页直接选（口径 F80/F81）。
TONES: tuple[str, ...] = ("spoken", "neutral", "academic")
DEFAULT_TONE: str = "neutral"

#: 语气 → prompt 里多出的那一行。
#:
#: ⚠️ **`neutral` 必须是空串**，这是硬要求，不是省事：
#: `pairs.json` 的 16 个样例是在**没有这一行**的 prompt 下生成的（口径 F18/F19）。
#: 默认档渲染成空 → 默认档的 prompt 与样例的生成条件**逐字节相同** → 样例仍然代表
#: 用户真会拿到的东西。哪天给 neutral 写上一句，那 16 个样例当场失效，必须连着重生成
#: （`explain/tests/test_style_axes.py` 有一条断言钉住这件事）。
#:
#: 每条都带一句**反向约束**：只说「要口语」，模型会为了口语牺牲准确；
#: 只说「要严谨」，它会把话拉长。两种偏移在 A 类实测里都见过。
TONE_FRAGMENTS: dict[str, str] = {
    "spoken": (
        "用口语讲：像同学之间讲题那样，句子短一些，可以直接用「你」称呼。"
        "**不要为了口语化牺牲准确**——该出现的术语照样出现，只是把它解释清楚。"
    ),
    "neutral": "",
    "academic": (
        "用书面语讲：措辞规范、句子完整，专业名词给出英文原文（括号紧跟中文之后），"
        "需要时直接写公式与符号。**严谨不等于啰嗦**——不要因此把话拉长。"
    ),
}


def render_tone_rule(tone: str) -> str:
    """语气那一行。默认档返回空串（见 `TONE_FRAGMENTS` 的注释）。"""
    if tone not in TONE_FRAGMENTS:
        raise ValueError(f"语气取值 {tone!r} 不在取值域 {TONES}")
    return TONE_FRAGMENTS[tone]


def render_b_class_rules(style: dict[str, str]) -> str:
    """渲染 B 类三条。顺序固定——system 要能被缓存，任何非确定性都会让缓存失效。"""
    lines = []
    for axis in ("use_examples", "structure", "depth"):
        value = style[axis]
        if value not in AXES[axis]:
            raise ValueError(f"{axis} 取值 {value!r} 不在取值域 {AXES[axis]}")
        lines.append(f"- {B_CLASS_FRAGMENTS[axis][value]}")
    return "\n".join(lines)


def render_all_style_rules(
    config: StyleConfig, b_class: dict[str, str], tone: str = DEFAULT_TONE
) -> str:
    """A 类 + B 类，**这是进 `build_system()` 的那一段**。

    拼法是 `A + "\n" + B`，**与 `pairs.json` 里 16 个样例的生成形状逐字节一致**
    （见 `f1_samples/generate_pairs.py` 的 `build_prompt`）。
    这不是风格选择：样例是用户在 onboarding 里据以选择的东西，
    它的 prompt 形状与生产分叉，F18/F19 的前提就垮了。

    `tone` 默认 `neutral` 且 neutral 渲染成空串，所以**不传它时的输出与 2026-09-19
    之前逐字节相同**——`generate_pairs.py` 不传，16 个样例因此仍然有效。

    `render_style_rules` 的签名与行为**一个字没动**：配置扫描实验按它跑过 180 轮，
    改它等于让那批已落盘的数据对应不上任何现存代码（口径 F19 的同一条理由）。
    """
    base = render_style_rules(config) + "\n" + render_b_class_rules(b_class)
    # 语气那一行接在 B 类之后。**默认档不加行**——加了的话，这一段就与
    # 16 个样例的生成条件分叉，而那正是 F18/F19 盯着的东西。
    line = render_tone_rule(tone)
    return base + ("\n- " + line if line else "")
