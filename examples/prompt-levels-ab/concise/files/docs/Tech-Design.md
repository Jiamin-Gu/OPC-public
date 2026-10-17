# 技术方案：校园小账本 —— 大学生生活费记账微信小程序（MVP）

## 0. 文档信息

| 项 | 内容 |
| --- | --- |
| 版本 | v0.1（草案，待同事评审） |
| 作者 | 工程师 |
| 依据文档 | `docs/PRD.md`（v0.1，PRD 为准） |
| 命名说明 | PRD「关联文档」中写的 `docs/TECH.md` 即本文，文件名以 CEO 指令的 `docs/Tech-Design.md` 为准；建议 PM 后续统一引用（见 §9.1） |
| 本阶段边界 | **只写方案，不写业务代码**；文中出现的代码片段均为**接口规格/伪代码**，用于表达约束，不是实现 |
| 面向读者 | 产品经理、设计师、测试 |

**方案一句话**：原生微信小程序（TS）+ 微信云开发（云函数 + 云数据库），客户端不直连数据库、所有写操作走云函数；金额全程以「分」整数存储；记账日期用 `YYYY-MM-DD` 字符串 + `monthKey` 冗余字段；写入幂等靠 `clientRequestId` 唯一索引；弱网靠本地待同步队列 + 补传。

> ⚠️ **数据说明**：本方案中没有线上流量、真实用户规模和可运行环境。所有容量、性能、工作量数字均为**估算**，估算依据写在对应的表格里。凡未实际运行的代码，本文不声称"已验证"。

---

## 1. 需求映射

每个 P0 功能映射到实现模块与关键数据。

| PRD 功能 | 实现模块（客户端） | 实现模块（云函数侧） | 关键数据表 | 备注 |
| --- | --- | --- | --- | --- |
| P0-1 快速记一笔 | 主包 `pages/record`、`utils/amount.ts`、`utils/date.ts`、`api/txn.ts`、`storage/pending-queue.ts` | `api` 云函数 `txn.create`、`validator`、`service/txn` | `transactions` | 记账 ≤5 秒要求 → 一次 `callFunction` 完成写入 + 预算进度回传（不做二次请求） |
| P0-2 账单列表与月度汇总 | 分包 A `pages/bills`、`pages/txn-detail` | `txn.list`、`stat.overview`、`txn.update`、`txn.delete`、`txn.restore` | `transactions` | 编辑/删除后所有汇总由**服务端重算返回**，客户端不自己算 |
| P0-3 分类预算与超支提醒 | 分包 A `pages/budget`、主包首页进度条 | `budget.get`、`budget.save`、`budget.copyFromLastMonth` | `budgets` | 80%/100% 阈值判定在服务端；提醒内容与文案由服务端返回，客户端只展示 |
| P0-4 微信一键登录与云同步 | `app.ts`、`api/auth.ts`、`storage/pending-queue.ts` | `auth.login`、`user.updateProfile`、`user.deleteAccount` | `users` | 静默登录；登录失败降级"本地模式"，不阻塞记账 |
| P0-5 基础图表 | 分包 B `pages/stats`（含图表库） | `stat.overview` | `transactions` | 与账单页**共用同一个聚合接口**，保证口径一致 |
| 埋点（PRD §6.4） | `utils/track.ts`（批量上报） | `track.event` | `events`（可选） | 字段口径见 §8.3，需 PM 确认 |
| 软删除 90 天清理 | — | 定时触发器 `cleanupSoftDeleted` | `transactions`、`budgets` | 每天 1 次 |

---

## 2. 架构概览

### 2.1 架构模式

**单体 + 单一后端入口的"前后端分离"。** 不拆微服务、不引入消息队列、不做读写分离——理由是：单人开发、MVP 阶段预估日活量级在百级（估算依据见 §6.4），拆服务带来的运维与一致性问题远大于收益。云函数内部按 **router → validator → service → repository** 分层，**核心业务规则（金额、日期归属、超支阈值）集中在 `service`，不依赖小程序 UI 与云数据库 SDK**，这样它可以被单元测试直接覆盖，也是将来迁移后端的低成本出口。

### 2.2 模块划分与依赖方向

```
┌──────────────── 微信小程序（原生 + TypeScript）─────────────────────┐
│ 主包  pages/record(记账首页)  pages/mine(我的)  pages/login(启动)     │
│ 分包A pages/bills  pages/txn-detail  pages/budget                    │
│ 分包B pages/stats（含图表库，唯一体积大户）                           │
│                                                                      │
│  store/        跨页共享：当前用户、预算快照、待同步条数（轻量订阅）      │
│  api/          统一 callFunction 封装：action 路由、错误码、重试、幂等键 │
│  storage/      本地待同步队列、最近快照缓存、settings                   │
│  utils/        金额(分↔元)、日期(UTC+8)、uuid、校验                     │
└───────────────────────────────┬──────────────────────────────────────┘
                     cloud.callFunction（微信内部链路，无需备案域名）
┌───────────────────────────────▼──────────────────────────────────────┐
│ 云函数 api（Node.js，单函数 + action 路由）                            │
│   router ──▶ validator（入参白名单校验）──▶ service（纯业务逻辑）        │
│                                              └──▶ repository（唯一接触  │
│                                                   云数据库 SDK 的地方） │
│ 定时触发器 cleanupSoftDeleted（每日硬删 90 天前的软删记录）              │
└───────────────────────────────┬──────────────────────────────────────┘
                        云函数以管理端身份访问（客户端无直连权限）
┌───────────────────────────────▼──────────────────────────────────────┐
│ 云数据库：users / transactions / budgets / events                     │
│ 云存储：头像（可选，P0 头像授权本就可选）                               │
└──────────────────────────────────────────────────────────────────────┘
```

**依赖方向铁律**（防止后续腐化）：

- `service` 只依赖 `repository` 的**接口**，不 `import` 云数据库；测试时注入内存实现。
- `repository` 是唯一 `require('wx-server-sdk')` 的地方；将来迁到自建后端只需重写这一层。
- 客户端 `utils/` 不依赖 `api/`，`api/` 不依赖页面；页面不直接读本地存储，走 `storage/`。
- **客户端不做业务计算**：预算进度、超支提醒、月度汇总全部由服务端返回，客户端只格式化展示。避免"两套算法算出两个数"。

### 2.3 登录鉴权方案（openid / session 流程）

选择云开发后，鉴权链路比自建短很多，关键点是**客户端拿不到也不传递 openid**。

```
小程序启动
  └─ wx.cloud.init({ env })            // env 由构建配置区分 dev/staging/prod
  └─ cloud.callFunction('api', { action: 'auth.login', payload: { privacyAccepted } })
        │
        │  云函数内部：const { OPENID, UNIONID } = cloud.getWXContext()
        │  —— OPENID 由微信平台在调用链路上注入，云函数天然可信，客户端无法伪造
        ▼
   按 openid 查 users
     命中  → 更新 lastLoginAt，返回 { userId, isNewUser:false }
     未命中 → 创建 users 文档（_id 为新生成的 userId），返回 { userId, isNewUser:true }
  └─ 客户端把 userId 存本地，后续所有请求只带 action + payload（**不带 userId**）
```

**为什么不需要自签 session token**：云函数的每一次调用都由微信平台注入 `OPENID`，等价于"每次都做了鉴权"，没有独立的会话票据可被窃取或过期。代价是**必须把所有数据访问收口到云函数**——见 ADR-2，客户端直连数据库会直接绕过全部校验。

