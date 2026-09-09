import type { StyleRowResult } from "@/lib/style/session";

/**
 * 读不到自己那行时的提示。三个新页面共用，措辞与 `/`（阶段 1.5）保持一致。
 *
 * **0 行不是「还没配置」，是坏状态**：F09 的触发器保证每个注册用户都有且只有一行。
 * 把它渲染成空白或「去完成 onboarding」，会让一个坏状态看起来像正常初始态，
 * 然后在第一次讲解时以别的形态暴露——那时候归因成本高得多。
 */
export function RowStateNotice({
  result,
}: {
  result: Extract<StyleRowResult, { state: "error" | "bad-row-count" }>;
}) {
  if (result.state === "error") {
    return (
      <p
        role="alert"
        className="panel border-[color:var(--c-danger)]/30 bg-[var(--c-danger-wash)] p-4 text-sm text-[var(--c-danger)]"
      >
        读取风格配置失败：{result.message}
        {result.code ? `（${result.code}）` : null}
      </p>
    );
  }

  return (
    <p
      role="alert"
      className="panel border-[color:var(--c-warn)]/30 bg-[var(--c-warn-wash)] p-4 text-sm leading-relaxed text-[var(--c-warn)]"
    >
      读到 {result.rows} 行，应为 1 行。每个用户注册时由数据库触发器建行（口径 F09），
      读到 0 行说明触发器没跑或被 RLS 挡住，<b>不是</b>「尚未配置」。
    </p>
  );
}
