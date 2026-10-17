# 技术方案：健身房团课预约微信小程序（MVP）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 作者 | 工程师 |
| 依据文档 | docs/PRD.md（v1.1，PM） |
| 说明 | PRD 第 1 页把本文档登记为 `docs/技术方案.md`，实际落盘文件名为 `docs/Tech-Design.md`（同一份文档，请 PM 知悉，若需统一命名可改名） |
| 阶段边界 | 本阶段**只产出文档，不写代码**。文中的 SQL / 伪代码是设计描述，不是交付代码 |
| 前置说明 | 本方案产出时，共享文件区只有 `docs/PRD.md`，**交互设计文档尚未落盘**。文中涉及 UI 表现的部分全部依据 PRD 第 7 章的 AC 推导，交互稿定稿后若与本方案不一致，以交互稿调整前端，**不改动服务端时间窗与状态机** |

---

## 0. 一页速览（给 CEO / PM）

- **架构**：微信小程序（会员 / 教练 / 运营同一套代码）→ 单个后端服务（模块化单体）→ PostgreSQL 一台。**不引入 Redis、不引入消息队列、不拆微服务**。
- **为什么这么简单**：预估全量峰值 5～20 QPS，抢课瞬间单课次几十并发；这个量级用「一个数据库事务 + 行锁 + 数据库 CHECK 约束」就能 100% 防住超卖，引入中间件只会增加运维面而没有收益（见 ADR-1~ADR-3、ADR-7）。
- **最关键的技术决策**：**"取消座位"与"候补转正"在同一个数据库事务内完成，且该事务中不改变 `seat_taken` 计数**。这样 PRD 6.5.1 要求的"对外 `remaining` 恒为 0"是数据库层面的事实，而不是靠定时任务追赶的状态（见 7.2）。
- **不超卖的三重保险**：① `UPDATE ... WHERE seat_taken < capacity` 条件更新；② `CHECK (seat_taken >= 0 AND seat_taken <= capacity)` 表级约束（写坏了直接报错，不是静默超卖）；③ 对账脚本按明细重算，可随时验证。
- **工作量**：单人 **8.5～9.5 周**（含自测与压测），里程碑 M0～M7 见第 10 章。另有 7 项**非编码前置**：域名备案 + HTTPS、微信订阅消息模板审批、手机号快速验证组件的付费与配额等（见 10.3）。
- **需要 CEO / PM / 运营拍板的 16 件事**：第 13 章，其中 **Q9/Q11 会员名单来源** 与 **"T-30 分钟后现场补位"规则缺失（T1）** 是真正会卡住上线/功能的两个。

---

## 1. 需求映射

| PRD 功能 | 实现模块 | 备注 |
| --- | --- | --- |
| US-12 登录 / 手机号绑定 / 角色识别（P0 前置） | `auth`（微信 code2session、手机号获取、角色解析） | openid 为唯一身份键；身份与视角分离（ADR-8） |
| US-01 课表查看 | `schedule`（课表查询 + 状态角标计算） | 角标与"可否预约"由服务端算好下发，前端不推导（PRD 13.2/13.3） |
| US-02 课程详情 | `schedule`（详情 + `my_actions[]`） | 返回全部时间窗阈值 + 不可执行原因 |
| US-03 预约 | `booking`（占座、上限、限制期、幂等） | 时间窗规则在 `domain/window.ts` 纯函数，可单测 |
| US-04 取消 | `booking` + `waitlist`（**同事务释放+转正**） | 核心难点，见 7.2 |
| US-05 候补排队 | `waitlist`（入队 FIFO、退出、上限） | 队列序号由 session 行锁内自增分配 |
| US-06 候补转正与确认 | `waitlist` + `job`（超时任务、级联、兜底补偿） | 见 7.3 / 7.4 |
| US-07 教练发起签到 + 会员签到 | `checkin`（发起、校验、尝试计数、手动标记） | 见 7.6 |
| 6.7 爽约判定与 7 天限制 | `job` + `booking`（信誉） | 每日 T+15min 任务，跳过教练未发起签到的课次 |
| US-08 我的预约 / 信誉 | `booking`（查询聚合） | 爽约次数按明细实时统计，不用计数器（见 5.9） |
| US-09 消息中心 | `notification`（站内消息 + 订阅消息） | 站内消息是业务事实，订阅消息是尽力而为（ADR-9） |
| US-10 教练今日课程与名单 | `coach`（仅鉴权范围内的名单） | 鉴权与"当前视角"无关（PRD 13.4） |
| US-11 / P0-D 运营排课、改期、取消、名单 | `admin`（排课 CRUD、改期保护、运营确认名单） | 改期保护字段与逻辑见 7.5 |
| 6.11 课次变更（改期/换教练/容量） | `admin` + `job`（重算任务、批量通知） | 时间窗全部按新 T 重算，需重建定时任务 |
| 6.1 时间窗参数可配置（D7） | `config`（`app_config` 表 + 进程内 60s 缓存） | 参数变更不影响已存在记录的判定，因为判定都读"当前 now + 当前配置" |
| 10. 埋点 | `analytics`（写 `event_log` 表 + 异步导出） | MVP 先落库，不做数仓 |
| 12.3 回滚开关 | `config`（`booking.enabled` 开关） | 关闭后预约/候补接口返回维护中，已产生的记录与签到不受影响 |

---

## 2. 架构概览

### 2.1 架构模式

**模块化单体（Modular Monolith）+ 单库 + 单进程内定时任务。**

理由（结合真实规模，不做架构宇航员）：

1. 全部业务围绕"课次"这一个聚合，写路径高度集中在会话行上，**没有任何读写极不对称的场景**（课表读多写少，但读量也只要毫秒级 SQL 就能撑住）。
2. 会员端 / 教练端 / 运营端共用同一套领域规则（时间窗、状态机、鉴权）。拆服务会把"时间窗规则"复制成 3 份，直接违反 PRD 6.0"同一课次不能出现多个结论"的核心诉求。
3. 团队只有 1 名工程师（我）+ 编码 agent。可维护性是第一约束：**引入的每个中间件都会变成只有一个人会运维的黑盒**。

放弃的东西：无法独立扩容某个热点接口（本场景不需要）；定时任务与 API 同进程，需要 `pg_advisory_lock` 保证多实例下不重复执行（见 ADR-5）。

### 2.2 运行时拓扑

```
                    ┌──────────────────────────────────────────┐
   微信客户端       │  微信小程序（一套代码，按角色显示 tab）   │
   （会员/教练/运营）│  原生小程序 + TypeScript                  │
                    └───────────────┬──────────────────────────┘
                                    │ HTTPS / JSON（域名已备案 + 白名单）
                                    ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  API 服务（Node.js 20 + TypeScript + Fastify）                │
        │                                                              │
        │  modules/  auth │ schedule │ booking │ waitlist │ checkin    │
        │            │ coach │ admin │ notification │ analytics        │
        │                        │                                     │
        │  domain/   时间窗纯函数 window.ts │ 状态机 booking-fsm.ts     │
        │            （不依赖 DB / HTTP / 微信，可 100% 单测）          │
        │                                                              │
        │  infra/    pg(连接池) │ wx-api(微信接口) │ jwt │ logger        │
        │            job-runner(任务表轮询) │ config │ idempotency       │
        └───────┬───────────────────────────────┬──────────────────────┘
                │ SQL（参数化）                  │ HTTPS
                ▼                                ▼
     ┌───────────────────────┐     ┌──────────────────────────────────┐
     │ PostgreSQL 16         │     │ 微信开放接口                       │
     │ （唯一持久化与唯一时钟）│     │ code2session / getPhoneNumber      │
     │ 约束+部分唯一索引+排除  │     │ subscribeMessage.send              │
     │ 约束+行锁+任务表        │     │ getAccessToken                     │
     └───────────────────────┘     └──────────────────────────────────┘
```

**部署形态（MVP）**：1 台 2C4G 云服务器，Docker Compose 起 `postgres` + `api` 两个容器；备份用 `pg_dump` 每日全量 + 云盘快照。**没有 Redis、没有 Nginx 之外的中间层**（HTTPS 终止用云负载均衡或 Caddy + 自动证书）。

### 2.3 模块划分与依赖方向

依赖方向**单向向内**：`modules → domain`，`modules → infra`；`domain` 不依赖任何外部。

```
                    ┌────────────────────────────┐
                    │   domain/（纯规则，无 IO）   │
                    │  window.ts   时间窗/边界     │
                    │  booking-fsm.ts 状态机合法迁移│
                    │  waitlist.ts 选人校验清单     │
                    │  errors.ts   领域错误码       │
                    └────────────▲───────────────┘
                                 │ 只被依赖，不依赖别人
   ┌──────────┬──────────┬───────┴────┬──────────┬──────────┬─────────┐
   │ auth     │ schedule │ booking     │ waitlist │ checkin  │ admin   │
   │ 登录/角色 │ 课表/详情│ 预约/取消    │ 候补/转正 │ 签到/爽约│ 排课/改期│
   └────┬─────┴────┬─────┴──────┬──────┴────┬─────┴────┬─────┴────┬────┘
        │          │            │           │          │          │
        └──────────┴────────────┴─────┬─────┴──────────┴──────────┘
                                      ▼
                           ┌────────────────────┐
                           │ infra/             │
                           │  db │ wx │ jwt │   │
                           │  logger │ job │    │
                           │  config │ notifier │
                           └────────────────────┘
```

**跨模块调用规则**（防止变成大泥球）：

- 跨模块只允许调用对方 `index.ts` 暴露的 **application service**，禁止互相直接读对方表。
- 唯一的例外：`booking` 与 `waitlist` 共享 `session` 与 `booking` 两张表，因为 PRD D5 明确"候补也是 Booking 的一种状态"，且"释放+转正"必须同事务。这一处共享用一个内部函数 `transferSeatInTx(tx, sessionId, ...)` 收敛，两个模块都调它，避免两份实现漂移。

---

## 3. 技术选型

| 选项 | 选择 | 理由 | 放弃的备选 |
| --- | --- | --- | --- |
| 小程序端 | **微信原生小程序 + TypeScript** | 只用微信小程序这一个端（PRD 3 明确不做 App/H5）；原生对 `wx.login`、`getPhoneNumber`、`requestSubscribeMessage`、订阅消息跳转无适配损耗；单人维护成本最低 | Taro / uni-app：为"多端复用"付编译层与调试成本，而本项目没有第二个端，收益为零（可逆：若将来要 App，再评估 Taro，业务逻辑已在服务端，前端重写成本可控） |
| 后端语言 | **TypeScript（Node.js 20 LTS）** | 前后端同一语言，单人上下文切换成本最低；JSON 处理与微信 HTTP 接口天然贴合；类型系统足够表达领域状态机 | Java/Spring：事务能力更强但启动/依赖/部署重，本场景用不到；Go：性能过剩，而 JSON/ORM 便利性下降，团队只有 1 人 |
| Web 框架 | **Fastify** | 内置 JSON Schema 校验、性能好、插件少而稳；比 Express 更快地暴露输入错误 | NestJS：DI 容器 + 装饰器对 10 个模块是过度设计 |
| 输入校验 | **TypeBox / JSON Schema（Fastify 原生）** | 与 Fastify 一体，零额外运行时；在边界统一校验（安全默认开启） | zod：可用，但会多一层 adapter |
| 数据访问 | **Kysely（typed SQL builder）+ `pg`** | 本项目大量依赖 PostgreSQL 特有语义：`SELECT ... FOR UPDATE`、部分唯一索引、`CHECK`、`EXCLUDE`、`ON CONFLICT`。Kysely 让我写得出精确 SQL 又有类型；迁移用 SQL 文件，可读可 review | Prisma：schema 好用，但行锁/部分索引/条件更新要退化成 `$queryRaw`，反而割裂；Mongoose 类：无事务与约束能力，直接排除 |
| 数据库 | **PostgreSQL 16** | 需要的 5 个能力全在一处：① 部分唯一索引（"一人一课次最多 1 条活跃记录"）；② `CHECK` 约束（`seat_taken ≤ capacity` 硬防超卖）；③ `EXCLUDE USING gist`（同教练时间不重叠，PRD AC-11-3）；④ 行锁 + `SKIP LOCKED`（任务表）；⑤ `timestamptz` 与 `now()` 作为**唯一时钟源**，天然解决多实例时钟漂移 | MySQL：无部分索引、无排除约束，超卖防护要下沉到应用层，弱；MongoDB：跨文档事务与约束能力弱，状态机+计数场景不合适 |
| 定时任务 | **数据库任务表（`scheduled_task`）+ 进程内轮询（10s）+ `pg_advisory_lock`** | 任务与业务在同一事务里插入 → 不会丢（"转正后 15 分钟超时"这类任务必须与转正同事务落库）；支持多实例（advisory lock）、可人工重放、可 SQL 查状态 | node-cron 内存定时：进程重启丢任务、多实例重复执行，**不可用于座位类逻辑**；BullMQ/Kafka：为 20 QPS 引入中间件，不值（ADR-7） |
| 认证 | **自签 JWT（`jose`）**：access 2h + refresh 30d（refresh 落库可吊销） | 微信只提供 openid，不提供登录态；服务端自签可控制过期、吊销、多角色。access 短、refresh 长是标准做法且实现简单 | 服务端 session 表 + cookie：小程序不天然带 cookie；全用长 JWT：无法吊销 |
| 微信 access_token 管理 | **`wx_access_token` 单行表 + advisory lock 刷新** | 单实例场景够用；比 Redis 少一个组件；集中式 `getToken()` 保证并发只刷一次 | Redis：为 1 个 key 部署一个 Redis 不划算 |
| 通知 | **站内消息（DB，必做）+ 微信订阅消息（尽力而为）** | 见 ADR-9 与第 8 章 | 短信：PRD 明确不做（成本） |
| 观测 | `pino` 结构化日志 + `trace_id` 贯穿 + 关键指标落表 + 对账脚本 | 出问题第一现场是日志与对账，而不是看板；MVP 不引入 Prometheus/Grafana | 全链路 APM：成本高于收益 |
| 测试 | **Vitest 单测（domain 纯函数全覆盖）+ 真实 PostgreSQL 的集成测试 + 并发验收脚本** | 时间窗边界与状态机是本项目 90% 的 bug 源，必须能脱离网络单测；并发必须打真库（内存库测不出行锁行为） | 只用 mock：测不出唯一索引冲突与锁行为 |
| 运营后台形态 | **小程序内"运营"入口（放在"我的"页，不做 tabBar）** | 复用登录、鉴权、组件；少一套前端框架与部署。**硬约束**：微信 tabBar 最多 5 个，会员端已用 4 个（课表/我的预约/消息/我的），教练占第 5 个，运营只能非 tab 进入 | 独立 Web 后台（React）：排课表格体验更好，但需新增前端工程 + 部署 + 第二套鉴权，MVP 工期不允许；P1-5 再补 |

### 3.1 需要引入的依赖（附用途）

`fastify`、`@fastify/jwt`（或 `jose`）、`@fastify/rate-limit`、`pg`、`kysely`、`pino`、`undici`（调微信 HTTP）、`@sinclair/typebox`、`dayjs` + `timezone` 插件（仅用于把 `date` 参数转成 `Asia/Shanghai` 的一天区间；**所有关键时间计算在 SQL 里用 `timestamptz` 完成**）、开发期 `vitest`、`tsx`。**没有需要"版本锁定后不能动"的重量级框架**。