| 场景 | 处理 |
| --- | --- |
| `auth.login` 失败（网络/微信侧异常） | 客户端进入**本地模式**：`store.userId = null`，可继续记账（写本地队列）；顶部提示"未同步"；下次启动或网络恢复时重试登录并补传 |
| 本地模式下的历史数据 | 只展示本地队列 + 本地缓存的最近快照，标注"未同步"；数据合并在登录成功后自动完成 |
| 数据合并规则 | 纯插入式合并：以 `clientRequestId` 去重（设备各自生成的 UUID 不会冲突），服务端没有的补插，服务端已有的跳过。**不涉及字段级合并**，因为本地模式是"只新增不编辑"（编辑/删除仅在登录态可用） |
| 用户注销 | `user.deleteAccount`：`users.status='deleted'`、软删该用户全部 `transactions`/`budgets`、清空本地缓存；90 天后由定时任务硬删 |
| unionid | P0 不使用（需绑定微信开放平台账号）；字段预留 |

> **对 PRD §7.2 问题 1（是否允许未登录长期使用）的回答**：本方案**不依赖 CEO 的选择**。启动即静默登录，"登录失败 → 本地模式可记账"是纯降级路径，两种产品口径都兼容。需 CEO 确认的只剩一点：**本地模式下是否允许查看统计页**（本方案默认：允许，但页面上标注"仅统计未同步的本地数据"）。

---

## 3. 技术选型

| 选项 | 选择 | 理由 | 放弃的备选 |
| --- | --- | --- | --- |
| 小程序框架 | **微信原生小程序 + TypeScript**（官方 TS 模板 + `miniprogram-api-typings`） | 单端微信，原生包体积与中低端安卓性能最优，无构建层；TS 能拦住金额/日期这类最容易写错的类型错误 | Taro / uni-app：跨端收益为零，却多一层编译与调试成本，图表与 Canvas 性能更难控 |
| UI 组件库 | **Vant Weapp**（按需引入） | 组件覆盖 TabBar/弹层/进度条/日期选择；按需引入体积可控；社区成熟 | TDesign miniprogram（也可选，视觉更统一，留给设计师定稿）；自研全部组件（工作量不值） |
| 状态管理 | **自研轻量 store（约 50 行，发布订阅）** | 跨页共享状态只有「当前用户 + 预算快照 + 待同步条数」三项；真相同源是服务端，页面 `onShow` 主动拉取刷新，不需要全局响应式框架 | `mobx-miniprogram`（2KB 也可，但当前共享状态太少，收益不抵依赖）；`globalData` 裸用（无变更通知，页面间会不同步） |
| 后端 | **微信云开发 云函数**（Node.js） | 免服务器/域名/备案/证书/运维；与登录天然集成；灰度用户量级下成本≈0 | 自建后端（Node/Java + 云服务器 + HTTPS + 备案 + MySQL）：工作量与运维成本对 1 人团队不成比例，收益（SQL 聚合、强事务）在 MVP 用不上。迁移触发条件见 ADR-1 |
| 数据库 | **微信云开发 云数据库**（文档型） | 与云函数同环境、免运维、免连接池；支持 `aggregate` 聚合与**唯一索引**（幂等必需） | MySQL/MongoDB 自建：需要运维、备份、连接管理 |
| 图表库 | **uCharts（小程序版，仅放分包 B）** | 只画环形图 + 柱状图，体积小（几十 KB 级）、支持 Canvas 2D、支持点击事件（PRD 要求点分类看明细） | ECharts(`ec-canvas`)：定制包也常在几百 KB，对包体积和低端机不划算；纯 Canvas 自绘：无依赖但坐标轴/标签/命中区域自己写，性价比低（作为备选，见 ADR-7） |
| 语言/构建 | TypeScript + 微信开发者工具自带构建（`es6→es5`、代码压缩、按需注入） | 不引入 webpack/vite：小程序有自己的编译链，加一层只会更难排查 | 自建构建流水线（P0 不值） |
| 环境隔离 | 云开发 **dev / staging / prod 三环境** | 测试不污染生产数据；测试环境用测试 openid | 单环境（无法安全联调） |

**包体积预算（估算，需上传后实测校准）**

| 分区 | 内容 | 预算 |
| --- | --- | --- |
| 主包 | 框架 + 记账首页 + 我的 + 启动页 + utils/api/store | ≤ 1.2 MB |
| 分包 A | 账单列表 / 记录详情 / 预算设置 | ≤ 600 KB |
| 分包 B | 统计页 + uCharts | ≤ 700 KB |
| 静态资源 | 图标用 iconfont/内联 SVG，头像走云存储 | ≤ 100 KB |

> 硬约束：**主包 ≤ 2 MB**（微信平台限制），总包 ≤ 20 MB。手段见 §6.5。

---

## 4. 数据模型

### 4.1 通用约定

| 约定 | 规则 |
| --- | --- |
| **金额** | 一律以**分**存整数（`amountCent: int`）。单笔上限 999999.99 元 = `99999999` 分。**全程不出现浮点运算**，元↔分只在输入解析与展示格式化两处发生 |
| **时区** | 全系统统一 **UTC+8（北京时间）**。云函数运行环境时区为 UTC，**禁止**用 `new Date().toLocaleDateString()` 取"今天"，必须用 §4.5 的工具函数 |
| **记账日期** | `occurredDate: string 'YYYY-MM-DD'`（用户视角的日期，天然是北京时间） |
| **归属月份** | `monthKey: string 'YYYY-MM'`，由 `occurredDate.slice(0,7)` 推导的**冗余字段**，用于索引与聚合；服务端**忽略**客户端传来的 monthKey |
| **系统时间** | `createdAt`/`updatedAt`/`deletedAt` 一律由**服务端**生成（`Date` 类型），不信任客户端时间 |
| **软删除** | 所有业务写入不物理删除，置 `deletedAt`；查询一律带 `deletedAt == null` 条件；90 天后由定时任务硬删 |
| **主键** | `_id` 由系统生成（uuid），**不把 openid 当主键**，对外接口只暴露 `userId` |
| **镜像字段** | 云开发客户端写入会自动加 `_openid`，但云函数以管理端身份写入**不会**。因此统一**显式存 `openid` 字段**，不依赖 `_openid` |

### 4.2 表结构

#### `users`

| 字段 | 类型 | 必填 | 说明/约束 |
| --- | --- | --- | --- |
| `_id` | string | 是 | userId（系统生成 uuid） |
| `openid` | string | 是 | 微信 openid，**唯一索引** |
| `unionid` | string\|null | 否 | 预留，P0 不用 |
| `nickname` | string\|null | 否 | ≤ 30 字符，可选授权 |
| `avatarUrl` | string\|null | 否 | 云存储 fileID 或 https 地址 |
| `status` | string | 是 | `active` \| `deleted` |
| `privacyAcceptedAt` | Date\|null | 否 | 隐私协议同意时间（合规留痕） |
| `settings` | object | 是 | `{ budgetCycle:'naturalMonth', guideSeen:bool }` |
| `lastLoginAt` | Date | 是 | 服务端时间 |
| `createdAt` / `updatedAt` | Date | 是 | 服务端时间 |
| `schemaVersion` | int | 是 | 数据迁移用，初始 1 |

**索引**：`unique(openid)`、`(status)`

#### `transactions`（账目）

| 字段 | 类型 | 必填 | 说明/约束 |
| --- | --- | --- | --- |
| `_id` | string | 是 | 订单/记录 ID |
| `userId` | string | 是 | 归属用户；**所有查询强制带此条件** |
| `type` | string | 是 | `expense` \| `income` |
| `amountCent` | int | 是 | 1 ≤ x ≤ 99999999（分） |
| `categoryId` | string | 是 | 必须在分类白名单内，且与 `type` 匹配 |
| `note` | string | 是 | 默认 `''`，≤ 50 个 Unicode 字符 |
| `occurredDate` | string | 是 | `YYYY-MM-DD`，范围：今天及过去 1 年内，禁止未来 |
| `monthKey` | string | 是 | `YYYY-MM`，由 `occurredDate` 推导（冗余，供索引/聚合） |
| `clientRequestId` | string | 是 | 客户端生成的 UUID v4，**幂等键** |
| `deviceId` | string | 是 | 客户端生成并持久化，用于排查多端冲突 |
| `createdAt` / `updatedAt` | Date | 是 | 服务端时间 |
| `deletedAt` | Date\|null | 否 | 软删除标记 |
| `restoredAt` | Date\|null | 否 | 撤销删除留痕（可观测性） |
| `schemaVersion` | int | 是 | 初始 1 |

