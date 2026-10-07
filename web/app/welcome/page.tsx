import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { CSSProperties } from "react";

import { Wordmark } from "@/app/components/wordmark";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: { absolute: "Relecture" } };

/**
 * 欢迎页：未登录用户的入口。
 *
 * ## 2026-10-07 改版：从「一个标志 + 一句话」到「说清楚这是什么」
 *
 * 原来那一版只有文字标志、一句话和两个按钮，波带扫过时字母抖一下。好看，
 * 但第一次来的人看完不知道它**做什么**。现在三块：
 *
 * 1. 左边：标志、一句话（「弄懂每个问题」下面一道朱红笔描出来）、登录。
 * 2. 右边：**一页被批注过的讲义**——公式被圈住、页边贴一张「换个讲法」的便签、
 *    底下标着出处页码。这就是产品本身做的事，不用读字也看得懂。
 * 3. 底部：三条编号的原则（只用你的课件 / 讲法由你定 / 每次都标出处）。
 *
 * 全部是服务端渲染 + CSS 动画，**没有客户端组件**：原来的字母颤抖要每帧读波带位置，
 * 现在的描线、上升都由 CSS 时间线驱动，系统开了「减少动态效果」时直接停在画完的样子。
 *
 * **注册按钮先放着但禁用**：自助注册是进度总结 §七 第 2 项，还没做。
 * 按钮在、说明在，用户就知道「现在不能注册」而不是「找不到注册」。
 *
 * 已登录的人不该看到这一页，直接送回首页。
 */
export default async function WelcomePage() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (data.user) redirect("/");

  return (
    <main className="welcome">
      <header className="welcome__top enter-drop">
        <span className="wordmark-sm">
          <Wordmark />
        </span>
        <Link href="/login" className="link text-[14px]">
          登录
        </Link>
      </header>

      <section className="welcome__hero">
        <div className="min-w-0">
          <p className="t-eyebrow enter">课件讲解 · 按你喜欢的讲法</p>
          <h1 className="wordmark-hero mt-6 sm:mt-8" aria-label="Relecture">
            <span className="rise-line" aria-hidden>
              <span style={{ "--d": "0.1s" } as CSSProperties}>
                <Wordmark />
              </span>
            </span>
          </h1>

          {/* 断行写死在逗号后：交给浏览器的话，「在」会被留在上一行（2026-09-15 截图实测）。 */}
          <p className="welcome__lead mt-8 sm:mt-10">
            <span className="rise-line">
              <span style={{ "--d": "0.32s" } as CSSProperties}>选择适合你的讲解方式，</span>
            </span>
            <span className="rise-line">
              <span style={{ "--d": "0.42s" } as CSSProperties}>
                在自己的节奏里
                <span className="scribble">
                  弄懂每个问题
                  <svg aria-hidden viewBox="0 0 200 12" preserveAspectRatio="none">
                    <path pathLength={1} d="M3 8.5C38 4 84 3 124 5.2c27 1.5 52 3.6 73-.6" />
                  </svg>
                </span>
                。
              </span>
            </span>
          </p>

          <div className="enter-later mt-10 flex flex-wrap items-center gap-3 sm:mt-12">
            <Link href="/login" className="btn btn--primary btn--lg min-w-[156px]">
              登录 <span className="btn__arrow" aria-hidden>→</span>
            </Link>
            <button
              type="button"
              disabled
              className="btn btn--ghost btn--lg min-w-[120px]"
              aria-describedby="register-note"
            >
              注册
            </button>
            <p id="register-note" className="t-caption ml-1">
              目前是邀请制，注册即将开放
            </p>
          </div>
        </div>

        <Specimen />
      </section>

      <footer className="welcome__foot enter-later">
        {PRINCIPLES.map(([title, body], i) => (
          <div key={title} className="principle">
            <span className="principle__num">{String(i + 1).padStart(2, "0")}</span>
            <p className="principle__title">{title}</p>
            <p className="principle__body">{body}</p>
          </div>
        ))}
      </footer>
    </main>
  );
}

const PRINCIPLES: readonly (readonly [string, string])[] = [
  ["只用你的课件", "讲解只从你上传的讲义里找依据。片段里没有的部分，会直接说出来，不拿通用知识补。"],
  ["讲法由你来定", "讲多细、举不举例子、先给结论还是先铺垫——开始时选一次，之后在讲解里随时改。"],
  ["每次都标出处", "讲解后面跟着用到的课件和页码，随时能翻回原文对照。"],
];

/**
 * 一页被批注过的讲义。纯装饰（`aria-hidden`），内容取自演示课程的热机那一讲。
 * 描线的先后由 `--d` 排：先圈公式，再画箭头，最后便签落下来（便签的延迟在 CSS 里）。
 */
function Specimen() {
  return (
    <figure className="specimen" aria-hidden>
      <div className="specimen__sheet">
        <div className="specimen__head">
          <span>22.081J · Lecture 3</span>
          <span>p. 20</span>
        </div>
        <p className="specimen__title">热机的效率上限</p>
        <p className="specimen__text">任何热机都只能把一部分热变成功，剩下的必须排给低温的一侧。</p>
        <div className="specimen__row">
          <span className="specimen__formula">
            η = 1 − T<sub>c</sub> / T<sub>h</sub>
            <svg viewBox="0 0 200 70" preserveAspectRatio="none">
              <path
                pathLength={1}
                style={{ "--d": "1.5s" } as CSSProperties}
                d="M108 6C58 2 12 12 6 34c-5 20 40 31 98 30 56-1 92-11 90-32-2-19-40-28-96-27"
              />
            </svg>
          </span>
          <svg className="specimen__arrow" viewBox="0 0 64 36">
            <path pathLength={1} style={{ "--d": "2s" } as CSSProperties} d="M62 8C44 4 22 10 6 26m0 0 1-10m-1 10 10-2" />
          </svg>
          <div className="specimen__note">
            <b>换个讲法</b>
            把 T<sub>c</sub> 想成冷却水的温度：它不是 0 K，就总有热要排出去。
          </div>
        </div>
        <p className="specimen__text">只要冷的那一侧不是绝对零度，η 就到不了 1。</p>
        <div className="specimen__cite">
          <span>出处</span>
          <span>lec03.pdf · 第 20 页</span>
        </div>
      </div>
    </figure>
  );
}
