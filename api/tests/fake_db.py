"""假的 PostgREST。**记下它收到的每个请求**，供「token 真的被转发了」那条断言。"""

from __future__ import annotations

from typing import Any


class FakeRest:
    def __init__(self, rows_by_user: dict[str, list[dict[str, Any]]],
                 *, conversations: dict[str, dict[str, Any]] | None = None,
                 messages: list[dict[str, Any]] | None = None,
                 fail_message_insert: bool = False) -> None:
        self.rows_by_user = rows_by_user
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.writes: list[tuple[str, Any]] = []
        #: F5。**必须显式给**——默认空字典的意思是「这个账号一个会话都没有」，
        #: 而不是「随便什么 id 都能读到一个会话」。后者会让 404 那条断言永远绿不了。
        self.conversations = conversations or {}
        self.messages = list(messages or [])
        self.fail_message_insert = fail_message_insert

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
        if "/conversations" in url:
            cid = _eq(url, "id")
            conv = self.conversations.get(cid or "")
            return [conv] if conv is not None else []
        if "/messages" in url:
            cid = _eq(url, "conversation_id")
            rows = [m for m in self.messages if m.get("conversation_id") == cid]
            if "order=turn_index.desc" in url:
                if not rows:
                    return []
                return [{"turn_index": max(int(m["turn_index"]) for m in rows)}]
            # 历史：服务端按 created_at.desc 取最近 N 条再自己翻回正序。
            limit = int(_eq(url, "limit") or len(rows) or 1)
            return list(reversed(rows))[:limit]

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
