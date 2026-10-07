"""检索增强的四个模型助手（2026-10-07，口径 F96–F99）。

| 助手 | 什么时候 | 做什么 | 口径 |
| --- | --- | --- | --- |
| `QueryRewriter` | 每轮检索前 | 指代消解成独立问题 + 课件语言的关键词 | F96 |
| `LlmReranker` | 每轮检索后 | 从融合后的候选里挑出最能回答问题的 B 片 | F98 |
| `Contextualizer` | 上传时 | 给每片写一两句「在整份课件里的位置」，进嵌入与词法 | F99 |
| `VisionTranscriber` | 上传时 | 把读不出文字的页（图片页 / 扫描页）转写成文字 | F100 |

## 共同的四条

1. **分层调用**（PRD §7.3）：一律 Haiku 4.5、关 thinking、输出很短。
   模型 id 是 `RAG_MODEL_NAME`，**与兜底判断的 `GAP_MODEL_NAME` 刻意分开写**——
   数值相同，但它们是两个独立的选择：哪天给判断换模型，不该顺手把重排也换了
   （判断的校准数只对它自己那一个 id 成立，`gap.py` 那条注释的同一个意思）。
2. **失败不打断主链路**（口径 F42 的同一条）：每个助手失败或超时都回落到
   「没有它时」的行为——改写失败用原问题、重排失败用融合排名、定位语失败
   不加前缀、转写失败该页照旧记为跳过页。**回落不静默**：`implementation`
   写 `"error"` 并带原因，进响应与事件。
3. **成本单独记账、计入单轮 / 单次上传**（口径 F71 的同一条）。
4. **不回答问题、不补知识**写进每一个 prompt——它们是检索的帮手，
   不是第二个讲解者。

## ⚠️ 收益未测（如实登记）

四个助手的召回收益**都没有在本项目的评测集上测过**（本轮没有 API key 可用）。
`retrieval/scripts/eval_hybrid.py` 是测它的脚本，判据沿用 R17：
在多个独立条件下同向重复才算证据。**触发条件**：拿到 key 后的第一件事，
结果写进 `docs/F2/` 并回填 `f1_samples/README.md` 的 F96–F99 行。

决策日期：2026-10-07
"""

from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .gap import GAP_TIMEOUT_S
from .llm import Completion, Model

__all__ = [
    "RAG_MODEL_NAME",
    "RAG_TIMEOUT_S",
    "INGEST_CALL_TIMEOUT_S",
    "CONTEXT_PROMPT_VERSION",
    "VISION_PROMPT_VERSION",
    "QueryRewriter",
    "RewriteResult",
    "LlmReranker",
    "RerankResult",
    "Contextualizer",
    "ContextResult",
    "VisionTranscriber",
    "VisionResult",
    "build_rag_model",
]

#: 检索助手的模型。**与 `GAP_MODEL_NAME` 分开写**，见模块说明第 1 条。
RAG_MODEL_NAME = "claude-haiku-4-5-20251001"

#: 改写与重排的超时。**借用兜底判断的实测锚点**（同一个模型、输入量级相当：
#: 判断 P90 1,595 ms，5 s ≈ 3× P90），不是为这两个助手测出来的。
#: **触发条件**：内测产生 `rewrite_latency_ms` / `rerank_latency_ms` 的分布后，
#: 按它们自己的 P90 重定——那时这一行要改成独立的数，并写上来源。
RAG_TIMEOUT_S = GAP_TIMEOUT_S

#: 上传时（定位语、转写）单次调用的超时。**是净空不是阈值**：只挡挂死。
#: 一批 12 片的定位语约 1k 输出 token，Haiku 量级几秒；60 s 是它的十倍以上。
INGEST_CALL_TIMEOUT_S = 60.0

#: prompt 一改就要抬——它进 `documents.index_ruleset`（见 `api/api/indexing.py`），
#: 新旧两批定位语不会被当成同一口径。
CONTEXT_PROMPT_VERSION = "ctx-1"
VISION_PROMPT_VERSION = "vision-1"


def build_rag_model(max_tokens: int, *, cache_system: bool = False) -> Model:
    """检索助手用的模型。**唯一构造处**——四个助手的生产实例都从这里来。"""
    from .llm import AnthropicModel

    return AnthropicModel(
        name=RAG_MODEL_NAME,
        effort=None,            # Haiku 4.5 不支持 effort（gap.py 同一条，实测 400）
        thinking="disabled",
        max_tokens=max_tokens,
        cache_system=cache_system,
        cache_conversation=False,
    )


