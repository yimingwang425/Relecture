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
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import followup as _followup
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
    #:
    #: ⚠️ **给了 `conversation_id` 时这个字段不作数**（2026-09-20）：
    #: 那种情况下真实轮次由服务端从 `messages` 里算（`next_turn_index()`）。
    #: 两边各算一份的话，刷新页面之后前端会从 1 重新数，而「平均会话轮次」
    #: 是北极星指标（PRD §3.1）——那个数会静静地变小，没有任何东西会报错。
    #: 响应里回传 `turn_index`，是**这一轮真正用的那个**。
    turn_index: int = 1
    #: 这一轮属于哪个会话（F5）。**可选**：不给就是一次性的问答，不落库、无历史。
    #:
    #: 给了的话这一轮会做三件事：读这个会话已有的消息当历史、把一问一答写回去、
    #: 轮次从库里算。会话行本身由**浏览器**建（标题是纯 UI 状态，
    #: 服务端不产出它的任何一个字段），所以这里只认 id、不建行。
    conversation_id: str | None = None


class RetrieveRequest(BaseModel):
    """评测用：只检索、不生成（口径 F97 的测量手段）。"""

    model_config = {"extra": "forbid"}   # 同 ExplainRequest

    question: str = Field(min_length=1, max_length=500)
    #: 关掉哪几环。**闭集**，认不出的值 422——拼错一个就等于「什么都没关」，
    #: 而那样两组对照会是同一个东西、差异恒为 0。
    ablate: list[Literal["rewrite", "lexical", "rerank"]] = Field(default_factory=list)


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


#: 上传体积上限。沿用 `parse_service.limits.MAX_UPLOAD_BYTES` 的那个数与它的来源说明
#: （**占位待定**，现值是测试集最大文件的近 30 倍，不会误伤）——
#: 不在这里写第二个数字。
def _max_upload_bytes() -> int:
    from parse_service.limits import MAX_UPLOAD_BYTES

    return MAX_UPLOAD_BYTES


