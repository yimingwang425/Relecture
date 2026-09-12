"""以**用户身份**访问 Supabase。

## 一条纪律：请求带的是用户自己的 JWT

服务端不持有 service_role key（口径 F40），也不替用户做行级过滤。
每个请求同时带两样：

- `apikey`：publishable key，Supabase 网关用它认项目（本来就是公开的）
- `Authorization: Bearer <用户的 JWT>`：PostgREST 据此设置 `auth.uid()`，**RLS 由数据库执行**

于是这一跳与前端直连 Supabase 走的是**同一条数据库路径**。F1 阶段 1.5 论证过
「多一跳 FastAPI 只是把 RLS 从数据库执行变成应用转发，不增加保障」——那条仍然成立，
所以这里刻意**不**把 RLS 搬到应用层重做一遍。这一跳存在的理由是跑 Python，不是转述权限。

## 读不到行时，三种情况必须分开

`style_config` 一人一行由注册触发器保证（口径 F09）。读到 0 行是**坏状态**，
不是「用户还没配置」——他会进不了 onboarding、拿不到臂。所以这里照搬
`web/lib/style/session.ts` 的三分：`ok` / `error` / `bad-row-count`，
**不用 `.single()`**（那会把「0 行」和「被 RLS 挡住」压成同一个错误对象）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["StyleRow", "SupabaseError", "DuplicateDocument", "SupabaseClient"]

STYLE_COLUMNS = "user_id,detail_level,use_examples,structure,depth,f1_arm,onboarded_at"


class SupabaseError(Exception):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class DuplicateDocument(SupabaseError):
    """同一用户、同一课程、同一份文件（唯一索引 `documents_owner_course_sha_idx`）。

    ⚠️ 数据库那条是**硬失败 23505，不是「提示」**。不翻译的话用户看到的是
    一个数据库错误码。翻译在这里做，而不是让每个调用方各写一遍。

    ## 🔀 **本模块是这个异常唯一的定义处**（2026-09-12 合并）

    此前 `api/api/ingest.py` 里还有一个**同名同义**的 `DuplicateDocument(Exception)`，
    而 `main.py` 同时 import 两个（其中一个带着
    `# noqa: F401 —— 保持符号可见` 这样的注释，说明当时就觉得别扭）。

    两个类的代价在阶段 6 第一批当场兑现：「先查后提示」在 `ingest.py` 里抛的是
    **它自己那个**，而路由 `except` 的是**本模块这个** —— 两者无继承关系，
    于是那条重复提示会掉进兜底的 `except Exception`，被报成
    **422「无法处理该 PDF」**。被 `test_pre_check_names_the_existing_document` 抓出来。

    **同名同义的两个类，比两个不同名的类更危险**：读代码的人看到 `DuplicateDocument`
    不会去确认它是哪一个，而 `except` 只认对象的真实类型。

    ## `existing` 分开两条路

    两条路都抛这个类，用 `existing` 分辨：
      · **先查查到的** → `existing` 是已存在那一行，提示说得出是哪一份、什么时候传的；
      · **唯一索引拦下的** → `existing is None`（先查后插之间的窗口，两个标签页同时传）。
    两层都在，不是一层换一层。
    """

    def __init__(self, message: str, *, existing: Any | None = None) -> None:
        super().__init__(message, status=409)
        #: 已存在的那一行（`id` / `filename` / `created_at` / `page_count`）。
        #: **只有「先查」那条路有**；被唯一索引拦下时是 None。
        self.existing = existing


@dataclass(frozen=True, slots=True)
class StyleRow:
    user_id: str
    detail_level: str
    use_examples: str
    structure: str
    depth: str
    f1_arm: str
    onboarded_at: str | None

    @property
    def b_class(self) -> dict[str, str]:
        """进 prompt 的 B 类三条（口径 F45）。"""
        return {
            "use_examples": self.use_examples,
            "structure": self.structure,
            "depth": self.depth,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "detail_level": self.detail_level,
            "use_examples": self.use_examples,
            "structure": self.structure,
            "depth": self.depth,
            "f1_arm": self.f1_arm,
            "onboarded_at": self.onboarded_at,
        }


class SupabaseClient:
    """PostgREST 的最小客户端。`transport` 可注入，供断言喂一个假的。"""

    def __init__(self, rest_url: str, apikey: str, transport: Any | None = None) -> None:
        self._rest = rest_url.rstrip("/")
        self._apikey = apikey
        self._transport = transport

    def _get(self, path: str, token: str) -> Any:
        headers = {
            "apikey": self._apikey,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        url = f"{self._rest}{path}"
        if self._transport is not None:
            return self._transport(url, headers, None)

        import httpx

        try:
            resp = httpx.get(url, headers=headers, timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            raise SupabaseError(f"连不上数据库：{exc}", status=504) from exc
        if resp.status_code >= 400:
            # 原文照报 + 状态码。压成「读取失败」会把 401（token 不对）、
            # 403（RLS 拦了）、404（表不存在）说成同一句话，而处置完全不同。
            raise SupabaseError(
                f"数据库返回 {resp.status_code}：{resp.text[:300]}", status=502
            )
        return resp.json()

    def style_row(self, user_id: str, token: str) -> StyleRow:
        rows = self._get(f"/style_config?select={STYLE_COLUMNS}&user_id=eq.{user_id}", token)
        if not isinstance(rows, list):
            raise SupabaseError(f"预期数组，得到 {type(rows).__name__}")
        if len(rows) != 1:
            # **坏状态，不是空状态**（口径 F09）：每个合法用户注册时就有一行。
            raise SupabaseError(
                f"读到 {len(rows)} 行 style_config，应为 1 行。"
                "0 行说明注册触发器没跑到——那是坏状态，不是「还没配置」（口径 F09）",
                status=500,
            )
        r = rows[0]
        return StyleRow(
            user_id=r["user_id"],
            detail_level=r["detail_level"],
            use_examples=r["use_examples"],
            structure=r["structure"],
            depth=r["depth"],
            f1_arm=r["f1_arm"],
            onboarded_at=r.get("onboarded_at"),
        )

    # ---------------------------------------------------------------- 写入
    #
    # 全部带**用户自己的 JWT**，RLS 由数据库执行（与读取同一条路径）。

    def _post(self, path: str, token: str, rows: Any, *, prefer: str | None = None,
              duplicate_message: str | None = None) -> Any:
        """POST 一批行。

        `duplicate_message` 给了就表示**这个调用点上的 23505 有确定含义**，
        由调用点翻译成人话。⚠️ 见下面 `insert_document` 那条注释：
        这个参数的形状是被一个真 bug 逼出来的。
        """
        headers = {
            "apikey": self._apikey,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        url = f"{self._rest}{path}"
        if self._transport is not None:
            return self._transport(url, headers, rows)

        import httpx

        try:
            resp = httpx.post(url, headers=headers, json=rows, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            raise SupabaseError(f"连不上数据库：{exc}", status=504) from exc
        if resp.status_code >= 400:
            body = resp.text[:400]
            # 23505 = 唯一索引。翻译成人话，**不把数据库错误码丢给用户**。
            #
            # 判的是**错误码本身 + 调用点**，不去匹配错误正文里的列名或索引名：
            # 那两样是数据库写的，而我们没有权威的副本可比（见 insert_document）。
            if duplicate_message is not None and ("23505" in body or resp.status_code == 409):
                raise DuplicateDocument(duplicate_message)
            raise SupabaseError(f"数据库返回 {resp.status_code}：{body}", status=502)
        return resp.json() if resp.content else None

    def insert_document(self, *, token: str, row: dict[str, Any]) -> dict[str, Any]:
        """建一行 `documents`。

        ## ⚠️ 这里的重复翻译修过一次（2026-09-12，第一次真跑摄入抓出来的）

        原本的判断是 `if "23505" in body and "content_sha256" in body`，
        **在真库上从来不成立**：PostgREST 的正文写的是索引名

            {"code":"23505", ... "message":"duplicate key value violates unique
             constraint \"documents_owner_course_sha_idx\""}

        里面**没有 `content_sha256` 这个词**。于是重传同一份文件返回的是
        `502 数据库返回 409：{"code":"23505",...}` —— 正是那条注释说要避免的
        「把数据库错误码丢给用户」。

        它为什么没被单测抓到：单测喂的假错误正文是**我自己写的**，
        而我按列名写了它。**判据的两端同源**（F16 的同一形态）——
        假正文与匹配条件都出自同一个人的同一个猜测，它们必然相等。

        现在判的是**错误码 + 调用点**：`documents` 上只有一条唯一索引
        （`documents_owner_course_sha_idx`，见 `20260911100000_f2_corpus.sql`），
        所以这个调用点上的 23505 含义唯一，不需要去认正文里的任何字符串。
        **不认字符串，就没有「认错字符串」这种失败。**

        配套：`tests/test_ingest.py` 里那条断言喂的假正文改成了
        **真库上实际观测到的那一份**（2026-09-12 抄录），不是手写的。
        """
        out = self._post("/documents", token, row, prefer="return=representation",
                         duplicate_message="这门课里已经有同一份文件了")
        if not isinstance(out, list) or len(out) != 1:
            raise SupabaseError(f"建文档返回 {out!r}，应为 1 行")
        return out[0]

    def insert_slices(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._post("/slices", token, rows)

    def find_document_by_sha(self, *, token: str, course_id: str,
                             sha: str) -> dict[str, Any] | None:
        """这门课里有没有同一份文件。**「先查后提示」的那一查。**

        ⚠️ **它不替代唯一索引，是排在它前面的一层。**
        `documents_owner_course_sha_idx` 仍然是权威：查完到插入之间有一个窗口，
        同一份文件在两个标签页里同时上传就会撞上它，那时仍然是 23505 → 409。
        两层，与迁移里 grant + policy 的两层是同一个做法。

        为什么要这一查：唯一索引给的是一个**硬失败**，翻译过来只能说
        「这门课里已经有同一份文件了」——说不出**是哪一份、什么时候传的**。
        `20260911100000_f2_corpus.sql` 里那条登记的原话是「要『提示』得先查后提示」。
        """
        rows = self._get(
            f"/documents?select=id,filename,created_at,page_count"
            f"&course_id=eq.{course_id}&content_sha256=eq.{sha}", token)
        return rows[0] if isinstance(rows, list) and rows else None

    def insert_events(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        """写埋点事件。**带用户自己的 JWT**——`style_events` 的策略是
        `with check (user_id = auth.uid())`，服务端替用户写也得是用户的身份。

        不加 `Prefer: return=representation`：客户端与本服务对这张表
        **只有 insert 权限**，带 select 的返回会直接 42501。
        """
        if rows:
            self._post("/style_events", token, rows)

    def insert_embeddings(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._post("/slice_embeddings", token, rows)

    def create_course(self, *, token: str, owner: str, name: str) -> dict[str, Any]:
        out = self._post("/courses", token, {"owner": owner, "name": name},
                         prefer="return=representation")
        if not isinstance(out, list) or len(out) != 1:
            raise SupabaseError(f"建课返回 {out!r}，应为 1 行")
        return out[0]

    def courses(self, token: str) -> list[dict[str, Any]]:
        out = self._get("/courses?select=id,name,created_at&order=created_at.desc", token)
        return out if isinstance(out, list) else []

    def course(self, token: str, course_id: str) -> dict[str, Any] | None:
        """一门课。读不到返回 None —— **可能是不存在，也可能是别人的**，
        而这两件事在 RLS 下本来就该长得一样（否则课程 id 的存在性会泄露）。"""
        out = self._get(f"/courses?select=id,name&id=eq.{course_id}", token)
        return out[0] if isinstance(out, list) and len(out) == 1 else None

    # ---------------------------------------------------------------- 检索（阶段 5）

    #: `documents` 上检索要用到的列。**`unusable_pages` 在里面**：口径 X07 要求
    #: 「哪些页读不出来」一路带到检索层，少了它，模型面对一个内容在图里的提问
    #: 最可能的行为是基于标题编一段像样的讲解（PRD §6.2：更高危险——它看起来成功了）。
    DOCUMENT_COLUMNS = "id,filename,verdict,unusable_pages,page_count"

    def documents_in_course(self, token: str, course_id: str) -> list[dict[str, Any]]:
        """一门课的全部文档。**整门课，不是「与问题相关的那几份」**——
        `_render_manifest` 写的是「当前课程材料」，它列的就是这门课有什么。"""
        out = self._get(
            f"/documents?select={self.DOCUMENT_COLUMNS}&course_id=eq.{course_id}"
            "&order=created_at.asc", token)
        return out if isinstance(out, list) else []

    def match_slices(self, *, token: str, course_id: str, query: list[float],
                     model: str, ruleset: str, limit: int) -> list[dict[str, Any]]:
        """调 `match_slices` RPC。返回 `[{document_id, slice_id, similarity}]`，**已按相似度降序**。

        ⚠️ **`p_query` 直接传 JSON 数组。** 这不是推断：
        `supabase/verify_corpus_prod.py` 第 1 步对真库观测过，
        JSON 数组与 pgvector 文本 `'[...]'` 两种形状**都被接受**（2026-09-12）。

        作用域是**一门课**（口径 F47），且函数体里另有 `owner = auth.uid()` 过滤——
        与 RLS 两层，任一层被误改另一层还在。
        """
        out = self._post("/rpc/match_slices", token, {
            "p_course_id": course_id, "p_query": query,
            "p_model": model, "p_ruleset": ruleset, "p_limit": limit,
        })
        return out if isinstance(out, list) else []

    #: 讲解需要的切片列。`text` 是 `IndexedSlice` 刻意不带的那一样
    #: （它只需要排序所需字段），在这里补上。
    SLICE_COLUMNS = "document_id,slice_id,ordinal,page_indices,title,text,char_count"

    def slices_in_documents(self, token: str, document_ids: list[str]) -> list[dict[str, Any]]:
        """按 `document_id` 批量取切片。

        **一发请求取回全部命中文档的切片，再在内存里按 `(document_id, slice_id)`
        配对**，而不是每个命中发一次请求：命中是 5 个，五次串行往返会直接加到
        用户等待的延迟上，而这一步在生成之前。

        取回的比需要的多（一份文档的全部切片），代价是几十 KB 文本；
        换掉的是 N 次往返。这门课最大的一份实测 39 片。
        """
        if not document_ids:
            return []
        joined = ",".join(sorted(set(document_ids)))
        out = self._get(
            f"/slices?select={self.SLICE_COLUMNS}&document_id=in.({joined})", token)
        return out if isinstance(out, list) else []
