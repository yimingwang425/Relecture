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
      // 网络层拒掉时没有状态码 —— CORS 被挡就长这样，点名说出来。
      setError(`连不上产品服务端（${apiBase}）。可能是它没部署，或 CORS 里没有本站来源。原始错误：${String(e)}`);
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