def create_app(
    settings: Settings | None = None,
    verifier: TokenVerifier | None = None,
    supabase: SupabaseClient | None = None,
    retriever: Any | None = None,
    model_factory: Any | None = None,
    embedder_factory: Any | None = None,
    gap_checker_factory: Any | None = None,
    rag_model_factory: Any | None = None,
    vision_factory: Any | None = None,
) -> FastAPI:
    """组装。每个依赖都可注入，**为的是断言能喂真东西**。

    例如验签：断言里注入一把本地生成的 EC 私钥签出的真 token，
    走的仍然是 `jwt.decode` 那条真实路径，而不是一个被打桩掉的替身（通则七 #7）。
    """
    cfg = settings or load_settings()

    # ---- 检索助手（改写 / 重排 / 定位语）与图片页转写用的模型（口径 F96–F100）。
    #
    # 生产：配了 key 就是真的 Haiku。测试：**注入了讲解模型而没注入检索助手时，
    # 不造真的检索助手**——否则每条测试都会真的出网，失败后走超时回落，
    # 测试照样绿、只是每条慢 10 秒，而且测的已经不是它声称的那件事。
    def make_rag_model(max_tokens: int, *, cache_system: bool = False) -> Any | None:
        if rag_model_factory is not None:
            return rag_model_factory()
        if model_factory is not None or cfg.anthropic_api_key is None:
            return None
        from explain.rag import build_rag_model

        return build_rag_model(max_tokens, cache_system=cache_system)
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
            # ---- 允许的前端来源（2026-09-21）。
            #
            # **不是秘密**：这些来源就是公开的网址，而且任何人打一次预检就能
            # 逐个试出来。把它说出来换到的是：「我这个域名在不在白名单里」
            # 从一道猜谜变成一个事实。
            #
            # 2026-09-21 真的猜错过一次：建课报 `TypeError: Failed to fetch`，
            # 而前端那句话说「可能是它没部署，或 CORS 里没有本站来源」——
            # 两个猜测都不对（服务部署好了、来源也在白名单里，是**冷启动**）。
            # 于是时间花在翻 Render 控制台上。
            #
            # ⚠️ 空清单也要照实报成 `[]`。**不配就是一律挡住**，不是默认放开；
            # 而 `[]` 与「这个字段不存在」在排错时含义完全不同。
            "cors_allow_origins": list(cfg.cors_allow_origins),
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

    def _prepare_explain(req: ExplainRequest, p: Principal, row: StyleRow) -> dict[str, Any]:
        """一轮讲解里**会以 HTTP 状态码失败**的那一半：配置、课程、会话。

        拆出来是为了流式（口径 F94）：SSE 一旦开始写，状态码就已经是 200 了。
        「不是你的课」「key 没配」这类错必须在开流之前判完，仍然是 404 / 503——
        否则前端拿到一个 200 的流，里面写着失败，而重试与否的判断全靠状态码。
        """
        from explain.style_axes import style_config_for

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
                                       embedder_factory or _default_embedder, make_rag_model)
            label = course["name"]
        else:
            active = _default_retriever()
            label = _COURSE_LABEL

        # ---- F5 · 会话历史。**给了 conversation_id 才有历史**，否则这一轮是孤立的。
        #
        # 历史喂的是「**用户当时问的那句话** + 最终答案」，不是当时那一整个渲染过的
        # user turn（它带着那一轮检索到的片段、兜底判断的限定语、F4 的注入语）。
        # 两个理由，都不是省事：
        #   · 每轮的片段都是**那一轮**检索的，本轮会重新检索一遍；把旧片段再带上
        #     既不让模型多知道什么，又按轮数线性撑大请求体（实测每轮 1100–1500 字符）；
        #   · 限定语是**当时那一轮的状态**（他那一轮点了「没看懂」）。带进历史等于
        #     让它对之后每一轮都生效，而用户只点了一次。
        # 代价如实记：历史里的提问不含它当时看到的材料，所以模型对旧轮的细节
        # 只能依赖自己上一轮说过的话。这是有意的取舍，不是疏漏。
        history: list[dict[str, Any]] = []
        turn_index = req.turn_index
        if req.conversation_id:
            conv = db.conversation(p.token, req.conversation_id)
            if conv is None:
                # 同 `course` 那条：**不区分「不存在」与「是别人的」**。
                raise HTTPException(404, "找不到这个会话（可能不存在，也可能不是你的）")
            for m in db.conversation_history(p.token, req.conversation_id):
                history.append({"role": m["role"], "content": m["content"]})
            turn_index = db.next_turn_index(p.token, req.conversation_id)

        # 查询改写要用历史做指代消解（口径 F96）。**同一份历史**，不另读一遍。
        if hasattr(active, "history"):
            active.history = list(history)

        return {"style": style, "active": active, "label": label,
                "history": history, "turn_index": turn_index}

    def _run_explain(req: ExplainRequest, p: Principal, row: StyleRow,
                     prep: dict[str, Any], emit: Any, started_at: float) -> dict[str, Any]:
        """一轮讲解里**只会在流里失败**的那一半：检索、判断、生成、落库、埋点。

        `emit` 收流式事件；JSON 路由传一个什么都不做的，两条路由跑的是同一段代码。
        """
        from explain.pipeline import Trajectory, run_round
        from explain.review import RuleReviewer
        from explain.style_prompt import render_all_style_rules

        style, active, label = prep["style"], prep["active"], prep["label"]
        history, turn_index = prep["history"], prep["turn_index"]

        # ---- F4 · 检索**提到这里做**，因为注入要在生成之前拿到本轮的 slice_ids。
        #
        # `run_round(context=...)` 是既有参数（口径 X02：两组对照共用同一份
        # ContextPack），不是为本轮新开的口子。检索仍然只发生一次。
        import uuid as _uuid

        emit({"type": "stage", "stage": "retrieving"})
        ctx = active.fetch(req.question)
        #: 讲解轮标识（口径 F65）。**服务端生成**——响应里此前没有任何稳定的轮
        #: 标识（`retrieval.signature` 只认提问与命中切片，同一个人把同一个问题
        #: 问两遍会得到同一个值，拿它当幂等键会让第二轮的标记覆盖第一轮）。
        #: 一次 `/explain` = 一轮；轮内的重生成不新开一个（重生成不计入轮次，
        #: 轮的定义是「用户可见的一次输出」，PRD §13.0）。
        round_id = str(_uuid.uuid4())
        slice_keys = _marks.slice_keys_of(ctx)
        confusion = _confusion_for_round(db, p, slice_keys)
        # 出处先发：正文还在路上时，用户已经能看到这一轮用了哪几页。
        emit({"type": "retrieval", "round_id": round_id, "slice_keys": slice_keys,
              "retrieval": ctx.to_dict(), "confusion": confusion})
        emit({"type": "stage", "stage": "generating"})

        # ---- 兜底判断（F3b / PRD §10.1）。**接进生产，不加开关**（口径 F70）。
        #
        # 此前这里不传 `gap_checker`，`run_round` 回落到 `NullGapChecker`，
        # `gap_verdict` 恒 null——F3b 的实测数全部来自实验侧，而没有任何东西在核对
        # 「线上到底接没接」。再加一个开关就是再造一个同样的分叉；要停用就 revert。
        #
        # `GuardedGapChecker`：判断抛异常时讲解照常返回，`gap_verdict=null` 并带原因。
        # 注入的 factory 也套它——测试走的就是生产这一层包装。
        from explain.gap import GuardedGapChecker

        make_gap = gap_checker_factory or _default_gap_checker
        gap_checker = GuardedGapChecker(make_gap())

        make_model = model_factory or _default_model
        result = run_round(
            round_index=1,
            question=req.question,
            trajectory=Trajectory(history=history),
            retriever=active,
            context=ctx,
            model=make_model(),
            reviewer=RuleReviewer(style),
            # ← A 类 + B 类，拼法与 pairs.json 里 16 个样例逐字节同形（口径 F19/F45）
            # 语气那一行：默认 neutral 渲染成空，这一段与样例的生成条件逐字节相同。
            style_rules=render_all_style_rules(style, row.b_class, row.tone),
            course_label=label,
            review_enabled=True,
            # F4 的限定语进**本轮用户消息**，不进 system（口径 F57）：
            # marks 逐轮变化，而 system 是按「会话内不变」设计的。
            # 追问的限定语（口径 F107）跟在它后面：有历史才加，单轮一个字不多。
            turn_prefix="\n\n".join(
                x for x in (confusion["note"] or "", _followup.followup_prefix(history)) if x),
            # G03a 的限定语（非 `covered` 且该课件跳过页非空）由它产出、
            # 由 `build_user_turn` 渲染进本轮消息。**跳过页不给判断模型**（G01 末条）。
            gap_checker=gap_checker,
            on_event=emit,
            started_at=started_at,
        )

        ev = result.final_review.evaluation

        # ---- F5 · 把这一问一答写回会话。**在发事件之前**，理由与事件同一条
        # （口径 F42）：写库失败**不打断这次讲解**，但也不静默——结果进响应。
        #
        # ⚠️ 一问一答**共用同一个 turn_index**。分开编号的话「平均会话轮次」
        # 会当场翻倍，而那是北极星指标。
        persisted: dict[str, Any] = {"written": 0, "attempted": 0}
        if req.conversation_id:
            persisted["attempted"] = 2
            try:
                # ⚠️ **两行的键必须一模一样**（2026-10-08 生产实测）：PostgREST 批量插入时
                # 各对象键不同就 400 PGRST102「All object keys must match」，**整批一行都不写**。
                # 原来问那一行没有 round_id / meta，于是从 F5 上线起会话一条消息都没存进去——
                # 而这里吞掉异常不打断讲解（F42），屏幕上一切正常，刷新之后对话是空的。
                # round_id 显式给 None（问那一行本来就没有，null 是事实）；meta 给 {}，
                # 不给 None：那一列 not null，传 null 会换一种方式整批失败。
                db.insert_messages(token=p.token, rows=[
                    {"owner": p.user_id, "conversation_id": req.conversation_id,
                     "role": "user", "content": req.question, "turn_index": turn_index,
                     "round_id": None, "meta": {}},
                    {"owner": p.user_id, "conversation_id": req.conversation_id,
                     "role": "assistant", "content": result.final_text,
                     "turn_index": turn_index, "round_id": round_id,
                     # 重新打开这个会话时，这一轮要能**原样重现**：出处面板、
                     # 兜底判断那条提示、诊断里的那几个数。所以 meta 装的是
                     # `answerFromResponse()` 读的那一组字段，一个不少——
                     # 少一个的表现不是报错，是历史轮次的出处面板莫名其妙地空着。
                     #
                     # **不进 content**：content 是讲解正文，混进结构化数据之后
                     # 「这条消息说了什么」就不再是一个字符串能回答的问题了。
                     "meta": {
                         # **写 `result.context`，不写上面那个 `ctx`。**
                         # 两者目前是同一个对象（`run_round(context=ctx)` 原样带着走），
                         # 但依赖这一点等于让「落库的出处」跟着 run_round 的内部实现走：
                         # 哪天它在轮内重取一次，历史里存的就还是旧的那一份，
                         # 而页面上显示得完全正常。
                         "retrieval": result.context.to_dict(),
                         "gap": result.gap.to_dict(),
                         "slice_keys": slice_keys,
                         "confusion": confusion,
                         "accepted": result.accepted,
                         "regenerations": result.regenerations,
                         "detail_level": row.detail_level,
                         "mean_paragraph_chars": ev.by_name("paragraph_length").raw,
                     }},
                ])
                persisted["written"] = 2
            except Exception as exc:  # noqa: BLE001
                persisted["error"] = f"{type(exc).__name__}: {exc}"

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
                # **用真正跑的那个轮次**，不是请求里带来的那个（见 `turn_index` 的注释）。
                course_id=req.course_id, turn_index=turn_index,
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

        ctx = result.context
        return {
            # 埋点的结果**进响应**。写不进去不打断讲解，但也不许静默（口径 F42）。
            "events": events_result,
            # ---- F4（口径 F65）。这两个是 `POST /marks` 的入参来源：
            # 前端**原样带回来**，不自己拼、不自己编。
            "round_id": round_id,
            "slice_keys": slice_keys,
            # ---- F5。`turn_index` 是**这一轮真正用的那个**（给了 conversation_id
            # 时由服务端从库里算）。前端据此显示轮次，不再自己数。
            "conversation_id": req.conversation_id,
            "turn_index": turn_index,
            "persisted": persisted,
            # 注入到底触发没触发，**从外面看得见**（口径 F53：验收要确认的东西，
            # 系统必须能自己答出来）。§九的判据「注入组的 messages 里必须含限定语」
            # 靠它机械可查，不靠人去读那段讲解。
            "confusion": confusion,
            # 兜底判断的结果**从外面看得见**（口径 F53）。部署后验收第 4 步读的就是它：
            # `gap_implementation` 是 "model" 而 `gap_verdict` 不是 null，才算线上真的接上了。
            "gap": result.gap.to_dict(),
            "text": result.final_text,
            "accepted": result.accepted,
            "regenerations": result.regenerations,
            # ---- 延迟（口径 F93 / F94）。三个数各管一件事：首字是用户感到的等待，
            # 关键路径可由各次调用自报的耗时复算，墙钟是真的等了多久。
            "latency": {
                "first_token_ms": result.first_token_latency_ms,
                "critical_path_ms": round(result.latency_ms, 1),
                "wall_ms": result.wall_ms,
                "speculative_discarded": len(result.discarded),
            },
            # ---- 检索（阶段 5）。**原样回传 backend**，让「真检索」与「替身」
            # 在响应里分得开；`slices` 里带 document_id 之外的全部身份信息，
            # §13.0 的 `retrieval_executed` 就从这里取。
            "course_id": req.course_id,
            "retrieval": ctx.to_dict(),
            # 这三个是验收要读的：配置真的进了 prompt 吗、真的改变了输出吗。
            "detail_level": row.detail_level,
            "tone": row.tone,
            "config_fingerprint": style.fingerprint(),
            "paragraph_length_target": style.paragraph_length.to_dict(),
            "mean_paragraph_chars": ev.by_name("paragraph_length").raw,
            "all_activated_passed": ev.all_activated_passed,
            "style_rules": result.system_text.split("## 材料范围")[0].strip(),
        }

    @app.post("/explain")
    def explain(req: ExplainRequest, both: tuple[Principal, StyleRow] = Depends(style_row)) -> dict[str, Any]:
        """一轮讲解（整段返回）。**这是 `style_config` 那一行第一次影响模型输出。**

        阶段 5 起 `course_id` 可选：给了就真检索那门课的语料
        （`SupabaseRetriever`，RLS 由数据库执行），不给走阶段 3 的替身。

        验收脚本与不需要流式的调用方用它；页面用 `/explain/stream`。
        两条路由共用 `_prepare_explain` + `_run_explain`，**不是两份实现**。
        """
        import time as _time

        started = _time.perf_counter()
        p, row = both
        prep = _prepare_explain(req, p, row)
        return _run_explain(req, p, row, prep, lambda _e: None, started)

    @app.post("/explain/stream")
    def explain_stream(req: ExplainRequest,
                       both: tuple[Principal, StyleRow] = Depends(style_row)) -> Any:
        """一轮讲解，**SSE 流式**（口径 F94）。

        事件（`event:` 行是类型，`data:` 行是 JSON）：

        | 类型 | 什么时候 | 内容 |
        | --- | --- | --- |
        | `stage` | 进入检索 / 生成 | `{"stage": "retrieving" 或 "generating"}` |
        | `retrieval` | 检索完 | 出处、`round_id`、`slice_keys`、`confusion` |
        | `gap` | 兜底判断返回 | 与响应里 `gap` 同形 |
        | `delta` | 每段正文 | `{"text": "..."}` |
        | `reset` | 审查不达标、要重生成 | 前端清掉已显示的那一版 |
        | `done` | 结束 | **与 `/explain` 的响应体逐字段相同** |
        | `error` | 开流之后失败 | `{"status", "detail"}` |

        ⚠️ **开流之前能判的错仍然走状态码**（`_prepare_explain`）：
        课不存在 404、key 没配 503、请求体不合法 422。开流之后状态码已经是 200，
        所以之后的失败只能是 `error` 事件——它带一个 `status`，语义同状态码。

        ⚠️ **用户关掉页面，这一轮仍会跑完并落库**：生成在后台线程里，
        流断了只是不再往外写。这是有意的——落库的那一问一答是会话历史，
        半截中断的话下次打开会话会少一轮，而用户其实看到了大半段。
        """
        import json as _json
        import queue as _queue
        import threading as _threading
        import time as _time

        from fastapi.responses import StreamingResponse

        started = _time.perf_counter()
        p, row = both
        prep = _prepare_explain(req, p, row)
        q: _queue.Queue[dict[str, Any] | None] = _queue.Queue()

        def work() -> None:
            try:
                out = _run_explain(req, p, row, prep, q.put, started)
                q.put({"type": "done", "response": out})
            except HTTPException as exc:
                q.put({"type": "error", "status": exc.status_code, "detail": exc.detail})
            except Exception as exc:  # noqa: BLE001 —— 开流之后只能用事件报错
                q.put({"type": "error", "status": 500,
                       "detail": f"{type(exc).__name__}: {exc}"})
            finally:
                q.put(None)

        _threading.Thread(target=work, name="explain-stream", daemon=True).start()

        def sse() -> Any:
            while True:
                try:
                    item = q.get(timeout=_SSE_KEEPALIVE_S)
                except _queue.Empty:
                    # 注释行。代理与浏览器都忽略它，但它让连接不被当成空闲掐掉。
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    return
                kind = item.get("type", "message")
                data = {k: v for k, v in item.items() if k != "type"}
                yield f"event: {kind}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            # 关掉反向代理的缓冲，否则「流式」会变成「攒够一整段再一次性到」。
            "X-Accel-Buffering": "no",
        })

    # ---------------------------------------------------------------- F4 · 「这部分没看懂」
    #
    # 路径与其余五条对齐（`/explain`、`/courses`、`/courses/{id}/documents`、
    # `/me/style`、`/health`），**不带 `/api/v1` 前缀**。任务书上写的是
    # `/api/v1/marks`，2026-09-13 改齐：那时前端还没调它，改只动这一处；
    # 留着的话，要么日后统一时动三条前端已经在调的路由（失败的样子是页面白屏），
    # 要么它永远是路由表里唯一一条长得不一样的。
    @app.post("/marks")
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

    @app.post("/courses/{course_id}/retrieve")
    def retrieve(course_id: str, req: RetrieveRequest,
                 p: Principal = Depends(principal)) -> dict[str, Any]:
        """**只检索、不生成**。给 `api/scripts/eval_retrieval_prod.py` 用（口径 F97）。

        混合检索的四个环节（改写 / 词法 / 融合 / 重排）在本轮**没有实测收益**。
        这个端点让评测集能在真库、真模型上逐环节对照，花的只是检索那一点钱
        （每题两次 Haiku + 一次嵌入），而不是每题一次完整讲解。
        """
        if db.course(p.token, course_id) is None:
            raise HTTPException(404, "找不到这门课（可能不存在，也可能不是你的）")
        r = _course_retriever(db, p.token, course_id,
                              embedder_factory or _default_embedder, make_rag_model)
        r.ablate = frozenset(req.ablate)
        try:
            ctx = r.fetch(req.question)
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        return {"retrieval": ctx.to_dict(), "slice_keys": _marks.slice_keys_of(ctx)}

    @app.post("/courses/{course_id}/reindex")
    def reindex_course(course_id: str, p: Principal = Depends(principal)) -> dict[str, Any]:
        """把这门课里**还在老口径上**的文档升级到当前的检索索引（口径 F97 / F99）。

        老文档不升级也检索得到（向量一路按它自己的口径取行），只是吃不到
        词法一路与定位语。逐份做、逐份报；一份失败不挡其余几份。详见 `reindex.py`。
        """
        from explain.rag import Contextualizer

        from .indexing import INDEX_RULESET
        from .reindex import reindex_document

        if db.course(p.token, course_id) is None:
            raise HTTPException(404, "找不到这门课（可能不存在，也可能不是你的）")
        try:
            docs = db.documents_in_course(p.token, course_id)
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

        ctx_model = make_rag_model(2000, cache_system=True)
        make_embedder = embedder_factory or _default_embedder
        done: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        current = 0
        for d in docs:
            if d.get("index_ruleset") == INDEX_RULESET:
                current += 1
                continue
            try:
                done.append(reindex_document(
                    store=db, token=p.token, owner=p.user_id, document=d,
                    embedder=make_embedder(),
                    contextualizer=Contextualizer(ctx_model) if ctx_model is not None else None))
            except Exception as exc:  # noqa: BLE001 —— 一份失败不挡其余几份，但要说出来
                failed.append({"document_id": str(d["id"]), "error": f"{type(exc).__name__}: {exc}"})
        return {"index_ruleset": INDEX_RULESET, "already_current": current,
                "reindexed": done, "failed": failed}

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

        from .ingest import UnusableDocument, ingest_document
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
            # 定位语与图片页转写（口径 F99 / F100）。拿不到模型（没配 key、测试没注入）
            # 时是 None：上传照常，定位语为空、图片页照旧是跳过页。
            from explain.rag import Contextualizer, VisionTranscriber

            ctx_model = make_rag_model(2000, cache_system=True)
            if vision_factory is not None:
                vision = vision_factory()
            else:
                vis_model = make_rag_model(1500)
                vision = VisionTranscriber(vis_model) if vis_model is not None else None
            try:
                result = ingest_document(
                    data=data, filename=name, owner=p.user_id, course_id=course_id,
                    token=p.token, store=db, embedder=make_embedder(),
                    find_existing=lambda sha: db.find_document_by_sha(
                        token=p.token, course_id=course_id, sha=sha),
                    contextualizer=Contextualizer(ctx_model) if ctx_model is not None else None,
                    vision=vision,
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
            except UnusableDocument as exc:
                # ⚠️ **必须排在下面那个裸 `except Exception` 前面。**
                # 排在后面会被归成 `unparsable`（「这个文件我们打不开」），而事实是
                # 文件打开了、解析成功了、只是一个字都读不出来。归错之后用户会去
                # 换一个「没坏的」文件，然后再传一次同一份扫描件。
                raise _UploadFailure(
                    "no_usable_text",
                    f"{exc.message}：{exc.reason}",
                    extra={"bytes": len(data),
                           "page_count": exc.page_count,
                           "unusable_page_count": exc.unusable_page_count},
                ) from exc
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
                # **null = 未检查**（口径 F68 / F69）。用户上传不做受限页排除，
                # `ingest_document` 从来没收到过受限页清单，于是它返回的 0
                # 说的是「清单为空」，不是「检查过、没有受限页」。写 0 就是主动产生假证据。
                # 将来真有排除机制，这里才可能出现整数。
                "restricted_pages_excluded": None,
                "bytes": len(data),
                "duration_ms": ms(),
                # ---- 2026-10-07（口径 F97 / F99 / F100）。都不进必需清单：
                # 老构建发的事件没有它们，进了清单会让那批事件被静默拒掉（F50）。
                "index_ruleset": result.index_ruleset,
                "vision_page_count": result.vision_page_count,
                "vision_skipped_over_cap": result.vision_skipped_over_cap,
                "contextualized_count": result.contextualized_count,
                "ingest_llm_cost_usd": result.llm_cost_usd,
            },
            deck_id=result.document_id,
        )

        return {
            "document_id": result.document_id,
            "slice_count": result.slice_count,
            "embedded_count": result.embedded_count,
            "skipped_pages": result.skipped_pages,
            "restricted_pages_excluded": None,   # 同上：未检查，不是 0
            "verdict": result.verdict,
            # 转写救回了几页、几片有定位语（口径 F99 / F100）。上传页据此说清
            # 「第 N 页是图，已经转写成文字」——否则用户会以为图里的内容仍然读不了。
            "vision_page_count": result.vision_page_count,
            "vision_skipped_over_cap": result.vision_skipped_over_cap,
            "contextualized_count": result.contextualized_count,
            "index_ruleset": result.index_ruleset,
            "ingest_llm_cost_usd": result.llm_cost_usd,
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

#: SSE 保活间隔（秒）。生成开始前（检索 + 首字前的思考）可能十几秒没有任何事件，
#: 15 s 远低于常见代理的 60 s 空闲超时。**不是阈值判断**，只是不让连接被掐。
_SSE_KEEPALIVE_S = 15.0


def _course_retriever(db: Any, token: str, course_id: str, make_embedder: Any,
                      make_rag_model: Any = None) -> Any:
    """真检索后端。**每次请求新建，绝不缓存**——它持有某一个用户的 JWT。

    与下面 `_default_retriever()` 的 `@lru_cache` 对照着看：那个缓存的是替身，
    替身不持有任何人的东西。

    改写器与重排器（口径 F96 / F98）在拿得到模型时接上，拿不到就是 `None`，
    检索退回「原问题 + 融合排名」——`meta.rewrite/rerank.implementation` 会写 `none`。
    """
    from explain.rag import LlmReranker, QueryRewriter

    from .retriever import SupabaseRetriever

    rw_model = make_rag_model(400) if make_rag_model else None
    rr_model = make_rag_model(200) if make_rag_model else None
    return SupabaseRetriever(
        store=db, embedder=make_embedder(), token=token, course_id=course_id,
        rewriter=QueryRewriter(rw_model) if rw_model is not None else None,
        reranker=LlmReranker(rr_model) if rr_model is not None else None,
    )


def _default_model() -> Any:
    from explain.llm import AnthropicModel

    return AnthropicModel()


def _default_gap_checker() -> Any:
    """生产的兜底判断器。**构造处只有 `explain.gap.build_model_gap_checker` 一个**，
    实验侧也调它（口径 F70）——这里不写模型 id、不写任何参数。"""
    from explain.gap import build_model_gap_checker

    return build_model_gap_checker()


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
