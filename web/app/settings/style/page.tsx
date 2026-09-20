import type { Metadata } from "next";

import Link from "next/link";

import { BrandLine } from "@/app/components/brand-line";
import { Diagnostics } from "@/app/components/diagnostics";
import { RowStateNotice } from "@/app/components/row-state";
import { newSessionId } from "@/lib/style/events";
import { configOf, loadStyleRow } from "@/lib/style/session";

import { StyleSettings } from "./settings-client";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "讲解风格" };

/**
 * 展示并修改已有风格配置，外加反馈按钮（口径 F05–F07）。
 *
 * 这一页刻意**不显示档位的区间数字**（如「30–120 字/段」）：那些数值只存在于
 * `scan/points.py` 的 `AXIS_POINTS`，抄进 TS 就是 F13 那个坑。
 * 2026-09-09 拍板用文字描述档位，于是「把 AXIS_POINTS 提升到生产模块」
 * 的触发条件不成立，那一项继续挂在 `f1_samples/README.md` §二。
 */
export default async function StyleSettingsPage() {
  const result = await loadStyleRow();

  return (
    <main data-state={result.state} className="shell">
      <BrandLine>
        <Link href="/settings" className="link">
          返回设置
        </Link>
      </BrandLine>

      {result.state !== "ok" ? (
        <div className="mt-12">
          <RowStateNotice result={result} />
        </div>
      ) : (
        /* `sessionId` 在**服务端**抽（同 /onboarding/style/page.tsx）：客户端组件的
           渲染在 SSR 与 hydrate 各跑一次，渲染期取随机数两侧会分叉（React #418）。
           「一次页面加载 = 一个 session」是占位口径，PRD §3.4.5 的切分规则未定。 */
        <>
          <StyleSettings
            userId={result.userId}
            initialConfig={configOf(result.row)}
            onboarded={result.row.onboarded_at !== null}
            arm={result.row.f1_arm}
            sessionId={newSessionId()}
          />

          {/* 这一行四个轴的**真实取值**（不是中文标签）。回归工具按 data-field 读它们：
              2026-09-19 起首页会把没走过 onboarding 的人直接送去风格选择，
              那几条断言于是搬到这一页——断的仍是「用自己的会话读到自己那一行」。 */}
          <Diagnostics
            className="mt-12"
            items={[
              ["账号", result.email ?? "—"],
              ["user_id", result.userId, "user-id"],
              ["detail_level", result.row.detail_level, "detail_level"],
              ["use_examples", result.row.use_examples, "use_examples"],
              ["structure", result.row.structure, "structure"],
              ["depth", result.row.depth, "depth"],
              ["f1_arm", result.row.f1_arm, "f1_arm"],
              [
                "onboarded_at",
                result.row.onboarded_at ? String(result.row.onboarded_at) : "null（未完成 onboarding）",
              ],
            ]}
          />
        </>
      )}
    </main>
  );
}
