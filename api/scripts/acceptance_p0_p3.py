#!/usr/bin/env python3
"""验收纪律 1：**同一个问题、同一份课件，P0 与 P3 各跑一次。**

    cd api && .venv/bin/python scripts/acceptance_p0_p3.py \
        --base https://<railway-host> \
        --email <账号> --password <密码>

这是 F1 五个阶段以来**第一次能端到端观测「配置改变了输出」**。
在此之前所有断言测的都是「存得对不对、采得对不对」。

## ⚠️ 判据不能只写「各自落在各自区间」

P0 是 30–120，P3 是 15–40，**两个区间在 30–40 上重叠**。
一段平均 35 字的讲解**同时满足两个区间**——只写「各自达标」的话，
一个把配置整个忽略、两次返回同一段文字的实现也能全绿。

所以判据是三条，跑之前定死：

1. P0 那次的平均段落字数落在 **30–120**
2. P3 那次落在 **15–40**
3. **`mean(P0) > mean(P3)`** ← 方向。这一条才是「配置改变了输出」的内容，
   前两条只是「都合法」

区间数字全部从生产的 `StyleConfig` 读，**脚本里不写字面量**（口径 F13/F21）。
第 3 条**不引入任何新阈值**：只比大小，不问「差多少才算数」——
那个数没有先验，现在拍就是瞎猜（同 F38 对 `dwell_ms` 的处置）。

## 允许控制

两次的 `config_fingerprint` 必须**不同**。少了它，「两个档位输出不同」
与「两次请求之间配置压根没变过」分不开。

## 它会改你的配置

脚本会把该账号的 `detail_level` 依次改成 P0 与 P3（以用户身份走 PostgREST，
受 RLS 约束），跑完**改回原值**。用一个专门的验收账号，别用你自己的。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api  # noqa: F401,E402  —— 注入 explain / a_rules 路径

from explain.style_axes import style_config_for  # noqa: E402


def call(url: str, *, method: str = "GET", token: str | None = None, apikey: str | None = None,
         payload: dict | None = None, headers: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if apikey:
        req.add_header("apikey", apikey)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=180) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="服务端地址，如 https://xxx.up.railway.app")
    ap.add_argument("--supabase-url", required=True)
    ap.add_argument("--publishable-key", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--question", default="Bloom filter 的误判率是怎么来的？")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    sb = args.supabase_url.rstrip("/")
    key = args.publishable_key

    # ---- 登录，拿用户自己的 JWT
    status, body = call(f"{sb}/auth/v1/token?grant_type=password", method="POST",
                        apikey=key, payload={"email": args.email, "password": args.password})
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        print(f"登录失败（{status}）：{body}")
        return 1
    token = body["access_token"]
    uid = body["user"]["id"]
    print(f"账号 {args.email}  user_id {uid}")

    # ---- 记下原值，跑完改回去
    status, rows = call(f"{sb}/rest/v1/style_config?select=detail_level&user_id=eq.{uid}",
                        token=token, apikey=key)
    if status != 200 or not isinstance(rows, list) or len(rows) != 1:
        print(f"读配置失败（{status}）：{rows}")
        return 1
    original = rows[0]["detail_level"]
    print(f"原档位 {original}（跑完会改回去）\n")

    results: dict[str, dict] = {}
    try:
        for tier in ("P0", "P3"):
            status, _ = call(f"{sb}/rest/v1/style_config?user_id=eq.{uid}", method="PATCH",
                             token=token, apikey=key, payload={"detail_level": tier},
                             headers={"Prefer": "return=representation"})
            if status not in (200, 204):
                print(f"改档位到 {tier} 失败（{status}）")
                return 1

            status, out = call(f"{base}/explain", method="POST", token=token,
                               payload={"question": args.question})
            if status != 200 or not isinstance(out, dict):
                print(f"{tier} 讲解失败（{status}）：{out}")
                return 1
            results[tier] = out
            print(f"{tier}  平均段落 {out['mean_paragraph_chars']:.1f} 字 · "
                  f"目标区间 {out['paragraph_length_target']} · "
                  f"重生成 {out['regenerations']} 次 · 指纹 {out['config_fingerprint']}")
    finally:
        call(f"{sb}/rest/v1/style_config?user_id=eq.{uid}", method="PATCH",
             token=token, apikey=key, payload={"detail_level": original})
        print(f"\n已把档位改回 {original}")

    # ---- 判据（跑之前定死，见文件头）
    print("\n判据：")
    passed = 0
    failed = 0

    def check(ok: bool, name: str, detail: str = "") -> None:
        nonlocal passed, failed
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail else ""))

    for tier in ("P0", "P3"):
        rng = style_config_for(tier).paragraph_length          # ← 区间从生产配置读
        mean = results[tier]["mean_paragraph_chars"]
        check(rng.min <= mean <= rng.max, f"{tier} 的平均段落落在 {rng.min}–{rng.max}",
              f"实测 {mean:.1f}")

    m0, m3 = results["P0"]["mean_paragraph_chars"], results["P3"]["mean_paragraph_chars"]
    check(m0 > m3, "方向：P0 的平均段落 > P3 的",
          f"{m0:.1f} vs {m3:.1f}  ← 两个区间在 30–40 上重叠，只看「各自达标」不够")

    check(results["P0"]["config_fingerprint"] != results["P3"]["config_fingerprint"],
          "允许控制：两次的 config_fingerprint 不同",
          "← 少了它，「输出不同」与「配置压根没变过」分不开")

    check(results["P0"]["text"] != results["P3"]["text"], "两次的正文不是同一段")

    print(f"\n{passed} 绿 / {failed} 红\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
