"""按路径分发的假 PostgREST。`fake_db.FakeRest` 只会答 `style_config` 一张表，
F4 的断言要走完 **课程 → 检索 → 讲解 → 标记 → 再讲解** 这一整条，所以有这一份。

## 它模拟哪些东西，以及**它证明不了什么**

模拟：
  · **RLS**——看得见谁由 `Authorization` 里那个 token 的 `sub` 决定，
    不由 URL 上的过滤条件决定（同 `fake_db.FakeRest` 的做法：
    「服务端自己过滤 user_id」这种写法在这里会露馅）。
  · `confusion_marks` 的 **`unique (user_id, round_id)`**——撞了抛 `DuplicateMark`，
    与 `_post` 把真库的 23505 翻译成的东西是同一个类。
  · `slice_ids && $1` 的**集合语义**。

⚠️ **它不是数据库，下面这些在这里全绿不算数**（口径 F34：两种证据不互相顶替）：
  · RLS 策略真的存在、真的挡得住 —— `supabase/verify_rls_local.mjs`（PGlite 跑
    迁移原文 + 反向控制）与 `api/scripts/verify_f4_marks.py`（真库两个账号）；
  · `&&` 与 PostgREST 的 `ov.` 是不是同一件事 —— **只有真库能答**
    （这里的交集是我用 Python 写的，它按我以为的语义实现）。
    `verify_f4_marks.py` 用「有交集」与「无交集」两组键各查一次；
  · 唯一约束、cascade、CHECK —— 同上，`verify_rls_local.mjs` 跑的是迁移原文。

这份文件的作用是让**服务端那一侧的接线**（键拼得对不对、注入触发条件对不对、
幂等返回 200 而不是 409、事件真的多了一行）能在本地被钉住。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from api.supabase import DuplicateMark


def parse_pg_array(literal: str) -> list[str]:
    """`{"a:b","c:d"}` → `['a:b', 'c:d']`。**`_pg_array_literal` 的逆。**

    不复用生产那一半：正反两半出自同一处的话，「拼错了」在这里也会被同样地
    读回来，于是断言恒真（F16 那条「判据两端同源」）。
    """
    s = literal.strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise AssertionError(f"不是 Postgres 数组字面量：{literal!r}")
    inner = s[1:-1]
    if not inner:
        return []
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|([^,]+)', inner):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        out.append(raw.replace('\\"', '"').replace("\\\\", "\\"))
    return out


class RoutedRest:
    """一个能答 6 张表的假 PostgREST。

    `documents` / `slices` / `matches` 按 `course_id` 分组给，
    这样「两门不同的课」在断言里是真的两门课，而不是同一份数据换个 id。
    """

    def __init__(
        self,
        *,
        style_rows: dict[str, list[dict[str, Any]]],
        courses: dict[str, dict[str, Any]] | None = None,
        documents: dict[str, list[dict[str, Any]]] | None = None,
        slices: dict[str, list[dict[str, Any]]] | None = None,
        matches: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.style_rows = style_rows
        #: `course_id -> {"id", "name", "owner"}`
        self.courses = courses or {}
        #: `course_id -> [document, ...]`
        self.documents = documents or {}
        #: `document_id -> [slice, ...]`
        self.slices = slices or {}
        #: `course_id -> [{document_id, slice_id, similarity}, ...]`
        self.matches = matches or {}

        #: 「表」。断言直接读它们 —— 埋点那条守卫要的是**行数增加**，
        #: 不是「函数被调用」（后者在写库失败时照样绿）。
        self.marks: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.seen: list[tuple[str, dict[str, str]]] = []
        #: 每一次 marks 交集查询的 URL 原文。给「查询形状」那条断言用。
        self.overlap_urls: list[str] = []

    # ------------------------------------------------------------------ 身份
    @staticmethod
    def _sub(headers: dict[str, str]) -> str | None:
        """解 token 的 sub，**不验签**——这里扮演的是数据库，不是验签器。"""
        import jwt as _jwt

        token = headers.get("Authorization", "").removeprefix("Bearer ").strip()
        try:
            return _jwt.decode(token, options={"verify_signature": False})["sub"]
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ 分发
    def __call__(self, url: str, headers: dict[str, str], body: Any = None) -> Any:
        self.seen.append((url, dict(headers)))
        sub = self._sub(headers)
        parsed = urlparse(url)
        path = parsed.path.split("/rest/v1", 1)[-1] or parsed.path
        q = parse_qs(parsed.query, keep_blank_values=True)

        if body is not None:
            return self._write(path, sub, body)
        return self._read(path, sub, q, parsed.query)

    # ------------------------------------------------------------------ 写
    def _write(self, path: str, sub: str | None, body: Any) -> Any:
        if path.startswith("/style_events"):
            rows = body if isinstance(body, list) else [body]
            self.events.extend(rows)
            return None

        if path.startswith("/confusion_marks"):
            row = dict(body if isinstance(body, dict) else body[0])
            # `unique (user_id, round_id)`。真库上是 23505 → `_post` 翻成
            # DuplicateMark；这里直接抛同一个类（见文件头：它不替代真库那条）。
            if any(m["user_id"] == row["user_id"] and m["round_id"] == row["round_id"]
                   for m in self.marks):
                raise DuplicateMark()
            self.marks.append(row)
            return None

        if path.startswith("/rpc/match_slices"):
            cid = body.get("p_course_id")
            return list(self.matches.get(cid, []))[: body.get("p_limit", 5)]

        raise AssertionError(f"假 PostgREST 不认识这个写入路径：{path}")

    # ------------------------------------------------------------------ 读
    def _read(self, path: str, sub: str | None, q: dict[str, list[str]],
              raw_query: str) -> Any:
        if path.startswith("/style_config"):
            return list(self.style_rows.get(sub or "", []))

        if path.startswith("/courses"):
            eq = self._eq(q, "id")
            rows = [c for c in self.courses.values() if c.get("owner") == sub]
            if eq:
                rows = [c for c in rows if c["id"] == eq]
            return [{"id": c["id"], "name": c["name"]} for c in rows]

        if path.startswith("/documents"):
            cid = self._eq(q, "course_id")
            return list(self.documents.get(cid, []))

        if path.startswith("/slices"):
            ids = self._in(q, "document_id")
            out: list[dict[str, Any]] = []
            for d in ids:
                out.extend(self.slices.get(d, []))
            return out

        if path.startswith("/confusion_marks"):
            mine = [m for m in self.marks if m["user_id"] == sub]
            ov = q.get("slice_ids", [None])[0]
            if ov is None:
                return [{"id": i} for i, _ in enumerate(mine)]
            self.overlap_urls.append(raw_query)
            assert ov.startswith("ov."), f"交集查询的操作符不是 ov.：{ov!r}"
            want = set(parse_pg_array(unquote(ov[len("ov."):])))
            return [m for m in mine if want & set(m.get("slice_ids") or [])]

        raise AssertionError(f"假 PostgREST 不认识这个读取路径：{path}")

    @staticmethod
    def _eq(q: dict[str, list[str]], name: str) -> str | None:
        v = q.get(name, [None])[0]
        return v[len("eq."):] if v and v.startswith("eq.") else None

    @staticmethod
    def _in(q: dict[str, list[str]], name: str) -> list[str]:
        v = q.get(name, [None])[0]
        if not v or not v.startswith("in."):
            return []
        return [x for x in v[len("in."):].strip("()").split(",") if x]