**索引**：

| 索引 | 用途 |
| --- | --- |
| `unique(userId, clientRequestId)` | **幂等**：重复提交只落一笔 |
| `(userId, deletedAt, occurredDate desc, _id desc)` | 账单列表分页排序（日期倒序） |
| `(userId, monthKey, type, deletedAt)` | 月度汇总 / 分类占比 / 图表聚合 |

#### `budgets`

| 字段 | 类型 | 必填 | 说明/约束 |
| --- | --- | --- | --- |
| `_id` | string | 是 | |
| `userId` | string | 是 | |
| `monthKey` | string | 是 | `YYYY-MM`，**预算按自然月独立** |
| `kind` | string | 是 | `total` \| `category` |
| `categoryId` | string | 是 | `kind=total` 时固定为哨兵值 `'__total__'`（**不用 null**，避免唯一索引对 null 的处理歧义） |
| `amountCent` | int | 是 | ≥ 1（分）；0/负数/非数字一律拒绝 |
| `clientRequestId` | string | 是 | 幂等键 |
| `createdAt` / `updatedAt` | Date | 是 | |
| `deletedAt` | Date\|null | 否 | |

**索引**：`unique(userId, monthKey, kind, categoryId)`（同时充当幂等保护与"一月一值"约束）、`(userId, monthKey)`

> **"沿用上月预算"**：不是共享一条记录，而是把上月记录**复制成本月新文档**（新 `_id`、新 `clientRequestId`、`monthKey=本月`），保证预算按月独立、修改本月不影响上月。

#### `events`（埋点，可选，可延后）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userId` | string | 可为空（未登录期） |
| `eventName` | string | 如 `record_saved` |
| `props` | object | 见 §8.3，**不含**备注原文 |
| `clientTime` | Date | 客户端时间（仅作参考） |
| `serverTime` | Date | 服务端时间 |
| `appVersion` / `platform` | string | |

**索引**：`(eventName, serverTime desc)`

#### `categories`（P0 不建表）

P0 分类固定，**以代码常量（前后端共享的 JSON）实现**，`transactions.categoryId` 存字符串 ID：

- 支出：`food`餐饮、`transport`交通、`shopping`购物、`entertainment`娱乐、`study`学习、`utility`生活缴费、`medical`医疗、`other_expense`其他
- 收入：`allowance`生活费、`parttime`兼职、`scholarship`奖学金、`redpacket`红包、`other_income`其他

每条含 `{ id, name, type, icon, color, order }`。**好处**：记一笔不需要多一次分类查询（对 ≤5 秒目标直接有利）。
**P1 自定义分类的迁移路径**：新增 `categories` 集合（`{_id, userId|''系统预置, name, type, icon, order, deletedAt}`）；校验时"先查常量表，再查用户表"；`transactions` 无需改动，因为 `categoryId` 已是字符串。

### 4.3 状态流转

**账目**：

```
（不存在）──txn.create──▶ active ──txn.update──▶ active（字段更新，updatedAt=服务端时间）
                            │
                            ├──txn.delete──▶ deleted（deletedAt 置值）──txn.restore(≤5s)──▶ active
                            │                        │
                            │                        └── 90 天后 定时任务 ──▶ 物理删除
                            └── 重复 clientRequestId 提交 ──▶ 仍为 1 条（返回首次结果，duplicated=true）
```

**预算**：`（不存在）→ 已设置 →（改金额）已设置 →（改月份）新月份为未设置`。
本月未设置时，`budget.get` 返回 `lastMonthTotal`，由首页提示"是否沿用上月预算（X 元）"。

### 4.4 金额存储方式的取舍

| 方案 | 决定 |
| --- | --- |
| **整数分**（选中） | 加减乘除无舍入误差，聚合结果与逐笔相加完全一致；与 PRD "不四舍五入导致对不上账"一致 |
| 浮点元 | 放弃：`0.1+0.2 !== 0.3`，累加上百笔必然出现 1 分偏差 |
| Decimal/字符串元 | 放弃：云数据库无原生 Decimal 类型，字符串无法直接聚合求和 |

**输入解析规格（`utils/amount.ts`）**——这是最容易出错的一处，规则写死：

```
parseAmountToCent(input: string) -> { ok: true, cent: number } | { ok: false, code: ErrorCode }
规则：
 1. 去除首尾空格；空字符串                → AMOUNT_REQUIRED
 2. 含 '-' 或非 [0-9.] 字符               → AMOUNT_NEGATIVE / AMOUNT_INVALID
 3. 小数位 > 2 位 → **直接截断**（不四舍五入）:"50.999" → 50.99 → 5099
    （截断而非进位，保证界面显示与入库金额永远一致）
 4. 整数字面量 > 6 位 → AMOUNT_OUT_OF_RANGE（> 999999.99 元）
 5. 值为 0 → AMOUNT_ZERO
 6. 用字符串拼接成整数，**不经过 parseFloat**："50.9" → 整数部分 50*100 + 小数部分补齐 "90" = 5090
展示：formatCentToYuan(5099) -> "50.99"（固定 2 位小数，千分位由 UI 决定）
```

### 4.5 日期与时区处理规格（`utils/date.ts` + 云函数同名模块）

云函数运行在 UTC 时区，这是最容易出错的地方，规则如下：

```
beijingToday(): 'YYYY-MM-DD'
  const t = new Date(Date.now() + 8 * 3600 * 1000)
  用 t.getUTCFullYear()/getUTCMonth()/getUTCDate() 拼装
  —— 取"北京时间的今天"，与运行环境时区无关

monthKeyOf(occurredDate): string      // '2025-06-01' -> '2025-06'
  occurredDate.slice(0, 7)

isValidDateString(s): bool            // 正则 ^\d{4}-\d{2}-\d{2}$ 且日期真实存在（拒绝 2025-02-30）

validateOccurredDate(s, today = beijingToday()): ok | DATE_IN_FUTURE | DATE_OUT_OF_RANGE
  s > today              → DATE_IN_FUTURE（未来日期，PRD 要求禁止）
  s < today - 365 天      → DATE_OUT_OF_RANGE（PRD 要求最多回溯 1 年）
  字符串比较即可，因为 'YYYY-MM-DD' 字典序 == 时间序

边界规则：跨月归属一律看 occurredDate，与 createdAt 无关
（PRD 验收：5 月 31 日的记录不计入 6 月汇总；把日期从 5 月改到 6 月，两个月汇总同时变化）
```

---

## 5. 接口设计

### 5.1 统一调用约定

客户端**只调用一个云函数** `api`（见 ADR-9），用 `action` 路由：

```js
// 请求
cloud.callFunction({
  name: 'api',
  data: {
    action: 'txn.create',
    payload: { /* 见下 */ },
    clientRequestId: '9f1c...',   // 写操作必填（UUID v4）
    appVersion: '1.0.0',
    platform: 'android'
  }
})

// 响应（所有接口统一形状）
{
  code: 0,                       // 0 = 成功；非 0 见 §5.3
  message: '',                   // 面向开发者的描述；面向用户的文案在 data 或客户端映射
  data: { /* 各接口自己的结构 */ },
  requestId: 'req_7c2a...'        // 服务端生成，用于日志排查
}
```

**铁律**：

