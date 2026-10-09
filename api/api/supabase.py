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

__all__ = ["StyleRow", "SupabaseError", "DuplicateDocument", "DuplicateMark",
           "SupabaseClient"]

STYLE_COLUMNS = "user_id,detail_level,use_examples,structure,depth,f1_arm,onboarded_at,tone"


def _pg_array_literal(values: list[str]) -> str:
    """`['a:b', 'c:d']` → `{"a:b","c:d"}`（PostgREST 查询参数里的数组字面量）。

    **每一项都加双引号**，即使当前的键里不含逗号或花括号：
    「现在的取值恰好安全」不是一条能长期成立的性质，而它失效时的表现是
    一个被切成两半的键——那个键与任何东西都不相交，于是注入静默不触发。
    """
    out = []
    for v in values:
        s = str(v).replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'"{s}"')
    return "{" + ",".join(out) + "}"


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


class DuplicateMark(SupabaseError):
    """同一用户、同一 `round_id`（`confusion_marks_user_round_uniq`）。

    ⚠️ **它不是一个错误，是幂等**。同一轮点两次是完全正常的用户行为
    （点了没反应就再点一次），路由把它变成 **200**，不是 409——
    前端不需要区分首次与重复，而**要它区分等于要它维护一个它不关心的分支**。

    ## 为什么单独一个类，而不复用 `DuplicateDocument`

    同名同义的两个类比两个不同名的类更危险，这条教训是 2026-09-12 的
    真 bug（见上面那个类的 docstring）。这里的反面同样要守：
    **一个名叫 `DuplicateDocument` 的异常从 marks 那条路抛出来**，
    读代码的人会去查上传逻辑。名字必须说得出它谈的是什么。
    """

    def __init__(self, message: str = "同一 round_id 已经标记过") -> None:
        super().__init__(message, status=409)


