/**
 * 把 `api/` 的断言接进 `npm run verify:guards`。
 *
 *   npm run verify:api
 *
 * ## 为什么这条**要求 venv，而不是没有就跳过**
 *
 * `explain/` 的断言能做到零依赖（核心本就零第三方依赖），所以
 * `verify_explain_py.mjs` 用 stdlib 跑法就够了。**`api/` 做不到**：
 * 它要 FastAPI 验路由、要 `cryptography` 真的做 ES256 验签——
 * 而「用真私钥签出 token 再验」正是那组断言的价值所在（打桩掉就只剩
 * 「调用了 verify」，证明不了它会拒绝什么）。
 *
 * 于是只剩两条路：**没 venv 就跳过**，或**没 venv 就判红**。取后者。
 * 跳过了却退出 0 的检查与它防的失败是同一类东西（F15 的 `--allow-offline`、
 * F42 的「测试存在 ≠ 测试被执行」是同一条）。
 *
 * 一次性安装：
 *
 *     cd api && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
 */

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const API = join(dirname(WEB), "api");
const PY = join(API, ".venv", "bin", "python");

/** 期望的最少用例数。「0 通过」与「26 通过」在退出码上没有区别。
 *
 * ⚠️ **加了用例就把这个数抬上去。** 不抬的话它会慢慢退化成一条永远满足的下界，
 * 而它存在的全部理由是「有一批用例被 collect 漏了」这件事会静默发生
 * （import 错误让整个文件被跳过时，pytest 仍然退出 0 报「N passed」）。
 *
 * 变更史：26（阶段 2/3）→ 46（阶段 4 摄入）→ 101（阶段 5 检索 + 阶段 6 埋点）
 * → 118（Render 首次部署后的补正：构建标识 + extra=forbid + hit_documents）
 * → 163（阶段 6 第一批：上传两个事件 + 失败分类闭集 + 先查后提示 + CORS）
 * → 172（人工验收补正：埋点结果进响应 + 字段落在哪的断言）。
 */
const MIN_CASES = 172;

let passed = 0;
let failed = 0;
const check = (cond, name, detail = "") => {
  cond ? passed++ : failed++;
  console.log(`  ${cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"} ${name}${detail ? "  " + detail : ""}`);
};

console.log("\n\x1b[1mapi · 鉴权 + 读配置 + 生成接线（阶段 2 / 3）\x1b[0m");

check(
  existsSync(PY),
  "api/.venv 存在",
  existsSync(PY)
    ? ""
    : '← 没有就判红，不跳过。装一次：cd api && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"',
);

if (existsSync(PY)) {
  let out = "";
  let code = 0;
  try {
    // 不传 `-q`：`api/pyproject.toml` 的 addopts 已经有一个，再加就成了 `-qq`，
    // 而 `-qq` **把「N passed」那行也吞掉**——于是下面解析出 0，
    // 「全绿」照样成立（0 条没有失败）。条数断言正是为这种情形准备的，
    // 它确实红了一次，所以这行注释是事后写的，不是预见。
    out = execFileSync(PY, ["-m", "pytest", "-p", "no:cacheprovider", "--no-header"], {
      cwd: API,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (e) {
    code = e.status ?? 1;
    out = `${e.stdout ?? ""}${e.stderr ?? ""}`;
  }

  const m = /(\d+)\s+passed/.exec(out);
  const failedM = /(\d+)\s+failed/.exec(out);
  const green = m ? Number(m[1]) : 0;

  // 「退出码 0」不够：0 条用例也是退出码 0。三个条件缺一不可。
  check(code === 0 && !failedM && green > 0, "pytest 全绿", code === 0 ? `${green} 通过` : out.slice(-900));
  check(green >= MIN_CASES, `跑到了至少 ${MIN_CASES} 条`, `实得 ${green} ← 少于预期说明有用例没被跑到`);
}

console.log(`\n${failed === 0 ? "\x1b[32m" : "\x1b[31m"}${passed} 绿 / ${failed} 红\x1b[0m\n`);
process.exit(failed === 0 ? 0 : 1);