- **客户端永远不传 `userId` / `openid`**，服务端从 `cloud.getWXContext().OPENID` 反查；传了也忽略。
- 所有 `payload` 字段做**类型与范围白名单校验**，未识别字段丢弃（不落库）。
- `clientRequestId` 必填的接口：`txn.create` / `txn.update` / `budget.save` / `budget.copyFromLastMonth` / `track.event`。

### 5.2 接口清单与示例

#### ① `auth.login` —— 静默登录/建号

```json
// 入参
{ "action": "auth.login", "payload": { "privacyAccepted": true, "nickname": null, "avatarUrl": null } }
// 出参
{ "code": 0, "data": {
    "userId": "u_3f9a1c",
    "isNewUser": false,
    "serverTime": "2025-06-01T10:00:00.000Z",
    "settings": { "budgetCycle": "naturalMonth", "guideSeen": true }
}, "requestId": "req_7c2a" }
// 幂等：重复调用只更新 lastLoginAt，不会重复建号（openid 唯一索引兜底）
```

#### ② `txn.create` —— 记一笔（**核心接口**）

```json
// 入参
{ "action": "txn.create",
  "clientRequestId": "9f1c2d3e-...",
  "payload": {
    "type": "expense",
    "amountCent": 5000,            // 50.00 元
    "categoryId": "food",
    "note": "午饭",
    "occurredDate": "2025-06-01",
    "deviceId": "d_ab12"
  } }

// 出参（一次请求同时返回写入结果 + 最新预算进度 + 需展示的提醒，避免二次请求，满足 ≤5 秒）
{ "code": 0, "data": {
    "txn": { "id": "t_88f1", "type": "expense", "amountCent": 5000, "categoryId": "food",
             "note": "午饭", "occurredDate": "2025-06-01", "monthKey": "2025-06",
             "createdAt": "2025-06-01T02:00:01.120Z" },
    "duplicated": false,           // true = 命中幂等，是重复提交
    "budget": {
      "monthKey": "2025-06",
      "total": { "amountCent": 200000, "usedCent": 84000, "remainCent": 116000, "ratio": 0.42, "level": "normal" },
      "categories": [
        { "categoryId": "food", "amountCent": 80000, "usedCent": 64000, "remainCent": 16000, "ratio": 0.8, "level": "warn" }
      ],
      "alerts": [
        { "scope": "category", "categoryId": "food", "threshold": 0.8,
          "message": "餐饮已用 80%，还剩 160 元" }
      ]
    }
}, "requestId": "req_..." }

// 错误：1002/1003/1004/1005/1006（见 §5.3）
// level 取值：normal | warn(≥80%) | over(≥100%)
// alerts 由服务端去重合并（总预算与分类同时越线只给一条，PRD §P0-3）
```

#### ③ `txn.update` —— 编辑

```json
// 入参（patch 只传要改的字段）
{ "action": "txn.update", "clientRequestId": "uuid",
  "payload": { "id": "t_88f1",
    "patch": { "occurredDate": "2025-06-02", "amountCent": 6000 },
    "baseUpdatedAt": "2025-06-01T02:00:01.120Z" } }   // 可选，P1 用于乐观锁
// 出参
{ "code": 0, "data": { "txn": { /* 更新后 */ }, "budget": { /* 同 ②，服务端重算 */ } } }
// 规则：patch 里出现 occurredDate 时，服务端重新推导 monthKey；
//       代码 3002 表示该记录已被删除，不允许编辑。
//       冲突策略：MVP = last-write-wins（服务端时间），记录 lastDeviceId 便于排查（ADR-6）
```

#### ④ `txn.delete` / `txn.restore` —— 软删除与 5 秒撤销

```json
{ "action": "txn.delete", "payload": { "id": "t_88f1" } }
{ "code": 0, "data": { "id": "t_88f1", "deletedAt": "2025-06-01T02:05:00.000Z",
                       "undoDeadline": "2025-06-01T02:05:05.000Z", "budget": { /* 重算 */ } } }

{ "action": "txn.restore", "payload": { "id": "t_88f1" } }
// 服务端校验 now - deletedAt ≤ 5s + 1s 容差，否则返回 3004 UNDO_EXPIRED
// 撤销成功后 budget 同样重算返回，保证首页/账单/统计三处口径一致
```

#### ⑤ `txn.list` —— 按条件查询账单（游标分页）

```json
// 入参
{ "action": "txn.list", "payload": {
    "monthKey": "2025-06",       // 可选；不传 = 不限月份（配合 dateFrom/dateTo）
    "dateFrom": null, "dateTo": null,
    "type": null,                // null=全部 | expense | income
    "categoryId": null,
    "pageSize": 20,              // 上限 50
    "cursor": null               // 首页不传；后续传上一页返回的 nextCursor
} }
// 出参
{ "code": 0, "data": {
    "items": [
      { "id": "t_88f1", "type": "expense", "amountCent": 5000, "categoryId": "food",
        "note": "午饭", "occurredDate": "2025-06-01", "updatedAt": "..." }
    ],
    "nextCursor": "eyJkIjoiMjAyNS0wNi0wMSIsImMiOiIyMDI1LTA2LTAxVDAyOjAwOjAxLjEyMFoiLCJpIjoidF84OGYxIn0",
    "hasMore": true,
    "total": 37                  // 可选，仅用于展示"共 N 笔"
} }
// 排序：occurredDate desc, createdAt desc, _id desc（索引 §4.2，游标对应三者）
// 不返回 deletedAt 非空的记录
// 按天分组的"当日小计"由客户端对已加载项累加（仅展示用）；月度汇总一律用 stat.overview
```

#### ⑥ `stat.overview` —— 月度汇总 + 分类占比 + 每日趋势（账单页与统计页共用）

```json
// 入参
{ "action": "stat.overview", "payload": { "monthKey": "2025-06", "type": "expense" } }
// 出参
{ "code": 0, "data": {
    "monthKey": "2025-06",
    "summary": { "expenseCent": 84000, "incomeCent": 200000, "balanceCent": 116000, "txnCount": 12 },
    "byCategory": [
      { "categoryId": "food", "amountCent": 42000, "ratio": 0.5, "count": 8 },
      { "categoryId": "transport", "amountCent": 42000, "ratio": 0.5, "count": 4 }
    ],
    "daily": [
      { "date": "2025-06-01", "expenseCent": 5000 },
      { "date": "2025-06-02", "expenseCent": 0 }
    ],
    "budget": { /* 同 ② 的 budget 结构 */ }
} }
// 无数据时：byCategory=[]、daily=[]、summary 全 0（客户端据此显示空状态，不画空图表）
// 口径保证：与 txn.list 的分组一致，都只按 monthKey(=occurredDate 前 7 位) 过滤
```

#### ⑦ `budget.get` / `budget.save` / `budget.copyFromLastMonth`

```json
{ "action": "budget.get", "payload": { "monthKey": "2025-06" } }
{ "code": 0, "data": {
    "monthKey": "2025-06",
    "isSet": true,
    "total": { "amountCent": 200000, "usedCent": 84000, "remainCent": 116000, "ratio": 0.42, "level": "normal" },
    "categories": [ { "categoryId": "food", "amountCent": 80000, "usedCent": 64000,
                      "remainCent": 16000, "ratio": 0.8, "level": "warn" } ],
    "lastMonthTotal": { "amountCent": 200000 }     // 用于"是否沿用上月预算"提示
} }

// 保存（幂等；整月覆盖式提交，未列出的分类预算视为删除）
{ "action": "budget.save", "clientRequestId": "uuid",
  "payload": { "monthKey": "2025-06",
    "total": { "amountCent": 200000 },
    "categories": [ { "categoryId": "food", "amountCent": 80000 } ] } }
{ "code": 0, "data": {
    "monthKey": "2025-06",
    "total": { /* 快照 */ },
    "categories": [ /* 快照 */ ],
    "warnings": [ { "code": "SUM_EXCEEDS_TOTAL", "message": "分类预算合计已超过总预算，是否继续？" } ]
} }
// 注意：SUM_EXCEEDS_TOTAL 是 warning 不是 error —— PRD 要求"允许保存但给出提醒"
// 金额为 0/负数 → 1003 AMOUNT_OUT_OF_RANGE；非数字 → 1002 PARAM_INVALID

{ "action": "budget.copyFromLastMonth", "payload": { "monthKey": "2025-06" } }
// 复制上月全部预算到本月，返回本月快照；上月无预算 → 3001 NOT_FOUND
```