---

## 4. 微信登录与用户身份打通

### 4.1 身份链路

```
小程序                         后端                          微信
  │ wx.login() → code          │                              │
  ├───────────────────────────►│ POST /auth/login {code}      │
  │                            ├──── code2session ────────────►│
  │                            │◄─── {openid, unionid?,        │
  │                            │       session_key} ───────────┤
  │                            │ 按 openid upsert app_user     │
  │                            │ 解析角色（手机号匹配）         │
  │◄── {token, refresh, user, ─┤                              │
  │     roles[], default_view} │                              │
  │                            │                              │
  │ 点"立即预约"→ 未绑手机号     │                              │
  │ <button open-type=         │                              │
  │  getPhoneNumber> → code    │                              │
  ├───────────────────────────►│ POST /auth/phone {code}       │
  │                            ├── getPhoneNumber(code) ──────►│
  │                            │◄── {phoneNumber, purePhone} ──┤
  │                            │ 写 phone + phone_verified_at  │
  │                            │ 重算 roles / default_view     │
  │◄── {user 更新, 继续原操作} ─┤                              │
```

### 4.2 openid / unionid 取舍

| 项 | 决策 | 理由 |
| --- | --- | --- |
| 主身份键 | **`openid`（唯一索引）** | 会员身份只在本小程序内使用；`openid` 是当前小程序内的稳定唯一标识 |
| unionid | **字段预留，不参与逻辑** | `unionid` 需要绑定微信开放平台账号才有值。MVP 只做一个小程序，用不到；但**字段现在就存**（有值就写），将来做公众号/App 时不必回填历史数据。这是一个可逆决策 |
| 手机号 | **`phone` 唯一索引（部分索引，仅非空）** | 手机号是门店侧唯一能核对的键（PRD Q11），也是角色识别键（教练/运营白名单） |
| `session_key` | **不持久化** | 只在手机号获取时用到，而现在 `getPhoneNumber` 走 code 由服务端直接调微信接口拿号码，不需要本地解密，因此**不存 `session_key`**（减少泄露面，安全默认开启） |

> 安全说明：`getPhoneNumber` 返回的 `code` 由服务端换取号码，**手机号不经过客户端上报**，避免客户端伪造手机号。

### 4.3 角色解析（PRD US-12 / AC-12-4~12-9）

```
绑定/登录时执行 resolveRoles(phone)：
  roles = []
  if member_roster 命中 phone 且 active              → roles += 'member'
  if coach 命中 phone 且 status='active'             → roles += 'coach'（并回写 coach.user_id）
  if store_staff 命中 phone 且 status='active'       → roles += 'staff'
  if phone 为空                                        → roles = []（未绑定，仅可浏览）
default_view = 'member' if 'member' in roles else
               'staff'  if 'staff'  in roles else
               'coach'  if 'coach'  in roles else 'member'
```

关键实现约定：

1. **`roles` 是服务端算出来的集合，`default_view` 只影响前端落地 tab**。鉴权一律按角色集合判定，**与 `default_view`、与前端自称的视角完全无关**（AC-12-6 / R12）。
2. `phone` 在 `app_user` 上唯一：同一手机号只允许一个微信账号绑定（AC-12-8 不支持一微信多手机号）。**冲突处理**：若新 openid 尝试绑定一个已被其他 openid 占用的手机号，返回 `409 PHONE_ALREADY_BOUND`，提示"该手机号已绑定其他微信账号，请联系前台"（PRD 未定义此分支，见 11.2-B3）。
3. **角色随登录实时重算，不缓存**（AC-12-9：被移出白名单后下次登录降级）。已经产生的预约与信誉数据不删（按 `user_id` 关联，与手机号无关）。
4. **`roles` 变更后，已签发的 access token 内仍带旧角色** → 因此 **token 内只放 `user_id`，不放 `roles`**；每次请求由服务端查一次角色（命中 `app_user.phone` 与白名单表，走索引，成本可忽略）。这是"安全默认开启"的选择：多一次查询换掉一整类越权风险。

### 4.4 鉴权与越权防线

| 接口类别 | 鉴权规则 |
| --- | --- |
| 公开（课表、详情、门店、课程类型） | 无 token 也可访问（AC-12-1 游客可浏览）；带 token 则额外返回"我的状态" |
| 会员写操作（预约/取消/候补/确认/签到） | 需 token + 已绑定手机号，否则 `403 PHONE_NOT_BOUND`（AC-03-3 前端拉起绑手机号） |
| 教练名单类（`/coach/**` 名单、发起签到、手动标记） | 需 token 且满足 `session.coach_id === me.coach_id`（按 `coach.user_id` 反查），否则 `403 NOT_SESSION_COACH`，**不返回任何姓名/手机号**（AC-12-6 / R12） |
| 运营类（`/admin/**`：排课、改期、名单、解除限制） | 需 token 且 `store_staff` 命中该 `store_id`，或 `role = 'super_admin'`；跨门店一律 403 |
| 解绑/改绑手机号 | **不提供接口**（PRD 3 / AC-12-8）；提供"联系门店"静态信息 |

---

## 5. 数据模型

### 5.1 通用约定

- 所有时间字段用 `timestamptz`（存 UTC）；**时区常量 `Asia/Shanghai` 只用于"自然日"边界换算与展示**（见 7.7）。
- 所有表带 `created_at timestamptz NOT NULL DEFAULT now()`；可变表带 `updated_at`。
- 主键用 `bigserial`（单库、单体，无需 UUID；`bigint` 索引更小、局部性更好）。对外暴露的 id 无所谓安全性（无靠 id 猜测的越权接口——所有读接口都带鉴权或只读公开数据）。
- 软删除不做：业务用状态字段（`cancelled` / `status`），真删只发生在数据清理作业（消息 90 天清理）。
- **DDL 顺序说明**：下文按"领域分组"展示表结构，便于阅读；**实际迁移脚本必须按外键依赖顺序创建**：

```
001 extension(btree_gist)
002 store → class_type → app_user
003 coach / store_staff / member_roster   （依赖 app_user、store）
004 session                               （依赖 store、coach、class_type、app_user）
005 booking                               （依赖 session、app_user）
006 checkin_record / checkin_attempt       （依赖 session、booking、app_user）
007 session_change_log / notification / subscribe_grant
008 scheduled_task / idempotency_key / audit_log / app_config / wx_access_token / event_log
009 约束与索引（EXCLUDE、部分唯一索引）——也可随表创建，按迁移工具能力决定
```

### 5.2 会员与身份

```sql
-- 会员（含教练/运营，统一身份表）
CREATE TABLE app_user (
  id                bigserial PRIMARY KEY,
  openid            text        NOT NULL,
  unionid           text,                              -- 预留，不参与逻辑
  nickname          text,
  avatar_url        text,
  phone             varchar(20),                       -- 未绑定为 NULL
  phone_verified_at timestamptz,
  default_view      text        NOT NULL DEFAULT 'member'
                    CHECK (default_view IN ('member','coach','staff')),
  ban_until         timestamptz,                       -- 爽约限制期截止；可由明细重算（见 5.9）
  last_login_at     timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_user_openid ON app_user(openid);
CREATE UNIQUE INDEX uq_user_phone  ON app_user(phone) WHERE phone IS NOT NULL;
CREATE INDEX idx_user_ban ON app_user(ban_until) WHERE ban_until IS NOT NULL;
```

```sql
-- 角色集合（一人多角色，PRD D9）
CREATE TABLE user_role (
  user_id    bigint NOT NULL REFERENCES app_user(id),
  role       text   NOT NULL CHECK (role IN ('member','coach','staff','super_admin')),
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role)
);

-- 会员名单（门店会员系统导入；对应 PRD Q11）
CREATE TABLE member_roster (
  phone       varchar(20) PRIMARY KEY,
  member_no   text,
  member_name text,
  store_id    bigint REFERENCES store(id),
  source      text NOT NULL DEFAULT 'import',   -- import | manual
  active      boolean NOT NULL DEFAULT true,
  imported_at timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 教练（手机号白名单；对应 PRD Q9）
CREATE TABLE coach (
  id         bigserial PRIMARY KEY,
  user_id    bigint REFERENCES app_user(id),   -- 首次登录后回写
  phone      varchar(20) NOT NULL,
  name       text NOT NULL,
  avatar_url text,
  bio        text,
  status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_coach_phone ON coach(phone);
CREATE INDEX idx_coach_user ON coach(user_id) WHERE user_id IS NOT NULL;

-- 门店运营 / 前台（手机号白名单 + 门店绑定）
CREATE TABLE store_staff (
  id         bigserial PRIMARY KEY,
  user_id    bigint REFERENCES app_user(id),
  phone      varchar(20) NOT NULL,
  name       text NOT NULL,
  store_id   bigint NOT NULL REFERENCES store(id),
  role       text NOT NULL DEFAULT 'staff' CHECK (role IN ('staff','store_admin')),
  status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_store_staff_phone_store ON store_staff(phone, store_id);
CREATE INDEX idx_store_staff_user ON store_staff(user_id) WHERE user_id IS NOT NULL;
```

> **一个人多角色** = `user_role` 多行 + 同时命中 `coach.phone` 与 `member_roster.phone`。不额外建"角色视角"表：视角只是前端状态（`default_view` 是默认值，切换记埋点 `role_view_switch`）。

### 5.3 门店与课程类型

```sql
CREATE TABLE store (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  short_name   text NOT NULL,
  address      text NOT NULL,
  lat          numeric(9,6),
  lng          numeric(9,6),
  phone        varchar(20),
  status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  is_default   boolean NOT NULL DEFAULT false,   -- 首次进入的默认门店（AC-01-1）
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_store_default ON store(is_default) WHERE is_default;  -- 只允许一个默认门店

CREATE TABLE class_type (
  id                   bigserial PRIMARY KEY,
  name                 text NOT NULL,
  default_duration_min int  NOT NULL DEFAULT 60 CHECK (default_duration_min BETWEEN 10 AND 240),
  sort                 int  NOT NULL DEFAULT 0,
  status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active','hidden')),
  created_at           timestamptz NOT NULL DEFAULT now()
);
```

### 5.4 课次（排期场次）

```sql
CREATE TABLE session (
  id                  bigserial PRIMARY KEY,
  store_id            bigint NOT NULL REFERENCES store(id),
  coach_id            bigint NOT NULL REFERENCES coach(id),
  class_type_id       bigint REFERENCES class_type(id),
  title               text NOT NULL,
  room                text,
  start_at            timestamptz NOT NULL,                       -- T
  end_at              timestamptz NOT NULL,
  capacity            int  NOT NULL CHECK (capacity BETWEEN 1 AND 200),
  seat_taken          int  NOT NULL DEFAULT 0,                    -- 冗余计数，硬校验见下
  status              text NOT NULL DEFAULT 'scheduled'
                      CHECK (status IN ('scheduled','cancelled','finished')),
  cancel_reason       text,
  cancelled_at        timestamptz,
  cancelled_by        bigint REFERENCES app_user(id),
  checkin_code        char(6) NOT NULL,                           -- 6 位固定签到码
  checkin_started_at  timestamptz,                                -- 教练点"开始签到"的时刻
  waitlist_seq        bigint NOT NULL DEFAULT 0,                  -- 候补序号发生器（仅在持有该行锁时自增）
  promote_expire_streak int NOT NULL DEFAULT 0,                   -- 连续"转正后超时"次数（PRD 6.5.8）
  promote_paused      boolean NOT NULL DEFAULT false,             -- 级联超限后暂停自动转正，等运营处理
  roster_confirm_state text NOT NULL DEFAULT 'none'
                      CHECK (roster_confirm_state IN ('none','pending','done')),  -- 教练未发起签到→待运营确认名单
  reschedule_count    int NOT NULL DEFAULT 0,
  last_rescheduled_at timestamptz,                                -- 最近一次改期通知发出时刻
  created_by          bigint REFERENCES app_user(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT ck_session_time   CHECK (end_at > start_at),
  CONSTRAINT ck_seat_capacity  CHECK (seat_taken >= 0 AND seat_taken <= capacity)  -- ★ 超卖的最后一道闸
);

CREATE INDEX idx_session_store_time ON session(store_id, start_at) WHERE status = 'scheduled';
CREATE INDEX idx_session_start      ON session(start_at);
CREATE INDEX idx_session_coach_time ON session(coach_id, start_at);
CREATE INDEX idx_session_need_judge ON session(start_at) WHERE status = 'scheduled';  -- 任务扫描用

-- 同门店同教练课次时间不重叠（PRD AC-11-3 / AC-11-8），数据库级保证，避免只靠应用校验
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE session ADD CONSTRAINT ex_session_coach_time
  EXCLUDE USING gist (coach_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&)
  WHERE (status = 'scheduled');
```

**为什么把 `seat_taken` 做成冗余计数而不是每次 `count(*)`**：`count(*)` 无法在同一个原子的 SQL 里和"插入预约"构成一个"不许负数/不许超上限"的约束；而 `CHECK` 约束把"不超卖"变成数据库不可违反的事实。代价是计数可能与明细不一致 → 用第 9.4 节的对账脚本兜底，并在每次写操作后校验不变式。

**计数口径（与 PRD 6.9 完全一致，写死在代码常量里）**：

```
seat_taken = count(booking where session_id=? and status in ('booked','promote_pending','checked_in','no_show'))
remaining  = capacity - seat_taken   -- 不允许为负（CHECK 保证）
waitlist_count = count(booking where session_id=? and status = 'waitlisted')
```

### 5.5 预约 / 候补（单表，PRD D5）

```sql
CREATE TABLE booking (
  id                  bigserial PRIMARY KEY,
  session_id          bigint NOT NULL REFERENCES session(id),
  user_id             bigint NOT NULL REFERENCES app_user(id),
  status              text NOT NULL CHECK (status IN (
                        'booked','waitlisted','promote_pending','cancelled',
                        'checked_in','no_show','expired','auto_closed')),
  source              text NOT NULL DEFAULT 'direct' CHECK (source IN ('direct','waitlist','walk_in')),
  queue_no            bigint,                       -- 加入候补时按 session.waitlist_seq 分配，之后不可变
  booked_at           timestamptz NOT NULL DEFAULT now(),
  cancelled_at        timestamptz,
  cancel_reason       text CHECK (cancel_reason IN (
                        'member_cancel','reschedule_excuse','store_cancelled',
                        'waitlist_quit','give_up','admin_release')),
  cancelled_by        bigint REFERENCES app_user(id),
  promoted_at         timestamptz,
  confirm_deadline    timestamptz,                  -- min(promoted_at + 15min, T)
  confirmed_at        timestamptz,
  checked_in_at       timestamptz,
  checkin_method      text CHECK (checkin_method IN ('code','coach','ops')),
  operator_id         bigint REFERENCES app_user(id),
  no_show_at          timestamptz,                  -- 爽约判定时刻（用于审计；统计口径用 session.start_at，见 5.9）
  excused             boolean NOT NULL DEFAULT false,
  reschedule_affected boolean NOT NULL DEFAULT false,
  excuse_until        timestamptz,                  -- min(改期通知时刻+24h, 新T)
  skip_count          int NOT NULL DEFAULT 0,       -- 被转正校验跳过的累计次数（PRD 6.5.9）
  last_skip_at        timestamptz,
  last_skip_reason    text CHECK (last_skip_reason IN ('ban','limit','duplicate','invalid')),
  version             int NOT NULL DEFAULT 0,       -- 乐观锁预留（当前主用行锁）
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ★ "一人一课次最多 1 条活跃记录"（PRD 6.2.2）由数据库保证，而不是应用层 if
CREATE UNIQUE INDEX uq_booking_active ON booking(session_id, user_id)
  WHERE status IN ('booked','waitlisted','promote_pending','checked_in','no_show');

-- ★ 队列序号在活跃状态内唯一，杜绝"两人同为第 3 位"
CREATE UNIQUE INDEX uq_booking_queue ON booking(session_id, queue_no)
  WHERE status IN ('waitlisted','promote_pending');

CREATE INDEX idx_booking_user_active ON booking(user_id)
  WHERE status IN ('booked','promote_pending','waitlisted');          -- 上限校验用
CREATE INDEX idx_booking_session_status ON booking(session_id, status);
CREATE INDEX idx_booking_promote_deadline ON booking(confirm_deadline)
  WHERE status = 'promote_pending';                                   -- 超时扫描用
CREATE INDEX idx_booking_noshow ON booking(user_id, no_show_at)
  WHERE status = 'no_show' AND excused = false;                       -- 30 天爽约统计用
CREATE INDEX idx_booking_user_history ON booking(user_id, booked_at DESC);
```

