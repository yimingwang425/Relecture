/**
 * 反馈映射（F05 / F06 / F07）与比较树的断言。
 *
 *   npm run verify:logic
 *
 * ## 为什么这三条口径值得单独测
 *
 * 它们都是「**没做某件事**」型的语义，而没做某件事在页面上看不出来：
 *
 * - **F05** 要求详细程度沿 `P0 → P2 → P3` 移动，**跳过 P1**。少一格和多一格
 *   在界面上都只是「档位变了」，只有断言能分辨。
 * - **F06** 要求「例子没懂」**不改配置**、且**不计入**「配置修改频率」的分子。
 *   接错了的表现是 §3.2 的指标因为按钮没接通而达标——一个自己会变绿的指标。
 * - **F07** 要求端点**不静默吞掉**：配置不变，但必须有一句说明。
 *   吞掉与正常处理在库里留下的痕迹完全相同（都是没有变更）。
 *
 * ## 怎么跑 TS
 *
 * 三个模块是纯 TS（无 JSX、无运行时依赖），用项目已有的 `tsc` 编成 CommonJS
 * 到临时目录再 require。刻意不引入 `tsx` / `ts-node` 之类的新依赖——
 * 为一个测试脚本增加一条供应链不划算。
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));

let passed = 0;
let failed = 0;

function check(cond, name, detail = "") {
  if (cond) passed++;
  else failed++;
  const mark = cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m";
  console.log(`  ${mark} ${name}${detail ? "  " + detail : ""}`);
}

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sorted_ = (xs) => [...xs].sort();

/** 递归列出所有带 "use client" 的组件源码。 */
function listClientComponents(dir) {
  const out = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...listClientComponents(full));
    else if (/\.(tsx|ts)$/.test(e.name)) {
      const src = readFileSync(full, "utf8");
      if (/^\s*["']use client["']/m.test(src)) out.push({ path: full, src });
    }
  }
  return out;
}

/**
 * 从 `lib/style/` 里推导出「会产生随机的导出函数」名单。
 *
 * **不写死名单**：第一版判据只查字面的 `Math.random(`，而真实的 bug 是
 * `useState(() => makeFlips(...))` —— 随机藏在一层函数调用后面，
 * 客户端源码里根本没有 `Math.random` 这几个字，判据当场空转
 * （这一点是被它自己的反向控制抓出来的，不是想出来的）。
 *
 * 所以改成推导：谁的函数体里有 `Math.random`，谁就是随机源。
 * 以后在 lib/style 里新加一个随机 helper，它会**自动**成为客户端组件的违禁标识符，
 * 不需要有人记得回来改名单。
 */
/**
 * 渲染期取值两侧会分叉的**原始**随机源。名单只有这两个，其余都是从它们推导出来的。
 *
 * `crypto.randomUUID` 是阶段 4 加的：`session_id` 用它生成，而它与 `Math.random`
 * 是**同一类危险**——客户端组件的渲染在 SSR 与 hydrate 各跑一次，两次取到不同的
 * uuid。左右位置分叉肉眼可见（#418 报错），`session_id` 分叉**看不见**：
 * 它只是让同一次 onboarding 的 6 步事件挂在两个 session 上，而弃流点分析正是
 * 按 session 分组的。看不见的那个更需要护栏。
 */
const PRIMITIVE_RANDOM_SOURCES = ["Math.random", "crypto.randomUUID"];

function randomSourceNames(libDir) {
  const names = new Set(PRIMITIVE_RANDOM_SOURCES);
  for (const f of readdirSync(libDir)) {
    if (!/\.tsx?$/.test(f)) continue;
    // **先剥注释再分析**：注释写在下一个 export 之前，不剥的话它会落进上一块，
    // 把文档里提到的 `Math.random()` 算成上一个函数的随机性（实测踩到，
    // configFromPicks 因此被误判成随机源）。
    const src = stripComments(readFileSync(join(libDir, f), "utf8"));
    // `export async function` 也要能切开。原来只切 `export function`，
    // 于是一个 async 导出的函数体会被算进**上一个**同步导出函数的身体里——
    // 那会同时造成误判（上一个被算成随机源）和漏判（async 那个永远进不了名单）。
    const chunks = src.split(/export\s+(?:async\s+)?function\s+/).slice(1);
    for (const chunk of chunks) {
      const name = chunk.match(/^(\w+)/)?.[1];
      if (!name) continue;
      const body = chunk.split(/\nexport\s/)[0];
      // 不要求后面跟 `(`：`rng = Math.random` 这种**把随机函数当值传**的写法
      // 同样是随机源，而它后面跟的是 `)`（实测：makeFlips 因此被漏判）。
      const re = new RegExp(PRIMITIVE_RANDOM_SOURCES.map((n) => n.replace(".", "\\.")).join("|") + "\\b");
      if (re.test(body)) names.add(name);
    }
  }
  return [...names];
}

/** 剥掉块注释与行注释。判据只该看代码，不该看文档里举的例子。 */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

/**
 * 客户端组件里的随机源。**纯函数**，好让反向控制直接喂修复前的源码。
 *
 * 查的是**引用**而不是字面量：`makeFlips` 出现在客户端组件里就算，
 * 哪怕它自己不含 `Math.random`——随机发生在哪一侧才是判据关心的事。
 * 同样先剥注释：解释「为什么不能在这里随机」的那段注释本身提到了函数名，
 * 不剥就会把注释算成违规（判据把自己的文档判红）。
 *
 * ⚠️ 覆盖不到的：渲染期用 `Date.now()` / `new Date()` 是同一类问题
 * （SSR 与 hydrate 两次取值不同），但本项目里这两个只出现在事件处理里
 * （写 `onboarded_at`），拦了会误伤，所以没拦。**如实登记，不写成已覆盖。**
 */
function clientRandomnessProblems(files, sources) {
  const problems = [];
  for (const { path, src } of files) {
    const code = stripComments(src);
    for (const name of sources) {
      const re = new RegExp("(^|[^\\w.])" + name.replace(".", "\\.") + "\\s*\\(", "m");
      if (re.test(code)) {
        problems.push(`${path} 引用了随机源 ${name} —— SSR 与 hydrate 会各跑一次`);
      }
    }
  }
  return problems;
}

// ---------------------------------------------------------------- 编译

