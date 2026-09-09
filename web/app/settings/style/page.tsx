import { AppNav } from "@/app/components/app-nav";
import { RowStateNotice } from "@/app/components/row-state";
import { configOf, loadStyleRow } from "@/lib/style/session";

import { StyleSettings } from "./settings-client";

export const dynamic = "force-dynamic";

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
    <main
      data-state={result.state}
      className="mx-auto w-full max-w-[880px] px-5 py-8"
    >
      <AppNav email={result.email} />

      {result.state !== "ok" ? (
        <div className="mt-10">
          <RowStateNotice result={result} />
        </div>
      ) : (
        <StyleSettings
          userId={result.userId}
          initialConfig={configOf(result.row)}
          onboarded={result.row.onboarded_at !== null}
          arm={result.row.f1_arm}
        />
      )}
    </main>
  );
}
