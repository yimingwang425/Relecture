#!/usr/bin/env python3
"""阶段 5 · **验收纪律 2**：检索真的按课作用域，且同课多份讲义都召得回。

    cd supabase && python3 verify_retrieval_prod.py --base http://localhost:8099

前置：先跑一次 `api/scripts/ingest_real.py`（它把真语料摄入真库，
并写下 `api/scripts/.ingest_real_state.json`）。本脚本**复用那份状态**，
不重新摄入——重摄一次要再花一笔 OpenAI 的钱，且真库里会多一份数据。

## 两条判据，缺一不可

1. **A 课提问召回的切片全在 A 课**（跨课不召回）
2. **同课两份讲义必须能同时出现在一次召回里**（允许控制）

第 2 条不是锦上添花：**少了它，第 1 条在检索整个坏掉时同样全绿**——
一个恒返回空列表的实现完美满足「不召回别的课」。
这与 `verify_corpus_prod.py` 里那条「同一门课的两份讲义都召回」是同一个角色，
只是那里用的是手写正交向量，这里用的是**真模型对真课件算出来的向量**。

## 跨课那一条为什么用**同一份文件**

课程 B 里那份与课程 A 的 A2 是**逐字节同一份文件**（`ingest_real.py` 特意这么传的）。
内容不同的两门课，「跨课不召回」可能是**主题本身把它们分开了**，
作用域过滤压根没参与；内容相同时，**只有作用域能把它们分开**。

## 常量不写字面量

`SELECTED_MODEL` / `SELECTED_DIMENSIONS` / `EMBED_TEXT_RULESET_VERSION`
从 `retrieval/` 的源文件里**解析出来**，不在本脚本写第二份（口径 F13/F21）。

为什么是解析而不是 import：`retrieval/__init__.py` 会把 compose / embed / index
全拉进来，于是要 numpy、slicer、openai —— 那就把这份脚本的
「零第三方依赖、不依赖本机 venv」整个丢掉了。解析取的是同一个出处，
而且解析失败会**退出**，不会拿一个默认值继续（同 `verify_rls_prod.py` 对 CA 的处置）。

## 每条断言标着它验的是哪一层

`[作用域]` / `[允许控制]` / `[RLS]` / `[服务层]` / `[观察]`。
失败时的处置完全不同：作用域错要改 `match_slices` 的 where；
RLS 错要改策略；服务层错在 `api/api/retriever.py`。
压成「检索失败」的话三种原因都长一个样。

决策日期：2026-09-12
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "scripts"))
from prod_verify import ssl_context, wake_service  # noqa: E402

SSL_CTX = ssl_context()
STATE_PATH = REPO / "api" / "scripts" / ".ingest_real_state.json"


# ---------------------------------------------------------------- 常量：解析，不抄

def parse_constant(path: Path, name: str) -> str:
    """从源文件里取一个模块级常量。**取不到就退出**，不给默认值。

    拿默认值继续的后果是：脚本照常跑完、照常全绿，而它用的 model 字符串
    与摄入写进库里的那个不一样 —— 表现是「什么都召不回」，
    和「这门课没有内容」长得一模一样。
    """
    if not path.is_file():
        sys.exit(f"读不到 {path}：常量 {name} 的唯一出处不在了，脚本不能继续。")
    m = re.search(rf"^{re.escape(name)}\s*=\s*(.+?)\s*(?:#.*)?$",
                  path.read_text(encoding="utf-8"), re.MULTILINE)
    if not m:
        sys.exit(f"在 {path.name} 里找不到 {name} = ...。"
                 "它是这个值的唯一出处；本脚本不在自己这里写第二份。")
    return m.group(1).strip().strip("\"'")


SELECTION = REPO / "retrieval" / "retrieval" / "selection.py"
COMPOSE = REPO / "retrieval" / "retrieval" / "compose.py"
SELECTED_MODEL = parse_constant(SELECTION, "SELECTED_MODEL")
SELECTED_DIMENSIONS = int(parse_constant(SELECTION, "SELECTED_DIMENSIONS"))
RULESET = parse_constant(COMPOSE, "EMBED_TEXT_RULESET_VERSION")
#: 与 `api/api/ingest.py` 的 `EMBED_MODEL_ID()` 同形。拼法在那里，这里只复现。
MODEL_ID = f"{SELECTED_MODEL}@{SELECTED_DIMENSIONS}"


# ---------------------------------------------------------------- 环境

def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for dotenv in (HERE / ".env", REPO / "retrieval" / ".env"):
        if dotenv.is_file():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY",
                           "F1_VERIFY_PASSWORD", "OPENAI_API_KEY") if not env.get(k)]
    if missing:
        sys.exit(f"缺少环境变量：{', '.join(missing)}（见 .env.example；"
                 "OPENAI_API_KEY 可放 retrieval/.env）")
    key = env["SUPABASE_PUBLISHABLE_KEY"]
    if key.startswith("sb_secret_") or "service_role" in key:
        sys.exit("拒绝运行：给的是 secret / service_role key。"
                 "用它跑的 RLS 验收什么也证明不了。")
    return env


ENV = load_env()
URL = ENV["SUPABASE_URL"].rstrip("/")
APIKEY = ENV["SUPABASE_PUBLISHABLE_KEY"]
PASSWORD = ENV["F1_VERIFY_PASSWORD"]
EMAIL_A = ENV.get("F1_VERIFY_EMAIL_A", "f1-rls-a@example.com")
EMAIL_B = ENV.get("F1_VERIFY_EMAIL_B", "f1-rls-b@example.com")


def call(method: str, url: str, token: str | None = None, payload: object = None,
         apikey: str | None = None, timeout: int = 300) -> tuple[int, object, str]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if apikey:
        req.add_header("apikey", apikey)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        ctx = SSL_CTX if url.startswith("https://") else None
        with urllib.request.urlopen(req, data, timeout=timeout, context=ctx) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw), raw
        except json.JSONDecodeError:
            return e.code, None, raw


def rest(method: str, path: str, token: str, payload: object = None):
    return call(method, f"{URL}{path}", token=token, payload=payload, apikey=APIKEY)


def sign_in(email: str) -> tuple[str, str]:
    s, b, raw = call("POST", f"{URL}/auth/v1/token?grant_type=password",
                     apikey=APIKEY, payload={"email": email, "password": PASSWORD})
    if s != 200 or not isinstance(b, dict):
        sys.exit(f"账号 {email} 登录失败（{s}）：{raw[:300]}")
    return b["access_token"], b["user"]["id"]


def embed(text: str) -> list[float]:
    """真调 OpenAI 嵌入。**维度显式传**，与摄入那一侧同一个值。

    断的是**实际返回**的长度（口径 F16：拿嵌入器跟它自己比永远不会失败）。
    """
    s, b, raw = call("POST", "https://api.openai.com/v1/embeddings",
                     token=ENV["OPENAI_API_KEY"],
                     payload={"model": SELECTED_MODEL, "input": text,
                              "dimensions": SELECTED_DIMENSIONS})
    if s != 200 or not isinstance(b, dict):
        sys.exit(f"OpenAI 嵌入失败（{s}）：{raw[:300]}")
    vec = b["data"][0]["embedding"]
    if len(vec) != SELECTED_DIMENSIONS:
        sys.exit(f"OpenAI 实际返回 {len(vec)} 维，而 schema 是 vector({SELECTED_DIMENSIONS})。"
                 "后面全部白跑，先去查 dimensions 参数有没有发出去。")
    return vec


# ---------------------------------------------------------------- 断言

PASSED = FAILED = 0
LAYERS = {"作用域": "\033[33m[作用域]\033[0m", "允许": "\033[32m[允许控制]\033[0m",
          "RLS": "\033[35m[RLS]\033[0m", "服务": "\033[36m[服务层]\033[0m",
          "埋点": "\033[34m[埋点]\033[0m"}


def check(layer: str, name: str, cond: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
    print(f"  {'\033[32m✓\033[0m' if cond else '\033[31m✗\033[0m'} "
          f"{LAYERS[layer]} {name}" + (f"   {detail}" if detail else ""))
    return cond


def observe(name: str, value: str) -> None:
    """**观察项，不计入绿数。**

    判读标准跑之前没定死的东西不许当判据（口径 F29）。
    计进绿数的话，一份业务断言全红的报告仍然会有几条绿。
    """
    print(f"  \033[2m·\033[0m \033[2m[观察]\033[0m {name}   {value}")


#: 线上那个服务自报的构建标识。`None` = 它答不出自己是谁（口径 F17）。
#: 在 `main()` 里被赋值 —— §4 的失败提示要引用它。
DEPLOYED_COMMIT: str | None = None


#: 两个提问跑之前定死。**规划那条是主力**——课程 A 与课程 B 里那份规划讲义
#: 内容逐字节相同，所以它是「只有作用域能把两门课分开」的那一发。
Q_PLANNING = "城市规划的历史传统有哪些？"
Q_ENERGY = "这门课讲的能源问题主要有哪些？"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="服务端地址（Render 或本机容器）")
    ap.add_argument("--state", default=str(STATE_PATH))
    ap.add_argument("--skip-explain", action="store_true",
                    help="跳过 /explain 那一节（它要真调 Anthropic）")
    ap.add_argument("--expect-commit", default=None,
                    help="期望线上跑的是哪个提交（口径 F17）。给了就断言它相等；"
                         "不给只打印。**不给的时候请自己核对那一行**")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    state_path = Path(args.state)
    if not state_path.is_file():
        sys.exit(f"找不到 {state_path}。先跑一次 api/scripts/ingest_real.py —— "
                 "本脚本验的是**真语料**上的检索，凭空造两行验不了「同课两份讲义都召回」。")
    st = json.loads(state_path.read_text(encoding="utf-8"))

    course_a, course_b = st["course_a"], st["course_b"]
    a1, a2, b1 = st["doc_a1"]["id"], st["doc_a2"]["id"], st["doc_b1"]["id"]

    print(f"\n生产项目：{URL}\n服务端：{base}")
    print(f"\033[2m选型（从 retrieval/ 源文件解析，不写字面量）："
          f"{MODEL_ID} · ruleset {RULESET}\033[0m")
    print(f"\033[2m课程 A {course_a[:8]}（{st['doc_a1']['file']} + {st['doc_a2']['file']}）"
          f"｜课程 B {course_b[:8]}（{st['doc_b1']['file']}，与 A2 **同一份文件**）\033[0m\n")

    if not wake_service(base):
        return 1

    # ================================================================ 0 · 构建标识
    #
    # ⚠️ **口径 F17：验收前先核对读的是哪个构建。**
    #
    # 2026-09-12 这一节不存在，代价是：Render 上跑的是阶段 4 的旧构建
    # （本轮改动没提交），而第 1–3 节**全绿**——它们验的是 `match_slices`
    # 这一层，那一层在旧构建里也是对的（schema 与 RPC 早就在生产了）。
    # 于是「8 条绿」被读成了「这一版部署成功」。
    #
    # 构建标识答不出来时**不停**（第 1–3 节验的是数据库，与哪个构建无关），
    # 但要吵一声，并且 §4 的失败提示会把它引出来。
    print("\n\033[1m0 · 构建标识（口径 F17：先核对读的是哪个构建）\033[0m")
    global DEPLOYED_COMMIT
    s, hb, raw = call("GET", f"{base}/health")
    DEPLOYED_COMMIT = (hb or {}).get("build_commit") if isinstance(hb, dict) else None
    if DEPLOYED_COMMIT:
        print(f"  线上构建：\033[1m{DEPLOYED_COMMIT}\033[0m")
    else:
        print("  \033[33m线上构建：答不出（/health 的 build_commit 是 null）。"
              "\n    这本身是一个问题：没有构建标识，「8 条绿」说不出是哪一版的 8 条绿。"
              "\n    Render 会自动注入 RENDER_GIT_COMMIT；本机构建传 "
              "--build-arg BUILD_COMMIT=$(git rev-parse HEAD)。\033[0m")
    if args.expect_commit:
        check("服务", "线上构建 == --expect-commit",
              DEPLOYED_COMMIT is not None
              and DEPLOYED_COMMIT.startswith(args.expect_commit[:7]),
              f"线上 {DEPLOYED_COMMIT!r} / 期望 {args.expect_commit!r}  "
              "← 不等就别解释下面任何一条结果（口径 F17）")

    tok_a, uid_a = sign_in(EMAIL_A)
    tok_b, uid_b = sign_in(EMAIL_B)
    if uid_a == uid_b:
        sys.exit("A 与 B 是同一个用户——跨用户那几条会平凡通过。换邮箱。")

    def match(token: str, course_id: str, vec: list[float], limit: int):
        s, rows, raw = rest("POST", "/rest/v1/rpc/match_slices", token, {
            "p_course_id": course_id, "p_query": vec,
            "p_model": MODEL_ID, "p_ruleset": RULESET, "p_limit": limit})
        return s, (rows if isinstance(rows, list) else []), raw

    def explain_zero_rows(token: str, course_id: str) -> str:
        """**「0 行」有两种原因，处置完全不同。** 出现 0 行时多问一句，别让它们同形。

        ① `match_slices` 的 where 没匹配上（`model` / `embed_ruleset` / owner 对不上）
        ② 那些行真的不在（摄入没跑完、或被删了）

        2026-09-12 记：真库上出现过**一次**课程 A 返回 0 行，随后 6 次重跑
        全部 30 行，**至今没有解释**。那一次脚本只打印了 `len(rows)`，
        于是这两种原因分不开——这个函数就是那次缺的东西。
        如实登记：**不是「已修」，是「下次再出现时能查」**。
        """
        s, docs, _ = rest("GET", f"/rest/v1/documents?select=id&course_id=eq.{course_id}", token)
        s2, embs, _ = rest(
            "GET", "/rest/v1/slice_embeddings?select=document_id,model,embed_ruleset&limit=3", token)
        return (f"\n      \033[33m→ 0 行的两种原因分开查："
                f"\n        ① 这门课的 documents：{len(docs or [])} 行"
                f"\n        ② 该用户的 slice_embeddings 样本：{embs if embs else '（读不到）'}"
                f"\n        脚本用的 model/ruleset：{MODEL_ID!r} / {RULESET!r}"
                f"\n        ①=0 → 摄入没落库；①>0 而 ② 的 model/ruleset 与脚本不同 → "
                f"where 对不上（改选型后没重算向量）。\033[0m")

    v_planning = embed(Q_PLANNING)
    v_energy = embed(Q_ENERGY)

    # ============================================================ 1 · 允许控制先跑
    #
    # **顺序是刻意的。** 「同课两份讲义都召得回」放在「跨课不召回」前面：
    # 它要是红的，下面那条的全绿一文不值（什么都召不回时它同样成立）。
    print("\n\033[1m1 · 允许控制：同课两份讲义能同时出现在一次召回里\033[0m")

    s, rows, raw = match(tok_a, course_a, v_planning, 30)
    check("允许", "课程 A 有召回", s == 200 and len(rows) > 0,
          f"HTTP {s} {len(rows)} 行 {raw[:120]}"
          + (explain_zero_rows(tok_a, course_a) if s == 200 and not rows else ""))
    docs_hit = {r["document_id"] for r in rows}
    check("允许", "**两份讲义都在同一次召回里**", docs_hit == {a1, a2},
          f"命中 {len(docs_hit)} 份 / 应为 2 份  "
          "← 少了这一条，下面「跨课不召回」在检索整个坏掉时同样全绿")

    # ============================================================ 2 · 作用域
    print("\n\033[1m2 · 作用域（口径 F47：一门课）\033[0m")

    check("作用域", "课程 A 的召回**全部**来自课程 A 的两份文档",
          bool(rows) and all(r["document_id"] in {a1, a2} for r in rows),
          f"{len(rows)} 行，外来 {sum(1 for r in rows if r['document_id'] not in {a1, a2})} 行")

    s, rows_b, raw = match(tok_a, course_b, v_planning, 30)
    # ⚠️ 2026-10-07 改：原判据是「每一行都是 B1」——它把「课程 B 只有 B1 一份文档」当成了
    # 恒真的前提。真库上课程 B 在 2026-09-12 12:19 多了一份 22.081J（不是 ingest_real 传的），
    # 于是这条在作用域完全正确时红了：30 行、含 A2 的 0 行。**它要守的是「课程 A 的文档
    # 一行都不混进来」**，所以改成：课程 B 的文档清单**现查**，召回 ⊆ 它，且 A2 不出现。
    # 两端仍不同源：A2 的 id 来自 state 文件（摄入时记的），B 的清单来自数据库（现查）。
    s_docs, docs_b_live, _ = rest("GET", f"/rest/v1/documents?select=id,filename&course_id=eq.{course_b}", tok_a)
    b_ids = {d["id"] for d in (docs_b_live or [])} if s_docs == 200 else set()
    extra = [d for d in (docs_b_live or []) if d["id"] != b1]
    if extra:
        print(f"  \033[2m· [观察] 课程 B 里有 state 文件没记的文档：{[(d['id'][:8], d['filename']) for d in extra]}"
              "  ← 验收夹具漂了（不是 ingest_real 传的）；不影响判据，但「B 只有一份」不再成立\033[0m")
    check("作用域", "同一个提问查课程 B → 全部来自课程 B 的文档，**A2 一行都不出现**",
          s == 200 and len(rows_b) > 0 and b1 in b_ids and a2 not in b_ids
          and all(r["document_id"] in b_ids for r in rows_b),
          f"{len(rows_b)} 行；含 A2 的 {sum(1 for r in rows_b if r['document_id'] == a2)} 行；"
          f"课程 B 外的 {sum(1 for r in rows_b if r['document_id'] not in b_ids)} 行  "
          "← B1 与 A2 是逐字节同一份文件，所以只有作用域能把它们分开")

    # 能源那份只在课程 A 里。查课程 B 时它**不该**出现——
    # 这一条与上一条不同源：上一条防的是「同内容混进来」，这一条防的是
    # 「另一门课的另一份讲义混进来」。
    s, rows_e, _ = match(tok_a, course_b, v_energy, 30)
    check("作用域", "能源提问查课程 B → 召不回课程 A 的能源讲义",
          all(r["document_id"] != a1 for r in rows_e),
          f"{len(rows_e)} 行，含 A1 的 {sum(1 for r in rows_e if r['document_id'] == a1)} 行")

    # ============================================================ 3 · RLS
    print("\n\033[1m3 · RLS（跨用户）\033[0m")
    s, rows_x, raw = match(tok_b, course_a, v_planning, 30)
    check("RLS", "B 用 A 的 course_id 调 match_slices → 0 行",
          s == 200 and rows_x == [], f"HTTP {s} {len(rows_x)} 行 {raw[:120]}")
    check("RLS", "而 A 查得到（允许控制）", len(rows) > 0,
          f"{len(rows)} 行 ← 少了它，上一条在整表不可读时也绿")

    # ============================================================ 4 · 服务层
    if args.skip_explain:
        print("\n\033[2m…跳过 /explain 那一节（--skip-explain）\033[0m")
    else:
        print("\n\033[1m4 · 服务层 /explain（真检索 → 真生成）\033[0m")

        session = str(uuid.uuid4())
        s, out, raw = call("POST", f"{base}/explain", token=tok_a,
                           payload={"question": Q_PLANNING, "course_id": course_a,
                                    "session_id": session, "turn_index": 1})
        check("服务", "/explain 带 course_id → 200", s == 200 and isinstance(out, dict),
              f"HTTP {s} {raw[:200]}")

        # ---------------------------------------------------------------- 响应形状
        #
        # ⚠️ **这一条是新加的，而它本该从第一天就在。**
        #
        # 2026-09-12 Render 第一次部署后，这一节崩在 `KeyError: 'retrieval'`，
        # 而 `/explain` 的正文在讲**布隆过滤器**（阶段 3 的替身语料）。
        # 真实原因是**线上跑的是阶段 4 的旧构建**（本轮改动没提交），
        # 它的 `ExplainRequest` 没有 `course_id`，pydantic 默认 `extra="ignore"`
        # 把那个字段静默丢掉了，于是返回 200。
        #
        # **崩溃不是红。** 一个 KeyError 让脚本在断言之外退出，
        # 那一节的绿数既不是 0 也不是真实值，而「这一节没跑完」在合计行里看不出来。
        # 所以这里先把形状判成一条**会红的断言**，并且**点名最可能的原因**：
        # 形状不对 = 服务端比脚本旧，去看构建标识，不是去改代码。
        ctx_out = out.get("retrieval") if isinstance(out, dict) else None
        shape_ok = check(
            "服务", "响应里有 retrieval.meta（形状 = 这个构建认识 course_id）",
            isinstance(ctx_out, dict) and isinstance(ctx_out.get("meta"), dict),
            ("" if isinstance(ctx_out, dict) else
             f"\n      \033[33m→ 响应里没有 retrieval。**最可能是线上跑的构建比这份脚本旧**"
             f"（阶段 4 及以前的 /explain 没有这个字段）。"
             f"\n        `x-build-commit` 与 /health 的 build_commit 是 {DEPLOYED_COMMIT!r}；"
             f"核对它是不是你以为的那个提交（口径 F17）。"
             f"\n        ⚠️ 旧构建会**静默丢掉** course_id 并返回 200 + 合成课件——"
             f"这正是本条断言存在的理由。\033[0m"))

        if shape_ok:
            meta = ctx_out["meta"]
            check("服务", "检索后端是 **supabase**，不是替身",
                  meta.get("backend") == "supabase",
                  f"backend={meta.get('backend')}  "
                  "← 两条路必须在响应里分得开：「用合成课件答的」与"
                  "「用你的课件答的」是两件事")

            # ------------------------------------------------------ 来自哪门课
            #
            # ⚠️ **断的是 `document_id`，不是 `source_file`，也不是 HTTP 200。**
            #
            # 两处教训叠在这一条上：
            #
            #  ① 上一版只断了 200。那是「有没有报错」，不是「读的是哪个对象」——
            #     于是一个走替身、正文在讲布隆过滤器的响应照样绿（F17 的同一形态）。
            #
            #  ② 上一版的补救判据是「source_file 属于课程 A 的文件名集合」，
            #     **那个判据本身也是坏的**：`MIT11_201F07_slides2.pdf`
            #     **同时在课程 A 和课程 B 里**（ingest_real.py 特意这么传的，
            #     为的是让「跨课不召回」只能由作用域解释）。于是真的召回了
            #     课程 B 时，文件名集合一模一样，判据照样绿。
            #     **判据在它要区分的两种情况下取值相同，就不是判据**（同 F48）。
            #
            # `document_id` 是唯一能分开的东西，而它的另一端来自 ingest 时
            # 记下的 id（`.ingest_real_state.json`），不是服务端这一次自报的。
            hits = meta.get("hit_documents") or []
            docs_hit = {h.get("document_id") for h in hits}
            check("服务", "召回切片的 **document_id 全部属于请求的那门课**",
                  bool(docs_hit) and docs_hit <= {a1, a2},
                  f"命中 {sorted(x[:8] for x in docs_hit if x)} ⊆ "
                  f"课程 A {sorted(x[:8] for x in (a1, a2))}  "
                  "← 断的是 document_id：那两份讲义里有一份的**文件名**"
                  "同时出现在课程 B，按文件名判分不开")
            check("服务", "hit_documents 与 slices 逐项对齐",
                  len(hits) == len(ctx_out.get("slices") or []),
                  f"{len(hits)} vs {len(ctx_out.get('slices') or [])}  "
                  "← 对不上的话事件里的 document_id 会逐项配错，"
                  "而配错的身份比没有身份更糟")
            check("服务", "服务端报的「这门课有哪些文档」与摄入时记下的一致",
                  set(meta.get("course_document_ids") or []) == {a1, a2},
                  f"{sorted(x[:8] for x in (meta.get('course_document_ids') or []))}  "
                  "← 两端不同源：一端是服务端现查的，一端是 ingest 时落的 state 文件")

            observe("B=5 时这一次召回里出现了几份讲义",
                    f"{len(docs_hit)} 份（**观察项**：top-5 全来自一份讲义可能是"
                    "正确检索，「能不能同时出现」那条判据在 §1）")
            observe("段落长度 / 重生成",
                    f"{out['mean_paragraph_chars']:.1f} 字 · "
                    f"重生成 {out['regenerations']} 次 · 达标 {out['accepted']}")
            observe("正文开头（人眼核一下它讲的是不是这门课）",
                    repr(out["text"][:70]) + " …")

            # ------------------------------------------------------ 允许控制：换一门课
            #
            # **上下文必须跟着换。** 少了这一条，上面那条「document_id ⊆ 课程 A」
            # 在一个「永远返回课程 A」的实现上也绿（甚至在一个把 course_id
            # 整个忽略、恒用第一门课的实现上也绿）。
            s2, out2, raw2 = call("POST", f"{base}/explain", token=tok_a,
                                  payload={"question": Q_PLANNING, "course_id": course_b,
                                           "session_id": session, "turn_index": 2})
            m2 = ((out2 or {}).get("retrieval") or {}).get("meta") or {}
            hits2 = m2.get("hit_documents") or []
            docs_hit2 = {h.get("document_id") for h in hits2}
            check("允许", "**换成课程 B → 上下文换成 B 的切片**",
                  s2 == 200 and bool(docs_hit2) and docs_hit2 <= {b1},
                  f"命中 {sorted(x[:8] for x in docs_hit2 if x)} ⊆ 课程 B "
                  f"{[b1[:8]]}（HTTP {s2}）  ← 少了它，上面那条在一个"
                  "「把 course_id 整个忽略、恒用第一门课」的实现上也绿")
            check("允许", "两次的 document_id 集合**确实不同**",
                  bool(docs_hit) and bool(docs_hit2) and docs_hit != docs_hit2,
                  f"{sorted(x[:8] for x in docs_hit if x)} vs "
                  f"{sorted(x[:8] for x in docs_hit2 if x)}  "
                  "← 提问逐字相同、两份讲义内容逐字节相同，"
                  "所以这个差别只能来自作用域")
            # ------------------------------------------------------ 一条写错了的判据，改掉
            #
            # ⚠️ 上一版这里断的是「两次的 `context_signature` 必须不同」。**那条判据是错的**，
            # 它在真库上红了一次，而红的是判据本身不是实现：
            #
            #   `ContextPack.signature()` 自己的文档写着它是**内容指纹**，用途是
            #   「核验两组、各配置点拿到的是同一份上下文」（口径 X02）。
            #   而课程 B 的那份讲义与课程 A 的 A2 是**逐字节同一份文件** ——
            #   切片文本逐字相同、`slice_id` 相同、文件名相同。
            #   **内容真的一样，所以指纹一样是对的。**
            #
            # 记在这里而不是删掉，因为它正是「为什么 document_id 是必需的」那句话的证据：
            # 指纹分不开这两门课，`source_file` 也分不开（同名），
            # **只有 document_id 分得开** —— 上面那两条断的就是它。
            sig2 = ((out2 or {}).get("retrieval") or {}).get("signature")
            observe("两次的 context signature",
                    f"{ctx_out.get('signature')} vs {sig2}"
                    + ("  ← **相同，而这是对的**：两份讲义逐字节相同，内容指纹本就该相同。"
                       "所以分辨两门课只能靠 document_id（见上两条），不能靠指纹或文件名"
                       if ctx_out.get("signature") == sig2 else
                       "  ← 不同。若两份讲义仍是同一个文件，说明指纹的口径改了，"
                       "去核对 explain/context.py 的 signature()"))

            # ---- 埋点（阶段 6）。
            #
            # ⚠️ **这里为什么不去 select 事件表来数行数**：客户端对 `style_events`
            # 只有 insert 权限（读事件是分析行为，走 service_role），
            # 而本脚本刻意**不持有 service_role key** —— 用它跑的验收
            # 什么也证明不了（文件头那条拒绝运行的检查同源）。
            #
            # 服务端报的 `emitted` 是真凭据：那三条 insert 全部通过了真库上的
            # 三条 CHECK（payload 必需字段、arm = f1_assign_arm(user_id)、
            # RLS 的 with check）。任何一条不过都会是 23514/42501，
            # 于是 `failed > 0` 且 `failures` 里带错误码。
            evs = out.get("events") or {}
            n_expect = 3 + out["regenerations"]   # 三条固定 + 每次重生成一条
            # PGRST204 = PostgREST 的 schema cache 里没有这一列。
            # **它是一个很具体的原因，不该和「护栏拦下了一条坏事件」压成同一句话**：
            # 前者去 Supabase 的 SQL Editor 跑迁移，后者去改代码。
            hint = ""
            if "PGRST204" in str(evs.get("failures", "")):
                hint = ("\n      \033[33m→ PGRST204：真库上还没有 course_id / deck_id 这两列。"
                        "**迁移 20260912120000_f3_explain_events.sql 尚未在生产执行。**"
                        "\n        这不是代码问题，也不是护栏拦的 —— 去 Supabase 的 "
                        "SQL Editor 跑那份原文，然后重跑本脚本。"
                        "\n        ⚠️ 部署顺序：**迁移先于这个构建**。反了的话这一版的"
                        "四个事件会全部写不进去（而讲解本身照常返回）。\033[0m")
            check("埋点", f"事件真的落库（期望 {n_expect} 条）",
                  evs.get("emitted") == n_expect and evs.get("failed") == 0,
                  f"{evs}  ← emitted 意味着 insert 过了真库上的三条 CHECK："
                  f"payload 必需字段 / arm 不可伪造 / RLS 的 with check{hint}")
            check("埋点", "没有构造错误（缺必需字段会在构造时就抛）",
                  "build_error" not in evs, f"{evs.get('build_error', '无')}")

            # 允许控制：**不给 session_id 就一条都不发**，且给出点名的理由。
            # 少了它，「emitted == 3」在一个「无论如何都发」的实现上也绿，
            # 而那个实现会现编 session_id —— 把「平均会话轮次」静静改成假数。
            s3, out3, _ = call("POST", f"{base}/explain", token=tok_a,
                               payload={"question": Q_PLANNING, "course_id": course_a})
            e3 = (out3 or {}).get("events") or {}
            check("允许", "不给 session_id → 0 条事件 + 点名的理由",
                  s3 == 200 and e3.get("emitted") == 0 and "session_id" in
                  str(e3.get("skipped_reason", "")),
                  f"{e3}  ← 现编一个 session 会把「平均会话轮次」"
                  "（北极星指标）静静改成 1.0 附近")

        # 允许控制：不给 course_id 仍然走得通，且**后端不同**。
        s, out4, raw = call("POST", f"{base}/explain", token=tok_a,
                            payload={"question": Q_PLANNING})
        check("允许", "不给 course_id → 仍然 200，但后端是替身",
              s == 200 and isinstance(out4, dict)
              and ((out4.get("retrieval") or {}).get("meta") or {}).get("backend") == "in_memory",
              f"HTTP {s} backend="
              f"{((out4 or {}).get('retrieval', {}) or {}).get('meta', {}).get('backend')}  "
              "← 少了它，「backend==supabase」在两条路压根没分开时也可能绿")

        # 未知字段必须被拒。**这一条防的正是 2026-09-12 那次误读**：
        # 旧构建把 `course_id` 静默丢掉并返回 200 + 合成课件。
        s, _, raw = call("POST", f"{base}/explain", token=tok_a,
                         payload={"question": Q_PLANNING, "corse_id": "拼错的字段名"})
        check("服务", "未知请求字段 → 422 并点名，**不静默丢掉**",
              s == 422 and "corse_id" in raw,
              f"HTTP {s} {raw[:160]}  ← 200 说明这个构建在静默忽略它不认识的字段，"
              "那正是「服务端比客户端旧」表现成一次正常应答的机制")

        s, _, raw = call("POST", f"{base}/explain", token=tok_b,
                         payload={"question": Q_PLANNING, "course_id": course_a})
        check("RLS", "B 拿 A 的 course_id 调 /explain → 404",
              s == 404, f"HTTP {s} {raw[:160]}  "
              "← 不区分「不存在」与「是别人的」：区分了就泄露课程 id 的存在性")

        s, _, raw = call("POST", f"{base}/explain", token=tok_a,
                         payload={"question": Q_PLANNING, "course_id": str(uuid.uuid4())})
        check("服务", "不存在的 course_id → 404（与上一条同一句话）",
              s == 404, f"HTTP {s} {raw[:160]}")

    # ============================================================ 5 · 混合检索（2026-10-07）
    #
    # 迁移 20261007100100 的 `match_slices_indexed` 在本地验不了（要 pgvector），
    # 这里是它的三条真库观测；外加 `/retrieve` 证明四个环节**真的在线上跑**——
    # 「接上了」与「有用」是两句话，后者在 `api/scripts/eval_retrieval_prod.py`。
    print("\n\033[1m5 · 混合检索（match_slices_indexed + /retrieve）\033[0m")

    def match_indexed(token: str, course_id: str, vec: list[float], limit: int):
        s, rows, raw = rest("POST", "/rest/v1/rpc/match_slices_indexed", token, {
            "p_course_id": course_id, "p_query": vec, "p_model": MODEL_ID, "p_limit": limit})
        return s, (rows if isinstance(rows, list) else []), raw

    s, rows, raw = match_indexed(tok_a, course_a, v_energy, 30)
    check("作用域", "match_slices_indexed 对老口径文档照样召回",
          s == 200 and len(rows) > 0,
          f"HTTP {s} · {len(rows)} 行 {raw[:120] if s != 200 else ''} "
          "← ingest_real 那批文档是 2026-10-07 之前传的（index_ruleset 默认 embed-text-1.0）；"
          "0 行说明按文档口径取行那一步没对上，老课会整门召不回")
    keys = [(r["document_id"], r["slice_id"]) for r in rows]
    check("作用域", "同一片不重复出现（新旧两口径不混用）",
          len(keys) == len(set(keys)), f"{len(keys)} 行 / {len(set(keys))} 个不同的片")
    s, rows_b, _ = match_indexed(tok_b, course_a, v_energy, 30)
    check("RLS", "B 用 A 的 course_id 调 match_slices_indexed → 0 行",
          s == 200 and rows_b == [], f"HTTP {s} · {len(rows_b)} 行")

    if not args.skip_explain:
        s, out, raw = call("POST", f"{base}/courses/{course_a}/retrieve", token=tok_a,
                           payload={"question": Q_ENERGY})
        meta = (out or {}).get("retrieval", {}).get("meta", {}) if isinstance(out, dict) else {}
        check("服务", "/retrieve → 200 且 fusion = rrf", s == 200 and meta.get("fusion") == "rrf",
              f"HTTP {s} {raw[:160] if s != 200 else ''}")
        rw = (meta.get("rewrite") or {}).get("implementation")
        rr = (meta.get("rerank") or {}).get("implementation")
        check("服务", "查询改写在线上真的跑了（implementation = model）", rw == "model",
              f"rewrite = {rw!r} {meta.get('rewrite', {}).get('error') or ''} "
              "← none 说明线上没拿到检索助手（key？），error/invalid 看 error 字段")
        check("服务", "重排在线上真的跑了（implementation = model）", rr in ("model", "skipped"),
              f"rerank = {rr!r}")
        ch = meta.get("channels") or {}
        print(f"  \033[2m观察项（不计入绿数）：向量 {ch.get('vector')} 行 · 词法 {ch.get('lexical')} 行"
              f" · 词法查询词 {ch.get('lexical_query_terms')} 个 · 词法报错 {ch.get('lexical_error')!r}"
              f" · 检索模型成本 {meta.get('llm_cost_usd')} · 改写后的问题 "
              f"{(meta.get('rewrite') or {}).get('query')!r}\033[0m")
        print("  \033[2m老文档在升级索引（/courses/{id}/reindex）之前词法一路必然是 0 行——"
              "那是预期，不是故障。\033[0m")

    print(f"\n\033[1m合计\033[0m  绿 {PASSED} · 红 {FAILED}")
    print("\033[2m这批是真库观测，与 api/tests 的本地 pytest 数**不相加**（口径 F34）。"
          "观察项不计入绿数。\033[0m\n")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