**状态机（PRD 6.9 的工程化版本，`domain/booking-fsm.ts` 用表实现"允许的迁移集合"）**

```
                 ┌─────────────── cancel(member_cancel|reschedule_excuse) ──► cancelled
booked ──────────┤
  │              └─────────────── store_cancel ──────────────────────────► cancelled(store_cancelled)
  ├── checkin(code|coach|ops) ──────────────────────────────────────────► checked_in
  └── T+15min 未签到 ──────────────────────────────────────────────────► no_show (excused = ?)

waitlisted ──(座位释放 & 通过校验)──► promote_pending
waitlisted ──(校验不通过)──────────► waitlisted  (动 skip_count，位次不变)
waitlisted ──(会员退出)────────────► cancelled(waitlist_quit)
waitlisted ──(T-15min 队列清空)────► auto_closed
promote_pending ──(确认)──► booked(source=waitlist, confirmed_at)
promote_pending ──(放弃)──► cancelled(give_up)
promote_pending ──(超时)──► expired
```

任何不在上表的迁移一律抛 `DomainError('ILLEGAL_TRANSITION')` 并写 `audit_log`（不静默忽略）。

### 5.6 签到记录与尝试计数

```sql
-- 签到审计明细（每一次尝试都落一条，用于排查与"连续错 5 次"统计）
CREATE TABLE checkin_record (
  id          bigserial PRIMARY KEY,
  session_id  bigint NOT NULL REFERENCES session(id),
  user_id     bigint REFERENCES app_user(id),
  booking_id  bigint REFERENCES booking(id),
  method      text NOT NULL CHECK (method IN ('code','coach','ops')),
  result      text NOT NULL CHECK (result IN (
                'success','fail_code','fail_not_started','fail_window',
                'fail_ended','fail_status','fail_attempts')),
  attempt_no  int,
  operator_id bigint REFERENCES app_user(id),
  client_ip   inet,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_checkin_session ON checkin_record(session_id, created_at);
CREATE INDEX idx_checkin_session_user ON checkin_record(session_id, user_id, created_at);

-- 尝试计数器（保证并发下"错 5 次封禁"精确；AC-07-4）
CREATE TABLE checkin_attempt (
  session_id     bigint NOT NULL REFERENCES session(id),
  user_id        bigint NOT NULL REFERENCES app_user(id),
  fail_count     int NOT NULL DEFAULT 0,
  blocked        boolean NOT NULL DEFAULT false,
  first_fail_at  timestamptz,
  last_fail_at   timestamptz,
  PRIMARY KEY (session_id, user_id)
);
```

> `fail_count` 用 `INSERT ... ON CONFLICT DO UPDATE SET fail_count = checkin_attempt.fail_count + 1 RETURNING fail_count` 原子自增；成功后 `ON CONFLICT` 置 0。对外错误码只有 `CHECKIN_CODE_INVALID`，**不透露"码对了几位"**。

### 5.7 课次变更日志

```sql
CREATE TABLE session_change_log (
  id              bigserial PRIMARY KEY,
  session_id      bigint NOT NULL REFERENCES session(id),
  change_type     text NOT NULL CHECK (change_type IN ('reschedule','coach_change','capacity_change')),
  old_start_at    timestamptz,
  new_start_at    timestamptz,
  old_value       text,
  new_value       text,
  affected_count  int NOT NULL DEFAULT 0,      -- 受影响的有效预约数
  reason          text NOT NULL,
  notify_sent_at  timestamptz,                 -- ★ 决定是否豁免爽约（PRD 6.11.5）
  operator_id     bigint REFERENCES app_user(id),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_change_session ON session_change_log(session_id, created_at DESC);
```

### 5.8 通知与订阅授权

```sql
CREATE TABLE notification (
  id         bigserial PRIMARY KEY,
  user_id    bigint NOT NULL REFERENCES app_user(id),
  type       text NOT NULL,                     -- 见第 8.1 节事件表
  session_id bigint,
  booking_id bigint,
  title      text NOT NULL,
  content    text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}',
  inapp_read_at timestamptz,                    -- 站内消息已读
  wx_status  text NOT NULL DEFAULT 'not_needed'
             CHECK (wx_status IN ('not_needed','pending','sent','failed','no_grant','used_up')),
  wx_error   text,
  wx_sent_at timestamptz,
  wx_retry   int NOT NULL DEFAULT 0,
  dedupe_key text,                              -- 幂等键，防止任务重放导致重复消息
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_notification_dedupe ON notification(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX idx_notification_user ON notification(user_id, created_at DESC);
CREATE INDEX idx_notification_wx_pending ON notification(created_at) WHERE wx_status = 'pending';

-- 一次性订阅授权额度（客户端 requestSubscribeMessage 的回调上报）
CREATE TABLE subscribe_grant (
  id           bigserial PRIMARY KEY,
  user_id      bigint NOT NULL REFERENCES app_user(id),
  template_id  text NOT NULL,
  scene        text NOT NULL,                   -- booking_success | waitlist_join | ...
  granted_at   timestamptz NOT NULL DEFAULT now(),
  used_at      timestamptz,
  used_by      bigint REFERENCES notification(id)
);
CREATE INDEX idx_grant_usable ON subscribe_grant(user_id, template_id, granted_at)
  WHERE used_at IS NULL;
```

### 5.9 信誉（爽约次数与限制期）

**决策：不维护 `no_show_count_30d` 计数器，实时按明细统计。**

```sql
-- 判定与查询共用的口径（PRD 6.1 / 6.7.3）
SELECT count(*) FROM booking b JOIN session s ON s.id = b.session_id
WHERE b.user_id = $1
  AND b.status = 'no_show' AND b.excused = false
  AND s.start_at >= now() - interval '30 days'
  AND s.start_at <= now();
```

- **用 `s.start_at`（开课时间）而不是 `no_show_at`（判定时刻）**：判定任务延迟或重试会让窗口边界漂移，开课时间是业务事实。**这一点需 PM 确认（见 13 章 T6）**，PRD 6.1 写的是"最近 30 个自然日内累计"，未指明以哪个时刻计。
- 查询走 `idx_booking_noshow` + join，会员的爽约记录数极少（个位数），成本可忽略。
- `app_user.ban_until` 仍保留为**冗余字段**（避免每次请求都算），但**唯一的真相是明细**：解除/重算走同一个函数 `recomputeBanUntil(userId)`；第 9.4 节对账脚本会校验二者一致。
- 限制触发：`count >= 2` → `ban_until = 判定时刻 + 7 天`（PRD 6.7.3 口径）。幂等写法：`ban_until = greatest(coalesce(ban_until, now()), now() + interval '7 days')`。

### 5.10 任务表、幂等、审计、配置

```sql
CREATE TABLE scheduled_task (
  id          bigserial PRIMARY KEY,
  task_type   text NOT NULL,        -- no_show_judge | waitlist_close | promote_timeout
                                    -- | compensation_scan | session_finish | notification_retry
                                    -- | message_cleanup | roster_confirm_remind
  ref_type    text NOT NULL,        -- session | booking | notification
  ref_id      bigint NOT NULL,
  run_at      timestamptz NOT NULL,
  status      text NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','running','done','failed','skipped')),
  attempts    int NOT NULL DEFAULT 0,
  last_error  text,
  locked_by   text,
  locked_at   timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  done_at     timestamptz
);
-- 同一业务的同类任务只允许一个未完成任务（幂等根本保障）
CREATE UNIQUE INDEX uq_task_dedupe ON scheduled_task(task_type, ref_type, ref_id)
  WHERE status IN ('pending','running','failed');
CREATE INDEX idx_task_due ON scheduled_task(run_at) WHERE status = 'pending';

CREATE TABLE idempotency_key (
  user_id       bigint NOT NULL REFERENCES app_user(id),
  key           text   NOT NULL,
  endpoint      text   NOT NULL,
  request_hash  text,
  response_body jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  actor_id    bigint REFERENCES app_user(id),
  actor_role  text,
  action      text NOT NULL,
  target_type text,
  target_id   bigint,
  detail      jsonb NOT NULL DEFAULT '{}',
  client_ip   inet,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_target ON audit_log(target_type, target_id, created_at DESC);

CREATE TABLE app_config (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  remark     text,
  updated_by bigint REFERENCES app_user(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wx_access_token (
  id         int PRIMARY KEY DEFAULT 1 CHECK (id = 1),   -- 单行表
  token      text NOT NULL,
  expires_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE event_log (            -- 埋点（PRD 第 10 章）
  id         bigserial PRIMARY KEY,
  user_id    bigint,
  event      text NOT NULL,
  props      jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_event_time ON event_log(event, created_at);
```

**默认配置项（`app_config` 初始化数据，与 PRD 6.1 一一对应）**

| key | 默认值 | 对应 PRD |
| --- | --- | --- |
| `schedule.visible_days` | `8`（今天 ~ 今天+7） | 6.1 |
| `booking.open_before_days` | `7` | 6.1 |
| `booking.close_before_min` | `30` | 6.1 |
| `cancel.deadline_before_min` | `120` | 6.1 |
| `waitlist.promote_stop_before_min` | `15` | 6.1 |
| `waitlist.confirm_window_min` | `15` | 6.1 |
| `waitlist.max_concurrent` | `2` | 6.2.3 |
| `waitlist.max_cascade_timeouts` | `3` | 6.5.8 |
| `booking.max_concurrent` | `3` | 6.2.3 |
| `checkin.open_before_min` / `checkin.close_after_min` | `30` / `15` | 6.1 |
| `checkin.max_fail_attempts` | `5` | AC-07-4 |
| `no_show.judge_after_min` | `15` | 6.1 |
| `no_show.window_days` / `no_show.threshold` | `30` / `2` | 6.1 |
| `ban.days` | `7` | 6.7.3 |
| `reschedule.excuse_window_hours` | `24` | 6.11.4 |
| `session.default_capacity` / `max_capacity` | `30` / `200` | AC-11-1 |
| `booking.enabled` | `true` | 12.3 回滚开关 |

配置读取：进程内 60 秒缓存（`Map` + TTL），运营后台修改后写 `audit_log`。**配置变更只影响"变更之后发生的新判定"**，因为时间窗都是按 `now + 配置` 现算的，不存在需要回填的历史；**唯一例外**：`cancel.deadline_before_min` 调大后，原本已过期的取消会重新可取消——这是符合直觉的行为，但要在配置页给出提示文案（见 11.3-C6）。

---

## 6. 接口设计

### 6.1 通用约定

- 前缀 `/api/v1`，全部 HTTPS + JSON。
- 认证：`Authorization: Bearer <access_token>`。
- 写操作幂等：请求头 `Idempotency-Key: <uuid>`（客户端生成，按用户维度去重，TTL 24h）。**服务端必须校验**（PRD 13.1），命中则直接返回首次响应。
- 限流：按 `user_id + endpoint` 计数，默认 60 次/分钟；**预约/候补写接口 10 次/分钟**（防脚本抢课）。
- 统一响应包：

```json
{
  "code": "OK",
  "message": "",
  "data": { },
  "server_time": "2025-03-01T19:00:00+08:00",
  "trace_id": "01HX..."
}
```

- **每个课次对象都带 `windows` 与 `my_actions`**（PRD 13.2 / 13.3）：前端只做展示与倒计时，**不得自行推导业务规则**。

```json
"windows": {
  "server_now":      "2025-03-01T19:00:00+08:00",
  "booking_open_at": "2025-02-22T19:00:00+08:00",
  "booking_deadline":"2025-03-01T18:30:00+08:00",
  "cancel_deadline": "2025-03-01T17:00:00+08:00",
  "promote_stop_at": "2025-03-01T18:45:00+08:00",
  "checkin_start_at":"2025-03-01T18:30:00+08:00",
  "checkin_end_at":  "2025-03-01T19:15:00+08:00",
  "excuse_until":    null
},
"my_actions": [
  { "action": "cancel", "allowed": true,  "reason": null },
  { "action": "book",   "allowed": false, "reason": "BOOKING_CLOSED", "hint": "已截止预约" }
]
```

### 6.2 错误码表

| HTTP | code | 触发场景 | data 附加 |
| --- | --- | --- | --- |
| 400 | `INVALID_PARAM` | 参数缺失/格式错/日期超范围 | `fields[]` |
| 400 | `IDEMPOTENCY_KEY_REUSED` | 同一 key 但 request_hash 不同 | — |
| 401 | `UNAUTHENTICATED` / `TOKEN_EXPIRED` | 无 token / token 过期 | `action: "refresh"` |
| 403 | `PHONE_NOT_BOUND` | 未绑定手机号做写操作（AC-03-3） | `action: "bind_phone"` |
| 403 | `NOT_SESSION_COACH` | 非该课次教练访问名单（AC-12-6 / R12） | — |
| 403 | `NOT_STORE_STAFF` | 非该门店运营访问后台 | — |
| 403 | `BOOKING_DISABLED` | 回滚开关关闭 | `message` |
| 404 | `SESSION_NOT_FOUND` / `BOOKING_NOT_FOUND` | — | — |
| 409 | `NOT_OPEN` | `now < T-7天`（AC-03-10） | `booking_open_at` |
| 409 | `BOOKING_CLOSED` | `now > T-30min`（AC-03-7） | `booking_deadline` |
| 409 | `SESSION_CANCELLED` | 课次已被门店取消（AC-03-7） | `cancel_reason` |
| 409 | `SESSION_STARTED` | 课次已开始（`now >= T`） | `start_at` |
| 409 | `SESSION_FULL` | 座位已满（AC-03-2） | `waitlist_count`, `can_join_waitlist` |
| 409 | `WAITLIST_FULL` | 候补人数 ≥ 容量（AC-05-5） | — |
| 409 | `ALREADY_BOOKED` | 已有 booked/checked_in 记录（AC-05-4） | `booking_id` |
| 409 | `ALREADY_WAITLISTED` | 已在候补中（AC-05-3） | `position`, `queue_no` |
| 409 | `BOOKING_LIMIT_EXCEEDED` | 同时预约 ≥ 3（AC-03-5） | `limit`, `current` |
| 409 | `WAITLIST_LIMIT_EXCEEDED` | 同时候补 ≥ 2（AC-05-7） | `limit`, `current` |
| 409 | `BAN_ACTIVE` | 处于爽约限制期（AC-03-6） | `ban_until`, `no_show_count` |
| 409 | `CANCEL_DEADLINE_PASSED` | 已过 T-2h 且不在 `excuse_until`（AC-04-2） | `cancel_deadline` |
| 409 | `BOOKING_STATUS_NOT_ALLOWED` | 当前状态不允许该操作（如 expired 后确认） | `status` |
| 409 | `PROMOTE_EXPIRED` | 转正确认已超时（AC-06-5） | `expired_at` |
| 409 | `CHECKIN_NOT_STARTED` | 教练未发起签到（AC-07-14） | — |
| 409 | `CHECKIN_NOT_OPEN` | `now < T-30min`（AC-07-5） | `checkin_start_at` |
| 409 | `CHECKIN_ENDED` | `now >= T+15min`（AC-07-15） | `checkin_end_at` |
| 409 | `CHECKIN_CODE_INVALID` | 签到码不匹配 | `attempts_left` |
| 409 | `CHECKIN_ATTEMPTS_EXCEEDED` | 错 5 次封禁（AC-07-4） | — |
| 409 | `PHONE_ALREADY_BOUND` | 该手机号已绑定其他 openid（11.2-B3） | — |
| 409 | `SESSION_TIME_CONFLICT` | 排课/改期与同教练课次冲突（AC-11-3/8） | `conflict_session_id` |
| 409 | `CAPACITY_BELOW_TAKEN` | 缩容小于 `seat_taken`（AC-11-8） | `seat_taken` |
| 409 | `RESCHEDULE_IN_PAST` | 改期到过去时刻（AC-11-8） | — |
| 429 | `RATE_LIMITED` | 触发限流 | `retry_after` |
| 500 | `INTERNAL_ERROR` | 未预期异常（已记日志，含 trace_id） | `trace_id` |
| 502 | `WX_API_ERROR` | 微信接口失败（登录/手机号/订阅消息）；登录类直接失败，通知类降级 | `wx_errcode` |

