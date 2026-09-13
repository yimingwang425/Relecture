"""产品服务端 —— **F1 那一行第一次成为一次讲解生成的输入**。

## 这一跳为什么现在才建

F1 阶段 1.5 论证过：F1 全部读写落在一张表上，多一跳 FastAPI 只是把 RLS
从「数据库执行」变成「应用转发」，不增加保障。**那条论证到今天仍然成立**——
所以本服务**不**把 RLS 搬到应用层重做一遍（见 `supabase.py`）。

它存在的理由是别的：`a_rules` 的审查判定与 `explain` 的 prompt 组装是 Python，
而浏览器跑不了 Python。F1 阶段 4 把这一跳推迟了（口径 F39），理由是
「`prompt_profile_id` 只在分析时被消费」——**那条理由对生成链路不成立**。

## 边界（阶段 2 / 阶段 3）

- 鉴权 + 读配置：`/me/style`
- 生成：`/explain`，检索用**固定的合成课件**

⚠️ **本阶段的检索是替身，登记在案。** `InMemoryRetriever` 自己的文档写着
「它不假装自己是检索……任何召回率类断言都不得建立在它之上」。
所以这里**不得出任何召回类结论**——那是阶段 5 接 pgvector 之后的事。
用它做**风格**断言是合法的：风格遵循度不依赖检索对不对。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import marks as _marks
from .auth import AuthError, HttpJwks, Principal, TokenVerifier, bearer_token
from .build import build_commit
from .upload_failures import UploadFailure as _UploadFailure
from .settings import Settings, load_settings
from .supabase import StyleRow, SupabaseClient, SupabaseError

__all__ = ["app", "create_app"]


class StyleResponse(BaseModel):
    user_id: str
    detail_level: str
    use_examples: str
    structure: str
    depth: str
    f1_arm: str
    onboarded_at: str | None
    #: 档位 → 阈值之后的指纹。**同一个问题两次调用若指纹相同，说明配置没变**——
    #: 验收要靠它把「配置没生效」与「两次配置本来就一样」分开。
    config_fingerprint: str


class ExplainRequest(BaseModel):
    # ⚠️ **未知字段一律拒，不静默丢掉。** pydantic 的默认是 `extra="ignore"`，
    # 而那个默认在 2026-09-12 真的咬了一次：Render 上跑的是阶段 4 的旧构建，
    # 它的 `ExplainRequest` 没有 `course_id`，于是客户端发过去的 `course_id`
    # **被静默丢掉**，检索走了替身，而响应是 **200**，正文在讲布隆过滤器
    # （合成课件）。看起来一切正常。
    #
    # 收紧成 `forbid` 之后，同一形态的下一次会是 **422 并点名那个字段**——
    # 「服务端比客户端旧」从一个静默的错答变成一条响亮的失败。
    # 这是 `web/lib/env.ts`「宁可起不来」那条纪律在请求体上的同一件事。
    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1, max_length=500)
    #: 检索作用域（口径 F47：课程是实体，作用域是一门课，不是一份文档）。
    #:
    #: **可选**，而且两条路的语义完全不同，不许混：
    #:   · 给了 → 走 `SupabaseRetriever`，真检索这门课的语料；
    #:   · 不给 → 走阶段 3 那个**替身**（固定合成课件）。
    #:
    #: 留着替身那条路不是为了兼容，是因为**验收纪律 1（P0 vs P3 真调模型）
    #: 的判据不能依赖检索**：它比的是同一份材料在两个档位下的段落长度，
    #: 材料一变那个比较就不成立了。风格断言走替身是合法的
    #: （`InMemoryRetriever` 的原话：用它做召回断言不合法，风格断言合法）。
    course_id: str | None = None
    #: 一次页面加载 = 一个 session（**占位口径**，PRD §3.4.5 的切分规则至今未定）。
    #:
    #: ⚠️ **缺了它就一条事件都不发，服务端不现编一个。** 现编的后果是每一轮
    #: 都变成一个独立会话，而「平均会话轮次」是北极星指标（PRD §3.1）——
    #: 那个数会静静地变成 1.0 附近，看起来像一个真实的产品结论。
    #: 少发事件是可见的损失，编一个 session 是不可见的错数。
    session_id: str | None = None
    #: 会话内第几轮。**按用户可见的输出计，重生成不计入轮次**（PRD §13.0）。
    #: 由调用方给：只有它知道这是第几轮（本服务不持有会话状态）。
    turn_index: int = 1


class CourseRequest(BaseModel):
    model_config = {"extra": "forbid"}   # 同上

    name: str = Field(min_length=1, max_length=200)


class MarkRequest(BaseModel):
    """F4 · 「这部分没看懂」。**入参三个必需 + 两个埋点用的可选**。"""

    model_config = {"extra": "forbid"}   # 同上

    #: 哪一轮。取 `/explain` 响应里的 `round_id`，原样带回来（口径 F65）。
    #: 它同时是幂等键：同一轮点两次，表里仍然一行，两次都返回 200。
    round_id: str
    #: 本轮命中的全部切片，取 `/explain` 响应里的 `slice_keys`，原样带回来。
    #:
    #: ⚠️ **空列表一律 422，不静默收下。** 一条空的 mark 与任何一轮都不会有
    #: 交集——它是一行死数据，而死数据与「标记没生效」在库里长得一模一样。
    #: 走替身检索那条路（不传 `course_id`）拿到的 `slice_keys` 就是空的，
    #: 那时前端不该显示这个按钮（见 `marks.py` 模块说明）。
    #:
    #: 上限 64 是一条**净空**，不是口径：检索预算 B=5
    #: （`SupabaseRetriever.budget`），所以正常情况下这里是 5 个左右。
    #: 它挡的是「有人拿这个接口往库里灌一个巨大的数组」，不是业务规则。
    slice_ids: list[str] = Field(min_length=1, max_length=64)
    #: 本轮提问原文。**存下来，v1 不注入**（口径 F59）。
    question: str = Field(min_length=1, max_length=500)
    #: 一次页面加载 = 一个 session（占位口径，同 `/explain`）。
    #: **缺了就不发事件，服务端不现编**——理由见 `events.py` 模块说明。
    session_id: str | None = None
    #: 只进**事件**那一行的 `course_id` 列，供按课分组。
    #: ⚠️ `confusion_marks` 表里**没有**这一列（口径 F63）：切片经 `documents`
    #: 可 join 出课程，而检索本就按课隔离。这两件事不矛盾——
    #: 一个是分析维度，一个是业务字段。
    course_id: str | None = None


#: 上传体积上限。沿用 `parse_service.app.MAX_UPLOAD_BYTES` 的那个数与它的来源说明
#: （**占位待定**，现值是测试集最大文件的近 30 倍，不会误伤）——
#: 不在这里写第二个数字。
def _max_upload_bytes() -> int:
    from parse_service.app import MAX_UPLOAD_BYTES

    return MAX_UPLOAD_BYTES


def create_app(
    settings: Settings | None = None,
    verifier: TokenVerifier | None = None,
    supabase: SupabaseClient | None = None,
    retriever: Any | None = None,
    model_factory: Any | None = None,
    embedder_factory: Any | None = None,
) -> FastAPI:
    """组装。每个依赖都可注入，**为的是断言能喂真东西**。

    例如验签：断言里注入一把本地生成的 EC 私钥签出的真 token，
    走的仍然是 `jwt.decode` 那条真实路径，而不是一个被打桩掉的替身（通则七 #7）。
    """
    cfg = settings or load_settings()
    verify = verifier or TokenVerifier(HttpJwks(cfg.jwks_url), issuer=cfg.issuer)
    db = supabase or SupabaseClient(cfg.rest_url, cfg.supabase_publishable_key)

    app = FastAPI(
        title="AI 课件讲解助手 · 产品服务端",
        version="0.1.0",
        description="鉴权 → 读风格配置 → 讲解生成。F1→F3 接线。",
    )

    def principal(authorization: str | None = Header(default=None)) -> Principal:
        try:
            return verify.verify(bearer_token(authorization))
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    def style_row(p: Principal = Depends(principal)) -> tuple[Principal, StyleRow]:
        try:
            return p, db.style_row(p.user_id, p.token)
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    # ---------------------------------------------------------------- 上传失败的响应形状
    #
    # `HTTPException` 只产出 `{"detail": ...}`，而前端要按**分类**分支
    # （八类各有自己的文案与「要不要给重试按钮」）。从状态码反推会丢信息：
    # 415 与 501 都是「格式家族」，能分开它们正是因为服务端一个类一个码。
    #
    # ⚠️ **`detail` 保持字符串**，`failure_kind` 作为**同级字段**加上去。
    # 把 detail 换成对象会让既有的读取方（`ingest_real.py` 那条
    # 「409 且是人话，不是 23505」）当场变红，而那条断言仍然要成立。
    @app.exception_handler(_UploadFailure)
    async def _upload_failure_handler(request: Any, exc: _UploadFailure) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status,
            content={
                "detail": exc.detail,          # ← 人话，给这一次请求的用户
                "failure_kind": exc.kind,      # ← 闭集，给前端分支与分析
                "retryable": exc.retryable,
                # 失败那条事件落没落库，同样要能从外面看见（见 _record_upload_event）。
                "events": getattr(exc, "events_result", None),
            },
        )

    # ---------------------------------------------------------------- CORS
    #
    # 上传页在浏览器里**直连本服务**（Vercel 的 serverless 请求体上限 4.5MB，
    # 而本服务上限 50MB —— 代理一跳会把大课件挡在 Vercel 那一层，
    # 且那个失败与「文件太大」长得一样却发生在别的地方）。所以要 CORS。
    #
    # ⚠️ **清单为空时不挂中间件**，于是浏览器一律被挡住。
    # 「配错 → 上传页当场报 CORS 错误」是一眼看得见的；
    # 「默认放开 → 一切正常」是看不见的，直到有人发现任意站点都能拿着
    # 用户的 token 打这个服务。`load_settings()` 另外拒绝 `*`。
    if cfg.cors_allow_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_allow_origins),
            # 只放开实际用到的：上传是 POST，读课程是 GET，预检是 OPTIONS。
            allow_methods=["GET", "POST", "OPTIONS"],
            # Authorization 必须在，它是这一跳的鉴权方式。
            allow_headers=["Authorization", "Content-Type"],
            # **不开 credentials**：本服务不用 cookie，鉴权走 Authorization 头。
            # 开了它会让 `allow_origins` 的错误配置后果更严重，而我们不需要它。
            allow_credentials=False,
            # 让前端读得到构建标识（口径 F17）——跨源时默认只暴露几个安全头。
            expose_headers=["x-build-commit"],
        )

    # ---------------------------------------------------------------- 构建标识
    #
    # 口径 F17：「验收前先核对读的是哪个构建」。web 侧靠响应头 `x-build-commit`，
    # **这个服务此前一个标识都没有** —— 2026-09-12 因此有 8 条断言在一个
    # 阶段 4 的旧构建上通过了（详见 `api/api/build.py` 的模块说明）。
    # 同名同义的头，加在**每个**响应上，包括错误响应。
    @app.middleware("http")
    async def add_build_header(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        commit = build_commit()
        # 取不到就**不加这个头**，不加一个 'unknown'。
        # 「答不出自己是谁」与「答了，内容是 unknown」在验收里含义相反。
        if commit:
            response.headers["x-build-commit"] = commit
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        # **不报凭据有没有配好之外的任何东西**，也不报版本以外的项目信息。
        #
        # `build_commit` 是例外，且它不是项目信息而是**构建标识**：
        # 一个 private 仓库的 git sha 说不出仓库里有什么，而没有它，
        # 「我在跟哪个构建说话」这个问题就答不出来（口径 F17）。
        # 取不到时是 **null**，不是 'unknown' —— 见 `build.py`。
        return {
            "status": "ok",
            "model_key_configured": cfg.anthropic_api_key is not None,
            "build_commit": build_commit(),
        }

    @app.get("/me/style", response_model=StyleResponse)
    def me_style(both: tuple[Principal, StyleRow] = Depends(style_row)) -> StyleResponse:
        """以**用户身份**读 `style_config`，RLS 由数据库执行。

        读到的一定是自己那行——不是因为这里过滤了 `user_id`，
        而是因为策略 `style_config_select_own` 在数据库里。
        """
        from explain.style_axes import style_config_for

        _, row = both
        return StyleResponse(
            **row.to_dict(),
            config_fingerprint=style_config_for(row.detail_level).fingerprint(),
        )

    @app.post("/explain")
    def explain(req: ExplainRequest, both: tuple[Principal, StyleRow] = Depends(style_row)) -> dict[str, Any]:
        """一轮讲解。**这是 `style_config` 那一行第一次影响模型输出。**

        阶段 5 起 `course_id` 可选：给了就真检索那门课的语料
        （`SupabaseRetriever`，RLS 由数据库执行），不给走阶段 3 的替身。
        """
        from explain.pipeline import Trajectory, run_round
        from explain.review import RuleReviewer
        from explain.style_axes import style_config_for
        from explain.style_prompt import render_all_style_rules

        p, row = both

        if cfg.anthropic_api_key is None and model_factory is None:
            # 缺 key 是部署配置问题，不是用户输入问题——点名说清，不写「请重试」。
            raise HTTPException(status_code=503, detail="服务端未配置 ANTHROPIC_API_KEY")

        try:
            style = style_config_for(row.detail_level)
        except ValueError as exc:
            # 库里出现了取值域外的档位。**判失败，不回落默认值**——回落会让
            # 「用户选了 P3」与「库里有个我们不认识的值」在输出里长得一模一样。
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # ---- 检索后端：给了 course_id 就是真的，没给就是阶段 3 那个替身。
        #
        # ⚠️ **两条路必须在返回里能被分辨**（口径 F34 的同一条）：压成一句
        # 「检索完成」的话，一次走替身的调用和一次真检索在响应里长得一模一样，
        # 而「用合成课件答的」与「用你的课件答的」是两件完全不同的事。
        # 所以下面 `retrieval.meta.backend` 原样回传。
        if retriever is not None:
            active = retriever
            label = _COURSE_LABEL
        elif req.course_id:
            course = db.course(p.token, req.course_id)
            if course is None:
                # **不区分「不存在」与「是别人的」**：区分了就等于用 404/403
                # 泄露课程 id 的存在性，而 RLS 的整个意思就是让别人的行不可见。
                raise HTTPException(404, "找不到这门课（可能不存在，也可能不是你的）")
            active = _course_retriever(db, p.token, req.course_id,
                                       embedder_factory or _default_embedder)
            label = course["name"]
        else:
            active = _default_retriever()
            label = _COURSE_LABEL

        # ---- F4 · 检索**提到这里做**，因为注入要在生成之前拿到本轮的 slice_ids。
        #
        # `run_round(context=...)` 是既有参数（口径 X02：两组对照共用同一份
        # ContextPack），不是为本轮新开的口子。检索仍然只发生一次。
        import uuid as _uuid

        ctx = active.fetch(req.question)
        #: 讲解轮标识（口径 F65）。**服务端生成**——响应里此前没有任何稳定的轮
        #: 标识（`retrieval.signature` 只认提问与命中切片，同一个人把同一个问题
        #: 问两遍会得到同一个值，拿它当幂等键会让第二轮的标记覆盖第一轮）。
        #: 一次 `/explain` = 一轮；轮内的重生成不新开一个（重生成不计入轮次，
        #: 轮的定义是「用户可见的一次输出」，PRD §13.0）。
        round_id = str(_uuid.uuid4())
        slice_keys = _marks.slice_keys_of(ctx)
        confusion = _confusion_for_round(db, p, slice_keys)

        make_model = model_factory or _default_model
        result = run_round(
            round_index=1,
            question=req.question,
            trajectory=Trajectory(),
            retriever=active,
            context=ctx,
            model=make_model(),
            reviewer=RuleReviewer(style),
            # ← A 类 + B 类，拼法与 pairs.json 里 16 个样例逐字节同形（口径 F19/F45）
            style_rules=render_all_style_rules(style, row.b_class),
            course_label=label,
            review_enabled=True,
            # F4 的限定语进**本轮用户消息**，不进 system（口径 F57）：
            # marks 逐轮变化，而 system 是按「会话内不变」设计的。
            turn_prefix=confusion["note"] or "",
        )

        # ---- §13.0 的四个事件。**在返回之前发**，且失败不打断这次讲解。
        #
        # 在 Python 侧发而不是让前端发：这四个事件的全部字段都产生在
        # 刚刚那次 `run_round()` 里（token 四类分列、成本与价目、逐维得分、
        # 检索分数分布），**浏览器一个都不知道**。理由详见 `api/api/events.py`。
        from .events import build_explain_events, record_events

        events_result: dict[str, Any]
        try:
            built = build_explain_events(
                result, config=style, question=req.question,
                course_id=req.course_id, turn_index=req.turn_index,
            )
            events_result = record_events(
                db, token=p.token, user_id=p.user_id, arm=row.f1_arm,
                session_id=req.session_id, events=built, course_id=req.course_id,
            )
        except Exception as exc:  # noqa: BLE001
            # 构造失败（缺必需字段）是我们自己的 bug，**不该让用户丢掉这段讲解**。
            # 但也不静默：原样进响应，调用方能显示出来（同口径 F42）。
            events_result = {"emitted": 0, "attempted": 0, "failed": 0,
                             "build_error": f"{type(exc).__name__}: {exc}"}

        ev = result.final_review.evaluation
        ctx = result.context
        return {
            # 埋点的结果**进响应**。写不进去不打断讲解，但也不许静默（口径 F42）。
            "events": events_result,
            # ---- F4（口径 F65）。这两个是 `POST /api/v1/marks` 的入参来源：
            # 前端**原样带回来**，不自己拼、不自己编。
            "round_id": round_id,
            "slice_keys": slice_keys,
            # 注入到底触发没触发，**从外面看得见**（口径 F53：验收要确认的东西，
            # 系统必须能自己答出来）。§九的判据「注入组的 messages 里必须含限定语」
            # 靠它机械可查，不靠人去读那段讲解。
            "confusion": confusion,
            "text": result.final_text,
            "accepted": result.accepted,
            "regenerations": result.regenerations,
            # ---- 检索（阶段 5）。**原样回传 backend**，让「真检索」与「替身」
            # 在响应里分得开；`slices` 里带 document_id 之外的全部身份信息，
            # §13.0 的 `retrieval_executed` 就从这里取。
            "course_id": req.course_id,
            "retrieval": ctx.to_dict(),
            # 这三个是验收要读的：配置真的进了 prompt 吗、真的改变了输出吗。
            "detail_level": row.detail_level,
            "config_fingerprint": style.fingerprint(),
            "paragraph_length_target": style.paragraph_length.to_dict(),
            "mean_paragraph_chars": ev.by_name("paragraph_length").raw,
            "all_activated_passed": ev.all_activated_passed,
            "style_rules": result.system_text.split("## 材料范围")[0].strip(),
        }

    # ---------------------------------------------------------------- F4 · 「这部分没看懂」
    #
    # ⚠️ **路径带 `/api/v1` 前缀，而本服务其余路由都不带**（`/explain`、
    # `/courses`、`/me/style`）。这是 F4-P1 任务书写死的路径，照做；
    # 登记为一处**已知的不一致**，不在本轮顺手统一——统一要动前端已经在调的
    # 三条路由，那是一次与 F4 无关的改动，而它失败的样子是「页面白屏」。
    # 触发条件：真要加版本前缀时一次性全改，那时 v1 的路由表还只有五条。
    @app.post("/api/v1/marks")
    def create_mark(req: MarkRequest, p: Principal = Depends(principal)) -> dict[str, Any]:
        """记一次「这部分没看懂」。**幂等**：同一 `round_id` 提交两次都返回 200。

        不做 GET、不做 DELETE（口径 F60）：v1 不回显已标记状态，也不做取消。
        误标的代价是「多换一次讲法」，方向单向可解释。
        """
        import uuid as _uuid

        try:
            round_id = str(_uuid.UUID(req.round_id))
        except (ValueError, AttributeError, TypeError):
            # **在这里拒，不让数据库去 22P02。** 那个错会被包成 502，
            # 而 502 读起来是「服务端坏了」，实际是客户端发了个不是 uuid 的东西。
            raise HTTPException(
                422, f"round_id 不是一个 uuid：{req.round_id!r}。"
                     "它要取 /explain 响应里的那一个，不要自己编") from None

        try:
            created = db.insert_mark(token=p.token, row={
                "user_id": p.user_id,
                "round_id": round_id,
                "slice_ids": req.slice_ids,
                "question": req.question,
            })
            # **插完再数**，不是插之前的数 + 1：幂等重复时那个 +1 会多算一个。
            # 名字叫 `mark_count_after` 说的就是「之后」。
            count = db.count_marks(p.token)
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

        events_result = _record_mark_event(
            db, p, session_id=req.session_id, course_id=req.course_id,
            round_id=round_id, slice_keys=req.slice_ids,
            mark_count_after=count, created=created)

        return {
            "round_id": round_id,
            # False = 这一轮之前已经标记过（幂等重复）。**不是错误**，
            # 前端不需要分支；给出来是为了埋点分析分得开。
            "created": created,
            "mark_count_after": count,
            "slice_count": len(req.slice_ids),
            # 埋点结果进响应（同 `/explain` 与上传）：事件表从外面读不到，
            # 没有它任何人都无法自证这条事件落没落库。
            "events": events_result,
        }

    # ---------------------------------------------------------------- 摄入
    #
    # `parse-service` 并入本服务（2026-09-11 拍板）：解析 → 切片 → 嵌入 → 落库
    # 对用户是**一次动作**，拆两个服务意味着整份 PDF 多走一次网络、两套部署、
    # 两条 §13.3 记录。连带后果是 `/parse` 从「无鉴权无状态」变成要鉴权——
    # **那本来就得改，它现在谁都能调。**

    @app.get("/courses")
    def list_courses(p: Principal = Depends(principal)) -> list[dict[str, Any]]:
        try:
            return db.courses(p.token)
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    @app.post("/courses", status_code=201)
    def create_course(req: CourseRequest, p: Principal = Depends(principal)) -> dict[str, Any]:
        try:
            return db.create_course(token=p.token, owner=p.user_id, name=req.name.strip())
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    @app.post("/courses/{course_id}/documents", status_code=201)
    async def upload(
        course_id: str,
        file: UploadFile = File(...),
        #: 一次页面加载 = 一个 session（占位口径，同 `/explain`）。
        #: **缺了就不发事件，不现编**——理由见 `events.py` 模块说明。
        session_id: str | None = None,
        p: Principal = Depends(principal),
    ) -> dict[str, Any]:
        """上传一份课件。**一次事务性的动作**（见 `ingest.py` 的模块说明）。

        阶段 6 第一批加了两件事：
          · **先查后提示**——建行之前问一次这门课有没有同一份文件，提示说得出
            是哪一份、什么时候传的（唯一索引仍然是权威，两层）。
          · **两个事件**：`deck_uploaded` / `deck_upload_failed`（PRD §13.1 第一行）。
            失败**按分类发**，不压成「上传失败」——每一类用户该做的事都不同
            （见 `upload_failures.py` 那张表）。
        """
        import time

        from .ingest import ingest_document
        # 唯一定义处是 supabase.py（两个同名类已于 2026-09-12 合并，见那里的 docstring）。
        from .supabase import DuplicateDocument
        t0 = time.monotonic()
        name = file.filename or "upload"

        def ms() -> float:
            return round((time.monotonic() - t0) * 1000, 1)

        # ⚠️ **失败也要发事件，所以整段包在一个 try 里。**
        # 各个 raise 点抛的是 `UploadFailure`（带分类），不是裸 HTTPException——
        # 裸抛的话分类就只存在于状态码里，而 415 / 501 那一对**必须分得开**
        # （「不支持」与「还没做」对用户是两件完全不同的事）。
        try:
            if not name.lower().endswith(".pdf"):
                if name.lower().endswith(".pptx"):
                    # 区分「不支持」与「还没做」——PPTX 的 schema 已定，解析器未交付。
                    # 压成一句「不支持」会让人以为是永久不支持而去做别的兜底。
                    raise _UploadFailure(
                        "pptx_not_implemented",
                        "PPTX 解析尚未实现（schema 已定义，解析器为下一次交付）")
                raise _UploadFailure("unsupported_format", f"仅支持 .pdf，收到 {name}")

            data = await file.read()
            if not data:
                raise _UploadFailure("empty_file", "文件为空", extra={"bytes": 0})
            limit = _max_upload_bytes()
            if len(data) > limit:
                raise _UploadFailure(
                    "too_large", f"文件超过 {limit // 1024 // 1024} MB 上限",
                    extra={"bytes": len(data), "limit_bytes": limit})

            make_embedder = embedder_factory or _default_embedder
            try:
                result = ingest_document(
                    data=data, filename=name, owner=p.user_id, course_id=course_id,
                    token=p.token, store=db, embedder=make_embedder(),
                    find_existing=lambda sha: db.find_document_by_sha(
                        token=p.token, course_id=course_id, sha=sha),
                )
            except DuplicateDocument as exc:
                # 两条路都到这里：**先查查到的**（exc.existing 非空，说得出是哪一份）
                # 与**唯一索引拦下的**（exc.existing 为空，先查后插之间的窗口）。
                # 数据库那条是硬失败 23505，不是「提示」。
                raise _UploadFailure(
                    "duplicate_in_course", exc.message,
                    extra={"bytes": len(data),
                           "existing_document_id": (exc.existing or {}).get("id"),
                           # **哪一层拦下的**要记：如果 `by_unique_index` 长期占多数，
                           # 说明「先查」这一层没在起作用（而它的绿是看不见的）。
                           "caught_by": "pre_check" if exc.existing else "unique_index"}) from exc
            except SupabaseError as exc:
                raise _UploadFailure("db_error", exc.message,
                                    extra={"bytes": len(data)}) from exc
            except Exception as exc:  # 解析器对损坏文件抛的异常类型不统一
                raise _UploadFailure("unparsable", f"无法处理该 PDF：{exc}",
                                    extra={"bytes": len(data)}) from exc

        except _UploadFailure as fail:
            # ---- 失败事件。**分类进 payload，异常原文不进**（PRD §12.2 / 口径 F32）：
            # `unparsable` 的原文来自解析器，而它谈论的是用户上传的文件内容。
            # 原文回给这一次请求的用户（只有他看得到）并留在服务端日志里。
            fail.events_result = _record_upload_event(
                db, p, session_id=session_id, course_id=course_id,
                event_type="deck_upload_failed",
                payload={
                    "failure_kind": fail.kind,
                    "filename": name,
                    # 0 也是一个值，所以它可以必需（见迁移里那段）。
                    "bytes": fail.extra.get("bytes", 0),
                    "duration_ms": ms(),
                    "retryable": fail.retryable,
                    "http_status": fail.status,
                    **{k: v for k, v in fail.extra.items() if k != "bytes"},
                },
                deck_id=None,   # 失败时**没有** document 行，null 在这里是事实
            )
            # **原样抛**，由上面那个处理器决定响应形状
            # （它多带一个 `failure_kind`，而 `detail` 仍是人话）。
            raise

        # ---- 成功事件。**它是 `deck_id` 那一列第一个真正的填写者**
        # （`20260912120000` 里登记的「恒 NULL」由此解掉）。
        events_result = _record_upload_event(
            db, p, session_id=session_id, course_id=course_id,
            event_type="deck_uploaded",
            payload={
                "filename": name,
                "source_format": result.source_format,
                "page_count": result.page_count,
                "usable_page_count": result.usable_page_count,
                "verdict": result.verdict,
                "slice_count": result.slice_count,
                "embedded_count": result.embedded_count,
                "skipped_pages": result.skipped_pages,
                "restricted_pages_excluded": result.restricted_pages_excluded,
                "bytes": len(data),
                "duration_ms": ms(),
            },
            deck_id=result.document_id,
        )

        return {
            "document_id": result.document_id,
            "slice_count": result.slice_count,
            "embedded_count": result.embedded_count,
            "skipped_pages": result.skipped_pages,
            "restricted_pages_excluded": result.restricted_pages_excluded,
            "verdict": result.verdict,
            # 埋点结果**进响应**（2026-09-13 改，见 `_record_upload_event` 的说明）：
            # 事件表从外面读不到，而「deck_id 到底带没带」正是验收要确认的。
            # 页面可以不读这个字段；没有它，任何人都无法自证埋点落没落库。
            "events": events_result,
        }

    return app


def _confusion_for_round(db: Any, p: Any, slice_keys: list[str]) -> dict[str, Any]:
    """本轮要不要注入那句限定语。**永不抛**。

    ## 查不到 marks 时**不打断讲解，但也不静默**

    这一跳是一个增强，不是主链路：数据库抖一下就让用户丢掉整段讲解，
    换来的只是「这一轮没换切入点」。所以失败时照常生成，
    并把错误原样放进响应的 `confusion.error` —— 与埋点那条同一处置（口径 F42）。

    静默咽下的后果是：功能上线之后**永远不注入**，而输出看起来完全正常。
    那正是这个字段存在的理由。

    ## 返回里为什么带 `matched_slice_keys`

    「有没有交集」由数据库的 `&&` 决定，「是哪几片」由 `matched_keys()` 在
    Python 里算。两者打架（`injected` 为真而 `matched_slice_keys` 为空）
    意味着数据库那侧的键与这里的拼法不同源 —— 那时这一对数字自己会把它说出来。
    """
    if not slice_keys:
        return {"injected": False, "note": None, "marked_rounds": 0,
                "matched_slice_keys": [],
                # 空不是错误：替身检索没有 document_id，产不出键（见 marks.py）。
                "skipped_reason": "本轮没有可标记的切片身份（检索走替身，没有 document_id）"}
    try:
        rows = db.marks_overlapping(p.token, slice_keys)
    except Exception as exc:  # noqa: BLE001
        return {"injected": False, "note": None, "marked_rounds": 0,
                "matched_slice_keys": [],
                "error": f"{type(exc).__name__}: {exc}"}

    note = _marks.confusion_prefix(rows)
    return {
        "injected": bool(note),
        "note": note or None,
        "marked_rounds": len(rows),
        "matched_slice_keys": _marks.matched_keys(rows, slice_keys),
    }


def _record_mark_event(
    db: Any, p: Any, *, session_id: str | None, course_id: str | None,
    round_id: str, slice_keys: list[str], mark_count_after: int, created: bool,
) -> dict[str, Any]:
    """发一条 `confusion_mark_created`。**永不抛**——埋点失败不打断标记（口径 F42）。

    `arm` 在这里懒取，理由与 `_record_upload_event` 那段一字不差：
    让这条路由 `Depends(style_row)` 会把一次埋点的前置变成标记本身的前置，
    而**标记压根不需要风格配置**。一条埋点不该让用户标记不了。
    """
    import json as _json

    from .events import build_mark_event, record_events

    if not session_id:
        skipped = ("没有 session_id：一次页面加载 = 一个 session，服务端不现编"
                   "（编了会把「平均会话轮次」这个北极星指标改成假数）")
        print(_json.dumps({"event": "mark_event_skipped", "reason": skipped},
                          ensure_ascii=False))
        return {"emitted": 0, "attempted": 0, "failed": 0, "skipped_reason": skipped}
    try:
        arm = db.style_row(p.user_id, p.token).f1_arm
        out = record_events(
            db, token=p.token, user_id=p.user_id, arm=arm, session_id=session_id,
            events=[build_mark_event(round_id=round_id, slice_keys=slice_keys,
                                     mark_count_after=mark_count_after,
                                     created=created)],
            course_id=course_id,
        )
        if out.get("failed"):
            print(_json.dumps({"event": "mark_event_insert_failed", "result": out},
                              ensure_ascii=False))
        return {**out, "event_type": "confusion_mark_created", "course_id": course_id}
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        print(_json.dumps({"event": "mark_event_failed", "error": err},
                          ensure_ascii=False))
        return {"emitted": 0, "attempted": 0, "failed": 0, "error": err,
                "event_type": "confusion_mark_created"}


def _record_upload_event(
    db: Any, p: Any, *, session_id: str | None, course_id: str,
    event_type: str, payload: dict[str, Any], deck_id: str | None,
) -> dict[str, Any]:
    """发一条上传事件。**永不抛**——埋点失败不打断上传（口径 F42）。

    ## `arm` 在这里懒取，而不是把上传路由改成依赖 `style_row`

    表上有 `check (arm = f1_assign_arm(user_id))`，所以必须知道这个用户的臂。
    最省事的写法是让上传路由也 `Depends(style_row)`，**但那会把一次埋点的前置
    变成上传本身的前置**：`style_config` 读不到（0 行是坏状态，口径 F09）时
    上传会直接 500，而**上传压根不需要风格配置**。

    一条埋点不该让用户传不上课件。所以这里自己去读，读不到就不发这条事件——
    与「没有 `session_id` 就不发」是同一条处置：**少发事件是可见的损失**。

    ⚠️ 失败**不静默**：打一行结构化日志，**并把结果返回给调用方放进响应**。

    ## 🔁 这里推翻了上一批自己的一个决定（2026-09-13）

    上一批写的是：「日志够了，因为上传是低频动作 —— 上传的响应体是给页面渲染用的，
    塞一个埋点结果进去会让页面多一条与用户无关的分支。」

    **那个取舍是错的**，而这一次的人工验收就是代价：验收的人想确认
    `deck_uploaded` 的 `deck_id` 到底是不是 null，而**从外面无从确认**——
    客户端对 `style_events` 只有 insert 权限（读事件走 service_role，
    而验收脚本刻意不持有它）。于是只能去 SQL Editor 手查，
    而一次查错地方（`payload->>'deck_id'`，它其实是列）就读成了「功能没做」。

    `/explain` 从一开始就把 `events` 放进响应，而**那正是「四个讲解事件真的落库」
    当初能被验证的唯一途径**。两条路径上同一个需求，我给了不同的答案，
    理由是「页面简洁」——**拿可验证性换页面简洁，是拿一个响亮的失败换一个安静的**。

    页面那条「与用户无关的分支」代价其实是零：页面可以不读这个字段。
    而没有它，任何人都无法自证埋点有没有落库。
    """
    import json as _json

    from .events import complete_event, record_events

    if not session_id:
        skipped = ("没有 session_id：一次页面加载 = 一个 session，服务端不现编"
                   "（编了会把「平均会话轮次」这个北极星指标改成假数）")
        print(_json.dumps({"event": "upload_event_skipped",
                           "event_type": event_type, "reason": skipped},
                          ensure_ascii=False))
        return {"emitted": 0, "attempted": 0, "failed": 0, "skipped_reason": skipped}
    try:
        arm = db.style_row(p.user_id, p.token).f1_arm
        out = record_events(
            db, token=p.token, user_id=p.user_id, arm=arm, session_id=session_id,
            # **走 complete_event，不直接拼 dict**：缺必需字段要在构造时抛，
            # 不能交给数据库去 23514 —— 被拒的事件是静默消失的。
            events=[complete_event(event_type, payload)],
            course_id=course_id, deck_id=deck_id,
        )
        if out.get("failed"):
            print(_json.dumps({"event": "upload_event_insert_failed",
                               "event_type": event_type, "result": out},
                              ensure_ascii=False))
        # **把「这一行到底带没带 deck_id」也回传。** 验收要确认的正是它，
        # 而从外面读不到事件表（客户端只有 insert 权限）。
        return {**out, "event_type": event_type, "deck_id": deck_id,
                "course_id": course_id}
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        print(_json.dumps({"event": "upload_event_failed", "event_type": event_type,
                           "error": err}, ensure_ascii=False))
        return {"emitted": 0, "attempted": 0, "failed": 0, "error": err,
                "event_type": event_type, "deck_id": deck_id}


def _default_embedder() -> Any:
    """真嵌入器。**这是 PRD §13.3 的第二次出境**——切片原文 → OpenAI。

    模型与维度从 `retrieval.selection` 读（那是它们在代码里的唯一出处）。
    """
    from retrieval.embed import OpenAIEmbedder
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL

    return OpenAIEmbedder(SELECTED_MODEL, SELECTED_DIMENSIONS)


_COURSE_LABEL = "合成课件（未指定课程：检索走阶段 3 的替身）"


def _course_retriever(db: Any, token: str, course_id: str, make_embedder: Any) -> Any:
    """真检索后端。**每次请求新建，绝不缓存**——它持有某一个用户的 JWT。

    与下面 `_default_retriever()` 的 `@lru_cache` 对照着看：那个缓存的是替身，
    替身不持有任何人的东西。
    """
    from .retriever import SupabaseRetriever

    return SupabaseRetriever(store=db, embedder=make_embedder(),
                             token=token, course_id=course_id)


def _default_model() -> Any:
    from explain.llm import AnthropicModel

    return AnthropicModel()


@lru_cache(maxsize=1)
def _default_retriever() -> Any:
    """固定语料：F1 的自写合成课件。

    ⚠️ **这是替身，不是检索**（见模块 docstring）。用它做风格断言合法，
    做召回断言不合法。合成课件是 PRD §12.1 指定的对外展示素材（口径 F02），
    所以它同时也是这一阶段唯一能合法喂给模型的课件。
    """
    from pathlib import Path

    from explain.context import ContextSlice
    from explain.retrieval_adapter import InMemoryRetriever

    deck = (Path(__file__).resolve().parents[2] / "f1_samples" / "synthetic_deck.md").read_text(
        encoding="utf-8"
    )
    return InMemoryRetriever(
        default_slices=(
            ContextSlice(
                source_file="synthetic_deck.md",
                slice_id="whole",
                page_indices=(1,),
                title=None,
                text=deck,
                char_count=len(deck),
                score=None,
                via="hit",
            ),
        ),
        meta={"backend": "in_memory", "note": "阶段 3 替身，不得据此出召回结论"},
    )


def app_from_env() -> FastAPI:
    """uvicorn 的入口（`--factory`）。

    **缺环境变量在这里就炸**，不是等到第一个请求 —— 配错了的服务
    「起来了但连的是另一个项目」是静默的，而启动失败不是。
    """
    return create_app()
