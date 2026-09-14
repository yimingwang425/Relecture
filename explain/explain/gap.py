"""F3b · 兜底判断（PRD §10.1）。

§10.1 实测否定了「用检索分数判断课件有没有讲」这条路——四个 embedding 模型上
「有答案」与 `no_answer` 两组的 top-1 相似度**完全重叠**。所以这道判断必须调模型，
且它**每轮都发生**，不是异常路径。

## 三件事，两条通路

| 情况 | 谁判 |
| --- | --- |
| 片段够不够回答 / 不够是哪一种 | **模型**（两分类，见下） |
| 内容是不是压在读不了的图里 | **规则**（口径 G03） |

「能用规则的绝不调模型」——同 A 类审查。

## G02 · 类别定义在模型看得见的范围内

模型只看得到检索片。若检索漏了那一页，它看到的就是「片段里没有」，
**它无法知道那是课件没讲还是检索没取到**。要求它区分，等于要求它报告一个
观测不到的量——那是造假断言的配方（通则七 #1）。

所以两个非 `covered` 的类别都**不对课件整体下判断**：

=================  ==========================================  ==================
``covered``        片段够回答                                  —
``insufficient``   **取到的片段不足以回答这个问题**            不是「课件没讲」
``partial``        片段提到了主题，但没回答所问的那一层        不是「提了没展开」
=================  ==========================================  ==================

`multipage` 最好只到 6/9（F2 §十二第 3 条）。让模型说「课件没讲」，
就是让它替检索背书。**说到自己看得见的范围为止。**

「判断错」与「检索漏」的区分不在这里做，在评测侧按 gold 页归因分解（口径 G11）。

## G03 · 第三类走规则，但机制换过

原定「`skipped_pages` 与检索命中页的交集」——**那个交集恒为空集**：
`image_only` 页不产生切片，不可能出现在检索命中页里
（`retrieval/README.md` §四：「gold 页不产生切片，`page-recall` 在结构上恒为 0」）。
实测 14.129 的两道 `unreadable_page` 题，交集都是空。

改为：**当判断不是 `covered`（即 `insufficient` 或 `partial`）且该课件存在跳过页时，
把跳过页清单作为限定语注入讲解 prompt。** 仍是零模型成本，
比交集弱，但**它真的会触发**（通则七 #5：永不触发的约束不是约束）。

### G03a · 触发条件从 `insufficient` 放宽到「非 `covered`」（2026-09-05）

**改动依据是校准数据，且改在跑批之前。** 原条件只认 `insufficient`，
而校准显示两道 `unreadable_page` 题里**只有一道触发**：

| 题 | gold 页 | 判断 | 原条件下是否触发 |
| --- | --- | --- | --- |
| Q030 | p35（`image_only`） | `insufficient` | ✅ |
| Q029 | p33/34/36（`image_only`） | **`partial`** | ❌ |

Q029 判 `partial` 是**合理的**：检索回来的邻近片段确实提到了账本方案这个主题，
只是没有那三页图里的具体对比。**模型看不到跳过页的内容**——它没有依据判
`insufficient`。要求它判 `insufficient` 等于要求它知道自己看不到什么。

所以门槛设在 `insufficient` 上，等于把这条通道的触发条件建立在一个
**模型无法可靠给出的判断**上——2 题里漏 1 题，通道形同虚设（又是通则七 #5）。

放宽的代价与 G03 本身的论证同构：**多说一行限定语**，方向单向。
且提示语本身是条件式的（「如果学生问的正是那部分」），误触发无害。

## G05 · 不输出置信度

未校准的置信度会被拿去设阈值，那是凭空造一个占位阈值（§3.2）。

决策日期：2026-08-22（F3a 留插入点）· 2026-09-05（F3b 实现）
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .context import ContextPack, SkippedPageRef
from .llm import Model

__all__ = [
    "GapVerdict",
    "GapResult",
    "GapChecker",
    "NullGapChecker",
    "ModelGapChecker",
    "GuardedGapChecker",
    "GAP_MODEL_NAME",
    "GAP_TIMEOUT_S",
    "GAP_SYSTEM_PROMPT",
    "VERDICTS",
    "build_model_gap_checker",
    "skipped_pages_hint",
]

GapVerdict = Literal["covered", "insufficient", "partial"]

#: 合法取值域。**取值不在域内即判无效样本**，不做「就近解释」（口径 G01）——
#: 「就近解释」会把一个坏输出洗成好输出，而且不留痕迹。
VERDICTS: tuple[str, ...] = ("covered", "insufficient", "partial")

GAP_SYSTEM_PROMPT = """你要判断：给定的课件片段，够不够回答学生的问题。

