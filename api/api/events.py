"""阶段 6 · §13.0 的四个讲解事件：`explain_turn` / `style_review_result` /
`regeneration_triggered` / `retrieval_executed`。

## 为什么在 Python 侧发，而 F1 那五个在 TS 侧发

不是随手分的。判据是**数据在哪**：

- F1 那五个事件的全部字段都产生在浏览器里（哪一步、谁在左、停留多久），
  服务端压根不知道。它们的唯一构造处是 `web/lib/style/events.ts`。
- 这四个的全部字段都产生在这一次 `run_round()` 里：token 四类分列、成本与价目、
  三维审查的逐维得分、检索的分数分布与口径版本。**浏览器一个都不知道。**

让前端发这四个，就得先把它们从服务端序列化过去再发回来——多一跳、多一份
可以漂的副本，而且**前端能改这些数字**（成本、是否达标、维度得分）。
这与 F39 的判断同形：「不为一个纯函数建服务」的反面是「不把数据搬到它不在的地方去算」。

代价说清楚：**事件的构造处从此有两个**（TS 一个、Python 一个）。
可接受的理由是它们**构造的是不同的事件**，没有一个字段两边都写——
不是同一份东西的两个副本（那才是 F13 那个坑）。
必需字段清单仍然只有一处出处：数据库的 `f1_event_required_keys`。
本模块下面那张 `REQUIRED_KEYS` 是它的镜像，**由 `verify_rls_local.mjs` 从
SQL 原文读回来逐项比对**（同 `events.ts` 里那份镜像的做法，口径 F33）。

## `session_id` 缺了就**不发事件**，不现编一个

表上 `session_id` 是 `not null`。现编一个 uuid 的话，每一轮都会变成一个
独立会话——而**「平均会话轮次」是北极星指标**（PRD §3.1）。
那个数会静静地变成 1.0 附近，看起来像一个真实的产品结论。

所以：没有 `session_id` → 一条事件都不发，并在响应里**点名说清**
（`events.skipped_reason`）。少发事件是可见的损失，编一个 session 是不可见的错数。

## 写入失败不打断讲解，但也不静默（口径 F42）

用户等了几十秒拿到的那段讲解不该因为一条埋点写不进去而丢掉。
但也不能悄悄咽下：本模块在**这次请求里**把重试做完（阶梯比浏览器那份短，
用户正在等这一屏），失败则把结构化结果放进响应的 `events` 字段。
与 `web/lib/style/events.ts` 的 `recordEvent()` 是同一条处置。

决策日期：2026-09-12
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["EXPLAIN_EVENT_TYPES", "UPLOAD_EVENT_TYPES", "MARK_EVENT_TYPES",
           "REQUIRED_KEYS", "build_explain_events", "build_mark_event",
           "complete_event", "record_events", "SERVER_RETRY_DELAYS_MS"]

EXPLAIN_EVENT_TYPES = (
    "explain_turn",
    "style_review_result",
    "regeneration_triggered",
    "retrieval_executed",
)

#: 阶段 6 第一批（上传页）。PRD §13.1 第一行：文件类型、页数、处理耗时、成功/失败。
#: 「成功/失败」由 **event_type 本身**承担，不另立布尔字段——立了就有两处出处，
#: 而它们漂开的表现是「一条 deck_uploaded 里写着 ok=false」。
UPLOAD_EVENT_TYPES = ("deck_uploaded", "deck_upload_failed")

#: F4（F4 方案 §3.4）。一条 mark 一条事件。
#:
#: **在 Python 侧构造，不让前端发**，判据仍是口径 F51「数据在哪」：
#: `mark_count_after` 产生在服务端（它是 mark 落库之后的一次计数），
#: 浏览器不知道这个数。让前端发就得先把它序列化过去再发回来，
#: 多一份可以漂的副本，而且**前端能改这个数**。
MARK_EVENT_TYPES = ("confusion_mark_created",)

#: 必需字段清单的**镜像**。唯一出处是迁移里的 `f1_event_required_keys`
#: （`20260912120000_f3_explain_events.sql`），`verify_rls_local.mjs` 从 SQL 原文
#: 读回来与本表逐项比对——两份存在 ≠ 两份一致，所以有那条比对（口径 F33）。
#:
#: 在 Python 侧也放一份的理由与 `events.ts` 里那份相同：数据库那份是**最后一道**
#: 防线，它变红的时候事件已经丢了（insert 被 23514 拒，用户毫无感知）。
#: 这一份让缺字段在**构造时**就抛出来。
#:
#: ⚠️ **null 有含义的字段不在清单里**（`all_activated_passed` /
#: `first_token_latency_ms`）：`f1_event_payload_ok` 把 JSON null 判成「没有」，
#: 进了清单会让一条完全正确的事件被静默拒掉。理由详见那个迁移文件。
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "explain_turn": ("turn_index", "question", "usage", "cost_usd", "pricing_rates",
                     "latency_ms", "accepted", "regenerations",
                     "style_config_fingerprint", "metric_set_id"),
    "style_review_result": ("dimensions", "counted_dimensions"),
    "regeneration_triggered": ("attempt_index", "failed_dimensions", "final_accepted"),
    "retrieval_executed": ("top_k_scores", "slice_ids", "pages", "latency_ms",
                           "embed_model", "dimensions", "ruleset_versions", "backend"),
    # ---- 上传页两个（阶段 6 第一批）----
    # ⚠️ `restricted_pages_excluded` **不在这里**（内测前-P1，口径 F69）：它现在写 null，
    # 意思是「未检查」。null 有含义的字段不进必需清单（口径 F50）——进了，
    # 每一次成功上传的事件都会被判成缺字段而静默丢掉。
    "deck_uploaded": ("filename", "source_format", "page_count", "usable_page_count",
                      "verdict", "slice_count", "embedded_count", "skipped_pages",
                      "duration_ms"),
    # ⚠️ **只有三个**，刻意的（口径 F50）。失败可能发生在任何一步之前：
    # 格式不对时连文件都没读，`page_count` / `source_format` 全是 null。
    # 把它们列成必需 = **最该被采集的那一类失败进不了库**
    # （用户第一次上传就撞上格式错误）。
    "deck_upload_failed": ("failure_kind", "filename", "bytes"),
    # ---- F4 ----
    # 三个都不可能是 null：这条事件在 mark 已经落库之后才发。
    "confusion_mark_created": ("round_id", "slice_ids", "mark_count_after"),
}

#: 服务端的退避阶梯。**比浏览器那份短**——用户正在等这一屏
#: （与 `web/lib/style/events.ts` 的 `SERVER_RETRY_DELAYS_MS` 同一个理由与同一组数）。
SERVER_RETRY_DELAYS_MS = (200, 700)


def complete_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """构造时就检查必需字段。缺了直接抛，**不交给数据库去拒**：
    数据库拒掉的表现是「这条事件没了」，而没了的事件在任何报表里都不出现，
    也没有任何东西会红（漏采集是最安静的一类 bug）。

    ⚠️ **0 与 False 不算缺。** `payload.get(k) is None or == ""` 这个写法
    在 Python 里对 `0` 和 `False` 是安全的（它们既不是 None 也不等于 ""），
    但 `skipped_pages` 常为 0、`retryable` 可能是 False，
    所以这一点必须是刻意的而不是巧合 —— `tests/test_upload_events.py` 钉着它。
    """
    known = REQUIRED_KEYS.get(event_type)
    if known is None:
        # 未知类型在这里就拦。交给数据库的话是 23514，
        # 而那时错误信息说的是「payload 不合格」，不是「这个类型不存在」。
        raise ValueError(f"未知 event_type {event_type!r}；取值域见 REQUIRED_KEYS 与迁移")
    missing = [k for k in known
               if payload.get(k) is None or payload.get(k) == ""]
    if missing:
        raise ValueError(f"{event_type} 的 payload 缺必需字段：{', '.join(missing)}")
    bad = _slice_ids_problem(event_type, payload)
    if bad:
        raise ValueError(f"{event_type} 的 payload {bad}")
    return {"event_type": event_type, "payload": payload}


def _slice_ids_problem(event_type: str, payload: dict[str, Any]) -> str:
    """`slice_ids` 非空护栏。空则返回一句人话，没问题返回空串。

    ## 为什么「键在」还不够

    `slice_ids` 早就在 `REQUIRED_KEYS` 与数据库的必需字段清单里，
    但那两处判的都是**键在不在**——`[]` 完全通过（它不是 None、不是 `""`）。
    于是「检索命中了 5 片，而事件里的 slice_ids 是空的」在此之前
    **没有任何东西会红**。

    这不是一条洁癖：`retrieval_executed.slice_ids` 是护栏指标「重复提问率」
    的**唯一**数据来源（口径 F62），漏了就再也补不回来。

    ## 空检索是例外，且只对 `retrieval_executed` 成立

    一片都没检索到是**合法状态**（`build_user_turn` 里那句
    「【本轮没有检索到任何课件片段】」），而且它更该被采集到——
    一并拒掉就是 F50 那条教训的复发。所以 `slice_count == 0` 时放行。
    `confusion_mark_created` 没有这个例外：写入侧就拒绝空 mark。

    ⚠️ **这份判据是数据库那条 `f4_event_slice_ids_ok` 的镜像**
    （`20260913150000_f4_confusion_marks.sql`）。两份存在 ≠ 两份一致，
    所以 `tests/test_marks.py` 里有一条把两边喂同一组 payload 逐个比对的断言。
    """
    if event_type not in ("retrieval_executed", "confusion_mark_created"):
        return ""
    ids = payload.get("slice_ids")
    if ids is None:
        return ""       # 缺键是上面那条的活，不在这里重复判
    if not isinstance(ids, list):
        return f"的 slice_ids 不是数组（{type(ids).__name__}）——构造处写错了形状"
    if ids:
        return ""
    if event_type == "retrieval_executed" and payload.get("slice_count") == 0:
        return ""       # 空检索：合法状态，而且更该被采集到
    return ("的 slice_ids 是空的，而本轮并非空检索"
            f"（slice_count={payload.get('slice_count')!r}）。"
            "它是护栏指标「重复提问率」的唯一来源，漏了就再也补不回来")


#: 旧名字保留：`test_events.py` 与 `build_explain_events` 用的是它。
_complete = complete_event


def build_explain_events(
    result: Any,
    *,
    config: Any,
    question: str,
    course_id: str | None,
    deck_id: str | None = None,
    turn_index: int = 1,
    question_tags: list[str] | None = None,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """把一次 `run_round()` 的结果变成 §13.0 的那批事件。**纯函数，不碰网络。**

    `config` 是 `a_rules.config.StyleConfig`（`fingerprint()` / `metric_set_id()`
    的出处，口径 F04）。
    """
    from explain.log import pricing_record

    ctx = result.context
    ev = result.final_review.evaluation
    usage = result.usage.to_dict()
    pricing = pricing_record(model_name or _model_name_of(result))

    out: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- explain_turn
    #
    # ⚠️ **轮次口径：按用户可见的输出计，重生成不计入轮次**（PRD §13.0 原话），
    # 只计入成本与质量指标。所以 `turn_index` 由调用方给（它知道这是第几轮），
    # 而 `regenerations` 是同一条事件里的另一个字段——一个字段两种含义的话，
    # 成本表和北极星指标永远对不上，且两边看起来都是对的。
    out.append(_complete("explain_turn", {
        "turn_index": turn_index,
        "question": question,
        "question_tags": question_tags or [],
        # 四类分列，不合并。三类输入互不重叠；合并之后就再也算不回成本结构。
        "usage": usage,
        "cost_usd": result.cost_usd,
        "pricing_rates": pricing["pricing_rates_per_mtok"],
        # 价目带到期日，否则事后无法重算（§13.0 原话）。
        "pricing_intro_until": pricing["pricing_intro_until"],
        "pricing_as_of": pricing["pricing_as_of"],
        # 关键路径（判断与首版生成并行之后不再是总和，口径 F93）。
        "latency_ms": round(result.latency_ms, 1),
        # 流式之后**测得到了**（口径 F94）：从请求开始到第一段正文放出闸门。
        # 没有正文时仍是 null——**不塞假数**。它刻意不在 REQUIRED_KEYS 里，见那张表的注释。
        "first_token_latency_ms": result.first_token_latency_ms,
        "wall_ms": result.wall_ms,
        # 投机生成被作废的次数（0 或 1）。它花的钱已计入上面的 `cost_usd`。
        "speculative_discarded": len(result.discarded),
        # 检索时的改写 + 重排（口径 F96 / F98）。**已计入 `cost_usd`**，单列是为了分得开。
        # None = 有一次超时（可能已计费，金额未知）——取自 meta 原值，不取上面那个按 0 加的。
        "retrieval_llm_cost_usd": (result.context.meta or {}).get("llm_cost_usd", 0.0),
        "accepted": result.accepted,
        "regenerations": result.regenerations,
        # F04 的两个标识各管一件事：前者管「阈值变没变」，后者管「指标集是不是同一个」。
        "style_config_fingerprint": config.fingerprint(),
        "metric_set_id": config.metric_set_id(),
        "prompt_template_version": _prompt_template_version(),
        "context_signature": ctx.signature(),
        # 兜底判断：**单独记账，但已计入上面的 `cost_usd` / `latency_ms`**（口径 F71 / G08）。
        **_gap_fields(result.gap),
        # 多智能体链路（口径 F109–F114）。**全是可选字段**，不进 REQUIRED_KEYS——
        # 实验侧直接调 `run_round` 时没有它们；null 有含义（F50）。不需要迁移：payload 是 jsonb。
        **_agent_fields(getattr(result, "agent", None)),
    }))

    # ---------------------------------------------------------------- style_review_result
    #
    # §8.1 已定「不做单一混合分」——**按维度分列存，不可只存合并结论**。
    # 只存合并结论的话，§3.2 的「各激活维度均需达标」事后就算不出来。
    #
    # ⚠️ 维度**从 `ev.dimensions` 逐个 `to_dict()`，不从 `ev.to_dict()["dimensions"]` 取**。
    # 后者是 `{name: {...}}` 一个**字典**（`a_rules/evaluator.py` 第 80 行），
    # 按列表遍历它会拿到一串字符串。第一版就是那么写的，
    # 被真库那一发抓出来（`AttributeError: 'str' object has no attribute 'get'`）——
    # 单测没抓到，因为测试里那个假 Evaluation 的 `to_dict()` 返回的是**列表**，
    # 形状是我自己写的。**与 numpy 那次、与 23505 那次是同一个形态**：
    # 替身照协议/照我的记忆写，而不是照生产实现真的返回什么。
    # 现在测试用的是真的 `a_rules.evaluate()`，不再有这个假货。
    #
    # 存成**列表**而不是字典：分析时要按维度横向比较，列表里每项自带 `name`，
    # 而字典的键在 jsonb 里不保证顺序，也不能直接喂给按行展开的查询。
    dims = [d.to_dict() for d in ev.dimensions]
    # `counted_dimensions` 取 `ev.scored` 那条口径（**已激活且算得出数**），
    # 不是自己按 `activated` 重筛一遍 —— 重筛会漏掉「激活了但这份样本算不出数」
    # 那一类，而 `counts_toward_score` 的注释写着它不计入分母。
    # 判据的两端不能都出自我对这条口径的记忆。
    out.append(_complete("style_review_result", {
        "dimensions": dims,
        # 无维度可判时为 null，**不是 true**（口径 D42）。
        # 不在 REQUIRED_KEYS 里，否则那一版会被静默拒掉。
        "all_activated_passed": ev.all_activated_passed,
        "counted_dimensions": [d.name for d in ev.scored],
        "config_version": ev.config_version,
        # 风格审查 agent 对最终那一版的判断（2026-10-12）。上面三维照算、只记录；
        # 打不打回由它与版权那一条决定。没问审查员时为 null。可选字段，不进 REQUIRED_KEYS。
        "judge": (result.final_review.judge.to_dict()
                  if getattr(result.final_review, "judge", None) is not None else None),
    }))

    # ---------------------------------------------------------------- regeneration_triggered
    #
    # **一次重生成一条事件**（口径 F37 的同一条：一步一个事件）。
    # 压成一条「重生成了 2 次」的话，「第 1 次是哪个维度不达标、第 2 次是哪个」
    # 就再也分不开，而那正是要拿来看审查反馈有没有用的东西。
    #
    # `attempt_index` 从 1 起：`attempts[0]` 是首次生成，不是重生成。
    for i, attempt in enumerate(result.attempts[:-1]):
        out.append(_complete("regeneration_triggered", {
            "attempt_index": i + 1,
            "failed_dimensions": list(attempt.review.failed_dimensions),
            # 触顶降级时为 false（PRD §10）。**它是 false 不是 null**，所以可以必需。
            "final_accepted": result.accepted,
        }))

    # ---------------------------------------------------------------- retrieval_executed
    #
    # §10.1 已实测检索分数**判不了** no_answer（四个 embedding 模型上两组分布完全重叠）。
    # **但分布本身仍要长期监控**：它是「检索质量有没有随语料变化而退化」的唯一信号。
    # 埋这个字段不等于打算用它做兜底判断。
    meta = ctx.meta or {}
    out.append(_complete("retrieval_executed", {
        "top_k_scores": [s.score for s in ctx.slices if s.score is not None],
        # **每项两段。** `slice_id` 是文档内局部 id，一门课两份讲义都有 's001'
        # （2026-09-12 真库观测坐实）。只记 slice_id 的话事后分不开是哪一份讲义。
        #
        # ⚠️ **`document_id` 也要记，不能只记 `source_file`。**
        # 同一份文件可以挂在不同课程下（唯一索引带 course_id，口径 F47），
        # 真库上 `MIT11_201F07_slides2.pdf` 同时在两门课里——按文件名分组的分析
        # 会把两门课的召回混成一份。`document_id` 由真检索后端给（meta 里那份
        # 与 slices 逐项对齐）；替身没有它，那时是 None，**如实空着不编**。
        "slice_ids": _slice_identities(ctx),
        "pages": sorted({p for s in ctx.slices for p in s.page_indices}),
        "latency_ms": meta.get("retrieval_latency_ms", 0.0),
        "embed_model": meta.get("embed_model"),
        "dimensions": meta.get("embed_dimensions"),
        "ruleset_versions": {
            "embed_text": meta.get("embed_text_ruleset"),
            "expansion": meta.get("expansion_ruleset"),
        },
        # **替身与真检索必须在事件里分得开**（同响应里那条）：
        # 压成一句「检索完成」的话，一次用合成课件的调用和一次真检索
        # 在分析时长得一模一样，而它们的召回数字不可合并。
        "backend": meta.get("backend", "unknown"),
        "slice_count": len(ctx.slices),
        "context_chars": ctx.total_chars,
        "skipped_pages_in_scope": len(ctx.skipped_pages),
        "course_id": course_id,
    }))
    return out


def build_mark_event(*, round_id: str, slice_keys: list[str], mark_count_after: int,
                     created: bool) -> dict[str, Any]:
    """F4 · `confusion_mark_created`。**纯函数，不碰网络。**

    `created=False` 表示这一次是幂等重复（同一 `round_id` 点了第二次）。
    **仍然发事件**，只是带上 `created: false`：

      「同一轮被点了两次」是一个真实的用户行为信号（点了没反应就再点一次），
      不发的话它在数据里与「只点了一次」完全一样。而 `mark_count_after`
      在这两种情况下都是对的（重复时它不涨），所以两者不会把计数搅乱。
    """
    return complete_event("confusion_mark_created", {
        "round_id": round_id,
        # 与 `confusion_marks.slice_ids` 里存的是**同一份**（同一个 `slice_key()`）。
        "slice_ids": list(slice_keys),
        "mark_count_after": mark_count_after,
        "slice_count": len(slice_keys),
        # 幂等重复也发。见 docstring。
        "created": created,
        "prompt_template_version": _prompt_template_version(),
    })


def _slice_identities(ctx: Any) -> list[dict[str, Any]]:
    """每个命中切片的身份：`source_file` + `slice_id` + `document_id`。

    `document_id` 取 `meta["hit_documents"]`（真检索后端提供，与 `slices`
    **逐项对齐**）。替身后端没有这一项 → `None`，**不编**：
    编一个的话按 deck 分组的分析会把替身那批算成某一份真实讲义。

    长度对不上就全部记 `None` 并不静默：对不上意味着后端的两份输出不同步，
    而按序号硬配会把身份配错——配错的身份比没有身份更糟（它看起来是对的）。
    """
    meta = ctx.meta or {}
    hits = meta.get("hit_documents") or []
    aligned = len(hits) == len(ctx.slices)
    return [
        {
            "source_file": s.source_file,
            "slice_id": s.slice_id,
            "document_id": (hits[i].get("document_id") if aligned else None),
        }
        for i, s in enumerate(ctx.slices)
    ]


def _gap_fields(gap: Any) -> dict[str, Any]:
    """`explain_turn` 里兜底判断那几列（口径 F71：G08 的生产版）。

    它是**每轮固定开销，不是异常路径**，分开记才能回答「这一跳值不值」。
    `RoundResult.cost_usd` / `latency_ms` 本来就含它（`pipeline.py`），
    这里是把它**从总数里拆出来单独落一份**，不是重复计入。

    `gap_model` 要记：讲解的 `pricing_rates` 是讲解模型的价，判断走 Haiku，
    不记模型名的话这笔成本事后无法按价目重算。

    ## 三个字段为什么都不进必需清单

    - `gap_verdict`：判断无效或失败时是 null，而那个 null 有含义（口径 F50）。
    - `gap_cost_usd`：**判断失败时写 null，不写 0**。请求可能已经计费，
      我们不知道花了多少——写 0 是一条「没花钱」的假证据（同口径 F69）。
      转换在 `GapResult.to_dict()`，与 `/explain` 响应同一处，这里不再转第二次。
    - 全部：部署顺序是迁移先于构建（CLAUDE.md），迁移跑完到新构建上线之间
      **旧构建的 `explain_turn` 没有这几个键**。进了必需清单，那段窗口里的讲解事件
      会被 23514 全部静默拒掉。

    代价如实登记：这几列**没有数据库护栏**，护栏在 `tests/test_gap_wiring.py`
    （断的是表里那一行的值，不是函数被调用）。
    """
    d = gap.to_dict()
    return {
        "gap_verdict": d["gap_verdict"],
        "gap_reason": d["gap_reason"],
        "gap_cost_usd": d["gap_cost_usd"],
        "gap_latency_ms": d["gap_latency_ms"],
        # "model" / "null" / "error"。上线后它应当恒为 "model"——
        # 出现 "null" 说明构建里没接上，出现 "error" 看 `gap_error`。
        "gap_implementation": d["gap_implementation"],
        "gap_model": d["gap_model"],
        "gap_error": d["gap_error"],
        "gap_skipped_hint_pages": d["gap_skipped_hint_pages"],
    }


def _agent_fields(agent: Any) -> dict[str, Any]:
    """`agent_mode` 是读这一组字段的入口：`agent` / `pipeline` / `fallback`，或 null（没经过 agent 入口）。

    ⚠️ `agent_cost_usd` **已计入** `cost_usd`，单列是为了回答「多出来的这几跳值不值」——
    与 `gap_cost_usd` 同一个处置（F71）。路由判成 chat 时路由那次调用就是讲解本身，
    钱在 attempts 里，不在这里（`AgentTrace._route_billed_here`）。
    """
    if agent is None:
        return {"agent_mode": None}
    d = agent.to_dict()
    route = d.get("route") or {}
    tools = d.get("tools") or []
    return {
        "agent_mode": d["mode"],
        "agent_route": route.get("route"),
        "agent_route_implementation": route.get("implementation"),
        "agent_route_cost_usd": route.get("cost_usd"),
        "agent_tool_rounds": d["tool_rounds"],
        "agent_tool_calls": [t["name"] for t in tools],
        "agent_tools_failed": sum(1 for t in tools if not t["ok"]),
        "agent_slices_added": sum(int(t.get("new_slices") or 0) for t in tools),
        "agent_hit_round_cap": d["hit_round_cap"],
        "agent_cost_usd": d["cost_usd"],
        "agent_cost_known": d["cost_known"],
        "agent_prompt_version": d["prompt_version"],
        "agent_fallback_error": d["fallback_error"],
    }


def _model_name_of(result: Any) -> str:
    """取这次真正用的模型名。**从 completion 上读，不写字面量**——
    日志里写一个猜的模型名，会让成本按错误的价目重算。"""
    for a in result.attempts:
        name = getattr(a.completion, "model", None)
        if name:
            return str(name)
    return ""


def _prompt_template_version() -> str:
    from explain.style_prompt import PROMPT_TEMPLATE_VERSION

    return PROMPT_TEMPLATE_VERSION


# ---------------------------------------------------------------- 写库


def record_events(
    store: Any,
    *,
    token: str,
    user_id: str,
    arm: str,
    session_id: str | None,
    events: list[dict[str, Any]],
    course_id: str | None = None,
    deck_id: str | None = None,
    delays_ms: tuple[int, ...] = SERVER_RETRY_DELAYS_MS,
    sleep: Any = None,
) -> dict[str, Any]:
    """写这一批事件。**返回结果，不抛**（埋点写不进去不该打断讲解）。

    没有 `session_id` 就一条都不发 —— 见模块 docstring：编一个会把
    「平均会话轮次」这个北极星指标静静地改成一个假数。
    """
    if not session_id:
        return {"emitted": 0, "attempted": 0, "failed": 0,
                "skipped_reason": "没有 session_id：一次页面加载 = 一个 session，"
                                  "服务端不现编（编了会把「平均会话轮次」改成假数）"}

    napper = sleep or (lambda ms: time.sleep(ms / 1000))
    rows = [{
        "user_id": user_id,
        "event_type": e["event_type"],
        "arm": arm,
        "session_id": session_id,
        "payload": e["payload"],
        "course_id": course_id,
        "deck_id": deck_id,
    } for e in events]

    emitted, failures = 0, []
    for row in rows:
        last: str | None = "未尝试"
        for attempt in range(len(delays_ms) + 1):
            try:
                store.insert_events(token=token, rows=[row])
                last = None
                break
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                if attempt < len(delays_ms):
                    napper(delays_ms[attempt])
        if last is None:
            emitted += 1
        else:
            failures.append({"event_type": row["event_type"], "error": last})

    out: dict[str, Any] = {"emitted": emitted, "attempted": len(rows),
                           "failed": len(failures)}
    if failures:
        # **不静默。** 埋点失败进响应，调用方可以显示出来（同 F42 的处置）。
        out["failures"] = failures
    return out
