#!/usr/bin/env python3
"""F1 阶段 1 · **生产环境**验收：style_config 的 RLS 与 AB 分组护栏。

只用 publishable key + 两个真实账号的登录态跑，**不碰 secret key**——
约束 2：绕过 RLS 是合规问题不是 bug（PRD §12.1 / F1方案 §6.2）。
零第三方依赖（urllib），与 a_rules / explain 同一约定。

    cd supabase && cp .env.example .env && $EDITOR .env
    python3 verify_rls_prod.py

## 每条断言都配了反向控制

任务书的验收原文是「用 A 的登录态读 B 的行 → 返回空」。**B 的行不存在时它也返回空**，
表被整个收权时同样返回空——这条断言单独拿出来是空转的（通则七 #5）。
本地反向验证（verify_rls_local.mjs 的 R3）把这个场景演示了出来。

故每条「拒绝」断言旁边都有一条「允许」断言，两条一起才有意义：

    2 A 读不到 B    ← 控制：1 B 读得到自己（否则可能是整表不可读）
    3 A 改不动 B    ← 控制：6 B 改得动自己（否则可能是 update 通路根本不通）
    5 f1_arm 改不动 ← 同上，控制也是 6

## 断言的是错误码，不是「有没有报错」

步骤 4（A 冒名 insert user_id = B）：user_id 是主键，且触发器保证每个合法用户
都已有行，所以冒名 insert 必然也撞唯一约束。**只断言「报了错」测的是主键，
不是 with check**——RLS 关掉后错误码从 42501 变成 23505，断言照样绿。
本地实测（2026-09-07）：RLS 开 → 42501；RLS 关 → 23505。故断言 42501。

⚠️ **2026-09-10 起步骤 4 拦它的层换了。** 迁移 20260910090000 收回了 authenticated
的 insert 授权并删掉 insert 策略，所以现在先被**权限层**拒。权限层与 RLS 层的
SQLSTATE **都是 42501**——只断错误码的话，这条断言会在「测的对象已经换了」的
情况下静默保持绿色（与它当初要修的那个毛病同形，只是换了一层）。故同时断错误文本：
权限层报 permission denied，RLS 层报 row-level security。

配套的「允许」控制在本地（口径 F11/F12）：`verify_rls_local.mjs` 的
`trigger_inserts_for_unprivileged_role`（无权限角色注册仍建行）与 R8（把两层加回来
即可插入）。**生产上不做破坏性验证**，所以那两条不在这里。
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def ssl_context() -> ssl.SSLContext:
    """CA 根证书。**只在找不到时才降级，且降级是报错不是关校验。**

    python.org 的 macOS 安装包不带根证书（要手动跑 Install Certificates.command），
    默认 context 会在握手阶段报 CERTIFICATE_VERIFY_FAILED。
    正确的修法是给它一个 bundle；**不是 check_hostname=False + CERT_NONE**——
    那样脚本会在一个未经验证的 TLS 通道上跑「合规验收」，而它看起来是绿的。
    """
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] > 0:
        return ctx
    for cafile in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"):
        if cafile and Path(cafile).is_file():
            ctx = ssl.create_default_context(cafile=cafile)
            if ctx.cert_store_stats()["x509_ca"] > 0:
                return ctx
    sys.exit("找不到 CA 根证书。macOS 上跑一次 "
             "/Applications/Python\\ 3.13/Install\\ Certificates.command，"
             "或设 SSL_CERT_FILE 指向一个 bundle。")


SSL_CTX = ssl_context()


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    dotenv = HERE / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    missing = [
        k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "F1_VERIFY_PASSWORD")
        if not env.get(k)
    ]
    if missing:
        sys.exit(f"缺少环境变量：{', '.join(missing)}（见 .env.example）")
    if env["SUPABASE_PUBLISHABLE_KEY"].startswith("sb_secret_") or "service_role" in env["SUPABASE_PUBLISHABLE_KEY"]:
        sys.exit("拒绝运行：给的是 secret / service_role key。约束 2——用它跑的验收什么也证明不了。")
    return env


ENV = load_env()
URL = ENV["SUPABASE_URL"].rstrip("/")
APIKEY = ENV["SUPABASE_PUBLISHABLE_KEY"]
PASSWORD = ENV["F1_VERIFY_PASSWORD"]
EMAIL_A = ENV.get("F1_VERIFY_EMAIL_A", "f1-rls-a@example.com")
EMAIL_B = ENV.get("F1_VERIFY_EMAIL_B", "f1-rls-b@example.com")


class Resp:
    def __init__(self, status: int, body: Any, raw: str):
        self.status, self.body, self.raw = status, body, raw

    @property
    def pg_code(self) -> str | None:
        """PostgREST 把 Postgres 的 SQLSTATE 原样放在 body.code 里。"""
        return self.body.get("code") if isinstance(self.body, dict) else None

    def __repr__(self) -> str:
        return f"<{self.status} {self.raw[:120]}>"


def call(method: str, path: str, token: str | None = None,
         payload: Any = None, headers: dict[str, str] | None = None) -> Resp:
    req = urllib.request.Request(f"{URL}{path}", method=method)
    req.add_header("apikey", APIKEY)
    req.add_header("Authorization", f"Bearer {token or APIKEY}")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30, context=SSL_CTX) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        status = e.code
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = raw
    return Resp(status, body, raw)


# ---------------------------------------------------------------- 账号

def sign_in(email: str) -> tuple[str, str]:
    """已存在就登录，不存在就注册。返回 (access_token, user_id)。"""
    r = call("POST", "/auth/v1/token?grant_type=password",
             payload={"email": email, "password": PASSWORD})
    if r.status != 200:
        r = call("POST", "/auth/v1/signup", payload={"email": email, "password": PASSWORD})
        if r.status not in (200, 201):
            sys.exit(f"账号 {email} 注册失败（{r.status}）：{r.raw[:300]}\n"
                     f"提示：Confirm email 需关闭；若报 500，很可能是 F09 的建行触发器抛了错——"
                     f"那是它该有的行为，去 SQL Editor 看错误。")
    if not isinstance(r.body, dict) or "access_token" not in r.body:
        sys.exit(f"账号 {email} 没拿到 access_token：{r.raw[:300]}")
    return r.body["access_token"], r.body["user"]["id"]


# ---------------------------------------------------------------- 断言

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✗\033[0m"
    if cond:
        PASSED += 1
    else:
        FAILED += 1
    print(f"  {mark} {name}" + (f"   {detail}" if detail else ""))


def rows(r: Resp) -> list:
    return r.body if isinstance(r.body, list) else []


def main() -> int:
    print(f"\n生产项目：{URL}")

    tok_a, uid_a = sign_in(EMAIL_A)
    tok_b, uid_b = sign_in(EMAIL_B)
    print(f"账号 A {uid_a}\n账号 B {uid_b}\n")
    if uid_a == uid_b:
        sys.exit("A 和 B 是同一个用户——整套验收会平凡通过。换邮箱。")

    sc = "/rest/v1/style_config"

    print("\033[1m0 · 触发器建行（口径 F09）\033[0m")
    r = call("GET", f"{sc}?user_id=eq.{uid_a}&select=*", tok_a)
    check("A 注册后自动有一行", len(rows(r)) == 1, f"{r.status} {rows(r)[:1]}")
    row_a = rows(r)[0] if rows(r) else {}
    check("默认档位是 P0 / more / conclusion_first / why",
          (row_a.get("detail_level"), row_a.get("use_examples"),
           row_a.get("structure"), row_a.get("depth")) == ("P0", "more", "conclusion_first", "why"))
    check("f1_arm 已定且取值合法", row_a.get("f1_arm") in ("pairwise", "text"), str(row_a.get("f1_arm")))

    print("\n\033[1m1 · B 读自己 → 1 行【步骤 2 的反向控制】\033[0m")
    r = call("GET", f"{sc}?user_id=eq.{uid_b}&select=*", tok_b)
    check("B 读得到自己的行", len(rows(r)) == 1, f"HTTP {r.status}")
    b_before = rows(r)[0] if rows(r) else {}

    print("\n\033[1m2 · A 读 B → 0 行\033[0m")
    r = call("GET", f"{sc}?user_id=eq.{uid_b}&select=*", tok_a)
    check("A 读不到 B 的行", r.status == 200 and len(rows(r)) == 0, f"HTTP {r.status} {r.raw[:80]}")
    r = call("GET", f"{sc}?select=*", tok_a)
    check("A 全表扫描也只看得到自己", len(rows(r)) == 1 and rows(r)[0]["user_id"] == uid_a,
          f"{len(rows(r))} 行")

    print("\n\033[1m3 · A 改 B → 0 行受影响\033[0m")
    r = call("PATCH", f"{sc}?user_id=eq.{uid_b}", tok_a,
             payload={"detail_level": "P3"}, headers={"Prefer": "return=representation"})
    check("A 的 update 影响 0 行", r.status in (200, 204) and len(rows(r)) == 0,
          f"HTTP {r.status} {r.raw[:80]}")
    r = call("GET", f"{sc}?user_id=eq.{uid_b}&select=detail_level", tok_b)
    check("B 的值确实没被改动", rows(r) and rows(r)[0]["detail_level"] == b_before.get("detail_level"),
          f"{b_before.get('detail_level')} → {rows(r)[0]['detail_level'] if rows(r) else '?'}")

    print("\n\033[1m4 · A 冒名 insert user_id = B → 42501 权限层（不是 23505，也不该是 RLS 层）\033[0m")
    r = call("POST", sc, tok_a, payload={"user_id": uid_b, "f1_arm": "pairwise"})
    body = (r.raw or "").lower()
    check("被拒且错误码是 42501", r.pg_code == "42501",
          f"HTTP {r.status} code={r.pg_code}"
          + ("  ← 23505 说明挡它的是主键，不是权限也不是 RLS" if r.pg_code == "23505" else ""))
    # 把「哪一层拦的」也钉住：insert 授权已收回，正确答案是权限层。
    # 退回 RLS 层（permission denied 变成 row-level security）说明有人把 grant 加回来了，
    # 那时这条会红并指出退化，而不是继续绿着。
    check("拦它的是权限层，不是 RLS 层", "permission denied" in body,
          f"body={r.raw[:120]}"
          + ("  ← 报的是 RLS，说明 insert 授权被加回来了" if "row-level security" in body else ""))

    print("\n\033[1m4.5 · 触发器建行仍然有效【步骤 4 的允许控制】\033[0m")
    # 步骤 4 只说明「客户端插不进去」。**把注册一起打死也会让它绿**——
    # 而那正是 F09 说的坏状态（用户没有配置行，进不了 onboarding）。
    # 生产上不能造新用户来测触发器，所以这里断言的是它的产物：
    # 两个验收账号的行都在，且 f1_arm 与 f1_assign_arm 算出来的一致。
    # 那两行只可能来自触发器——客户端从来没有过 insert 通路。
    r = call("GET", f"{sc}?user_id=eq.{uid_b}&select=user_id,f1_arm", tok_b)
    check("B 的行存在（只可能由触发器建）", len(rows(r)) == 1,
          f"HTTP {r.status} 行数={len(rows(r))}  ← 0 行说明触发器没跑或被 RLS 挡住")

    print("\n\033[1m5 · B 改自己的 f1_arm → 抛异常（口径 F10，不静默改写）\033[0m")
    other = "text" if b_before.get("f1_arm") == "pairwise" else "pairwise"
    r = call("PATCH", f"{sc}?user_id=eq.{uid_b}", tok_b, payload={"f1_arm": other})
    check("请求被拒", r.status >= 400, f"HTTP {r.status} code={r.pg_code}")
    check("拒的方式是抛异常，不是静默改写", r.pg_code == "23514", f"code={r.pg_code}")
    r = call("GET", f"{sc}?user_id=eq.{uid_b}&select=f1_arm", tok_b)
    check("f1_arm 没变", rows(r) and rows(r)[0]["f1_arm"] == b_before.get("f1_arm"))

    print("\n\033[1m6 · B 改自己的 detail_level → 成功【步骤 3、5 的反向控制】\033[0m")
    target = "P1" if b_before.get("detail_level") != "P1" else "P2"
    r = call("PATCH", f"{sc}?user_id=eq.{uid_b}", tok_b,
             payload={"detail_level": target}, headers={"Prefer": "return=representation"})
    check("B 改得动自己的档位", r.status in (200, 204) and len(rows(r)) == 1,
          f"HTTP {r.status} → {target}")
    check("updated_at 被触发器刷新",
          bool(rows(r)) and rows(r)[0].get("updated_at") != b_before.get("updated_at"))
    call("PATCH", f"{sc}?user_id=eq.{uid_b}", tok_b,
         payload={"detail_level": b_before.get("detail_level", "P0")})

    print("\n\033[1m7 · 其余护栏\033[0m")
    r = call("PATCH", f"{sc}?user_id=eq.{uid_b}", tok_b, payload={"detail_level": "P9"})
    check("CHECK 约束挡住非法档位", r.pg_code == "23514", f"HTTP {r.status} code={r.pg_code}")

    r = call("DELETE", f"{sc}?user_id=eq.{uid_b}", tok_b, headers={"Prefer": "return=representation"})
    r2 = call("GET", f"{sc}?user_id=eq.{uid_b}&select=user_id", tok_b)
    check("无 delete 策略：B 删不掉自己的行（注销走 cascade）", len(rows(r2)) == 1,
          f"delete HTTP {r.status}，行仍在")

    r = call("GET", f"{sc}?select=*")  # 只带 apikey，无用户 token
    check("未登录读不到任何行", r.status >= 400 or len(rows(r)) == 0,
          f"HTTP {r.status} {r.raw[:80]}")

    print(f"\n\033[1m合计\033[0m  绿 {PASSED} · 红 {FAILED}\n")
    if FAILED == 0:
        print("反向验证（拆掉防护后这些断言会不会红）见 verify_rls_local.mjs —— "
              "破坏性动作只在本地做，生产不 disable RLS（口径 F12）。\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
