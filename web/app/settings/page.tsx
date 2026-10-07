import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { BrandLine } from "@/app/components/brand-line";
import { Diagnostics } from "@/app/components/diagnostics";
import { LogoutButton } from "@/app/components/logout-button";
import { PageHeader } from "@/app/components/page-header";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "设置" };

/**
 * 设置页。**顶栏去掉之后，它接住了顶栏原来放的那两样东西**：讲解风格与退出登录。
 *
 * ## 为什么这一页不直接把风格表单放进来
 *
 * 风格设置有自己的一页（`/settings/style`），它带着反馈按钮与那一串
 * 诊断字段，回归工具按 `data-field` 读它们。搬进来等于把那些断言也搬一遍，
 * 而这一页真正要回答的只有「我能改什么」。所以这里是入口，不是表单。
 *
 * ## 账号那一段只读，不做改密/改邮箱
 *
 * 那两件事要走 Supabase 的邮件流程，而本项目连注册页都还没做
 * （欢迎页上的「注册」是后续的事）。**不摆一个点不动的按钮**——
 * 摆了就是在说「这里能改」，然后用户点下去什么都没发生。
 */
export default async function SettingsPage() {
  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/welcome");

  return (
    <main data-state="ok" className="shell shell--narrow">
      <BrandLine>
        <Link href="/" className="link">
          我的课程
        </Link>
      </BrandLine>

      <PageHeader
        eyebrow="设置"
        title="讲法、账号"
        lead="讲解的详略、举例、结构、深度都在「讲解风格」里；那份配置一人一份，所有课通用。"
      />

      <div className="flex flex-col gap-3">
        <Link
          href="/settings/style"
          data-field="to-style"
          className="row no-underline transition-transform hover:-translate-y-[1px]"
        >
          <div className="min-w-0 flex-1">
            <p className="row__title">讲解风格</p>
            <p className="row__sub">
              四种讲法选一种，或者逐项改：讲多细、举不举例子、先结论还是先铺垫、讲多深、语气。
            </p>
          </div>
          <span aria-hidden className="t-meta">
            →
          </span>
        </Link>

        <div className="row">
          <div className="min-w-0 flex-1">
            <p className="row__title">账号</p>
            <p className="row__sub mono break-all">{auth.user.email ?? "（这个账号没有邮箱）"}</p>
          </div>
          <LogoutButton className="btn btn--ghost btn--sm shrink-0" />
        </div>
      </div>

      <Diagnostics
        className="mt-10"
        items={[
          ["账号", auth.user.email ?? "—"],
          ["user_id", auth.user.id, "user-id"],
        ]}
      />
    </main>
  );
}
