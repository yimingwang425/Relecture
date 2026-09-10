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
  check(
    typeof TEXT_ARM_CALL.effort === "string" && TEXT_ARM_CALL.effort.length > 0,
    "TEXT_ARM_CALL 有 effort",
    TEXT_ARM_CALL.effort,
  );

  // 单测能证明这个对象存在，证明不了 **route 真的用了它**——
  // F3a 那次 59 条全绿而 request_signature 被改名没人发现，就是这个形态（通则七 #7）。
  // 所以这里读源码：请求参数必须从 TEXT_ARM_CALL 取，不能是字面量。
  for (const field of ["model", "max_tokens", "effort", "thinking"]) {
    check(
      routeSrc.includes(`TEXT_ARM_CALL.${field}`),
      `route.ts 的 ${field} 取自 TEXT_ARM_CALL`,
      "← 写成字面量的话，发出去的和记下来的会各自漂",
    );
  }
  check(
    /model:\s*"claude-/.test(routeSrc) === false,
    "route.ts 里没有硬编码的模型 id",
    "← 有的话就是第二处出处",
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
  // 判据的分辨力：这些标记在语料里确实存在（上面数过），所以「渲染出来了」才有信息量。
  check(all.includes("<strong>"), "粗体渲染成 <strong>");
  check(all.includes("<code"), "代码渲染成 <code>");
  check(all.includes("<li>"), "列表渲染成 <li>");
  check(all.includes("<blockquote"), "引用渲染成 <blockquote>");
  check(all.includes("<em>"), "斜体渲染成 <em>  ← 语料里只有 3 处，漏了就会剩下裸星号");

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

  /** 纯函数，好让下面的反向控制直接喂一段改过的 SQL。 */
  function parseSqlRequiredKeys(sql) {
    const block = /create or replace function public\.f1_event_required_keys[\s\S]*?\$\$;/.exec(sql)?.[0];
    if (!block) return null;
    const out = {};
    for (const m of block.matchAll(/when\s+'(\w+)'\s*then\s*array\[([^\]]*)\]/g)) {
      out[m[1]] = [...m[2].matchAll(/'(\w+)'/g)].map((k) => k[1]);
    }
    return out;
  }

  const sqlKeys = parseSqlRequiredKeys(migSrc);
  // 判据自身的分辨力：解析失败会得到 null 或空对象，而空对象之间的比对必然相等。
  check(sqlKeys !== null, "迁移里找到 f1_event_required_keys");
  check(Object.keys(sqlKeys ?? {}).length === 5, "SQL 解析出 5 个 event_type", `实得 ${Object.keys(sqlKeys ?? {}).length}`);
  check(ev.EVENT_TYPES.length === 5, "TS 的 EVENT_TYPES 是 5 个", ev.EVENT_TYPES.join(" · "));

  /** 返回不一致的说明。空数组 = 两份相同。 */
  function compareKeys(tsKeys, sql) {
    const problems = [];
    const tsTypes = sorted_(Object.keys(tsKeys));
    const sqlTypes = sorted_(Object.keys(sql ?? {}));
    if (!eq(tsTypes, sqlTypes)) problems.push(`event_type 集合：TS ${JSON.stringify(tsTypes)} ≠ SQL ${JSON.stringify(sqlTypes)}`);
    for (const t of tsTypes) {
      if (!eq(sorted_(tsKeys[t] ?? []), sorted_(sql?.[t] ?? []))) {
        problems.push(`${t}：TS ${JSON.stringify(tsKeys[t])} ≠ SQL ${JSON.stringify(sql?.[t])}`);
      }
    }
    return problems;
  }

  const keyProblems = compareKeys(ev.REQUIRED_PAYLOAD_KEYS, sqlKeys);
  check(keyProblems.length === 0, "TS 与 SQL 的必需字段表逐项一致", keyProblems.join("；"));

  // 反向控制：从 SQL 那份里拿掉 left_variant，比对**必须**报出来。
  const tamperedSql = migSrc.replace(
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

  // -------- 7.2 五个构造函数产出的 payload 必带必需字段
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

rmSync(out, { recursive: true, force: true });

console.log(`\n${failed === 0 ? "\x1b[32m" : "\x1b[31m"}${passed} 绿 / ${failed} 红\x1b[0m\n`);
process.exit(failed === 0 ? 0 : 1);