### 6.3 课表查询

`GET /api/v1/schedule?date=2025-03-01&store_id=3&class_type_ids=1,4`

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `date` | 否 | `YYYY-MM-DD`，**按 `Asia/Shanghai` 自然日**解释；默认今天；超出 `[今天, 今天+7]` 返回 `400 INVALID_PARAM`（AC-01-3） |
| `store_id` | 否 | 缺省 = 我的默认门店（最近约课门店 → 系统默认门店，AC-01-1）；传 `all` 表示全部门店 |
| `class_type_ids` | 否 | 逗号分隔；缺省 = 不限 |

返回 `data`：

```json
{
  "date": "2025-03-01",
  "store_scope": { "mode": "single", "store_id": 3 },
  "date_options": ["2025-03-01", "2025-03-02", "2025-03-03", "2025-03-04",
                   "2025-03-05", "2025-03-06", "2025-03-07", "2025-03-08"],
  "items": [
    {
      "session_id": 1024,
      "title": "动感单车 45'",
      "class_type": { "id": 4, "name": "单车" },
      "start_at": "2025-03-01T19:00:00+08:00",
      "end_at": "2025-03-01T19:45:00+08:00",
      "coach": { "id": 7, "name": "李教练", "avatar_url": "https://cdn.example.com/a.png" },
      "store": { "id": 3, "short_name": "国贸店" },
      "room": "单车房",
      "capacity": 20,
      "seat_taken": 20,
      "remaining": 0,
      "waitlist_count": 4,
      "badge": "full",
      "badge_text": "已满 · 候补 4 人",
      "my_status": null,
      "windows": { },
      "my_actions": [ ]
    }
  ]
}
```

`badge` 枚举（9 种，与 AC-01-7 对齐）：`not_open`(未开放预约) / `available`(可预约) / `booked`(已预约) / `waitlisted`(候补中) / `promote_pending`(待确认) / `full`(已满) / `closed`(已截止) / `cancelled`(已取消) / `finished`(已结束)。

**角标优先级（服务端唯一口径，避免前后端不一致）**：
`my_status(promote_pending > booked > waitlisted) > cancelled/finished(按课次状态) > not_open(now < T-7天) > closed(now > T-30min) > full(remaining=0) > available`

服务端 SQL 关键点：一次查询取回该自然日该门店的 `session` + `left join booking(me)`；`badge` 与 `windows` 在应用层用 `domain/window.ts` 计算。**已取消课次**按 AC-01-8 由 PM/设计二选一：本方案默认**返回但置灰**（`cancelled` 角标），若要"不展示"则后端加 `exclude_cancelled=true` 参数，客户端切换不需要改后端。

### 6.4 课程详情

`GET /api/v1/sessions/:id`

返回 `data`：课表卡片全字段 + `description` + `store.address/lat/lng/phone` + `coach.bio` + `rules_text`（取消与爽约规则的**服务端生成文案**，避免前端硬编码过期规则）+ 我的记录：

```json
{
  "session": { "session_id": 1024, "title": "动感单车 45'", "windows": { }, "my_actions": [ ] },
  "my_booking": {
    "id": 5551, "status": "promote_pending", "source": "waitlist",
    "queue_no": 1, "position": 1,
    "confirm_deadline": "2025-03-01T18:52:00+08:00",
    "excuse_until": null,
    "reschedule_affected": false,
    "skip_count": 0, "last_skip_reason": null,
    "checkin_started": true,
    "checkin_code_visible": false
  },
  "change_notice": {
    "rescheduled": true,
    "old_start_at": "2025-03-01T18:00:00+08:00",
    "new_start_at": "2025-03-01T19:00:00+08:00",
    "text": "课程已改期：原 03月01日 18:00 → 现 03月01日 19:00"
  }
}
```

> 上例 `session` 字段做了省略展示，**实际返回与 6.3 的 item 结构完全一致**（同一构造函数，避免两处漂移）。

**`my_actions` 覆盖 PRD 13.3 要求的全部动作**：`book` / `join_waitlist` / `cancel` / `quit_waitlist` / `confirm_promote` / `give_up_promote` / `checkin` / `view_my_booking`；每个动作带 `allowed` + `reason(code)` + `hint(text)`。前端遇到未定义 `action` 时忽略即可（向前兼容）。

### 6.5 预约

`POST /api/v1/sessions/:id/bookings`（`Idempotency-Key` 必填）

请求体：`{}`（预约不需要额外参数；所有规则在服务端）

成功 `200`：

```json
{ "code": "OK", "data": {
  "booking": { "id": 5551, "session_id": 1024, "status": "booked",
               "source": "direct", "booked_at": "2025-03-01T19:00:01+08:00" },
  "session": { "remaining": 2, "seat_taken": 18, "capacity": 20 },
  "message": "预约成功，开课前 2 小时可取消",
  "need_subscribe": ["booking_success", "session_changed"]
}}
```

失败示例（HTTP 409）：

```json
{ "code": "BOOKING_LIMIT_EXCEEDED",
  "message": "最多同时预约 3 节课，请先取消或等已约课程结束",
  "data": { "limit": 3, "current": 3, "holding": [1024, 1031, 1044] } }
```

**服务端校验顺序（固定顺序，便于测试用例一一对应）**：

1. 幂等键命中 → 返回首次响应；
2. `booking.enabled` 开关；
3. 登录态 / 手机号绑定（`PHONE_NOT_BOUND`）；
4. 参数与课次存在性（`SESSION_NOT_FOUND`）；
5. 课次状态（`SESSION_CANCELLED` → `SESSION_STARTED`）；
6. 时间窗：`now < T-7天` → `NOT_OPEN`；`now > T-30min` → `BOOKING_CLOSED`（**边界左闭，与 PRD 6.0 一致**）；
7. 限制期 `ban_until > now` → `BAN_ACTIVE`；
8. 已有活跃记录 → `ALREADY_BOOKED` / `ALREADY_WAITLISTED`（先查，唯一索引兜底）；
9. 同时预约上限 ≥ 3 → `BOOKING_LIMIT_EXCEEDED`；
10. 占座（第 7.1 节 SQL）；
11. 落站内消息 + 订阅消息任务 + 埋点 `booking_success`。

### 6.6 取消

`DELETE /api/v1/sessions/:id/bookings/me`（`Idempotency-Key` 必填）

请求体：`{ "confirm": true }`（供前端二次确认语义，服务端不依赖它做正确性判断）

成功 `200`：

```json
{ "code": "OK", "data": {
  "booking": { "id": 5551, "status": "cancelled", "cancel_reason": "member_cancel",
               "cancelled_at": "2025-03-01T15:00:00+08:00" },
  "seat": { "transferred_to_waitlist": true, "remaining": 0 },
  "message": "已取消，名额已转给候补会员" }}
```

两种情况文案不同（**PRD AC-04-1 与 AC-04-4 的文案需要区分，见 11.1-A2**）：
- 座位真正释放：`"已取消，名额已释放"`，`remaining` +1；
- 座位转给候补：`"已取消，名额已转给候补会员"`，`remaining` 恒 0。

失败：`CANCEL_DEADLINE_PASSED`（含 `excuse_until` 说明）/ `BOOKING_STATUS_NOT_ALLOWED`（已取消/已签到等）/ `SESSION_CANCELLED`（AC-04-8 记录显示"门店已取消"）。

> **重复取消的幂等**：已 `cancelled` 再调用返回 200 并回放首次结果（AC-04-6 要求"重试后最终状态为 cancelled"），不报错——避免客户端网络重试拿到 409 造成"取消失败"的误报。

### 6.7 加入候补 / 退出候补

`POST /api/v1/sessions/:id/waitlist`

```json
{ "code": "OK", "data": {
  "booking": { "id": 5560, "status": "waitlisted", "queue_no": 5,
               "position": 5, "waitlist_count": 5 },
  "message": "已加入候补，当前第 5 位",
  "need_subscribe": ["waitlist_promoted"] } }
```

失败：`WAITLIST_FULL`、`ALREADY_WAITLISTED`（带 `position`）、`ALREADY_BOOKED`、`WAITLIST_LIMIT_EXCEEDED`、`BOOKING_CLOSED`、`NOT_OPEN`、`SESSION_CANCELLED`。

`DELETE /api/v1/sessions/:id/waitlist/me` → `{ "status": "cancelled", "cancel_reason": "waitlist_quit" }`，返回 `queue_shifted: true`（AC-05-8）。

> 关于 `position`：**`queue_no` 加入时分配、永不变**；`position` = 当前 `waitlisted` 队列中 `queue_no <= 我的` 的记录数（实时排名）。这样 AC-05-8（有人退出位次前移）与 AC-06-14（被跳过者位次不变）**同时成立**，不需要给队列做物理重排。详见 11.1-A1。

### 6.8 候补转正确认 / 放弃

`POST /api/v1/bookings/:id/confirm`（确认参加）
`POST /api/v1/bookings/:id/give-up`（放弃，座位立刻顺延）

成功：

```json
{ "code": "OK", "data": {
  "booking": { "id": 5560, "status": "booked", "source": "waitlist",
               "confirmed_at": "2025-03-01T18:40:12+08:00" },
  "message": "候补成功，记得按时到场签到" }}
```

放弃：`{ "status": "cancelled", "cancel_reason": "give_up", "transferred_to_next": true }`

失败：`PROMOTE_EXPIRED`（超时，需在 `confirm_deadline` 前操作）、`BOOKING_STATUS_NOT_ALLOWED`（状态不是 `promote_pending`）。**这两类失败都必须返回"座位已释放给谁"的语义信息**（`seat_released: true, waitlist_remaining: 3`），因为用户最关心"我还有没有机会"（AC-06-7：可重新加入候补）。

### 6.9 签到

**教练发起签到**：`POST /api/v1/coach/sessions/:id/checkin/start`
→ `{ "checkin_code": "482913", "checkin_started_at": "2025-03-01T18:40:00+08:00", "checked_in": 8, "expected": 20 }`
失败：`CHECKIN_NOT_OPEN`（`now < T-30min`）/ `CHECKIN_ENDED` / `NOT_SESSION_COACH` / `SESSION_CANCELLED`。幂等：重复调用返回同一 `checkin_code`，不改变 `checkin_started_at` 首次值。

**会员签到**：`POST /api/v1/sessions/:id/checkin`，body `{ "code": "482913" }`

```json
{ "code": "OK", "data": {
  "status": "checked_in",
  "checked_in_at": "2025-03-01T19:02:10+08:00",
  "message": "签到成功，课程愉快！" }}
```

失败：`CHECKIN_CODE_INVALID`（`attempts_left` 递减）、`CHECKIN_ATTEMPTS_EXCEEDED`、`CHECKIN_NOT_STARTED`、`CHECKIN_NOT_OPEN`（`checkin_start_at`）、`CHECKIN_ENDED`、`BOOKING_STATUS_NOT_ALLOWED`（不是 booked/已确认转正）。

**教练手动标记**：`POST /api/v1/coach/sessions/:id/roster/:userId/mark`，body `{ "present": true }` → 二次确认由前端负责；服务端写 `checkin_record(method='coach')` + `audit_log`（AC-07-7）。

**签到校验的服务端顺序（严格对齐 AC-07-3/5/13/14/15）**：

1. 我是该课次的有资格记录（`booked` 或已确认的转正）；
2. `now < T-30min` → `CHECKIN_NOT_OPEN`（**即使码正确也拒绝**，AC-07-5）；
3. `now >= T+15min` → `CHECKIN_ENDED`（上界开区间，AC-07-15）；
4. `checkin_started_at IS NULL` → `CHECKIN_NOT_STARTED`（AC-07-14）；
5. 尝试次数封禁；
6. 码比对（定长比较 + 恒定时间比较，避免时序侧信道）；
7. 成功 → 迁移状态 + 记 `checked_in_at` + 累计教练端实到数。

### 6.10 其余接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | `{code}` → token / roles / default_view |
| POST | `/api/v1/auth/refresh` | refresh token 换新 access |
| POST | `/api/v1/auth/phone` | `{code}` 绑定手机号（可带 `resume_intent`） |
| GET | `/api/v1/me` | 用户 + roles + `no_show_count` + `ban_until` |
| GET | `/api/v1/stores` ；`GET /api/v1/class-types` | 筛选数据源 |
| GET | `/api/v1/my/bookings?tab=upcoming\|history` | US-08（`promote_pending` 置顶，AC-08-2） |
| GET | `/api/v1/my/reputation` | 爽约次数、限制状态、规则文案 |
| GET | `/api/v1/notifications?cursor=` ；`POST /api/v1/notifications/:id/read` ；`POST /api/v1/notifications/read-all` ；`GET /api/v1/notifications/unread-count` | US-09 |
| POST | `/api/v1/subscribe-grants` | 客户端上报 `requestSubscribeMessage` 结果 |
| GET | `/api/v1/coach/sessions?date=` | US-10（含 `checkin_started_at`、`roster_confirm_state`） |
| GET | `/api/v1/coach/sessions/:id/roster` | 名单（教练或该门店运营方可访问） |
| POST | `/api/v1/admin/sessions` | 排课（生成 6 位码、默认时长） |
| POST | `/api/v1/admin/sessions/:id/walk-in` | 现场补位（**建议新增，见 11.2-B1**） |
| PATCH | `/api/v1/admin/sessions/:id` | 改期/换教练/容量（必填 `reason`、返回影响说明） |
| POST | `/api/v1/admin/sessions/:id/cancel` | 取消课次（必填 `reason`） |
| POST | `/api/v1/admin/sessions/:id/copy-week` | 复制上周课表（AC-11-6） |
| GET | `/api/v1/admin/sessions/:id/roster` ；`GET /api/v1/admin/sessions/:id/roster.csv` | 名单 + CSV |
| POST | `/api/v1/admin/sessions/:id/roster-confirm` | 教练未发起签到时运营确认名单（AC-07-16/17） |
| POST | `/api/v1/admin/sessions/:id/promote-resume` | 恢复自动转正（解除 `promote_paused`，见 11.1-A5） |
| GET | `/api/v1/admin/pending-tasks` | 待处理：改期待通知、级联暂停、待确认名单（R14） |
| POST | `/api/v1/admin/users/:id/unban` | 手动解除限制（6.7.6，写 audit_log） |
| GET | `/api/v1/admin/reconcile?session_id=` | 对账（见 9.4） |

