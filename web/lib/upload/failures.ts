/**
 * 上传失败分类 → **用户看到的那句话**。
 *
 * ## 这份清单是第三处落地，所以有一条会红的比对
 *
 * 同一个闭集住在三处：
 *
 * | 处 | 干什么 |
 * | --- | --- |
 * | `supabase/migrations/20260912180000_f3_upload_events.sql` 的 `f3_deck_upload_failure_kinds()` | **唯一出处**。写错的值 23514 被拒 |
 * | `api/api/upload_failures.py` 的 `FAILURE_KINDS` | 服务端分类 + HTTP 状态码 |
 * | **本文件** | 前端文案 |
 *
 * 三处存在 ≠ 三处一致，所以 `scripts/verify_style_logic.mjs` 从 **SQL 原文**
 * 解析出那个清单，与本文件的键逐项比对（口径 F33，与 `events.ts` 里
 * `REQUIRED_PAYLOAD_KEYS` 那份镜像同一个做法）。
 *
 * 少了那条比对，后端加一类而前端没加的表现是：**用户看到 `undefined`**，
 * 或者看到一句兜底文案，而那句兜底文案会把一个我们本来能说清的失败
 * 说成「上传失败，请重试」——正是 PRD §10 明确禁止的那种无信息量提示。
 *
 * ## 为什么每一类都要自己的文案，不能压成「上传失败」
 *
 * **用户侧的处置每一类都不同。** 压成一句的具体代价：
 *
 * - `pptx_not_implemented` 说成「不支持」→ 他会以为永远不支持，去手动转 PDF，
 *   而我们下一次交付就会支持。
 * - `duplicate_in_course` 说成「失败」→ 他会再传一次，再失败一次。
 *   而事实是**什么都不用做，它已经在这门课里了**。
 * - `unparsable` 说成「请重试」→ 他会重试一个永远不会成功的上传。
 *
 * `retryable` 那一栏决定要不要给「重试」按钮，取值与
 * `api/api/upload_failures.py` 的 `RETRYABLE_KINDS` 同源（也在比对范围里）。
 * 方向是刻意取小的：**把可重试判成不可重试，用户少点一次按钮；
 * 反过来他会一直重试一个永不成功的上传。**
 *
 * 决策日期：2026-09-12
 */

/** 闭集。**顺序与 SQL 里那份一致**，比对按集合做，但保持顺序便于人眼对照。 */
export const FAILURE_KINDS = [
  "unsupported_format",
  "pptx_not_implemented",
  "empty_file",
  "too_large",
  "duplicate_in_course",
  "unparsable",
  "db_error",
  "unknown",
] as const;

export type FailureKind = (typeof FAILURE_KINDS)[number];

export type FailureCopy = {
  /** 一句话说清**发生了什么**。不含「请重试」这类无信息量的措辞（PRD §10）。 */
  title: string;
  /** 说清**用户该做什么**。没有可做的事就明说没有。 */
  action: string;
  /** 要不要给「重试」按钮。与服务端的 `RETRYABLE_KINDS` 同源。 */
  retryable: boolean;
};

export const FAILURE_COPY: Record<FailureKind, FailureCopy> = {
  unsupported_format: {
    title: "这个文件不是 PDF。",
    action: "目前只能读 .pdf。换一个 PDF 文件再试。",
    retryable: false,
  },
  pptx_not_implemented: {
    // ⚠️ **不要写成「不支持 PPTX」。** 那会让用户以为永远不支持而去手动转 PDF，
    // 而它只是还没交付（schema 已经定了）。
    title: "PPTX 还不能读 —— 这是我们还没做，不是不支持。",
    action: "暂时先转成 PDF 上传。PPTX 支持在做，做好之后这里会直接能用。",
    retryable: false,
  },
  empty_file: {
    title: "这个文件是空的（0 字节）。",
    action: "检查一下是不是没保存成功，或者传的是一个快捷方式。",
    retryable: false,
  },
  too_large: {
    title: "文件超过了体积上限。",
    action: "拆成几份分别上传，或者先压缩一下。",
    retryable: false,
  },
  duplicate_in_course: {
    // **这一类最容易被说成「失败」，而它几乎不是失败。**
    title: "这门课里已经有同一份文件了。",
    action: "不用重新上传 —— 它已经可以用来提问了。要换新版本的话，先删掉旧的那一份。",
    retryable: false,
  },
  unparsable: {
    title: "这份 PDF 打不开，或者里面读不出文字。",
    action: "扫描件（整页都是图片）目前读不了。如果是加密或损坏的文件，换一份能正常打开的。",
    retryable: false,
  },
  db_error: {
    title: "存的时候出错了 —— 这一次是我们这边的问题。",
    action: "文件没问题，重试一次通常就好。",
    retryable: true,
  },
  unknown: {
    title: "遇到了一个我们没预料到的错误。",
    action: "重试一次；如果一直这样，把文件名告诉我们。",
    retryable: true,
  },
};

/**
 * 把服务端返回的分类变成文案。
 *
 * ⚠️ **认不出来的分类走 `unknown`，但必须留下痕迹。**
 * 静默回落到 `unknown` 的表现是「偶尔有些失败被归成未知」，
 * 而真实原因是**后端加了一类而前端没加**——那条比对本该拦住它，
 * 但比对跑在 CI 里，线上这一刻需要的是「至少别把它说成 undefined」。
 */
export function copyFor(kind: string | null | undefined): FailureCopy & { matched: boolean } {
  if (kind && (FAILURE_KINDS as readonly string[]).includes(kind)) {
    return { ...FAILURE_COPY[kind as FailureKind], matched: true };
  }
  if (kind) {
    console.error(
      JSON.stringify({
        event: "upload_failure_kind_unrecognized",
        kind,
        hint: "后端加了一类而前端没加；verify:logic 里那条镜像比对应当已经红了",
      }),
    );
  }
  return { ...FAILURE_COPY.unknown, matched: false };
}

/**
 * HTTP 状态码 → 分类。**兜底用**，正常路径读响应体里的 `failure_kind`。
 *
 * 为什么要这条兜底：CORS 被挡、网关超时、或服务端在 `deck_upload_failed` 这条
 * 路径之前就挂了的时候，响应体里没有 `failure_kind`。那时状态码是仅有的信息。
 *
 * ⚠️ **它会丢信息**，所以不是主路径：415 与 501 都是「格式家族」，
 * 状态码能分开它们正是因为服务端一个类一个码（`HTTP_STATUS_BY_KIND`）。
 */
export const KIND_BY_STATUS: Record<number, FailureKind> = {
  415: "unsupported_format",
  501: "pptx_not_implemented",
  400: "empty_file",
  413: "too_large",
  409: "duplicate_in_course",
  422: "unparsable",
  502: "db_error",
  500: "unknown",
};

export function kindFromStatus(status: number): FailureKind {
  return KIND_BY_STATUS[status] ?? "unknown";
}
