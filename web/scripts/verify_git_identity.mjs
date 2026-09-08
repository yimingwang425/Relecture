/**
 * 提交作者身份检查 —— 防的是**一种不报错的失败**。
 *
 *   node scripts/verify_git_identity.mjs [--allow-offline]
 *
 * ## 它防什么
 *
 * Vercel 部署前会把提交的作者邮箱反查成 GitHub 账号。查不到就**拦下部署**，
 * 而拦下的表现是「什么也没发生」：代码没问题、构建根本没跑、
 * 没有一条错误指向这里。你会以为自己在看新版本，其实看的是上一次部署。
 * 通则六的形态——会静默改变结果的问题，比会报错的危险得多。
 *
 * 2026-09-08 实际踩到：GitHub 主邮箱从学校邮箱换成个人邮箱、学校邮箱被移除，
 * 而仓库的 `user.email` 还是老的。
 *
 * ## 为什么查的是 GitHub API，不是比对一个写死的邮箱
 *
 * 比对常量的版本在这次事故里**会是绿的**：邮箱没变，变的是「GitHub 还认不认它」。
 * 一个在事故发生时照样绿的检查，就是没有检查。
 *
 * `gh api repos/…/commits/<sha>` 的 `author.login` **正是 Vercel 反查的同一个信号**：
 * 归属得到账号时是 login，归属不到时是 `null`。
 *
 * 另一个后果值得知道：**归属是按账号当前的已验证邮箱实时算的，不是提交时冻结的。**
 * 所以换邮箱之后，历史上用旧邮箱的提交会一起变成不可归属——
 * 新提交不受影响，但**回滚或重新部署那些提交会撞同一堵墙**。
 *
 * ## 离线时不静默放行
 *
 * 没有 gh / 未登录时，网络那条查不了。默认**判失败**，
 * 要放行必须显式加 `--allow-offline`。一个跳过了却仍然退出 0 的检查，
 * 与它防的那个失败是同一类东西。
 */

import { execFileSync } from "node:child_process";

/**
 * 反向控制的锚点：一个**已知反查不到账号**的历史提交（作者是已从 GitHub 移除的
 * 学校邮箱）。每次运行都要求它返回 null——否则说明这个检查读到的不是活信号，
 * 「HEAD 可归属」那条断言也就什么都证明不了（通则七 #4）。
 */
const STALE_ANCHOR = { sha: "3c7ffba", note: "作者是已从 GitHub 移除的学校邮箱" };

const ALLOW_OFFLINE = process.argv.includes("--allow-offline");

let passed = 0;
let failed = 0;
function check(cond, name, okDetail = "", badDetail = "") {
  const detail = cond ? okDetail : badDetail;
  if (cond) passed++;
  else failed++;
  console.log(`  ${cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"} ${name}${detail ? "  " + detail : ""}`);
}

function git(...args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function ghJson(path) {
  const out = execFileSync("gh", ["api", path], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  return JSON.parse(out);
}

function repoSlug() {
  const url = git("remote", "get-url", "origin");
  const m = url.match(/github\.com[:/](.+?)(?:\.git)?$/);
  if (!m) throw new Error(`解析不出 GitHub 仓库：${url}`);
  return m[1];
}

console.log("\n\x1b[1m三 · 提交作者身份（Vercel 部署的前置条件）\x1b[0m");

// ---------------------------------------------------------------- 本地
const configEmail = (() => {
  try {
    return git("config", "user.email");
  } catch {
    return "";
  }
})();
const headEmail = git("log", "-1", "--format=%ae");
const headSha = git("rev-parse", "--short", "HEAD");

check(configEmail !== "", "git config user.email 有值", configEmail, "未设置");
check(
  configEmail === headEmail,
  "HEAD 的作者邮箱与当前配置一致",
  headEmail,
  `配置 ${configEmail} ≠ HEAD ${headEmail} —— 改了配置但没重写提交？`,
);

// ---------------------------------------------------------------- 网络
let gh = true;
try {
  execFileSync("gh", ["auth", "status"], { stdio: "ignore" });
} catch {
  gh = false;
}

if (!gh) {
  const msg = "gh 不可用或未登录，查不了 GitHub 归属";
  if (ALLOW_OFFLINE) {
    console.log(`  \x1b[33m—\x1b[0m ${msg}（--allow-offline，本次放行）`);
  } else {
    check(false, msg, "", "这条是主检查。要跳过必须显式加 --allow-offline");
  }
} else {
  const slug = repoSlug();

  // 反向控制先跑：它若不为 null，下面那条就什么也证明不了。
  let anchorLogin = "(未取到)";
  let anchorOk = false;
  try {
    const d = ghJson(`repos/${slug}/commits/${STALE_ANCHOR.sha}`);
    anchorLogin = d.author?.login ?? null;
    anchorOk = anchorLogin === null;
  } catch (e) {
    anchorLogin = `(取不到：${String(e.message).split("\n")[0]})`;
  }
  check(
    anchorOk,
    `【反向控制】${STALE_ANCHOR.sha} 反查不到账号`,
    `null —— ${STALE_ANCHOR.note}，说明读到的是活信号`,
    `得到 ${JSON.stringify(anchorLogin)} —— 锚点失效（历史被重写？邮箱被加回 GitHub？），` +
      `下面那条断言因此不成立`,
  );

  // HEAD 必须已推送，否则 GitHub 不认识这个 SHA。
  let headLogin = null;
  let headErr = null;
  try {
    headLogin = ghJson(`repos/${slug}/commits/${headSha}`).author?.login ?? null;
  } catch (e) {
    headErr = String(e.message).split("\n")[0];
  }
  if (headErr) {
    console.log(`  \x1b[33m—\x1b[0m HEAD ${headSha} 在 GitHub 上还不存在（未推送）：跳过归属检查`);
  } else {
    check(
      headLogin !== null,
      `HEAD ${headSha} 能反查到 GitHub 账号`,
      String(headLogin),
      "null —— Vercel 会静默拦下这次部署，且不会报任何错",
    );
  }
}

console.log(`\n  身份检查：绿 ${passed} · 红 ${failed}`);
if (failed > 0) {
  console.log(
    "\n  修法：\n" +
      "    git config user.email <GitHub 上已验证的邮箱>   # 只设 local\n" +
      "    git commit --amend --reset-author --no-edit    # 或 rebase 重写多个\n" +
      "    git push --force-with-lease\n",
  );
}
process.exit(failed === 0 ? 0 : 1);