---

## 7. 关键难点与方案

### 7.1 高并发抢名额：为什么不用分布式锁

**结论：用「会话行锁 + 条件更新 + CHECK 约束」，不用 Redis 分布式锁，不用乐观锁重试。**

```sql
-- 事务外先做无锁的资格预检（限制期、上限、时间窗），失败直接返回
BEGIN;

-- ① 锁住课次行：同一课次的所有写操作（预约/取消/候补/转正）被串行化
SELECT id, capacity, seat_taken, status, start_at, end_at
  FROM session WHERE id = $1 FOR UPDATE;

-- ② 状态与时间窗（在锁内重查，防止"加锁前的检查"失效）
--    status = 'scheduled'
--    now() >= start_at - make_interval(days => $open_days)
--    now() <= start_at - make_interval(mins => $close_min)

-- ③ 条件占座：这是防超卖的关键语句
UPDATE session
   SET seat_taken = seat_taken + 1, updated_at = now()
 WHERE id = $1 AND seat_taken < capacity;
-- 影响行数 = 0 → SESSION_FULL（回滚）

-- ④ 插入预约（部分唯一索引兜底：重复请求会撞 uq_booking_active）
INSERT INTO booking(session_id, user_id, status, source)
VALUES ($1, $2, 'booked', 'direct')
RETURNING id;

-- ⑤ 站内消息、订阅消息任务、埋点
COMMIT;
```

**为什么可行（并发量估算见 9.3）**：抢课瞬间同一课次 50～100 并发请求，在行锁上排队，每次持锁时间 = 1 条 UPDATE + 1 条 INSERT + 消息写入 ≈ 1～3 ms，100 个请求总耗时约 0.1～0.3 s，远低于 AC-03-1 要求的 2 s。**行锁在这里不是性能瓶颈，而是正确性的来源。**

**放弃的方案与代价**：

| 方案 | 为什么不用 |
| --- | --- |
| Redis 分布式锁 | 锁与数据不在同一事务里，锁过期/网络分区会破坏原子性；且引入一个只做"互斥"的新组件与运维面。本场景并发不足以让它有价值（ADR-3） |
| 纯乐观锁（version 重试） | 抢课场景冲突率高 → 大量无意义重试与用户可见的失败；而且"重试后可能已满"的语义比"排队 3ms"更差 |
| `SEQUENCE` / 号段预分配 | 无效方案：容量是硬限制，不是发号 |
| 排队中间件（MQ + 消费者） | 引入最终一致性，与 PRD "接口 2 秒内返回成功"冲突 |

**兜底**：
1. `CHECK (seat_taken <= capacity)`：任何代码路径写超了直接抛错并回滚，**绝不会静默超卖**（回滚条件第 1 条就是超卖）。
2. 唯一部分索引 `uq_booking_active`：并发重复预约也只留 1 条。
3. 入库后自检：同事务内 `SELECT count(*) ... FOR SHARE` 与 `seat_taken` 比对（成本极低，把口径漂移挡在写入时而非上线后）。

### 7.2 取消与候补转正必须同事务（PRD 6.5.1 / D11）

**这是整个系统最容易写错的 30 行代码，单独说明。**

```sql
BEGIN;

-- ① 锁课次行：序列化该课次上的所有写操作
SELECT id, capacity, seat_taken, status, start_at, end_at, waitlist_seq,
       promote_expire_streak, promote_paused
  FROM session WHERE id = $sess FOR UPDATE;

-- ② 锁我的记录并校验取消资格
SELECT * FROM booking WHERE session_id = $sess AND user_id = $me FOR UPDATE;
-- 资格：status='booked' 且 (now() <= start_at - cancel_deadline_before_min
--                              OR now() <= excuse_until)   -- 改期保护期，AC-04-11

-- ③ 取消我的记录
UPDATE booking
   SET status = 'cancelled', cancelled_at = now(),
       cancel_reason = $reason,          -- member_cancel | reschedule_excuse
       cancelled_by = $me
 WHERE id = $myBooking;

-- ④ 判断座位归属：候补非空 且 now() < start_at - promote_stop_before_min 且 未暂停
--    （按 7.3 的清单扫描队列，取第一名合格候补）
--    命中 → $headBooking：
UPDATE booking
   SET status = 'promote_pending', promoted_at = now(),
       confirm_deadline = least(now() + make_interval(mins => $confirm_win), $T)
 WHERE id = $headBooking;
--        ★★ 此处【不修改 session.seat_taken】★★
--        座位从"我的 booked"转移给"他的 promote_pending"，seat_taken 净变化 = 0
--        → 对外 remaining 恒为 0；AC-04-4 / AC-06-16 是数据库事实，不靠定时任务兜时间差

--    未命中（队列为空 / 无人合格 / now() >= T-15min / promote_paused）：
UPDATE session SET seat_taken = seat_taken - 1, updated_at = now() WHERE id = $sess;
--        → 座位真正释放，AC-04-10 / AC-06-13

-- ⑤ 写转正超时任务（与转正同一事务，杜绝"转正了但没有超时任务"）
INSERT INTO scheduled_task(task_type, ref_type, ref_id, run_at)
VALUES ('promote_timeout', 'booking', $headBooking, $confirmDeadline);

-- ⑥ 站内消息（同步写库）；订阅消息标 pending，由 worker 异步发
COMMIT;
```

**必须遵守的两条不变量**（对账脚本会验）：

- **不变量 A**：`session.seat_taken = count(booking where session_id=? and status in ('booked','promote_pending','checked_in','no_show'))`
- **不变量 B**：候补队列非空 且 `now < T-15min` 时，**不存在**读请求能观察到 `remaining > 0`

**为什么必须有兜底补偿扫描（AC-04-12）**：理论上事务保证了原子性，但进程在 `COMMIT` 前被 kill、或"先释放后转正"的代码被误写等极端情况，仍可能留下"座位已释放但没人转正"。兜底扫描每分钟一次：

```sql
-- 找出"有活跃候补、但 remaining > 0、且 now < T-15min"的课次
SELECT s.id FROM session s
 WHERE s.status = 'scheduled'
   AND s.start_at > now() + interval '15 min'
   AND s.seat_taken < s.capacity
   AND NOT s.promote_paused
   AND EXISTS (SELECT 1 FROM booking b WHERE b.session_id = s.id AND b.status = 'waitlisted');
```

对每个命中课次执行一次"释放+转正"事务（与 7.2 相同的函数），并写 `audit_log(action='compensate')`。**补偿动作必须幂等**：靠 `FOR UPDATE` + 状态判定，重复执行不会二次转正。

### 7.3 候补队列 FIFO 与转正前校验（PRD 6.5.9）

**入队**（FIFO 的实现方式）：

```sql
BEGIN;
SELECT id, capacity, seat_taken, start_at, waitlist_seq
  FROM session WHERE id = $sess FOR UPDATE;   -- 序列化入队，保证 queue_no 不重复

-- 校验：seat_taken >= capacity（满员才可候补）
--       now() <= start_at - 30min（未过截止）
--       waitlist_count < capacity（候补未满）
--       无活跃记录 / 同时候补 < 2（config.waitlist.max_concurrent）/ 不在限制期

UPDATE session SET waitlist_seq = waitlist_seq + 1 WHERE id = $sess;   -- 行锁内自增
-- 用 RETURNING waitlist_seq 取值作为本次 queue_no
INSERT INTO booking(session_id, user_id, status, source, queue_no)
VALUES ($sess, $me, 'waitlisted', 'direct', $newQueueNo);
COMMIT;
```

- `queue_no` 用**会话行锁内的计数器**而不是全局序列：全局序列会在课程之间穿插，不利于"按课次扫描队列"，且不易读。
- `uq_booking_queue(session_id, queue_no) WHERE status IN ('waitlisted','promote_pending')` 保证同一课次序号绝不重复。
- **同时候补上限 2**（AC-05-7）与"同时预约上限 3"是**两套计数**（PRD 6.2.3 明确分开），实现中必须分别算，别合并成一个"活跃记录数"。

**取人（扫描完整队列，找不到合格者才释放）**：

```sql
SELECT b.id, b.user_id, b.queue_no, u.ban_until,
       (SELECT count(*) FROM booking x
          JOIN session sx ON sx.id = x.session_id
         WHERE x.user_id = b.user_id
           AND x.status IN ('booked','promote_pending')
           AND x.session_id <> $sess
           AND sx.start_at > now()
       ) AS holding_cnt,
       (SELECT count(*) FROM booking y
         WHERE y.user_id = b.user_id AND y.session_id = $sess
           AND y.status IN ('booked','waitlisted','promote_pending')
       ) AS dup_cnt
  FROM booking b
  JOIN app_user u ON u.id = b.user_id
 WHERE b.session_id = $sess AND b.status = 'waitlisted'
 ORDER BY b.queue_no
 FOR UPDATE OF b;
```

在应用层按 PRD 6.5.9 逐条判定（顺序 a→b→c→d，全部为**加锁后重新读取**的数据）：

| 校验项 | 判定 | 不通过 |
| --- | --- | --- |
| a 限制期 | `ban_until IS NULL OR ban_until <= now()` | 跳过，`skip_count+1`，`last_skip_reason='ban'`，发站内消息 |
| b 同时预约上限 | `holding_cnt < 3`（**在锁内重读**） | 跳过，`last_skip_reason='limit'`，发站内消息 |
| c 重复活跃记录 | `dup_cnt = 1`（只有自己这条候选） | 跳过，`last_skip_reason='duplicate'`，**额外生成运营待处理提醒 + 日志**（数据异常） |
| d 记录仍有效 | `status = 'waitlisted'`（查询已过滤；锁内再确认未被"退出"抢先改掉） | 跳过，**不通知**（会员已自行退出） |

**合格者** → 置 `promote_pending`、锁座（`seat_taken` 不变）、发通知 + 写超时任务。

**级联计数（PRD 6.5.8）的实现**：

- `session.promote_expire_streak`：**转正在 `confirm_deadline` 内被确认 → 重置为 0**；**转正后超时（expired）→ +1**；被 6.5.9 跳过的**不计入**。
- `streak >= 3` → `session.promote_paused = true` + 站内消息给该门店运营 + 后台待处理提醒。
- 恢复：**运营手动操作**（`POST /admin/sessions/:id/promote-resume`）或该课次被改期/扩容时重置。**PRD 未定义恢复方式 → 见 11.1-A5**。

**超时扫描**（`promote_timeout` 任务，`run_at = confirm_deadline`）：

```sql
BEGIN;
SELECT id, start_at, promote_expire_streak FROM session WHERE id = $sess FOR UPDATE;

UPDATE booking SET status = 'expired', updated_at = now()
 WHERE id = $booking AND status = 'promote_pending' AND confirm_deadline <= now()
 RETURNING id;
-- 0 行 → 已被"确认/放弃"，任务幂等跳过，streak 不变
-- 1 行 → streak = streak + 1；然后按 7.2 第 ④ 步顺延给下一位合格候补
--        （顺延成功则 seat_taken 不变；无人合格才 seat_taken - 1）
COMMIT;
```

**`T-15min` 队列清空**（`waitlist_close` 任务）：`UPDATE booking SET status='auto_closed' WHERE session_id=$sess AND status='waitlisted'`，发站内消息，**不发订阅消息**（PRD 6.1）。

### 7.4 转正通知与超时释放的时序

```
T-7d  ──────────── 可预约 / 可候补 ──────────── T-30min ──── T-15min ── T ── T+15min
                                                    │            │              │
                                                    截止预约/候补  │            爽约判定
                                                                  └ 停止自动转正 + 队列清空
        ▲ 转正可发生在这一整段（任意时刻的座位释放触发）
                                                 ▲ confirm_deadline = min(promoted+15min, T)
```

- 转正的 `confirm_deadline` 一定 ≤ `T`（PRD 6.0）；由于 `now >= T-15min` 不再发起新转正，实际上不存在"临开课前才转正却只给 15 分钟"的情形，`min(..., T)` 是防御性写法。
- **可靠性设计**：转正 → 写站内消息（同步、必成功，失败则整个事务回滚）→ 标记订阅消息 `pending`（由通知 worker 异步发送、失败重试 3 次）。**订阅消息失败绝不影响座位流转**（AC-06-6）。
- **可观测性**：每次座位转移都写 `audit_log`（`action='seat_transfer'`，含 `from_booking_id` / `to_booking_id` / `reason`），出问题时能完整还原"这个座位到底给了谁、为什么"。

### 7.5 取消截止、爽约判定与改期保护

**取消资格判定（单一函数 `canCancel(booking, session, config, now)`）**：

```
可取消  ⟺  booking.status == 'booked'
            且 (now <= T - cancel.deadline_before_min     -- 常规截止，AC-04-1
                或 now <= booking.excuse_until)           -- 改期保护期，AC-04-11
reason  = (now <= T - cancel.deadline_before_min) ? 'member_cancel' : 'reschedule_excuse'
```

**爽约判定任务（`no_show_judge`，`run_at = T+15min`）**：

```sql
BEGIN;
SELECT id, start_at, checkin_started_at FROM session WHERE id = $sess FOR UPDATE;

-- ① 教练未发起签到 → 不做自动爽约判定，转运营确认（PRD 6.6.7 / AC-07-16）
--    checkin_started_at IS NULL 时：
UPDATE session SET roster_confirm_state = 'pending' WHERE id = $sess;
INSERT INTO scheduled_task(task_type, ref_type, ref_id, run_at)
VALUES ('roster_confirm_remind', 'session', $sess, now() + interval '2 hour');
-- 站内消息给该门店运营 + 后台待处理提醒，然后 COMMIT 并结束

-- ② 教练已发起签到时，判定爽约（含改期豁免）
UPDATE booking
   SET status = 'no_show',
       no_show_at = now(),
       excused = (
         reschedule_affected
         AND EXISTS (
           SELECT 1 FROM session_change_log cl
            WHERE cl.session_id = $sess
              AND cl.change_type IN ('reschedule','coach_change')
              AND cl.notify_sent_at > $newT - interval '2 hour'   -- ★ PRD 6.11.5
         )
       ),
       updated_at = now()
 WHERE session_id = $sess
   AND status = 'booked'
   AND checked_in_at IS NULL
 RETURNING id, user_id, excused;

-- ③ 对每名被判定的会员：重算 30 天爽约次数（口径见 5.9，excused 的不计）
--    count >= 2 → UPDATE app_user
--                   SET ban_until = greatest(coalesce(ban_until, now()), now() + interval '7 days')
--                 WHERE id = $userId;
--    并发性说明：ban_until 用 greatest() 累加上限，重复执行不会无限延长（幂等）

COMMIT;
```