#### ⑧ `user.*`

```json
{ "action": "user.updateProfile", "payload": { "nickname": "小明", "avatarUrl": "cloud://..." } }
{ "action": "user.getProfile" }        // 返回 userId/nickname/avatarUrl/createdAt/privacyAcceptedAt
{ "action": "user.deleteAccount", "payload": { "confirm": "注销账号" } }   // 需显式确认串，防误触
// deleteAccount：status=deleted + 软删业务数据 + 返回 { deletedAt, purgeAfter }
// 返回后客户端必须清空本地缓存与待同步队列
```

### 5.3 错误码

| code | 常量 | 含义 | 客户端处理 |
| --- | --- | --- | --- |
| 0 | OK | 成功 | — |
| 1001 | PARAM_MISSING | 必填字段缺失 | 开发期错误，提示通用失败并上报 |
| 1002 | PARAM_INVALID | 参数类型/格式非法（如 categoryId 不在白名单、日期格式错） | 同上 |
| 1003 | AMOUNT_OUT_OF_RANGE | 金额 ≤0 / >999999.99 / 预算金额 ≤0 | "金额需大于 0"或"单笔金额不能超过 999999.99" |
| 1004 | DATE_IN_FUTURE | 日期晚于今天（UTC+8） | "不能记录未来的账" |
| 1005 | DATE_OUT_OF_RANGE | 日期早于 1 年前 | "最多只能补记 1 年前的账" |
| 1006 | NOTE_TOO_LONG | 备注超 50 字 | "备注最多 50 字" |
| 1007 | INVALID_CATEGORY | 分类不存在或与收支类型不匹配 | 提示重选分类 |
| 2001 | UNAUTHORIZED | 无法取得 OPENID（调用链异常） | 提示重试；转本地模式 |
| 2002 | USER_DELETED | 账号已注销 | 引导重新登录 |
| 3001 | NOT_FOUND | 记录/预算不存在 | 刷新列表 |
| 3002 | ALREADY_DELETED | 记录已删除（编辑/删除时报） | 刷新列表并提示"该记录已删除" |
| 3003 | FORBIDDEN | 越权访问他人数据 | 视为异常，上报 |
| 3004 | UNDO_EXPIRED | 超过 5 秒撤销窗口 | toast "撤销已超时" |
| 4001 | RATE_LIMITED | 写入过于频繁（限流，见 §6.2） | 提示稍后重试 |
| 5000 | INTERNAL_ERROR | 服务端未知异常 | 提示"服务繁忙"，保留本地队列稍后重试 |
| 5001 | DB_ERROR | 数据库错误 | 同上（可重试） |
| 5002 | TIMEOUT | 云函数超时 | 同上（可重试） |

> **可重试 vs 不可重试**：客户端只对 `5000/5001/5002` 与网络错误自动重试；`1000~4999` 为业务错误，重试无意义（重复提交也只会被幂等拦下，但会污染日志），直接提示用户。

### 5.4 幂等流程（服务端，`txn.create` 为例）

```
1. clientRequestId 必填且为 UUID 格式，否则 1001
2. 查 transactions where { userId, clientRequestId }
   命中 → 返回既有 txn + 当前 budget 快照，duplicated = true   ← 重复提交不报错
3. 未命中 → 插入
4. 若插入抛唯一键冲突（并发两条同时到达）→ 重新查询并按第 2 步返回 duplicated = true
（唯一索引是最终防线，不依赖"先查后插"的竞态窗口）
```

---

## 6. 关键难点与对策

| 难点 | 影响 | 对策 |
| --- | --- | --- |
| **离线/弱网提交** | 记账丢失 = 用户信任崩塌（PRD 回滚条件之一） | 本地待同步队列，见 §6.1 |
| **并发与幂等** | 重复记账、多端互相覆盖 | `clientRequestId` 唯一索引 + last-write-wins，见 §6.2 |
| **金额精度** | 汇总对不上账 | 全程整数分，字符串解析，见 §4.4 |
| **统计聚合性能** | 统计页变慢、耗云资源额度 | 复合索引 + 单次聚合接口，见 §6.3 |
| **小程序包体积** | 主包超 2MB 无法上传/审核 | 分包 + 按需注入 + 轻量图表库，见 §6.5 |
| **时区/日期归属** | 跨月账单归错月、汇总与列表不一致 | 统一 `YYYY-MM-DD` + `monthKey` 冗余，见 §4.5 |

### 6.1 离线/弱网提交

**核心原则：先写本地，再写远端；本地是"尚未确认的意图"，服务端是唯一真相。**

```
用户点保存
  ├─ 1. 本地校验（金额/日期/备注，与 §4.4、§4.5 同一套规则，前后端共用同一份规格）
  ├─ 2. 生成本地记录 { tempId, clientRequestId(uuid), payload, status:'pending', createdAt(本地) }
  ├─ 3. **先落 wx.storage 队列**，再更新 UI（顺序不能反，否则进程被杀会丢）
  │      → 账单列表 = 服务端数据 ∪ 待同步队列（按 clientRequestId 去重），待同步项显示"待同步"角标
  ├─ 4. 立刻 cloud.callFunction('txn.create')
  │      成功 → 队列项标记 sent 并从队列移除；用服务端返回覆盖本地项
  │      业务错误(1000~4999) → 标记 failed，UI 标记"保存失败"并给出可点击的重试/编辑
  │      网络/5000/5001/5002 → 保持 pending，进入退避重试
  └─ 5. 触发补传：网络恢复(wx.onNetworkStatusChange)、小程序 onShow、用户手动下拉刷新
       退避：1s → 2s → 4s → 8s，最多 5 次；之后标记 failed 等用户手动重试（不静默丢弃）
       单次补传按 createdAt 顺序，串行发送，避免瞬间打爆云函数
```

**边界处理**：

- 队列中的记录**只能新增，不能编辑/删除**（编辑删除仅在登录态可用，避免复杂的合并逻辑）。
- 队列持久化上限：保留最近 200 条或 7 天，超出则提示用户"存在大量未同步记录"。**不做静默丢弃**。
- 队列项**不上报埋点**（等真正落库成功后再上报），防止刷高"记账成功"数据。
- 弱网下的**读**：`stat.overview` 失败时展示本地缓存的最近一次快照，并在顶部标注"数据显示可能不是最新"。

### 6.2 并发与幂等

| 场景 | 对策 |
| --- | --- |
| 用户连点保存 | 客户端按钮 loading+禁用；服务端唯一索引兜底 |
| 补传重试与首次请求同时到达 | 唯一索引；后到者返回 `duplicated:true` |
| 多端并发改同一笔 | PRD 定为 **last-write-wins**：服务端 `updatedAt = 服务端当前时间` 覆盖写入，并记录 `lastDeviceId`。**代价**：两端同时编辑会丢一方修改。ADR-6 里写明：MVP 接受（单人单端使用为主），P1 加 `baseUpdatedAt` 乐观锁返回 `3005 VERSION_CONFLICT` 让用户选择 |
| 删除与撤销竞争 | 删除是软删；撤销只需把 `deletedAt` 置空并校验 5 秒窗口；重复撤销返回 `3002 ALREADY_DELETED` 等价结果（幂等） |
| 预算并发修改 | `budget.save` 为整月覆盖 + 唯一索引 `(userId, monthKey, kind, categoryId)`，天然收敛到最新一次提交 |
| 恶意/异常高频写 | 云函数内按 userId 做轻量限流：同一 userId 每分钟 `txn.create` 超过 60 次返回 `4001`（用 `events` 或云开发缓存计数，P0 可用简单内存计数 + 定时重置） |

