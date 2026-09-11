"""假的 PostgREST。**记下它收到的每个请求**，供「token 真的被转发了」那条断言。"""

from __future__ import annotations

from typing import Any


class FakeRest:
    def __init__(self, rows_by_user: dict[str, list[dict[str, Any]]]) -> None:
        self.rows_by_user = rows_by_user
        self.seen: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Any:
        self.seen.append((url, dict(headers)))
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
