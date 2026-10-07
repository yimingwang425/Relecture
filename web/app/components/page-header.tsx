import type { ReactNode } from "react";

/**
 * 页面标题区：眉题 + 大标题（擦出入场）+ 一句说明 + 右侧操作。
 *
 * 参考稿的 hero 是居中的；应用页面是左对齐——用户是来办事的，
 * 标题要和下面的内容对齐，而不是和整个视口对齐。
 */
export function PageHeader({
  eyebrow,
  title,
  lead,
  aside,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  lead?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="mt-10 mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-4 sm:mt-14">
      <div className="min-w-0 max-w-[720px]">
        {eyebrow && <p className="t-eyebrow enter mb-3">{eyebrow}</p>}
        <h1 className="t-display enter-wipe">{title}</h1>
        {lead && <p className="t-body enter-late mt-3 max-w-[62ch]">{lead}</p>}
      </div>
      {aside && <div className="enter-late shrink-0">{aside}</div>}
    </header>
  );
}
