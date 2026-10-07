import Link from "next/link";
import type { ReactNode } from "react";

import { Wordmark } from "@/app/components/wordmark";

/**
 * 页眉的**一行**：左边应用名，右边一个去处。
 *
 * ## 这不是导航栏，顶栏已经去掉了（2026-09-20）
 *
 * 原来那条黑色胶囊导航并排放着「我的课程 / 课件 / 讲解风格」，三件事的使用频率
 * 差着量级：提问是每天做的，传课件是一次性的准备，改风格是偶尔。并排等于说
 * 它们一样重要，而用户每次都要在三个里面挑那一个他其实一直在用的。
 *
 * 现在：课件与会话进课程工作区的侧栏（就在用的地方），风格与退出进设置页，
 * 这一行只在「我的课程」与设置页上出现，它只需要回答「这是什么应用」
 * 和「怎么去另一边」。
 *
 * **这一行不是客户端组件**：它没有状态。原来那个导航要算当前页高亮、
 * 要管汉堡菜单，所以整棵树都被 `"use client"` 拖下水了。
 */
export function BrandLine({ children }: { children?: ReactNode }) {
  return (
    <div className="brandline enter">
      <Link href="/" className="brandline__mark" aria-label="Relecture，回到我的课程">
        <Wordmark />
      </Link>
      <nav className="flex items-center gap-6">{children}</nav>
    </div>
  );
}
