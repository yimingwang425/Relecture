"""假的 PostgREST。**记下它收到的每个请求**，供「token 真的被转发了」那条断言。"""

from __future__ import annotations

from typing import Any


class FakeRest:
    def __init__(self, rows_by_user: dict[str, list[dict[str, Any]]],
                 *, conversations: dict[str, dict[str, Any]] | None = None,
                 messages: list[dict[str, Any]] | None = None,
                 fail_message_insert: bool = False,
                 memories: list[dict[str, Any]] | None = None,
                 memories_table_missing: bool = False,
                 checks_table_missing: bool = False) -> None:
        self.rows_by_user = rows_by_user
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.writes: list[tuple[str, Any]] = []
        #: F5。**必须显式给**——默认空字典的意思是「这个账号一个会话都没有」，
        #: 而不是「随便什么 id 都能读到一个会话」。后者会让 404 那条断言永远绿不了。
        self.conversations = conversations or {}
        self.messages = list(messages or [])
        self.fail_message_insert = fail_message_insert
        #: F116–F118 · `conversation_memories`。`memories_table_missing` 模拟「构建先上、迁移没跑」：
        #: 真 PostgREST 对不存在的表回 404（PGRST205），读写都抛。
        self.memories = list(memories or [])
        self.memories_table_missing = memories_table_missing
        #: F121–F123 · `answer_checks`。同上：`checks_table_missing` 模拟迁移没跑。
        self.checks: list[dict[str, Any]] = []
        self.checks_table_missing = checks_table_missing

    def __call__(self, url: str, headers: dict[str, str], body: Any = None) -> Any:
        self.seen.append((url, dict(headers)))
        if body is not None:          # 写入
            self.writes.append((url, body))
            # **照真 PostgREST 的两条规矩拒收**（2026-10-08，生产上 messages 一直是 0 行才发现）：
            #  · 请求体要能序列化成 JSON——真客户端（httpx）在这一步就会抛；
            #  · 批量插入时**每个对象的键必须一模一样**，否则 400 PGRST102
            #    「All object keys must match」，整批一行都不写。
            # 这个假库原来什么都收，于是「问那一行少了 round_id / meta」在测试里一直是绿的。
            import json as _json
            from api.supabase import SupabaseError
            _json.dumps(body)
            if isinstance(body, list) and len({tuple(sorted(r)) for r in body}) > 1:
                raise SupabaseError("PGRST102 All object keys must match", status=400)
            if "/conversation_memories" in url:
                if self.memories_table_missing:
                    raise SupabaseError("数据库返回 404：PGRST205 relation conversation_memories 不存在", status=502)
                for r in (body if isinstance(body, list) else [body]):
                    # 照真库的部分唯一索引拒收：同一会话同一个 through_turn 只有一份 written。
                    if r.get("status") == "written" and any(
                            m.get("status") == "written" and m["conversation_id"] == r["conversation_id"]
                            and m["through_turn"] == r["through_turn"] for m in self.memories):
                        raise SupabaseError("数据库返回 409：23505 duplicate key", status=502)
                    self.memories.append(dict(r))
                return None
            if "/answer_checks" in url:
                if self.checks_table_missing:
                    raise SupabaseError("数据库返回 404：PGRST205 relation answer_checks 不存在", status=502)
                for r in (body if isinstance(body, list) else [body]):
                    # 照真库的触发器与唯一约束拒收：轮必须是这个会话里一条讲解的那一轮；一轮一行。
                    if not any(m.get("conversation_id") == r["conversation_id"] and m.get("round_id") == r["round_id"]
                               and m.get("role") == "assistant" for m in self.messages):
                        raise SupabaseError("数据库返回 409：23503 会话里没有这一轮的讲解", status=502)
                    if any(c["round_id"] == r["round_id"] for c in self.checks):
                        raise SupabaseError("数据库返回 409：23505 duplicate key", status=502)
                    self.checks.append(dict(r))
                return None
            if "/messages" in url:
                if self.fail_message_insert:
                    from api.supabase import SupabaseError
                    raise SupabaseError("写 messages 失败（造出来的）")
                self.messages.extend(body if isinstance(body, list) else [body])
                return None
            if "/documents" in url:
                import uuid as _uuid
                return [{"id": str(_uuid.uuid4()), **(body if isinstance(body, dict) else {})}]
            return None
        # 模拟 RLS：**看得见谁由 Authorization 里那个 token 的 sub 决定**，
        # 不由 URL 上的 `user_id=eq.` 决定。「服务端自己过滤 user_id」这种写法
        # 在这里会露馅——它会在 token 换人之后仍然返回同一行。
        #
        # 解 sub 不验签：这里扮演的是数据库，不是验签器；验签由 api.auth 负责，
        # 它有自己那组断言（用另一把私钥签的 token 必须被拒）。
        import jwt as _jwt

        token = headers.get("Authorization", "").removeprefix("Bearer ").strip()
        try:
            sub = _jwt.decode(token, options={"verify_signature": False})["sub"]
        except Exception:  # noqa: BLE001
            return []

        # ---- F5 的两张表。**先于兜底那一行判**：兜底返回的是风格行，
        # 而 `conversation()` 只看「是不是恰好 1 行」——不拦的话任何一个 id
        # 都会被读成一个存在的会话，404 那条断言就永远绿不了。
        if "/conversation_memories" in url:
            from api.supabase import SupabaseError
            if self.memories_table_missing:
                raise SupabaseError("数据库返回 404：PGRST205 relation conversation_memories 不存在", status=502)
            cid = _eq(url, "conversation_id")
            rows = [m for m in self.memories if m.get("conversation_id") == cid
                    and m.get("owner") == sub and m.get("status") == "written"]
            rows.sort(key=lambda m: m["through_turn"], reverse=True)
            return rows[:1]
        if "/conversations" in url:
            cid = _eq(url, "id")
            conv = self.conversations.get(cid or "")
            return [conv] if conv is not None else []
        if "/messages" in url:
            cid = _eq(url, "conversation_id")
            rows = [m for m in self.messages if m.get("conversation_id") == cid]
            # turn_index 的范围过滤（前情笔记之后的原文、压缩 agent 的材料）。照 PostgREST 的
            # 写法逐个解析：同一列可以出现多次，彼此是「且」。
            import re as _re
            for op, val in _re.findall(r"turn_index=(gt|gte|lte|lt)\.(\d+)", url):
                v = int(val)
                cmp = {"gt": lambda t: t > v, "gte": lambda t: t >= v,
                       "lt": lambda t: t < v, "lte": lambda t: t <= v}[op]
                rows = [m for m in rows if cmp(int(m["turn_index"]))]
            if "order=created_at.asc" in url:
                return [dict(m) for m in rows]
            if "order=turn_index.desc" in url:
                if not rows:
                    return []
                return [{"turn_index": max(int(m["turn_index"]) for m in rows)}]
            # 历史：服务端按 created_at.desc 取最近 N 条再自己翻回正序。
            limit = int(_eq(url, "limit") or len(rows) or 1)
            out = list(reversed(rows))[:limit]
            # PostgREST 的 JSON 路径别名 `web_sources:meta->web_sources`：照真库的形状给出这一列，
            # 没有这个键时是 null（不是缺键）。
            if "web_sources:meta-%3Eweb_sources" in url or "web_sources:meta->web_sources" in url:
                out = [{**m, "web_sources": (m.get("meta") or {}).get("web_sources")} for m in out]
            if "style_reminder:meta-%3Estyle_reminder" in url or "style_reminder:meta->style_reminder" in url:
                out = [{**m, "style_reminder": (m.get("meta") or {}).get("style_reminder")} for m in out]
            return out

        return list(self.rows_by_user.get(sub, []))


def row(user_id: str, **over: Any) -> dict[str, Any]:
    base = {
        "user_id": user_id,
        "detail_level": "P0",
        "use_examples": "more",
        "structure": "conclusion_first",
        "depth": "why",
        "f1_arm": "pairwise",
        "onboarded_at": "2026-09-11T00:00:00Z",
    }
    base.update(over)
    return base


def _eq(url: str, field: str) -> str | None:
    """从 PostgREST 风格的查询串里取 `field=eq.<值>`（`limit=` 不带 eq.）。"""
    import re

    m = re.search(rf"[?&]{field}=(?:eq\.)?([^&]+)", url)
    return m.group(1) if m else None