只输出一个 JSON 对象，不要有其他文字：

{"verdict": "covered" | "insufficient" | "partial", "reason": "一句话，40 字以内"}

三个取值的含义：

- `covered`：片段里有回答这个问题所需的内容。
- `insufficient`：**取到的片段里没有**回答这个问题所需的内容。
- `partial`：片段提到了这个问题所涉及的主题，但**没有回答学生问的那一层**。
  例如学生问「造价多少」，片段提到了「要做成本效益分析」却没给任何数字。

三条纪律：

1. **只判断你看到的片段，不判断整份课件。** 你看到的是检索出来的若干片，
   不是全文；课件里可能有你没看到的内容。所以是「片段里没有」，
   不是「课件里没有」。
2. `insufficient` 与 `partial` 的分界是**主题**：片段完全没触及这个主题选前者，
   触及了但没答到那一层选后者。
3. 不要回答学生的问题，不要补充你自己知道的知识。你只做这一个判断。"""

_JSON_RE = re.compile(r"\{.*?\}", re.S)


@dataclass(frozen=True, slots=True)
class GapResult:
    """一次兜底判断的结果。

    `verdict is None` 有两种含义，`implementation` 区分它们：
    `"null"` = 本轮没做这个判断；其余 = 做了但输出不合法（无效样本）。
    **不区分的话，F3b 上线后无法回答「兜底判断从哪个批次开始真的在跑」。**
    """

    verdict: GapVerdict | None
    reason: str | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    implementation: str = "null"
    model: str | None = None
    #: 规则通道（G03）的输出：需要作为限定语提及的跳过页。
    skipped_hint: tuple[SkippedPageRef, ...] = ()
    #: 输出不合法时保留原文取证；合法时为 None。
    invalid_raw: str | None = None
    #: 判断**根本没跑完**（调用抛异常）时的原因，`implementation="error"`。
    #: 与 `invalid_raw` 是两件事：那个是模型答了但答得不合法。
    error: str | None = None

    @property
    def is_invalid(self) -> bool:
        return self.implementation not in ("null", "error") and self.verdict is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_verdict": self.verdict,
            "gap_reason": self.reason,
            "gap_cost_usd": self.cost_usd,
            "gap_latency_ms": round(self.latency_ms, 1),
            "gap_implementation": self.implementation,
            "gap_model": self.model,
            "gap_invalid_output": self.invalid_raw,
            "gap_error": self.error,
            "gap_skipped_hint_pages": [p.index for p in self.skipped_hint],
        }


def skipped_pages_hint(
    context: ContextPack, verdict: GapVerdict | None
) -> tuple[SkippedPageRef, ...]:
    """规则通道（G03）。零模型成本。

    **在非 `covered` 上触发**（口径 G03a）。原条件只认 `insufficient`，
    校准实测 2 道 `unreadable_page` 题里漏 1 道——模型看不到跳过页的内容，
    没有依据判 `insufficient`，判 `partial` 是合理的。
    把门槛建立在一个模型无法可靠给出的判断上，通道就形同虚设。

    `covered` 仍不触发：片段够回答，再提「有些页读不了」是无关噪声。

    **已知精度限制（口径 G04，登记不修）**：多个分散跳过块时会全部列出。
    当前语料的跳过页是一个连续块（14.129 p33–36），列全部与指出具体块等价。
    """
    if verdict is None or verdict == "covered":
        return ()
    return tuple(context.skipped_pages)


class GapChecker(Protocol):
    def check(self, question: str, context: ContextPack) -> GapResult: ...


@dataclass(frozen=True, slots=True)
class NullGapChecker:
    """不做判断。F3a 用的就是它。

    **刻意不返回 `covered`**：返回 `covered` 等于断言「片够回答」，
    而我们根本没做这个判断——那是一条假断言（通则七 #1）。
    """

    def check(self, question: str, context: ContextPack) -> GapResult:
        return GapResult(verdict=None, implementation="null")


def _parse(text: str) -> tuple[str | None, str | None]:
    """抽出 verdict 与 reason。取值不合法一律返回 (None, None)。"""
    m = _JSON_RE.search(text)
    if not m:
        return None, None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        return None, None
    reason = data.get("reason")
    return verdict, (str(reason)[:80] if reason is not None else None)


@dataclass(frozen=True, slots=True)
class ModelGapChecker:
    """调模型做两分类判断。**每轮一次，是固定开销不是异常路径**（PRD §9.1）。

    分层调用（PRD §7.3）：用低成本模型、输出极短、关 thinking。
    它是严格串行的一跳——输入是检索结果、输出进生成的 prompt，
    **既不能与检索并行，也不能与生成并行**。
    """

    model: Model
    max_slice_chars: int = 1200

    def _render(self, question: str, context: ContextPack) -> str:
        if context.is_empty:
            return f"【课件片段】\n（本轮没有检索到任何片段）\n\n【学生的问题】\n{question}"
        parts = ["【课件片段】"]
        for s in context.slices:
            pages = "、".join(str(p) for p in s.page_indices)
            head = f"— 第 {pages} 页" + (f"｜{s.title}" if s.title else "")
            parts.append(f"{head}\n{s.text[: self.max_slice_chars]}")
        parts.append(f"\n【学生的问题】\n{question}")
        return "\n\n".join(parts)

    def check(self, question: str, context: ContextPack) -> GapResult:
        completion = self.model.complete(
            GAP_SYSTEM_PROMPT, [{"role": "user", "content": self._render(question, context)}]
        )
        verdict, reason = _parse(completion.text)
        return GapResult(
            verdict=verdict,  # type: ignore[arg-type]
            reason=reason,
            cost_usd=completion.cost,
            latency_ms=completion.latency_ms,
            implementation="model",
            model=completion.model,
            skipped_hint=skipped_pages_hint(context, verdict),  # type: ignore[arg-type]
            invalid_raw=None if verdict else completion.text[:200],
        )


#: 兜底判断的模型。**F3b 校准与跑批用的就是这一个 id**（2026-09-05），
#: 召回 10/10、$0.00126/轮、1,406 ms 都是它的数——换 id 就是换了被测对象。
GAP_MODEL_NAME = "claude-haiku-4-5-20251001"


def build_model_gap_checker(model: Model | None = None) -> ModelGapChecker:
    """兜底判断器的**唯一构造处**。实验侧（`scan/live.py`）与生产侧（`api/main.py`）都调它。

    ## 为什么住在这里（内测前-P1，口径 F70）

    原先这份配置住在 `实验脚本/配置扫描实验/scan/live.py::build_gap_checker`，
    生产一行都不 import 它。于是 F3b 的实测数（+4.8%、1.4 s、召回 10/10）
    **全部来自实验侧，生产链路一次没跑过**，而没有任何东西在核对这件事。
    在 `api/` 下再抄一份 = 两份 Haiku 配置，改一份另一份不跟（G06 那条教训：
    受限页号抄一份就多一个会漂的副本）。所以搬到两边都已依赖的 `explain`，
    方向同口径 F46「生产拥有，实验再导出」。

    分层调用（PRD §7.3）：低成本模型、输出极短、关 thinking（G01）。
    **Haiku 4.5 不支持 `effort`**（实测 2026-09-05，传了返回 400），故 `effort=None`。

    `model` 只给测试注入假模型用；不传就是真的那一个。
    """
    if model is None:
        from .llm import AnthropicModel

        model = AnthropicModel(
            name=GAP_MODEL_NAME,
            effort=None,
            thinking="disabled",
            max_tokens=300,
            cache_system=False,        # 系统提示远低于最小可缓存长度，开了也不生效
            cache_conversation=False,  # 单轮调用，没有历史可缓存
        )
    return ModelGapChecker(model)


#: 兜底判断的超时（秒）。**锚在实测上，不是占位阈值**：F3b 实测判断延迟
#: 均值 1,406 ms、**P90 1,595 ms**（2026-09-05），5 s ≈ 3× P90。
#: 改它之前先有新的实测分布——拍一个数与随手写的没有区别（PRD §3.2 阈值来源规则）。
GAP_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class GuardedGapChecker:
    """**判断失败或超时都不打断讲解。** 生产用；实验侧不套它（实验里调用失败就该响亮地停）。

    这一跳是串行插在生成之前的（`ModelGapChecker` 的 docstring）。它抛异常的话，
    `run_round` 连生成都走不到，用户等来的是一个 500——**为一条标注丢掉整段讲解**，
    与「埋点失败不打断主链路」（口径 F42）是同一条处置。

    ## 超时（内测前-P1 追加）

    **只接异常不够。** 接进生产之前这里是 `NullGapChecker`，零延迟，没有东西可以挂住；
    接进之后它是主链路上的一次外部 API 调用——Haiku 挂住，整轮讲解跟着挂住，
    用户看到的是转圈，**而挂住不是异常，上面那层兜底根本不会触发**。
    所以调用放进一个线程，`join(GAP_TIMEOUT_S)` 到点不回就按判断失败处理，
    走同一条 `implementation="error"` 路径。

    ⚠️ **代价如实登记**：Python 杀不掉一个正在跑的线程。超时之后那次调用
    **仍在后台跑完**（守护线程，不挡进程退出），可能照样计费。
    讲解不再等它，但它占着的连接要等 SDK 自己的超时才释放。

    ⚠️ 失败**不伪装成任何一个判断**：`verdict=None` + `implementation="error"` +
    `error` 带原因。尤其不返回 `covered`（那等于断言「片够回答」，而我们没判）。
    **成本在这里记 0.0，但它的真实含义是「未知」**——请求可能已经计费。
    事件那一侧把它写成 `null` 而不是 0（`api/events.py` 的 `_gap_fields`），
    理由同口径 F69：写 0 就是一条看起来「检查过、没花钱」的假证据。
    """

    inner: Any
    #: `None` = 调用时读模块常量 `GAP_TIMEOUT_S`（不在构造时绑定，便于它是唯一出处）。
    timeout_s: float | None = None

    def check(self, question: str, context: ContextPack) -> GapResult:
        timeout = GAP_TIMEOUT_S if self.timeout_s is None else self.timeout_s
        started = time.perf_counter()
        box: dict[str, Any] = {}

        def call() -> None:
            try:
                box["result"] = self.inner.check(question, context)
            except Exception as exc:  # noqa: BLE001 —— 任何失败都不该让用户丢掉这一轮
                box["error"] = exc

        worker = threading.Thread(target=call, name="gap-check", daemon=True)
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            return self._failed(started, f"TimeoutError: 判断超过 {timeout:g} 秒未返回，按判断失败处理")
        if "error" in box:
            exc = box["error"]
            return self._failed(started, f"{type(exc).__name__}: {str(exc)[:200]}")
        if "result" not in box:
            return self._failed(started, "判断线程结束但没有给出结果")
        return box["result"]

    def _failed(self, started: float, reason: str) -> GapResult:
        return GapResult(
            verdict=None,
            latency_ms=(time.perf_counter() - started) * 1000,
            implementation="error",
            model=getattr(getattr(self.inner, "model", None), "name", None),
            error=reason,
        )