- **任务幂等**：`UPDATE ... WHERE status = 'booked' AND checked_in_at IS NULL`，重放不会重复计数（第二次影响 0 行）。
- **任务延迟容错**：任务用 `run_at` 调度，但**判定条件用数据自身的时间**（`status='booked'` 而非 `now = T+15min`），因此进程停机重启后补跑结果仍然正确。为避免"课次被取消后误判爽约"，任务开始时需确认 `session.status = 'scheduled'`。
- **课次置 `finished`**：由独立的 `session_finish` 任务在 `now >= end_at` 时置位，与爽约判定解耦（见 11.2-B6）。
- **豁免判定用的 `notify_sent_at`** 是"通知实际发出时刻"（PRD 6.11.5 明确是"通知发出时刻"）。若订阅消息发失败只落了站内消息，`notify_sent_at` 取**站内消息入库时刻**（会员能看到的最早时刻），并在日志里区分两种来源来源。**这是一条需要 PM 确认的口径（11.3-C8）**。

### 7.6 签到防代签

按 PRD 的优先级（成本最低优先）：

**MVP 实现（PRD D2：6 位固定码）**

| 措施 | 说明 | 防的是 |
| --- | --- | --- |
| 签到码在 `checkin_started_at` 之前不下发 | 详情页 `checkin_code_visible=false`，接口不返回码 | 提前离店后他人代签 |
| 码绑定 `session_id`（不是全局码） | 校验必须同时匹配课次 | 跨课次用旧码 |
| 尝试次数封禁（5 次） | `checkin_attempt` 原子自增 | 暴力猜码 |
| 恒定时间比较 + 统一错误码 | 不透露"码对了几位" | 侧信道 |
| 签到时间落库 + 教练端实到/应到计数 | 教练肉眼核对（PRD R5 的人肉兜底） | 明显异常（全课同时签到） |
| 签到窗 `[T-30min, T+15min)` | 早到晚到都有界 | 提前很久或课后很久的"代签" |
| 教练可手动标记（降级路径） | 手机没电也能上课 | — |

**明确不做（MVP）**：地理位置校验（需要 `getLocation` 授权、用户常拒绝、伪造 GPS 成本极低，性价比差）；动态二维码（需要相机权限与扫码组件，PRD 明确放 P1-2）。

**P1 升级路径（现在就把数据留好）**：`checkin_record` 已含 `method`、`client_ip`、`attempt_no`；将来加"会员扫码 / 动态码"只需新增 `method` 取值与一个接口，数据模型不用改。

| 方案 | 增加成本 | 防代签效果 | 结论 |
| --- | --- | --- | --- |
| 现状：6 位固定码 | — | 低（转发即破） | MVP 接受（PRD R5 已标为已知风险） |
| 动态码（教练端每 30s 刷新 6 位） | 1～2 人日（码派生逻辑 + 有效期） | 中（需在场才能拿到当前码） | **建议提前到 MVP**：把码改成 `HMAC(session_id, 时间片)` 派生而非存储，服务端零状态 |
| 会员二维码 + 教练扫码 | 3～5 人日（相机、扫码、异常流） | 中高 | P1-2（PRD 已定） |
| 定位校验 | 2～3 人日 + 授权流失 | 低 | 不建议 |

> 动态码会改变 PRD AC-07-3/AC-07-5 的用例语义（"输入正确码"变成"输入当前时间片内的码"），**需 PM/测试点头才做（见 11.1-A6）**；默认实现按 PRD 的固定码。

### 7.7 时区与时间处理

| 决策 | 做法 | 理由 |
| --- | --- | --- |
| 存储 | 全部 `timestamptz`（内部 UTC） | 无夏令时、无歧义 |
| 唯一时钟源 | **SQL 里一律用 `now()`**，业务代码不产生"当前时间" | 多实例/容器时钟漂移会导致 PRD 6.0 的边界判定不一致。**测试走注入点**：`domain/window.ts` 接受 `now: Date` 参数，单测可任意构造边界 |
| 自然日 | 显式 `AT TIME ZONE 'Asia/Shanghai'`，常量 `APP_TZ='Asia/Shanghai'` | "今天+7"、"最近 30 天"都是自然日概念；只在 SQL 里换算，不在应用层做字符串拼接 |
| 课表日期查询 | `dayRange($date)` 辅助函数生成 `[本地当天 00:00+08:00, 次日 00:00+08:00)` 的半开区间，与 `start_at` 比较 | 用 `start_at` 与上海本地日边界比较，避免"UTC 日"切错；收敛到一个函数防漏写 |
| 返回给前端 | ISO 8601 带 `+08:00` 偏移 + `server_time` | 前端倒计时一律用 `server_time` 与本地时钟的差值做校正，**不信任手机时钟**（PRD 6.0） |
| 测试时间 | `app_config.test.time_offset_seconds`（**仅非生产环境生效**） | PRD 6.0 允许"服务端时间偏移接口"；实现为**只在 `NODE_ENV != 'production'` 时生效**，生产环境该 key 被忽略并记 WARN 日志。**这是硬性安全要求**：一个能改生产时钟的后门会毁掉所有时间窗正确性 |
| 无夏令时 | 不做时区库/多时区 | PRD 6.0 明确单时区 |

**边界实现（PRD 6.0 左闭规则的代码级口径）**，全部写成单测（每个边界 2 个用例：恰好相等 / 差 1 秒）：

| 判定 | 实现 | 恰好等于时 |
| --- | --- | --- |
| 预约开放 | `now >= T - open_before_days 天` | 可预约 |
| 预约/候补截止 | `now <= T - 30min` | 可预约 |
| 取消截止 | `now <= T - 2h`（或 `now <= excuse_until`） | 可取消 |
| 自动转正 | `now < T - 15min` | **不转正**（严格小于） |
| 转正确认 | `now <= confirm_deadline` | 可确认 |
| 签到 | `T-30min <= now < T+15min` | 上界为假（已结束） |
| 爽约判定 | 任务在 `now >= T+15min` 首次触发，判定条件为 `status='booked' AND checked_in_at IS NULL` | 到点即判定 |

### 7.8 幂等与并发写的一致处理

| 场景 | 机制 |
| --- | --- |
| 连点"立即预约"3 次（AC-03-4） | `Idempotency-Key`（同 key 返回首次响应）+ `uq_booking_active` 唯一索引兜底 |
| 取消重试（AC-04-6） | 幂等键 + 状态机允许"已 cancelled → 再得到 cancelled 结果"的重复读 |
| 双重签到 | `UPDATE booking SET status='checked_in', checked_in_at=now() WHERE id=$b AND status='booked'`，0 行则返回当前状态（幂等成功） |
| 重复任务执行 | `scheduled_task` 部分唯一索引 + 状态条件 UPDATE |
| 重复消息 | `notification.dedupe_key` 唯一索引（如 `promote_pending:{booking_id}`） |

---

## 8. 微信订阅消息模板与触达策略

### 8.1 事件 → 模板 → 渠道

| PRD 事件 | 模板（需在小程序后台申请） | 渠道 | 触发时机 |
| --- | --- | --- | --- |
| 预约成功 | 预约成功通知（课程名称、上课时间、上课地点、温馨提示） | 站内消息（必）+ 订阅消息 | 预约接口成功后 |
| **候补转正待确认** | 候补转正通知（课程名称、上课时间、上课地点、确认截止时间） | **订阅消息（尽力）+ 站内消息** | 转正事务提交后（异步 worker） |
| 候补未成功 / 队列清空 | 走站内消息 | 站内消息 | `waitlist_close` 任务 |
| 候补被跳过（限制期/超上限） | 走站内消息 | 站内消息 | 跳过时 |
| 课次被门店取消 | 课程取消通知（课程名称、原上课时间、取消原因） | 订阅消息 + 站内消息 | 取消事务后 |
| **课次改期 / 换教练** | 课程变更通知（课程名称、原时间、新时间、变更原因） | **订阅消息 + 站内消息** | 改期事务后（含无损取消截止时刻） |
| 开课提醒 `T-2h` | 开课提醒（课程名称、上课时间、上课地点） | 订阅消息 | P1（PRD P1-1）；表结构已就绪 |
| 爽约记录与限制 | 走站内消息 | 站内消息 | 爽约判定任务之后 |
| 教练未发起签到（提醒运营） | 站内消息（运营侧） | 站内消息 | `roster_confirm_remind` |

**模板字段注意**：订阅消息模板的 `thing` 类型字段有长度限制（约 20 字符）、`time` 需 `yyyy-MM-dd HH:mm` 格式、`phrase` 有枚举长度限制。**因此模板文案必须在落库时做截断与格式归一化**（统一在 `infra/notifier/template.ts` 做：落库完整文案，推送用截断版），否则会收到微信 `47003` 参数错误。模板 ID 放环境变量，不写进代码。

### 8.2 一次性订阅的现实约束（必须让产品知道）

- 健身行业**只能用一次性订阅**（"长期订阅"仅开放给政务/医疗等特定类目）。**每次推送都在消耗用户的一次授权**。
- 触达策略的核心是**在"用户有动机的瞬间"请求授权**，而不是启动时批量请求：

| 时机 | 请求哪几个模板 | 为什么此时成功率最高 |
| --- | --- | --- |
| 预约成功后 | 预约成功、课程变更、开课提醒（一次 `requestSubscribeMessage` 最多 3 个模板 ID） | 用户刚约上课，对"别错过课"最敏感 |
| **加入候补时** | 候补转正、课程变更 | **候补用户比谁都怕错过转正通知，这是全链路授权率最高的时刻**，必须在这里请求 |
| 详情页点"签到"时 | 开课提醒 | 次选 |

- 额度管理：客户端回调 → `POST /subscribe-grants` 上报 → `subscribe_grant` 记账；发送时 `SELECT ... FOR UPDATE SKIP LOCKED` 出队一条可用额度并置 `used_at`；无额度 → `wx_status='no_grant'`，**只落站内消息**。
- 微信返回码处理：`43101`（用户拒绝）→ `no_grant`（**不消耗额度**）；`47003`（参数错误）→ 记 `failed` 并告警（这是配置/代码 bug，不能静默）；`42001/40001`（token 失效）→ 刷新 token 后重试一次；`45009`（频次超限）→ 退避重试。
- **降级必须不影响业务**：通知 worker 与业务事务完全解耦（`notification.wx_status='pending'`），worker 挂了只是推送晚到，座位流转、超时释放照常。**站内消息是同步写库、进入业务事务的**——这是"业务事实"，不能丢。

### 8.3 站内消息必达的设计

- 所有状态变化在同一事务里写 `notification`（同步、强一致）。
- 小程序启动/切前台时拉未读数（`GET /notifications/unread-count`）→ 红点。
- **`promote_pending` 的强提示**（R1 的兜底）：详情页 + 我的预约列表 + 消息中心三处都显示倒计时（AC-06-7/AC-08-2/AC-09-3）；存在 `promote_pending` 时首页顶部横幅置顶显示"你有 1 个候补名额待确认，HH:MM 前失效"。这是把"订阅消息失败"的损失降到最低的产品级手段。
- 90 天清理：`message_cleanup` 每日任务（AC-09-5）——**只删已读消息**，未读保留（避免"用户没看到就被删"）。

---

## 9. 质量属性检查

### 9.1 可靠性

| 失败场景 | 降级行为 |
| --- | --- |
| 微信 `code2session` 不可用 | 登录失败，前端提示"微信服务异常，请稍后重试"；**已登录用户的 token 仍有效**，课表与"我的预约"可继续用 |
| 微信订阅消息发送失败 | 站内消息照常；重试 3 次（1min / 5min / 30min）后置 `failed` 并计入日报 |
| 微信 access_token 失效 | 统一 `getToken()` 内检测 `42001/40001` → 强制刷新一次 → 重试；仍有问题则 WARN + 站内消息兜底 |
| 定时任务进程崩溃 | 任务在 DB 里，重启后按 `run_at` 补跑；判定条件不依赖"精确时刻"，补跑结果正确 |
| DB 故障/数据丢失 | MVP 单实例 + 每日 `pg_dump` + 云盘快照。**明确写清**：RPO 约 1 天、RTO 约 30 分钟，这是"单机省钱"的已知代价（见 11.3-C2），不是遗漏 |
| 接口超时/网络中断 | 写接口全部幂等（`Idempotency-Key`），客户端可安全重试 |
| 缓存不一致 | 唯一的缓存是 `app_config`（60s TTL）与 `wx_access_token`（按过期时间），都是"过期即刷新"，不存在脏读 |
| 超卖检测 | 写入时的 CHECK 约束（硬拦截）+ 每日 `reconcile` 脚本（软校验） |

**重试策略统一**：只重试**幂等**的远端调用（微信接口、任务执行）；业务写接口靠客户端幂等键；禁止对"可能已成功"的写操作做无脑自动重试。

### 9.2 安全

| 面 | 措施 |
| --- | --- |
| 输入校验 | 所有外部输入在 HTTP 边界用 JSON Schema 校验（Fastify）；日期范围、枚举、长度、数字上下限；`class_type_ids` 限制最大数量 |
| SQL 注入 | 全部参数化（Kysely/`pg` 占位符）；**不拼接 SQL 字符串**；动态排序/分页字段用白名单枚举 |
| 认证 | 自签 JWT（HS256，密钥从 `JWT_SECRET` 环境变量读），access 2h / refresh 30d 可吊销 |
| 授权 | 名单接口服务端判定"该课次教练 / 该门店运营"，**与前端视角无关**（AC-12-6）；上线前必测"会员请求他人课次名单 → 403" |
| 越权枚举 | 读接口按 `user_id` 过滤；`/bookings/:id/confirm` 校验 `booking.user_id = me` |
| 敏感数据 | 手机号在日志与后台列表**默认掩码**（`138****8888`）；`session_key` 不落库；`app_secret`、模板 ID、`JWT_SECRET` 只从环境变量读，不写代码、不入日志 |
| 限流 | 全局 60/min/用户；预约、候补、签到写接口 10/min/用户；登录 20/min/IP |
| 防刷 | 幂等键 + 唯一索引 + 尝试次数封禁；签到码恒定时间比较 |
| 审计 | 运营的每一次取消/改期/解除限制/改名单都写 `audit_log`（含 IP、操作人） |
| 最小权限 | DB 应用账号只授 `SELECT/INSERT/UPDATE/DELETE`（DDL 用迁移账号）；生产 DB 不暴露公网 |
| 回滚开关 | `booking.enabled=false` 一键关闭预约入口，返回 `403 BOOKING_DISABLED`，前端显示维护提示（PRD 12.3） |
| 环境隔离 | 测试时间偏移只在非生产生效（7.7） |

### 9.3 性能预估（**全部为估算，附假设**）

**假设**（等 PRD Q3 答复后修正）：

- 门店 5 家；每店每天 8 场团课 → 全量 40 场/天；
- 注册会员 1000 人，日活 25% = 250 人；活跃会员每周约 2 次课 → 每天约 350～500 次预约尝试；
- 课表查询：日活 250 人 × 人均 3 次/天 = 750 次/天；
- 课次容量 20，热门课（晚间黄金时段）满员率约 70%。

