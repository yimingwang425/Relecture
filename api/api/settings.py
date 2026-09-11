"""服务配置。**缺一个就起不来，不给默认值。**

给默认值的那种配置错误是静默的：服务照常启动、请求照常返回，
只是连的是另一个项目、或者压根没验签。宁可起不来（同 `web/lib/env.ts` 的做法）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["Settings", "load_settings"]


@dataclass(frozen=True, slots=True)
class Settings:
    supabase_url: str
    #: **publishable key**，与前端用的是同一个。
    #:
    #: ⚠️ 本服务**不持有 service_role key**（口径 F40）。它绕过全部 RLS——
    #: 服务端拿着它就等于「以为有 RLS，其实没有」，而那不会报错。
    #: 所有读写都带着**用户自己的 JWT** 打给 PostgREST，由数据库执行 RLS，
    #: 与前端直连 Supabase 时完全同一条路径（F1 阶段 1.5 的论证在这里继续成立：
    #: 多一跳应用层不增加保障，除非它真的做了别的事——这里做的是跑 Python）。
    supabase_publishable_key: str
    #: 模型 key。缺它时 `/explain` 报 503 并点名是服务端未配置，不静默失败。
    anthropic_api_key: str | None

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    e = dict(os.environ if env is None else env)

    url = (e.get("SUPABASE_URL") or "").rstrip("/")
    key = e.get("SUPABASE_PUBLISHABLE_KEY") or ""
    missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_PUBLISHABLE_KEY", key)) if not v]
    if missing:
        raise RuntimeError(f"缺少环境变量：{', '.join(missing)}。见 api/.env.example")

    # 拒绝 secret / service_role key。与 `supabase/verify_rls_prod.py` 开头那条同源：
    # 用它跑的东西看起来一切正常，而 RLS 根本没有参与——**一个永远通过的防护**。
    if key.startswith("sb_secret_") or "service_role" in key:
        raise RuntimeError(
            "SUPABASE_PUBLISHABLE_KEY 给的是 secret / service_role key。"
            "本服务以用户身份走 RLS，拿 service_role 等于把 RLS 整个绕过（口径 F40）。"
        )

    return Settings(
        supabase_url=url,
        supabase_publishable_key=key,
        anthropic_api_key=(e.get("ANTHROPIC_API_KEY") or "").strip() or None,
    )
