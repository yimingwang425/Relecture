"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createClient } from "@/lib/supabase/client";

/**
 * 建一门课（= 一个项目）。
 *
 * **走产品服务端的 `POST /courses`，不在浏览器里直接插表**：建课与上传是同一条链路，
 * 归属、命名规则都在那一侧；前端另开一条插入路径，两条迟早不一致，而不一致不会报错。
 *
 * 建完 `router.refresh()`：列表由服务端渲染，刷新一次就拿到新行，
 * 不在客户端维护第二份列表副本。
 */
export function NewCourse({ apiBase }: { apiBase: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);

    const { data } = await createClient().auth.getSession();
    const jwt = data.session?.access_token;
    if (!jwt) {
      setError("登录状态已过期，请重新登录。");
      setBusy(false);
      return;
    }

    try {
      const r = await fetch(`${apiBase}/courses`, {
        method: "POST",
        headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      if (!r.ok) {
        setError(`建课失败（HTTP ${r.status}）。${await r.text()}`);
        setBusy(false);
        return;
      }
      setName("");
      setBusy(false);
      router.refresh();
    } catch (e) {
      // 网络层拒掉时**没有状态码**，所以这里分不出是哪一种失败。
      //
      // ⚠️ 2026-09-21 改掉了原来那句话。它说的是「可能是它没部署，或 CORS 里
      // 没有本站来源」——两个猜测当时都不对（服务部署好了、来源也在白名单里），
      // 真正的原因是**冷启动**：Render 免费层休眠后，第一个请求打在它的边缘上，
      // 而那个错误页不带 CORS 头，于是浏览器报成一模一样的 Failed to fetch。
      // 结果是时间花在翻 Render 控制台上。
      //
      // 现在：**按可能性排序**说，并且给出能一句话分清楚的办法
      // （页面上那个 ApiWarmup 会持续叫醒它，醒了那行提示就消失）。
      setError(
        `连不上讲解服务（${apiBase}）。最常见的原因是它在休眠——` +
          `免费层 15 分钟没人用就睡，首次唤醒要几十秒。` +
          `页面上那条「正在叫醒讲解服务」消失之后再试一次。` +
          `一直叫不醒的话，打开 ${apiBase}/health 看 cors_allow_origins 里有没有 ` +
          `${typeof window === "undefined" ? "本站来源" : window.location.origin}。` +
          `原始错误：${String(e)}`,
      );
      setBusy(false);
    }
  }

  return (
    <form onSubmit={create} className="flex flex-col gap-2" data-field="new-course">
      <div className="flex gap-2">
        <input
          data-field="new-course-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="新课程名，例如 18.06 线性代数"
          aria-label="新课程名"
          className="input flex-1"
        />
        <button
          type="submit"
          data-field="create-course"
          disabled={!name.trim() || busy}
          className="btn btn--primary shrink-0"
        >
          {busy ? "建课中…" : "建课"}
        </button>
      </div>
      {error && (
        <p role="alert" className="notice notice--danger text-[13px]">
          {error}
        </p>
      )}
    </form>
  );
}