// 编译产物落在 `node_modules/` 下（已被 git 忽略），**不落系统临时目录**：
// 编出来的 markdown.js 要 require `react/jsx-runtime`，而 /tmp 下解析不到 node_modules。
const out = mkdtempSync(join(WEB, "node_modules", ".f1-style-logic-"));
try {
  execFileSync(
    "npx",
    [
      "tsc",
      "lib/style/axes.ts",
      "lib/style/feedback.ts",
      "lib/style/flow.ts",
      "lib/style/taste.ts",
      "lib/style/text-arm.ts",
      "lib/style/events.ts",
      "lib/style/dwell.ts",
      "lib/style/markdown.tsx",
      "--module", "commonjs",
      "--target", "es2020",
      // **跟 tsconfig 一样开 strict。** 不开的话联合类型的收窄行为与真实构建不同：
      // 这里编得过、`next build` 编不过（或反过来），而两边编的是同一份源码——
      // 那就等于用一个不同的编译器去验一个我们并不发布的东西（同 F12 的「跑的是原文」）。
      "--strict",
      "--skipLibCheck",
      "--jsx", "react-jsx",
      "--esModuleInterop",
      // rootDir 写死，免得 tsc 按输入文件的公共前缀推断，输出层级随之漂。
      "--rootDir", "lib/style",
      "--outDir", out,
    ],
    { cwd: WEB, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
} catch (e) {
  console.error("编译失败：\n" + (e.stdout ?? "") + (e.stderr ?? ""));
  process.exit(1);
}

// `lib/upload/` 单独编一次：上面那次写死了 `--rootDir lib/style`，
// 换成 `lib` 会让所有 require 路径多一层，改动面比再编一次大得多。
const outUpload = mkdtempSync(join(WEB, "node_modules", ".f1-upload-"));
try {
  execFileSync(
    "npx",
    ["tsc", "lib/upload/failures.ts",
     "--module", "commonjs", "--target", "es2020", "--strict", "--skipLibCheck",
     "--rootDir", "lib/upload", "--outDir", outUpload],
    { cwd: WEB, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
} catch (e) {
  console.error("编译失败（lib/upload）：\n" + (e.stdout ?? "") + (e.stderr ?? ""));
  process.exit(1);
}

// `lib/explain/` 同理再编一次（rootDir 写死的代价）。
const outExplain = mkdtempSync(join(WEB, "node_modules", ".f1-explain-"));
try {
  execFileSync(
    "npx",
    ["tsc", "lib/explain/turns.ts",
     "--module", "commonjs", "--target", "es2020", "--strict", "--skipLibCheck",
     "--rootDir", "lib/explain", "--outDir", outExplain],
    { cwd: WEB, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
} catch (e) {
  console.error("编译失败（lib/explain）：\n" + (e.stdout ?? "") + (e.stderr ?? ""));
  process.exit(1);
}

const require_ = createRequire(import.meta.url);
const { AXES, DEFAULTS, DETAIL_LADDER, DEPTH_LADDER } = require_(join(out, "axes.js"));
const { applyFeedback, FEEDBACK_KINDS } = require_(join(out, "feedback.js"));
const { stepAt, configFromPicks, TOTAL_STEPS, REACHABLE_PAIR_IDS, makeFlips } = require_(
  join(out, "flow.js"),
);

const cfg = (over = {}) => ({ ...DEFAULTS, ...over });

// ---------------------------------------------------------------- F05

console.log("\n\x1b[1m一 · F05 · 反馈沿轴移动一格\x1b[0m");
{
  check(eq(DETAIL_LADDER, ["P0", "P2", "P3"]), "详细程度阶梯是 P0 → P2 → P3", DETAIL_LADDER.join(" → "));
  check(!DETAIL_LADDER.includes("P1"), "阶梯里没有 P1  ← 口径 F23，P1 已退出");
  check(
    eq(DEPTH_LADDER, ["what", "why", "derivation"]),
    "深度阶梯是 是什么 → 为什么 → 推导",
    "方向与详细程度阶梯相反，是最容易接反的一处",
  );

  const a = applyFeedback(cfg({ detail_level: "P0" }), "too_verbose");
  check(
    a.config.detail_level === "P2" && a.config_changed,
    "P0 + 太啰嗦 → P2",
    "**跳过 P1**，不是 P0 → P1",
  );

  const b = applyFeedback(cfg({ detail_level: "P2" }), "too_verbose");
  check(b.config.detail_level === "P3" && b.config_changed, "P2 + 太啰嗦 → P3");

  const c = applyFeedback(cfg({ depth: "derivation" }), "too_deep");
  check(c.config.depth === "why" && c.config_changed, "推导 + 讲太深 → 为什么");

  const d = applyFeedback(cfg({ depth: "what" }), "too_shallow");
  check(d.config.depth === "why" && d.config_changed, "是什么 + 讲太浅 → 为什么");

  // 只动目标轴。反馈按钮顺手改了别的轴，用户是看不出来的。
  const e = applyFeedback(cfg({ detail_level: "P0" }), "too_verbose");
  check(
    e.config.use_examples === DEFAULTS.use_examples &&
      e.config.structure === DEFAULTS.structure &&
      e.config.depth === DEFAULTS.depth,
    "只动目标轴，其余三条不变",
  );
}

// ---------------------------------------------------------------- F06

console.log("\n\x1b[1m二 · F06 ·「例子没懂」不改配置\x1b[0m");
{
  for (const detail of AXES.detail_level) {
    const before = cfg({ detail_level: detail });
    const r = applyFeedback(before, "example_unclear");
    check(eq(r.config, before), `${detail} + 例子没懂 → 配置逐字段不变`);
    check(!r.config_changed, `${detail} + 例子没懂 → config_changed 为 false`);
    check(
      !r.counts_as_config_edit,
      `${detail} + 例子没懂 → **不计入**「配置修改频率」的分子`,
    );
    check(r.regenerate, `${detail} + 例子没懂 → 触发重生成`);
  }

  // 反向控制：会改配置的那种反馈**必须**计入分子。
  // 少了这条，上面四条在「所有反馈都不计入」的实现下同样全绿。
  const moved = applyFeedback(cfg({ detail_level: "P0" }), "too_verbose");
  check(
    moved.counts_as_config_edit && moved.config_changed && !moved.regenerate,
    "反向控制：太啰嗦（真的改了配置）计入分子、不触发重生成",
  );
}

// ---------------------------------------------------------------- F07

console.log("\n\x1b[1m三 · F07 · 端点不静默吞掉\x1b[0m");
{
  const cases = [
    [cfg({ detail_level: "P3" }), "too_verbose", "已在最简洁一档"],
    [cfg({ depth: "what" }), "too_deep", "已在最浅一档"],
    [cfg({ depth: "derivation" }), "too_shallow", "已在最深一档"],
  ];
  for (const [before, kind, label] of cases) {
    const r = applyFeedback(before, kind);
    check(eq(r.config, before), `${label} + ${kind} → 配置不变`);
    check(!r.config_changed, `${label} + ${kind} → config_changed 为 false`);
    check(
      typeof r.message === "string" && r.message.length > 0,
      `${label} + ${kind} → **有一句说明**（不静默）`,
    );
    check(!r.counts_as_config_edit, `${label} + ${kind} → 不计入分子（没有真的改）`);
  }

  // 每种反馈在每种配置下，输出都必须落在取值域内——P1 之类的退出取值不该被算出来。
  let allInDomain = true;
  for (const detail of AXES.detail_level) {
    for (const depth of AXES.depth) {
      for (const kind of FEEDBACK_KINDS) {
        const r = applyFeedback(cfg({ detail_level: detail, depth }), kind);
        for (const axis of Object.keys(AXES)) {
          if (!AXES[axis].includes(r.config[axis])) allInDomain = false;
        }
      }
    }
  }
  check(allInDomain, "任意配置 × 任意反馈，输出都在取值域内", "共 3×3×4 = 36 组");
}

// ---------------------------------------------------------------- 比较树

console.log("\n\x1b[1m四 · 6 次固定顺序的比较树\x1b[0m");
{
  // 穷举 2^6 条路径：每步都有 a/b 两种选择。
  const seenPairs = new Set();
  const results = [];
  let brokeSomewhere = null;

  for (let mask = 0; mask < 1 << TOTAL_STEPS; mask++) {
    const picks = {};
    try {
      for (let i = 1; i <= TOTAL_STEPS; i++) {
        const step = stepAt(i, picks);
        seenPairs.add(step.pairId);
        // 用取值域里「第 (bit) 个」取值近似 a/b：真实取值由 pairs.json 的 variant_label 给，
        // 这里只需要一个合法取值来驱动分支。
        const domain = AXES[step.axis];
        const bit = (mask >> (i - 1)) & 1;
        picks[step.axis] = domain[bit % domain.length];
      }
      results.push(configFromPicks(picks));
    } catch (err) {
      brokeSomewhere = err.message;
      break;
    }
  }

  check(brokeSomewhere === null, "64 条路径都走得通并产出完整配置", brokeSomewhere ?? "");
  check(results.length === 1 << TOTAL_STEPS, `产出 ${1 << TOTAL_STEPS} 份配置`, `实得 ${results.length}`);
  check(
    eq([...seenPairs].sort(), [...REACHABLE_PAIR_IDS].sort()),
    "走到的对 == REACHABLE_PAIR_IDS",
    `${seenPairs.size} 个`,
  );

  // 每条轴都必须被 6 步覆盖到——漏一条的表现是那条轴永远是默认值，
  // 而默认值和「用户选的恰好等于默认值」在库里长得一模一样。
  const covered = Object.keys(AXES).every((axis) => results.every((r) => r[axis] !== undefined));
  check(covered, "四条轴都被 6 步覆盖，没有一条靠默认值兜底");

  // 缺一条轴时 configFromPicks 必须抛异常，而不是补默认值。
  let threw = false;
  try {
    configFromPicks({ detail_level: "P0" });
  } catch {
    threw = true;
  }
  check(threw, "反向控制：picks 缺轴 → 抛异常，不静默补默认值");
}

// ---------------------------------------------------------------- 接线

console.log("\n\x1b[1m五 · 两处「一处出处」的接线\x1b[0m");
{
  const { TEXT_ARM_CALL } = require_(join(out, "text-arm.js"));
  const routeSrc = readFileSync(join(WEB, "app/api/style/from-text/route.ts"), "utf8");
  const pairSrc = readFileSync(join(WEB, "app/onboarding/style/pairwise-client.tsx"), "utf8");

  // --- 对照臂：发出去的参数与记进事件的必须同源
  check(
    typeof TEXT_ARM_CALL.model === "string" && TEXT_ARM_CALL.model.length > 0,
    "TEXT_ARM_CALL 有 model",
    TEXT_ARM_CALL.model,
  );
  // 单测能证明这个对象存在，证明不了 **route 真的用了它**——
  // F3a 那次 59 条全绿而 request_signature 被改名没人发现，就是这个形态（通则七 #7）。
  // 所以这里读源码：请求参数必须从 TEXT_ARM_CALL 取，不能是字面量。
  //
  // ⚠️ **字段名从对象自己派生，不写死一张清单**（2026-09-20 改）。
  // 原来写死的是 Anthropic 那套（effort / thinking / max_tokens）；换成 Gemini
  // 之后那三条必然红，而红的理由不是「参数没同源」，是**判据记着一套过时的字段名**。
  // 派生之后，下一次换服务商这几条会自动跟着走。
  //
  // `provider` 例外：它是**描述**（记进事件、回给客户端），不是请求参数。
  // 例外清单显式写在这里，而不是「碰巧没检查到」。
  const NOT_A_REQUEST_PARAM = new Set(["provider"]);
  const callFields = Object.keys(TEXT_ARM_CALL).filter((k) => !NOT_A_REQUEST_PARAM.has(k));
  check(callFields.length >= 3, "TEXT_ARM_CALL 里有足够多的请求参数可查",
    `${callFields.join(" / ")} ← 少于 3 个说明这条在空转`);
  for (const field of callFields) {
    check(
      routeSrc.includes(`TEXT_ARM_CALL.${field}`),
      `route.ts 的 ${field} 取自 TEXT_ARM_CALL`,
      "← 写成字面量的话，发出去的和记下来的会各自漂",
    );
  }
  check(
    /\bmodel:\s*["'`]/.test(routeSrc) === false,
    "route.ts 里没有硬编码的模型 id",
    "← 有的话就是第二处出处（不限某一家的前缀：换服务商时前缀会变，这条不该跟着失效）",
  );
  check(
    routeSrc.includes("call: TEXT_ARM_CALL"),
    "响应里回了 call，阶段 4 的事件能原样带走",
  );

  // --- 主臂：随机**不许发生在客户端组件里**（2026-09-10 人工验收报的 React #418）
  //
  // 这条补的是一个真实的缺口：`makeFlips` 是纯函数、组件也确实调了它——
  // 原来那两条断言**都成立**，而 bug 照样在。问题在**调用发生的位置**：
  // 客户端组件的渲染在 SSR 与 hydrate 各跑一次，`useState(() => makeFlips())`
  // 的惰性初始化两侧各跑一遍，约一半概率抽出不同结果 → hydration 文本不匹配，
  // 服务端先绘制的那一版被换掉。
  //
  // 后果不止是控制台报错：用户看到并点下去的可能是服务端那一版，
  // 而 `leftVariant` 记的是客户端那一版，「点击对应哪个档位在左」就对不上——
  // **位置随机化的目的（让点击可归因）直接失效**。
  //
  // 所以判据从「函数对不对」上移到「随机发生在哪一侧」。
  const clientFiles = listClientComponents(join(WEB, "app"));
  check(clientFiles.length >= 3, "扫到客户端组件", `${clientFiles.length} 个`);

  const randomSources = randomSourceNames(join(WEB, "lib", "style"));
  check(
    randomSources.includes("makeFlips"),
    "推导出的随机源名单含 makeFlips",
    randomSources.join(" / ") + "  ← 名单是推导的，不是写死的",
  );
  // 阶段 4：session_id 也必须在服务端抽。它进名单是**自动**的（函数体里有
  // crypto.randomUUID），不需要有人记得回来改名单——这正是当初改成推导的理由。
  check(
    randomSources.includes("newSessionId"),
    "推导出的随机源名单含 newSessionId",
    "← session_id 与 left_variant 同一条处置：服务端抽好传 prop",
  );

  const randomInClient = clientRandomnessProblems(clientFiles, randomSources);
  check(
    randomInClient.length === 0,
    "客户端组件里没有随机源",
    randomInClient.length === 0
      ? "← 随机只能在服务端抽好传进来"
      : randomInClient.join("；"),
  );

  // 允许控制：随机**必须还在**，只是搬到了服务端。
  // 少了这条，把随机整个删掉也会让上一条全绿——而那正是最坏的结果（位置恒定）。
  const pageSrc = readFileSync(join(WEB, "app/onboarding/style/page.tsx"), "utf8");
  check(
    /makeFlips\(\s*TOTAL_STEPS\s*\)/.test(pageSrc) && /flips=\{/.test(pageSrc),
    "服务端页面抽随机并作为 prop 传下去",
    "← 只删不搬的话，位置会恒定而上一条照样绿",
  );
  check(
    /flips:\s*\(0 \| 1\)\[\]/.test(pairSrc),
    "客户端组件把 flips 作为 prop 接收",
  );

  // 允许控制（阶段 4）：三个服务端页面**必须**各自抽一个 session_id 并传下去。
  // 少了这条，把 newSessionId 整个删掉也会让「客户端没有随机源」全绿——
  // 而那时事件全部落在 undefined/空 session 上，弃流点分析直接没了。
  for (const rel of [
    "app/onboarding/style/page.tsx",
    "app/onboarding/style-text/page.tsx",
    "app/settings/style/page.tsx",
  ]) {
    const src = readFileSync(join(WEB, rel), "utf8");
    check(
      /sessionId=\{newSessionId\(\)\}/.test(src),
      `${rel} 在服务端抽 session_id 并作为 prop 传下去`,
      "← 只删不搬的话，上一条照样绿",
    );
  }

  // 反向控制：把 newSessionId 的调用塞进一个客户端组件，判据必须报出来。
  // 与上面 makeFlips 那条同构 —— 判据自己先过一遍反向控制（F16）。
  const fakeClient = {
    path: "settings-client.tsx(把 session 生成搬回客户端)",
    src: '"use client";\nconst id = newSessionId();\n',
  };
  const sessionDetected = clientRandomnessProblems([fakeClient], randomSources);
  check(
    sessionDetected.length > 0,
    "反向控制：客户端组件里调 newSessionId → 判据变红",
    sessionDetected.length > 0 ? `报出 ${sessionDetected.length} 条` : "没报出 —— 这条是空转的",
  );

  // 判据自身的分辨力：把修复前的写法塞回去，这条**必须**报出问题。
  const preFix = pairSrc.replace(
    "const [frozenFlips] = useState<(0 | 1)[]>(flips);",
    "const [frozenFlips] = useState<(0 | 1)[]>(() => makeFlips(TOTAL_STEPS));",
  );
  const detected = clientRandomnessProblems(
    [{ path: "pairwise-client.tsx(修复前)", src: preFix }],
    randomSources,
  );
  check(
    detected.length > 0,
    "反向控制：把修复前的写法塞回去，判据变红",
    detected.length > 0 ? `报出 ${detected.length} 条` : "没报出 —— 这条护栏是空转的",
  );

  // --- 主臂：记下了谁在左
  // 两个分支都要真的可达。写成 `rng() < 0` 时位置会恒定，而页面看起来完全正常——
  // 与 f1_assign_arm 那条「两个臂都要被断言碰到」是同一个形态（通则七 #7）。
  const flipDist = makeFlips(400).reduce((m, f) => ((m[f] = (m[f] ?? 0) + 1), m), {});
  check(
    (flipDist[0] ?? 0) > 0 && (flipDist[1] ?? 0) > 0,
    "左右两种顺序都可达",
    `400 次 → 左 ${flipDist[0] ?? 0} / 右 ${flipDist[1] ?? 0}`,
  );
  // 注入可控 rng，把两个分支各钉一次——不依赖真随机碰巧覆盖到。
  check(
    JSON.stringify(makeFlips(3, () => 0.1)) === "[0,0,0]" &&
      JSON.stringify(makeFlips(3, () => 0.9)) === "[1,1,1]",
    "rng 的两侧分别落到 0 与 1",
  );
  check(pageSrc.includes("makeFlips(TOTAL_STEPS)"), "页面用的是被测过的那个函数");
  check(
    pairSrc.includes("leftVariant"),
    "记录了 leftVariant",
    "← 没有它，位置效应事后无法从数据里分离",
  );
  check(
    pairSrc.includes("data-left-variant"),
    "页面上暴露 data-left-variant",
    "← 人工/回归要能确认随机化真的在发生",
  );
  // 选中记的是档位下标而不是左右位置——位置随机之后这是唯一稳定的东西。
  check(
    pairSrc.includes("pair.samples[selected].variantLabel"),
    "记的是档位而非左右位置",
    "← 记位置的话，随机化会直接把选择弄错",
  );
}

// ---------------------------------------------------------------- 样例渲染

console.log("\n\x1b[1m六 · 16 个样例都渲染得出来\x1b[0m");
{
  const { renderToStaticMarkup } = require_("react-dom/server");
  const { createElement } = require_("react");
  const { Markdown } = require_(join(out, "markdown.js"));
  const pairsJson = require_(join(WEB, "..", "f1_samples", "pairs.json"));

  const samples = pairsJson.pairs.flatMap((p) => p.samples);
  check(samples.length === 16, "读到 16 个样例", `实得 ${samples.length}`);

  let rendered = [];
  let crashed = null;
  for (const s of samples) {
    try {
      rendered.push(renderToStaticMarkup(createElement(Markdown, { text: s.text })));
    } catch (err) {
      crashed = `${s.sample_id}: ${err.message}`;
      break;
    }
  }
  check(crashed === null, "16 个样例全部渲染不抛异常", crashed ?? "");

  const all = rendered.join("");
  // 判据的分辨力：**前提由语料自己数出来**，不写死。
  //
  // 原来这五条直接断言五种标记都被渲染出来，前提是「语料里确实存在（上面数过）」——
  // 那个「数过」锚在 2026-09-09 那版 Bloom filter 语料上。2026-09-19 换成线性代数语料后
  // 斜体一处都没有，这条当场变红，而渲染器一个字节都没改。
  //
  // 改成：语料里有这种标记才断言它渲染成对应标签；没有就**明说这条空转、不计入绿数**。
  // 删掉它是另一个错误——以后语料里再出现斜体，渲染漏了就没有东西会红。
  //
  // 计数用的正则**独立于实现**（`lib/style/markdown.tsx` 里的 INLINE 是另一份）：
  // 同源的话，两边一起改仍然全绿，那就是空断言（同 F4-P1 抓到的形态）。
  const MARKERS = [
    ["粗体", "<strong>", /\*\*[^*\n]+\*\*/],
    ["代码", "<code", /`[^`\n]+`/],
    ["无序列表", "<li>", /^[-*][ \t]+\S/m],
    ["引用", "<blockquote", /^>[ \t]?\S/m],
    ["斜体", "<em>", /(^|[^*])\*[^*\n]+\*([^*]|$)/],
  ];
  for (const [label, tag, re] of MARKERS) {
    const hits = samples.filter((s) => re.test(s.text)).length;
    if (hits === 0) {
      console.log(`  \x1b[33m—\x1b[0m ${label}：当前语料里没有这种标记，这条判据空转（不计入绿数）`);
      continue;
    }
    check(all.includes(tag), `${label}渲染成 ${tag}`, `语料里 ${hits} 个样例含它`,
      `语料里 ${hits} 个样例含它，渲染结果里却没有 —— 会剩下裸标记`);
  }

  // 反向控制：标记确实被吃掉了，而不是原样漏进输出。
  const leftoverBold = (all.match(/\*\*/g) ?? []).length;
  check(leftoverBold === 0, "输出里没有残留的 ** 标记", `残留 ${leftoverBold} 处`);
  const leftoverStar = (all.match(/(?<!\*)\*(?!\*)/g) ?? []).length;
  check(leftoverStar === 0, "输出里没有残留的裸星号", `残留 ${leftoverStar} 处`);

  // HTML 转义：React 默认转义，这里钉住它——渲染路径将来若改成 innerHTML 会红。
  const evil = renderToStaticMarkup(
    createElement(Markdown, { text: "**a** <img src=x onerror=alert(1)>" }),
  );
  check(
    !evil.includes("<img") && evil.includes("&lt;img"),
    "正文里的 HTML 被转义，不当标签渲染",
    evil.slice(0, 80),
  );
}


// ---------------------------------------------------------------- 埋点五事件

/**
 * 纯函数，好让下面的反向控制直接喂一段改过的 SQL。七与十四两节共用。
 *
 * ⚠️ **取最后一个定义，不是第一个。** `f1_event_required_keys` 被
 * `create or replace` 过多次（阶段 4 五个类型 → 阶段 6 九个 → 上传批十一个 → F4 十二个），
 * 而迁移是按序执行的，**生效的是最后那一份**。
 *
 * 第一版用 `.exec()` 取的是**第一个**匹配，也就是阶段 4 那份（只有 5 个类型）。
 * 它当时是绿的，而且会一直绿——**绿的理由是它在跟一份已经不生效的定义比**。
 * 2026-09-12 加上传两个事件时发现：那条 `=== 5` 的分辨力断言正是靠读到
 * 旧定义才通过的。这是 F16 的又一个形态：判据在它要区分的两种情况下取值相同
 * （旧定义与新定义里那 5 个 style_* 恰好一样），所以它分不出「TS 与生效定义漂了」。
 */
function parseSqlRequiredKeys(sql) {
  const blocks = [
    ...sql.matchAll(/create or replace function public\.f1_event_required_keys[\s\S]*?\$\$;/g),
  ];
  if (blocks.length === 0) return null;
  const block = blocks[blocks.length - 1][0];   // ← **最后一个 = 生效的那一份**
  const out = {};
  for (const m of block.matchAll(/when\s+'(\w+)'\s*\n?\s*then\s*array\[([^\]]*)\]/g)) {
    out[m[1]] = [...m[2].matchAll(/'(\w+)'/g)].map((k) => k[1]);
  }
  return out;
}

console.log("\n\x1b[1m七 · 埋点五事件（阶段 4）\x1b[0m");
{
  const ev = require_(join(out, "events.js"));
  const { createDwellClock } = require_(join(out, "dwell.js"));
  const { TEXT_ARM_CALL } = require_(join(out, "text-arm.js"));

  // -------- 7.1 TS 的必需字段表 vs 迁移里的 f1_event_required_keys
  //
  // 同一份清单住在两处，各有不可替代的理由：
  //   · SQL 那份是最后一道防线，但它变红的时候事件已经丢了（insert 被拒，用户无感）；
  //   · TS 那份让缺字段在构造时就抛出来，跑一次 verify:logic 就能发现。
  // **两份存在 ≠ 两份一致**（口径 F33），所以有这一节。
  const migDir = join(WEB, "..", "supabase", "migrations");
  const migSrc = readdirSync(migDir)
    .filter((f) => f.endsWith(".sql"))
    .sort()
    .map((f) => readFileSync(join(migDir, f), "utf8"))
    .join("\n");

  const sqlKeys = parseSqlRequiredKeys(migSrc);
  // 判据自身的分辨力：解析失败会得到 null 或空对象，而空对象之间的比对必然相等。
  check(sqlKeys !== null, "迁移里找到 f1_event_required_keys");
  // **生效那一份有十四个类型**：F1 五个 + 讲解四个 + 上传两个 + F4 一个
  // （`confusion_mark_created`，2026-09-13）+ 开场标签两个（2026-09-19）。
  // 这个数是「解析器读到的是最新定义」的证据——读到旧定义会得到 5、11 或 12。
  check(
    Object.keys(sqlKeys ?? {}).length === 14,
    "SQL 生效定义解析出 14 个 event_type",
    `实得 ${Object.keys(sqlKeys ?? {}).length} ← 得到 5 说明解析器读的是阶段 4 那份旧定义；`
      + `得到 11/12 说明读的是 F4 或内测前那一版，漏了后来加的类型`,
  );
  // 反向控制：只取第一个定义时**必须**得到 5，否则上一条的分辨力是空的。
  {
    const firstOnly = /create or replace function public\.f1_event_required_keys[\s\S]*?\$\$;/.exec(migSrc)?.[0] ?? "";
    const n = [...firstOnly.matchAll(/when\s+'(\w+)'/g)].length;
    check(n === 5, "反向控制：第一个定义确实只有 5 个类型", `${n} 个 ← 上一条的分辨力靠它`);
  }
  // TS 只构造前端发得出的那几个（讲解 / 上传那批由服务端发，口径 F51）：
  // F1 的五个 + 开场标签两个 = 7。
  check(ev.EVENT_TYPES.length === 7, "TS 的 EVENT_TYPES 是 7 个", ev.EVENT_TYPES.join(" · "));
  // **TS 只镜像 F1 那五个**，这是口径 F51 的结果：讲解与上传的事件由 Python 侧
  // 构造（它们的字段全部产生在服务端），镜像在 `api/api/events.py`，
  // 那边有自己的一条从 SQL 原文读回来的比对。
  check(
    ev.EVENT_TYPES.every((t) => t.startsWith("style_")),
    "TS 镜像的五个都是 style_* 家族",
    "← 讲解/上传的事件在 Python 侧构造（口径 F51），不在这份镜像里",
  );

  /**
   * 返回不一致的说明。空数组 = 两份相同。
   *
   * **TS 那份是 SQL 的子集**（只镜像 F1 那五个，口径 F51），所以比的是：
   * ① TS 的每个类型都必须在 SQL 里（不在 = TS 有个数据库不认识的类型，
   *    那种事件一律 23514 被拒，而被拒的事件静默消失）；
   * ② 两边共有的类型，字段逐项相同。
   *
   * ⚠️ **不比「SQL 里有 TS 没有的」** —— 那是常态（讲解与上传六个类型
   * 在 Python 侧）。比了的话这一条会永远红，然后被人放宽成永远绿。
   */
  function compareKeys(tsKeys, sql) {
    const problems = [];
    const tsTypes = sorted_(Object.keys(tsKeys));
    const sqlTypes = Object.keys(sql ?? {});
    const orphan = tsTypes.filter((t) => !sqlTypes.includes(t));
    if (orphan.length) {
      problems.push(`TS 有而 SQL 没有的 event_type：${JSON.stringify(orphan)}`
        + "（这种事件会被数据库 23514 拒掉，而被拒的事件静默消失）");
    }
    for (const t of tsTypes) {
      if (!sqlTypes.includes(t)) continue;
      if (!eq(sorted_(tsKeys[t] ?? []), sorted_(sql?.[t] ?? []))) {
        problems.push(`${t}：TS ${JSON.stringify(tsKeys[t])} ≠ SQL ${JSON.stringify(sql?.[t])}`);
      }
    }
    return problems;
  }

  const keyProblems = compareKeys(ev.REQUIRED_PAYLOAD_KEYS, sqlKeys);
  check(keyProblems.length === 0, "TS 与 SQL 的必需字段表逐项一致", keyProblems.join("；"));

  // 反向控制：从 SQL **生效那一份**里拿掉 left_variant，比对**必须**报出来。
  //
  // ⚠️ 用 `replaceAll`：那一行在三个迁移里各出现一次，只改第一处的话
  // 生效定义没变，而解析器读的是生效那一份 —— 这条反向控制就会空转。
  const tamperedSql = migSrc.replaceAll(
    "then array['step', 'axis', 'pair_id', 'left_variant']",
    "then array['step', 'axis', 'pair_id']",
  );
  check(
    compareKeys(ev.REQUIRED_PAYLOAD_KEYS, parseSqlRequiredKeys(tamperedSql)).length > 0,
    "反向控制：SQL 少一个字段时比对变红",
    "← 少了它，两个空对象之间的相等也会被读成一致",
  );

  // `schema_version`（PRD §13.0 要求必埋）由**数据库的 default** 给，客户端一个字都不发。
  // 让客户端发就是第二处出处，而两处漂开的表现是「版本号说没变，其实变了」——
  // 比没有版本号更糟，因为它会让人停止追问。
  check(
    /schema_version text not null default '[a-z0-9-]+'/.test(migSrc),
    "迁移里 schema_version 有 default",
    /schema_version text not null default '([a-z0-9-]+)'/.exec(migSrc)?.[1] ?? "",
  );
  check(
    !/schema_version/.test(stripComments(readFileSync(join(WEB, "lib/style/events.ts"), "utf8"))),
    "TS 侧不发 schema_version",
    "← 由 default 给，客户端不碰",
  );

  // 公共列不该在 payload 里再存一遍——两处存同一个值就有两处会漂。
  const dupes = Object.entries(ev.REQUIRED_PAYLOAD_KEYS).flatMap(([t, keys]) =>
    keys.filter((k) => ["user_id", "arm", "session_id", "created_at"].includes(k)).map((k) => `${t}.${k}`),
  );
  check(dupes.length === 0, "payload 里不重复存公共列", dupes.join(" "));

  // -------- 7.2 每个构造函数产出的 payload 必带必需字段
  //
  // 「缺一个字段」是这套埋点最容易发生也最安静的坏法：数据库会拒，用户无感，
  // 报表里那一列就是空的。所以每个事件都真的构造一次再逐字段核。
  const cfg0 = cfg();
  const built = {
    style_pair_shown: ev.pairShown({ step: 1, axis: "detail_level", pairId: "detail_p0_p3", leftVariant: "P0" }),
    style_pair_chosen: ev.pairChosen({
      step: 1, axis: "detail_level", pairId: "detail_p0_p3",
      leftVariant: "P0", chosenVariant: "P3", dwellMs: 1234, hiddenMs: 0,
    }),
    style_onboarding_completed: ev.onboardingCompleted({ config: cfg0, stepsCompleted: 6, totalMs: 42000 }),
    style_text_mapped: ev.textMapped({ ok: true, axesFromText: ["detail_level"], axesFromDefault: ["depth"] }),
    style_config_changed: ev.manualChange(cfg0, { ...cfg0, depth: "derivation" }),
    style_taste_shown: ev.tasteShown({ board: "tone", tagIds: ["tone_peer", "tone_written"] }),
    style_taste_chosen: ev.tasteChosen({
      tags: ["tone_peer", "stuck_why"], tone: "spoken",
      priors: { depth: "why" }, totalMs: 18000,
    }),
  };
  for (const t of ev.EVENT_TYPES) {
    const payload = built[t].payload;
    const missing = ev.REQUIRED_PAYLOAD_KEYS[t].filter(
      (k) => payload[k] === undefined || payload[k] === null || payload[k] === "",
    );
    check(missing.length === 0, `${t} 的 payload 必需字段齐全`, missing.length ? `缺 ${missing.join(", ")}` : `${ev.REQUIRED_PAYLOAD_KEYS[t].length} 个`);
    check(built[t].event_type === t, `${t} 的 event_type 对得上`);
  }

  // 反向控制：构造函数自己也要会拒。少了这条，`complete()` 可以是个恒真的空壳。
  let refused = false;
  try {
    ev.pairShown({ step: 1, axis: "detail_level", pairId: "detail_p0_p3", leftVariant: "" });
  } catch (e) {
    refused = /left_variant/.test(e.message);
  }
  check(refused, "反向控制：left_variant 为空时构造函数抛异常且点名字段");

  // -------- 7.3 `dwell_ms` / `hidden_ms`：只采集不判读（口径 F38）
  //
  // 注入一个可控时钟，把两个数各钉一次。用真时间的话这几条会变成薛定谔的绿。
  {
    let t = 0;
    const clock = createDwellClock(() => t);
    t = 100; clock.markHidden();
    t = 400; clock.markVisible();   // 隐藏 300ms
    t = 1000;
    const r = clock.read();
    check(r.dwell_ms === 1000, "dwell_ms 是墙钟时长，切走的时间**照算**", `${r.dwell_ms}ms`);
    check(r.hidden_ms === 300, "hidden_ms 单独累计页面不可见时长", `${r.hidden_ms}ms`);
    check(r.hidden_ms <= r.dwell_ms, "hidden_ms ≤ dwell_ms");

    // 两个数分开的意义：只记一个总数的话，「读了 40 秒」与「挂着 40 秒」不可分。
    let t2 = 0;
    const c2 = createDwellClock(() => t2);
    t2 = 1000;
    const r2 = c2.read();
    check(
      r2.dwell_ms === r.dwell_ms && r2.hidden_ms !== r.hidden_ms,
      "两次同样长的停留，靠 hidden_ms 才分得开",
      `都是 ${r2.dwell_ms}ms，hidden ${r.hidden_ms} vs ${r2.hidden_ms}`,
    );

    // 换步要归零，否则第 2 步的时长里混着第 1 步的。
    let t3 = 0;
    const c3 = createDwellClock(() => t3);
    t3 = 5000; c3.reset();
    t3 = 5200;
    check(c3.read().dwell_ms === 200, "reset() 后计时归零", `${c3.read().dwell_ms}ms`);

    // 重复调用不该重复累加（visibilitychange 在某些浏览器上会连发）。
    let t4 = 0;
    const c4 = createDwellClock(() => t4);
    t4 = 100; c4.markHidden(); c4.markHidden();
    t4 = 300; c4.markVisible(); c4.markVisible();
    t4 = 500;
    check(c4.read().hidden_ms === 200, "markHidden / markVisible 重复调用不重复累加", `${c4.read().hidden_ms}ms`);
  }

  // **代码里不许出现毫秒阈值常量**（口径 F38 / F29）。
  //
  // v1 不设阈值、不做「乱点」判定：我们没有任何先验知道读完一段 400 字讲解要多久，
  // 现在拍一个数字是瞎猜，而拿到数据再按分布划线等于用结果定义判据。
  // 一旦有人在这里写下 `dwell_ms > 1500`，那条线就再也说不清是怎么来的了。
  const THRESHOLD_SHAPES = [
    /(dwell|hidden)[A-Za-z_]*\s*[<>]=?\s*\d/i,
    /\d\s*[<>]=?\s*(dwell|hidden)/i,
    /\b[A-Z_]*(DWELL|HIDDEN)[A-Z_]*\s*=\s*\d/,
  ];
  function thresholdHits(src) {
    const code = stripComments(src);
    return THRESHOLD_SHAPES.filter((re) => re.test(code)).map((re) => String(re));
  }
  const trackedFiles = [
    "lib/style/events.ts",
    "lib/style/dwell.ts",
    "app/onboarding/style/pairwise-client.tsx",
    "app/settings/style/settings-client.tsx",
  ];
  const thresholdProblems = trackedFiles.flatMap((rel) =>
    thresholdHits(readFileSync(join(WEB, rel), "utf8")).map((re) => `${rel} 命中 ${re}`),
  );
  check(thresholdProblems.length === 0, "埋点代码里没有毫秒阈值常量", thresholdProblems.join("；"));
  check(
    thresholdHits("if (dwell_ms < 1500) return;").length > 0,
    "反向控制：写下一个毫秒阈值，判据变红",
    "← 判据自己先过一遍分辨力",
  );

  // -------- 7.4 `config_changed` 与 feedback.ts 同源（任务书 §4.2 硬要求 3）
  //
  // F06 要「例子没懂」不进「配置修改频率」的分子。在埋点里重新判一遍就等于给这条
  // 口径开第二个出处，而两处不一致**不会报错**：§3.2 的「修改次数趋近 0」
  // 会因为埋点少写一个分支而自己达标。
  let sameSource = true;
  let mismatch = "";
  for (const detail of AXES.detail_level) {
    for (const depth of AXES.depth) {
      const before = cfg({ detail_level: detail, depth });
      for (const kind of FEEDBACK_KINDS) {
        const outcome = applyFeedback(before, kind);
        const payload = ev.feedbackChange(before, kind).payload;
        if (
          payload.config_changed !== outcome.config_changed ||
          payload.counts_as_config_edit !== outcome.counts_as_config_edit ||
          !eq(payload.after, outcome.config) ||
          payload.trigger !== `feedback:${kind}`
        ) {
          sameSource = false;
          mismatch = `${detail}/${depth} + ${kind}`;
        }
      }
    }
  }
  check(sameSource, "任意配置 × 任意反馈：事件字段与 applyFeedback 逐项相同", mismatch || "3×3×4 = 36 组");

  // F06 在事件里的样子：两个 false，而且是**两个独立的字段**。
  for (const detail of AXES.detail_level) {
    const pl = ev.feedbackChange(cfg({ detail_level: detail }), "example_unclear").payload;
    check(
      pl.config_changed === false && pl.counts_as_config_edit === false,
      `${detail} + 例子没懂 → 事件里 config_changed 与 counts_as_config_edit 都是 false`,
      "← F06：不改配置，且不进「配置修改频率」的分子",
    );
  }
  // F07 端点：同样两个 false，但**事件照发**——不发的话，「点了但没改」与「没点」
  // 在库里长得一模一样，而这条事件是 F07 唯一的数据痕迹。
  const endpoint = ev.feedbackChange(cfg({ detail_level: "P3" }), "too_verbose").payload;
  check(
    endpoint.config_changed === false && eq(endpoint.before, endpoint.after),
    "端点（P3 + 太啰嗦）→ 事件里 before 与 after 相同且 config_changed 为 false",
  );
  // 反向控制：真的改了配置的那种，两个字段必须都是 true。
  const moved2 = ev.feedbackChange(cfg({ detail_level: "P0" }), "too_verbose").payload;
  check(
    moved2.config_changed === true && moved2.counts_as_config_edit === true && moved2.after.detail_level === "P2",
    "反向控制：P0 + 太啰嗦 → 两个字段都是 true，after 是 P2",
    "← 少了它，一个恒返回 false 的实现也能让上面全绿",
  );

  // 手动改轴：改了就是 true，没改就是 false。
  const manualMoved = ev.manualChange(cfg0, { ...cfg0, depth: "derivation" }).payload;
  const manualSame = ev.manualChange(cfg0, { ...cfg0 }).payload;
  check(manualMoved.config_changed === true && manualMoved.trigger === "manual", "手动改轴 → trigger=manual 且 config_changed=true");
  check(manualSame.config_changed === false, "手动保存但一个字段都没改 → config_changed=false");

  // -------- 7.5 `call` 必须从 text-arm.ts 读，描述原文不得进事件
  const mapped = ev.textMapped({ ok: true, axesFromText: ["detail_level"], axesFromDefault: [] }).payload;
  check(eq(mapped.call, TEXT_ARM_CALL), "事件里的 call 与 TEXT_ARM_CALL 逐字段相同", JSON.stringify(mapped.call));

  const eventsSrc = readFileSync(join(WEB, "lib/style/events.ts"), "utf8");
  // 结构上传不进来：构造函数根本没有描述这个参数。**这比「记得别传」强**。
  check(
    !/function textMapped\([\s\S]*?\)\s*:/.test(eventsSrc) ||
      !/description/.test(/function textMapped\(([\s\S]*?)\}\)/.exec(eventsSrc)?.[1] ?? ""),
    "textMapped 的参数里没有描述原文",
    "← 参数里没有这个东西，才是真的传不进来（PRD §12.2）",
  );
  // 行为上也核一次：喂一段 canary，它不该出现在任何字段里。
  const CANARY = "我希望讲得细一点并且多举例子ZZCANARYZZ";
  const canaryJson = JSON.stringify(
    ev.textMapped({ ok: true, axesFromText: ["detail_level"], axesFromDefault: [] }).payload,
  );
  check(!canaryJson.includes("ZZCANARYZZ") && !canaryJson.includes(CANARY), "事件 payload 里不含描述原文");

  // 失败也要能落库：ok=false 时字段仍然在（空列表），否则失败永远进不了库，
  // 而失败率恰恰是这条臂最该看的数字之一。
  const failed_ = ev.textMapped({ ok: false, failure: "rate_limited" }).payload;
  check(
    failed_.ok === false && Array.isArray(failed_.axes_from_text) && Array.isArray(failed_.axes_from_default),
    "ok=false 时两个轴列表仍在（空数组），事件仍能落库",
    `failure=${failed_.failure}`,
  );

  // -------- 7.6 接线：这些构造函数**真的被页面调了**
  //
  // 单测能证明构造函数会算，证明不了页面调了它（F3a 那次 59 条全绿而
  // request_signature 被改名没人发现，就是这个形态，通则七 #7）。
  const pairSrc = readFileSync(join(WEB, "app/onboarding/style/pairwise-client.tsx"), "utf8");
  const settingsSrc = readFileSync(join(WEB, "app/settings/style/settings-client.tsx"), "utf8");
  const textRouteSrc = readFileSync(join(WEB, "app/api/style/from-text/route.ts"), "utf8");

  check(/pairShown\(\{/.test(pairSrc) && /pairChosen\(\{/.test(pairSrc), "成对比较页两个事件都发（一步一个事件，口径 F37）");
  check(/leftVariant,/.test(pairSrc), "两个事件都带上 leftVariant", "← 阶段 3 那次位置随机化靠它才有意义");
  check(/onboardingCompleted\(\{/.test(pairSrc), "主臂收尾屏发 style_onboarding_completed");
  // **对照臂也必须发。** onboarding 完成率是 A5 预登记的三个指标之一，按臂分组读——
  // 这条臂不发，那条曲线就只有一半，而少的那一半不会以任何形式报错。
  const textClientSrc = readFileSync(join(WEB, "app/onboarding/style-text/text-client.tsx"), "utf8");
  check(
    /onboardingCompleted\(\{/.test(textClientSrc),
    "对照臂收尾也发 style_onboarding_completed",
    "← 不发的话，text 臂的 onboarding 完成率根本算不出来",
  );
  check(/textMapped\(\{\s*ok:\s*true/.test(textRouteSrc), "route.ts 成功路径发 style_text_mapped");
  check(
    (textRouteSrc.match(/textMapped\(\{\s*ok:\s*false/g) ?? []).length >= 5,
    "route.ts 的失败分支也发事件",
    `${(textRouteSrc.match(/textMapped\(\{\s*ok:\s*false/g) ?? []).length} 处 ← 失败率是这条臂最该看的数字之一`,
  );
  check(/feedbackChange\(saved, kind\)/.test(settingsSrc), "设置页反馈按钮发 style_config_changed");
  check(/manualChange\(saved, draft\)/.test(settingsSrc), "设置页手动保存发 style_config_changed");
  // 反馈按钮**在配置没变时也要发**——F06/F07 的唯一数据痕迹。
  check(
    /if \(!outcome\.config_changed\) \{[\s\S]*?void send\(event\);/.test(settingsSrc),
    "配置没变的分支里也发了事件",
    "← 不发的话，「点了但我们决定不改」与「没点」在库里一模一样",
  );

  // 客户端对 style_events **只有 insert 权限**，带 select 的写入会直接 42501。
  // 先剥注释：文档里解释「为什么不 .select()」的那句话本身含这几个字，
  // 不剥的话判据会把自己的文档判红（与 randomSourceNames 那次同一个坑）。
  check(
    !/\.select\(/.test(stripComments(eventsSrc)),
    "recordEvent 不带 .select()",
    "← 事件表只授予 insert，读走 service_role",
  );
}


// ---------------------------------------------------------------- 写入失败之后

console.log("\n\x1b[1m八 · 事件写入失败之后（2026-09-11 人工验收的红）\x1b[0m");
{
  const ev = require_(join(out, "events.js"));

  // 实测：某账号 style_pair_chosen 6 条而 style_pair_shown 只有 5 条，缺的固定是 step 1，
  // Console 里 ERR_CONNECTION_CLOSED + TypeError: Failed to fetch。
  //
  // **红的不是「网络会抖」——网络抖是必然的。** 红的是失败之后没有任何补偿：
  // 事件丢了、日志里一行、然后就没了。而 shown/chosen 配对是算位置效应的基础。
  //
  // 下面这一组测的就是「补偿真的在」，而不是「代码里写了 retry 这个词」。

  /** 可编程的假 sink：按脚本逐次返回成功或失败，并记下收到的每一行。 */
  function fakeSink(script) {
    const seen = [];
    let i = 0;
    return {
      seen,
      calls: () => i,
      from() {
        return {
          insert(row) {
            const step = script[Math.min(i, script.length - 1)];
            i++;
            if (step === null) {
              seen.push(row);
              return Promise.resolve({ error: null });
            }
            return Promise.resolve({ error: step });
          },
        };
      },
    };
  }

  const NET = { message: "TypeError: Failed to fetch", code: "" };     // 实测那条
  const BAD_PAYLOAD = { message: "violates check constraint", code: "23514" };
  const noSleep = () => Promise.resolve();
  const memStorage = () => {
    const m = new Map();
    return {
      getItem: (k) => (m.has(k) ? m.get(k) : null),
      setItem: (k, v) => m.set(k, v),
      removeItem: (k) => m.delete(k),
      _dump: () => m,
    };
  };
  const CTX = { userId: "u1", arm: "pairwise", sessionId: "s1" };
  const anEvent = () =>
    ev.pairShown({ step: 1, axis: "detail_level", pairId: "detail_p0_p3", leftVariant: "P0" });

  // -------- 8.1 分类：什么算瞬时
  check(ev.classifyInsertFailure({ code: "" }) === "transient", "没有错误码 → 瞬时", "← fetch 被网络层拒掉时就是这样，实测那条正是它");
  check(ev.classifyInsertFailure({}) === "transient", "code 缺失 → 瞬时");
  check(ev.classifyInsertFailure({ code: "PGRST301" }) === "transient", "JWT 过期 → 瞬时");
  check(ev.classifyInsertFailure({ code: "503" }) === "transient", "503 → 瞬时");
  // 方向是刻意的：把瞬时当永久 → 一条响亮的报告；反过来 → 静默空转。
  check(ev.classifyInsertFailure({ code: "23514" }) === "permanent", "23514 → 永久", "← payload 缺字段，重试一万次也不会成功");
  check(ev.classifyInsertFailure({ code: "42501" }) === "permanent", "42501 → 永久", "← 权限/RLS，重试无用");
  check(ev.classifyInsertFailure({ code: "PGRST204" }) === "permanent", "PGRST204 → 永久", "← 列不存在是真 bug，要吵不要重试");

  // -------- 8.2 瞬时失败**最终会落库**（这就是那条红的直接修复）
  {
    const sink = fakeSink([NET, NET, null]);
    const q = ev.createEventQueue(sink, CTX, { delaysMs: [1, 1, 1], sleep: noSleep, storage: memStorage() });
    q.enqueue(anEvent());
    await q.flush();
    check(sink.seen.length === 1, "前两次网络失败后，第三次真的写进去了", `尝试 ${sink.calls()} 次，落库 ${sink.seen.length} 条`);
    check(q.state().pending === 0 && q.state().dropped === 0, "落库后队列清空、无丢弃", JSON.stringify(q.state()));
    check(sink.seen[0]?.payload?.left_variant === "P0", "落库的那条带着 left_variant", "← 缺 shown 的那一步位置效应无从算起");
  }

  // -------- 8.3 瞬时失败用尽重试 → **留在队列里，不算丢**
  //
  // 这条是 8.2 的反向控制：少了它，一个「失败就 shift 掉」的实现也能让 8.2 全绿
  // （只要偶尔成功一次），而丢掉的那些没有任何东西会红。
  {
    const sink = fakeSink([NET]);
    const q = ev.createEventQueue(sink, CTX, { delaysMs: [1, 1], sleep: noSleep, storage: memStorage() });
    q.enqueue(anEvent());
    await q.flush();
    const st = q.state();
    check(st.pending === 1 && st.dropped === 0 && st.stalled === true,
      "一直失败 → pending 仍是 1、dropped 是 0、stalled 为真",
      `${JSON.stringify(st)} ← 网络问题不丢任何东西`);
    check(st.lastError !== null && /Failed to fetch/.test(st.lastError), "错误原文留着，供页面显示", st.lastError ?? "");
  }

  // -------- 8.4 永久失败 → 丢掉并计数，**且不重试**
  {
    const sink = fakeSink([BAD_PAYLOAD]);
    const q = ev.createEventQueue(sink, CTX, { delaysMs: [1, 1, 1], sleep: noSleep, storage: memStorage() });
    q.enqueue(anEvent());
    await q.flush();
    check(sink.calls() === 1, "永久失败只试一次", `实际 ${sink.calls()} 次 ← 重试会把「我们构造了一条坏事件」拖成「队列一直不空」`);
    check(q.state().dropped === 1 && q.state().pending === 0, "计入 dropped 并出队", JSON.stringify(q.state()));
  }

  // -------- 8.5 顺序：shown 卡住时 chosen 不许插队
  //
  // 乱序不会丢数据，但「先看到再点选」这个事实会没了——而那是位置效应分析的前提。
  {
    const sink = fakeSink([NET, null, null]);
    const q = ev.createEventQueue(sink, CTX, { delaysMs: [1], sleep: noSleep, storage: memStorage() });
    q.enqueue(anEvent());
    q.enqueue(
      ev.pairChosen({
        step: 1, axis: "detail_level", pairId: "detail_p0_p3",
        leftVariant: "P0", chosenVariant: "P3", dwellMs: 1, hiddenMs: 0,
      }),
    );
    await q.flush();
    check(
      sink.seen.length === 2 &&
        sink.seen[0].event_type === "style_pair_shown" &&
        sink.seen[1].event_type === "style_pair_chosen",
      "shown 先于 chosen 落库，即使 shown 失败过一次",
      sink.seen.map((r) => r.event_type).join(" → "),
    );
  }

  // -------- 8.6 跨页面存活：卡住的那条落进 storage，下一个队列捡得起来
  //
  // 用户在收尾屏点「回到首页」时组件会卸载，内存队列跟着没。
  {
    const store = memStorage();
    const dead = fakeSink([NET]);
    const q1 = ev.createEventQueue(dead, CTX, { delaysMs: [1], sleep: noSleep, storage: store });
    q1.enqueue(anEvent());
    await q1.flush();
    check(q1.state().pending === 1, "第一个队列没发出去，留在 pending", JSON.stringify(q1.state()));

    const alive = fakeSink([null]);
    const q2 = ev.createEventQueue(alive, CTX, { delaysMs: [1], sleep: noSleep, storage: store });
    check(q2.state().pending === 1, "新队列从 storage 里读到了那一条", "← 组件卸载不等于事件没了");
    await q2.flush();
    check(alive.seen.length === 1 && q2.state().pending === 0, "下一次页面加载把它冲了出去", `落库 ${alive.seen.length} 条`);
    check(store._dump().size === 0, "冲完把 storage 清干净", "← 不清的话下次会重复发");
  }

  // -------- 8.7 服务端那条路径也重试（route handler 没有队列可排）
  {
    const sink = fakeSink([NET, null]);
    const r = await ev.recordEvent(sink, { ...CTX, arm: "text", event: anEvent() }, { delaysMs: [1], sleep: noSleep });
    check(r.ok === true && sink.calls() === 2, "recordEvent 对瞬时失败会重试", `${sink.calls()} 次`);
    const sink2 = fakeSink([BAD_PAYLOAD]);
    const r2 = await ev.recordEvent(sink2, { ...CTX, arm: "text", event: anEvent() }, { delaysMs: [1, 1], sleep: noSleep });
    check(r2.ok === false && r2.kind === "permanent" && sink2.calls() === 1, "永久失败不重试并报出 kind", `${sink2.calls()} 次，kind=${r2.kind}`);
  }

  // -------- 8.8 接线：三个页面用的是队列，不是裸插入
  const pairSrc2 = readFileSync(join(WEB, "app/onboarding/style/pairwise-client.tsx"), "utf8");
  const settingsSrc2 = readFileSync(join(WEB, "app/settings/style/settings-client.tsx"), "utf8");
  const textSrc2 = readFileSync(join(WEB, "app/onboarding/style-text/text-client.tsx"), "utf8");
  for (const [name, src] of [["主臂", pairSrc2], ["设置页", settingsSrc2], ["对照臂", textSrc2]]) {
    const code = stripComments(src);
    check(/createEventQueue\(/.test(code), `${name} 用 createEventQueue`);
    check(/\.flush\(\)/.test(code), `${name} 挂载时冲一次队列`, "← 上一次页面加载没发完的靠它出去");
    check(!/recordEvent\(/.test(code), `${name} 不直接调 recordEvent`, "← 裸插入没有重试也没有排队，正是这次的红");
    check(/data-events-pending/.test(code), `${name} 在 DOM 上暴露 pending/dropped`, "← 人工验收要能当场看见，不是事后查库");
  }
}

// ================================================================ 十 · 上传页（阶段 6 第一批）
console.log("\n\x1b[1m十 · 上传页 · 失败分类（阶段 6 第一批）\x1b[0m");
{
  const f = require_(join(outUpload, "failures.js"));
  const migDir2 = join(WEB, "..", "supabase", "migrations");
  const migAll = readdirSync(migDir2)
    .filter((x) => x.endsWith(".sql")).sort()
    .map((x) => readFileSync(join(migDir2, x), "utf8")).join("\n");
  const pySrc = readFileSync(join(WEB, "..", "api", "api", "upload_failures.py"), "utf8");

  // -------- 10.1 三处落地的闭集：SQL（唯一出处）· Python（分类+状态码）· TS（文案）
  //
  // 三处存在 ≠ 三处一致（口径 F33）。少了这一节，后端加一类而前端没加的表现是
  // **用户看到一句兜底文案**，而那句兜底会把一个本来能说清的失败说成
  // 「请重试」—— 正是 PRD §10 明确禁止的那种无信息量提示。
  function parseSqlKinds(sql) {
    const blocks = [...sql.matchAll(
      /create or replace function public\.f3_deck_upload_failure_kinds[\s\S]*?\$\$;/g)];
    if (!blocks.length) return null;
    const block = blocks[blocks.length - 1][0];   // ← 生效的那一份（同 7.1 的教训）
    const arr = /select array\[([\s\S]*?)\]/.exec(block)?.[1] ?? "";
    return [...arr.matchAll(/'(\w+)'/g)].map((m) => m[1]);
  }
  const sqlKinds = parseSqlKinds(migAll);
  check(sqlKinds !== null, "迁移里找到 f3_deck_upload_failure_kinds");
  check(sqlKinds?.length === 9, "SQL 解析出 9 类", `实得 ${sqlKinds?.length}`);
  check(eq(sorted_(f.FAILURE_KINDS), sorted_(sqlKinds ?? [])),
    "TS 的 FAILURE_KINDS 与 SQL 逐项一致",
    `TS ${JSON.stringify(sorted_(f.FAILURE_KINDS))} vs SQL ${JSON.stringify(sorted_(sqlKinds ?? []))}`);

  // 反向控制：SQL 少一类时比对必须变红。
  //
  // ⚠️ **必须 replaceAll，不能 replace**（2026-09-20 当场红了一次）。
  // `20260920100000` 用 `create or replace` 又定义了一遍这个函数，于是
  // `'unparsable',` 在迁移全文里出现两次；`replace` 只删掉**旧文件里**那一次，
  // 而解析取的是**最后一份**（生效的那一份）——于是这条反向控制自己空转了：
  // 它删了一行，被比对的那份却一个字没变，然后「比对没红」被读成「控制失败」。
  // 这正是这条控制存在的理由，只是它这次指向的是它自己。
  check(
    !eq(sorted_(f.FAILURE_KINDS),
        sorted_(parseSqlKinds(migAll.replaceAll("    'unparsable',\n", "")) ?? [])),
    "反向控制：SQL 少一类时比对变红",
    "← 少了它，两个空数组之间的相等也会被读成一致",
  );

  // Python 侧那份（分类的服务端出处）。**从源文件解析**，不在这里抄第三份。
  const pyKinds = [...(/FAILURE_KINDS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/.exec(pySrc)?.[1] ?? "")
    .matchAll(/"(\w+)"/g)].map((m) => m[1]);
  check(pyKinds.length === 9, "Python 侧解析出 9 类", `实得 ${pyKinds.length}`);
  check(eq(sorted_(f.FAILURE_KINDS), sorted_(pyKinds)), "TS 与 Python 的闭集逐项一致");

  // -------- 10.2 `retryable` 与 Python 的 RETRYABLE_KINDS 同源
  //
  // 方向是刻意取小的：把可重试判成不可重试，用户少点一次按钮；
  // **反过来他会一直重试一个永不成功的上传**。
  const pyRetryable = [...(/RETRYABLE_KINDS: frozenset\[str\] = frozenset\(\{([\s\S]*?)\}\)/
    .exec(pySrc)?.[1] ?? "").matchAll(/"(\w+)"/g)].map((m) => m[1]);
  check(pyRetryable.length === 2, "Python 侧解析出 2 类可重试", pyRetryable.join(" · "));
  const tsRetryable = f.FAILURE_KINDS.filter((k) => f.FAILURE_COPY[k].retryable);
  check(eq(sorted_(tsRetryable), sorted_(pyRetryable)),
    "TS 的 retryable 与 Python 的 RETRYABLE_KINDS 一致",
    `TS ${JSON.stringify(sorted_(tsRetryable))} vs Py ${JSON.stringify(sorted_(pyRetryable))}`);
  check(!tsRetryable.includes("duplicate_in_course"),
    "「已经有这份文件了」不给重试按钮", "← 重试会再失败一次，而事实是什么都不用做");
  check(!tsRetryable.includes("unparsable"),
    "「打不开」不给重试按钮", "← 重试一个永远不会成功的上传");
  check(!tsRetryable.includes("no_usable_text"),
    "「一页都读不出文字」不给重试按钮", "← 同一份扫描件重试多少次都是同一个结果");

  // -------- 10.3 每一类都有自己的文案，且不含无信息量的措辞（PRD §10）
  const emptyCopy = f.FAILURE_KINDS.filter(
    (k) => !f.FAILURE_COPY[k]?.title?.trim() || !f.FAILURE_COPY[k]?.action?.trim());
  check(emptyCopy.length === 0, "九类各有 title 与 action", emptyCopy.join(" "));

  const titles = f.FAILURE_KINDS.map((k) => f.FAILURE_COPY[k].title);
  check(new Set(titles).size === titles.length,
    "八条 title 互不相同", "← 相同就等于压成了「上传失败」");

  // 「请稍后重试」这类无信息量提示：PRD §10 明确禁止。
  const vacuous = f.FAILURE_KINDS.filter((k) =>
    /请稍后(再)?重试|稍后再试|未知错误，请重试/.test(
      f.FAILURE_COPY[k].title + f.FAILURE_COPY[k].action));
  check(vacuous.length === 0, "没有「请稍后重试」这类无信息量文案", vacuous.join(" "));

  // PPTX 那一类**不许**说成「不支持」——会让用户以为永远不支持而去手动转格式。
  const pptx = f.FAILURE_COPY.pptx_not_implemented;
  check(/还没做|尚未|在做/.test(pptx.title + pptx.action),
    "PPTX 那一类说的是「还没做」", pptx.title);
  check(!/^不支持/.test(pptx.title), "PPTX 那一类不以「不支持」开头");
  // 允许控制：真正不支持的那一类**要**说「只能读 .pdf」。
  check(/\.pdf/.test(f.FAILURE_COPY.unsupported_format.action),
    "「不是 PDF」那一类点名只能读 .pdf");

  // 「已经有这份文件了」要说清**不用重新上传**。
  check(/不用(重新)?(再)?上传|已经可以/.test(f.FAILURE_COPY.duplicate_in_course.action),
    "重复那一类说清不用重新上传", f.FAILURE_COPY.duplicate_in_course.action);

  // -------- 10.4 状态码兜底表与 Python 的 HTTP_STATUS_BY_KIND 同源
  const pyStatus = Object.fromEntries(
    [...pySrc.matchAll(/"(\w+)": (\d{3}),/g)].map((m) => [m[1], Number(m[2])]));
  check(Object.keys(pyStatus).length === 9, "Python 侧解析出 9 个状态码映射",
    `实得 ${Object.keys(pyStatus).length}`);
  const statusProblems = f.FAILURE_KINDS.filter(
    (k) => f.KIND_BY_STATUS[pyStatus[k]] !== k);
  check(statusProblems.length === 0, "TS 的状态码兜底表与 Python 互为反函数",
    statusProblems.join(" "));
  check(new Set(Object.values(pyStatus)).size === 9,
    "九类各有不同的状态码", "← 复用会让前端按状态码分支时把两类并成一类");

  // -------- 10.5 认不出来的分类：回落 unknown，但 `matched` 为 false
  check(f.copyFor("unsupported_format").matched === true, "认得的分类 matched=true");
  const unmatched = f.copyFor("brand_new_kind_from_the_future");
  check(unmatched.matched === false, "认不出的分类 matched=false",
    "← 页面据此显示「后端可能加了一类而前端没跟上」");
  check(unmatched.title === f.FAILURE_COPY.unknown.title, "认不出的分类回落 unknown 文案");
  check(f.copyFor(undefined).matched === false, "没有分类时也回落 unknown");
  check(f.kindFromStatus(501) === "pptx_not_implemented", "kindFromStatus 认得 501");
  check(f.kindFromStatus(418) === "unknown", "kindFromStatus 兜底 unknown");
}

// ================================================================ 十一 · 上传页的接线
console.log("\n\x1b[1m十一 · 上传页 · 接线（阶段 6 第一批）\x1b[0m");
{
  // ⚠️ **2026-09-20 改了这一行指向的文件。**
  //
  // 跨课的 `/courses` 上传页退役了（现在只是一条 redirect），上传搬进了每门课
  // 自己的侧栏弹窗，托管它的服务端页面是 `app/courses/[id]/page.tsx`。
  // 继续读那个 redirect 壳子的话，下面三条会一直红——而红的理由不是纪律没守，
  // 是判据指着一个已经不承担这件事的文件。**纪律没变，承载它的页面变了。**
  const page = readFileSync(join(WEB, "app", "courses", "[id]", "page.tsx"), "utf8");
  const client = readFileSync(join(WEB, "app", "courses", "upload-client.tsx"), "utf8");
  const clientCode = stripComments(client);
  const pageCode = stripComments(page);

  // `session_id` 是这一页唯一的埋点责任。不带的话服务端一条事件都不发
  // （它不现编一个——编了会把「平均会话轮次」这个北极星指标改成假数）。
  check(/session_id=\$\{encodeURIComponent\(sessionId\)\}/.test(clientCode),
    "上传请求带 session_id", "← 不带则服务端一条事件都不发，且它不现编");

  // `sessionId` 必须在**服务端**抽（React #418：客户端组件 SSR 与 hydrate
  // 各渲染一次，渲染期取随机数两侧会分叉）。
  check(/newSessionId\(\)/.test(pageCode), "sessionId 在服务端页面里抽");
  check(!/newSessionId/.test(clientCode), "客户端组件不调 newSessionId",
    "← React #418：SSR 与 hydrate 两侧会分叉");

  // 口径 F51：这一页**不构造**那两个事件（它们的字段全在服务端产生）。
  for (const bad of ["deck_uploaded", "complete_event", "REQUIRED_PAYLOAD_KEYS"]) {
    check(!new RegExp(bad).test(clientCode), `客户端不构造 ${bad}`,
      "← 口径 F51：字段全部产生在服务端，浏览器一个都不知道");
  }

  // 直连产品服务端，**不走 route handler 代理**（Vercel 请求体上限 4.5MB，
  // 而上传上限 50MB —— 代理会把大课件挡在 Vercel 那一层）。
  check(/fetch\(`\$\{apiBase\}/.test(clientCode), "直连 apiBase，不走本站代理",
    "← Vercel serverless 请求体上限 4.5MB，上传上限 50MB");
  check(!/fetch\(["'`]\/api\//.test(clientCode), "没有指向本站 /api/ 的上传路径");

  // 失败按分类显示。
  check(/copyFor\(/.test(clientCode), "失败走 copyFor 的分类文案");
  check(/failure_kind/.test(clientCode), "优先读响应体里的 failure_kind",
    "← 从状态码反推会丢信息（415 与 501 都是格式家族）");
  check(/kindFromStatus\(/.test(clientCode), "状态码只作兜底");

  // 状态挂在 DOM 上，回归工具不按中文文案定位（改文案不该让断言静默变绿）。
  for (const marker of ["data-state", "data-failure-kind", 'data-field="upload-failure"',
                        'data-field="upload-ok"', 'data-field="retry"',
                        'data-field="unknown-kind"']) {
    check(client.includes(marker), `DOM 上有 ${marker}`);
  }
  check(/data-state=\{/.test(pageCode), "页面有终态标记 data-state",
    "← 三个分支各恰好一个，供回归工具等待与断言");
  check(/no-api-base/.test(pageCode), "缺 NEXT_PUBLIC_API_BASE_URL 时点名说清",
    "← 不在 lib/env.ts 抛：那会让整站因为一个没部署的服务起不来");

  // 串行上传：并行会让同一门课的两份同名文件同时撞唯一索引，
  // 而那时「先查后提示」查不到任何一份（两边都还没插进去）。
  check(/for \(const it of added\) await uploadOne\(it\)/.test(clientCode),
    "多份文件串行上传", "← 并行时「先查」两边都查不到，双双撞唯一索引");
}

// ================================================================ 十二 · 讲解页 · 轮次口径
console.log("\n\x1b[1m十二 · 讲解页 · turn_index 口径（阶段 6 第二批）\x1b[0m");
{
  const T = require_(join(outExplain, "turns.js"));
  const turn = (state, turnIndex = 1, over = {}) =>
    ({ turnIndex, question: "Q", state, ...over });

  // §13.0 原文：「**轮次口径：按用户可见的输出计，重生成不计入轮次**，
  // 只计入成本与质量指标。」拆成两条可各自被断言的规则。

  // -------- 12.1 失败不进轮次
  //
  // ⚠️ 这一条前端**很容易做错**：最自然的写法是「发一次请求就 +1」。
  // 而一次 503（服务端没配 key）、404（课程不对）、或 CORS 被挡，
  // **用户一个字都没看到** —— 那不是一轮讲解。
  // 按请求计数会让「平均会话轮次」把失败算进分子，而那个数只会**偏高**，
  // 看起来像产品更粘。
  check(T.nextTurnIndex([]) === 1, "空会话的第一轮是 1");
  check(T.nextTurnIndex([turn("answered")]) === 2, "答过一轮之后下一轮是 2");
  check(
    T.nextTurnIndex([turn("failed", 1)]) === 1,
    "**失败之后下一轮仍然是 1**",
    "← 它是「第几次让用户看到回答」的序号，不是「第几次发请求」",
  );
  check(
    T.nextTurnIndex([turn("answered", 1), turn("failed", 2), turn("answered", 2)]) === 3,
    "失败夹在中间时序号不跳号",
    "← 两轮成功 → 下一轮 3；失败那次与它后面那次共用序号 2",
  );
  check(T.nextTurnIndex([turn("pending", 1)]) === 1,
    "还在等的那一轮也不计入", "← 它还没产生输出");

  // -------- 12.2 重生成不进轮次
  //
  // 前端天然做对了（重生成在服务端一次请求里面，前端看不见），
  // **但它必须有断言**：否则哪天有人按 `regenerations` 去累加轮次，
  // 没有任何东西会红。
  const withRegen = [
    turn("answered", 1, { answer: { regenerations: 2, backend: "supabase" } }),
  ];
  check(T.nextTurnIndex(withRegen) === 2,
    "一轮里重生成两次，下一轮仍然是 2", "← 重生成只计入成本与质量指标（§13.0）");
  check(
    !/regenerations/.test(String(T.nextTurnIndex)) &&
      !/regenerations/.test(String(T.answeredCount)),
    "轮次函数的实现里不出现 regenerations",
    "← 一个字段两种含义，两张报表永远对不上，而两边看起来都是对的",
  );

  // -------- 12.3 两个函数差 1 但语义不同，不许合并
  check(T.answeredCount([turn("answered"), turn("failed", 2)]) === 1, "answeredCount 只数答过的");
  check(
    T.answeredCount([]) + 1 === T.nextTurnIndex([]),
    "answeredCount + 1 === nextTurnIndex",
    "← 合成一个会让调用方在某处忘记 ±1，而那种错在数据里是整体偏移一格",
  );

  // -------- 12.4 只允许一轮在飞
  check(T.hasPending([turn("pending")]) === true, "hasPending 认得在飞的轮次");
  check(T.hasPending([turn("answered"), turn("failed", 2)]) === false,
    "没有在飞时 hasPending 为 false");

  // -------- 12.5 出处按文件合并页号
  const ans = T.answerFromResponse({
    text: "一段讲解", accepted: true, regenerations: 0,
    mean_paragraph_chars: 75.5, detail_level: "P0",
    retrieval: {
      meta: { backend: "supabase" },
      slices: [
        { source_file: "a.pdf", pages: [12, 3] },
        { source_file: "a.pdf", pages: [3, 7] },
        { source_file: "b.pdf", pages: [1] },
      ],
    },
  });
  check(ans.sources.length === 2, "同一份讲义的多片合并成一条出处", `${ans.sources.length} 条`);
  const a = ans.sources.find((x) => x.sourceFile === "a.pdf");
  check(eq(a.pages, [3, 7, 12]), "页号去重并升序", JSON.stringify(a.pages));
  check(ans.backend === "supabase", "backend 从 meta 取出");
  check(T.answerFromResponse({ text: "x", accepted: true, regenerations: 0,
    mean_paragraph_chars: 1, detail_level: "P0" }).backend === "unknown",
    "没有 meta 时 backend 是 unknown，不是 supabase",
    "← 默认成 supabase 会把「不知道」说成「是你的课件」");

  // -------- 12.6 用合成课件答的必须能被判出来
  //
  // 2026-09-12 真实发生：线上跑着旧构建，它静默丢掉 course_id 并用合成课件作答，
  // 返回 200，正文在讲布隆过滤器（口径 F52）。那种回答**看起来完全正常**。
  check(T.isFromRequestedCourse({ backend: "supabase" }) === true, "supabase → 来自这门课");
  check(T.isFromRequestedCourse({ backend: "in_memory" }) === false,
    "in_memory → **不是**来自这门课", "← 页面据此打一条醒目提示");
  check(T.isFromRequestedCourse({ backend: "unknown" }) === false,
    "unknown 也判成「不是」", "← 方向取严：不确定时不要声称是用户的课件");
}

// ================================================================ 十三 · 讲解页的接线
console.log("\n\x1b[1m十三 · 讲解页 · 接线（阶段 6 第二批）\x1b[0m");
{
  const page = readFileSync(join(WEB, "app", "explain", "page.tsx"), "utf8");
  const client = readFileSync(join(WEB, "app", "explain", "explain-client.tsx"), "utf8");
  const pageCode = stripComments(page);
  const clientCode = stripComments(client);

  // session_id 在服务端抽（React #418）。这一页比别的页更在意：
  // 它是「平均会话轮次」的分组键，两侧分叉会让同一次会话的轮次落到两个 session 上。
  check(/newSessionId\(\)/.test(pageCode), "sessionId 在服务端页面里抽");
  check(!/newSessionId/.test(clientCode), "客户端组件不调 newSessionId",
    "← React #418：SSR 与 hydrate 两侧会分叉，而它是会话分组键");

  // turn_index 走账本，**不是自己数请求**。
  check(/nextTurnIndex\(turns\)/.test(clientCode), "turn_index 由 nextTurnIndex 算",
    "← 自己数请求会把失败算进「平均会话轮次」的分子");
  check(!/turnIndex:\s*turns\.length/.test(clientCode), "不按 turns.length 算 turn_index");
  check(/turn_index: turnIndex/.test(clientCode), "请求体里带 turn_index");
  check(/session_id: sessionId/.test(clientCode), "请求体里带 session_id");

  // 口径 F51：这一页不构造那四个事件。
  for (const bad of ["explain_turn", "style_review_result", "regeneration_triggered",
                     "retrieval_executed", "complete_event"]) {
    check(!new RegExp(bad).test(clientCode), `客户端不构造 ${bad}`,
      "← 口径 F51：字段全部产生在服务端，浏览器一个都不知道");
  }

  // 只允许一轮在飞。不流式，第二次提交只会让两个几十秒的请求同时跑，
  // 而先回来的先渲染 —— 轮次顺序会与提问顺序相反。
  check(/if \(!q \|\| !courseId \|\| pending\) return/.test(clientCode),
    "在飞时不接受新提问", "← 不流式，并发会让轮次顺序与提问顺序相反");
  check(/disabled=\{[^}]*pending/.test(client), "在飞时按钮/输入被禁用");

  // 等待可见：没有计时器的话，40 秒的等待与卡死的请求长得一样。
  check(/data-field="elapsed"/.test(client), "有走着的等待计时器");
  check(/别关页面/.test(client), "明说别关页面", "← 不流式，关了这一轮就没了");

  // 用合成课件答的必须被看见（口径 F52 那次真实失败）。
  check(/isFromRequestedCourse\(/.test(clientCode), "按 backend 判是不是用了这门课");
  check(/data-field="wrong-backend"/.test(client), "后端不对时打一条醒目提示");
  check(/role="alert"/.test(client), "那条提示是 alert");

  // 出处点名到页。
  check(/data-field="sources"/.test(client), "显示出处（文件 + 页号）");

  // 失败不占轮次，要说清楚 —— 否则用户以为自己「用掉」了一轮。
  check(/不计入会话轮次/.test(client), "失败那一轮说清它不计入轮次");

  // 档位可见，但**不显示区间数字**（口径 F13）。
  check(/data-field="detail-level"/.test(client), "显示当前讲解详略档位");
  check(!/30\s*[–-]\s*120|15\s*[–-]\s*40|25\s*[–-]\s*55/.test(client),
    "不显示档位的区间数字",
    "← 那些数只存在于 scan/points.py 的 AXIS_POINTS，抄进 TS 就是 F13 那个坑");

  // 直连产品服务端；缺 API base 在页面点名。
  check(/fetch\(`\$\{apiBase\}\/explain`/.test(clientCode), "直连 apiBase/explain");
  check(/no-api-base/.test(pageCode), "缺 NEXT_PUBLIC_API_BASE_URL 时点名说清");
  check(/data-state=\{/.test(pageCode), "页面有终态标记 data-state");

  // DOM 标记（回归工具不按中文文案定位）。
  for (const m of ['data-field="turn"', "data-turn-index", "data-backend",
                   'data-field="answer"', 'data-field="turn-failure"',
                   'data-field="answered-count"']) {
    check(client.includes(m), `DOM 上有 ${m}`);
  }
}

// ================================================================ 十四 · 讲解页 · 这部分没看懂（F4-P2）
console.log("\n\x1b[1m十四 · 讲解页 · 「这部分没看懂」（F4-P2）\x1b[0m");
await (async () => {
  const T = require_(join(outExplain, "turns.js"));
  const client = readFileSync(join(WEB, "app", "explain", "explain-client.tsx"), "utf8");
  const clientCode = stripComments(client);
  const API = join(WEB, "..", "api", "api");

  const KEYS = ["d1:s001", "d2:s001"];
  const answered = (over = {}) => ({
    turnIndex: 1, question: "能源问题有哪些", courseId: "c1", state: "answered",
    answer: T.answerFromResponse({
      text: "x", accepted: true, regenerations: 0, mean_paragraph_chars: 1, detail_level: "P0",
      retrieval: { meta: { backend: "supabase" }, slices: [] },
      round_id: "11111111-1111-4111-8111-111111111111",
      slice_keys: KEYS,
      confusion: { injected: false, note: null, marked_rounds: 0, matched_slice_keys: [] },
      ...over,
    }),
  });

  /** 假 fetch：记下每一次调用，按给定的响应回。`throws` 模拟网络层失败。 */
  function fakeFetch(res) {
    const calls = [];
    const fn = async (url, init) => {
      calls.push({ url, init });
      if (res === "throws") throw new TypeError("Failed to fetch");
      return { ok: res.status < 300, status: res.status,
               json: async () => res.body, text: async () => JSON.stringify(res.body ?? "") };
    };
    return { fn, calls };
  }
  const OK = (created) => ({ status: 200, body: {
    round_id: "11111111-1111-4111-8111-111111111111", created, mark_count_after: 1, slice_count: 2,
    events: { emitted: 1, attempted: 1, failed: 0, event_type: "confusion_mark_created" } } });
  const submit = (fetch, turn = answered()) =>
    T.submitMark({ fetch, apiBase: "https://api.test", jwt: "jwt", turn, sessionId: "sess-1" });

  // -------- 14.1 响应里取出 round_id / slice_keys / confusion
  const a = answered().answer;
  check(a.roundId === "11111111-1111-4111-8111-111111111111", "round_id 从 /explain 响应取出");
  check(eq(a.sliceKeys, KEYS), "slice_keys 原样取出，不重拼", "← 唯一拼法在服务端 slice_key()（F66）");
  check(a.confusion?.injected === false && a.confusion?.markedRounds === 0, "confusion 字段取出");

  // -------- 14.2 空检索轮不渲染；服务端旧时说出来
  check(T.markUnavailable(a) === null, "有 round_id 与切片 → 可标记");
  check(T.markUnavailable(answered({ slice_keys: [],
    confusion: { injected: false, skipped_reason: "本轮没有可标记的切片身份" } }).answer) === "empty-retrieval",
    "slice_keys 为空 → empty-retrieval", "← POST /marks 对空 slice_ids 回 422");
  check(T.markUnavailable(answered({ round_id: undefined, slice_keys: undefined }).answer) === "old-server",
    "响应里没有 round_id → old-server", "← 服务端比客户端旧，要响亮，不能与空检索一样静默");
  check(/markUnavailable\(t\.answer\) === null && \(\s*<div[^>]*data-field="mark"/.test(clientCode),
    "按钮只在 markUnavailable === null 时渲染");
  check(/markUnavailable\(t\.answer\) === "old-server"/.test(clientCode) && /data-field="mark-old-server"/.test(client),
    "服务端旧时页面上有一句话");

  // -------- 14.3 请求体：字段必须是服务端 MarkRequest 认得的（extra="forbid"）
  const mainPy = readFileSync(join(API, "main.py"), "utf8");
  function parseMarkRequest(src) {
    const block = /class MarkRequest\(BaseModel\):[\s\S]*?(?=\n\S)/.exec(src)?.[0] ?? "";
    const fields = {};
    for (const m of block.matchAll(/^ {4}(\w+):[^\n]*$/gm)) fields[m[1]] = !/=\s*None\s*$/.test(m[0]);
    return fields;   // name → required
  }
  function markBodyProblems(body, fields) {
    const p = [];
    const extra = Object.keys(body).filter((k) => !(k in fields));
    if (extra.length) p.push(`服务端不认得的字段 ${JSON.stringify(extra)}（extra=forbid → 422）`);
    for (const [k, req] of Object.entries(fields)) if (req && !(k in body)) p.push(`缺必需字段 ${k}`);
    if (!Array.isArray(body.slice_ids) || body.slice_ids.length === 0) p.push("slice_ids 为空（422）");
    if (!body.session_id) p.push("缺 session_id → 服务端跳过 confusion_mark_created");
    return p;
  }
  const fields = parseMarkRequest(mainPy);
  check(eq(sorted_(Object.keys(fields).filter((k) => fields[k])), ["question", "round_id", "slice_ids"]),
    "从 main.py 读出 MarkRequest 的必需字段是三个", JSON.stringify(fields));
  const body = T.markRequestBody(answered(), "sess-1");
  const bodyProblems = markBodyProblems(body, fields);
  check(bodyProblems.length === 0, "请求体字段全部是 MarkRequest 认得的，且带 session_id", bodyProblems.join("；"));
  check(eq(body.slice_ids, KEYS) && body.round_id === a.roundId && body.course_id === "c1",
    "round_id / slice_ids 取自本轮响应，course_id 取自本轮发出时的课");
  // 反向控制 ×2：多一个字段 / 缺 session_id，判据必须报出来。
  check(markBodyProblems({ ...body, deck_id: "x" }, fields).length > 0,
    "反向控制：多塞一个字段 → 判据报 422 风险");
  {
    const { session_id: _drop, ...noSession } = body;
    check(markBodyProblems(noSession, fields).some((s) => s.includes("session_id")),
      "反向控制：去掉 session_id → 判据报「服务端跳过事件」");
  }

  // -------- 14.4 点击 → 发出请求 → 成功后 disabled
  {
    const f = fakeFetch(OK(true));
    const s = await submit(f.fn);
    check(f.calls.length === 1, "点一次发一个请求", `${f.calls.length} 次`);
    check(f.calls[0]?.url === "https://api.test/marks", "路径是 /marks（无 /api/v1）", f.calls[0]?.url);
    check(f.calls[0]?.init.method === "POST", "方法是 POST");
    check(eq(JSON.parse(f.calls[0]?.init.body ?? "{}"), body), "发出去的请求体就是 markRequestBody");
    check(s.status === "marked" && T.markButtonDisabled(s) === true, "成功 → marked 且 disabled");
    check(T.markButtonDisabled({ status: "sending" }) === true, "在飞时 disabled");
    check(T.markButtonDisabled(undefined) === false, "未点过 → 可点");
    // 2026-09-20：轮次列表从倒序改成正序（聊天式布局），所以下标表达式从
    // `turns.length - 1 - i` 变成了 `index`。**断的是意图，不是那一串字符**：
    // 用户完全可能在下一轮还在飞时回头标记上一轮，所以不能按「最后一轮」标记。
    check(/onClick=\{\(\) => void markConfused\(index\)\}/.test(clientCode),
      "按钮点击接到 markConfused（按那一轮自己的下标）");
    check(!/markConfused\(turns\.length - 1\)/.test(clientCode),
      "不按「最后一轮」标记",
      "← 下一轮在飞时回头标上一轮，会标到错的那一轮上，而页面看起来完全正常");
    check(/submitMark\(\{/.test(clientCode) && /disabled=\{markButtonDisabled\(t\.mark\)\}/.test(clientCode),
      "页面走 submitMark，disabled 由 markButtonDisabled 决定");
    check(/"已记下"/.test(clientCode), "成功后按钮文案是「已记下」");
  }

  // -------- 14.5 失败 → 恢复可点，不静默
  for (const [label, res] of [["HTTP 502", { status: 502, body: { detail: "db down" } }],
                              ["HTTP 422", { status: 422, body: { detail: "slice_ids" } }],
                              ["网络层失败", "throws"]]) {
    const s = await submit(fakeFetch(res).fn);
    check(s.status === "failed" && T.markButtonDisabled(s) === false && !!s.message,
      `${label} → failed、可再点、带原因`, s.message);
  }
  check(/data-field="mark-failure"/.test(client) && /t\.mark\.message/.test(clientCode),
    "失败原因显示在页面上");

  // -------- 14.6 重复提交 created:false —— UI 与首次成功一致，不许多一个分支
  {
    const first = await submit(fakeFetch(OK(true)).fn);
    const again = await submit(fakeFetch(OK(false)).fn);
    check(eq(first, again), "created:false 与 created:true 得到完全相同的状态", JSON.stringify(again));
    check(!/created/.test(String(T.submitMark)) && !/\.created\b/.test(clientCode),
      "submitMark 与页面都不读 created", "← 服务端刻意不返回 409，就是为了前端少这个分支");
  }

  // -------- 14.7 埋点 confusion_mark_created：一个发送方，三个必需字段
  //
  // ⚠️ 与任务书的偏离（见 F4-P2 人工验收清单 §〇）：这条事件 P1 已在服务端
  // `POST /marks` 里发（`_record_mark_event`，带 created 与 course_id 列、带重试）。
  // 前端再经 createEventQueue 发一条 = 每次点击两行。所以这里断的是
  // 「前端**不**另发」+「服务端发事件的前提（session_id）前端给齐了」。
  const REQUIRED = ["mark_count_after", "round_id", "slice_ids"];
  const migDir3 = join(WEB, "..", "supabase", "migrations");
  const sqlKeys3 = parseSqlRequiredKeys(readdirSync(migDir3).filter((f) => f.endsWith(".sql")).sort()
    .map((f) => readFileSync(join(migDir3, f), "utf8")).join("\n"));
  check(eq(sorted_(sqlKeys3?.confusion_mark_created ?? []), REQUIRED),
    "SQL 生效定义里 confusion_mark_created 的必需字段是那三个", JSON.stringify(sqlKeys3?.confusion_mark_created));
  const pyKeys = /"confusion_mark_created":\s*\(([^)]*)\)/.exec(readFileSync(join(API, "events.py"), "utf8"))?.[1] ?? "";
  check(eq(sorted_([...pyKeys.matchAll(/"(\w+)"/g)].map((m) => m[1])), REQUIRED),
    "Python REQUIRED_KEYS 里是同样三个", pyKeys.trim());
  check(/round_id=round_id, slice_keys=req\.slice_ids/.test(mainPy) && /mark_count_after=count/.test(mainPy),
    "服务端事件的三个字段分别来自本轮 round_id / slice_ids / 插完再数的 count");
  check(/session_id=req\.session_id, course_id=req\.course_id/.test(mainPy),
    "服务端事件读的 session_id / course_id 就是请求体里前端带的那两个");

  function extraEmitterProblems(code) {
    return ["confusion_mark_created", "createEventQueue", "recordEvent", "\\.enqueue\\(", "style_events"]
      .filter((pat) => new RegExp(pat).test(code));
  }
  check(extraEmitterProblems(clientCode).length === 0, "讲解页不另发这条事件（不入队、不裸插）",
    extraEmitterProblems(clientCode).join(", "));
  check(extraEmitterProblems(clientCode + '\nqueue.enqueue({ event_type: "confusion_mark_created" });').length > 0,
    "反向控制：往页面里加一次 enqueue → 判据必须红");
  check(!/\/api\/v1\/marks/.test(clientCode), "页面不调 /api/v1/marks");

  // -------- 14.8 文案（口径 F56）：两个按钮的说明放在一起
  const scope = /data-field="mark-scope"[\s\S]*?<\/ul>/.exec(client)?.[0] ?? "";
  check(/这部分没看懂/.test(scope) && /讲太深/.test(scope), "「这部分没看懂」与「讲太深」的说明在同一块里");
  check(/这次/.test(scope) && /以后所有讲解/.test(scope) && /不改/.test(scope),
    "说明里把「这次这几页 / 不改配置」与「以后所有讲解」说开");
  const btn = /<button\s+data-field="mark-confused"[\s\S]*?<\/button>/.exec(client)?.[0] ?? "";
  check(btn !== "" && !/title=/.test(btn), "按钮上没有 tooltip（说明不拆到 tooltip 里）");
})();

// ================================================================ 十五 · 讲解页 · 兜底判断提示与出处（UI 重做）
console.log("\n\x1b[1m十五 · 讲解页 · 兜底判断提示与出处（UI 重做）\x1b[0m");
{
  const T = require_(join(outExplain, "turns.js"));
  const client = readFileSync(join(WEB, "app", "explain", "explain-client.tsx"), "utf8");
  const clientCode = stripComments(client);
  const base = {
    text: "x", accepted: true, regenerations: 0, mean_paragraph_chars: 1, detail_level: "P0",
    round_id: "r", slice_keys: ["d1:s001"],
    retrieval: {
      meta: { backend: "supabase" },
      slices: [
        { source_file: "a.pdf", pages: [3], title: "能源问题", score: 0.61 },
        { source_file: "a.pdf", pages: [7, 3], title: null, score: 0.52 },
      ],
      skipped_pages_in_scope: [{ source_file: "a.pdf", index: 33, kind: "image_only", reason: "r" }],
    },
  };
  const withGap = (gap) => T.answerFromResponse({ ...base, gap });

  // -------- 15.1 出处面板：逐片保留 title / score；按文件合并的 sources 不变
  const a = withGap(undefined);
  check(a.slices.length === 2 && a.slices[0].title === "能源问题" && a.slices[0].score === 0.61
        && a.slices[1].title === null,
    "slices 逐片保留 title / score（缺了是 null，不编）", JSON.stringify(a.slices));
  check(eq(a.sources, [{ sourceFile: "a.pdf", pages: [3, 7] }]), "sources 仍按文件合并页号");
  check(a.skippedPages.length === 1 && a.skippedPages[0].index === 33, "读不了的页原样取出");

  // -------- 15.2 gap：服务端没给就是 null，不编成 covered
  check(a.gap === null, "响应里没有 gap → null", "← 编成 covered 等于替一个没跑的判断背书");
  const g = withGap({ gap_verdict: "partial", gap_implementation: "model", gap_reason: "只讲了成本",
                      gap_error: null, gap_skipped_hint_pages: [33] });
  check(g.gap?.verdict === "partial" && eq(g.gap?.hintPages, [33]) && g.gap?.implementation === "model",
    "gap 取出 verdict / implementation / 限定语页号");
  check(withGap({ gap_verdict: "covered 大概", gap_implementation: "model" }).gap?.verdict === null,
    "认不出的 verdict → null，不就近解释", "← 同 G01");

  // -------- 15.3 提示的判据：只在「判了，且不是 covered」时出现。**两个方向都要能变红**
  function gapNoticeProblems(fn) {
    const p = [];
    const at = (gap) => fn(withGap(gap));
    if (at({ gap_verdict: "covered", gap_implementation: "model" }) !== null) p.push("covered 也提示（恒触发）");
    if (at({ gap_verdict: null, gap_implementation: "error", gap_error: "TimeoutError" }) !== null)
      p.push("判断失败也提示（没判断就没有依据）");
    if (at(undefined) !== null) p.push("服务端没给 gap 也提示");
    for (const v of ["partial", "insufficient"]) {
      const n = at({ gap_verdict: v, gap_implementation: "model", gap_skipped_hint_pages: [33, 34] });
      if (!n || n.verdict !== v || !eq(n.pages, [33, 34])) p.push(`${v} 没提示或页号不对（恒不触发）`);
    }
    return p;
  }
  const real = gapNoticeProblems(T.gapNotice);
  check(real.length === 0, "gapNotice：covered / 失败 / 缺失不提示；partial / insufficient 提示且带页号",
    real.join("；"));
  check(gapNoticeProblems(() => ({ verdict: "partial", pages: [] })).length > 0,
    "反向控制：恒触发的实现 → 判据红", "← 最难发现的那种：每轮多挂一条，用户学会无视");
  check(gapNoticeProblems(() => null).length > 0, "反向控制：恒不触发的实现 → 判据红");

  // -------- 15.4 接线：页面真的按它渲染
  check(/gapNotice\(t\.answer\)/.test(clientCode) && /data-field="gap-notice"/.test(client),
    "讲解页按 gapNotice 渲染提示");
  check(/data-field="slices"/.test(client) && /data-field="turn-diagnostics"/.test(client),
    "出处面板与诊断面板在页面上", "← 诊断字段折叠不删（进度总结 §八 第 7 条）");
}

// ================================================================ 十六 · 开场标签（2026-09-19）
console.log("\n\x1b[1m十六 · 开场标签 → 语气 / 领域 / 先验\x1b[0m");
{
  const T = require_(join(out, "taste.js"));
  const F = require_(join(out, "flow.js"));
  const A = require_(join(out, "axes.js"));

  // -------- 16.1 标签表本身：**没有装饰标签**
  const ids = T.ALL_TAGS.map((x) => x.id);
  check(new Set(ids).size === ids.length, "标签 id 不重复", `${ids.length} 个`);
  // 2026-09-19 去掉「你学什么」：课件讲什么由老师定，不由学生的专业定。
  check(T.BOARDS.length === 2, "两个板块", T.BOARDS.map((b) => b.id).join(" / "));
  const noEffect = T.ALL_TAGS.filter((x) => !x.effect || !x.effect.kind);
  check(noEffect.length === 0, "每个标签都有 effect",
    "← 没有 effect 的标签就是让用户白点一次，正是这次要消灭的「假装选择」",
    noEffect.map((x) => x.id).join(", "));
  const badPrior = T.ALL_TAGS.filter(
    (x) => x.effect.kind === "prior" && !(A.AXES[x.effect.axis] ?? []).includes(x.effect.value));
  check(badPrior.length === 0, "先验标签的取值都在该轴取值域里", "",
    badPrior.map((x) => `${x.id}→${x.effect.value}`).join(", "));

  // -------- 16.2 解析：多数票 / 平票回落 / 同轴矛盾作废 / 未知报出
  function resolveProblems(fn) {
    const p = [];
    if (fn(["tone_peer", "tone_plain", "tone_written"]).tone !== "spoken") p.push("多数票没生效");
    if (fn(["tone_peer", "tone_written"]).tone !== "neutral") p.push("平票没回落中性");
    if (fn([]).tone !== "neutral") p.push("没选语气时不是默认中性");
    if ("field" in fn(["tone_peer"])) p.push("去掉的 field 又回来了（专业猜不出内容类型）");
    if (fn(["stuck_short", "stuck_slow"]).priors.detail_level !== undefined)
      p.push("同轴矛盾没有作废该轴先验（替用户猜了一个）");
    if (!fn(["nope_not_a_tag"]).unknown.includes("nope_not_a_tag")) p.push("未知标签没被报出来");
    if (fn(["stuck_why"]).priors.depth !== "why") p.push("先验没取出来");
    return p;
  }
  const resolveReal = resolveProblems(T.resolveTaste);
  check(resolveReal.length === 0,
    "resolveTaste：多数票 · 平票回落中性 · 同轴矛盾作废 · 未知标签报出", resolveReal.join("；"));
  check(resolveProblems(() => ({ tone: "spoken", priors: {}, unknown: [] })).length > 0,
    "反向控制：恒返回同一个语气的实现 → 判据红");
  check(resolveProblems((sel) => {
    const r = T.resolveTaste(sel);
    if (sel.includes("stuck_short") && sel.includes("stuck_slow")) r.priors.detail_level = "P3";
    return r;
  }).length > 0, "反向控制：同轴矛盾时替用户猜一个 → 判据红");

  // -------- 16.3 排步：先验用来挑更有信息量的一对，**不用来跳过**
  function planProblems(planFn) {
    const p = [];
    const cases = [{}, { detail_level: "P0" }, { depth: "what" },
      { detail_level: "P3", depth: "derivation", use_examples: "more", structure: "buildup_first" }];
    for (const priors of cases) {
      let steps;
      try { steps = planFn(priors); } catch (e) { p.push(`${JSON.stringify(priors)} 抛了 ${e.message}`); continue; }
      if (steps.length !== F.TOTAL_STEPS) p.push(`${JSON.stringify(priors)} 排出 ${steps.length} 步`);
      const axes = new Set(steps.map((s) => s.axis));
      for (const axis of Object.keys(A.AXES)) {
        if (!axes.has(axis)) p.push(`${JSON.stringify(priors)} 少问了 ${axis}`);
      }
    }
    const withPrior = planFn({ detail_level: "P0" });
    const firstDetail = withPrior.findIndex((s) => s.axis === "detail_level");
    const firstOther = withPrior.findIndex((s) => s.axis !== "detail_level");
    if (firstOther > firstDetail) p.push("有先验的轴没被排到没有先验的轴后面");
    return p;
  }
  const planReal = planProblems(T.plan);
  check(planReal.length === 0, "plan：恒 6 步 · 四条轴一条不少 · 有先验的轴排后面", planReal.join("；"));
  check(planProblems((priors) => T.plan(priors).filter((s) => priors[s.axis] === undefined)).length > 0,
    "反向控制：拿先验跳过比较 → 判据红",
    "← 跳过等于让「他说的」直接变成配置，而那正是不可信的那一半");

  // -------- 16.35 空先验时必须与旧的固定顺序**逐步相同**
  //
  // 对照臂（pairwise）走的就是这条路径。两者一旦不一致，A5 比的就不再是
  // 「标签先验有没有用」，而是「两套不同的提问顺序」——而这不会报错。
  {
    const picksCases = [{}, { detail_level: "P0" }, { detail_level: "P3" },
      { depth: "what" }, { depth: "derivation" }];
    const diffs = [];
    for (let i = 1; i <= F.TOTAL_STEPS; i++) {
      for (const picks of picksCases) {
        const legacy = F.stepAt(i, picks);
        const adaptive = T.stepAtWithPriors(i, picks, {});
        if (legacy.pairId !== adaptive.pairId || legacy.axis !== adaptive.axis) {
          diffs.push(`第 ${i} 步 picks=${JSON.stringify(picks)}：固定 ${legacy.pairId} vs 自适应 ${adaptive.pairId}`);
        }
      }
    }
    check(diffs.length === 0, "空先验 → 与旧的固定顺序逐步相同（对照臂体验没变）", "", diffs.join("；"));
  }

  // -------- 16.4 覆盖的样例集合必须与固定顺序**完全相同**（pairs.json 就那 8 对）
  const reach = T.reachablePairIds();
  check(eq(sorted_(reach), sorted_([...F.REACHABLE_PAIR_IDS])),
    "自适应排法覆盖的 pair_id 与旧的固定顺序一致", `${reach.length} 对`,
    `${JSON.stringify(reach)}\n      vs ${JSON.stringify([...F.REACHABLE_PAIR_IDS])}`);

  // -------- 16.5 先验不进配置
  let threw = false;
  try { T.configFromPicksAndTone({ detail_level: "P0" }, "spoken"); } catch { threw = true; }
  check(threw, "6 步没走完就取配置 → 抛异常，不拿先验补齐",
    "← 补齐会让「他选的」与「他说的」在库里长得一模一样");
}

// ================================================================ 十七 · 会话工作区（2026-09-20）
//
// 这一节盯的是**改版之后新出现的几处「两份可以漂的东西」**。
// 它不测 UI 长什么样——那要浏览器，走 verify:browser。
console.log("\n\x1b[1m十七 · 会话工作区 · 抄件与纪律\x1b[0m");
{
  const wsPath = join(WEB, "app", "courses", "[id]", "workspace-client.tsx");
  const coursePath = join(WEB, "app", "courses", "[id]", "page.tsx");
  const ws = readFileSync(wsPath, "utf8");
  const coursePage = readFileSync(coursePath, "utf8");
  const wsCode = stripComments(ws);
  const coursePageCode = stripComments(coursePage);
  const explainCode = stripComments(
    readFileSync(join(WEB, "app", "explain", "explain-client.tsx"), "utf8"));

  // -------- 17.1 模型名那份抄件与 llm.py 的默认值逐字相同
  //
  // 写这一行的时候真的漂过一次（"Claude Sonnet 4.5" vs claude-sonnet-5）。
  // 抄件不靠小心维护，靠这一条。
  const llm = readFileSync(join(WEB, "..", "explain", "explain", "llm.py"), "utf8");
  const pyModel = /name:\s*str\s*=\s*"([^"]+)"/.exec(llm)?.[1] ?? null;
  check(pyModel !== null, "从 llm.py 解析出默认模型 id", String(pyModel));
  const tsModel = /EXPLAIN_MODEL_LABEL\s*=\s*"([^"]+)"/.exec(coursePageCode)?.[1] ?? null;
  check(tsModel !== null, "从课程页解析出模型标签", String(tsModel));
  check(tsModel === pyModel, "输入框下面那个模型名与 llm.py 的默认值相同",
    `页面 ${tsModel} vs llm.py ${pyModel}`);
  // 反向控制：解析器要真的分得开，不是两边都解析成 null 然后「相等」。
  check(pyModel !== null && tsModel !== null && pyModel.length > 3,
    "反向控制：两个解析器都拿到了非空值",
    "← 都解析成 null 的话，上一条会在 null === null 上平凡通过");

  // -------- 17.2 换会话必须重挂载，不能用 effect 同步 props 到 state
  check(/key=\{activeId\}/.test(wsCode), "换会话时聊天组件按 activeId 重挂载",
    "← 不重挂载的话，旧会话的轮次还在屏幕上而输入框已经指向新会话了");

  // -------- 17.3 会话行由浏览器建，消息**不由**浏览器写
  check(/from\("conversations"\)[\s\S]{0,200}\.insert\(/.test(wsCode),
    "会话行由浏览器直写（标题是纯 UI 状态）");
  check(!/from\("messages"\)[\s\S]{0,120}\.insert\(/.test(wsCode),
    "浏览器不写 messages",
    "← round_id / turn_index / 答案正文只有那一次 run_round 知道（同口径 F51）");

  // -------- 17.4 读消息失败**不能**渲染成空会话
  check(/setError\(`读这个会话的消息失败/.test(wsCode),
    "读消息失败要说出来，不悄悄显示成空会话",
    "← 2026-09-19 在课件列表上踩过同一个形态（42703 被吞成「还没有课件」）");

  // -------- 17.5 课件读失败与「真的没有」分开
  check(/data-field="documents-error"/.test(coursePageCode), "课件/对话读失败有自己的提示");

  // -------- 17.6 去掉的那些东西**真的去掉了**
  check(!/AppNav/.test(coursePageCode) && !/AppNav/.test(wsCode),
    "课程工作区里没有顶栏", "← 2026-09-20 去掉");
  check(!/empty-turns/.test(explainCode), "「还没有提问」那张卡已经去掉");
  check(!/type="file"[\s\S]{0,200}image/.test(wsCode) && !/accept="image/.test(wsCode),
    "提问框不提供上传图片");

  // -------- 17.7 输入框仍然守着原来那几条
  check(/data-field="elapsed"/.test(explainCode), "等待计时器还在",
    "← 没有它，40 秒的等待与一个卡死的请求长得一模一样");
  check(/conversation_id: conversationId/.test(explainCode), "请求体里带 conversation_id");
}

// ================================================================ 十八 · 讲法矫正（2026-09-20）
//
// 成对比较撤掉之后，风格矫正搬到了讲解里。这一节盯的是**搬家过程中最容易错的
// 那几条**——它们错了都不会报错，只会让用户的配置悄悄变成别的样子。
console.log("\n\x1b[1m十八 · 讲法矫正 · 搬进讲解之后\x1b[0m");
{
  const routeSrc = readFileSync(join(WEB, "app/api/style/from-text/route.ts"), "utf8");
  const route = stripComments(routeSrc);
  const nudge = stripComments(readFileSync(join(WEB, "app/explain/style-nudge.tsx"), "utf8"));
  const onboarding = stripComments(readFileSync(join(WEB, "app/onboarding/page.tsx"), "utf8"));
  const taste = stripComments(readFileSync(join(WEB, "app/onboarding/taste/taste-client.tsx"), "utf8"));

  // -------- 18.1 **没提到的轴保持原样，不回落默认档**
  //
  // 这是整节最要紧的一条。用户说「再短一点」，描述里只提到详细程度；
  // 以默认档为基线的话另外三条会被悄悄重置——他改了一件事，四件事全变了，
  // 而页面上什么都不会说。
  check(/const config: StyleConfig = \{ \.\.\.base \}/.test(route),
    "矫正以**用户当前配置**为基线", "← 用 DEFAULTS 当基线会把没提到的轴悄悄重置");
  check(/value: base\[axis\], source: "default"/.test(route),
    "未提到的轴取当前值，不取默认值");
  check(!/const config: StyleConfig = \{ \.\.\.DEFAULTS \}/.test(route),
    "反向控制：基线不是 DEFAULTS");

  // -------- 18.2 臂的门禁去掉了（成对比较撤了，留着它每个人都会撞上 409）
  check(!/f1_arm !== "text"/.test(route), "不再按臂拦下这个端点");
  check(!/status: 409/.test(route), "没有「你在成对比较臂」那条 409");

  // -------- 18.3 key 不许是公开变量
  check(/process\.env\.GEMINI_API_KEY/.test(route), "key 从 GEMINI_API_KEY 读");
  check(!/NEXT_PUBLIC_GEMINI/.test(routeSrc), "key **不带 NEXT_PUBLIC_ 前缀**",
    "← 带了就会被内联进浏览器 bundle，任何人都能烧这个额度");

  // -------- 18.4 顺序：先写标签，再让服务端读着刚写完的那一行去映射那句话
  const iTags = nudge.indexOf('.from("style_config")');
  const iText = nudge.indexOf("/api/style/from-text");
  check(iTags > -1 && iText > -1 && iTags < iText,
    "标签先写库，那句话后调",
    "← 反了的话那句话以旧配置为基线，刚点的标签当场被盖掉");

  // -------- 18.5 看过讲解才提示
  check(/answered >= 1/.test(nudge), "第一次拿到讲解之后才提示",
    "← 没看过讲解就没有可反应的东西，那正是成对比较被撤掉的理由");
  check(/localStorage/.test(nudge) && /catch/.test(nudge),
    "「已看过」记在本地且读写都包了 try/catch",
    "← 隐私模式下 localStorage 会抛；抛了就再提示一次，不该白屏");

  // -------- 18.6 开场只剩标签页，且它自己写完 onboarded_at
  check(/redirect\("\/onboarding\/taste"\)/.test(onboarding), "开场一律进标签页");
  check(!/f1_arm/.test(onboarding), "开场不再按臂分流");
  check(/onboarded_at: new Date\(\)/.test(taste), "标签页自己写 onboarded_at",
    "← 不写的话首页会把人一直送回 onboarding");
  check(/configFromTaste\(/.test(taste), "标签真的变成四条轴",
    "← 不写轴的话每个新用户拿到的都是默认档");
}

// ================================================================ 十九 · 连不上时说什么（2026-09-21）
//
// 冷启动那次教训：建课报 `TypeError: Failed to fetch`，而前端那句话说
// 「可能是它没部署，或 CORS 里没有本站来源」——**两个猜测都不对**
// （服务部署好了、来源也在白名单里），真正的原因是 Render 免费层休眠。
// 于是排错时间花在翻控制台和查白名单上。
//
// 这一节钉的是：失败时**不猜**，并且给出能一句话分清楚的办法。
console.log("\n\x1b[1m十九 · 连不上讲解服务时说什么\x1b[0m");
{
  const newCourse = stripComments(readFileSync(join(WEB, "app/components/new-course.tsx"), "utf8"));
  const warmup = stripComments(readFileSync(join(WEB, "app/components/api-warmup.tsx"), "utf8"));
  const mainPy = readFileSync(join(WEB, "..", "api", "api", "main.py"), "utf8");

  // -------- 19.1 那句猜错的话没了
  check(!/可能是它没部署/.test(newCourse), "建课失败不再猜「可能是它没部署」",
    "← 2026-09-21 实测：这个猜测把排错引向了 Render 控制台，而服务是好的");
  check(/休眠/.test(newCourse), "失败文案先说休眠（最常见的那一种）");

  // -------- 19.2 说得出「本站来源」是什么，别让人去猜
  check(/window\.location\.origin/.test(newCourse) && /window\.location\.origin/.test(warmup),
    "失败文案打印本站来源",
    "← 白名单是逐字匹配的；不打印的话「我的来源是什么」只能靠猜");

  // -------- 19.3 /health 把白名单说出来，于是那个问题有事实可查
  check(/"cors_allow_origins"/.test(mainPy), "/health 报出 cors_allow_origins",
    "← 这些来源本来就是公开网址，说出来换到的是一个事实而不是一道猜谜");

  // -------- 19.4 叫醒要真的重试，而且**不报喜**
  check(/BACKOFF_MS/.test(warmup) && /BACKOFF_MS\.length/.test(warmup),
    "叫醒按递增间隔重试", "← 冷启动实测几十秒，密集重试只是白发请求");
  check(/if \(state\.kind === "ready"\) return null/.test(warmup),
    "醒着时页面上不多一行字", "← 一切正常时不该报喜");
  check(/data-field="api-waking"/.test(warmup) && /data-field="api-unreachable"/.test(warmup),
    "「在唤醒」与「叫不醒」是两个不同的终态",
    "← 压成一个的话，等一会儿有用和等多久都没用就分不开了");
}

rmSync(out, { recursive: true, force: true });
rmSync(outUpload, { recursive: true, force: true });
rmSync(outExplain, { recursive: true, force: true });

console.log(`\n${failed === 0 ? "\x1b[32m" : "\x1b[31m"}${passed} 绿 / ${failed} 红\x1b[0m\n`);
process.exit(failed === 0 ? 0 : 1);
