/**
 * 四条风格轴的**取值域一致性**护栏。
 *
 *   npm run verify:axes
 *
 * ## 它防的是什么
 *
 * 阶段 3 之后，同一份取值域存在于三处，而且不可能合并成一处：
 *
 * | 出处 | 为什么非有不可 |
 * | --- | --- |
 * | `f1_samples/style_axes.py` 的 `AXES` | 生成/核对样例的脚本读它 |
 * | `supabase/migrations/*.sql` 的 CHECK | 数据库自己执行，不信任应用层 |
 * | `web/lib/style/axes.ts` 的 `AXES` | 浏览器跑不了 Python，也读不到 CHECK |
 *
 * 三份靠人记得同时改是不行的——**漂了不会报错**：前端拒绝的取值数据库照收，
 * 或者反过来，用户点一下按钮拿到一个 `23514`。这条护栏把三份解析出来逐项比对。
 *
 * ## 判据自身先验一遍分辨力（F16 的形态）
 *
 * 「三份相等」这个结论只有在**三份都真的解析出东西**时才有意义——
 * 解析失败得到三个空对象，比对同样会绿。所以每个解析器都断言了非空条数，
 * 且 `--selftest` 会把每份数据故意改坏一次，要求比对**必须变红**。
 * 一个永远绿的检查不是检查。
 */

import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ROOT = dirname(WEB);

let passed = 0;
let failed = 0;

function check(cond, name, detail = "") {
  if (cond) passed++;
  else failed++;
  const mark = cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m";
  console.log(`  ${mark} ${name}${detail ? "  " + detail : ""}`);
}

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sorted = (xs) => [...xs].sort();

// ---------------------------------------------------------------- 解析

/** 取 `startRe` 匹配处之后第一个 `{`（或 `(`）到其配对闭括号之间的文本。 */
function blockAfter(src, startRe, open = "{", close = "}") {
  const m = startRe.exec(src);
  if (!m) return null;
  const from = src.indexOf(open, m.index + m[0].length - 1);
  if (from < 0) return null;
  let depth = 0;
  for (let i = from; i < src.length; i++) {
    if (src[i] === open) depth++;
    else if (src[i] === close && --depth === 0) return src.slice(from + 1, i);
  }
  return null;
}