# ---------------------------------------------------------------- 公共

def _json_obj(text: str) -> dict[str, Any] | None:
    """取输出里**第一个 `{` 到最后一个 `}`**。不合法就是 None，不就近解释（G01 同一条）。"""
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        data = json.loads(text[a:b + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _guarded(fn: Callable[[], Completion], timeout: float) -> tuple[Completion | None, str | None, float]:
    """在线程里跑一次调用，到点不回就放弃。返回 `(结果, 错误, 毫秒)`。

    与 `GuardedGapChecker` 同一个处置与同一个代价：Python 杀不掉线程，
    超时后那次调用仍在后台跑完、可能计费——所以超时时成本记为**未知**（None）。
    """
    started = time.perf_counter()
    box: dict[str, Any] = {}

    def call() -> None:
        try:
            box["ok"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(timeout)
    ms = (time.perf_counter() - started) * 1000
    if t.is_alive():
        return None, f"TimeoutError: 超过 {timeout:g} 秒未返回", ms
    if "err" in box:
        e = box["err"]
        return None, f"{type(e).__name__}: {str(e)[:200]}", ms
    return box.get("ok"), None, ms


# ---------------------------------------------------------------- 查询改写（F96）

REWRITE_SYSTEM = """你把学生的提问改写成检索用的查询。只输出一个 JSON 对象，不要有其他文字：

{"query": "……", "keywords": ["……", "……"]}

- query：一句**脱离对话也能看懂**的完整问题。提问里的「它」「这个」「那一步」「上面说的」
  要换成对话里指代的具体东西。没有指代时原样照抄提问。用提问的语言写。
- keywords：3–8 个检索关键词，用来在课件里做**字面匹配**。课件可能是英文：
  中文概念要给出对应的英文术语；提问里出现的公式、符号、缩写、专有名词原样保留。
- 不要回答问题，不要加入对话里没有的信息。"""


@dataclass(frozen=True, slots=True)
class RewriteResult:
    query: str
    keywords: tuple[str, ...] = ()
    #: `"model"` 改写成功 · `"error"` 调用失败或超时 · `"invalid"` 输出不合法。
    #: 后两种 `query` 就是原问题、`keywords` 为空——**回落不等于没做**，分开记。
    implementation: str = "model"
    cost_usd: float | None = 0.0
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "keywords": list(self.keywords),
                "implementation": self.implementation, "cost_usd": self.cost_usd,
                "latency_ms": round(self.latency_ms, 1), "error": self.error}


@dataclass
class QueryRewriter:
    """**推翻口径 X01 的一半**（「检索 query = 本轮提问原文，不含历史改写」）。

    X01 的依据是 PRD §7.5「检索意图单一，不需要多跳推理」——那条对**第一轮**仍然成立。
    不成立的是**追问**：F5 之后有了会话历史，「那它为什么会这样？」这种提问
    拿原文去检索，取回来的是与「它」无关的片。X01 的另一半（检索结果与配置、
    与审查条件无关）**仍然成立**：改写只看提问与历史，不看风格配置。

    第一轮也调：要的是关键词（中文提问 → 英文课件的术语），不是指代消解。
    """

    model: Model
    timeout_s: float = RAG_TIMEOUT_S
    #: 历史只带最近几轮、每条截断——改写要的是指代对象，不是全文。
    max_history_turns: int = 3
    max_answer_chars: int = 300

    def _render(self, question: str, history: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        recent = history[-2 * self.max_history_turns:]
        for m in recent:
            who = "学生" if m.get("role") == "user" else "助手"
            text = str(m.get("content", ""))
            if who == "助手":
                text = text[: self.max_answer_chars]
            lines.append(f"{who}：{text}")
        head = "【最近的对话】\n" + "\n".join(lines) + "\n\n" if lines else ""
        return f"{head}【本轮提问】\n{question}"

    def rewrite(self, question: str, history: list[dict[str, Any]] | None = None) -> RewriteResult:
        msgs = [{"role": "user", "content": self._render(question, history or [])}]
        c, err, ms = _guarded(lambda: self.model.complete(REWRITE_SYSTEM, msgs), self.timeout_s)
        if c is None:
            return RewriteResult(query=question, implementation="error",
                                 cost_usd=None, latency_ms=ms, error=err)
        data = _json_obj(c.text)
        q = data.get("query") if data else None
        kws = data.get("keywords") if data else None
        if not isinstance(q, str) or not q.strip() or not isinstance(kws, list):
            return RewriteResult(query=question, implementation="invalid", cost_usd=c.cost,
                                 latency_ms=ms, error=f"输出不合法：{c.text[:200]}")
        clean = tuple(str(k).strip() for k in kws if str(k).strip())[:8]
        return RewriteResult(query=q.strip()[:500], keywords=clean, cost_usd=c.cost, latency_ms=ms)


# ---------------------------------------------------------------- 重排（F98）

RERANK_SYSTEM = """你给课件片段按「对回答这个问题有多大用」排序。只输出一个 JSON 对象，不要有其他文字：

{"order": [片段编号, 片段编号, ……]}

- 只列出最有用的至多 {n} 个编号，最有用的排在最前面。
- 标准是**能不能用来回答这个问题**，不是话题像不像。
- 不要回答问题，不要解释。"""


@dataclass(frozen=True, slots=True)
class RerankResult:
    #: 重排后的候选**下标**（指向传入的 candidates）。失败时为 None → 调用方用融合排名。
    order: tuple[int, ...] | None
    implementation: str = "model"
    cost_usd: float | None = 0.0
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"order": list(self.order) if self.order is not None else None,
                "implementation": self.implementation, "cost_usd": self.cost_usd,
                "latency_ms": round(self.latency_ms, 1), "error": self.error}


@dataclass
class LlmReranker:
    """候选 → 至多 B 片。**输出里不认识的编号丢掉、重复的去重**，不就近解释。

    返回不足 B 个时由调用方按融合排名回填（R07 第 5 条的同一个理由：
    同预算比较要求真的用满预算）。
    """

    model: Model
    timeout_s: float = RAG_TIMEOUT_S
    max_chars: int = 600

    def rerank(self, query: str, candidates: list[tuple[str | None, list[int], str]],
               top_n: int) -> RerankResult:
        if len(candidates) <= 1:
            return RerankResult(order=tuple(range(len(candidates))), implementation="skipped")
        parts = [f"【问题】\n{query}\n\n【候选片段】"]
        for i, (title, pages, text) in enumerate(candidates, start=1):
            head = f"[{i}]" + (f" {title}" if title else "") + (
                f"｜第 {'、'.join(map(str, pages))} 页" if pages else "")
            parts.append(f"{head}\n{text[: self.max_chars]}")
        msgs = [{"role": "user", "content": "\n\n".join(parts)}]
        system = RERANK_SYSTEM.replace("{n}", str(top_n))
        c, err, ms = _guarded(lambda: self.model.complete(system, msgs), self.timeout_s)
        if c is None:
            return RerankResult(order=None, implementation="error", cost_usd=None,
                                latency_ms=ms, error=err)
        data = _json_obj(c.text)
        raw = data.get("order") if data else None
        if not isinstance(raw, list):
            return RerankResult(order=None, implementation="invalid", cost_usd=c.cost,
                                latency_ms=ms, error=f"输出不合法：{c.text[:200]}")
        seen: list[int] = []
        for x in raw:
            try:
                i = int(x) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(candidates) and i not in seen:
                seen.append(i)
        if not seen:
            return RerankResult(order=None, implementation="invalid", cost_usd=c.cost,
                                latency_ms=ms, error=f"没有一个合法编号：{c.text[:200]}")
        return RerankResult(order=tuple(seen[:top_n]), cost_usd=c.cost, latency_ms=ms)


# ---------------------------------------------------------------- 定位语（F99）

CONTEXT_SYSTEM = """你为课件里的片段写「定位语」，用来提升检索。给每个片段写一到两句话：
它在整份课件里讲的是哪个主题、属于哪一部分、与前后内容是什么关系。

- 用片段本身的语言写；片段是英文时，在句末括号里补上关键概念的中文名。
- 只写定位，不复述片段内容，不评价，不补充片段之外的知识。
- 每条不超过 80 个汉字（英文不超过 50 词）。

只输出一个 JSON 对象，键是片段编号，值是定位语，不要有其他文字：
{"s001": "……", "s002": "……"}"""


@dataclass(frozen=True, slots=True)
class ContextResult:
    contexts: dict[str, str]
    cost_usd: float | None
    calls: int
    failed_batches: int
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"contextualized": len(self.contexts), "cost_usd": self.cost_usd,
                "calls": self.calls, "failed_batches": self.failed_batches,
                "errors": list(self.errors[:5])}


@dataclass
class Contextualizer:
    """一份课件的全部片 → `{slice_id: 定位语}`。**分批、并发、每批独立失败。**

    目录（每片的编号、页号、标题）放 system 并开缓存：同一份课件的各批共用它。
    ⚠️ 目录短于 Haiku 的最小可缓存长度时缓存不生效且**不报错**（`prompt.py`
    那条「未生效 ≠ 未命中」的同一件事）——这里不报「缓存命中率」，只记真实用量。
    """

    model: Model
    batch_size: int = 12
    workers: int = 4
    timeout_s: float = INGEST_CALL_TIMEOUT_S
    max_slice_chars: int = 1200

    def contextualize(self, filename: str,
                      slices: list[tuple[str, list[int], str | None, str]]) -> ContextResult:
        if not slices:
            return ContextResult({}, 0.0, 0, 0)
        outline = "\n".join(
            f"{sid}｜第 {'、'.join(map(str, pages))} 页｜{title or '（无标题）'}"
            for sid, pages, title, _ in slices)
        system = f"{CONTEXT_SYSTEM}\n\n【课件】{filename}\n【目录】\n{outline}"
        batches = [slices[i:i + self.batch_size] for i in range(0, len(slices), self.batch_size)]

        def one(batch):
            body = "\n\n".join(
                f"<{sid}> 第 {'、'.join(map(str, pages))} 页｜{title or '（无标题）'}\n"
                f"{text[: self.max_slice_chars]}"
                for sid, pages, title, text in batch)
            msgs = [{"role": "user", "content": f"【片段】\n{body}"}]
            return _guarded(lambda: self.model.complete(system, msgs), self.timeout_s), batch

        out: dict[str, str] = {}
        cost: float | None = 0.0
        failed = 0
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for (c, err, _ms), batch in pool.map(one, batches):
                if c is None:
                    failed += 1
                    errors.append(err or "unknown")
                    cost = None   # 超时的那次可能已计费 → 总额未知（F73 同一条）
                    continue
                if cost is not None:
                    cost += c.cost
                data = _json_obj(c.text) or {}
                wanted = {sid for sid, *_ in batch}
                got = 0
                for k, v in data.items():
                    if k in wanted and isinstance(v, str) and v.strip():
                        out[k] = v.strip()[:400]
                        got += 1
                if got == 0:
                    failed += 1
                    errors.append(f"输出不合法：{c.text[:120]}")
        return ContextResult(out, cost, len(batches), failed, tuple(errors))


# ---------------------------------------------------------------- 图片页转写（F100）

VISION_SYSTEM = """你把一页课件的图片转写成文字，供检索与讲解使用。

- 图里的文字**原样转写**（保留原语言），保持条目层次。
- 图表、示意图：用一两句话说清它表达了什么（坐标轴、趋势、关键数值、各部分的关系）。
- 公式写成 LaTeX 或纯文本。
- 读不清的地方写「[读不清]」，不要猜。不要补充图里没有的知识。
- 页面空白或只有装饰时，只输出「[无内容]」。

直接输出转写结果，不要开场白，不要总结。"""

_NOTHING = "[无内容]"


@dataclass(frozen=True, slots=True)
class VisionResult:
    page_index: int
    text: str
    implementation: str = "model"
    cost_usd: float | None = 0.0
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class VisionTranscriber:
    """一页 PNG → 文字。**转写不是课件原文**：入库时标题写明「图像转写」，
    讲解看得到这个标注（`build_user_turn` 把标题放在片头）。"""

    model: Model
    timeout_s: float = INGEST_CALL_TIMEOUT_S
    workers: int = 4

    def transcribe(self, filename: str, page_index: int, png: bytes) -> VisionResult:
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode("ascii")}},
            {"type": "text", "text": f"这是《{filename}》第 {page_index} 页。"},
        ]}]
        c, err, ms = _guarded(lambda: self.model.complete(VISION_SYSTEM, msgs), self.timeout_s)
        if c is None:
            return VisionResult(page_index, "", "error", None, ms, err)
        text = c.text.strip()
        if not text or text == _NOTHING:
            return VisionResult(page_index, "", "empty", c.cost, ms)
        return VisionResult(page_index, text, "model", c.cost, ms)

    def transcribe_many(self, filename: str, pages: list[tuple[int, bytes]]) -> list[VisionResult]:
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(lambda p: self.transcribe(filename, p[0], p[1]), pages))