@dataclass(frozen=True, slots=True)
class StyleRow:
    user_id: str
    detail_level: str
    use_examples: str
    structure: str
    depth: str
    f1_arm: str
    onboarded_at: str | None
    #: 语气档（2026-09-19）。**默认 neutral**：老行没有这一列时也要能工作，
    #: 而 neutral 渲染成空串 —— 与样例库的生成条件逐字节相同（F18/F19）。
    tone: str = "neutral"

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
            "tone": self.tone,
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
            # `.get` 而不是 `[...]`：迁移上线到新构建上线之间那段窗口里，
            # 旧库还没有这一列。缺了回落 neutral —— 而 neutral 渲染成空串，
            # 等于「与 2026-09-19 之前一模一样」，不是一个编出来的语气。
            tone=r.get("tone") or "neutral",
        )

    # ---------------------------------------------------------------- 写入
    #
    # 全部带**用户自己的 JWT**，RLS 由数据库执行（与读取同一条路径）。

    def _post(self, path: str, token: str, rows: Any, *, prefer: str | None = None,
              duplicate_message: str | None = None,
              duplicate_exc: type[SupabaseError] = DuplicateDocument) -> Any:
        """POST 一批行。

        `duplicate_message` 给了就表示**这个调用点上的 23505 有确定含义**，
        由调用点翻译成人话。⚠️ 见下面 `insert_document` 那条注释：
        这个参数的形状是被一个真 bug 逼出来的。

        `duplicate_exc` 是抛哪个类。默认 `DuplicateDocument`（既有调用点不变），
        marks 那条路传 `DuplicateMark`——**异常的名字必须说得出它谈的是什么**，
        见那个类的 docstring。
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
                raise duplicate_exc(duplicate_message)
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

    def insert_lexicon(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        """写词法索引（`slice_lexicon`，口径 F97）。与向量同一个形状：一片一行、主键带口径。"""
        if rows:
            self._post("/slice_lexicon", token, rows)

    def _delete(self, path: str, token: str) -> None:
        """DELETE 一批行。注入的 transport 收到 `x-method: DELETE`（同 `update_document`）。"""
        headers = {"apikey": self._apikey, "Authorization": f"Bearer {token}"}
        url = f"{self._rest}{path}"
        if self._transport is not None:
            self._transport(url, {**headers, "x-method": "DELETE"}, {})
            return

        import httpx

        try:
            resp = httpx.delete(url, headers=headers, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            raise SupabaseError(f"连不上数据库：{exc}", status=504) from exc
        if resp.status_code >= 400:
            raise SupabaseError(f"数据库返回 {resp.status_code}：{resp.text[:300]}", status=502)

    def delete_index_rows(self, *, token: str, document_id: str, ruleset: str) -> None:
        """删掉一份文档在**某一个口径**下的向量与词条。重建索引的幂等前提（`reindex.py`）。

        只删这一个口径：老口径的行原样留着，切换完成之前检索仍在用它们。
        """
        from urllib.parse import quote

        rs = quote(ruleset, safe="")
        self._delete(f"/slice_embeddings?document_id=eq.{document_id}&embed_ruleset=eq.{rs}", token)
        self._delete(f"/slice_lexicon?document_id=eq.{document_id}&ruleset=eq.{rs}", token)

    def slices_for_reindex(self, token: str, document_id: str) -> list[dict[str, Any]]:
        """重建索引要的切片列：比讲解多一个 `units`（T0 组文本的输入）。"""
        out = self._get(
            f"/slices?select=document_id,slice_id,page_indices,title,text,units"
            f"&document_id=eq.{document_id}&order=ordinal.asc", token)
        return out if isinstance(out, list) else []

    def update_document(self, *, token: str, document_id: str, fields: dict[str, Any]) -> None:
        """PATCH 一行 `documents`。**只给重建索引用**（切换 `index_ruleset`）。

        注入的 `transport` 收到的 headers 多一个 `x-method: PATCH`——假 PostgREST
        靠它把这一发与 POST 分开（真请求走 httpx.patch，不带这个头）。
        """
        headers = {
            "apikey": self._apikey,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{self._rest}/documents?id=eq.{document_id}"
        if self._transport is not None:
            self._transport(url, {**headers, "x-method": "PATCH"}, fields)
            return

        import httpx

        try:
            resp = httpx.patch(url, headers=headers, json=fields, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            raise SupabaseError(f"连不上数据库：{exc}", status=504) from exc
        if resp.status_code >= 400:
            raise SupabaseError(f"数据库返回 {resp.status_code}：{resp.text[:300]}", status=502)

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

    # ---------------------------------------------------------------- F5 · 会话与消息
    #
    # 三个方法都带**用户自己的 JWT**，RLS 由数据库执行（同 F4 那三个）。
    #
    # ⚠️ `messages` 这张表**服务端只写不改**（迁移里没授 update）。
    # 这里也就只有读与插两个方法——没有 `update_message`，
    # 不是忘了写：改一条已经说出口的回答没有合法场景。

    #: 重建历史要用到的列。**不读 `meta`**：那里面是出处片段与兜底判断结果，
    #: 给模型看没有用（本轮会重新检索），而它会把请求体撑大好几倍。
    MESSAGE_COLUMNS = "role,content,turn_index"

    def conversation(self, token: str, conversation_id: str) -> dict[str, Any] | None:
        """一个会话。读不到返回 None —— 可能不存在，也可能是别人的（同 `course()`）。"""
        out = self._get(
            f"/conversations?select=id,course_id,title&id=eq.{conversation_id}", token)
        return out[0] if isinstance(out, list) and len(out) == 1 else None

    def conversation_history(self, token: str, conversation_id: str,
                             limit: int = 40, after_turn: int | None = None) -> list[dict[str, Any]]:
        """这个会话已有的消息，**按时间正序**。

        `limit` 取的是**最近**的那些（PostgREST 只能先倒序取再翻回来）。
        取不完整的历史是刻意的：上下文窗口与钱都是有限的，而越早的轮次越可能
        已经被后面的轮次覆盖掉。**截断在这里发生，不在 prompt 拼装处**——
        放在那边的话，「这一轮模型看到了几轮历史」就散在两个地方决定。

        `after_turn`（2026-10-09，口径 F117）：前情笔记已经覆盖到第几轮，只取之后的原文。
        有笔记时原文通常只有 4–10 轮，`limit` 退回成一道保险。
        """
        after = f"&turn_index=gt.{int(after_turn)}" if after_turn is not None else ""
        out = self._get(
            f"/messages?select={self.MESSAGE_COLUMNS}"
            f"&conversation_id=eq.{conversation_id}{after}"
            f"&order=created_at.desc&limit={limit}", token)
        rows = out if isinstance(out, list) else []
        return list(reversed(rows))

    def next_turn_index(self, token: str, conversation_id: str) -> int:
        """这个会话的下一轮是第几轮。

        ⚠️ **由服务端从库里算，不采信调用方给的那个数**（口径 F13 的同一条）。
        前端也有一个 `turn_index`（`lib/explain/turns.ts`），它按「用户看到第几次
        回答」算；两边各算一份的话，刷新页面之后前端会从 1 重新数，
        而「平均会话轮次」是北极星指标（PRD §3.1）——那个数会静静地变小。
        """
        out = self._get(
            f"/messages?select=turn_index&conversation_id=eq.{conversation_id}"
            f"&order=turn_index.desc&limit=1", token)
        if isinstance(out, list) and out:
            return int(out[0]["turn_index"]) + 1
        return 1

    # ---------------------------------------------------------------- F5 · 前情笔记（口径 F116–F118）

    #: 压缩 agent 要的列：原文 + 那一轮的信号（讲法档位、有没有注入「没看懂」）。
    #: meta 整列读——只在触发整理时读、只读候选范围那十来行。
    TURN_COLUMNS = "role,content,turn_index,meta"

    def latest_memory(self, token: str, conversation_id: str) -> dict[str, Any] | None:
        """这个会话**最新**的一份笔记（`through_turn` 最大的 written 行），没有就 None。

        ⚠️ 迁移没跑时这张表不存在，这里会抛 `SupabaseError`——**调用方接住、当作没有笔记**，
        并把错误放进响应（口径 F42）。不在这里吞：吞了的话「迁移没跑」与「还没整理过」
        长得一模一样。
        """
        out = self._get(
            "/conversation_memories?select=through_turn,memory,created_at"
            f"&conversation_id=eq.{conversation_id}&status=eq.written"
            "&order=through_turn.desc&limit=1", token)
        return out[0] if isinstance(out, list) and out else None

    def conversation_turns(self, token: str, conversation_id: str,
                           lo: int, hi: int) -> list[dict[str, Any]]:
        """第 lo–hi 轮的消息（含 meta），按时间正序。压缩 agent 的材料。"""
        out = self._get(
            f"/messages?select={self.TURN_COLUMNS}"
            f"&conversation_id=eq.{conversation_id}"
            f"&turn_index=gte.{int(lo)}&turn_index=lte.{int(hi)}"
            "&order=created_at.asc", token)
        return out if isinstance(out, list) else []

    def insert_memory(self, *, token: str, row: dict[str, Any]) -> None:
        """写一行笔记（written 或 failed）。只追加，表不授 update。"""
        self._post("/conversation_memories", token, [row])

    def insert_messages(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        """写一问一答。**不加 `Prefer: return=representation`**——
        这张表授了 select，所以带回读其实不会 42501，但回读的内容我们一个字都不用，
        白白把答案正文再传一遍。
        """
        if rows:
            self._post("/messages", token, rows)

    # ---------------------------------------------------------------- F4 · confusion_marks
    #
    # 三个方法都带**用户自己的 JWT**，RLS 由数据库执行。
    #
    # ⚠️ **刻意不在 URL 上加 `user_id=eq.<自己>`。** 加了之后「策略被误删」
    # 这件事看起来仍然一切正常——服务端自己过滤掉了别人的行，而 RLS 已经不在。
    # 边界只有一处，就是数据库里那两条策略；跨用户隔离由
    # `verify_f4_marks.py` 在真库上用两个账号验（本地那条在 verify_rls_local.mjs）。
    #
    # 对照：`style_row()` 确实带了 `user_id=eq.`，那是因为它要判「恰好 1 行」
    # （口径 F09），不是为了过滤。

    #: 注入判定与计数要用到的列。`question` **不读**（口径 F59：v1 不注入它）——
    #: 不读就不会有人「顺手」把它拼进 prompt。
    MARK_COLUMNS = "id,round_id,slice_ids,created_at"

    def marks_overlapping(self, token: str, slice_keys: list[str]) -> list[dict[str, Any]]:
        """本人 marks 里与本轮切片**有交集**的行。一次 `&&`，零模型调用。

        PostgREST 的 `ov.` 就是 `&&`，走 `confusion_marks_slice_ids_gin`。

        ⚠️ **本地断言证明不了这个操作符名是对的**（假 transport 是我写的，
        它按我以为的语义实现）。写错的表现是永远 0 行 = 功能像没生效，
        而没有任何东西会红。真正的判据在 `api/scripts/verify_f4_marks.py`：
        它在真库上插一条 mark，用**有交集**与**无交集**两组键各查一次，
        要求前者 1 行、后者 0 行——两条一起才排除「恒空」与「恒满」。
        """
        if not slice_keys:
            # 空集与任何集合的交集都是空。**不发这个请求**：`ov.{}` 在
            # PostgREST 上的行为不是我们观测过的东西，而答案是确定的。
            return []
        from urllib.parse import quote

        lit = _pg_array_literal(slice_keys)
        out = self._get(
            f"/confusion_marks?select={self.MARK_COLUMNS}"
            f"&slice_ids=ov.{quote(lit, safe='')}", token)
        return out if isinstance(out, list) else []

    def insert_mark(self, *, token: str, row: dict[str, Any]) -> bool:
        """写一条 mark。返回**这次是不是真的新插了一行**。

        同一 `round_id` 第二次提交撞唯一索引 → 23505 → `DuplicateMark`，
        由路由翻成 200（幂等，不是错误）。判的是**错误码 + 调用点**，
        不去认正文里的列名或索引名——`confusion_marks` 上只有这一条唯一约束，
        所以这个调用点上的 23505 含义唯一（同 `insert_document` 那条教训：
        不认字符串，就没有「认错字符串」这种失败）。
        """
        try:
            self._post("/confusion_marks", token, row,
                       duplicate_message="同一 round_id 已经标记过（幂等，不是错误）",
                       duplicate_exc=DuplicateMark)
        except DuplicateMark:
            return False
        return True

    def count_marks(self, token: str) -> int:
        """这个用户至此累计多少条 mark（`mark_count_after` 的来源）。

        ⚠️ 读的是**行本身**再数长度，不是 `Prefer: count=exact`：
        那个数在响应头里，而 `_get` 只把 body 交出来。改 `_get` 的返回形状
        会波及每一个既有调用点，为一个 v1 里最多几十行的计数不值。
        **触发条件**：内测里有账号的 marks 上到几百条时，那时改成 count 头。
        """
        out = self._get("/confusion_marks?select=id", token)
        return len(out) if isinstance(out, list) else 0

    # ---------------------------------------------------------------- 检索（阶段 5）

    #: `documents` 上检索要用到的列。**`unusable_pages` 在里面**：口径 X07 要求
    #: 「哪些页读不出来」一路带到检索层，少了它，模型面对一个内容在图里的提问
    #: 最可能的行为是基于标题编一段像样的讲解（PRD §6.2：更高危险——它看起来成功了）。
    #:
    #: 2026-10-07 加 `index_ruleset`（检索按每份文档自己的口径取行，口径 F97）与
    #: `vision_pages`（转写救回的页不再算「读不了的页」，口径 F100）。
    #: ⚠️ 迁移 `20261007100000` 没跑的库上，这个 select 会 400 —— 迁移必须先于构建。
    DOCUMENT_COLUMNS = "id,filename,verdict,unusable_pages,page_count,index_ruleset,vision_pages"

    def documents_in_course(self, token: str, course_id: str) -> list[dict[str, Any]]:
        """一门课的全部文档。**整门课，不是「与问题相关的那几份」**——
        `_render_manifest` 写的是「当前课程材料」，它列的就是这门课有什么。"""
        out = self._get(
            f"/documents?select={self.DOCUMENT_COLUMNS}&course_id=eq.{course_id}"
            "&order=created_at.asc", token)
        return out if isinstance(out, list) else []

    def match_slices_indexed(self, *, token: str, course_id: str, query: list[float],
                             model: str, limit: int) -> list[dict[str, Any]]:
        """混合检索的向量一路（口径 F97）。与 `match_slices` 相同，但**按每份文档自己的
        `index_ruleset` 取向量**，所以不收 ruleset 参数——见迁移 `20261007100100`。

        ⚠️ **`p_query` 直接传 JSON 数组。** 这不是推断：
        `supabase/verify_corpus_prod.py` 第 1 步对真库观测过（对 `match_slices`，
        同一个 `vector(1024)` 参数类型），JSON 数组与 pgvector 文本 `'[...]'`
        两种形状**都被接受**（2026-09-12）。

        作用域是**一门课**（口径 F47），且函数体里另有 `owner = auth.uid()` 过滤——
        与 RLS 两层，任一层被误改另一层还在。

        （2026-10-07 删掉了客户端的 `match_slices` 方法：检索改走本方法后它零调用方。
        数据库里的 `match_slices` 函数**原样保留**——真库验收脚本直接调它。）"""
        out = self._post("/rpc/match_slices_indexed", token, {
            "p_course_id": course_id, "p_query": query, "p_model": model, "p_limit": limit,
        })
        return out if isinstance(out, list) else []

    def match_slices_lexical(self, *, token: str, course_id: str, query: str,
                             limit: int) -> list[dict[str, Any]]:
        """混合检索的词法一路（口径 F97）。`query` 是 `retrieval.lexical.tsquery()` 的产出。
        返回 `[{document_id, slice_id, rank}]`，已按 `ts_rank_cd` 降序。"""
        out = self._post("/rpc/match_slices_lexical", token, {
            "p_course_id": course_id, "p_query": query, "p_limit": limit,
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
