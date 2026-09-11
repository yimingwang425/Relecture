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

__all__ = ["StyleRow", "SupabaseError", "SupabaseClient"]

STYLE_COLUMNS = "user_id,detail_level,use_examples,structure,depth,f1_arm,onboarded_at"


class SupabaseError(Exception):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


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
            return self._transport(url, headers)

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
