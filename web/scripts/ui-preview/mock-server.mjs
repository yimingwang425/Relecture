/**
 * UI 预览用的**假后端**：一个进程同时扮演 Supabase（auth + PostgREST）与产品服务端（api/）。
 *
 *   node scripts/ui-preview/mock-server.mjs          # 默认 :4010
 *
 * ## 它是做什么的，以及它**不是**什么
 *
 * 只为一件事：在没有生产凭据、连不上 Supabase / Render 的环境里（例如云端容器），
 * 把**登录后的每一页**真的渲染出来、截图、看动效与微交互——UI 改版的自检靠它。
 *
 * ⚠️ **它不验证任何业务行为**：RLS、CHECK、事件必需字段、检索、生成全都没有。
 * 在它上面「能用」不说明任何东西能用（口径 F34：本地机制与真库观测不互相顶替）。
 * 它的数据是写死的种子，讲解是一段写死的文本按字流出来。
 *
 * 不进 `verify:guards`，不进构建产物（`scripts/` 不被 Next 打包）。
 */

import { createServer } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.MOCK_PORT ?? 4010);
const USER_ID = "7a0d1f5e-2c4b-4e8a-9f31-6b2d8c1e4a90";
const EMAIL = "lin.xiao@example.com";

// ---------------------------------------------------------------- 种子数据

const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const DAY = 86400000;

const db = {
  style_config: [
    {
      user_id: USER_ID, detail_level: "P2", use_examples: "more", structure: "conclusion_first",
      depth: "why", f1_arm: "taste", onboarded_at: process.env.MOCK_ONBOARDED === "0" ? null : iso(9 * DAY),
      taste_tags: ["tone_plain", "stuck_example", "stuck_why"], tone: "spoken",
    },
  ],
  courses: [
    { id: "c-energy", owner: USER_ID, name: "22.081J 能源导论", created_at: iso(2 * DAY) },
    { id: "c-linalg", owner: USER_ID, name: "18.06 线性代数", created_at: iso(6 * DAY) },
    { id: "c-urban", owner: USER_ID, name: "11.201 城市规划史", created_at: iso(14 * DAY) },
    { id: "c-newbie", owner: USER_ID, name: "8.01 经典力学", created_at: iso(0.2 * DAY) },
  ],
  documents: [
    { id: "d1", course_id: "c-energy", filename: "MIT22_081JF10_lec01b.pdf", display_name: "week1 · 能源概览", page_count: 39, verdict: "usable", created_at: iso(2 * DAY) },
    { id: "d2", course_id: "c-energy", filename: "MIT22_081JF10_lec02.pdf", display_name: null, page_count: 27, verdict: "usable", created_at: iso(1 * DAY) },
    { id: "d3", course_id: "c-linalg", filename: "1806_lec07_eigen.pdf", display_name: "特征值", page_count: 18, verdict: "usable", created_at: iso(5 * DAY) },
    { id: "d4", course_id: "c-urban", filename: "MIT11_201F07_slides2.pdf", display_name: null, page_count: 15, verdict: "usable", created_at: iso(13 * DAY) },
  ],
  conversations: [
    { id: "v1", owner: USER_ID, course_id: "c-energy", title: "热机效率", updated_at: iso(3600e3) },
    { id: "v2", owner: USER_ID, course_id: "c-energy", title: "第一周 · 核燃料", updated_at: iso(DAY) },
    { id: "v3", owner: USER_ID, course_id: "c-energy", title: "风电 vs 太阳能", updated_at: iso(2 * DAY) },
  ],
  messages: [],
};

const ANSWER_1 = `**热机只能把一部分热变成功**，剩下的必然要排到环境里——这是第 20 页那条效率公式说的事。

卡诺效率写成 \`η = 1 − Tc/Th\`：Th 是锅炉里蒸汽的温度，Tc 是冷凝器那一侧的温度，**都要用绝对温度**。只要 Tc 不是 0 K，η 就小于 1。

拿课件里的数字估一下：蒸汽约 800 K、冷却水约 300 K，上限就是 1 − 300/800 ≈ 62%。真实电厂还有摩擦、散热，实际只有 35% 左右。

> 所以那张「废热」示意图不是工程没做好，是热力学第二定律给的天花板。`;

