import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";

import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "../env";

/**
 * 服务端客户端。**每次请求新建一个**，不要提到模块作用域——
 * 提上去会让一个用户的会话被另一个用户的请求复用。
 *
 * 它带的仍是 publishable key + 用户自己的 JWT，**RLS 照常生效**。
 * service role key 一次都不出现（约束 2）。
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Component 里 cookies() 是只读的，这里必然抛。
          //
          // **这是本项目唯一一处刻意的静默吞**，写清为什么它不是 F07/F10 那种：
          // 被吞掉的不是「一次失败的写入」，而是「一次不必要的写入」——
          // 会话刷新由 proxy.ts 统一做，那里 cookie 可写、失败会暴露。
          // 若哪天删掉 proxy.ts，这里的吞才会变成真的丢失，
          // 故那个文件里留了对应的注释互指。
        }
      },
    },
  });
}
