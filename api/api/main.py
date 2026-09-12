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

from .auth import AuthError, HttpJwks, Principal, TokenVerifier, bearer_token
from .build import build_commit
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

        make_model = model_factory or _default_model
        result = run_round(
            round_index=1,
            question=req.question,
            trajectory=Trajectory(),
            retriever=active,
            model=make_model(),
            reviewer=RuleReviewer(style),
            # ← A 类 + B 类，拼法与 pairs.json 里 16 个样例逐字节同形（口径 F19/F45）
            style_rules=render_all_style_rules(style, row.b_class),
            course_label=label,
            review_enabled=True,
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
        p: Principal = Depends(principal),
    ) -> dict[str, Any]:
        """上传一份课件。**一次事务性的动作**（见 `ingest.py` 的模块说明）。"""
        from .ingest import DuplicateDocument as _Dup  # noqa: F401  —— 保持符号可见
        from .ingest import ingest_document
        from .supabase import DuplicateDocument

        name = file.filename or "upload"
        if not name.lower().endswith(".pdf"):
            # 区分「不支持」与「还没做」——PPTX 的 schema 已定，解析器未交付。
            # 压成一句「不支持」会让人以为是永久不支持而去做别的兜底。
            if name.lower().endswith(".pptx"):
                raise HTTPException(501, "PPTX 解析尚未实现（schema 已定义，解析器为下一次交付）")
            raise HTTPException(415, f"仅支持 .pdf，收到 {name}")

        data = await file.read()
        if not data:
            raise HTTPException(400, "文件为空")
        limit = _max_upload_bytes()
        if len(data) > limit:
            raise HTTPException(413, f"文件超过 {limit // 1024 // 1024} MB 上限")

        make_embedder = embedder_factory or _default_embedder
        try:
            result = ingest_document(
                data=data, filename=name, owner=p.user_id, course_id=course_id,
                token=p.token, store=db, embedder=make_embedder(),
            )
        except DuplicateDocument as exc:
            # 数据库那条唯一索引是**硬失败 23505**，不是「提示」。翻译成人话。
            raise HTTPException(status_code=409, detail=exc.message) from exc
        except SupabaseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        except Exception as exc:  # 解析器对损坏文件抛的异常类型不统一
            raise HTTPException(422, f"无法处理该 PDF：{exc}") from exc

        return {
            "document_id": result.document_id,
            "slice_count": result.slice_count,
            "embedded_count": result.embedded_count,
            "skipped_pages": result.skipped_pages,
            "verdict": result.verdict,
        }

    return app


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
