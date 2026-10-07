# UI 预览（假后端 + 截图）

在**没有生产凭据、连不上 Supabase / Render** 的环境里，把登录后的每一页真的渲染出来、截图自检。
2026-10-07 那一轮 UI 改版（「页边批注」视觉语言）就是靠它一轮轮截图迭代的。

```bash
cd web
node scripts/ui-preview/mock-server.mjs &                     # :4010，假 Supabase + 假 api/

NEXT_PUBLIC_SUPABASE_URL=http://localhost:4010 \
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_preview \
NEXT_PUBLIC_API_BASE_URL=http://localhost:4010/api \
  npx next dev -p 3000 &

node scripts/ui-preview/shoot.mjs /tmp/ui-shots               # 全部场景 × 桌面 / 手机
node scripts/ui-preview/shoot.mjs /tmp/ui-shots home,answered # 只截几张
PREVIEW_TABLET=1 node scripts/ui-preview/shoot.mjs /tmp/ui-shots   # 再加 820px 平板竖屏
```

| 环境变量 | 作用 |
| --- | --- |
| `MOCK_ONBOARDED=0` | 种子用户没走过开场，首页会送去 `/onboarding` |
| `MOCK_RETRIEVE_MS` / `MOCK_FIRST_TOKEN_MS` / `MOCK_CHAR_MS` | 流式讲解的节奏（检索、首字、每 3 个字） |
| `PW_CHROMIUM` | Chromium 路径（默认 `/opt/pw-browsers/chromium-1194/...`） |

## ⚠️ 它不验证任何东西（口径 F34）

假后端没有 RLS、没有 CHECK、没有事件必需字段、没有检索与生成——讲解是一段写死的文本按字流出来。
**在它上面「能用」不说明任何东西能用。** 它只回答「这一页长什么样、动起来怎么样」。

不进 `verify:guards`，不进构建产物。截图里左下角那个 **N** 是 `next dev` 的开发角标，生产构建没有。