### 6.3 统计聚合性能

**估算依据（假设，非实测）**：单用户日均 5 笔 → 月均约 150 笔 → 3 年后单人累计约 5400 笔。一次月度聚合命中的文档数 ≈ **150 条**，扫描量在 10² 量级。

- 聚合方式：`transactions.aggregate()`，`$match { userId, monthKey, deletedAt:null, type }` → `$group` by categoryId / occurredDate。命中 `(userId, monthKey, type, deletedAt)` 索引。
- 目标（估算目标，需实测）：聚合 P95 < 500ms（数据基数 10²）。
- **不做预聚合缓存表**：MVP 量级下缓存带来的失效逻辑（新增/编辑/删除/撤销都要更新缓存）风险大于收益。升级触发条件：单用户单月记录 > 2000 条，或聚合 P95 > 1s，则引入 `month_stats` 汇总集合（写时更新，同样走 service 层）。
- 账单列表走**游标分页**（20 条/页），不做 `skip` 深分页（`skip` 在大 offset 下线性变慢）。
- 每日趋势图返回 `daily` 数组含 0 值日期（由服务端补齐），客户端不自己补（避免两端日期口径不一致）。

### 6.4 容量与成本估算（估算，非实测）

| 项 | 假设 | 推算 |
| --- | --- | --- |
| 灰度用户 | 200 人（1 所高校种子，PRD §8 假设） | — |
| 日活 | 假设 30% → 60 人 | — |
| 日均记账 | 每人 5 笔 | 300 笔/日 ≈ 9000 笔/月 |
| 云函数调用 | 记账 1 次 + 列表 1 次 + 汇总 1 次 ≈ 每用户每天 12 次 | ≈ 720 次/日 ≈ 2.2 万次/月 |
| 数据库写入 | ≈ 300 次/日 | 9000 次/月 |
| 数据库读取（含聚合） | 聚合按 150 文档扫描计 | ≈ 5 万文档读取/月量级 |

> **云开发免费额度的具体数值我不在此引用**（额度与规则会调整，写了容易过期误导）。上线前必须做的一件事：**由工程师按官方最新文档核对上述推算量是否在免费额度内**，若超，评估升级套餐成本。这是 §9 待确认项之一。

### 6.5 小程序包体积

| 手段 | 说明 |
| --- | --- |
| 分包 | 统计页 + 图表库放分包 B；账单/详情/预算放分包 A；主包只留记账首页、我的、启动与基础设施 |
| 分包预下载 | `preloadRule` 在记账首页空闲时预下载分包 A，兼顾体验（避免切 Tab 白屏） |
| 按需注入 | `app.json` 设 `lazyCodeLoading: 'requiredComponents'`，只注入用到的组件 |
| 组件按需引入 | Vant Weapp 只引实际用的组件（进度条、弹层、日期选择、Toast 等） |
| 图表库 | uCharts 只放分包 B；**不用 ECharts 全量包** |
| 静态资源 | 图标用 iconfont / 内联 SVG；头像等用户图片走云存储，不打包 |
| 构建 | 开发者工具开启 es6→es5、代码压缩、上传时看主包实际体积 |
| 验证 | 每次上传前记录主包/分包体积，超过预算 90% 就要处理（阈值写进上线检查清单） |

---

## 7. 决策记录（ADR）

### ADR-1：后端选微信云开发，而不是自建服务器

**背景**：1 人工程团队，MVP 目标用户量级百级，需要快上线、低运维、与微信登录天然集成。
**决策**：云函数 + 云数据库（云开发）。
**备选方案**：
- 自建 Node/Java + 云服务器 + 域名 + HTTPS + 备案 + MySQL：可控性最强，但需要备案（数周）、运维、连接池、备份，对 1 人团队不成比例。
- 云开发 + 自建 MySQL（云函数连外部数据库）：两边的复杂度都要，无必要。
**影响与代价**：
- 得到：免备案/免运维、登录集成、成本≈0、上线周期以天计。
- 放弃：SQL 的强事务与复杂报表能力、供应商锁定、聚合能力受云数据库限制。
- 缓解锁定：`service` 层纯逻辑不碰 SDK，`repository` 层是唯一出口，迁移只需重写 repository + 改鉴权（见 §9.1 的迁移触发条件）。
**重新评估触发条件**（任一满足）：日活 > 5000；需要多端（App/Web）共用后端；需要秒级强一致的多表事务；需要复杂运营报表。

### ADR-2：客户端不直连数据库，全部走云函数

**背景**：云开发允许客户端直接读写数据库（按 `_openid` 隔离），写起来更省事。
**决策**：所有集合权限设为「仅管理端可读写」，客户端**禁止**使用 `wx.cloud.database()`，只走 `cloud.callFunction`。
**备选方案**：客户端直连数据库（省一次云函数调用，延迟更低）。
**影响与代价**：
- 得到：入参校验、金额/日期规则、幂等、软删除、越权防护全部在可信侧；规则只有一份实现（不会前端一套后端一套）。
- 放弃：客户端直连的低延迟与少量云函数调用次数；每个写操作多一跳。
- 缓解：记账走**单次调用返回写入结果 + 预算快照**，不额外增加往返。

### ADR-3：金额以「分」的整数存储

**背景**：金额要参与聚合求和，且用户会核对"汇总对不对得上"。
**决策**：`amountCent: int`；元↔分转换仅在输入解析与展示两处，用字符串解析不经过浮点。
**备选方案**：浮点元（有舍入误差）、Decimal（云数据库无原生类型，无法直接聚合）。
**影响与代价**：得到精确聚合；代价是每个涉及金额的代码路径都要注意单位，靠 TS 类型别名 `Cent = number` 与统一命名 `amountCent` 降低误用风险。

### ADR-4：记账日期用 `YYYY-MM-DD` 字符串 + `monthKey` 冗余

**背景**：跨月归属必须按"记账日期"，且要能按月索引聚合；用户输入的是"哪一天"，不是时间戳。
**决策**：存字符串 `occurredDate`，同时冗余 `monthKey`，索引与聚合都用 `monthKey`。
**备选方案**：存 Date 时间戳（时区转换易错，同一笔在不同时区可能落到不同月）；只存字符串不冗余 monthKey（聚合要按字符串前 7 位匹配，用不上索引前缀优化，且语义不清）。
**影响与代价**：字符串比较即时间比较（字典序=时间序），时区问题被彻底绕开；代价是冗余字段需在 `txn.create/update` 时同步维护（集中在 service 一处）。

### ADR-5：幂等用「clientRequestId + 唯一索引」，不建幂等表、不用分布式锁

**背景**：弱网补传、用户连点、多端同提交都会产生重复写。
**决策**：客户端生成 UUID v4 作为 `clientRequestId`，唯一索引 `(userId, clientRequestId)`，重复时返回首次结果并带 `duplicated:true`。
**备选方案**：独立的 idempotency 记录表（多一次读写与清理）；分布式锁（云开发无原生锁，引入复杂度不值）。
**影响与代价**：得到零额外表、零锁、并发安全（唯一索引是最终防线）；代价是每个写接口必须要求客户端传 `clientRequestId`（客户端 API 层统一生成，不靠页面各自记得）。

### ADR-6：多端冲突采用 last-write-wins（P0），不做 CRDT/字段级合并