const STRINGS = /["']([A-Za-z0-9_]+)["']/g;

/** `key: [...]` / `"key": (...)` → { key: [值...] }，顺序保留。 */
function parseListMap(block, entryRe) {
  const out = {};
  let m;
  while ((m = entryRe.exec(block)) !== null) {
    out[m[1]] = [...m[2].matchAll(STRINGS)].map((s) => s[1]);
  }
  return out;
}

function parseScalarMap(block, entryRe) {
  const out = {};
  let m;
  while ((m = entryRe.exec(block)) !== null) out[m[1]] = m[2];
  return out;
}

// -------- Python：f1_samples/style_axes.py

function readPython() {
  const src = readFileSync(join(ROOT, "f1_samples", "style_axes.py"), "utf8");

  const axes = parseListMap(
    blockAfter(src, /^AXES\s*:/m) ?? "",
    /["'](\w+)["']\s*:\s*\(([^)]*)\)/g,
  );
  const defaults = parseScalarMap(
    blockAfter(src, /^DEFAULTS\s*:/m) ?? "",
    /["'](\w+)["']\s*:\s*["'](\w+)["']/g,
  );
  const enforced = parseScalarMap(
    blockAfter(src, /^ENFORCED\s*:/m) ?? "",
    /["'](\w+)["']\s*:\s*(True|False)/g,
  );
  const ladderBlock = blockAfter(src, /^DETAIL_LADDER\s*:/m, "(", ")") ?? "";
  const detailLadder = [...ladderBlock.matchAll(STRINGS)].map((m) => m[1]);

  const pairsBlock = blockAfter(src, /^PAIRS\s*:/m, "(", ")") ?? "";
  const pairs = [...pairsBlock.matchAll(/\(\s*["'](\w+)["']\s*,\s*["'](\w+)["']\s*,\s*["'](\w+)["']\s*,\s*["'](\w+)["']\s*\)/g)]
    .map((m) => ({ id: m[1], axis: m[2], a: m[3], b: m[4] }));

  // B 类措辞的版本号与摘要（阶段 4，口径 F39）。摘要拿**块正文**算，见 §四。
  const promptVersion = /^PROMPT_TEMPLATE_VERSION\s*:\s*str\s*=\s*["']([^"']+)["']/m.exec(src)?.[1] ?? null;
  const promptDigest = /^PROMPT_TEMPLATE_DIGEST\s*:\s*str\s*=\s*["']([0-9a-f]+)["']/m.exec(src)?.[1] ?? null;
  const fragmentsBlock = blockAfter(src, /^B_CLASS_FRAGMENTS\s*:/m) ?? "";

  return { axes, defaults, enforced, detailLadder, pairs, promptVersion, promptDigest, fragmentsBlock };
}

/**
 * B 类措辞的摘要。**压掉空白后哈希块正文**——缩进与换行的变化不该逼人改版本号，
 * 措辞本身的变化必须。
 *
 * 纯函数，好让下面的反向控制直接喂一段改过的文本。
 */
function fragmentsDigest(block) {
  return createHash("sha256").update(block.replace(/\s+/g, " ").trim(), "utf8").digest("hex").slice(0, 12);
}

// -------- SQL：supabase/migrations/*.sql，按时间戳取每列**最后一次**定义

function readSql() {
  const dir = join(ROOT, "supabase", "migrations");
  const files = readdirSync(dir).filter((f) => f.endsWith(".sql")).sort();
  const checks = {};
  const seenIn = {};

  for (const f of files) {
    const src = readFileSync(join(dir, f), "utf8");
    // `check (<col> in ('a', 'b'))`——注释里的示例不会误命中，因为要求整条形态齐全。
    for (const m of src.matchAll(/check\s*\(\s*(\w+)\s+in\s*\(([^)]*)\)\s*\)/gi)) {
      const col = m[1];
      const values = [...m[2].matchAll(/'(\w+)'/g)].map((v) => v[1]);
      checks[col] = values;
      seenIn[col] = f;
    }
  }
  return { checks, seenIn, files };
}

// -------- TS：web/lib/style/axes.ts + flow.ts

function readTs(overrideAxesSrc) {
  const src = overrideAxesSrc ?? readFileSync(join(WEB, "lib", "style", "axes.ts"), "utf8");

  const axes = parseListMap(
    blockAfter(src, /export const AXES\s*=/) ?? "",
    /(\w+)\s*:\s*\[([^\]]*)\]/g,
  );
  const defaults = parseScalarMap(
    blockAfter(src, /export const DEFAULTS\s*=/) ?? "",
    /(\w+)\s*:\s*["'](\w+)["']/g,
  );
  const enforced = parseScalarMap(
    blockAfter(src, /export const ENFORCED\s*=/) ?? "",
    /(\w+)\s*:\s*(true|false)/g,
  );

  const flowSrc = readFileSync(join(WEB, "lib", "style", "flow.ts"), "utf8");
  const reachableBlock = blockAfter(flowSrc, /REACHABLE_PAIR_IDS[^=]*=/, "[", "]") ?? "";
  const reachable = [...reachableBlock.matchAll(STRINGS)].map((m) => m[1]);

  const promptVersion = /export const PROMPT_TEMPLATE_VERSION\s*=\s*["']([^"']+)["']/.exec(src)?.[1] ?? null;

  return { axes, defaults, enforced, reachable, promptVersion };
}

// -------- pairs.json

function readPairsJson() {
  const raw = JSON.parse(readFileSync(join(ROOT, "f1_samples", "pairs.json"), "utf8"));
  return raw.pairs.map((p) => ({
    id: p.pair_id,
    axis: p.target_dimension,
    labels: p.samples.map((s) => s.variant_label),
  }));
}

// ---------------------------------------------------------------- 比对

/** 返回不一致的说明数组。空数组 = 三处一致。**纯函数**，好让 selftest 直接喂坏数据。 */
function compare(py, sql, ts, pairsJson) {
  const problems = [];
  const axisIds = Object.keys(py.axes);

  for (const axis of axisIds) {
    if (!eq(py.axes[axis], ts.axes[axis])) {
      problems.push(`AXES.${axis}：Python ${JSON.stringify(py.axes[axis])} ≠ TS ${JSON.stringify(ts.axes[axis])}`);
    }
    // SQL 的 `in (...)` 是集合，顺序无意义，所以只比集合。
    const sqlValues = sql.checks[axis];
    if (!sqlValues) problems.push(`CHECK 里找不到列 ${axis}`);
    else if (!eq(sorted(sqlValues), sorted(py.axes[axis]))) {
      problems.push(`CHECK ${axis}：SQL ${JSON.stringify(sorted(sqlValues))} ≠ Python ${JSON.stringify(sorted(py.axes[axis]))}`);
    }
    if (py.defaults[axis] !== ts.defaults[axis]) {
      problems.push(`DEFAULTS.${axis}：Python ${py.defaults[axis]} ≠ TS ${ts.defaults[axis]}`);
    }
    const pyEnforced = py.enforced[axis] === "True";
    const tsEnforced = ts.enforced[axis] === "true";
    if (pyEnforced !== tsEnforced) {
      problems.push(`ENFORCED.${axis}：Python ${pyEnforced} ≠ TS ${tsEnforced}`);
    }
  }

  // 详细程度阶梯：顺序有意义（F05 沿它移动一格），所以按序比。
  if (!eq(py.detailLadder, ts.axes.detail_level)) {
    problems.push(
      `DETAIL_LADDER：Python ${JSON.stringify(py.detailLadder)} ≠ TS AXES.detail_level ${JSON.stringify(ts.axes.detail_level)}`,
    );
  }

  // 比较树能走到的对，必须与 style_axes.PAIRS、pairs.json 三方一致。
  const pyIds = sorted(py.pairs.map((p) => p.id));
  const jsonIds = sorted(pairsJson.map((p) => p.id));
  const tsIds = sorted(ts.reachable);
  if (!eq(pyIds, tsIds)) problems.push(`pair_id：Python ${JSON.stringify(pyIds)} ≠ flow.ts ${JSON.stringify(tsIds)}`);
  if (!eq(pyIds, jsonIds)) problems.push(`pair_id：Python ${JSON.stringify(pyIds)} ≠ pairs.json ${JSON.stringify(jsonIds)}`);

  // B 类措辞版本（阶段 4）。它是 prompt_profile_id 的构成字段之一（口径 F39），
  // 事件里存的是 TS 那一份，算 id 的是 Python 那一份——两份不等于同一个版本号时，
  // 同一个 id 会盖住两份不同的 prompt，而**没有任何东西会报错**。
  if (py.promptVersion !== ts.promptVersion) {
    problems.push(
      `PROMPT_TEMPLATE_VERSION：Python ${JSON.stringify(py.promptVersion)} ≠ TS ${JSON.stringify(ts.promptVersion)}`,
    );
  }

  // 每个样例的档位必须在该轴的取值域里——P1 那种已退出的取值混进样例，这条会红。
  for (const p of pairsJson) {
    for (const label of p.labels) {
      if (!py.axes[p.axis]?.includes(label)) {
        problems.push(`pairs.json ${p.id}：档位 ${label} 不在 ${p.axis} 的取值域 ${JSON.stringify(py.axes[p.axis])} 里`);
      }
    }
  }

  return problems;
}

// ---------------------------------------------------------------- 跑

const py = readPython();
const sql = readSql();
const ts = readTs();
const pairsJson = readPairsJson();

console.log("\n\x1b[1m一 · 判据自身的分辨力（先验，再读结论）\x1b[0m");
{
  // 解析失败会得到空对象，而空对象之间的比对必然相等——那个「相等」什么也不说明。
  check(Object.keys(py.axes).length === 4, "style_axes.py 解析出 4 条轴", `实得 ${Object.keys(py.axes).length}`);
  check(py.pairs.length === 8, "style_axes.py 解析出 8 对", `实得 ${py.pairs.length}`);
  check(py.detailLadder.length > 0, "style_axes.py 解析出 DETAIL_LADDER", py.detailLadder.join(" → "));
  check(Object.keys(ts.axes).length === 4, "axes.ts 解析出 4 条轴", `实得 ${Object.keys(ts.axes).length}`);
  check(ts.reachable.length === 8, "flow.ts 解析出 8 个 pair_id", `实得 ${ts.reachable.length}`);
  check(Object.keys(sql.checks).length >= 4, "迁移里解析出 ≥4 条 CHECK", `实得 ${Object.keys(sql.checks).length}`);
  check(sql.files.length > 0, "扫到迁移文件", `${sql.files.length} 个：${sql.files.join(", ")}`);
  check(pairsJson.length === 8, "pairs.json 解析出 8 对", `实得 ${pairsJson.length}`);
  check(py.promptVersion !== null, "style_axes.py 解析出 PROMPT_TEMPLATE_VERSION", String(py.promptVersion));
  check(ts.promptVersion !== null, "axes.ts 解析出 PROMPT_TEMPLATE_VERSION", String(ts.promptVersion));
  check(py.promptDigest !== null, "style_axes.py 解析出 PROMPT_TEMPLATE_DIGEST", String(py.promptDigest));
  check(py.fragmentsBlock.length > 200, "解析出 B_CLASS_FRAGMENTS 块正文", `${py.fragmentsBlock.length} 字符`);

  // 正向控制：把 TS 的取值域改坏，比对**必须**报出问题。否则这条护栏是空转的。
  const broken = readTs(
    readFileSync(join(WEB, "lib", "style", "axes.ts"), "utf8").replace(
      /detail_level:\s*\["P0",\s*"P2",\s*"P3"\]/,
      'detail_level: ["P0", "P1", "P2", "P3"]',
    ),
  );
  const detected = compare(py, sql, broken, pairsJson);
  check(
    detected.length > 0,
    "反向控制：TS 里混进 P1 时比对变红",
    detected.length > 0 ? `报出 ${detected.length} 条` : "没报出问题 —— 护栏是空转的",
  );
}

console.log("\n\x1b[1m二 · 三处取值域一致\x1b[0m");
{
  console.log(
    `  \x1b[2mdetail_level 的 CHECK 最后定义于 ${sql.seenIn.detail_level ?? "（找不到）"}\x1b[0m`,
  );
  const problems = compare(py, sql, ts, pairsJson);
  check(problems.length === 0, "Python / SQL / TS / pairs.json 四方一致", problems.length ? "" : "");
  for (const p of problems) console.log(`      \x1b[31m·\x1b[0m ${p}`);
}

console.log("\n\x1b[1m三 · P1 确已退出\x1b[0m");
{
  check(!py.axes.detail_level.includes("P1"), "style_axes.py 的 detail_level 不含 P1");
  check(!ts.axes.detail_level.includes("P1"), "axes.ts 的 detail_level 不含 P1");
  check(!sql.checks.detail_level?.includes("P1"), "最新 CHECK 不含 P1", `实为 ${JSON.stringify(sql.checks.detail_level)}`);
  check(
    !pairsJson.some((p) => p.labels.includes("P1")),
    "pairs.json 里没有 P1 样例",
  );
}

console.log("\n\x1b[1m四 · B 类措辞版本与摘要（口径 F39，阶段 4）\x1b[0m");
{
  // `prompt_profile_id` = f(B 类三条轴取值, PROMPT_TEMPLATE_VERSION)。
  // 版本号本身在两处（Python 算 id、TS 写事件），上面 §二 已比过。
  //
  // 这里补的是另一半，也是更容易漏的一半：**措辞改了但版本号没改**。
  // 那种情况下两处版本号仍然相等，§二 全绿，而同一个 prompt_profile_id
  // 会盖住两份不同的 prompt —— F04 要这个标识回答的问题（「讲解质量变了，
  // 是阈值变了还是 prompt 变了」）当场失效，且不会报错。
  const actual = fragmentsDigest(py.fragmentsBlock);
  check(
    actual === py.promptDigest,
    "B_CLASS_FRAGMENTS 的摘要与 PROMPT_TEMPLATE_DIGEST 相符",
    actual === py.promptDigest
      ? `${actual}  ← 措辞未变`
      : `实算 ${actual} ≠ 记录 ${py.promptDigest}  ← 措辞改了，把 PROMPT_TEMPLATE_VERSION（含 axes.ts 那份）与本摘要一起改`,
  );

  // 判据自身的分辨力：改一个字，摘要必须变。
  // 少了这条，一个恒返回同一个字符串的 fragmentsDigest 也能让上一条永远绿。
  const tampered = fragmentsDigest(py.fragmentsBlock.replace("多举例子", "多举些例子"));
  check(
    tampered !== actual,
    "反向控制：措辞改一个词，摘要就变",
    tampered !== actual ? `${actual} → ${tampered}` : "摘要没变 —— 这条护栏是空转的",
  );

  // 空白不该触发。缩进/换行变一下就逼人改版本号的话，这条护栏会被习惯性忽略。
  const respaced = fragmentsDigest(py.fragmentsBlock.replace(/\n/g, "\n  "));
  check(respaced === actual, "缩进与换行的变化不影响摘要", "← 只对措辞敏感");
}

console.log(
  `\n${failed === 0 ? "\x1b[32m" : "\x1b[31m"}${passed} 绿 / ${failed} 红\x1b[0m\n`,
);
process.exit(failed === 0 ? 0 : 1);
