import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./lib/env";

/**
 * 会话刷新。**这是全站唯一真正写 cookie 的地方**——
 * lib/supabase/server.ts 里那个 `catch {}` 之所以是安全的，前提就是本文件存在。
 * 删它之前先读那段注释。
 *
 * 文件名是 `proxy.ts`：Next 16 起 `middleware` 约定已废弃（构建会警告）。
 * 导出的函数名随之改成 `proxy`——**只改文件名不改函数名，它不会被调用，
 * 而构建不报错**，表现就是「会话再也不刷新了」，几十分钟后用户被登出，
 * 没有任何一条日志指向这里。
 *
 * 必须调 `getUser()` 而不是 `getSession()`：后者只解本地 cookie 里的 JWT，
 * **不向 Supabase 核实**，过期或伪造的 token 照样解得出一个 user 对象。
 * 用它做鉴权判断，等于把「有没有一个长得像 token 的字符串」当成「登录了」。
 */
export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  await supabase.auth.getUser();

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
