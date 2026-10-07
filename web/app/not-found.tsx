import Link from "next/link";

import { Wordmark } from "@/app/components/wordmark";

/**
 * 404。课程页对「不存在 / 不是你的」两种情况都调 `notFound()`（越权访问不能长得像正常的空页，
 * 见 `courses/[id]/page.tsx`），所以这一页**不区分**这两种——说「找不到」，不说「不存在」。
 *
 * 2026-10-07 之前这里是 Next 的默认 404（白底黑字、系统字体），是全站唯一一页不在设计里的。
 */
export default function NotFound() {
  return (
    <main className="shell flex min-h-dvh flex-col">
      <div className="brandline">
        <Link href="/" className="brandline__mark" aria-label="Relecture，回到我的课程">
          <Wordmark />
        </Link>
      </div>
      <section className="flex flex-1 flex-col justify-center py-16">
        <p className="t-num enter text-[clamp(96px,16vw,200px)] leading-none">404</p>
        <h1 className="t-display enter-wipe mt-6">这一页找不到了</h1>
        <p className="t-body enter-late mt-5 max-w-[46ch] text-[var(--c-muted-text)]">
          可能链接不对，也可能它属于另一个账号。回到你的课程重新打开就好。
        </p>
        <div className="enter-later mt-10">
          <Link href="/" className="btn btn--primary btn--lg">
            回到我的课程{" "}
            <span className="btn__arrow" aria-hidden>
              →
            </span>
          </Link>
        </div>
      </section>
    </main>
  );
}
