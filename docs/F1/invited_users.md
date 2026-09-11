# F1 内测 · 被邀请的账号

> **运维记录，不是产品数据。** 由 `scripts/invite_user.mjs` 每次建号后自动追加一行。
>
> **刻意不建表**（口径 F40）：建表就要管 RLS、管 cascade、管它与事件表的一致性，
> 而这是一份 30 行的清单。它也**不该随注销一起消失**——「我们邀请过谁」是运维事实，
> 与用户的个人数据不同；后者由 `auth.users` 的 `on delete cascade` 清空（PRD §12.1）。
>
> ⚠️ 这里**只写邮箱、日期、用途、user_id，不写密码**。密码通过其他渠道单独给到本人。

## 为什么是邀请制（口径 F40）

`/login` 只有登录没有注册入口 —— 阶段 3 人工验收登记过这一条，它是内测的**硬阻塞**：
没有用户就收不到任何埋点数据，而阶段 4 建的整套事件表在没有用户时全是空转（口径 F34）。

30–50 人规模下邀请制比自助注册页省得多：不用做邮箱验证流程、找回密码、防滥用。
那三样都要长期维护，而它们在这个规模上换不到任何东西。

## 怎么邀请

```bash
cp scripts/.env.example scripts/.env && $EDITOR scripts/.env   # 填 URL 与 secret key
node scripts/invite_user.mjs --email someone@example.com --password '……' --note '内测第一批'
```

建完**核对一次** `style_config` 里有没有那一行（口径 F09：没有配置行的用户是坏状态，
他进不了 onboarding、拿不到臂）——脚本会把这句 SQL 打出来。

## 清单

| 邮箱 | 日期 | 用途 | user_id |
| --- | --- | --- | --- |
| a1@test.local | 2026-09-10 | 阶段4验收 主臂 | `65ee1780-f952-4f1f-8f3c-ea17db96380e` |
| a2@test.local | 2026-09-10 | 阶段4验收 主臂 | `50def9f4-755f-447c-aab3-a454bb5b4cb9` |
| a3@test.local | 2026-09-10 | 阶段4验收 主臂 | `28150f95-c3a0-4a1f-b79d-173822fcf514` |
| a4@test.local | 2026-09-10 | 阶段4复验 主臂 | `72088832-3e3a-467a-9651-b7c2c907260d` |
| a5@test.local | 2026-09-10 | 阶段4复验 主臂 | `6ffac4ab-4842-451e-8548-d30d31bb7da4` |
| f1-rls-a@example.com | 2026-09-11 | 真库验证 A | `765c629b-52d7-441f-bdb8-d123872fe53d` |
| f1-rls-b@example.com | 2026-09-11 | 真库验证 B | `1326580d-33b2-45e2-b26f-4c63d4f8aca3` |