| 场景 | 估算量 | 瓶颈判断 |
| --- | --- | --- |
| 日常 API | 平均 < 0.1 QPS，峰值 5～20 QPS | 单进程 Node + 单 PG 轻松（Node 单进程可处理数百 QPS 的简单 JSON 接口） |
| 抢课瞬间 | 单课次 30～100 并发 | 行锁串行，预计 P95 < 500 ms，远低于 AC-03-1 的 2 s；**需并发脚本实测**（10.2） |
| 课表查询 | 单日 5 店 × 8 场 = 40 行，8 天共 320 行 | 一次索引扫描 + join，P95 < 50 ms（无需分页，接口保留 `limit` 参数） |
| 存储 | 3 年约 4.4 万课次、booking 约 60 万行；`event_log` 最大（每天数千行） | 单表 60 万行对 PG 无压力；`event_log` 半年后评估按月分区/归档（P1） |
| 订阅消息 | 每天最多几百条 | 微信接口频次限制 + 自建 worker 串行限速（默认 5 条/秒，可配），避免触发 `45009` |
| DB 连接 | 池 10～20 连接 | 行锁等待 + 池耗尽是最可能的第一瓶颈 → 监控 `pg_stat_activity` 的 `wait_event = 'Lock'` 与池等待时长 |

**结论**：不需要 Redis、不需要读写分离、不需要分库分表。**唯一需要提前准备的是并发压测（M6）与"取消 vs 预约"并发专项（AC-04-9）**。

### 9.4 可维护性

- **规则集中在 `domain/`**：时间窗（7 个边界的判定）、状态机合法迁移、候补取人校验清单、错误码。**这三个文件是"业务规则的可执行文档"**，PRD 第 6 章任何改动只需改它们 + 对应测试。
- **唯一真源**：`seat_taken` 的口径写成代码常量 + 对账查询收敛在一个模块里，避免散落多处。
- **对账脚本**（`GET /api/v1/admin/reconcile`，也可作 CLI）：

```sql
-- 不变量 A：seat_taken 与明细一致（返回的每一行都是需要人工介入的漂移）
SELECT s.id, s.seat_taken,
       (SELECT count(*) FROM booking b
         WHERE b.session_id = s.id
           AND b.status IN ('booked','promote_pending','checked_in','no_show')) AS actual
  FROM session s
 WHERE s.seat_taken <> (SELECT count(*) FROM booking b
                         WHERE b.session_id = s.id
                           AND b.status IN ('booked','promote_pending','checked_in','no_show'));

-- 不变量 B：队列非空 + 未到 T-15min 时，不应有 remaining > 0
SELECT s.id, s.capacity - s.seat_taken AS remaining
  FROM session s
 WHERE s.status = 'scheduled'
   AND s.start_at > now() + interval '15 min'
   AND s.seat_taken < s.capacity
   AND NOT s.promote_paused
   AND EXISTS (SELECT 1 FROM booking b WHERE b.session_id = s.id AND b.status = 'waitlisted');

-- 不变量 C：ban_until 与明细一致（有标签但明细不足 2 次 = 需要修正）
SELECT u.id, u.ban_until FROM app_user u
 WHERE u.ban_until IS NOT NULL AND u.ban_until > now()
   AND (SELECT count(*) FROM booking b JOIN session s ON s.id = b.session_id
         WHERE b.user_id = u.id AND b.status = 'no_show' AND b.excused = false
           AND s.start_at >= now() - interval '30 days') < 2;
```

### 9.5 可观测性

| 需要回答的问题 | 靠什么 |
| --- | --- |
| 这个用户的预约为什么失败？ | `trace_id` 贯穿日志 + `audit_log` + `idempotency_key` 记录 |
| 这个座位到底给了谁、为什么？ | `audit_log(action='seat_transfer')` + `booking.queue_no/skip_count` |
| 有没有超卖/计数漂移？ | 每日 `reconcile` 三不变量检查（有结果就告警） |
| 通知触达率如何（PRD R1）？ | `notification.wx_status` 聚合（`sent/no_grant/used_up/failed` 占比）→ 决定是否上短信 |
| 教练是否漏发起签到（R14）？ | `session.checkin_started_at IS NULL AND start_at < now()` 计数（每日报表） |
| 改期与豁免是否异常（R11）？ | `session_change_log` + `booking.excused` 统计 |
| 候补健康度？ | `booking.skip_count` 分布、`promote_expire_streak` 分布、补偿扫描触发次数（理想为 0，非 0 就要查根因） |

**告警（MVP 用"日志 + 每日报表"实现，不上告警平台）**：超卖（CHECK 约束报错立刻 ERROR）、补偿扫描命中（说明有事务中断，必须查）、订阅消息连续失败 > 10、预约接口 5xx 比例 > 0.5%。

---

## 10. 开发里程碑与工作量拆解

### 10.1 里程碑（每个里程碑都可运行、可验证）

| 里程碑 | 内容 | 如何验证 | 估算 |
| --- | --- | --- | --- |
| **M0 地基** | 仓库结构、TS/ESLint/build、`pg`+Kysely、迁移框架与全部 DDL、`app_config` 初始化、统一错误码与响应包、pino + `trace_id`、幂等中间件、限流、`.env.example` | 起了服务 + 迁移跑通；`GET /api/v1/health` 返回迁移版本；错误包格式快照测试 | 4 人日 |
| **M1 身份** | `auth`：code2session、JWT、refresh、手机号绑定、角色解析、白名单/名单 CSV 导入脚本、`GET /me` | 走通"游客浏览 → 绑手机 → roles 正确"；**AC-12-1~12-10 逐条对应用例**；"一人多角色默认会员视角 + 视角切换不改鉴权"用例；会员请求他人名单 → 403 | 4 人日 |
| **M2 排课与课表** | `admin` 排课 CRUD、冲突校验（EXCLUDE 约束）、取消课次、`schedule` 课表与详情；`domain/window.ts` 全边界单测；9 种角标 | 建课次 → 会员端能看到；**7 个时间边界各 2 个用例（恰好/差 1 秒）**；角标优先级用例；AC-01-*、AC-02-*、AC-11-1~11-3 | 5 人日 |
| **M3 预约与取消** | 占座事务、上限、限制期、幂等、取消（含真正释放分支）、对账脚本 | **50 并发抢 1 个名额 → 恰好 1 人成功、`seat_taken ≤ capacity`**（R3）；连点 3 次只 1 条记录；`reconcile` 不变量 A 通过；AC-03-*、AC-04-1~3 / 4-10 | 5 人日 |
| **M4 候补** | 入队/退出、取人校验清单、转正、超时任务、级联上限、兜底补偿扫描、`position` 计算 | **"取消 + 转正同事务"专项：并发下无人能观察到 `remaining > 0`**（AC-04-4 / 4-9 / 6-16）；6.5.9 四类跳过各一例 + 顺延 + 位次不变；无人合格 → 真正释放；注入故障验证补偿扫描 60s 内修复 | 6 人日 |
| **M5 签到与爽约** | 教练发起签到、会员签到（含尝试计数）、手动标记、爽约判定任务、运营确认名单、信誉页 | AC-07-1~17（含 `T-30min` 恰好、`T+15min` 恰好、教练未发起的另一条路径）；爽约 2 次 → `ban_until` 正确且明细与冗余一致；AC-08-*、AC-10-* | 5 人日 |
| **M6 通知、埋点与加固** | 订阅消息 worker、站内消息、消息中心、埋点、压测、监控、对账、回滚开关、部署脚本与 README、上线演练 | `subscribe_grant` 无额度时降级不阻塞业务；压测报告；`reconcile` 三不变量全绿；`booking.enabled=false` 后入口关闭且已有预约不变 | 6 人日 |
| **M7 改期保护（可与 M4/M5 部分并行）** | `PATCH /admin/sessions/:id`（改期/换教练/容量）、`SessionChangeLog`、任务重算、保护期与豁免爽约、批量通知 | AC-11-7~11；改期后全部时间窗按新 T 重算的用例；"通知发出晚于新 T-2h → excused"用例 | 4 人日 |

**合计约 39 人日 ≈ 8 周（单人，含自测）。** 若 CEO 能再给 1 名工程师：M1/M2 与 M4/M5 可并行，压到 **5～6 周**。以上**不含**微信审核等待、域名备案（见 10.3）与运营排课数据准备。

### 10.2 测试与验证策略（与测试同事对齐）

| 层级 | 覆盖 | 说明 |
| --- | --- | --- |
| 单元测试（Vitest，不碰 DB） | `domain/window.ts` 全部边界（7 边界 × 恰好/差 1s）、状态机合法迁移表、候补校验清单判定、坏数据 | 目标：PRD 第 6 章每一条规则都有一条测试指向它 |
| 集成测试（真 PostgreSQL） | 预约/取消/候补/转正/签到/爽约全链路；唯一索引冲突；CHECK 约束触发；任务幂等重放 | 必须用**真库**，因为正确性依赖行锁与约束；每个用例独立事务或独立 schema |
| 并发验收 | ① 50 并发抢 1 座；② 取消 vs 预约并发（AC-04-9）；③ 扩容 3 座同时转正 3 人（AC-06-10）；④ 转正超时与"确认"并发（只能有一个生效） | 脚本 + 断言 SQL（`seat_taken ≤ capacity`、`remaining` 的读取序列从未 > 0） |
| 时间旅行测试 | 通过 `test.time_offset_seconds`（仅非生产）构造 `T-7d / T-30min / T-2h / T-15min / T+15min` 等恰好时刻 | **本项目最关键的测试手段**，没有它边界无法验证 |
| 手工验收 | 真机走 PRD 12.2 的内部试用清单 | 含低端机首屏 ≤ 1.5s（AC-01-9） |

### 10.3 非编码前置（必须并行启动，否则会卡上线）

| 项 | 谁做 | 提前量 | 风险 |
| --- | --- | --- | --- |
| 小程序主体资质 + 服务类目（含"健身"类目） | CEO | 1～2 周 | 类目不符会影响订阅消息模板可用性 |
| 后端域名 ICP 备案 + HTTPS 证书 + 小程序 request 域名白名单 | CEO / 工程师 | 2～3 周（备案） | **最长的提前量，必须最先启动** |
| 微信订阅消息模板申请（第 8.1 节 4～5 个） | 工程师 | 1～3 天/模板 | 模板被拒需替换文案；**模板 ID 未定则 M6 无法收尾** |
| **手机号快速验证组件**（`getPhoneNumber`）：按次收费（约 0.03 元/次），需主体已认证（非个人主体）且有额度 | CEO | 3～5 天 | **PRD 未提及此项成本与主体资质限制（见 11.3-C3）**；若不可用，只能改"手机号 + 短信验证码"（成本更高）或"前台录入 + 人工核对" |
| 门店会员名单（手机号）+ 教练/运营白名单导入 | 运营 | 上线前 1 周 | PRD Q9/Q11，**不提供则 `roles=[]` 的用户无法预约，功能实际不可用** |
| 未来 7 天课表数据录入 | 运营 | 上线前 1 周 | PRD 12.1 前置条件 |
| 小程序代码审核 | 工程师 | 1～7 天 | 首次提交常被拒（类目/隐私政策/用户信息使用说明）；隐私协议必须写清"收集手机号用于门店身份核对" |

---

## 11. 对照 PRD：不可行或需产品调整的点

> 分三类：**A 必须改产品口径（否则实现会自相矛盾）**、**B 需要补规则（PRD 未定义，实现无法下手）**、**C 需 PM/CEO 知晓的代价与风险**。

### 11.1 A 类：PRD 内部矛盾 / 实现上做不到原样

| # | PRD 位置 | 问题 | 我的实现口径（需 PM 确认） |
| --- | --- | --- | --- |
| A1 | AC-05-8「位次依次前移」 vs 6.5.9 / AC-06-14「位次不变」 | 若"位次"= 存储的 `queue_no`，两处直接矛盾；若"位次"= 实时排名，则"位次不变"又不成立 | **`queue_no` 加入时分配、永不变；对外 `position` = 实时排名（活跃 `waitlisted` 中 `queue_no` 不大于我的记录数）**。这样"有人退出 → 我的 position 前移"（AC-05-8）与"我被跳过 → 我仍在原 `queue_no`、后续仍可被转正"（AC-06-14）同时成立。**需要产品确认展示文案**：被跳过者会看到"第 1 位"却始终不转正，建议显示"你在等待中（前面 0 人 · 本次名额已顺延给他人：原因）"，跳过 ≥2 次时显著提示（PRD 6.5.9 已要求） |
| A2 | AC-04-1「提示『已取消，名额已释放』」 vs 6.5.1 / AC-04-4「`remaining` 恒为 0，不对外释放」 | 同一接口两种语义，文案冲突；照 AC-04-1 写会让会员以为"名额回到了池子里" | **按分支返回不同文案**：真正释放 → "已取消，名额已释放"；转给候补 → "已取消，名额已转给候补会员"（并附 `remaining`）。**请 PM 确认这条文案**（本方案已按此实现） |
| A3 | 6.1「可见范围 = 今天 ~ 今天+7」+ AC-01-11「可见但未开放」 | 逻辑不矛盾，但**"未开放预约"只在特定条件出现**：课次进入可见窗口的时刻早于 `T-7天` 时，即课次落在"今天+7"且开课时刻晚于当天当前时刻。排到第 8 天之后的课次根本不可见，也不会出现该状态 | 实现按 PRD 原文（可见 8 个自然日 + 开放时刻独立判定），**但请 PM 与设计确认"未开放预约"角标的出现预期**，否则测试会问"为什么我从来造不出这个状态"。测试用时间偏移接口可稳定构造 |
| A4 | AC-07-9「签到不可撤销，只有运营后台可改」 vs AC-07-17「运营标记某人未到场 → 置 no_show」 | 若该人已签到，运营能否改成未到场？PRD 未说明 | 实现为：**运营可修改名单，但 `checked_in → no_show` 需二次确认 + 填原因 + 写 audit_log**；`booked → checked_in`（补签）同 AC-07-7。**请 PM 确认** |
| A5 | 6.5.8「一次座位释放最多连续转正 3 名候补」 | "一次释放"指"一次事件"还是"该课次累计"？若按"一次事件"，每次释放都重新给 3 次机会，级联上限形同虚设；且 PRD 未定义"之后怎么恢复自动转正" | 实现为**按课次累计的连续超时次数** `promote_expire_streak`（转正被确认即重置 0），达 3 次置 `promote_paused = true`。恢复方式：运营手动"恢复自动转正"，或该课次改期/扩容时重置。**请 PM 确认语义与恢复方式** |
| A6 | 6.6.5「签到码课次创建时生成，全课程有效期不变」 | 固定码一旦被转发（会员群）无法止损；而 6.6.5 又要求"只在窗口内可见"，两者张力明显 | 实现按 PRD 存固定码；**建议增加"运营可重置签到码"**（约 1 人日，写 audit_log）。另建议提前做时间片动态码（见 7.6，成本近似为 0，但会改变 AC-07-3/AC-07-5 的用例语义，需 PM/测试点头） |

### 11.2 B 类：需要补规则（PRD 未定义 → 实现无法下手）

