import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";
import { AXIS_IDS, type StyleConfig } from "./axes";

/**
 * 三个新页面共用的读取路径：登录态 + 自己那行 `style_config`。
 *
 * 读法与 `/`（阶段 1.5）一致：`select("*").eq("user_id", auth.user.id)` 拿数组，
 * **不用 `.single()`**。`.single()` 把「0 行」和「读取被 RLS 挡住」都变成同一个错误对象，
 * 而这两件事的处置完全不同——前者是 F09 的触发器没跑（坏状态），后者是策略出问题。
 * 拿数组就能把行数原样报出来。
 */

export type StyleRow = StyleConfig & {
  user_id: string;
  f1_arm: "pairwise" | "text";
  onboarded_at: string | null;
};

export type StyleRowResult =
  | { state: "ok"; email: string | null; userId: string; row: StyleRow }
  | { state: "error"; email: string | null; userId: string; message: string; code?: string }
  | { state: "bad-row-count"; email: string | null; userId: string; rows: number };

/** 未登录直接跳 `/login`；其余状态原样返回，由页面渲染。 */
export async function loadStyleRow(): Promise<StyleRowResult> {
  const supabase = await createClient();

  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/login");

  const email = auth.user.email ?? null;
  const userId = auth.user.id;

  const { data: rows, error } = await supabase
    .from("style_config")
    .select("user_id, detail_level, use_examples, structure, depth, f1_arm, onboarded_at")
    .eq("user_id", userId);

  if (error) {
    return { state: "error", email, userId, message: error.message, code: error.code };
  }
  if (!rows || rows.length !== 1) {
    return { state: "bad-row-count", email, userId, rows: rows?.length ?? 0 };
  }

  return { state: "ok", email, userId, row: rows[0] as StyleRow };
}

/** 取四条轴，丢掉 `user_id` / `f1_arm` / `onboarded_at`。 */
export function configOf(row: StyleRow): StyleConfig {
  return Object.fromEntries(AXIS_IDS.map((axis) => [axis, row[axis]])) as StyleConfig;
}
