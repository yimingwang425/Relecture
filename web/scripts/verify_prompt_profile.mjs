/**
 * 把 `prompt_profile_id` 的断言接进**真的会被跑的那条链**。
 *
 *   npm run verify:prompt-profile        （已并进 npm run verify:guards）
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
const TEST = join(ROOT, "explain", "tests", "test_prompt_profile.py");

/** 期望的最少断言条数。少于它说明有用例没被跑到——那是静默的覆盖率损失。 */
const MIN_CASES = 14;

let passed = 0;
let failed = 0;

function check(cond, name, detail = "") {
  if (cond) passed++;
  else failed++;
  console.log(`  ${cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"} ${name}${detail ? "  " + detail : ""}`);
}

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

console.log("\n\x1b[1mprompt_profile_id（口径 F04 / F39）\x1b[0m");

const real = runPython(TEST);

// 1 · 解释器必须在。拿不到就判失败，不跳过。
check(
  real.code !== null,
  "python3 可用",
  real.code === null ? "← 拿不到解释器即判失败，不跳过（跳过等于退出 0 的假绿）" : "",
);

if (real.code !== null) {
  const t = tally(real.out);

  // 2 · 解析得出条数。解析不出来时**不许**当成 0 红。
  check(t !== null, "跑法输出了合计行", t === null ? real.out.slice(-400) : `${t.green} 绿 / ${t.red} 红`);

  check(real.code === 0 && t?.red === 0, "断言全绿", real.code === 0 ? "" : real.out.slice(-800));

  // 3 · 条数达标。「0 绿 / 0 红」与「14 绿 / 0 红」在退出码上没有区别。
  check(
    (t?.green ?? 0) >= MIN_CASES,
    `跑到了至少 ${MIN_CASES} 条`,
    `实得 ${t?.green ?? 0} ← 少于预期说明有用例没被跑到`,
  );

  // 4 · 反向控制：改坏一条断言，必须变红。
  //
  // 改的是**副本**，放在同一个目录里（相对 import 才成立），跑完删掉——
  // 与 verify_build_guards.mjs 里那个 canary 页面同一个做法。
  const canary = join(ROOT, "explain", "tests", "_prompt_profile_canary.py");
  try {
    const src = readFileSync(TEST, "utf8");
    const broken = src.replace("assert len(ids) == 12", "assert len(ids) == 11");
    if (broken === src) {
      check(false, "反向控制的锚点还在", "← 被改的那行找不到了，这条反向控制已失效");
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

console.log(`\n${failed === 0 ? "\x1b[32m" : "\x1b[31m"}${passed} 绿 / ${failed} 红\x1b[0m\n`);
process.exit(failed === 0 ? 0 : 1);
