"use client";

import { useEffect, useState } from "react";

/**
 * 叫醒产品服务端，并把「它醒没醒」显示出来（2026-09-21）。
 *
 * ## 为什么需要它
 *
 * Render 免费层 15 分钟没人用就休眠。休眠之后的**第一个请求打在 Render 的边缘上**，
 * 而它在容器起来之前返回的错误页**不带 CORS 头**——于是浏览器把它报成
 * `TypeError: Failed to fetch`，与「CORS 没配」「服务没部署」长得一模一样。
 *
 * 2026-09-21 就这么绕了一圈：建课报这个错，前端那句话说「可能是它没部署，
 * 或 CORS 里没有本站来源」，于是时间花在翻 Render 控制台和查白名单上，
 * 而两者都是好的——**服务只是在睡觉**。
 *
 * ## 这不是「调大超时」
 *
 * F74 当初记的修法是「keep-alive 或升级计划，**不是调大超时**」——调超时是把
 * 症状藏起来。这里做的是 keep-alive 的那一半，而且**只在有人真的打开页面时做**：
 * 那正是需要它的时刻，也不需要任何定时任务或花钱。
 *
 * ## 为什么读 /health 而不是发个空请求
 *
 * 它顺带回答了另外两个问题：**线上跑的是哪个构建**（口径 F17）与
 * **白名单里有哪些来源**。所以叫醒失败时，这个组件能说出到底是哪一种失败，
 * 而不是再猜一次。
 *
 * ## 失败不拦路
 *
 * 叫不醒时只显示一行提示，**页面照常能用**：课程列表、课件、会话都直连
 * Supabase，与这个服务无关（同 `lib/env.ts` 那条「一个功能不可用不该放大成
 * 全站不可用」）。
 */

type State =
  | { kind: "waking"; attempt: number }
  | { kind: "ready"; buildCommit: string | null }
  | { kind: "unreachable"; attempts: number; detail: string };

/** 每次重试之间等多久。**递增**：冷启动实测要几十秒，密集重试只是白发请求。 */
const BACKOFF_MS = [0, 3000, 5000, 8000, 12000, 20000];

export function ApiWarmup({ apiBase }: { apiBase: string }) {
  const [state, setState] = useState<State>({ kind: "waking", attempt: 0 });

  useEffect(() => {
    let cancelled = false;

    async function ping(i: number): Promise<void> {
      if (cancelled) return;
      if (i > 0) await new Promise((r) => setTimeout(r, BACKOFF_MS[i]));
      if (cancelled) return;
      setState({ kind: "waking", attempt: i + 1 });
      try {
        const r = await fetch(`${apiBase}/health`, { cache: "no-store" });
        if (cancelled) return;
        if (r.ok) {
          const body = (await r.json().catch(() => ({}))) as { build_commit?: string | null };
          return setState({ kind: "ready", buildCommit: body.build_commit ?? null });
        }
        throw new Error(`HTTP ${r.status}`);
      } catch (e) {
        if (cancelled) return;
        if (i + 1 < BACKOFF_MS.length) return ping(i + 1);
        setState({ kind: "unreachable", attempts: BACKOFF_MS.length, detail: String(e) });
      }
    }

    void ping(0);
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  // 醒着就什么都不显示。**不报喜**——一切正常时页面上不该多一行字。
  if (state.kind === "ready") return null;

  if (state.kind === "waking") {
    return (
      <p className="notice notice--info text-[13px]" data-field="api-waking" data-state="waking">
        <span aria-hidden className="pulse-dot mr-3 align-[1px]" />
        正在叫醒讲解服务（免费层 15 分钟没人用就休眠，首次唤醒要几十秒）。
        {state.attempt > 1 ? ` 第 ${state.attempt} 次尝试。` : ""}
        <b>这期间建课与提问会失败</b>，等这行字消失再试。
      </p>
    );
  }

  return (
    <p role="alert" className="notice notice--warn text-[13px]" data-field="api-unreachable" data-state="unreachable">
      <b>叫不醒讲解服务</b>（试了 {state.attempts} 次）。课程、课件、对话仍然能看——
      它们直连数据库，与这个服务无关；建课、上传、提问要等它起来。
      <br />
      <span className="opacity-80">
        服务端：<code>{apiBase}</code>
        {" · "}
        本站来源：<code>{typeof window === "undefined" ? "" : window.location.origin}</code>
        {" · "}
        原始错误：{state.detail}
      </span>
      <br />
      <span className="opacity-80">
        把上面那个「本站来源」与 <code>{apiBase}/health</code> 里的{" "}
        <code>cors_allow_origins</code> 对一下 —— 不在里面就是白名单的问题，
        在里面就是服务本身没起来。
      </span>
    </p>
  );
}