**背景**：PRD 明确"以最后修改时间（服务端时间）为准，后写覆盖先写"。
**决策**：按 PRD 实现。服务端覆盖写，记录 `lastDeviceId` 便于事后排查。
**备选方案**：乐观锁（`baseUpdatedAt` 不匹配就报冲突让用户选）—— 交互复杂度高，MVP 不做；CRDT —— 复杂度远超收益。
**影响与代价**：实现最简单；代价是两端同时编辑会静默丢一方修改。可接受因为主场景是单人单端（换机是"用新机看旧数据"，不是持续双端并行编辑）。P1 加乐观锁。

### ADR-7：图表用 uCharts（分包 B），不用 ECharts 全量包

**背景**：只有环形图 + 柱状图两种，且 PRD 明确要求中低端安卓不卡顿、包体积敏感。
**决策**：uCharts 小程序版，只放分包 B，Canvas 2D 渲染。
**备选方案**：ECharts(`ec-canvas`)（定制包体积仍偏大）；纯 Canvas 自绘（无依赖，但坐标轴/标签/命中区域全手写，点击分类看明细要自己做命中检测）。
**影响与代价**：得到小体积 + 现成点击事件；代价是引入一个第三方库（需在 README 记录版本与用途），且与设计稿的视觉细节可能需要定制。若实测体积/性能不达标，降级方案是自绘（只两种图，可控）。

### ADR-8：状态管理用自研轻量 store，不用 mobx/全局大仓库

**背景**：跨页共享状态只有三项：当前用户、预算快照、待同步条数。真相同源是服务端。
**决策**：一个约 50 行的发布订阅 store，页面 `onShow` 主动拉取刷新。
**备选方案**：`mobx-miniprogram`（2KB，成熟，但当前需求太少）；裸 `globalData`（无变更通知，页面间不同步）。
**影响与代价**：得到零依赖、逻辑透明；代价是自研代码需要单测覆盖（订阅/取消订阅/重复订阅），并且将来共享状态变多时要及时换方案。升级触发条件：跨页共享状态超过 6 项，或出现"多处派生状态"时改用 mobx-miniprogram。

### ADR-9：云函数用「单函数 + action 路由」

**背景**：按域拆多个云函数（auth/txn/stat）是常见做法。
**决策**：单个 `api` 云函数，用 `action` 路由，内部 `router → validator → service → repository`。
**备选方案**：多函数（每个域一个）：职责更清晰，但每个函数独立冷启动、共享校验与错误处理代码要复制或多包一层公共层，1 人维护成本更高。
**影响与代价**：得到统一鉴权/错误码/日志、冷启动复用、部署只有一次；代价是单函数代码量增大，需要通过目录分层保持可读性。若某接口资源需求明显不同（如导出大文件），可单独拆出。

---

## 8. 里程碑与验证

### 8.1 工作量拆解（估算，1 名工程师，不含设计与测试的并行时间）

> 估算依据：按"接口数 × 复杂度 + 页面数 × 复杂度"粗估，**无历史项目数据可比对，误差可能达 ±50%**。仅用于排期参考，不作为承诺。

| 里程碑 | 内容 | P0 覆盖 | 估算人日 | 如何验证（可运行、可观察） |
| --- | --- | --- | --- | --- |
| M0 骨架 | 云环境（dev/staging/prod）建立、云函数 `api` 框架（router/错误码/日志/requestId）、小程序分包骨架、utils（金额/日期/uuid/校验）、TS 配置、README | — | 1.5 | 真机打开小程序能调通 `auth.login` 并在云函数日志看到 requestId |
| M1 登录与数据层 | `auth.login`、users 建号与唯一索引、`user.getProfile`、repository 抽象、本地模式降级 | P0-4（部分） | 2 | dev/staging 环境分别登录，云函数日志 openid 正确解析；手动断开网络进入本地模式不白屏 |
| M2 记一笔 | 记账首页 UI、金额键盘与校验、`txn.create`（含幂等 + budget 快照返回）、本地待同步队列 + 补传 | P0-1 | 3.5 | 用例：金额 0/负数/3 位小数/超上限/未来日期/备注超长/连点保存/飞行模式记账后联网补传；断言重复提交只落一笔 |
| M3 账单与编辑删除 | `txn.list`（游标分页）、按天分组、月度汇总、`txn.update`、`txn.delete`、`txn.restore` | P0-2 | 3 | 用例：5/31 记录不入 6 月；改日期后两月汇总同步变化；删除二次确认 + 5 秒撤销；超 5 秒撤销返回 3004；分页 20 条 |
| M4 预算 | `budget.get/save/copyFromLastMonth`、预算页、首页进度条、80%/100% 提醒（含总预算与分类合并去重） | P0-3 | 2.5 | 边界：640/800=80% 与 800/800=100% 各触发一次提醒且不重复弹；未设预算不提醒；分类合计超总预算只 warn 不 block；新月份沿用提示 |
| M5 图表 | 分包 B 统计页、uCharts 环形图 + 柱状图、切换收支/月份、点分类看明细 | P0-5 | 2 | 图表数据与 `stat.overview`/账单汇总逐项比对一致；空月份显示空状态不画空图；中低端真机实测帧率与首屏时间 |
| M6 我的/合规 | 我的页、头像昵称可选授权、隐私协议页、注销入口 + `user.deleteAccount` | P0-4（合规） | 1.5 | 注销后重新登录数据为空；本地缓存被清空；隐私协议首次启动可见 |
| M7 可观测与清理 | 埋点上报 `track.event`、错误上报、定时任务 `cleanupSoftDeleted`（软删 90 天硬删） | PRD §6.4 | 1 | 手动构造 90 天前软删记录的测试数据，运行定时任务后确认被硬删；埋点事件在数据库可查 |
| M8 联调与上线准备 | 全链路联调、真机测试配合、包体积检查、索引与权限核对、审核材料 | 全部 | 3 | 见 §8.4 上线检查清单，逐项签字 |
| **合计** | | | **≈ 18 人日**（日历上约 4 周，含联调与返工余量） | |

**P1 排期粗估（不在本期实现，仅供参考）**：订阅消息提醒 2d、自定义分类 2d（含 categories 集合迁移）、账单搜索筛选 2d、数据导出 1.5d、语音记账 3d、记账打卡 2d、日历视图 2d。

**P1 不打乱本期架构的预留点**：`categories` 表迁移路径（§4.2）；乐观锁 `baseUpdatedAt`（ADR-6）；`txn.list` 已支持 `dateFrom/dateTo/categoryId` 筛选（搜索筛选的接口已就位）；`type` 参数化（导出与统计复用）。

### 8.2 P0/P1 分批原则

- **P0 全部进 M0–M8**，不砍任何 P0 验收标准。
- P1 只做"接口/字段预留"，不做实现：`categories` 用常量、`txn.list` 支持日期与分类筛选、`update` 接口保留 `baseUpdatedAt` 参数（服务端 P0 忽略，P1 启用）。
- P2（多账本、自动导入、社交）**不预留任何字段**，避免过度设计。

### 8.3 可测性设计

| 设计 | 目的 |
| --- | --- |
| `service` 层纯函数（不 import 云 SDK） | 可用本地 Node 测试直接跑核心规则，不依赖云环境 |
| `repository` 以接口注入 | 测试用内存实现替换真实数据库 |
| 前后端共用同一份金额/日期规则规格（§4.4/§4.5） | 避免"客户端能存、服务端拒绝"这类不一致 |
| 统一 `requestId` + 结构化日志 | 出问题能用 requestId 串起"客户端动作 → 云函数调用 → 数据库操作" |
| dev/staging/prod 三环境 | 测试不污染生产；staging 用测试 openid |
| 幂等键由 `api/` 层统一生成 | 补传测试可精确构造"同一 clientRequestId 发两次" |
| 时间可控注入 | `service` 接受 `today` 参数（默认 `beijingToday()`），使"未来日期/1 年边界/跨月"可在测试里构造任意日期 |
| 阈值常量集中定义 | 80%/100%、1 年、50 字、999999.99 元、20 条/页 → 常量引用，测试改一处即可 |

