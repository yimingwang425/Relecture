#!/usr/bin/env python3
"""**第一次真跑摄入**：真 PDF → `/courses/{id}/documents` → 真 OpenAI → 真库。

    cd api && .venv/bin/python scripts/ingest_real.py \
        --base http://localhost:8099 \
        --supabase-url https://<ref>.supabase.co --publishable-key sb_publishable_... \
        --email <验收账号> --password <密码>

## 为什么必须有这一发

阶段 4 的真库验收 22 绿，是**直接往表里写行**验 schema 与 RPC 的，
**没有经过 `/courses/{id}/documents`**。`parse → slice → compose → embed → 落库`
这条链在真库上一次都没跑过，测试里的嵌入器全是确定性假嵌入器。

所以在这一发之前，能说的只有「schema 接得住手写的向量」，
**不能说「摄入链成立」**——那是两句话。

## ⚠️ 维度那两条：判据的两端**必须不同源**

`SELECTED_DIMENSIONS = 1024` 是**显式截断**，不是模型默认值
（`text-embedding-3-large` 默认 3072，`-small` 默认 1536）。
必须显式传 `dimensions=1024`，否则向量进不了 `vector(1024)` 那一列。

**断的是实际返回的 `len(vector)`，不是 `embedder.dimensions`。**
拿嵌入器跟它自己比永远不会失败——那是 F16 的第三次复发，
`api/api/ingest.py` 里那条注释记着它是怎么被抓出来的。

而「实际返回 1024」这一条**单独还不够**：如果这个模型的默认维度恰好就是 1024，
那么 `dimensions` 参数压根没发出去时它同样会绿。所以配一条：
**模型默认维度 ≠ SELECTED_DIMENSIONS**。两条合起来才证明那次截断在承重。

同一件事在**落库后再验一次，且取另一端**：从 `slice_embeddings` 读回那一列，
数它有几个分量。一端是 OpenAI 刚返回的，一端是 pgvector 存下来的——
这两端不会一起变。

## 它要花钱

真调 OpenAI embedding（PRD §13.3 的**第二次出境**，切片原文出境）。
三份文档 ≈ 69 页，`text-embedding-3-large` $0.13/M tokens，量级 $0.01。
**这是本项目第一次真调 OpenAI。**

## 语料选择不是随手挑的

- `MIT22_081JF10_lec01b.pdf`（能源，39 页）与 `MIT11_201F07_slides2.pdf`（规划，15 页）
  **整份 CC BY-NC-SA，无第三方保留权利页**（`测试集/SOURCES.md`）。
  14.129 那份有 8 页保留权利内容，而 `/courses/{id}/documents` **不传
  `restricted_pages`**（受限页排除对真实用户恒不触发，已登记）——
  用它就等于把那 8 页真的发给 OpenAI。**不用。**
  15.316 是整份扫描件（0 切片），验不了检索。
- 课程 B 里那份与课程 A 的 A2 是**逐字节同一份文件**。这不是省事：
  内容不同的两门课，「跨课不召回」可能是**主题本身把它们分开了**，
  作用域过滤压根没参与。内容相同时，**只有作用域能把它们分开**。

决策日期：2026-09-12
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "scripts"))
import api  # noqa: F401,E402  —— 注入 explain / a_rules / retrieval 路径

from prod_verify import ssl_context, wake_service  # noqa: E402
from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL  # noqa: E402

SSL_CTX = ssl_context()
CORPUS = REPO / "实验脚本" / "测试集"

#: 摄入完成后写在这里，供 `supabase/verify_retrieval_prod.py` 复用。
#: **不重跑摄入就能重跑检索验收**——重跑摄入要再花一次钱，且真库里会多一份数据。
STATE_PATH = HERE / ".ingest_real_state.json"

#: 模型默认维度。**这张表只在本脚本用于「默认值 ≠ 选定值」那一条控制**，
#: 不是维度的出处——出处是 `retrieval.selection.SELECTED_DIMENSIONS`。
#: 与 `retrieval/embed.py` 的 `OpenAIEmbedder.dimensions` 回落表同一份事实。
MODEL_DEFAULT_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}

PASSED = FAILED = 0
LAYERS = {
    "维度": "\033[36m[维度]\033[0m",
    "摄入": "\033[32m[摄入链]\033[0m",
    "落库": "\033[34m[落库核对]\033[0m",
    "唯一": "\033[35m[唯一索引]\033[0m",
}


def check(layer: str, name: str, cond: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✗\033[0m"
    print(f"  {mark} {LAYERS[layer]} {name}" + (f"   {detail}" if detail else ""))
    return cond


def call(url: str, *, method: str = "GET", token: str | None = None, apikey: str | None = None,
         payload: object = None, headers: dict | None = None,
         body: bytes | None = None, content_type: str | None = None,
         timeout: int = 300) -> tuple[int, object]:
    req = urllib.request.Request(url, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    elif payload is not None:
        req.add_header("Content-Type", "application/json")
    if apikey:
        req.add_header("apikey", apikey)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = body if body is not None else (json.dumps(payload).encode() if payload is not None else None)
    try:
        ctx = SSL_CTX if url.startswith("https://") else None
        with urllib.request.urlopen(req, data, timeout=timeout, context=ctx) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    """最小 multipart 编码。**不引第三方依赖**——这份脚本要能在只有标准库的地方跑。"""
    boundary = f"----f1ingest{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + data + tail, f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="服务端地址（Render 或本机容器）")
    ap.add_argument("--supabase-url", required=True)
    ap.add_argument("--publishable-key", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--skip-dimension-probe", action="store_true",
                    help="跳过那一次独立的 OpenAI 探针（它要本机有 OPENAI_API_KEY）")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    sb = args.supabase_url.rstrip("/")
    key = args.publishable_key

    print(f"\n服务端 {base}\n数据库 {sb}\n")

    # ---------------------------------------------------------------- 唤醒
    #
    # 冷启动不是失败（Render 免费层 15 分钟休眠）。**但重试 3 次仍不通就是失败。**
    if not wake_service(base):
        return 1

    # ================================================================ 1 · 维度
    #
    # **这一节在花钱之前跑。** 维度错的话后面三份文档全部白传。
    print("\n\033[1m1 · 维度（判据的两端不同源）\033[0m")

    default_dim = MODEL_DEFAULT_DIMENSIONS.get(SELECTED_MODEL)
    check("维度", f"{SELECTED_MODEL} 的默认维度 ≠ SELECTED_DIMENSIONS",
          default_dim is not None and default_dim != SELECTED_DIMENSIONS,
          f"默认 {default_dim} vs 选定 {SELECTED_DIMENSIONS} "
          "← 这一条是下一条的分辨力来源：默认值若恰好是 1024，"
          "`dimensions` 参数没发出去时下一条照样绿")

    if args.skip_dimension_probe:
        print("  \033[2m…跳过 OpenAI 探针（--skip-dimension-probe）。"
              "落库后那一条仍然会验实际维度。\033[0m")
    else:
        from retrieval.embed import OpenAIEmbedder

        probe = OpenAIEmbedder(SELECTED_MODEL, SELECTED_DIMENSIONS)
        vec = probe.embed(["维度探针：这一句只为拿回一个真向量。"])[0]
        actual = len(vec)
        check("维度", "**OpenAI 实际返回**的向量长度 == SELECTED_DIMENSIONS",
              actual == SELECTED_DIMENSIONS,
              f"实际 {actual} / 声称 {probe.dimensions} / schema {SELECTED_DIMENSIONS}  "
              "← 断的是实际返回，不是 embedder.dimensions（口径 F16）")
        if actual != SELECTED_DIMENSIONS:
            print("\n\033[31m维度对不上，后面不跑了。\033[0m "
                  "向量进不了 vector(1024) 那一列，传文档只是白花钱。\n")
            return 1

    # ---------------------------------------------------------------- 登录
    status, body = call(f"{sb}/auth/v1/token?grant_type=password", method="POST",
                        apikey=key, payload={"email": args.email, "password": args.password})
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        print(f"登录失败（{status}）：{body}")
        return 1
    token, uid = body["access_token"], body["user"]["id"]
    print(f"\n账号 {args.email}  user_id {uid}")

    # ================================================================ 2 · 摄入
    print("\n\033[1m2 · 摄入链（真 PDF → /courses/{id}/documents → 真 OpenAI → 真库）\033[0m")

    tag = uuid.uuid4().hex[:6]

    def mk_course(name: str) -> str | None:
        s, b = call(f"{base}/courses", method="POST", token=token, payload={"name": name})
        ok = check("摄入", f"建课「{name}」", s == 201 and isinstance(b, dict) and "id" in b,
                   f"HTTP {s} {str(b)[:160]}")
        return b["id"] if ok else None

    course_a = mk_course(f"验收课程 A · {tag}")
    course_b = mk_course(f"验收课程 B · {tag}")
    if not (course_a and course_b):
        return 1

    def upload(course_id: str, pdf: str, label: str) -> dict | None:
        data = (CORPUS / pdf).read_bytes()
        payload, ctype = multipart("file", pdf, data)
        s, b = call(f"{base}/courses/{course_id}/documents", method="POST", token=token,
                    body=payload, content_type=ctype, timeout=600)
        ok = check("摄入", f"{label}：上传 {pdf}（{len(data) // 1024} KB）→ 201",
                   s == 201 and isinstance(b, dict), f"HTTP {s} {str(b)[:200]}")
        if not ok:
            return None
        assert isinstance(b, dict)
        check("摄入", f"{label}：切片数 > 0", b["slice_count"] > 0, f"{b['slice_count']} 片")
        check("摄入", f"{label}：嵌入数 == 切片数",
              b["embedded_count"] == b["slice_count"],
              f"{b['embedded_count']} / {b['slice_count']}  "
              "← 不等说明有切片没拿到向量，而它在检索里就是不存在")
        print(f"      \033[2mdocument_id {b['document_id']}  跳过页 {b['skipped_pages']}  "
              f"verdict {b['verdict']}\033[0m")
        return b

    a1 = upload(course_a, "MIT22_081JF10_lec01b.pdf", "A1 能源")
    a2 = upload(course_a, "MIT11_201F07_slides2.pdf", "A2 规划")
    # 与 A2 **逐字节同一份文件**，挂进另一门课。见文件头：内容相同时，
    # 「跨课不召回」只能由作用域过滤来解释。
    b1 = upload(course_b, "MIT11_201F07_slides2.pdf", "B1 规划（与 A2 同文件）")
    if not (a1 and a2 and b1):
        return 1

    # ================================================================ 3 · 落库核对
    #
    # 上一节读的是**服务返回的计数**，这一节读的是**数据库里真躺着的行**。
    # 两端不会一起变——服务把数算错了，这里就对不上。
    print("\n\033[1m3 · 落库核对（另一端：直接查库，不看服务返回的数）\033[0m")

    def rows(path: str) -> list:
        s, b = call(f"{sb}/rest/v1/{path}", token=token, apikey=key)
        return b if s == 200 and isinstance(b, list) else []

    docs_a = rows(f"documents?select=id,filename,page_count&course_id=eq.{course_a}")
    docs_b = rows(f"documents?select=id,filename,page_count&course_id=eq.{course_b}")
    check("落库", "课程 A 有 2 份文档", len(docs_a) == 2, f"{len(docs_a)} 份")
    check("落库", "课程 B 有 1 份文档", len(docs_b) == 1, f"{len(docs_b)} 份")

    for label, d in (("A1", a1), ("A2", a2), ("B1", b1)):
        sl = rows(f"slices?select=slice_id&document_id=eq.{d['document_id']}")
        em = rows(f"slice_embeddings?select=slice_id,model&document_id=eq.{d['document_id']}")
        check("落库", f"{label}：slices 行数 == 服务报的 {d['slice_count']}",
              len(sl) == d["slice_count"], f"库里 {len(sl)} 行")
        check("落库", f"{label}：slice_embeddings 行数 == {d['embedded_count']}",
              len(em) == d["embedded_count"], f"库里 {len(em)} 行")
        check("落库", f"{label}：model 列 == '{SELECTED_MODEL}@{SELECTED_DIMENSIONS}'",
              bool(em) and all(r["model"] == f"{SELECTED_MODEL}@{SELECTED_DIMENSIONS}" for r in em),
              f"{ {r['model'] for r in em} }")

    # **维度的第二端**：pgvector 把那一列读回来是 '[x,y,...]' 文本，数分量。
    one = rows(f"slice_embeddings?select=embedding&document_id=eq.{a1['document_id']}&limit=1")
    stored_dim = None
    if one and isinstance(one[0].get("embedding"), str):
        stored_dim = len(one[0]["embedding"].strip().lstrip("[").rstrip("]").split(","))
    elif one and isinstance(one[0].get("embedding"), list):
        stored_dim = len(one[0]["embedding"])
    check("维度", "**数据库里那一列**的向量真的是 SELECTED_DIMENSIONS 维",
          stored_dim == SELECTED_DIMENSIONS,
          f"读回 {stored_dim} 维  ← 与「OpenAI 返回 1024」是两端："
          "一个是刚返回的，一个是 pgvector 存下来的，它们不会一起变")

    # ================================================================ 4 · 唯一索引
    print("\n\033[1m4 · 唯一索引 (owner, course_id, sha) 的两面\033[0m")
    data = (CORPUS / "MIT11_201F07_slides2.pdf").read_bytes()
    payload, ctype = multipart("file", "MIT11_201F07_slides2.pdf", data)
    s, b = call(f"{base}/courses/{course_a}/documents", method="POST", token=token,
                body=payload, content_type=ctype, timeout=600)
    detail = (b or {}).get("detail") if isinstance(b, dict) else str(b)[:120]
    check("唯一", "同课重传同一份文件 → 409 且是人话，不是 23505",
          s == 409 and isinstance(detail, str) and "23505" not in detail,
          f"HTTP {s}「{detail}」")
    # 允许控制：B1 那次已经成立了——同一份文件进了另一门课并返回 201。
    # 少了它，上一条在「任何重复文件都拦」时同样绿，而那会让同一份导论讲义
    # 用不了两个学期（口径 F47 明确要支持）。
    check("唯一", "允许控制：同一份文件挂进另一门课 → 201（上面 B1 那次）",
          b1 is not None and b1["document_id"] != a2["document_id"],
          f"A2 {a2['document_id'][:8]} ≠ B1 {b1['document_id'][:8]}")

    # ---------------------------------------------------------------- 交接
    state = {
        "created_at_tag": tag,
        "supabase_url": sb,
        "email": args.email,
        "user_id": uid,
        "course_a": course_a,
        "course_b": course_b,
        "doc_a1": {"id": a1["document_id"], "file": "MIT22_081JF10_lec01b.pdf",
                   "topic": "能源", "slices": a1["slice_count"]},
        "doc_a2": {"id": a2["document_id"], "file": "MIT11_201F07_slides2.pdf",
                   "topic": "城市规划", "slices": a2["slice_count"]},
        "doc_b1": {"id": b1["document_id"], "file": "MIT11_201F07_slides2.pdf",
                   "topic": "城市规划（与 A2 同文件）", "slices": b1["slice_count"]},
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\033[2m已写 {STATE_PATH.name}：检索验收复用它，不必重跑摄入"
          "（重跑要再花一次 OpenAI 的钱，且真库里会多一份数据）\033[0m")

    print(f"\n\033[1m合计\033[0m  绿 {PASSED} · 红 {FAILED}")
    print("\033[2m这批是**真库 + 真 OpenAI** 的观测，与 api/tests 的本地 pytest 数"
          "不相加（口径 F34）。\033[0m\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
