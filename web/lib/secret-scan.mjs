/**
 * 在**构建产物**里找真实凭据。与 `public-env-guard.mjs` 是两件事：
 * 那个查「环境变量里有没有」，这个查「最后打出来的东西里有没有」。
 *
 * ## 为什么不能匹配字符串本身
 *
 * 2026-09-08 人工验收时发现：在 devtools 里全局搜 `sb_secret_` 有 3 处命中，
 * 查下来全是**护栏自己的源码**（`v.startsWith("sb_secret_")` 这类字面量比较）。
 *
 * 这让「搜 `sb_secret_` 应为 0 命中」这个判据**永久失效**了：以后任何人搜都会
 * 命中这几处。当天还能人工分辨，是因为只有 3 处且都在一个文件里；
 * **真泄露的时候是第 4 处，混在里面看不出来。**
 *
 * 同样的毛病在 `service_role` 上更隐蔽——护栏里有 `v.includes("service_role")`，
 * 搜它同样永远命中。
 *
 * 所以两个判据都改成**形态匹配**：
 *
 * - `sb_secret_` 后面必须跟着 ≥20 个 key 字符。护栏源码里跟着的是引号，不命中。
 * - JWT 不看字面量，**解出载荷看 `role`**。护栏源码里的 `"service_role"` 是裸字符串，
 *   不构成 JWT，不命中。
 *
 * 一个永远命中、或永远不命中的判据，就不是判据（口径 F16，与 F14 同类）。
 *
 * ## 20 这个数的依据
 *
 * 实测本项目的 publishable key 在前缀之后有 31 个字符，新版 secret key 同构。
 * 取 20 两侧都有余量：**低于任何真实 key 的熵段长度**，
 * 又**远高于源码里字面量后面能跟的字符数**（那里跟的是 `"` 或 `'`，长度 0）。
 */

/** `sb_secret_` + 真实 key 字符。护栏源码里的字面量比较不会命中。 */
const SB_SECRET = /sb_secret_[A-Za-z0-9_-]{20,}/g;

/** JWT 形态。命中后必须解载荷确认 role，不看字面量。 */
const JWT_SHAPED = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/g;

function jwtRole(token) {
  try {
    const payload = JSON.parse(
      Buffer.from(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"),
    );
    return typeof payload?.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

/** 扫一段文本，返回命中列表（每项含类型与截断后的样本）。 */
export function scanText(text) {
  const hits = [];

  for (const m of text.matchAll(SB_SECRET)) {
    hits.push({ kind: "sb_secret", sample: m[0].slice(0, 24) + "…" });
  }
  for (const m of text.matchAll(JWT_SHAPED)) {
    if (jwtRole(m[0]) === "service_role") {
      hits.push({ kind: "service_role_jwt", sample: m[0].slice(0, 24) + "…" });
    }
  }
  return hits;
}

/**
 * 递归扫一个目录。返回 `{ filesScanned, hits }`。
 *
 * **`filesScanned` 必须被调用方断言 > 0。** 目录不存在或路径写错时命中数同样是 0，
 * 那种「0 命中」什么也证明不了——与「A 读 B 返回空」在 B 无行时也成立同形
 * （通则七 #5）。
 */
export async function scanDir(dir, { maxBytes = 8 * 1024 * 1024 } = {}) {
  const { readdir, readFile, stat } = await import("node:fs/promises");
  const { join } = await import("node:path");

  const hits = [];
  let filesScanned = 0;

  async function walk(d) {
    let entries;
    try {
      entries = await readdir(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = join(d, e.name);
      if (e.isDirectory()) {
        await walk(full);
        continue;
      }
      if (!e.isFile()) continue;
      const info = await stat(full).catch(() => null);
      if (!info || info.size > maxBytes) continue;
      const text = await readFile(full, "utf8").catch(() => null);
      if (text === null) continue;
      filesScanned++;
      for (const h of scanText(text)) hits.push({ ...h, file: full });
    }
  }

  await walk(dir);
  return { filesScanned, hits };
}
