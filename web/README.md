# web · 前端（Next.js on Vercel）

F1 阶段 1.5 的交付物：**最薄的部署链**。登录 → 读自己的 `style_config` → 显示四个字段。
没有 onboarding、没有 settings、没有注册页——那些是阶段 3。

它存在的理由不是这三个页面，是**先撞三方接缝**：Vercel 构建与环境变量、
浏览器到 Supabase Auth 的真实域名/TLS、以及会话跨服务端渲染边界的传递。
阶段 2 是纯离线资产活，零部署风险，所以排在它后面。

```bash
cp .env.example .env.local && $EDITOR .env.local
npm install && npm run dev

npm run verify:guards    # 身份 + 两条构建期护栏（后者跑三次真实 next build）
npm run verify:identity  # 只查提交身份，秒级；推完跑这一条
```

口径表在 [`../f1_samples/README.md`](../f1_samples/README.md) §一，全项目唯一一份。

---

## 〇、提交作者邮箱必须是 GitHub 上已验证的邮箱

**否则 Vercel 静默拦下部署。** 拦下的表现是「什么也没发生」：代码没问题、
构建根本没跑、没有一条错误指向这里——你会以为自己在看新版本，其实看的是上一次部署。
通则六的形态。

2026-09-08 实际踩到：GitHub 主邮箱从学校邮箱换成个人邮箱、学校邮箱被移除，
而仓库的 `user.email` 还是老的。

**归属是按账号当前的已验证邮箱实时算的，不是提交时冻结的。** 所以换邮箱之后，
历史上用旧邮箱的提交会一起变成不可归属——新提交不受影响，
但**回滚或重新部署那些提交会撞同一堵墙**。当时「能过」不等于现在还能过。

```bash
npm run verify:identity                    # 推完跑一次
npm run verify:identity -- --allow-offline # 没有 gh 时显式放行
```

查的是 `gh api repos/…/commits/<sha>` 的 `author.login`——**Vercel 反查的同一个信号**，
不是比对一个写死的邮箱。比对常量的版本在这次事故里**会是绿的**：邮箱没变，
变的是「GitHub 还认不认它」。一个在事故发生时照样绿的检查就是没有检查。

反向控制是一个已知反查不到账号的历史提交（`3c7ffba`，作者是已被移除的学校邮箱）：
它每次都必须返回 `null`，否则说明读到的不是活信号，「HEAD 可归属」那条也就不成立。

没有 `gh` 时默认**判失败**，要放行必须显式加 `--allow-offline`——
一个跳过了却仍然退出 0 的检查，与它防的那个失败是同一类东西。

想更早拦住，可以挂成 hook（本仓库没有自动装，改的是你的 git 配置）：

```bash
printf '#!/bin/sh\ncd web && npm run --silent verify:identity\n' > .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

---

## 一、两条构建期护栏

### 1 · `NEXT_PUBLIC_*` 里不许有 secret key

判定在 [`lib/public-env-guard.mjs`](lib/public-env-guard.mjs)，**构建期与运行期共用同一份**，
不各写一份。构建期那条是主的：等到运行期才发现，`NEXT_PUBLIC_` 的值已经被内联进
bundle 了——泄露在第一个访客打开页面时就已经发生。

只 `includes("service_role")` 是不够的。**旧版 legacy service key 是一个 JWT**，
`service_role` 这个词在 base64 载荷里，明文一个字都看不到：

```
eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.xxx
                     ^ 解开才是 {"role":"service_role"}
```

所以护栏会解 JWT 载荷看 `role`。危害最大的那个恰好是字面量扫不出来的那个。

**只扫 `NEXT_PUBLIC_` 前缀。** 不带该前缀的变量留在服务端、不进 bundle，
在那里放 secret 是正常用法（阶段 3 的模型 API key 就该在那儿）。一起拦掉会让这条
护栏在阶段 3 变成障碍，然后被关掉——**一条会被关掉的护栏等于没有护栏。**

### 2 · `/debug/rls` 是构建期开关，不是运行期 404

文件名是 `app/debug/rls/page.debug.tsx`。只有 `NEXT_PUBLIC_ENABLE_DEBUG_ROUTES=1` 时，
`debug.tsx` 才在 `next.config.ts` 的 `pageExtensions` 里，这个模块才成为一条路由。

用 `notFound()` 的版本里路由**仍在构建产物中**、页面代码仍被打包，防护取决于一个
运行期分支没被绕过。这里是连模块都不编译进去。**Vercel 生产环境不配这个变量即为关。**

### 验证

`npm run verify:guards`：便宜的单元用例覆盖判定分支，**三次真实 `next build`** 覆盖接线。

单测全绿只证明那个函数会算，证明不了 `next.config.ts` 真的调了它——F3a 那次
59 条测试全绿而 `AnthropicModel.request_signature` 被批量改名一条没红，就是这个形态
（通则七 #7：这个对象被哪条断言碰过？）。

| 断言 | 反向 |
| --- | --- |
| `NEXT_PUBLIC_TEST_KEY=sb_secret_fake` → 构建失败，且报出变量名 | — |
| 旧版 service_role JWT → 构建失败 | 用例本身断言了它**不含明文** `service_role`，否则测的是字面量分支 |
| 干净的 env → 构建成功 | 证明护栏不是「永远失败」 |
| 关掉开关 → 产物里没有 `/debug/rls` | 打开开关 → 有。否则「没有」可能只是这页编译不过 |

## 二、`/debug/rls` 为什么把两个查询放在同一次渲染里

它同时显示「查自己」和「查 `?probe=<uuid>`」的行数。分两次页面加载去查的话，
「读不到 B」就有第二种解释：**会话在两次之间过期了**。并排渲染后两个查询共享同一个
JWT、同一次请求周期，前者是后者的反向控制——缺了它后者什么也证明不了
（通则七 #5，与 `supabase/verify_rls_prod.py` 的步骤 1 同形）。

## 三、两处容易踩的地方

**`proxy.ts` 不是 `middleware.ts`。** Next 16 起 `middleware` 约定已废弃。
导出的函数名必须叫 `proxy`——**只改文件名不改函数名，它不会被调用，而构建不报错**，
表现是「会话再也不刷新了」，几十分钟后用户被登出，没有一条日志指向这里。

**`lib/supabase/server.ts` 里有一个 `catch {}`**，是全站唯一一处刻意的静默吞。
被吞的不是「一次失败的写入」而是「一次不必要的写入」：Server Component 里
`cookies()` 只读，写必然抛；真正的会话刷新在 `proxy.ts`，那里可写、失败会暴露。
**删 `proxy.ts` 之前先读那段注释**——删掉之后这个吞才会变成真的丢失。

## 四、已知没有覆盖到的

`proxy.ts` 的**会话刷新行为本身没有断言**。它只在 access token 接近过期时才做事，
而验收全程都在 token 有效期内。这里如实登记，不写成「已验证」——
凡是写成「已验证」的句子必须能指出是哪次测量（通则七 #1）。