const retrievalOf = (slices) => ({
  signature: "8206f7c0d83316f3",
  slice_count: slices.length,
  context_chars: 1820,
  slices,
  skipped_pages_in_scope: [{ source_file: "MIT22_081JF10_lec01b.pdf", index: 33, kind: "image_only", reason: "内容全在图里" }],
  unusable_files: [],
  meta: { backend: "supabase", fusion: "rrf", course_id: "c-energy" },
});

const SLICES_1 = [
  { source_file: "MIT22_081JF10_lec01b.pdf", slice_id: "s020", pages: [20], title: "REAL HEAT ENGINES", chars: 412, score: 0.612, via: "hit" },
  { source_file: "MIT22_081JF10_lec01b.pdf", slice_id: "s021", pages: [21], title: "WASTE HEAT", chars: 288, score: 0.548, via: "hit" },
  { source_file: "MIT22_081JF10_lec02.pdf", slice_id: "s004", pages: [4], title: "Thermodynamic limits", chars: 351, score: null, via: "lexical" },
];

db.messages.push(
  { conversation_id: "v1", role: "user", content: "为什么发电厂没办法把热全部变成电？", turn_index: 1, round_id: null, meta: null, created_at: iso(4000e3) },
  {
    conversation_id: "v1", role: "assistant", content: ANSWER_1, turn_index: 1,
    round_id: "5f0c3a7e-8d1b-4c2a-9e6f-1a2b3c4d5e6f", created_at: iso(3990e3),
    meta: {
      retrieval: retrievalOf(SLICES_1),
      gap: { gap_verdict: "covered", gap_implementation: "model", gap_reason: null, gap_error: null, gap_skipped_hint_pages: [] },
      slice_keys: ["d1:s020", "d1:s021", "d2:s004"],
      confusion: { injected: false, note: null, marked_rounds: 0, matched_slice_keys: [] },
      accepted: true, regenerations: 0, detail_level: "P2", mean_paragraph_chars: 62.5,
    },
  },
);

const STREAM_ANSWER = `**不能直接用**：天然铀里能裂变的 U-235 只占 0.7%，要先浓缩到 4% 左右才能做燃料——课件第 26 页给的就是这两个数。

浓缩的意思是把 U-235 的比例提上去。它和 U-238 化学性质几乎一样，只能靠**质量差**把它们分开，所以要用离心机一级一级地筛。

打个比方：一袋米里混着 0.7% 的糯米，你要做的是把糯米挑到占 4%，而两种米长得几乎一模一样——只能靠重量的一点点差别慢慢分。

这一步耗能多、设备贵，也是核燃料成本的大头。`;

// ---------------------------------------------------------------- 工具

const cors = (req) => ({
  "Access-Control-Allow-Origin": req.headers.origin ?? "*",
  "Access-Control-Allow-Credentials": "true",
  "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": req.headers["access-control-request-headers"] ?? "*",
  "Access-Control-Expose-Headers": "x-build-commit, content-range",
});

function send(res, req, status, body, extra = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...cors(req), "x-build-commit": "preview0000000", ...extra });
  res.end(body === undefined ? "" : JSON.stringify(body));
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks);
  const type = req.headers["content-type"] ?? "";
  if (type.includes("application/json")) {
    try { return JSON.parse(raw.toString("utf8")); } catch { return {}; }
  }
  return raw;
}

/** PostgREST 过滤：只认 `col=eq.v` 与 `col=in.(a,b)`，够预览用。 */
function filterRows(rows, params) {
  return rows.filter((r) => {
    for (const [k, v] of params) {
      if (["select", "order", "limit", "offset", "on_conflict", "columns"].includes(k)) continue;
      if (v.startsWith("eq.") && String(r[k]) !== v.slice(3)) return false;
      if (v.startsWith("in.")) {
        const set = v.slice(4, -1).split(",");
        if (!set.includes(String(r[k]))) return false;
      }
    }
    return true;
  });
}

function orderRows(rows, params) {
  const o = params.get("order");
  if (!o) return rows;
  const [col, dir] = o.split(".");
  return [...rows].sort((a, b) => (a[col] < b[col] ? -1 : 1) * (dir === "desc" ? -1 : 1));
}

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj)).toString("base64url");
}

export const ACCESS_TOKEN = `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({
  sub: USER_ID, email: EMAIL, role: "authenticated", aud: "authenticated",
  exp: Math.floor(Date.now() / 1000) + 30 * 86400, iat: Math.floor(Date.now() / 1000),
})}.c2ln`;