**埋点字段口径（需 PM 确认）**：

| 事件 | 触发时机 | 字段 |
| --- | --- | --- |
| `record_saved` | 记账**服务端确认成功**后 | `type, categoryId, amountCentBucket(如 0-10/10-50/…), occurredDate==today, latencyMs(点击保存→响应), offlineFlag` |
| `record_save_failed` | 业务错误或重试 5 次后仍失败 | `code, retryCount, offlineFlag` |
| `pending_synced` | 补传成功 | `queueAgeMs, retryCount` |
| `budget_set` | 预算保存成功 | `hasTotal, categoryCount, totalCentBucket` |
| `budget_alert_shown` / `_clicked` | 提醒展示/点击 | `scope(total\|category), threshold(0.8\|1.0), categoryId?` |
| `page_view` | 页面显示 | `page(stats\|bills\|budget), monthKey` |
| `sync_latency` | 多端同步 | `fromDeviceId, latencyMs` |

> **不采集**：备注原文、昵称原文（属个人信息，且对指标无价值）。`userId` 在埋点中统一做哈希后上报。

### 8.4 上线前检查清单（工程侧）

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 三个云环境已建且配置正确 | 客户端按构建区分 `env`，staging 不与 prod 串 |
| 2 | **所有唯一索引已手工创建** | ⚠️ 云数据库索引需在控制台/CLI 创建，不随代码部署。漏建唯一索引 = 幂等失效，属高危项 |
| 3 | 所有集合权限为「仅管理端可读写」 | 逐集合核对截图留档 |
| 4 | 客户端代码中无 `wx.cloud.database()` 直连 | grep 静态检查 |
| 5 | 代码与仓库中无密钥/明文环境敏感信息 | 本项目无第三方密钥；未来接入的密钥一律走云函数环境变量 |
| 6 | 定时触发器 `cleanupSoftDeleted` 已开启 | 用测试数据验证过 |
| 7 | 错误码与用户文案映射表已实现 | 用户看得到的是人话，不是 `code:5001` |
| 8 | 隐私协议、信息收集清单、注销入口齐备 | 微信审核硬要求（PRD P0-4） |
| 9 | 主包/分包体积在预算内 | 见 §6.5 阈值 |
| 10 | 中低端安卓真机跑过全链路 | 记账、图表、弱网补传三项必测 |
| 11 | 回滚方式确认 | 云函数可回滚到上一版本；数据库开启备份（云开发自带备份能力，需确认保留周期） |
| 12 | 日志中无 openid/备注明文 | 抽查最近 100 条云函数日志 |
| 13 | 云开发免费额度与推算用量核对 | 见 §6.4 |

---

## 9. 待确认与遗留问题

### 9.1 需 CEO / 同事确认

| # | 问题 | 我的建议 / 影响 | 需要谁拍板 |
| --- | --- | --- | --- |
| 1 | **未登录/本地模式是否允许长期使用**（PRD §7.2-1） | 方案已兼容两种口径（§2.3）；只剩一细节：本地模式下**是否允许看统计页**。建议允许但标注"仅含未同步数据"。不阻塞开工 | CEO |
| 2 | PRD 引用的技术文档路径为 `docs/TECH.md`，本文是 `docs/Tech-Design.md` | 建议 PM 更新 PRD 引用，保持文档可追溯 | PM |
| 3 | 统计页默认是否含收入（PRD §7.2-2） | 方案：默认只看支出（`type=expense`），接口支持切换；图表与汇总共用口径 | 设计师 + CEO |
| 4 | 埋点字段口径（§8.3）是否认可 | 尤其是 `amountCentBucket` 的分档边界 | PM |
| 5 | 分类体系 8 类支出是否够（PRD §7.2-4） | 技术上无成本差异（常量表一改即可，注意改 ID 会影响历史数据——**建议只新增不重命名 ID**） | 设计师 |
| 6 | 微信订阅消息在当前类目下的可用性与模板申请（PRD §7.2-3） | P1 事项，但**建议在 P0 开发期就提交类目与模板申请**（审核有周期），否则 P1 会卡在资质上 | CEO 决定是否提前申请 |
| 7 | 是否安排 3–5 人可用性测试（PRD §7.2-5 / §1.4） | 技术侧希望在有真机可测时同步进行，能提前发现"分类不符合习惯""记一笔超 5 秒" | CEO |
| 8 | 灰度用户规模 | 影响云开发额度核对与限流阈值（§6.3/§6.4）。目前按 200 用户估算 | CEO |

### 9.2 技术侧待验证（无法在没有运行环境时完成）

| 待验证 | 验证方法 | 失败时的对策 |
| --- | --- | --- |
| 云开发免费额度是否覆盖 §6.4 用量 | 对照官方最新文档逐项核对 | 升级套餐 或 降低聚合调用频次（合并请求） |
| 云数据库 `aggregate` 在真实数据量下的耗时 | staging 造 1 万笔测试数据实测 | 引入 `month_stats` 预聚合表（§6.3） |
| uCharts 在低端安卓的渲染帧率与体积 | 真机实测 + 上传体积记录 | 换自绘 Canvas（ADR-7 降级方案） |
| 云函数冷启动对"≤5 秒记账"的影响 | 真机首次调用多次计时 | 首页预热一次空调用；必要时拆轻量函数 |
| 唯一索引在并发插入下确实拦截重复 | staging 并发脚本同 clientRequestId 打 20 次 | 若拦截异常，改用事务 upsert |
| 客户端 `wx.storage` 队列在进程被杀后不丢 | 真机杀掉小程序后重启检查队列 | 减小队列单条体积、及时落盘 |

---

## 10. 附：本方案与 PRD 的一致性自查

| PRD 硬规则 | 方案落点 |
| --- | --- |
| 金额 ≤ 999999.99、最多 2 位小数、不接受 0/负数 | §4.1 上限 99999999 分、§4.4 解析规格（截断不四舍五入）、§5.3 码 1003 |
| 日期：今天及过去 1 年，禁止未来 | §4.5 `validateOccurredDate`、码 1004/1005 |
| 归属月份按记账日期，不按创建时间 | §4.1 `monthKey` 由 `occurredDate` 推导、§4.5 |
| 分类必填，默认餐饮 | §5.2 `txn.create` 默认值在客户端预填 + 服务端白名单校验（码 1007） |
| 备注 ≤ 50 字 | §4.2、码 1006 |
| 连点保存只写一笔 | §5.4 幂等、§6.2 |
| 网络异常本地暂存、幂等补传、不丢 | §6.1 |
| 编辑后所有汇总同时刷新 | §5.2 ③ 服务端重算并返回 budget 快照 |
| 删除二次确认 + 5 秒撤销 + 软删除 90 天 | §5.2 ④、§4.3、M7 定时清理 |
| 预算按自然月独立 + 月初沿用提示 | §4.2 budgets、`budget.copyFromLastMonth`、`lastMonthTotal` |
| 80%/100% 提醒，总预算与分类合并不重复弹 | §5.2 ② `alerts` 由服务端去重合并 |
| 未设预算不做提醒、不阻塞记账 | `budget.isSet=false` 时 `alerts=[]` |
| 分类预算合计超总预算只提醒不阻止 | `warnings: SUM_EXCEEDS_TOTAL`（非 error） |
| 静默登录、不强制填昵称头像 | §2.3 |
| 多端同步、冲突以服务端时间后写覆盖 | §6.2（ADR-6） |
| 图表与账单/汇总口径一致 | §5.2 ⑥ 单一聚合接口 |
| 隐私协议与注销入口 | M6、§8.4 检查项 8 |
| 记账写入失败率 < 0.5%（PRD 目标） | 需要埋点支撑：`record_save_failed` 事件（§8.3），上线后才有基线 |
