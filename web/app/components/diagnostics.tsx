import type { ReactNode } from "react";

/**
 * 诊断信息的折叠区。
 *
 * ## 为什么折叠，而不是删掉
 *
 * 旧页面把 `user_id`、构建号、`session_id`、`f1_arm` 直接铺在页面上——那是为验收做的 UI。
 * 进度总结 §八 第 7 条：**诊断字段不要直接删**，人工验收还要读它们
 * （构建号是验收第 0 步，口径 F17；`round_id` / `slice_keys` 是 F4 验收的入参）。
 *
 * 所以挪进一个默认收起的 `<details>`：用户看不见，验收的人点开就有。
 * **收起时内容仍在 DOM 里**，`data-field` 定位与 `textContent` 读取照常可用。
 */
export function Diagnostics({
  items,
  title = "诊断信息",
  className = "",
  children,
}: {
  /** `[标签, 值, data-field?]`。值为 null 时显示「—」，不省略这一行——缺失本身就是信息。 */
  items: [label: string, value: ReactNode, field?: string][];
  title?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <details className={`diagnostics ${className}`} data-field="diagnostics">
      <summary>{title}</summary>
      <dl className="diag-grid">
        {items.map(([label, value, field]) => (
          <div key={label} className="contents">
            <dt>{label}</dt>
            <dd data-field={field}>{value ?? "—"}</dd>
          </div>
        ))}
      </dl>
      {children}
    </details>
  );
}