const USER = {
  id: USER_ID, aud: "authenticated", role: "authenticated", email: EMAIL,
  email_confirmed_at: iso(30 * DAY), app_metadata: { provider: "email" }, user_metadata: {},
  created_at: iso(30 * DAY), updated_at: iso(DAY),
};

// ---------------------------------------------------------------- 产品服务端

async function sseExplain(req, res, body) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", ...cors(req), "x-build-commit": "preview0000000" });
  const ev = (type, data) => res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const slices = [
    { source_file: "MIT22_081JF10_lec01b.pdf", slice_id: "s026", pages: [26], title: "NUCLEAR FUEL", chars: 380, score: 0.641, via: "hit" },
    { source_file: "MIT22_081JF10_lec01b.pdf", slice_id: "s027", pages: [27], title: "ENRICHMENT", chars: 301, score: 0.553, via: "hit" },
  ];
  const roundId = randomUUID();
  ev("stage", { stage: "retrieving" });
  await sleep(Number(process.env.MOCK_RETRIEVE_MS ?? 900));
  ev("retrieval", { round_id: roundId, slice_keys: ["d1:s026", "d1:s027"], retrieval: retrievalOf(slices), confusion: { injected: false } });
  ev("stage", { stage: "generating" });
  await sleep(Number(process.env.MOCK_FIRST_TOKEN_MS ?? 1100));
  ev("gap", { gap_verdict: "covered", gap_implementation: "model" });
  const text = STREAM_ANSWER;
  const step = 3;
  for (let i = 0; i < text.length; i += step) {
    ev("delta", { text: text.slice(i, i + step) });
    await sleep(Number(process.env.MOCK_CHAR_MS ?? 28));
  }
  const conv = body.conversation_id;
  const turnIndex = db.messages.filter((m) => m.conversation_id === conv && m.role === "user").length + 1;
  // MOCK_PERSIST_FAIL=1：模拟服务端落库失败（e2e 的反向控制用）。讲解照常回来，库里不写。
  const persistFail = process.env.MOCK_PERSIST_FAIL === "1";
  if (conv && !persistFail) {
    db.messages.push(
      { conversation_id: conv, role: "user", content: body.question, turn_index: turnIndex, round_id: null, meta: null, created_at: new Date().toISOString() },
      {
        conversation_id: conv, role: "assistant", content: text, turn_index: turnIndex, round_id: roundId, created_at: new Date().toISOString(),
        // 与服务端落库的 meta 同形：少了 accepted 的话，会话重读时这一轮会显示成「重生成触顶仍未达标」。
        meta: {
          retrieval: retrievalOf(slices), slice_keys: ["d1:s026", "d1:s027"],
          gap: { gap_verdict: "covered", gap_implementation: "model", gap_reason: null, gap_error: null, gap_skipped_hint_pages: [] },
          confusion: { injected: false, note: null, marked_rounds: 0, matched_slice_keys: [] },
          accepted: true, regenerations: 0, detail_level: "P2", mean_paragraph_chars: 58.2,
        },
      },
    );
  }
  ev("done", {
    response: {
      events: { emitted: 3, attempted: 3, failed: 0 }, round_id: roundId, slice_keys: ["d1:s026", "d1:s027"],
      conversation_id: conv ?? null, turn_index: turnIndex,
      persisted: !conv ? { written: 0, attempted: 0 } : persistFail
        ? { written: 0, attempted: 2, error: "SupabaseError: PGRST102 All object keys must match（mock）" }
        : { written: 2, attempted: 2 },
      confusion: { injected: false, note: null, marked_rounds: 0, matched_slice_keys: [] },
      gap: { gap_verdict: "covered", gap_implementation: "model", gap_reason: null, gap_error: null, gap_skipped_hint_pages: [] },
      text, accepted: true, regenerations: 0,
      latency: { first_token_ms: 2010.4, critical_path_ms: 5200.1, wall_ms: 5400.3, speculative_discarded: 0 },
      course_id: body.course_id ?? null, retrieval: retrievalOf(slices), detail_level: "P2", tone: "spoken",
      config_fingerprint: "fp", paragraph_length_target: {}, mean_paragraph_chars: 58.2, all_activated_passed: true, style_rules: "",
    },
  });
  res.end();
}

