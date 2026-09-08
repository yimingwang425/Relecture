# supabase · 表与 RLS

F1 阶段 1 的交付物。**口径决策表不在这里，在 [`f1_samples/README.md`](../f1_samples/README.md) §一**
（F08–F14 是本阶段新增的）——一张表分成两处必然漂，而漂掉的那份通常是没人再看的那份。

```
migrations/20260907120000_f1_style_config.sql   唯一 DDL 出处
verify_rls_prod.py                              生产验收（正向，publishable key）
verify_rls_local.mjs                            本地反向验证（拆防护，断言必须红）
```

---

## 一、怎么应用

本仓库**没有** Supabase 的 access token / DB 密码（也不该有）。migration 的应用方式：
把 `migrations/20260907120000_f1_style_config.sql` **整份**贴进
Dashboard → SQL Editor 执行。文件是幂等的（`if not exists` / `drop policy if exists`
/ `create or replace`），重复执行安全。

> 之后若配上 access token，可改用 `npx supabase db push`。**在此之前不要在 Dashboard 里
> 手改表结构**——手改的那一次不会进 `migrations/`，本地反向验证跑的就是另一个对象了。

## 二、怎么验

```bash
# 生产 · 正向（需先在 Authentication → Sign In / Providers 关掉 Confirm email）
cd supabase && cp .env.example .env && $EDITOR .env
python3 verify_rls_prod.py

# 本地 · 反向（PGlite，零系统足迹；跑的是 migrations/ 下的原文）
npm install && npm run verify
```

两者分工是口径 F12：**正向验收在生产，破坏性动作在本地。**

`verify_rls_prod.py` 开头有一条硬检查——传进来的 key 若是 `sb_secret_` 开头就直接退出。
用 secret key 跑的 RLS 验收什么也证明不了，而它看起来是绿的（约束 2 / PRD §12.1）。

## 三、这套验收为什么是这个形状

任务书给的验收是一句话：**「用账号 A 的登录态去读账号 B 的行 → 返回空」**。
本地反向验证的 R3 把它拆穿了：**关掉 RLS 并删掉 B 的行，这句话依然成立。**
它在三种完全不同的情况下都绿——RLS 生效、B 没有行、整表被收权——
所以单独拿出来是空转的（通则七 #5）。

故每条「拒绝」断言旁边都放了一条「允许」断言：

| 拒绝 | 它的反向控制 | 没有控制时会怎样 |
| --- | --- | --- |
| A 读不到 B | **B 读得到自己** | 整表不可读也算绿 |
| A 改不动 B | **B 改得动自己** | update 通路根本不通也算绿 |
| `f1_arm` 改不动 | 同上 | 同上 |

`on delete cascade` 也补了一条：PRD §12.1 写着「注销即清空」，而本表**刻意不给 delete
策略**，清空完全依赖外键的 cascade——那是一条写在合规章节里、却从没被任何断言碰过的
承诺（通则七 #1）。现在 `verify_rls_local.mjs` 会真的删一次 `auth.users` 的行看配置是否
随之消失，R6 把外键改成 `no action` 让它变红。**生产上这一条待办**：见 §六。

**还有一条是实测抓出来的**（不是想出来的）：步骤 4「A 冒名 insert `user_id = B`」
原本断言的是「报了错」。`user_id` 是主键，且 F09 保证每个合法用户都已有行，
所以这个 insert 必然也撞唯一约束——本地实测 RLS 开 → `42501`，**RLS 关 → `23505`，
照样报错，断言照样绿**。它测的是主键不是 `with check`。改成断言错误码后，R1 才把它变红。

反向验证跑的四组拆解：

| | 拆掉什么 | 必须变红的断言 |
| --- | --- | --- |
| R1 | `disable row level security` | A 读 B / A 改 B / A 冒名 insert |
| R2 | `f1_arm` 钉死触发器 | B 改自己的臂 |
| R3 | RLS + B 的行一起去掉 | **演示**「A 读 B 为空」仍绿、而「B 读自己」变红 |
| R4 | 触发器 + update 策略的 `with check` | 演示 `with check` 是承重的 |
| R5 | 迁移文本里的**回填**那一节 | 存量用户补不上——证明回填在做事，不是死代码 |
| R6 | 外键的 `on delete cascade` | 注销清不掉配置（`23503` + 残留行）——证明 §12.1 的「注销即清空」靠的是它 |

## 四、表里刻意不存的东西

不要加回来（任务书阶段 1）：

| 不存 | 为什么 |
| --- | --- |
| `chinese_ratio_min` · `max_quote_chars` | 恒 0.85 / 恒 100，是常量。存了就会漂（口径 F03） |
| `config_fingerprint` · `prompt_profile_id` · `metric_set_id` | Python 侧现算。TS 一份 Python 一份必然漂（约束 3 / 口径 F04） |
| `config_version` | `a_rules.config.CONFIG_VERSION` 是模块常量，不是每用户字段 |
| 配置历史表 | 修改次数从埋点事件反推。多一张表就多一处会与事件流不一致的地方 |

## 五、迁移里那段回填

`auth.users` 的触发器**只对新注册用户生效**。以后任何一次 migration 若重建触发器
或改 `f1_assign_arm`，存量用户会整批漏掉，表现是「某些用户进不了 onboarding」——
不报错，要靠用户反馈才发现。回填那段防的是这个。

**它在 2026-09-07 首次执行时补 0 行（`auth.users` 为空），现在依然是空操作。**
一段永远补 0 行的语句和一段被删掉的语句，在生产上表现完全一样——
所以它必须在别处被真的执行过一次，否则没人能说出它还灵不灵。
`verify_rls_local.mjs` 的「回填」一节构造了它唯一会做事的场景
（存量用户 + 触发器缺失），R5 则把这段语句从迁移文本里切掉，断言必须变红。
| **delete 策略** | 注销走 `auth.users` 的 `on delete cascade`（§12.1「注销即清空」） |


---

## 六、待办 · 生产上的两个验收账号

`verify_rls_prod.py` 会在生产 `auth.users` 里留下两个账号
（`F1_VERIFY_EMAIL_A` / `_B`，默认 `f1-rls-a@example.com` / `f1-rls-b@example.com`）。
脚本没有 delete 权限，清不掉自己——那是 RLS 的正确行为，不是缺陷。

**请在 Dashboard → Authentication → Users 删掉这两个，并顺手看一眼
`select count(*) from public.style_config` 归零。** 一举两得：

1. 生产上补掉 §12.1「注销即清空」的验收——本地已验（R6），生产未验；
2. 不把测试账号留在真实用户表里。v1 只有 30–50 个用户，两条测试行会同时污染
   AB 分组的计数与「onboarding 完成率」的分母，而它们**看起来和真实用户一模一样**。

之后每次重跑 `verify_rls_prod.py` 都会重建这两个账号，跑完照此清理。
