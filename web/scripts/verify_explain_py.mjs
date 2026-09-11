/**
 * 把 `explain/` 侧的断言接进**真的会被跑的那条链**。
 *
 *   npm run verify:explain-py            （已并进 npm run verify:guards）
 *
 * 2026-09-11 从 `verify_prompt_profile.mjs` 改名并推广到多个文件：
 * F1→F3 接线把「档位 → StyleConfig」和 B 类渲染搬进了 `explain/`，
 * 那批「搬家没改变任何字节」的断言与 `prompt_profile_id` 是同一处境——
 * 没有 venv 的机器上跑不了，就等于不存在。
 *
 * ## 为什么需要这个包装
 *
 * 断言写在 `explain/tests/test_prompt_profile.py`，而 `explain/` 的 venv 是本地产物
 * （`.gitignore` 挡在仓库外）。**没有 venv 的机器上 `import pytest` 直接失败**——
 * 那 14 条会以「有测试」的样子存在而**从不执行**。
 *
 * 这与 F14 说的「承诺存在 ≠ 承诺被测量」是同一件事，换成了
 * 「测试存在 ≠ 测试被执行」；也与 F16 的「一个永远不命中的判据不是判据」同源。
 * 所以那个测试文件带了一个 stdlib 跑法，本脚本调它，并把结果并进 `verify:guards`。
 *
 * ## 三条设计约束（各自对着一个「假绿」）
 *
 * 1. **拿不到 `python3` 即判失败，不跳过。** 跳过了却退出 0 的检查，
 *    与它防的失败是同一类东西（同 `verify_git_identity.mjs` 的 `--allow-offline`）。
 * 2. **断言执行条数 ≥ 预期。** 一个「什么都没跑」的跑法退出码同样是 0，
 *    而「0 绿 / 0 红」在退出码上与「14 绿 / 0 红」长得一模一样。
 * 3. **反向控制**：把测试文件复制一份、改坏一条断言再跑，**必须变红**。
 *    少了它，一个恒打印「14 绿」的跑法能让上面两条永远绿。
 */

import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ROOT = dirname(WEB);
/**
 * 纳入本链的 explain 侧测试文件，以及各自的**最少断言条数**。
 *
 * 条数写死是刻意的：一个「什么都没跑」的跑法退出码同样是 0，
 * 「0 绿 / 0 红」与「14 绿 / 0 红」在退出码上没有区别。
 * 删掉用例要连这个数一起改——那一下是显式的，不会静默发生。
 */
const SUITES = [
  { file: "test_prompt_profile.py", minCases: 14, what: "prompt_profile_id（F04 / F39）" },
  { file: "test_style_axes.py", minCases: 14, what: "四条轴搬家后逐字节不变（F19 / F45 / F46）" },
];

let passed = 0;
let failed = 0;

function check(cond, name, detail = "") {
  if (cond) passed++;
  else failed++;
  console.log(`  ${cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"} ${name}${detail ? "  " + detail : ""}`);
}

const testPath = (f) => join(ROOT, "explain", "tests", f);

/** 跑一个 python 文件，返回 `{ code, out }`。**不吞异常**——拿不到解释器要能看出来。 */
function runPython(file) {
  try {
    const out = execFileSync("python3", [file], {
      cwd: ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, out };
  } catch (e) {
    if (e.code === "ENOENT") return { code: null, out: "找不到 python3" };
    return { code: e.status ?? 1, out: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

/** 从 `N 绿 / M 红` 里取两个数。取不到返回 null——「解析不出来」不等于「0 红」。 */
function tally(out) {
  const m = /(\d+)\s*绿\s*\/\s*(\d+)\s*红/.exec(out.replace(/\x1b\[[0-9;]*m/g, ""));
  return m ? { green: Number(m[1]), red: Number(m[2]) } : null;
}

const probe = runPython(testPath(SUITES[0].file));
check(
  probe.code !== null,
  "python3 可用",
  probe.code === null ? "← 拿不到解释器即判失败，不跳过（跳过等于退出 0 的假绿）" : "",
);

if (probe.code !== null) {
  for (const suite of SUITES) {
    console.log(`\n\x1b[1m${suite.what}\x1b[0m  \x1b[2m${suite.file}\x1b[0m`);
    const real = suite.file === SUITES[0].file ? probe : runPython(testPath(suite.file));
    const t = tally(real.out);

    check(t !== null, "跑法输出了合计行", t === null ? real.out.slice(-400) : `${t.green} 绿 / ${t.red} 红`);
    check(real.code === 0 && t?.red === 0, "断言全绿", real.code === 0 ? "" : real.out.slice(-800));
    check(
      (t?.green ?? 0) >= suite.minCases,
      `跑到了至少 ${suite.minCases} 条`,
      `实得 ${t?.green ?? 0} ← 少于预期说明有用例没被跑到`,
    );

    // 反向控制：改坏一条断言，必须变红。改的是**副本**，放在同一个目录里
    // （相对 import 才成立），跑完删掉——与 verify_build_guards.mjs 的 canary 同一做法。
    const canary = testPath(`_canary_${suite.file}`);
    try {
      const src = readFileSync(testPath(suite.file), "utf8");
      const broken = src.replace(/assert /, "assert not ");
      if (broken === src) {
        check(false, "反向控制的锚点还在", "← 文件里找不到 assert，这条反向控制已失效");
      } else {
        writeFileSync(canary, broken, "utf8");
        const r = runPython(canary);
        const bt = tally(r.out);
        check(
          r.code !== 0 && (bt?.red ?? 0) > 0,
          "反向控制：改坏一条断言 → 退出码非 0 且报出红",
          r.code !== 0 ? `退出码 ${r.code}，红 ${bt?.red ?? "?"}` : "仍然全绿 —— 这条跑法是空转的",
        );
      }
    } finally {
      rmSync(canary, { force: true });
    }
  }
}

console.log(`\n${failed === 0 ? "\x1b[32m" : "\x1b[31m"}${passed} 绿 / ${failed} 红\x1b[0m\n`);
process.exit(failed === 0 ? 0 : 1);