async function api(req, res, path, body) {
  if (path === "/health") return send(res, req, 200, { status: "ok", model_key_configured: true, build_commit: "preview0000000" });
  if (path === "/courses" && req.method === "GET") return send(res, req, 200, db.courses);
  if (path === "/courses" && req.method === "POST") {
    const row = { id: `c-${randomUUID().slice(0, 6)}`, owner: USER_ID, name: body.name, created_at: new Date().toISOString() };
    db.courses.unshift(row);
    return send(res, req, 201, row);
  }
  const up = /^\/courses\/([^/]+)\/documents$/.exec(path);
  if (up && req.method === "POST") {
    await new Promise((r) => setTimeout(r, Number(process.env.MOCK_UPLOAD_MS ?? 2200)));
    const id = `d-${randomUUID().slice(0, 6)}`;
    db.documents.unshift({ id, course_id: up[1], filename: "uploaded.pdf", display_name: null, page_count: 21, verdict: "usable", created_at: new Date().toISOString() });
    return send(res, req, 201, {
      document_id: id, slice_count: 21, embedded_count: 21, skipped_pages: 2, restricted_pages_excluded: null,
      verdict: "usable", vision_page_count: 2, vision_skipped_over_cap: 0, contextualized_count: 21,
      events: { emitted: 1, attempted: 1, failed: 0 },
    });
  }
  if (path === "/explain/stream") return sseExplain(req, res, body);
  if (path === "/marks") return send(res, req, 200, { round_id: body.round_id, created: true, mark_count_after: 1, slice_count: body.slice_ids?.length ?? 0, events: { emitted: 1 } });
  return send(res, req, 404, { detail: `mock api 不认识 ${req.method} ${path}` });
}

// ---------------------------------------------------------------- Supabase

async function supabase(req, res, url, body) {
  const p = url.pathname;
  if (p === "/auth/v1/user") return send(res, req, 200, USER);
  if (p === "/auth/v1/token") {
    return send(res, req, 200, {
      access_token: ACCESS_TOKEN, token_type: "bearer", expires_in: 3600 * 24 * 30,
      expires_at: Math.floor(Date.now() / 1000) + 3600 * 24 * 30, refresh_token: "preview-refresh", user: USER,
    });
  }
  if (p === "/auth/v1/logout") return send(res, req, 204);

  const m = /^\/rest\/v1\/([a-z_]+)$/.exec(p);
  if (!m) return send(res, req, 404, { message: `mock 不认识 ${p}` });
  const table = m[1];
  db[table] ??= [];
  const params = url.searchParams;
  const wantsObject = (req.headers.accept ?? "").includes("vnd.pgrst.object");
  const respond = (rows) => {
    if (wantsObject) {
      if (rows.length !== 1) return send(res, req, 406, { code: "PGRST116", message: `${rows.length} rows` });
      return send(res, req, 200, rows[0]);
    }
    return send(res, req, 200, rows);
  };

  if (req.method === "GET") return respond(orderRows(filterRows(db[table], params), params));
  if (req.method === "POST") {
    const rows = (Array.isArray(body) ? body : [body]).map((r) => ({
      id: randomUUID(), created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...r,
    }));
    db[table].push(...rows);
    if ((req.headers.prefer ?? "").includes("return=representation")) return respond(rows);
    return send(res, req, 201);
  }
  if (req.method === "PATCH") {
    const hit = filterRows(db[table], params);
    for (const r of hit) Object.assign(r, body);
    if ((req.headers.prefer ?? "").includes("return=representation")) return respond(hit);
    return send(res, req, 204);
  }
  if (req.method === "DELETE") {
    const hit = new Set(filterRows(db[table], params));
    db[table] = db[table].filter((r) => !hit.has(r));
    return send(res, req, 204);
  }
  return send(res, req, 405, {});
}

createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, cors(req));
    return res.end();
  }
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const body = req.method === "GET" ? null : await readBody(req);
  try {
    if (url.pathname.startsWith("/api/")) return await api(req, res, url.pathname.slice(4), body ?? {});
    return await supabase(req, res, url, body ?? {});
  } catch (e) {
    send(res, req, 500, { message: String(e) });
  }
}).listen(PORT, () => console.log(`mock backend on :${PORT}（Supabase + api/，只供 UI 预览）`));
