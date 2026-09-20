import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

/**
 * 旧的「课件」页（跨课上传）。**2026-09-20 退役，重定向到我的课程。**
 *
 * 为什么不直接删掉这个文件：这条路径可能还在某个人的书签、某条旧链接、
 * 或者浏览器的地址栏补全里。删掉的话那些入口变成 404，
 * 而 404 读起来像「这个功能没了」——它没有没，只是搬进了每门课自己的侧栏。
 *
 * 课件现在属于**一门课**：在 `/courses/[id]` 的侧栏里点「我的课件」，
 * 上传、改名都在那个弹窗里。跨课的上传页因此没有存在的位置了——
 * 它当初要先选课再传，而那个「先选课」正是现在已经发生过的事。
 */
export default async function CoursesPage() {
  redirect("/");
}
