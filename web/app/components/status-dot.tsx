/**
 * 行首的状态圆点（参考稿 `.row .dot`：绿勾 / 琥珀问号）。
 *
 * **不靠颜色单独表意**：每种状态的字形不同（勾 / 问号 / 叹号 / 空心 / 转圈），
 * 色弱用户照样分得开。旁边的文字才是信息本身，圆点只是让一列行可以扫读。
 */
export type DotKind = "ok" | "warn" | "bad" | "idle" | "busy" | "info";

export function StatusDot({ kind, className = "" }: { kind: DotKind; className?: string }) {
  if (kind === "busy") {
    return <span aria-hidden className={`spinner mt-[3px] shrink-0 ${className}`} />;
  }
  return (
    <svg
      aria-hidden
      viewBox="0 0 15 15"
      width="15"
      height="15"
      className={`mt-[3px] shrink-0 ${className}`}
    >
      {kind === "ok" && (
        <>
          <circle cx="7.5" cy="7.5" r="7.5" fill="var(--c-ok)" />
          <path d="M4.1 7.65 6.05 9.55 10.4 5.2" stroke="#fff" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}
      {kind === "warn" && (
        <>
          <circle cx="7.5" cy="7.5" r="7.5" fill="var(--c-amber)" />
          <path d="M5.6 5.7a1.95 1.95 0 1 1 2.7 1.8c-.5.2-.8.6-.8 1.1v.3" stroke="#fff" strokeWidth="1.3" fill="none" strokeLinecap="round" />
          <circle cx="7.5" cy="10.9" r=".8" fill="#fff" />
        </>
      )}
      {kind === "bad" && (
        <>
          <circle cx="7.5" cy="7.5" r="7.5" fill="var(--c-danger)" />
          <path d="M7.5 3.9v4.4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
          <circle cx="7.5" cy="10.8" r=".85" fill="#fff" />
        </>
      )}
      {kind === "info" && (
        <>
          <circle cx="7.5" cy="7.5" r="7.5" fill="var(--c-accent)" />
          <circle cx="7.5" cy="4.3" r=".85" fill="#fff" />
          <path d="M7.5 6.6v4.6" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
        </>
      )}
      {kind === "idle" && <circle cx="7.5" cy="7.5" r="6.75" fill="none" stroke="#cfcfcf" strokeWidth="1.5" />}
    </svg>
  );
}