| # | 缺口 | 为什么必须补 | 我的临时默认（请 PM 确认后写进 PRD） |
| --- | --- | --- | --- |
| B1 | **"`T-30min` 之后到店的会员想上课怎么办"** | 预约/候补在 `T-30min` 截止。座位若在 `T-30min` 之后被释放（扩容、放弃转正、转正超时都可能发生），此时既不能预约、也不再转正 → **座位必然空着**，而现场可能有会员想上。PRD 全流程没有"现场补位"入口 | 实现按 PRD 释放该座位（`remaining+1` 但无人能约）；**建议补规则**：教练/运营可在 `[T-30min, T+15min]` 内在名单页"现场加人"（把到场但未预约的会员置为 `booked`，`source='walk_in'`，记 audit_log，满员则拒绝）。**这是真实高频场景，不做会被门店投诉** |
| B2 | 会员名单来源与更新频率（PRD Q11） | `roles` 解析、AC-12-7"未查询到会员信息"提示都依赖它 | 建 `member_roster` 表 + CSV 导入脚本（幂等 upsert），**由运营提供名单文件**；未命中时按 AC-12-7 提示 + 展示门店电话 |
| B3 | **同一手机号已被另一个 openid 绑定的处理** | 用户换微信、或家人共用一个手机号，都会撞 `uq_user_phone` | 返回 `409 PHONE_ALREADY_BOUND`，提示"该手机号已绑定其他微信账号，请联系前台"；**不做自助解绑**（PRD 明确不做换绑）。运营有诉求时由工程师后台手工改（记 audit_log） |
| B4 | **改期是否同时改 `end_at`（时长）** | AC-11-1 说"教练可改结束时间"，6.11 只提改 `start_at`；不改时长则 `end_at` 会与 `start_at` 不再匹配 | 实现：改 `start_at` 时**默认平移 `end_at`（保持时长）**；运营显式改时长时走同一条时间窗重算路径，并重建定时任务 |
| B5 | 消息文案谁定稿 | 订阅消息模板文案由微信审核，无法与 PRD 100% 对齐；站内消息文案也需要统一出处 | 技术侧把站内消息文案集中在 `infra/notifier/copy.ts`；**请 PM 定稿文案**，订阅消息模板只求字段齐全（第 8.1 节） |
| B6 | 课次 `finished` 的定义时机 | PRD 6.9 有 `finished` 状态，但没说何时置位 | 实现：`session_finish` 任务在 `now >= end_at` 时置 `finished`（与爽约判定按 `T+15min` 独立跑，二者解耦） |
| B7 | 排课时的签到码唯一性范围 | PRD 只说"6 位码"，若要求全局唯一，100 万课次后必然碰撞 | 实现为**课次内唯一（校验绑 `session_id`）**，不做全局唯一（这也符合 6.6.5 的"课次创建时生成"）。请 PM 确认无"扫码枪全局识别课次"的需求 |

### 11.3 C 类：已按 PRD 实现，但请知晓代价 / 风险

| # | 决策 | 代价 / 风险 |
| --- | --- | --- |
| C1 | **不上 Redis / MQ**（ADR-7） | 单实例是明确的**单点**：进程挂 = 全站不可用（靠容器重启，预计 1～2 分钟）。届时把任务调度换成 advisory lock 已支持多实例、API 无状态可直接加副本，**是可逆的** |
| C2 | **单实例 + 每日备份** | RPO ≈ 1 天、RTO ≈ 30 分钟。若 CEO 要求更低的 RPO，需开云数据库高可用（成本上升，P1） |
| C3 | **手机号快速验证按次收费**（约 0.03 元/次）+ 需非个人主体 | 1000 名会员首次绑定约 30 元；若被刷则成本放大 → 小程序端限制"仅在用户点击绑定时调用"，并把调用次数纳入日报巡检。**PRD 未提及此项成本与主体资质，需 CEO 确认** |
| C4 | **订阅消息只能一次性订阅** | PRD R1 的"站内消息兜底"不是可选项而是**必须项**；本方案已把站内消息做成同步强一致、订阅消息做成异步尽力而为。上线 2 周后用 `notification.wx_status` 数据决定是否上短信 |
| C5 | **`seat_taken` 冗余计数** | 存在与明细漂移的理论可能 → CHECK 约束（防超卖）+ 写时自检 + 每日对账三重兜底；**不接受"漂移就漂移"** |
| C6 | **时间窗参数可配置** | 调大 `cancel.deadline_before_min` 会让"原本已过期"的取消重新可用（可接受，但要在配置页写明）；调小不追溯已发生的操作 |
| C7 | **tabBar 最多 5 个** | 会员端 4 个 + 教练 1 个 = 5，**运营入口只能放"我的"页**（非 tab）。若产品希望运营有独立 tab，则必须把"消息中心"合并进别处，需产品决策 |
| C8 | **改期豁免爽约的口径** | `notify_sent_at` 取"通知实际发出时刻"；订阅消息失败时取站内消息入库时刻。边界（正好 `新T-2h`）的用例需明确——本方案按"站内消息入库时刻"判定，因为它是最早能被用户看到的时刻 |
| C9 | **`walk_in` 现场补位不在 PRD 范围内** | 若采纳 B1，会新增 `source='walk_in'`、一个运营接口、以及"现场补位是否占用上座率统计"的口径问题（建议计入上座率但单独标记）。这是**新增范围**，需 CEO 确认是否进入 MVP |

---

## 12. 决策记录（ADR）

### ADR-1：模块化单体，不拆服务
**背景**：会员端、教练端、运营端共用同一套时间窗与状态机规则；预估峰值 5～20 QPS。
**决策**：一个 Fastify 服务 + 内部模块划分（auth/schedule/booking/waitlist/checkin/coach/admin/notification），模块间只通过 application service 调用。
**备选方案**：按端拆 3 个服务；或"预约服务 + 课表服务"拆开。
**影响与代价**：无法独立扩缩容与部署（改签到要重启全部）；换来规则只有一份实现、部署只有一套、调试不需要跨服务追踪。将来有真实压力时，无状态 API 可直接加副本，任务调度已用 advisory lock 支持多实例。

### ADR-2：选 PostgreSQL（而非 MySQL / MongoDB）
**背景**：需要"不超卖"的强保证、"一人一课次一条活跃记录"、"同教练时间不重叠"、"任务表可靠投递"。
**决策**：PostgreSQL 16，用 `CHECK` + 部分唯一索引 + `EXCLUDE USING gist` + `FOR UPDATE` + `SKIP LOCKED` + `timestamptz now()` 作为唯一时钟源。
**备选方案**：MySQL（无部分索引与排除约束，正确性要下沉到应用层）；MongoDB（跨文档事务与约束能力弱）。
**影响与代价**：需要 1 名会 PG 的工程师（就是我）；`btree_gist` 扩展在部分托管 PG 需显式开启。

### ADR-3：并发控制用行锁 + 条件更新 + CHECK 约束，不用分布式锁
见 7.1。**备选**：Redis 分布式锁、纯乐观锁重试、MQ 串行化。
**影响与代价**：单课次的写操作严格串行（这是特性不是缺陷）；同一课次高并发下用户等待毫秒级；放弃了"理论上更高吞吐"的方案，因为本场景的并发量用不到。

### ADR-4：预约与候补共用 `booking` 单表（延续 PRD D5）+ 部分唯一索引
**背景**：PRD D5 已定，且二者共享"座位"资源与生命周期。
**决策**：单表 + `status` 状态机 + 两个部分唯一索引（活跃记录唯一、队列序号唯一）。
**备选方案**：预约表 + 候补表分开。
**影响与代价**：状态机较复杂（8 个状态），必须在 `domain/booking-fsm.ts` 用表驱动 + 单测锁死；换来"取消一处、状态一致"（不会出现两表不一致）。

### ADR-5：定时任务用数据库任务表，而不是内存 cron 或消息队列
**背景**：必须支持"转正后 15 分钟超时""`T+15min` 爽约判定""`T-15min` 队列清空"这些**与业务事务强绑定**的延迟任务。
**决策**：`scheduled_task` 表 + 业务事务内插入 + 进程内 10 秒轮询 + `FOR UPDATE SKIP LOCKED` 领取 + `pg_advisory_lock` 保证单实例执行；任务幂等（状态条件 UPDATE）。
**备选方案**：`node-cron` 内存定时（重启丢任务、多实例重复）；BullMQ/Redis（多一个组件）；时间轮（进程重启丢失）。
**影响与代价**：任务精度 ±10 秒（对 15 分钟窗口完全够用）；任务表需要运维（失败任务要能查、能重放，故提供 `admin/pending-tasks`）。

### ADR-6：运营后台做在小程序内
**背景**：PRD 5 把运营端形态交给工程师定型；运营需求是"排课 + 看名单 + 取消/改期"。
**决策**：小程序内"我的 → 运营中心"（非 tab，见 11.3-C7），复用登录与鉴权。
**备选方案**：独立 Web 后台（React + 独立部署 + 独立鉴权）。
**影响与代价**：批量排课与 CSV 导出体验不如 Web（用"复制上周课表" + "生成 CSV 下载链接"缓解）；换来 2～3 人日与一整套前端工程的节省。若运营侧抱怨严重，P1-5 再补 Web 后台，后端接口不需要改。

### ADR-7：MVP 不引入 Redis 与消息队列
**背景**：唯一能想到的 Redis 用途是缓存课表与 access_token；唯一能用 MQ 的地方是通知发送。
**决策**：都不引入。access_token 用单行表 + advisory lock；通知用 `notification` 表 + worker 轮询；配置用进程内 60s TTL 缓存。
**备选方案**：Redis（缓存 + 分布式锁 + 队列"三合一"）。
**影响与代价**：单点（11.3-C1）；未来若加副本需要引入共享缓存，那时再引入成本很低。**放弃的是"看起来更专业"的技术栈，得到的是一个人能运维的系统。**

### ADR-8：JWT 里只放 `user_id`，角色每次查库
**背景**：角色（会员/教练/运营）来自可随时变更的白名单（AC-12-9）；名单类数据越权是 R12（高影响）。
**决策**：token 只放 `user_id`；每次请求查一次角色与门店范围；鉴权与前端"当前视角"无关。
**备选方案**：角色写进 token（少一次查询，但改角色要等 token 过期，且视角可能被伪造）。
**影响与代价**：每请求多 1～2 次索引查询（可忽略）；换来整类越权风险消失。

### ADR-9：站内消息强一致，订阅消息尽力而为
**背景**：微信一次性订阅授权率不可控（R1），但候补转正的超时释放不能因为通知失败就暂停（AC-06-6）。
**决策**：状态落库 + 站内消息在同一事务；订阅消息标 `pending` 由 worker 发送，失败只重试、不改业务状态；前端在详情页/列表/消息中心/首页横幅四处做倒计时强提示。
**备选方案**：等通知成功再开始计时（会造成"没授权的人永远不超时"，破坏座位流转）。
**影响与代价**：触达率不达标时座位浪费仍会发生（靠 15 分钟窗口 + 级联上限 3 次限制损失）；用 `wx_status` 数据驱动是否上短信。

### ADR-10：所有写操作强制幂等键 + 数据库唯一约束双保险
**背景**：PRD 13.1 要求全部写操作幂等；小程序网络抖动、用户连点、接口重试是常态。
**决策**：客户端生成 `Idempotency-Key`（按用户去重 24h），命中直接回放响应；同时用状态机 + 部分唯一索引兜住"没有 key 的重复"（如旧版本客户端）。
**备选方案**：只靠唯一索引（无法回放"首次响应"，用户可能看到失败但实际成功）。
**影响与代价**：多一张表与一次查询；换来"重试永远安全"，这是预约/取消这类操作的正确性底线。

### ADR-11：时间统一 `timestamptz` + `now()` + 显式上海时区换算
见 7.7。**备选**：存本地时间字符串（跨环境必出 bug）。**影响与代价**：所有涉及自然日的查询都要 `AT TIME ZONE 'Asia/Shanghai'`，容易漏写 → 用 `dayRange(date)` 辅助函数收敛，并加单测。

---

## 13. 待确认问题清单（与 PRD 第 11.2 节合并视图）

| # | 问题 | 需要谁回答 | 阻塞什么 | 我的临时默认 |
| --- | --- | --- | --- | --- |
| Q1（PRD） | "签到"是会员到场签到还是教练到岗打卡？ | CEO / 运营 | 教练端范围 | 会员到场签到（教练发起） |
| Q2（PRD） | 是否校验会员卡/课次余额？ | CEO / 运营 | 预约前置校验（PRD D4） | 不校验，只锁座位 |
| Q3（PRD） | 门店数 / 每日课次数 / 单课容量 | CEO / 运营 | 性能估算与压测规模 | 5 店 × 8 场 × 20 人（见 9.3） |
| Q4（PRD） | 游客能否浏览课表？ | CEO | 首屏与鉴权 | 可浏览，写操作需登录 |
| Q5（PRD） | 订阅消息模板是否可申请 | 工程师（已列入 10.3） | M6 | 按第 8.1 节申请 4～5 个模板 |
| Q9 / Q11（PRD） | 教练/运营白名单、会员名单来源与维护方 | 运营 | **M1 与上线** | CSV 导入脚本 + `member_roster` 表；运营提供文件 |
| Q12（PRD） | 教练能否约自己带的课 | CEO / 运营 | 预约规则 | 允许（会员能力不被剥夺） |
| Q13（PRD） | 教练漏发起签到时是否照常记爽约 | 门店运营 | AC-07-16/17 | 不自动判定，转运营确认 |
| Q14（PRD） | 改期无损取消期 24 小时是否合适 | 运营 | AC-11-9/10 | `min(通知时刻+24h, 新T)` |
| **T1** | **`T-30min` 后的座位能否"现场补位"**（11.2-B1 / 11.3-C9） | CEO / 运营 / PM | 座位浪费 + 门店投诉；是否扩范围 | 不支持（按 PRD），建议补规则 |
| **T2** | 位次展示口径（11.1-A1） | PM | 前端文案与测试用例 | `queue_no` 不变 + `position` 实时排名 |
| **T3** | 级联上限的语义与恢复方式（11.1-A5） | PM / 运营 | M4 | 按课次累计连续超时；运营手动恢复 |
| **T4** | 运营可否把"已签到"改成"未到场"（11.1-A4） | PM / 运营 | M5 | 可改，需二次确认 + 原因 + 审计 |
| **T5** | 是否采用时间片动态签到码（11.1-A6 / 7.6） | PM / 测试 | M5 用例语义 | 先按固定码（PRD 原文） |
| **T6** | 30 天爽约窗口以"开课时间"还是"判定时刻"计（5.9） | PM / 运营 | M5 | 开课时间 `session.start_at` |
| **T7** | 手机号快速验证组件的付费与主体资质（11.3-C3） | CEO | 绑手机号方案 + 成本 | 按按次收费预备；不可用则改短信验证码 |
| **T8** | 6 位签到码是否需要全局唯一（11.2-B7） | PM | 排课校验逻辑 | 课次内唯一（校验绑 `session_id`） |

---

## 14. 交付物与下一步

- 本文档：`docs/Tech-Design.md`（PRD 里登记为 `docs/技术方案.md`）。
- **本阶段按 CEO 指令只写文档，未写任何代码**，因此没有任何"已测试通过"的结论。所有并发、超卖、时间边界结论均为**设计推导 + 估算**，验证方式见 10.2。
- 下一步（待 CEO 确认后开工）：① M0 地基（仓库结构 + 全部 DDL + 迁移）；② 同时启动 10.3 的非编码前置（域名备案最紧）；③ 与测试同事对齐"时间旅行测试"手段与并发验收脚本的断言口径。
- 我需要的输入：第 13 章的 **Q3（规模）**、**Q9/Q11（名单）**、**T1/T2（两条会改到接口与文案的规则）**。这三项给到之前，M1 之后的工作有返工风险。
