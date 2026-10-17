# 技术方案：健身房团课预约微信小程序（MVP）

- 版本：v1.0（MVP 阶段，**只写方案不写代码**）
- 作者：工程师
- 日期：首版
- 上游依据：`docs/PRD.md`（v1.1，产品经理）。本方案的所有业务口径以 PRD 为准；与 PRD 表述不一致处集中列在 **§9 待确认问题**，不擅自改需求。
- 阅读指引：先看 §2（架构）→ §4（数据模型）→ §6（关键难点），评审重点在 §6 与 §9。
- 本阶段交付物边界：只有本文档。代码、迁移脚本、测试在本方案评审通过并进入开发后产出。

---

## 0. 摘要（一页版）

| 问题 | 结论 |
|---|---|
| 架构模式 | **模块化单体**：1 个 API 服务（Node 20 + TypeScript + Fastify）+ 1 个同进程内的定时任务循环 + 1 个 PostgreSQL 16。不拆微服务、不引消息队列、不引 Redis。 |
| 数据一致性 | 名额占用/释放/递补判定全部放在**单个数据库事务**内，用「原子 UPDATE 的 WHERE 条件比较-设置」+「场次行 `SELECT ... FOR UPDATE`」+「数据库 CHECK 约束 `occupied_count + reserved_count <= capacity`」三道防线防超卖。 |
| 候补递补 | 每个释放名额生成一条 `waitlist_release`（释放事件，`status = open`、`try_count = 0`），名额通过 `class_session.reserved_count` **锁定**给该事件；`try_count` 挂在释放事件上（与 PRD R3 一致，**不**挂在 `waitlist_entry` 上，见 §9-Q4）。 |
| 爽约计数 | 不落存储计数器，**查询时实时重算**（`status = no_show` 且场次 `end_at ∈ [now-30d, now)`）；`member_store_credit` 上的两个计数列降级为**展示缓存**。 |
| 通知 | 事务内写 `notification_outbox`，独立 worker 每 2 秒轮询发送微信订阅消息；失败/未授权走 PRD 规定的降级（确认时限延长 + 站内红点）。 |
| 估算规模 | 假设 3 门店、MAU 1000、日活 300、每人每天 20 次操作 ≈ 6000 请求/天；开课前 30 分钟高峰 ≈ 0.8 QPS，峰值按 10× 估 ≈ 8 QPS。单实例足够，瓶颈是同一场次的行锁等待（毫秒级）。 |
| 工作量 | 30 人日 + 20% 缓冲 ≈ **36 人日**（单人全职 ≈ 7.5 周）；4 周版砍法见 §8.3。 |
| 外部阻塞 | 小程序主体/类目、request 合法域名备案 + HTTPS、订阅消息模板报备（PRD Q5）— **必须在开发前 5～10 个工作日发起**，否则 S7 通知链路只能先跑站内降级。 |

---

## 1. 需求映射

### 1.1 P0/P1 功能 → 模块

| PRD 功能 | 优先级 | 实现模块 | 备注 |
|---|---|---|---|
| S1 微信登录与身份绑定 | P0 | `services/auth` + `member` 表 | code2session → 自有 JWT（**含角色**）；手机号授权走 `getuserphonenumber`；`GET /api/stores` 保证未绑定会员也能进课表 |
| S2 课表与课程详情 | P0 | `services/schedule` + `class_session` | 剩余名额公式**三处共用同一函数**（课表/详情/预约） |
| S3 预约 | P0 | `services/booking` | 原子占位 + 部分唯一索引 + 时间冲突 + 黑名单 |
| S4 我的预约 | P0 | `services/booking` | 进行中/历史两组；候补态复用同一响应结构 |
| S5 取消 / 请假 | P0 | `services/booking` + `services/waitlist` | 取消事务内联触发递补 |
| S6 候补入队 | P0 | `services/waitlist` | 队列上限按**实时记录数**校验（锁场次行串行入队）；实时 rank |
| S7 递补与确认 | P0 | `services/waitlist` + `jobs/promotion` | 名额锁定 + `try_count` 上限 + 临期门槛 |
| S8 教练点名 | P0 | `services/attendance` | 服务端窗口校验 + 变更日志 |
| S9 爽约与黑名单 | P0 | `services/credit` + `jobs/settlement` | 实时重算 + `pending_review` 兜底；`GET /api/me/credit` 提供置灰与解除时间的数据源 |
| S10 门店规则配置 | P0 | `services/admin` + `store_rule` | 6 个参数 + 3 个降级开关 |
| S11 轻量排课/名单（P1） | P1 | `services/admin` + CSV 导入脚本 | 兜底：CSV 导入 + SQL 脚本 |
| S12 订阅消息授权（P1） | P1 | 小程序端 + `subscription_message_log` | 授权结果记日志，与 outbox 对账 |
| S13 手动加容量/手动递补（P1） | P1 | `services/admin` | 加容量 → 每个新空位生成独立 `waitlist_release` |
| 埋点（PRD §9） | P0 | `event_log` 表 + `domain/events.ts` | 事件名与属性严格按 PRD §9 |

### 1.2 非功能需求映射

| PRD 要求 | 实现手段 |
|---|---|
| §2 指标可算（4 周后能回答 4 个问题） | `event_log` 埋点 + 5 条"指标 SQL"写进运维手册（利用率/爽约率/候补递补成功率/递补确认率/点名完成率） |
| §8.3 门店级降级开关（2 小时内可回滚） | `store_rule.enable_ban / enable_waitlist / enable_late_cancel`，读库即时生效（30 秒内存缓存），无需发版 |
| §5.3 R5 计数实时重算 | 重算 SQL + 索引 `booking(member_id, status)` JOIN `class_session(end_at)` |
| §7.1 隐私 | 教练端不返回 `member.phone`；日志脱敏；最小权限 DB 账号 |
| §5.3 R1 绝不超卖 | 原子 UPDATE + CHECK 约束（最后防线） |

---

## 2. 架构概览

### 2.1 架构模式与理由

**模块化单体（Modular Monolith），单实例部署。**

理由（针对本项目规模，而不是"最佳实践"）：

1. **业务只有一个强一致热点**：名额计数（`occupied_count` / `reserved_count`）。它天然要求"一个事务里决策 + 写入"。拆服务只会把本地事务换成分布式事务，把问题变难。
2. **请求量极小**（§0 估算 < 10 QPS）。拆服务、加 MQ、加缓存的收益在这条量级上是 0，成本是运维复杂度和一致性风险。
3. **唯一需要异步的东西是"发微信通知"和"定时补判"**，这两件事用一张 outbox 表 + 一个定时循环就够，不需要 MQ。
4. **可逆性**：模块边界清晰（services 之间不互相读对方表，只通过 service 方法），将来真到需要拆（例如多门店、多租户、候补量级上升）时，可按 service 切出去。现在拆是不可逆的过度设计。

代价（明确放弃的东西）：不能用"每模块独立扩容/独立发布"；单进程故障 = 全站不可用（用 systemd/docker restart + 健康检查兜底，MVP 可接受）。任务循环与 API 同进程意味着任务线程被长请求拖慢的风险——用"任务循环独立于 HTTP 请求处理，且每个任务加 30 秒超时"来约束。

### 2.2 模块划分与依赖方向

```
                        ┌────────────────────────────────────────────┐
                        │  微信小程序（原生 TS，1 套代码 3 种角色视图）  │
                        │  会员端：课表/详情/我的预约/取消/候补确认      │
                        │  教练端：我今天的课/点名页                   │
                        │  运营端：规则/名单/未点名异常/加容量          │
                        │  导航：custom-tab-bar 按 roles 渲染 2～3 个 tab │
                        └───────────────────┬────────────────────────┘
                                            │ HTTPS + Authorization: Bearer <JWT>
                        ┌───────────────────▼────────────────────────┐
                        │  Nginx：TLS 终止、limit_req、access log      │
                        └───────────────────┬────────────────────────┘
                                            │
   ┌────────────────────────────────────────▼────────────────────────────────────────┐
   │  API 服务（Node 20 + TypeScript + Fastify）                                      │
   │                                                                                 │
   │   http/routes  ──►  services  ──►  repos（SQL）                                  │
   │   （zod 校验入参）    （事务边界）      （只有这里出现 SQL）                        │
   │                        │                                                        │
   │                        └──►  domain/rules（纯函数，无 IO，可单测）                │
   │                                                                                 │
   │   auth │ schedule │ booking │ waitlist │ attendance │ credit │ notify │ admin    │
   └────────────────────────┬────────────────────────────────────────────────────────┘
                            │ pg 连接池（max 10）
   ┌────────────────────────▼────────────────────────────────────────────────────────┐
   │  Job Worker（同进程独立循环，每个任务带超时 + 幂等）                                │
   │  ① outbox 发送通知（每 2s）  ② 递补超时扫描（每 30s）                              │
   │  ③ 场次结算判定（每 5min）    ④ 展示缓存刷新/黑名单到期（每 1h）                    │
   └────────────────────────┬────────────────────────────────────────────────────────┘
                            │
   ┌────────────────────────▼────────────────────────────────────────────────────────┐
   │  PostgreSQL 16（单实例 + 每日全量备份 + WAL 归档）                                 │
   └─────────────────────────────────────────────────────────────────────────────────┘
                            │
              微信开放接口：code2session / getAccessToken / getuserphonenumber / subscribeMessage.send
```

依赖方向（强制）：

- `routes` → `services` → `repos` / `domain`。**反向依赖不存在**：`domain` 不知道 HTTP、不知道 SQL、不知道微信。
- `services` 之间**只能通过方法调用**交互，不允许跨 service 直接写对方的表（例：`booking.cancel()` 调用 `waitlist.onSlotReleased()`，而不是自己插 `waitlist_release`）。
- 事务边界在 `services` 层，一个 service 方法 = 一个事务（`withTx()` 包装）。`repos` 只收 `tx` 参数，不做事务控制。
- 事件埋点通过 `domain/events.ts` 定义常量，禁止散落的魔法字符串（PRD §9 的事件名要做指标 SQL）。

### 2.3 运行时与部署

```
云主机（2C4G 起步）或容器平台
 ├── nginx（443）
 ├── node app（API + worker 同进程，PID 1 由 systemd/docker 管理）
 └── postgres（同机或托管实例；同机则 data 目录独立磁盘 + 每日 pg_dump 到对象存储）
环境变量：DATABASE_URL / JWT_SECRET / WX_APPID / WX_SECRET / WX_TPL_* / TZ=Asia/Shanghai / LOG_LEVEL
```
- 密钥只从环境变量读，`.env` 加入 `.gitignore`，仓库里只有 `.env.example`（占位值）。
- 时区：DB 用 `timestamptz`（存 UTC 瞬时），应用 `TZ=Asia/Shanghai` 只影响"自然周/日期"的分组统计。上海无夏令时，不存在 DST 歧义。

---

## 3. 技术选型

| 选项 | 选择 | 理由 | 放弃的备选（及原因） |
|---|---|---|---|
| 后端语言/框架 | **Node 20 + TypeScript + Fastify** | 与小程序端同语言（TS），一个人维护两种上下文成本最低；Fastify 轻、插件少、吞吐够 | NestJS（DI/装饰器样板对本项目是纯负担）；Java/Spring（部署与启动成本高）；Python/FastAPI（可行，但失去与前端共享类型） |
| 数据访问 | **`pg` 驱动 + 手写 SQL + `node-pg-migrate` 迁移** | 本项目的核心难点全在 SQL 语义上：原子 UPDATE 比较-设置、`FOR UPDATE`、`SKIP LOCKED`、部分唯一索引、CHECK 约束、含 `CASE WHEN EXISTS` 的批量判定。ORM 在这些点上都要写 raw SQL 绕过去，收益只剩 CRUD 样板 | Prisma（不支持部分唯一索引/`FOR UPDATE` 的原生表达，会变成"一半 schema 一半 raw SQL"）；TypeORM（同上且类型更弱）；Drizzle（可行，但迁移+组合查询的学习成本换不来收益） |
| 数据库 | **PostgreSQL 16** | 需要部分唯一索引（`UNIQUE ... WHERE status IN (...)`）、`CHECK` 约束、`SELECT ... FOR UPDATE`、`timestamptz`、`jsonb`（变更日志）；这些都是本方案的关键机制 | MySQL 8（**不支持部分唯一索引**，需用生成列模拟，会让人误以为有约束）；MongoDB（名额计数需要事务+条件更新，文档库做这个更绕）；SQLite（单写锁 + 备份/并发能力不足） |
| 缓存/分布式锁 | **不引入（无 Redis）** | 锁的粒度和数据都在同一个 PG 行上，行锁就是最正确的锁；99% 的读（课表）量级不需要缓存 | Redis 分布式锁（引入双写不一致风险，且本场景不需要跨进程锁）；Redis 缓存课表（数据量小、查询已走索引，缓存反而制造"名额显示不一致"） |
| 异步/队列 | **`notification_outbox` 表 + 轮询 worker**；递补超时用定时扫描 | 与业务同事务写入，通知**不会丢**；不引入额外中间件；"5 秒内通知"用一个 2 秒轮询就满足 | RabbitMQ/Kafka/BullMQ（对一个日请求 6000 的系统是纯运维成本）；Redis 队列（同上）；PG LISTEN/NOTIFY（跨进程通知有效但无持久化，outbox 已覆盖持久性，故不做） |
| 定时任务 | **同进程内 `setInterval` 循环 + 任务锁（`pg_advisory_lock`）** | 只有 4 个任务；任务本身要求幂等 + 可重复执行（§6.7） | Quartz/独立 cron 容器（多一个部署单元）；pg_cron（可行，但 SQL 里写业务分支不如在 TS 里可测） |
| 小程序端 | **原生微信小程序 + TypeScript**（`miniprogram-api-typings`），不引跨端框架 | 只做微信一个端，跨端框架的收益为 0，成本是构建链、体积、调试复杂度 | Taro/uni-app（多端收益为零）；引入第三方 UI 库（WeUI 官方组件够用） |
| 鉴权 | **微信 `code2session` → 服务端签发 JWT（HS256，7 天，`Authorization: Bearer`）** | 小程序无 Cookie；7 天后静默重登（`wx.login` 不需要用户操作，无感） | Session + 服务端存储（多一张表多一次查询，收益是能强制下线；本项目不需要）；微信 openid 直传（可伪造，不可接受） |
| 观测 | **pino JSON 日志 + `request_id` + `event_log` 业务事件表** | 出问题能按 `request_id` 串起一次请求；指标能从 `event_log` 用 SQL 算出来 | 引入 APM/OpenTelemetry（一个人维护，过度）；只写日志不落库（PRD §9 要求 4 周后能做 SQL 分析） |
| 密码学/手机号 | 手机号明文存 `member.phone`，DB 只授权 app 账号读写，日志脱敏；教练端 API 不返回该字段 | 需求只要"与门店录入手机号比对"，加密列会让匹配逻辑复杂（需要 pgcrypto + 索引策略） | 手机号 Hash 匹配（支持"授权手机号匹配"但丢失可读性，运营侧需要看；且哈希后不可逆，申诉场景反而麻烦）。若法务（PRD Q6）要求加密，改为 `pgcrypto` 加密列 + `phone_hash` 唯一索引匹配，改动范围仅 `repos/member` |
| 依赖清单（后端） | `fastify`、`zod`、`pg`、`pino`、`jsonwebtoken`、`node-pg-migrate` | 每个都必需且成熟 | — |

> 依赖原则：新增任何依赖前问一句"标准库/已有依赖能不能做"。例如 HTTP 客户端用 Node 内置 `fetch`（20 版已稳定），不装 axios。

---

## 4. 数据模型

**规模：本节共 19 张表**（§4.1 基础实体 7 张、§4.2 核心业务实体 7 张、§4.3 配置/日志/埋点 5 张），**3 个部分唯一索引**（`booking`、`waitlist_entry`、`member.phone`），**1 个防超卖 CHECK 约束**。

约定：
- 所有表含 `created_at timestamptz NOT NULL DEFAULT now()`、`updated_at timestamptz NOT NULL DEFAULT now()`。
- 主键统一 `bigserial`；时间统一 `timestamptz`；金额本期无。
- 枚举用 `text + CHECK`（而不是 PG `enum` 类型）：改枚举值只需改约束，不需要 `ALTER TYPE` 的额外锁与顺序问题，且迁移可回滚。
- 软删除一律用 `status` 字段，不做物理删除（历史与审计需要）。
- 命名与 PRD §10 保持一致；PRD 未列但本方案需要的字段在"备注"里标出。

### 4.1 基础实体

**store 门店**
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| name | text | NOT NULL |
| address | text | |
| lat, lng | numeric(9,6) | |
| phone | text | 门店联系电话 |
| business_hours | jsonb | |
| timezone | text | DEFAULT 'Asia/Shanghai' |
| status | text | CHECK in ('active','inactive') DEFAULT 'active' |
索引：`(status)`。

**room 教室**
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigserial | PK |
| store_id | bigint | NOT NULL REFERENCES store |
| name | text | NOT NULL |
| capacity | int | NOT NULL CHECK (capacity > 0) |
| status | text | CHECK in ('active','inactive') |
索引：`(store_id)`。

**course_template 课程模板**
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigserial | PK |
| name | text | NOT NULL |
| category | text | |
| duration_minutes | int | CHECK (duration_minutes BETWEEN 15 AND 240) |
| description | text | |
| cover_url | text | |
| difficulty | text | CHECK in ('beginner','intermediate','advanced') |
| equipment | jsonb | |
| status | text | CHECK in ('active','inactive') |
索引：`(status)`。

**coach 教练 / coach_store 关系**
`coach(id, name, avatar_url, title, bio, phone, status)`；`coach_store(coach_id, store_id)` 唯一 `(coach_id, store_id)`。
`coach.phone` **只用于运营端展示与绑定，教练端/会员端接口一律不返回**。

**member 会员**
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigserial | PK |
| openid | text | NOT NULL UNIQUE |
| unionid | text | NULL |
| nickname, avatar_url | text | |
| phone | text | NULL（授权手机号后写入） |
| home_store_id | bigint | NULL REFERENCES store |
| bind_status | text | CHECK in ('unbound','bound') DEFAULT 'unbound' |
| status | text | CHECK in ('active','disabled') |
索引：**部分唯一索引** `UNIQUE (phone) WHERE phone IS NOT NULL`（绑定必须唯一命中；S1 边界"同一手机号被两个 openid 绑定"的最后防线，冲突码 23505 → `PHONE_ALREADY_BOUND`）、`(bind_status)`。

**member_store_credit 会员-门店信用**
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| member_id, store_id | bigint | NOT NULL, UNIQUE (member_id, store_id) |
| banned_until | timestamptz | NULL，**权威字段** |
| ban_reason | text | CHECK in ('no_show_3_in_30d') NULL |
| no_show_count_30d_cache | int | **展示缓存**（每 1 小时任务刷新，判定不使用），对应 PRD §10.1 的 `no_show_count_30d` |
| late_cancel_count_7d_cache | int | 展示缓存（仅监控，PRD §6.5 未启用惩罚时无业务效果） |
| last_no_show_at | timestamptz | NULL |
| last_recomputed_at | timestamptz | 缓存刷新时间，运营列表据它显示"数据更新时间" |
> **口径说明（关键）**：PRD §10.1 把 `no_show_count_30d` 写成表字段，但 R5 要求"唯一口径为查询时实时重算"。本方案保留该列但**改名为 `_cache` 后缀**，明确它只是展示缓存；**任何惩罚判定都必须调 `credit.computeNoShowCount()`**。见 §9-Q5。

### 4.2 核心业务实体

**class_session 团课场次**
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| store_id, room_id, course_template_id, coach_id | bigint | NOT NULL |
| start_at, end_at | timestamptz | NOT NULL, CHECK (start_at < end_at) |
| capacity | int | NOT NULL CHECK (capacity >= 0) |
| occupied_count | int | NOT NULL DEFAULT 0 CHECK (occupied_count >= 0) |
| reserved_count | int | NOT NULL DEFAULT 0 CHECK (reserved_count >= 0)（**本方案新增**，对应 PRD R1） |
| waitlist_count | int | NOT NULL DEFAULT 0 CHECK (waitlist_count >= 0)（**展示缓存，不参与上限判定**：上限一律按实时记录数判（§6.3）；每条 entry 离开 `{waiting,notified}` 时同事务 −1；每日对账自愈） |
| status | text | CHECK in ('scheduled','ongoing','finished','canceled')（PRD §10.2 的 `canceled_by_store` 与 `canceled` 语义重复，合并为一个值 + `cancel_reason`，见 §9-Q6） |
| cancel_deadline_minutes, waitlist_confirm_minutes, waitlist_capacity, booking_deadline_minutes | int | NULL = 场次级覆盖，NULL 时取门店规则 |
| created_by | bigint | 运营/教练 id |
| canceled_at, cancel_reason | timestamptz, text | |
| **CHECK 约束（防超卖最后防线）** | | `CHECK (occupied_count + reserved_count <= capacity)` |

索引：
- `UNIQUE (room_id, start_at)`（防同教室同时段重复排课，PRD §10.2 建议）
- `UNIQUE (coach_id, start_at)`（**本方案新增**：防同一教练被排到两个同时开始的场次）
- `(store_id, start_at)`（课表查询）
- `(status, end_at)`（结算扫描/递补开课关闭扫描）

**booking 预约**
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| session_id, member_id | bigint | NOT NULL |
| status | text | CHECK in ('booked','attended','no_show','excused','pending_review','canceled','late_canceled','canceled_by_store') — **比 PRD §10.2 多一个 `pending_review`**（PRD S8/S9/R5 用到但 §10.2 漏列，见 §9-Q1） |
| source | text | CHECK in ('normal','waitlist') DEFAULT 'normal' |
| booked_at | timestamptz | NOT NULL DEFAULT now() |
| canceled_at | timestamptz | NULL |
| cancel_type | text | CHECK in ('ontime','late','store') NULL |
| minutes_before_start | int | NULL（取消时快照，用于 PRD §9 阈值校准） |
| checked_in_at | timestamptz | NULL（冗余自 `check_in`，便于列表展示） |
| no_show_marked_at | timestamptz | NULL |
| waitlist_entry_id | bigint | NULL（`source='waitlist'` 时必填，CHECK 保证） |
| client_request_id | text | NULL（客户端幂等/追踪用，**不作为正确性依赖**，见 §9-Q2） |

索引（关键）：
- **部分唯一索引**：`UNIQUE (session_id, member_id) WHERE status IN ('booked','attended','no_show','excused','pending_review')`
  → 语义 = "同一会员对同一场次最多 1 条**占位中/已结算**记录"。取消类状态（`canceled`/`late_canceled`/`canceled_by_store`）不在索引内，因此 S3 要求的"取消后允许新建一条 booking"天然成立。
- `(member_id, status)`（我的预约 + 时间冲突检测 + 爽约重算的驱动索引）
- `(session_id, status)`（场次名单、递补取队首前的占用校验）
- `(session_id, member_id)`（普通查询）
- `(status, no_show_marked_at)`（结算与缓存刷新扫描）

**waitlist_entry 候补记录**
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| session_id, member_id | bigint | NOT NULL |
| position | int | NOT NULL（入队时 `= max(position)+1`，只增不改） |
| status | text | CHECK in ('waiting','notified','confirmed','expired','declined','canceled','canceled_auto') |
| joined_at | timestamptz | NOT NULL DEFAULT now() |
| notified_at, confirm_deadline_at, responded_at | timestamptz | NULL |
| notified_count | int | NOT NULL DEFAULT 0（**本方案新增**：该 entry 被通知过几次，仅用于监控/展示；"连续 3 次"的权威计数在 `waitlist_release.try_count`，见 §9-Q4） |
| resulting_booking_id | bigint | NULL（确认成功后落地的那条 booking） |

索引（关键）：
- `UNIQUE (session_id, position)`
- **部分唯一索引**：`UNIQUE (session_id, member_id) WHERE status IN ('waiting','notified')`
  → PRD §10.2 写的是普通唯一 `(session_id, member_id)`，但那会阻断 R2 允许的"退出候补后重新排队"（新记录）。改为部分唯一索引，语义 = "同时最多 1 条进行中的候补"。见 §9-Q3。
- `(session_id, status, position)`（取队首 + rank 计算 + 上限统计）

**waitlist_release 释放事件（名额锁定）** — PRD R1/R3/S7 要求的实体，PRD §10.2 未列出表结构，本方案补全
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| session_id | bigint | NOT NULL |
| status | text | CHECK in ('open','filled','declined','expired','abandoned') |
| try_count | int | NOT NULL DEFAULT 0 CHECK (try_count >= 0)（**同一释放事件内的已通知次数，上限 3**） |
| current_entry_id | bigint | NULL（当前通知的是哪条候补记录） |
| origin | text | CHECK in ('cancel','late_cancel','capacity_add','manual_promote')（PRD 无"场次重开"动作，故不含 `session_reopen`；运营手动指派用 `manual_promote`，见 ㉘） |
| origin_booking_id | bigint | NULL（因谁取消而释放，用于"临期空位"来源展示） |
| opened_at | timestamptz | NOT NULL DEFAULT now() |
| closed_at | timestamptz | NULL |
| closed_reason | text | NULL |

索引：
- `(session_id, status)`（`reserved_count` 对账、场次详情展示）
- `(status, opened_at)`（超时扫描 `status='open'`）
- `(current_entry_id)`

**check_in 点名记录**
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| session_id, booking_id, member_id | bigint | NOT NULL |
| result | text | CHECK in ('present','absent') |
| marked_by | bigint | NOT NULL |
| marked_by_role | text | CHECK in ('coach','staff','system') |
| marked_at | timestamptz | NOT NULL DEFAULT now() |
| change_log | jsonb | NOT NULL DEFAULT '[]'（每次修改追加 `{by, role, from, to, at}`） |
索引：`UNIQUE (booking_id)`（一次预约一条最终记录，修改走 UPDATE + 追加 change_log）、`(session_id)`、`(member_id, marked_at)`。

**user_role 管理端身份**（教练/运营）
`id, wechat_openid, phone, role CHECK in ('coach','store_staff','store_manager','admin'), store_id, coach_id NULL, status`
索引：`UNIQUE (wechat_openid)`、`(store_id, role)`。

**staff_invite 教练/运营身份邀请码**（**本方案新增**，支撑 S8「以手机号 + 门店邀请/后台绑定教练身份」；对应接口 ㉗）
| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | bigserial | PK |
| code | text | NOT NULL UNIQUE（一次性邀请码，8 位随机 + 校验位） |
| phone | text | NOT NULL（**只允许这个手机号使用**，防止邀请码在群里转发） |
| role | text | CHECK in ('coach','store_staff','store_manager') |
| store_id, coach_id | bigint | NOT NULL / NULL（role='coach' 时必填） |
| expires_at | timestamptz | NOT NULL（默认 7 天） |
| used_by_openid | text | NULL |
| used_at | timestamptz | NULL |
| created_by | bigint | 运营的 user_role.id |
| status | text | CHECK in ('active','used','revoked','expired') DEFAULT 'active' |

索引：`(phone, status)`。
- 流程：运营在后台录入教练手机号 → 生成邀请码并当面/微信发给教练 → 教练在小程序内授权手机号 + 填邀请码（㉗）→ 写 `user_role`、邀请码置 `used`。运营可 `revoked` 后重新生成。
- 若运营侧暂时只能手工改库，本表的语义等价于"预置一行 `code` + `phone`"，不影响接口。

### 4.3 配置、日志与埋点

**store_rule 门店规则**（PRD §10.3）
`store_id PK`、`cancel_deadline_minutes`(120)、`waitlist_confirm_minutes`(15)、`waitlist_capacity_ratio`(1.0)、`no_show_threshold_30d`(3)、`no_show_ban_days`(7)、`booking_deadline_minutes`(0)、`enable_ban`(false→灰度期 true)、`enable_waitlist`(true)、`enable_late_cancel`(true)、`updated_by`、`updated_at`。
- 每个参数都带 CHECK 范围（与 PRD S10 一致：`cancel_deadline_minutes ∈ [0,1440]`、`no_show_threshold_30d ∈ [2,5]` 等），**越界由应用层拒绝并给出人话提示，DB 约束作为最后防线**。
- 读取：应用内缓存 30 秒（`Map<storeId, {rule, expiresAt}>`），变更时主动失效 → 满足 PRD "1 分钟内会员端文案更新"。

**subscription_message_log 通知日志**（PRD §10.3）
`id, member_id, template_id, biz_type, biz_id, status CHECK in ('success','fail','unauthorized'), error_code, sent_at, attempt_count`
索引：`(member_id, sent_at)`、`(biz_type, biz_id)`。

**notification_outbox 通知待发队列**（本方案新增，实现"事务内决定、事务外发送"）
`id, member_id, template_id, biz_type, biz_id, payload jsonb, status CHECK in ('pending','sent','failed','gave_up'), attempt_count, next_attempt_at, last_error, created_at, sent_at`
索引：`(status, next_attempt_at)`（worker 拉取）。
- 与业务同事务 INSERT，保证"业务成功 ⇒ 通知一定会尝试发送"。
- 重试策略：立即、+10s、+60s、+5min（共 4 次），仍失败置 `failed` 并走 PRD 降级（站内红点 + 确认时限延长）。
- **发送成功 ≠ 用户看到**（一次性订阅可能被用户拒收：errcode 43101 → 记 `unauthorized`，同样走降级）。

**audit_log 审计日志**（PRD §10.3）
`id, operator_id, operator_role, store_id, action, target_type, target_id, before jsonb, after jsonb, reason, created_at`
`action` 取值严格按 PRD：`update_rule|cancel_session|add_capacity|manual_waitlist|revoke_no_show|release_ban|resolve_unmarked`。
索引：`(store_id, created_at DESC)`、`(target_type, target_id)`。

**event_log 埋点事件**（PRD §9）
`id, event text, member_id, session_id, store_id, server_time timestamptz, props jsonb`
索引：`(event, server_time)`、`(member_id, server_time)`、`(session_id, event)`。
- 事件名与属性严格按 PRD §9 表格，常量集中在 `domain/events.ts`（清单以 PRD §9 为准，不另立重复清单）。
- 写入走"best effort"：埋点失败只记日志，**绝不影响主事务**（try/catch 包裹，或与主事务分离的追加写）。取舍：可能丢个别埋点，换主链路稳定 —— 指标统计允许小误差。

### 4.4 状态机（实现口径）

**booking**
```
                ┌──────────────► canceled ─────────► (终止)
                │                late_canceled ─────► (终止)
booked ─────────┼────────────────► canceled_by_store ► (终止)
  │             │
  │  end_at 后结算
  ├── 有 check_in.result=present ──► attended ─(运营撤销/补记可改)─┐
  ├── 有 check_in.result=absent  ──► no_show ─(运营撤销)──────────┤
  └── 无 check_in 记录 / 该预约未被标记 ──► pending_review ─(运营处理)─┘
                                                      attended / no_show / excused
```
- `booked → no_show` 时**只写状态与 `no_show_marked_at`，不做任何计数**（PRD R5）。
- 撤销爽约只改 `booking.status`，随后触发**重算**，绝不"计数减 1"。

**waitlist_entry**
```
waiting ──(取队首，创建/复用释放事件)──► notified ──(确认)──► confirmed ──► 生成 booking(source=waitlist)
   │                                        ├──(放弃)──► declined
   │                                        ├──(超时/冲突)──► expired
   │                                        └──(场次取消/开课)──► expired（或 canceled_auto）
   └──(会员退出)──► canceled
场次被门店取消：waiting/notified ──► canceled_auto
```

**waitlist_release**
```
open ──(有人确认)──► filled
     ├──(下一位放弃且无下一顺位/上限达 3/开课)──► abandoned  （名额恢复空闲：reserved_count -1）
     ├──(开课/场次取消)──► expired                          （名额作废：reserved_count -1）
     └──(该名额已由运营手动分配)──► filled（记 audit_log）
```

**class_session**：`scheduled → ongoing（start_at 到点）→ finished（end_at 到点，由结算任务推进）`；任意点可 `canceled`（门店取消整场，PRD S5 边界）。

### 4.5 不落表的派生值（避免"存储计数器"的坑）

| 派生值 | 计算方式 | 用途 |
|---|---|---|
| 会员可见剩余名额 | `max(0, capacity - occupied_count - reserved_count)` | 课表/详情/预约，**必须共用 `domain/rules.remainingSlots()`** |
| 候补实时位次 rank | `count(waitlist_entry WHERE session_id=? AND status IN ('waiting','notified') AND position <= myPosition)` | S2/S4/S6 三处共用 `domain/rules` + 同一条 SQL |
| 当前队列人数（**上限判定用**） | `count(waitlist_entry WHERE session_id=? AND status IN ('waiting','notified'))` | S6 上限判定（R2）+ S2/S6 展示；**与 rank SQL 同源，不用 `waitlist_count` 缓存列** |
| 场次生效候补上限 | `coalesce(session.waitlist_capacity, ceil(capacity × rule.waitlist_capacity_ratio))` | S6 上限判定（R2，ratio 默认 1.0） |
| `no_show_count_30d` | `count(booking JOIN class_session WHERE member_id=? AND store_id=? AND booking.status='no_show' AND session.end_at ∈ [now-30d, now))` | 黑名单判定（权威口径） |
| 课时利用率 / 爽约率 / 递补成功率 / 确认率 / 点名完成率 | 见 PRD §2 口径，写成运维手册里的 5 条 SQL | 上线 4 周后的复盘 |

---

## 5. 接口设计

### 5.1 通用约定

- 前缀 `/api`，JSON 编解码，时间一律返回 ISO 8601 带时区（`2025-03-01T19:00:00+08:00`），**服务器时间**为准（响应里带 `serverTime`，客户端倒计时用它校准本地时钟偏差）。
- 鉴权：`Authorization: Bearer <JWT>`。JWT payload：`{ sub: memberId, roles: ['member','coach'|'staff'|'manager'|'admin'], coachId?, storeIds: [...], iat, exp }`。**多角色**：同一 openid 既可能是会员也是教练，`roles` 是数组（见 ADR-10）。
- 成功：HTTP 2xx + `{ "data": ... }`。失败：HTTP 4xx/5xx + `{ "code": "...", "message": "给人看的中文", "details": {...} }`。
- 所有入参用 zod 校验：类型、范围、长度、枚举；**未知字段一律拒绝**（`strict()`），避免客户端多传字段造成隐性行为。
- 幂等：写接口都接受 `clientRequestId`（字符串，仅用于日志追踪与埋点关联）；**正确性不依赖它**，而依赖唯一索引 + 状态机条件更新（§6.2）。
- 限流：Nginx `limit_req`（每 IP 20 r/s）+ 应用内每会员动作限制（写操作 1 次/秒，超出返回 `429 RATE_LIMITED`）。
- **越权防护**：会员端所有查询/写入都强制带 `member_id = jwt.sub` 条件；管理端校验 `user_role` 与该 `store_id` 的归属，且教练只能操作 `session.coach_id = 自己` 的场次。
- **角色化导航（对 design §9-2 的回复）**：**采用 `custom-tab-bar`**，按 `roles` 渲染 2～3 个 tab（会员 3 个：课表/我的预约/我的；教练 2 个：我今天的课/我的；运营 = 会员 tabBar + 「我的」内多出「门店管理」分组 D1/D2）。同一 openid 同时是教练与会员时，C1 顶部的「以会员身份约课」只切视图、不重签 token（token 已含多角色）；只有「绑定教练身份」（㉗）时才重签一次。代价：tab 数量变化需自行重算安全区高度、自行维护选中态（原生 tabBar 的自动高亮失效），详见 ADR-10。
- **默认门店回退链**（保证未绑定会员也能浏览课表，S1/S2）：`storeId` 缺省时按 `member.home_store_id` → 唯一 `status='active'` 的门店 → 兜底取门店列表第一家 active 门店；三处都为空的极端情况返回 `404 STORE_NOT_FOUND`。
- **接口编号 ①～㉘ 只用于交叉引用**，不表示调用顺序，也不表示"新增的都排在最后"。

### 5.2 错误码总表

| code | HTTP | 含义 | 客户端建议处理 |
|---|---|---|---|
| `AUTH_REQUIRED` | 401 | 未登录/Token 过期 | 静默重登（`wx.login`）后重试一次 |
| `AUTH_FORBIDDEN` | 403 | 角色/门店不匹配 | 提示"无权限" |
| `MEMBER_UNBOUND` | 403 | 会员未绑定门店会员身份（S1） | 引导绑定手机号 |
| `MEMBER_BANNED` | 403 | 该门店黑名单期内（S9） | 展示 `details.bannedUntil` + 联系门店 |
| `STORE_INACTIVE` | 403 | 门店停用 | 提示"该门店暂不可约课" |
| `INVALID_PARAM` | 400 | 参数校验失败 | 提示 `details.field` |
| `SESSION_NOT_FOUND` | 404 | 场次不存在 | 刷新课表 |
| `SESSION_STARTED` | 409 | 已开课，不可预约/候补/取消 | 置灰按钮，刷新 |
| `SESSION_CANCELED` | 409 | 场次被门店取消 | 刷新课表 |
| `SESSION_FULL` | 409 | 剩余名额为 0（含被锁定名额） | 展示"名额刚被约满，要加入候补吗？"（`details.canJoinWaitlist`） |
| `DUPLICATE_BOOKING` | 409 | 已持有该场次有效预约 | 按钮变"已约" |
| `TIME_CONFLICT` | 409 | 与已有预约/已成功候补时间重叠 | 展示 `details.conflictWith`（课程名 + 时间段） |
| `BOOKING_NOT_FOUND` | 404 | 预约不存在或不属于当前会员 | 刷新"我的预约" |
| `CANCEL_WINDOW_PASSED` | 409 | 已过取消截止，只能请假（S5/R4） | 切换按钮为"我要请假"并展示后果文案 |
| `BOOKING_STATE_CONFLICT` | 409 | 状态已变化（已被取消/已结算/名额已递补给他人） | 提示"名额状态已变化，请刷新"并刷新 |
| `WAITLIST_FULL` | 409 | 候补队列已达上限（R2） | 提示"候补已满，可在开课前再来看看" |
| `WAITLIST_CLOSED` | 409 | 已开课，候补关闭 | 置灰 |
| `WAITLIST_DUPLICATE` | 409 | 已在该场次候补中 | 展示 `details.rank` |
| `WAITLIST_ENTRY_NOT_FOUND` | 404 | 候补记录不存在/已失效 | 刷新"我的预约" |
| `WAITLIST_ENTRY_STATE_CONFLICT` | 409 | 递补名额已被关闭或已被占用（S7 边界） | 提示"名额已被占用，本次候补未成功"并刷新 |
| `ROSTER_WINDOW_CLOSED` | 409 | 点名窗口未开/已关（S8） | 只读态 |
| `ROSTER_FORBIDDEN` | 403 | 该场次不属于当前教练/门店 | 提示无权限 |
| `CAPACITY_CONFLICT` | 409 | 下调容量后 `occupied + reserved > capacity`（R1） | 提示需先取消预约或等递补结果 |
| `RULE_INVALID` | 400 | 规则参数越界 | 展示具体取值范围 |
| `RATE_LIMITED` | 429 | 操作过于频繁 | 按钮临时禁用 |
| `PHONE_ALREADY_BOUND` | 409 | 该手机号已绑定其他 openid（S1 边界） | 提示"该手机号已被绑定，请联系前台" |
| `STORE_NOT_FOUND` | 404 | 门店不存在，且默认门店回退链也为空 | 引导选择其他门店（`GET /api/stores`） |
| `INVITE_CODE_INVALID` | 403 | 邀请码无效/已使用/已过期/手机号不匹配（S8/㉗） | 提示"邀请码无效，请联系门店管理" |
| `ROLE_ALREADY_BOUND` | 409 | 该微信已绑定其他教练/门店身份（㉗） | 提示"该微信已绑定其他门店身份，请联系管理解绑" |
| `INTERNAL_ERROR` | 500 | 未预期错误（**服务端已记 `request_id`，响应里返回该 id**） | 提示"服务异常" + 重试按钮 |

> 共 **30 条**错误码。

> 约定：所有 409 都表示"请求合法但当前状态不允许"，客户端**必须**刷新对应数据，不能只弹提示。

### 5.3 会员端接口

**① `POST /api/auth/login`** — 静默登录/绑定状态查询（S1）
```jsonc
// req
{ "code": "wx.login 返回的 code" }
// data
{ "token": "jwt", "expiresIn": 604800, "serverTime": "...",
  "member": { "id": 12, "nickname": "小王", "avatarUrl": "...", "bindStatus": "unbound", "homeStoreId": 1 },
  "roles": ["member"],          // 决定渲染哪套 tabBar（design §2.2）；至少含 'member'，可为 ['member','coach']
  "coachId": null,              // roles 含 'coach' 时非空；教练端接口的鉴权依据
  "storeIds": [1],              // 该用户可见/可操作的门店：会员取 home_store_id，教练/运营取 user_role.store_id
  "defaultStoreId": 1,          // 客户端首屏调课表用它（= homeStoreId ?? 唯一 active 门店）
  "myWaitlistRank": null }
```

**② `POST /api/auth/bind-phone`** — 手机号授权绑定（S1）
```jsonc
// req
{ "phoneCode": "getPhoneNumber 的 code" }
// data 成功
{ "bindStatus": "bound", "member": { ... } }
// 失败
{ "code": "MEMBER_UNBOUND", "message": "该手机号未被门店录入，请联系前台绑定", "details": {} }
```
服务端流程：`code` → 微信 `getuserphonenumber` → 得手机号 → 匹配 `member.phone`：
- 匹配到且 `openid` 为空 → 写入 openid，`bind_status='bound'`；
- 匹配到但已绑定其他 openid → `409 PHONE_ALREADY_BOUND`（PRD S1 边界：转门店人工处理）；
- 未匹配 → `MEMBER_UNBOUND`。
- 并发/重复绑定由 `UNIQUE (phone) WHERE phone IS NOT NULL` 兜底（23505 → `PHONE_ALREADY_BOUND`）。
- **口径澄清**：`member` 是"人"（全局唯一，`openid` 唯一），门店归属由 `home_store_id` 与 `member_store_credit` 表达，所以手机号匹配只会命中一行。此假设已列入 §9-Q10 请产品确认。

**③ `GET /api/schedule`** — 课表查询（S2）
查询参数：`storeId`（**可选**；缺省按 §5.1 的默认门店回退链解析）、`dateFrom`、`dateTo`（默认今天~+6 天，最大跨度 31 天）、`category?`、`coachId?`
```jsonc
// data
{ "serverTime": "2025-03-01T18:00:00+08:00",
  "store": { "id": 1, "name": "望京店" },
  "rule": { "cancelDeadlineMinutes": 120, "waitlistConfirmMinutes": 15, "noShowThreshold30d": 3, "noShowBanDays": 7, "bookingDeadlineMinutes": 0, "waitlistEnabled": true, "lateCancelEnabled": true, "banEnabled": true },
  "lastUpdatedAt": "2025-03-01T17:58:00+08:00",   // 排课数据最后更新时间（PRD §7.1 要求展示）
  "sessions": [
    { "id": 501, "courseName": "燃脂搏击", "category": "cardio", "coverUrl": "...",
      "difficulty": "intermediate", "coach": { "name": "李教练", "avatarUrl": "..." },
      "roomName": "A 教室", "startAt": "...", "endAt": "...", "durationMinutes": 60,
      "capacity": 20, "occupiedCount": 15, "reservedCount": 2, "remaining": 3,
      "waitlistCount": 5, "waitlistCapacity": 20,
      "stateTag": "open",              // open | full | started | canceled
      "myState": "waitlist",           // none | booked | waitlist | notified
      "myWaitlistRank": 2,             // 实时 rank，非 position；myState 非 waitlist/notified 时为 null
      "myWaitlistConfirmDeadlineAt": null  // myState=notified 时为 confirm_deadline_at（B1"剩余 mm:ss"/B4 倒计时数据源）
    }
  ] }
```
- **"已约人数"口径**：`occupiedCount` 即 R1 的 `occupied_count`（含 `attended`）；`remaining = capacity - occupiedCount - reservedCount`（三处共用同一函数）。详情页 S2 要求的"已约人数"直接用它，客户端不再自己推算。
- `waitlistCount` / `waitlistCapacity` 是**实时值**（§6.3 ③ 的 count 与生效上限），不是 `class_session.waitlist_count` 缓存列。
- `remaining` 由 `domain/rules.remainingSlots(capacity, occupied_count, reserved_count)` 计算，**与详情页、预约接口共用**。
- 时间范围、门店状态过滤在 SQL 里做；空结果返回 `sessions: []`，由客户端渲染"这天还没有团课"。
- 不返回预约名单、不返回 `member.phone`。

**④ `GET /api/sessions/:id`** — 课程详情（S2）
```jsonc
// data
{ "session": { ...同课表项（含 occupiedCount / reservedCount / remaining / waitlistCount / waitlistCapacity）...,
    "description": "...", "equipment": ["拳套"],
    "coachBio": "...", "storeAddress": "...", "businessHours": "周一至周日 7:00-22:00" },
  "rule": { ... },
  "myBooking": { "id": 9001, "status": "booked", "cancelMode": "ontime" },  // cancelMode: ontime|late|none
  "myWaitlistEntry": { "id": 71, "status": "waiting", "rank": 2, "confirmDeadlineAt": null },
  "canBook": true, "canJoinWaitlist": false, "denyReason": null }           // denyReason: SESSION_STARTED|MEMBER_BANNED|MEMBER_UNBOUND|STORE_INACTIVE|WAITLIST_FULL...
```
`cancelMode` 由服务端算（`domain/rules.cancelMode(now, startAt, cancelDeadlineMinutes)`），**客户端不允许本地判断**——避免用户改手机时间绕过文案。

**⑤ `POST /api/sessions/:id/bookings`** — 预约（S3）
```jsonc
// req
{ "clientRequestId": "uuid-客户端生成" }
// data 成功
{ "booking": { "id": 9002, "status": "booked", "source": "normal", "bookedAt": "..." },
  "session": { "id": 501, "remaining": 2, "stateTag": "open" } }
```
服务端（**单事务**，伪代码见 §6.2）：校验登录/绑定/黑名单/门店/截止 → 会员行锁（时间冲突串行）→ 时间冲突检查 → 原子占位 → INSERT booking → 埋点。
错误：`SESSION_FULL`（并发输家，`details.canJoinWaitlist`）/ `DUPLICATE_BOOKING` / `TIME_CONFLICT` / `MEMBER_BANNED` / `SESSION_STARTED`。

**⑥ `GET /api/me/bookings`** — 我的预约（S4）
查询参数：`scope=active|history`（必填）、`page`、`pageSize`（默认 20，历史分页）
```jsonc
// data
{ "serverTime": "...",
  "items": [
    { "bookingId": 9002, "sessionId": 501, "courseName": "燃脂搏击",
      "startAt": "...", "endAt": "...", "coachName": "李教练", "roomName": "A 教室", "storeName": "望京店",
      "state": "booked",            // booked | pending_confirm | waitlist | attended | no_show | excused | canceled | late_canceled | canceled_by_store | pending_review | waitlist_failed
      "cancelMode": "ontime",       // 仅 active 有效
      "minutesUntilStart": 135,
      "waitlist": { "entryId": 71, "rank": 2, "confirmDeadlineAt": null }   // 仅候补态
    }
  ],
  "page": { "page": 1, "pageSize": 20, "hasMore": false } }
```
- `state = pending_review` 时客户端文案必须是"已结束 · 到场状态待门店确认"（**禁止**显示"已爽约"，PRD S9）。
- `state = waitlist_failed`（对应 entry 的 `expired/declined/canceled_auto`）显示"候补未成功"。

**⑦ `POST /api/bookings/:id/cancel`** — 取消 / 请假（S5）
```jsonc
// req
{ "clientRequestId": "uuid" }
// data（服务端按服务器时间判定，客户端不传意图）
{ "booking": { "id": 9002, "status": "late_canceled", "cancelType": "late", "canceledAt": "..." },
  "slotReleased": true, "promotionTriggered": true, "promotionSkippedReason": null }
```
- 服务端判定：`now ≤ start_at - cancel_deadline_minutes`（**闭区间**）→ `canceled` + `cancelType='ontime'`；否则若 `enable_late_cancel` 且 `now < start_at` → `late_canceled` + `cancelType='late'`；`now ≥ start_at` → `409 SESSION_STARTED`（PRD R4）。
- 幂等：`UPDATE booking SET status=... WHERE id=? AND member_id=? AND status='booked'`，`affected=0` 时若当前已是取消类状态 → 返回 200 与当前状态（**不重复释放名额**，PRD S5 边界）。
- 事务内联调用 `waitlist.onSlotReleased()`（§6.4），返回 `promotionSkippedReason`（如 `TOO_CLOSE_TO_START` / `NO_WAITLIST` / `WAITLIST_DISABLED`），客户端据此只展示"名额已释放"。

**⑧ `POST /api/sessions/:id/waitlist`** — 加入候补（S6）
```jsonc
// req { "clientRequestId": "uuid" }
// data
{ "entry": { "id": 71, "status": "waiting", "rank": 3, "joinedAt": "..." },
  "waitlistCount": 5, "waitlistCapacity": 20 }
```
校验顺序：登录/绑定/黑名单 → 场次未开课 → 未持有有效预约（`DUPLICATE_BOOKING`）→ 无进行中候补（`WAITLIST_DUPLICATE` + `details.rank`）→ **时间冲突**（R2：候补也受 R6 约束，`TIME_CONFLICT`）→ 锁场次行 + 实时队列人数上限校验（§6.3）→ 入队（`position = max+1`）。
- 响应里的 `waitlistCount` / `waitlistCapacity` 是**实时队列人数与生效上限**（§6.3 ③），不是缓存列。

**⑨ `DELETE /api/waitlist-entries/:id`** — 退出候补（S6/S5 边界）
```jsonc
// data { "entry": { "id": 71, "status": "canceled" } }
```
- `position` **不改**（R2）；不计处罚。
- 同事务把展示缓存 `waitlist_count` **−1**（§6.3 的维护规则），并按需修正实时队列人数。
- 若该 entry 正被某个 `open` 的释放事件持有（`status='notified'`）→ 同一事务内立即顺延给下一位（§6.4 `promoteNext()`）。

**⑩ `POST /api/waitlist-entries/:id/confirm`** — 确认递补（S7）
```jsonc
// data 成功
{ "booking": { "id": 9010, "status": "booked", "source": "waitlist" }, "session": { "remaining": 0 } }
// 失败
{ "code": "WAITLIST_ENTRY_STATE_CONFLICT", "message": "名额已被占用，本次候补未成功" }
```
- 事务内**按固定顺序**校验（任何一条失败都"关闭/顺延、绝不占名额"）：
  ① `now >= session.start_at` → release 置 `expired`、entry 置 `expired`、`reserved_count -1`，返回 `409 WAITLIST_CLOSED`（PRD S7"开课后候补关闭"）；
  ② `banned_until > now` → entry 置 `expired` 并 `promoteNext()` 顺延下一位，返回 `403 MEMBER_BANNED`（R5 惩罚范围含"不可递补确认"）；
  ③ 条件更新释放事件（`WHERE id=? AND status='open'`），`affected=0` → 立即关闭 entry（`expired`）并顺延；
  ④ 时间冲突复检（§9-Q7）。

**⑪ `POST /api/waitlist-entries/:id/decline`** — 放弃递补（S7）：同 ⑩ 的条件更新，`status='declined'`，不计爽约/晚取消，立即顺延（同事务 `waitlist_count -1`）。

**㉕ `GET /api/stores`** — 门店列表（S1/S2，**本轮新增**：未绑定会员也能进课表 + B1 门店切换的数据源）
```jsonc
// data
{ "stores": [ { "id": 1, "name": "望京店", "address": "建国路 88 号", "businessHours": "...", "status": "active" } ],
  "defaultStoreId": 1 }        // = member.home_store_id ?? 唯一 active 门店 ?? null
```
- **未登录也可调用**（此时 `defaultStoreId = null`，客户端取列表第一家 active 门店）；无 active 门店时返回 `stores: []` 并在 `emptyReason` 里说明。
- S1 的"未绑定会员可浏览课表但按钮置灰"就靠它拿到 `storeId`；`home_store_id` 为 NULL 的会员同样成立。

**㉖ `GET /api/me/credit?storeId=`** — 会员信用/黑名单状态（S9，**本轮新增**：B1 红条、按钮置灰与"近 30 天已 N 次"的数据源）
```jsonc
// data（storeId 缺省走默认门店回退链）
{ "storeId": 1,
  "noShowCount30d": 2,          // **实时重算**（§6.7 口径），不读 _cache 列
  "noShowThreshold30d": 3,
  "bannedUntil": null,          // 非空 → B1 红条"预约权限已暂停至 YYYY-MM-DD HH:MM"+ 按钮置灰 + 剩余解除时间
  "banReason": null,            // no_show_3_in_30d
  "banEnabled": true,           // 门店关闭黑名单惩罚时 false（客户端不显示红条、不置灰，PRD §8.3 降级开关）
  "storeContact": "010-88886666" }
```
- **页面加载时主动调用**（不依赖"用户点了按钮拿到 403"）：B1 顶部红条、B5 信用区、B3 历史"近 30 天在该店已 N 次"（design §5.1/§5.3/§6.5）都读它。
- `noShowCount30d` 必须走 §6.7 的实时重算，保证与 403 `MEMBER_BANNED` 的判定值一致（PRD R5）。
- 越权：只返回 `jwt.sub` 自己的数据。

**㉗ `POST /api/auth/coach-bind`** — 教练/运营身份绑定（S8，**本轮新增**：补上教练端（P0）唯一缺失的入口）
```jsonc
// req
{ "phoneCode": "getPhoneNumber 的 code", "inviteCode": "门店发给教练的一次性邀请码" }
// data
{ "roles": ["member","coach"], "coachId": 7, "storeIds": [1], "token": "重新签发的多角色 JWT（客户端直接替换）" }
```
- 服务端：`phoneCode` → 微信取手机号；校验 `staff_invite`（`code` 命中 + `status='active'` + `expires_at > now()` + `invite.phone = 手机号`）→ 写 `user_role(wechat_openid, role, store_id, coach_id)`、`staff_invite.status='used'`。
- 错误：邀请码任何一项不符 → `403 INVITE_CODE_INVALID`；该 openid 已绑其他教练/门店身份 → `409 ROLE_ALREADY_BOUND`（运营先解绑）。
- **前置依赖**（不阻塞开发，但阻塞验收）：门店需先在后台录入 `coach` 并生成邀请码，见 §8.2。

### 5.4 教练端接口

**⑫ `GET /api/coach/sessions?date=YYYY-MM-DD`** — 我今天的课（S8）
```jsonc
// data
{ "items": [ { "sessionId": 501, "courseName": "燃脂搏击", "startAt": "...", "endAt": "...",
    "roomName": "A 教室", "bookedCount": 18, "capacity": 20, "waitlistCount": 2,
    "checkinState": "not_open",     // not_open | open | closed
    "markedPresent": 0, "markedAbsent": 0, "unmarkedCount": 18 } ] }
```

**⑬ `GET /api/coach/sessions/:sessionId/roster`** — 点名名单（S8）
```jsonc
// data
{ "session": { ... },
  "window": { "checkinState": "open", "opensAt": "...", "closesAt": "..." },
  "roster": [ { "bookingId": 9002, "memberName": "王**", "avatarUrl": "...",
                "bookedAt": "...", "isLateBooking": false, "source": "waitlist",
                "result": null, "markedAt": null, "isSupplement": false } ],   // result: null|present|absent
  "readonly": false, "serverTime": "..." }
```
- **不返回手机号**（PRD R7）。会员名按昵称展示；如需脱敏，运营端开关控制。
- `isSupplement = true` 表示点名后新递补进来的会员（PRD S8 边界"补位加入"）。

**⑭ `PUT /api/coach/sessions/:sessionId/roster/:bookingId`** — 单条点名（S8）
```jsonc
// req { "result": "present" }   // present | absent
// data { "checkIn": { "bookingId": 9002, "result": "present", "markedAt": "..." } }
```
服务端校验（**顺序即安全边界**）：
1. 身份：`user_role.coach_id = session.coach_id`（教练）或 `user_role` 属于该场次门店且角色为 staff/manager（运营兜底）；
2. 窗口：`start_at - 30min ≤ now ≤ start_at + 15min`（服务器时间，PRD S8），否则 `ROSTER_WINDOW_CLOSED`；
3. 目标：`booking.session_id = :sessionId` 且 `booking.status = 'booked'`（结算发生在 `end_at`，而窗口在 `start_at+15min` 关闭，因此已结算的 `attended/no_show` 在窗口内**不可达**；运营事后改结论走 ㉑/㉒）；
4. 写入：`INSERT ... ON CONFLICT (booking_id) DO UPDATE`，同时把旧的 `result` 追加进 `change_log`（打错可撤回，PRD S8）。

**⑮ `POST /api/coach/sessions/:sessionId/roster/batch-mark`** — 批量（"一键全到"）
```jsonc
// req { "result": "present", "bookingIds": [9002, 9003] }   // 缺省 = 全部未标记
```
- 同一事务内逐条走 ⑭ 的校验；只要有一条越权/窗口/目标不合法 → **整批回滚**并返回具体 `bookingId`。
- 幂等：重复调用结果一致（`ON CONFLICT` + `change_log` 追加）。

**⑯ `POST /api/coach/sessions/:sessionId/roster/sync`** — 离线补交（P1，PRD S8 边界）
```jsonc
// req { "items": [ { "bookingId": 9002, "result": "present", "markedAt": "客户端记录的本地时间" } ] }
// 说明：markedAt 仅记录在 change_log，服务端仍用服务器时间校验窗口
```

### 5.5 运营端接口

**⑰ `GET /api/staff/stores/:storeId/rules`** / **⑱ `PUT /api/staff/stores/:storeId/rules`** — 规则配置（S10）
```jsonc
// PUT req（部分更新）
{ "cancelDeadlineMinutes": 120, "waitlistConfirmMinutes": 15, "waitlistCapacityRatio": 1.0,
  "noShowThreshold30d": 3, "noShowBanDays": 7, "bookingDeadlineMinutes": 0,
  "enableBan": true, "enableWaitlist": true, "enableLateCancel": true, "reason": "门店沟通结果" }
// data { "rule": { ...生效值... }, "warning": "该设置将导致所有预约都无法取消" }
```
- 越界 → `400 RULE_INVALID`；`cancel_deadline_minutes` > 7 天（即课程发布窗口）→ 200 + `warning` 并接受二次确认（由客户端二次提交 `confirmed: true`）。
- 写 `audit_log`（`before`/`after`/`reason`）；缓存立即失效。

**⑲ `GET /api/staff/sessions/:sessionId/roster`** — 场次名单（S11）
- 同 ⑬，但额外返回：会员手机号（**仅 staff/manager 角色**）、`cancel_type`、`no_show` 标记、`pending_review` 高亮、`lateCancelCount7d`、`noShowCount30d`（来自缓存并标注 `lastRecomputedAt`）。

**⑳ `POST /api/staff/sessions/:sessionId/capacity`** — 手动加容量（R1/S13）
```jsonc
// req { "delta": 2, "reason": "教练临时加椅子" }
// data { "session": { "capacity": 22, "occupiedCount": 20, "reservedCount": 1, "remaining": 1 },
//        "releasesCreated": 1, "vacantSlots": 1, "notified": 1, "skippedReason": null }
```
- `releasesCreated` **只统计真的创建了释放事件**的空位（有下一顺位可通知）；无队可递补的空位计入 `vacantSlots`，**不建 release、不加 `reserved_count`**（与 §6.4 步骤 4 一致，避免把空位锁死 30 秒）。
- `delta < 0`：**条件更新**（判断放进 `WHERE`，与 §6.2 原则一致；不做"先查再改"）：
  `UPDATE class_session SET capacity = capacity + $delta WHERE id = $1 AND capacity + $delta >= occupied_count + reserved_count` → 0 行 = `409 CAPACITY_CONFLICT`。同时把 SQLSTATE `23514`（CHECK 违反）也映射为 `CAPACITY_CONFLICT`，绝不让运营看到 500 `INTERNAL_ERROR`。
- `delta > 0`：`capacity` 更新后，为**每个新空位**尝试创建独立 `waitlist_release`（`try_count=0` → 通知队首时置 1），按 R2/R3 逐位串行通知；**没有下一顺位的空位不建 release、不加 `reserved_count`，保持空闲可约**（见 §6.4 步骤 4；示例里这类空位计入 `vacantSlots`）。
- 写 `audit_log`（`add_capacity`）。

**㉑ `POST /api/staff/sessions/:sessionId/unmarked-resolve`** — 处理「未点名异常」（S8/S9）
```jsonc
// req { "bookingIds": [9002], "result": "excused", "reason": "教练手机没电，会员实际到场" }  // attended|no_show|excused
// data { "resolved": [ { "bookingId": 9002, "status": "excused" } ], "banRecalculated": [ { "memberId": 12, "noShowCount30d": 1, "bannedUntil": null } ] }
```
- 只允许处理 `status = 'pending_review'` 的预约；整场批量 = 传 `allUnmarked: true`。
- 写 `audit_log`（`resolve_unmarked`），若产生 `no_show` → 触发该会员**重算**。

**㉒ `POST /api/staff/bookings/:bookingId/revoke-no-show`** — 撤销爽约（S9）
```jsonc
// req { "to": "attended", "reason": "会员提供了就医证明" }   // attended | excused
// data { "booking": { "id": 9002, "status": "attended" },
//        "noShowCount30d": 2, "bannedUntil": null, "banReleased": true }
```
- **只改 `booking.status`**，随后调 `credit.recompute()`；若重算值 < 阈值且 `banned_until > now` → 立即解除（`banned_until = null`）；写 `audit_log`（`revoke_no_show` / `release_ban`）。**严禁任何"计数减 1"语句**（PRD R5）。

**㉓ `POST /api/staff/sessions/:sessionId/cancel`** — 门店取消整场（S5 边界/S11）
```jsonc
// req { "reason": "教练临时请假" }
// data { "canceledBookings": 18, "canceledWaitlistEntries": 2, "notificationsQueued": 20, "releasesClosed": 1 }
```
- 同一事务：`class_session.status='canceled'`、所有 `booked` → `canceled_by_store`、所有 `waiting/notified` → `canceled_auto`、所有 `open` 的 `waitlist_release` → `expired`（`reserved_count = 0`）、批量写 outbox 通知；写 `audit_log`（`cancel_session`）。

**㉔ `GET /api/staff/metrics?storeId&from&to`**（P2，可先用 SQL 手工替代）— 返回 PRD §2 的 5 个指标值，口径与运维手册中的 SQL 完全一致。

### 5.6 其他
- `GET /healthz`：返回 `{ ok, db: true, lastJobRuns: { outbox: "...", promotion: "...", settlement: "..." } }`，供监控与回滚演练检查（任务"卡住"是最需要被立刻发现的问题）。
- 管理端不做完整后台（PRD §3），运营页也在小程序内（角色化视图）。

---

## 6. 关键难点与方案

### 6.1 难点总览与对策

| 难点 | 本质 | 方案 | 验证方式（开发期） |
|---|---|---|---|
| 并发抢位不超卖 | 计数与决策非原子 | 原子 UPDATE 比较-设置 + CHECK 约束 + 会员/场次行锁 | 并发测试：50 并发抢 5 名额，断言成功 5、`occupied=5`、无 CHECK 违反、无同会员双条 |
| 递补顺序与原子性 | 队列 + 名额 + 通知三方状态必须一致 | 一个事务内：建释放事件 → `reserved_count+1` → 取队首 `FOR UPDATE` → 置 `notified` → 写 outbox | 集成测试：取消 → 断言 release=open、reserved=1、队首 notified、outbox 有 1 条 |
| 取消后自动递补 | 事务内触发 + 事务外通知 | `booking.cancel()` 内联 `waitlist.onSlotReleased()`，通知走 outbox | 用例：3 秒内（worker 周期内）通知记录变 `sent` |
| 确认/放弃/超时的状态竞争 | 同一名额多入口竞争 | 释放事件的条件更新（`WHERE status='open'`）作为唯一裁决点 | 用例：确认 vs 直接预约并发，断言只有一方成功 |
| 签到防代签与时效 | 身份 + 时间 + 目标三重校验 | 身份绑定 + 排期归属 + 服务端窗口 + 变更日志 | 用例：非本场教练 403；窗口外 409；重复标记幂等且留 change_log |
| 通知送达与降级 | 一次性订阅 + 用户可能拒收 | outbox + 重试 + `43101`→`unauthorized` → 时限延长 + 站内红点 | mock 微信接口三分支测试 |
| 定时任务漏跑/重跑 | 无监控的定时任务不可靠 | 幂等 SQL + 每任务 30s 超时 + `lastJobRuns` 暴露到 `/healthz` | 用例：同一结算任务连跑 3 次，结果与跑 1 次相同 |

### 6.2 并发预约抢位与超卖防护

三道防线，缺一不可：

**第一道：原子 UPDATE（比较-设置在一条语句里）**
```sql
-- 占位：返回 1 行 = 成功；0 行 = 满员/已开课/容量被锁定占满
UPDATE class_session
   SET occupied_count = occupied_count + 1, updated_at = now()
 WHERE id = $1
   AND status = 'scheduled'
   AND start_at > now()
   AND occupied_count + reserved_count < capacity
RETURNING id, capacity, occupied_count, reserved_count;
```
为什么不用 `SELECT ... THEN UPDATE`：两个请求可能都读到"还有 1 个位置"，然后都加 1 → 超卖。把判断挪进 `WHERE`，让数据库的行锁替我们串行化，这是最便宜也最正确的方案。

**第二道：部分唯一索引兜底重复预约**
```sql
-- 唯一索引冲突（SQLSTATE 23505）→ 捕获后返回 DUPLICATE_BOOKING
INSERT INTO booking (session_id, member_id, status, source, booked_at, client_request_id)
VALUES ($1, $2, 'booked', 'normal', now(), $3);
```
双击/重放/网络重试都只能产生一条。**事务回滚会同时回退 `occupied_count`**（同一事务，天然一致）。

**第三道：数据库 CHECK 约束（最后防线）**
```sql
CHECK (occupied_count + reserved_count <= capacity)
```
任何未来新增的代码路径（运营手动加位、递补、批量操作）只要写错，PG 会直接报错，而不会静默超卖。**违反该约束时应用记 `critical` 级日志并触发告警**（这是数据一致性事故的信号，PRD §8.3 的回滚条件之一）。

**会员级串行化（时间冲突检查的正确性）**
```sql
-- 事务开始时先锁会员行：保证同一会员的"冲突检查 → 写入"是原子的
SELECT id FROM member WHERE id = $1 FOR UPDATE;
-- 再检查重叠（R6：[start_at, end_at) 半开区间）
SELECT b.id, s.course_template_id, s.start_at, s.end_at
  FROM booking b JOIN class_session s ON s.id = b.session_id
 WHERE b.member_id = $1
   AND b.status IN ('booked','attended','no_show','excused','pending_review')
   AND s.start_at < $newEnd AND s.end_at > $newStart
 LIMIT 1;
```
取舍：会员行锁让**同一会员**的并发预约串行（一个会员不可能同时点两个按钮产生有意义的结果），不同会员之间完全没有影响。这是"用一点点锁换掉一整类竞态"的划算交易。

**完整预约事务（伪代码）**
```
withTx:
  member = SELECT ... FROM member WHERE id=$me FOR UPDATE          -- 会员级串行
  if member.bind_status != 'bound' -> MEMBER_UNBOUND
  credit = SELECT banned_until FROM member_store_credit ...         -- 黑名单（含实时判定）
  if banned -> MEMBER_BANNED
  rule   = storeRule(storeId)  // 场次级覆盖优先：session 上的 NULL 字段回落到门店规则
  if store.status != 'active' -> STORE_INACTIVE
  if session.status = 'canceled' -> SESSION_CANCELED
  if now >= session.start_at - rule.bookingDeadlineMinutes -> SESSION_STARTED
  if conflict exists -> TIME_CONFLICT
  updated = UPDATE class_session SET occupied_count = occupied_count+1 WHERE ... (第一道)
  if !updated -> SESSION_FULL { canJoinWaitlist: true }
  INSERT booking ... (第二道，23505 -> DUPLICATE_BOOKING)
  INSERT event_log
```
`FOR UPDATE` 的读都在事务开头、写在同一批 SQL，**事务内不做任何网络调用**（不开微信接口、不发通知）——通知只写 outbox 表。

### 6.3 候补队列顺序与位次（对应 PRD R2）

- **入队**（同样在事务内、先锁会员行做冲突检查）：
```sql
-- 原子上限校验 + 取号（单条语句，避免"查完再插"的竞态）
UPDATE class_session
   SET waitlist_count = waitlist_count + 1
 WHERE id = $1 AND waitlist_count < $waitlistCapacity
RETURNING waitlist_count;                                    -- 0 行 -> WAITLIST_FULL
INSERT INTO waitlist_entry (session_id, member_id, position, status, joined_at)
VALUES ($1, $2,
        (SELECT COALESCE(MAX(position), 0) + 1 FROM waitlist_entry WHERE session_id = $1),
        'waiting', now());
```
> `position` 只在 INSERT 时计算一次；`waitlist_count` 与 INSERT 在同一事务，异常则一起回滚。因为 `position` 只增不改，`UNIQUE (session_id, position)` 恒成立（PRD S6 边界的"同一秒入队"问题因此消解）。
> `SELECT MAX(position)+1` 在并发下的正确性：同一事务已持 `class_session` 行锁（上面的 UPDATE），同场次入队天然串行，因此不会取到重复号。**这是刻意用场次行锁换掉"必须用序列/`ON CONFLICT` 重试"的复杂度。**

- **实时 rank**（唯一 SQL，课表/详情/我的预约/退出候补返回都用它）：
```sql
SELECT count(*) AS rank
  FROM waitlist_entry
 WHERE session_id = $1 AND status IN ('waiting','notified') AND position <= $myPosition;
```
- 任何 `waiting/notified` 记录状态变化（退出、超时、确认、放弃）都会让后面的人 rank 自动前移，**不修改任何 `position`**（PRD R2 要求）；这也保证了 `notified` 正在倒计时的会员其 `position` 不会被改动。

### 6.4 名额释放 → 锁定 → 递补（对应 PRD R3/S7）

**调用链**：`booking.cancel()` / `booking.leave()` / `staff.addCapacity()` / `waitlist.confirm()`（失败分支）→ `waitlist.onSlotReleased(sessionId, origin, bookingId)`。

**`onSlotReleased` 事务流程（一次只通知 1 人）**
```
1. session = SELECT ... FROM class_session WHERE id=$1 FOR UPDATE      -- 场次级串行
2. 门槛判断（R3）：if start_at - now < confirmMinutes + 5min
     -> 不创建释放事件；空位保持空闲可约；返回 skippedReason='TOO_CLOSE_TO_START'
3. if rule.enable_waitlist = false -> 空位保持空闲；返回 'WAITLIST_DISABLED'
4. 取队首
     head = SELECT * FROM waitlist_entry
             WHERE session_id=$1 AND status='waiting'
             ORDER BY position ASC LIMIT 1 FOR UPDATE SKIP LOCKED
   if !head -> 空位保持空闲可约；返回 'NO_WAITLIST'
5. INSERT waitlist_release (session_id, status='open', try_count=1,
        origin=$origin, origin_booking_id=$bid, current_entry_id=head.id, opened_at=now())
   UPDATE class_session SET reserved_count = reserved_count + 1 WHERE id=$1      -- 锁定名额
   UPDATE waitlist_entry
      SET status='notified', notified_at=now(),
          confirm_deadline_at = LEAST(now() + confirmMinutes, start_at),
          notified_count = notified_count + 1
    WHERE id = head.id
   INSERT notification_outbox (biz_type='waitlist_notified', biz_id=head.id, payload=...)
```
关键点：
- **`try_count` 初始为 1**（本次已通知 1 人），与 PRD R3"每次通知下一位 +1、达到 3 停止"一致。
- **`reserved_count` 与 `status='open'` 的释放事件数一一对应**：`reserved_count` 的对账 SQL 是
  `SELECT count(*) FROM waitlist_release WHERE session_id=? AND status='open'`，**运维手册里每天跑一次断言两者相等**（发现"锁定泄漏"——PRD §8.3 里的"名额反复锁死"事故）。
- `SKIP LOCKED` 的意义：万一有另一个事务正在处理同场次的另一条 entry（正常路径已被 `FOR UPDATE` 串行，这只是双保险），不会互相阻塞到死。

**顺延（`promoteNext(releaseId)`）**：确认失败/超时/放弃后调用
```
session FOR UPDATE
rel = SELECT ... FROM waitlist_release WHERE id=$1 FOR UPDATE
if rel.status != 'open' -> 已结束，返回
if rel.try_count >= 3 -> UPDATE rel SET status='abandoned', closed_at=now();
                          UPDATE class_session SET reserved_count = reserved_count - 1;
                          -- 名额标记"临期空位"（运营列表可见）；写 event_log
                          return
next = SELECT ... FROM waitlist_entry WHERE session_id=? AND status='waiting' ORDER BY position LIMIT 1 FOR UPDATE SKIP LOCKED
if !next -> UPDATE rel SET status='abandoned'; UPDATE class_session SET reserved_count = reserved_count - 1; return
UPDATE waitlist_entry SET status='notified', notified_at=now(),
       confirm_deadline_at=LEAST(now()+confirmMinutes, start_at), notified_count=notified_count+1 WHERE id=next.id
UPDATE waitlist_release SET try_count = try_count + 1, current_entry_id = next.id WHERE id=$1
INSERT notification_outbox ...
```
> **`reserved_count` 在顺延时不变**：名额始终锁在同一个释放事件上（PRD R3 明确要求）。只有"有人确认成功（`filled`）"或"事件结束（`abandoned/expired`）"才动它：
> - `filled`：`occupied_count + 1, reserved_count - 1`（名额从"锁定"变"占用"）；
> - `abandoned/expired`：`reserved_count - 1`（名额回到空闲或作废）。

**确认递补（`confirm`）事务流程**
```
session FOR UPDATE
rel = UPDATE waitlist_release SET status='filled', closed_at=now(), closed_reason='confirmed'
       WHERE id=$1 AND status='open' RETURNING *        -- 0 行 -> WAITLIST_ENTRY_STATE_CONFLICT
if 0 行: UPDATE waitlist_entry SET status='expired' WHERE id=$myEntryId AND status='notified'
         promoteNext(该 entry 所属的 open 释放事件)   -- 若还有
         return 409
时间冲突复检（Q7）：if 与会员已有有效预约重叠
         -> UPDATE entry SET status='expired'; UPDATE rel SET status='open'（回滚该次认领）;
            promoteNext(rel.id); return 409 TIME_CONFLICT("已为你顺延下一位")
UPDATE waitlist_entry SET status='confirmed', responded_at=now() WHERE id=$myEntryId AND status='notified'
INSERT booking (source='waitlist', waitlist_entry_id=myEntryId, status='booked')
UPDATE class_session SET occupied_count = occupied_count + 1, reserved_count = reserved_count - 1 WHERE id=sessionId
UPDATE waitlist_entry SET resulting_booking_id = <new booking id>
```
`if 0 行` 这个判断是**整个递补机制的唯一裁决点**：无论是运营手动加位占掉了这个名额、用户点晚了、还是另一个事务已经处理过，都只会走这一条分支。这样"不重复占位、不超卖"就是结构性成立的，而不是靠小心翼翼地检查各种情况。

**超时扫描（job，每 30 秒）**
```sql
-- confirm_deadline_at 存在「当前被通知者」waitlist_entry 上，故 JOIN 取它
SELECT r.id, r.session_id, r.current_entry_id, e.status AS entry_status
  FROM waitlist_release r
  JOIN class_session  s ON s.id = r.session_id
  LEFT JOIN waitlist_entry e ON e.id = r.current_entry_id
 WHERE r.status = 'open'
   AND (e.confirm_deadline_at <= now() OR e.id IS NULL OR s.start_at <= now())
 ORDER BY r.id
 LIMIT 100
 FOR UPDATE OF r SKIP LOCKED;
```
- `e.id IS NULL` 兜底：`open` 的释放事件却没有 `current_entry_id`（异常中间态），按"无被通知者"处理。
- 每个 release 的处理：若 `s.start_at <= now()` → release 置 `expired`、当前 entry 置 `expired`、`reserved_count - 1`（PRD S7"开课后名额不可再约"）；否则 entry 置 `expired` 后调 `promoteNext()`（`try_count >= 3` 则 `abandoned`）。
- `FOR UPDATE OF r` 只锁释放事件行，避免把 `class_session` / `waitlist_entry` 一起锁死（`promoteNext()` 内部会重新按需取 `FOR UPDATE`）。

### 6.5 取消后自动递补与"临期空位"

- 取消/请假成功后**在同一事务内**调用 `onSlotReleased`，因此"名额释放"与"名额被锁定给某人"是原子的，不存在"释放后短暂可被别人抢"的窗口（PRD D11 的锁定承诺）。
- 不发起递补的三种情况（PRD R3/S7）：`start_at - now < confirmMinutes + 5`、`enable_waitlist = false`、队列为空。此时**不创建释放事件、不锁定**，名额留空闲可约；同时写一条 `event_log('slot_left_vacant')`，运营端把它和"连续 3 次通知失败"一起显示为「临期空位」。
- 递补与"另一会员直接预约"的竞争：直接预约的原子 UPDATE 条件含 `occupied_count + reserved_count < capacity`，被锁定的名额已被 `reserved_count` 占掉 → 直接预约必然失败。两道事务都更新同一个 `class_session` 行，天然串行。

### 6.6 签到防代签与时效校验

| 风险 | 对策 | 说明 |
|---|---|---|
| 不是这节课的教练来点名 | `user_role.coach_id = session.coach_id`（或本店 staff/manager 兜底）→ 否则 `ROSTER_FORBIDDEN` | 教练身份用一次性邀请码绑定到 `openid`，运营端可解绑 |
| 冒用教练手机（本人不在场） | MVP **不解决**（PRD D4/D1 的已知取舍）；缓解手段：点名结果全部留 `marked_by + marked_at + change_log`，异常场次可事后审计抽查 | 与产品一致：无付费场景下代签动机低 |
| 窗口外改结果 | 服务端判定 `[start_at-30min, start_at+15min]`；窗口外写请求一律 `ROSTER_WINDOW_CLOSED` | **客户端时间不参与判定**；客户端只拿服务端返回的 `serverTime` 做展示 |
| 给非本场次会员标记 | 校验 `booking.session_id = :sessionId`，且 booking 状态合法 | 防止越权给别人造到场记录 |
| 打错无法撤回 | `check_in` 一行 + `change_log` 追加 | 满足 PRD S8"反复修改、以最后一次为准" |
| 手机时间被篡改 | 所有窗口判定用 `now()`（DB 服务器时间） | DB 与 API 同机或 NTP 同步 |
| 名单在点名后变化 | 名单接口每次读实时数据；新递补进来的人标 `isSupplement=true` | 已写 `check_in` 的记录不会丢（键是 `booking_id`） |
| 网点差时"点不动" | 批量接口设计为幂等 + 支持带本地 `markedAt` 的补交（P1）；失败时给出明确提示 | PRD S8 明确"若实现不了则显示明确失败提示" |

指北：真正的"防代签"需要会员自助签到（扫码/动态码/定位），PRD §3 与 D4 明确不做，本方案不预留接口，避免半成品。

### 6.7 定时任务（4 个循环）的幂等与可观测

| 任务 | 周期 | 幂等性保证 | 失败模式与处理 |
|---|---|---|---|
| `outbox-sender` | 2s | 只处理 `status='pending'` 且 `next_attempt_at <= now()`，`FOR UPDATE SKIP LOCKED`；发送成功后条件更新为 `sent` | 微信接口超时 → 记录 `last_error`、退避重试 4 次 → `failed` + 降级（PRD §6.4） |
| `promotion-timeout` | 30s | 处理 `status='open'` 的释放事件；所有状态变更都带 `WHERE status='open'` 条件 | 与确认/放弃并发 → 条件更新只有一方成功 |
| `session-settlement` | 5min | 结算 SQL 带 `b.status='booked' AND s.end_at < now()`，可重复执行 | 重跑不产生副作用（`RETURNING` 只返回本次真正改动的行，据此触发重算） |
| `cache-refresh` | 1h | 全量重算 `no_show_count_30d_cache`；顺带把已到期的 `banned_until` 置 NULL（PRD 要求"到期自动解除"） | 漏跑只影响展示，**不影响判定**（判定实时重算） |

**结算 SQL（幂等，一次搞定三种结论）**
```sql
UPDATE booking b
   SET status = CASE
         WHEN EXISTS (SELECT 1 FROM check_in c WHERE c.booking_id=b.id AND c.result='present') THEN 'attended'
         WHEN EXISTS (SELECT 1 FROM check_in c WHERE c.booking_id=b.id AND c.result='absent')  THEN 'no_show'
         ELSE 'pending_review' END,
       no_show_marked_at = CASE
         WHEN EXISTS (SELECT 1 FROM check_in c WHERE c.booking_id=b.id AND c.result='absent') THEN now()
         ELSE b.no_show_marked_at END,
       updated_at = now()
  FROM class_session s
 WHERE b.session_id = s.id AND b.status = 'booked' AND s.end_at < now()
RETURNING b.id, b.session_id, b.member_id, b.status;
```
- `pending_review` 分支覆盖 PRD R5 的两种"未点名"情形：**整场没有任何 check_in** 与 **该预约未被标记**（因为 `EXISTS` 都不成立）。
- 对 `RETURNING` 里新产生的 `no_show`，逐个调 `credit.recomputeAndApplyBan(memberId, storeId)`。

**黑名单重算与应用**
```sql
-- 重算（权威口径，PRD R5）
SELECT count(*) FROM booking b JOIN class_session s ON s.id = b.session_id
 WHERE b.member_id=$1 AND s.store_id=$2 AND b.status='no_show'
   AND s.end_at >= now() - interval '30 days' AND s.end_at < now();
```
```
if count >= threshold:
    -- PRD R5：banned_until = max(原 banned_until, 判定时刻 + N 天)，绝不缩短
    banned_until = GREATEST(COALESCE(credit.banned_until, now()), now() + N * interval '1 day')
    UPSERT member_store_credit ... ; INSERT event_log('ban_applied')
    权限校验时：if banned_until > now() -> MEMBER_BANNED
```
- 权限校验走 `member_store_credit.banned_until > now()`；**不依赖缓存计数**。
- 撤销爽约只改 `booking.status` 再重算（§5.5 ㉒）。

### 6.8 小程序订阅消息通知

**微信的限制决定了设计**：订阅消息是**一次性订阅**（用户授权一次 = 可发一条），且必须在用户手势（点击）时调 `wx.requestSubscribeMessage`。

方案：
1. **授权时机**：加入候补 / 预约成功后 / 确认递补页，各引导一次；一次调用可勾选多个模板（预约成功、递补待确认、课程取消、黑名单通知），提高"库存"。
2. **模板与配置**：4 个模板 ID 从环境变量读（`WX_TPL_WAITLIST_NOTIFIED` 等）；**模板报备是外部依赖（PRD Q5），未就绪前通知走站内降级**，不影响功能闭环。
3. **发送链路**：业务事务写 `notification_outbox` → worker 每 2 秒拉取 → `getAccessToken`（内存缓存，提前 5 分钟刷新，单实例无并发问题）→ `subscribeMessage.send`。
   - `errcode=0` → `subscription_message_log.status='success'`，outbox `sent`；
   - `errcode=43101`（用户拒收/无授权额度）→ `log.status='unauthorized'`，outbox `gave_up`，**触发降级**；
   - 网络/5xx → 重试 4 次后 `failed`，**触发降级**；
   - 40003/40001（token 异常）→ 强制刷新 token 并重试 1 次。
4. **降级动作（PRD §6.4 明确规定，必须实现）**：
   - 记录 `subscription_message_log`；
   - "我的预约"角标 + 首页红点（会员端轮询 `/api/me/bookings?scope=active` 即可得到 `pending_confirm` 态）；
   - **确认时限延长为 `min(30min, start_at - now)`**（`LEAST(now() + 30min, start_at)`）——实现位置在 `promote()` 里：查 outbox 该条是否 `gave_up/failed` 后决定用 15 还是 30 分钟；
   - 锁定期间**不释放**该名额给他人（与 PRD D11 一致）。
   > 落地细节：`promote()` 先按 15 分钟写 `confirm_deadline_at`，**等 outbox 发送结果回来后由 worker 用条件更新把它改成 30 分钟**（`WHERE status='notified' AND confirm_deadline_at = <原值>`）。这样避免"同步等通知结果"把事务拖长。该更新是幂等的（带原值条件），且不影响 §6.4 的裁决点（裁决只看 release 状态与 entry 状态）。
5. **到量监控**：`subscription_message_log` 失败率 > 30% → 触发 PRD §7.1 的"短信兜底"决策（本期不实现短信）。

### 6.9 风险与应对（含 PRD §7.1 的工程侧落实）

| 风险 | 影响 | 应对（本方案的具体机制） |
|---|---|---|
| 超卖 / 重复预约 | 高（信任崩塌） | §6.2 三道防线 + 并发测试 + CHECK 约束违反时 `critical` 告警 |
| 锁定泄漏（`reserved_count` 与 open 释放事件不等） | 高（名额被永久占死） | 每日对账 SQL + `/healthz` 暴露任务最近运行时间 + `session-settlement` 顺带修正（对所有 `start_at < now` 的场次把 open release 置 expired 并重算 `reserved_count`） |
| 递补通知未送达 | 高（候补价值崩塌） | outbox 重试 + 降级（时限延长 + 红点）+ 失败率监控 |
| 教练不点名导致误判 | 高 | `pending_review` + 运营批量处理 + **严禁自动记爽约**（结算 SQL 已结构性保证） |
| 黑名单误伤 | 中 | 阈值保守、重算口径、可撤销、`enable_ban` 开关、灰度期阈值放宽 |
| 行锁热点（同一场次开课前抢位） | 低 | 单场次事务 < 10ms；最坏 100 人抢 1 位 ≈ 1 秒排队。监控 `pg_locks` 等待时长即可，无需优化 |
| PG 单点故障 | 高（全站不可用） | 每日 `pg_dump` + WAL 归档；恢复演练一次（开发期）；托管实例可选开启 PITR |
| 定时任务静默停摆 | 高（数据不结算） | 任务不吞异常（顶层 catch + 日志 + `lastJobRuns` 暴露 + 连续 3 次失败告警） |
| 埋点缺失导致 4 周后无法复盘 | 中 | 埋点清单在 §4 与 PRD §9 对齐；上线前用"跑一遍主流程后 event_log 应有 N 条"做验收 |
| 隐私（手机号/到场记录） | 高 | 教练端不返回手机号；日志脱敏；运营端接口按角色控制字段；PRD Q6 的授权文案在授权页明示 |
| 规则配置改错（如取消阈值 7 天） | 中 | 范围校验 + 极端值二次确认 + `audit_log` + 缓存立即失效 |
| 容量下调导致约束冲突 | 中 | `CAPACITY_CONFLICT` 明确报错（不让 DB 抛 CHECK 异常给用户） |

### 6.10 性能估算（估算依据写明，不是实测）

- **规模假设**：3 门店、会员 3000、MAU 1000、日活 300、每人每天 20 次操作（PRD 假设 A1/A3 未定，此处取偏保守值）→ 约 6000 请求/天。
- **峰值**：假设 25% 请求集中在开课前 30 分钟 → 1500 次 / 30 分钟 ≈ 0.83 QPS；按 10× 峰值系数 ≈ **8 QPS**。Fastify + 单 PG 实例的典型能力在数百 QPS 量级，余量充足。
- **DB 行数**：booking 约 6000/天 × 365 ≈ 220 万行/年（3 门店）；`event_log` 约 3 倍。均可用普通索引满足，无需分区。
- **瓶颈**：明确是**同一场次 `class_session` 行的锁等待**（开课前集中抢位）。原因：所有占位/释放/递补都要更新该行。缓解不需要（毫秒级锁 + 几十人并发），仅在监控里看锁等待。
- **不做缓存**：课表查询走 `(store_id, start_at)` 索引，返回 7 天数据约几十行；引缓存反而带来"剩余名额显示不一致"的风险，收益为负。
- **真实流量未知**：以上是估算。上线后按 `/api/schedule` P95 与 `pg_stat_statements` 校准。

---

## 7. 决策记录（ADR）

### ADR-1：模块化单体，不拆服务、不引消息队列
**背景**：MVP 只有会员端/教练端/运营端三类接口，请求 < 10 QPS，唯一强一致热点是名额计数。
**决策**：单个 Node 服务（API + 4 个定时循环同进程）+ 单个 PostgreSQL。
**备选方案**：① 微服务（预约服务/候补服务/通知服务）；② 单体 + Kafka/RabbitMQ 做通知与递补异步；③ 单体 + Redis 做名额缓存与分布式锁。
**影响与代价**：无法按模块独立扩容/发布；单进程故障全站不可用；长请求可能拖慢任务循环（用每任务 30s 超时 + 独立循环缓解）。换来的是：本地事务解决所有一致性问题、一个部署单元、一个人能维护。

### ADR-2：PostgreSQL + 手写 SQL，不用 ORM
**背景**：核心逻辑依赖部分唯一索引、CHECK 约束、`FOR UPDATE`、`SKIP LOCKED`、原子 UPDATE 比较-设置、含 `EXISTS` 的批量状态判定。
**决策**：`pg` + 手写 SQL + `node-pg-migrate`（迁移里直接写 SQL）。
**备选方案**：Prisma / TypeORM / Drizzle。
**影响与代价**：没有自动生成的类型（用 ts 类型 + zod 手写 repo 返回类型）；SQL 要自己 review；迁移文件是 SQL 不是 schema DSL。换来的是：约束与锁的语义**显式可见**（这类 bug 恰恰最容易藏在 ORM 的抽象里），以及零"绕过 ORM 写 raw SQL"的分裂。

### ADR-3：名额锁定用独立表 `waitlist_release`，而不是在 session 上加布尔标记
**背景**：PRD D11/R3 要求"名额锁定给被通知者"，且同一场次可同时存在多个释放事件（多个空位、各自独立的 `try_count` 与 3 次上限）。
**决策**：每个释放名额一行 `waitlist_release`，`reserved_count` = 该场次 `status='open'` 的行数。`try_count` 挂在这一行上。
**备选方案**：① `class_session.reserved_count` + `reserved_entry_id` 单标记（同时多个空位时无法表达）；② 把 `try_count` 挂在 `waitlist_entry` 上（PRD R3 明确禁止：一条 entry 只被通知 1 次，计数会失效）。
**影响与代价**：多一张表和一轮 JOIN；但"名额 ↔ 释放事件 ↔ 被通知者"三者的对应关系可被 SQL 断言（对账），且天然支持"多个空位同时释放、各自独立顺延"。

### ADR-4：防超卖用「原子 UPDATE + CHECK 约束」，不用分布式锁/Redis
**背景**：并发抢位是唯一会引发信任崩塌的技术风险（PRD §7.1）。
**决策**：把判断写进 `UPDATE ... WHERE`，并加 `CHECK (occupied_count + reserved_count <= capacity)`。
**备选方案**：① Redis 分布式锁 + 先查后写；② 悲观锁（`SELECT ... FOR UPDATE` 后再 UPDATE）；③ 乐观锁（version 列，冲突重试）。
**影响与代价**：放弃"用锁保护业务逻辑"的直觉写法，业务逻辑必须适配成"一条条件更新表达意图"。换来的是：并发正确性由数据库保证，不依赖应用层是否记得加锁；CHECK 约束能抓住未来任何新增代码路径的错误。

### ADR-5：通知用 outbox 表 + 轮询 worker，不在事务里调微信接口
**背景**："取消 → 递补 → 通知"需要"业务与通知一致"，但微信接口调用可能超时（秒级）。
**决策**：事务内只写 `notification_outbox`；worker 每 2 秒拉取发送，带退避重试与失败降级。
**备选方案**：① 事务内同步调微信接口（会把事务拖到秒级，放大行锁等待）；② `commit` 后 fire-and-forget（进程崩溃则丢通知）；③ 引 MQ。
**影响与代价**：通知最多延迟 ~2 秒（PRD 要求 5 秒内，满足）；多一张表和一次发送状态机。换来的是事务短、通知不丢、失败可降级可观测。

### ADR-6：爽约计数实时重算，不落存储计数器
**背景**：PRD R5 明确要求，且历史上"计数器 + 定时衰减"的漏跑会造成"禁约期结束后突然又被禁"。
**决策**：`no_show_count_30d` 只在查询/判定时重算；表上的两个计数列只作展示缓存（列名带 `_cache`）。
**备选方案**：存储计数 + 每日衰减任务。
**影响与代价**：运营列表展示需要一次聚合查询（有 `(member_id, status)` 索引，量级 220 万行内可接受）；换来的是判定永远正确、无需担心任务漏跑、撤销爽约只需改一个字段。

### ADR-7：小程序端用原生 TS，不引跨端框架
**背景**：只做微信小程序（PRD §3 明确不做 H5/App/支付宝端）。
**决策**：原生小程序 + `miniprogram-api-typings`，请求封装统一处理 JWT、`serverTime` 校准、错误码 → 文案映射、加载/空/失败三态。
**备选方案**：Taro / uni-app。
**影响与代价**：将来真要做多端时会有一次重写。换来的是没有构建链、没有框架黑盒、体积小、调试直观。

### ADR-8：预约/取消/递补的幂等靠"约束 + 状态机条件更新"，不引幂等键表
**背景**：PRD S3 提到"服务端幂等键（session_id + member_id + 客户端请求 id）"。
**决策**：正确性由部分唯一索引（`booking`、`waitlist_entry`）与 `WHERE status=...` 条件更新保证；`client_request_id` 只做追踪与埋点关联。
**备选方案**：通用 `idempotency_key` 表（记录"某请求 id 已处理 → 返回缓存响应"）。
**影响与代价**：放弃"重放同一请求返回完全相同的响应体"这种严格语义（例如重复取消返回的是当前状态而不是原始响应）。换来的是：不需要维护幂等键的存储与过期，且**任何**重试/双击/并发路径都安全（约束在数据层，不依赖请求是否带键）。重复取消的响应差异对客户端无影响（都导向同一个 UI 状态）。

### ADR-9：`pending_review`（未点名异常）作为一等状态，绝不自动记爽约
**背景**：教练点名完成率目标仅 ≥90%，若整场未点名就自动记爽约，约 10% 场次的全部会员会被误判（已有 2 次者当场禁约）。
**决策**：结算 SQL 的 `ELSE 'pending_review'` 分支兜底；会员端文案为"到场状态待门店确认"；运营在「未点名异常」队列批量处理。
**备选方案**：超时未点名视为全 `attended`（宽纵）或全 `no_show`（高危）。
**影响与代价**：需要运营投入人工处理异常场次（培训成本）；换来的是"会员不被误伤、指标不被污染"（PRD §2 明确爽约率分母不含未点名场次）。

---

## 8. 里程碑与验证

### 8.1 里程碑表（单人全职估算，1 人日 = 8 小时）

| 里程碑 | 内容 | 人日 | 如何验证（可执行的具体断言） |
|---|---|---|---|
| **M0 骨架** | 仓库结构、TS 配置、Fastify 启动、pg 连接池、迁移工具、`/healthz`、日志（pino + request_id）、统一错误处理中间件、JWT 签发校验、zod 校验约定 | 1.5 | `npm run migrate && npm start` 后 `GET /healthz` 返回 `{ok:true,db:true}`；`domain/rules` 的 7 个纯函数有表驱动单测（含 R1/R2/R4/R5 边界：`now == start_at-120min`、`position` 并列、`reserved` 扣减、`banned_until` 取 max） |
| **M1 数据模型** | §4 全部 DDL、索引（含 3 个部分唯一索引）、CHECK、种子数据（1 门店/2 教室/3 课程模板/2 教练/未来 7 天场次） | 2 | 迁移可 `up`/`down` 重复执行；断言脚本：`\d booking` 含部分唯一索引；插入 `occupied=capacity` 后再加 1 触发 CHECK 报错；插入第 2 条同会员同场次 `booked` 报 23505 |
| **M2 登录与课表** | S1 登录/绑定、S2 课表与详情、剩余名额公式三处统一、`myState`/`myWaitlistRank`、规则读取与 30 秒缓存、门店筛选 | 3 | 接口测试：7 天窗口边界、`start_at` 已过不出现/置灰、门店 inactive、未绑定返回 `MEMBER_UNBOUND` 且浏览可用；断言"课表 `remaining` 与详情、预约接口用到的是同一个函数"（单测覆盖） |
| **M3 预约与取消** | S3 预约（原子占位 + 会员行锁 + 时间冲突 + 黑名单）、S4 我的预约、S5 取消/请假（服务端判定 `cancelMode`）、S13 部分（手动加容量接口） | 4 | **并发测试**（脚本 50 并发抢 5 名额）：成功恰 5、`occupied_count=5`、`capacity-reserved` 校验通过、无 CHECK 违反、无同会员双条；重复 `cancel` 两次只释放 1 个名额；`now == start_at-120min` 判为 `ontime`；冲突预约返回 `TIME_CONFLICT` 且带对比信息 |
| **M4 候补与递补（最难）** | S6 入队/退出/rank、S7 递补（锁定 + 通知 + 确认 + 放弃 + 超时 + 3 次上限 + 临期门槛）、加容量触发递补 | 6 | 集成测试：取消 → `release=open`、`reserved_count=1`、队首 `notified`、outbox 1 条；连续 3 次超时 → `abandoned` 且 `reserved_count=0`；`start_at - now < 20min` → 不创建 release 且 `reserved_count=0`；确认 vs 直接预约并发只有一方成功；退出候补后 rank 前移且 `position` 未变 |
| **M5 点名与信用** | S8 教练登录/今天课/名单/单条与批量点名/窗口校验/change_log、S9 结算判定、`pending_review`、黑名单重算/解除、撤销爽约、未点名异常处理 | 5 | 用例：整场无 `check_in` → 全部 `pending_review` 且 `no_show_count_30d=0`、不进黑名单；部分标记 → 只有明确 `absent` 计 `no_show`；3 次 `no_show` → `banned_until ≈ now+7d`；黑名单期再记 1 次 → `banned_until` 只延长不缩短；撤销 1 次 → 计数回 2 且**自动解除**；窗口外点名返回 `ROSTER_WINDOW_CLOSED`；非本场教练 403 |
| **M6 通知** | S12 订阅消息、模板配置、outbox worker、access_token 缓存、失败/拒收降级（时限延长 + 红点） | 2 | mock 微信接口三分支（成功 / 43101 / 超时）：断言 `subscription_message_log.status` 分别为 `success/unauthorized/fail`；未授权分支断言 `confirm_deadline_at = min(30min, start_at)` 且锁定未释放；重复发送同一 outbox 只发一次 |
| **M7 运营端** | S10 规则配置（6 参数 + 3 开关）、S11 名单与 CSV 导入导出、S13 手动递补、审计日志 | 3 | 用例：`cancel_deadline_minutes=2000` 被拒；阈值 >7 天返回 warning 且需二次确认；规则变更后已存在预约不受影响、会员端文案 1 分钟内更新；`enable_ban=false` 时爽约只记录不禁约；CSV 导入 100 场次后课表可见 |
| **M8 联调与上线准备** | 埋点核对、并发压测脚本、降级开关演练、运维手册（备份恢复、指标 SQL、对账 SQL）、回滚演练 | 2.5 | 走完一次主流程后 `event_log` 出现 PRD §9 全部事件名；对账 SQL 断言 `reserved_count == count(open release)`；关掉 `enable_waitlist` 后满员不可约、关掉 `enable_late_cancel` 后截止即不可取消；执行一次"2 小时回滚"演练记录 |
| **合计** | | **29 人日** | ＋20% 缓冲 ≈ **35 人日**（单人全职 ≈ 7 周；两人并行 ≈ 4～4.5 周，M4/M5 需串行在 M1 之后） |

### 8.2 外部依赖（非开发工作量，但会阻塞上线）

| 依赖 | 前置时间 | 阻塞什么 |
|---|---|---|
| 小程序主体 + 类目审核 | 1～2 周 | 无法发布体验版给门店试用 |
| 服务器域名备案 + HTTPS 证书 + 配置为 request 合法域名 | 1～3 周（备案长） | 真机无法请求接口 |
| 微信订阅消息模板报备（PRD Q5） | 5～10 工作日 | S7 通知链路；未就绪前 M6 只能跑站内降级 |
| 门店排课数据（未来 7 天，PRD Q4） | 依赖运营 | 课表为空则无法验收 |
| 教练身份绑定（邀请码/后台录入） | 依赖运营 | S8 无法验收 |

**建议：M0 之前就并行发起以上 3 项外部申请。**

### 8.3 若只有 4 周（压缩方案）

按优先级砍，且**必须保持闭环**：
1. 必须做：M0～M6（1.5+2+3+4+6+5+2 = **23.5 人日**）。
2. M7 降级：规则配置改为"直接改库 + 审计靠 SQL 记录"，排课走 CSV 导入脚本（3 人日 → **1 人日**，省 2 人日）。
3. M8 精简：只做埋点核对 + 主流程手工回归，并发压测脚本保留（这是唯一不能省的质量投入，因为超卖不可回滚）→ **2 人日**。
4. 明确不在 4 周版内的功能（写清避免期望错位）：运营端规则配置页、CSV 导出、`/api/staff/metrics`、离线补交点名、预约提醒通知、运营手动递补。

**结果**：4 周版 ≈ **26.5 人日**（23.5 + 1 + 2），风险集中在 M4（候补递补，6 人日估算若低估会直接挤掉 M8）。若 M4 超期，第一顺位是砍 M7（保留 CSV 兜底），第二顺位是把"运营手动加容量/手动递补"推迟（PRD 中它是 P1）。

---

## 9. 与 PRD 的差异与待确认问题（需要产品/CEO 回复，不阻塞开工）

以下 8 处是**我按 PRD 语义做了明确取舍并已写进方案**的地方。若产品不同意，请按条目号回复，我改方案（改动都很局部）。

| # | PRD 位置 | 问题 | 本方案的取舍（已实现口径） | 影响面 |
|---|---|---|---|---|
| Q1 | §10.2 `booking.status` 枚举 | S8/S9/R5 大量使用 `pending_review`，但 §10.2 的状态枚举里没有它 | 枚举增加 `pending_review`；结算 SQL 的 `ELSE` 分支兜底 | 极小（枚举值） |
| Q2 | S3 "服务端幂等键（session_id + member_id + 客户端请求 id）" | 若用通用幂等键表实现，会为"重放返回同一响应"引入一张表和过期策略 | 正确性靠部分唯一索引 + 条件更新；`client_request_id` 只用于追踪（ADR-8）。语义差异：重复取消返回"当前状态"而非"原始响应" | 客户端无感知 |
| Q3 | §10.2 `waitlist_entry` 唯一约束 `(session_id, member_id)` | 与 R2"退出候补后可重新排队取得新 position"直接冲突（新记录会被唯一约束拒绝） | 改为**部分唯一索引** `WHERE status IN ('waiting','notified')`，语义 = 同时最多 1 条进行中候补 | 中（不改则 S6/S5 的"退出后重新排队"报 500） |
| Q4 | §10.2 `waitlist_entry.notify_attempt_count`（"同一名额顺延计数，用于连续 3 次规则"） | 与 R3 明确写的"**不得**把该计数挂在单条 `waitlist_entry` 上（一个 entry 只会被通知 1 次，那样规则会失效）"矛盾 | `try_count` 权威放在 `waitlist_release`；`waitlist_entry.notified_count` 只作监控 | 中（放错位置会让"3 次上限"永久失效） |
| Q5 | §10.1 `member_store_credit.no_show_count_30d` / `late_cancel_count_7d` | 与 R5"唯一口径为查询时实时重算、不使用存储计数器"冲突 | 列名改为 `_cache` 后缀，仅展示；判定一律重算 | 小（命名与语义澄清） |
| Q6 | §10.2 `class_session.status` 含 `canceled_by_store`，而 §10.2 注释里也写 `canceled`，S5 用 `canceled_by_store` | 两个值的语义重叠（用户取消整场在本产品不存在，只有门店能取消整场） | 场次状态统一为 `canceled`，另加 `cancel_reason`；**预约**状态保留 `canceled_by_store`（区分"因门店取消"与"会员取消"） | 小 |
| Q7 | S7 只规定了"确认时若名额已被占用"的处理，未规定"确认时才发现与会员已有预约时间冲突" | 候补入队时校验了 R6，但入队之后会员可能又约了时间重叠的课，导致确认时冲突 | 确认事务内复检时间冲突：冲突则 entry 置 `expired`、释放事件回 `open` 并顺延下一位，返回 `TIME_CONFLICT` + 明确文案"已为你把名额顺延给下一位"。**注意**：该分支与"名额被占用"的 `expired` 共用状态，运营侧通过 `event_log` 区分原因 | 小（边界，但必须有确定行为） |
| Q8 | S7 "5 秒内"通知 | 通知走 outbox，最短延迟 = worker 周期 | worker 周期 2 秒（且 `promotion` 事务完成即写 outbox）→ 端到端通常 2～4 秒，满足 5 秒；但**网络/微信侧耗时不计入**，若微信接口慢则可能超 5 秒（此时会员端已有站内红点，不阻塞） | 小（口径澄清：PRD 的 5 秒指"系统侧发起通知"，不是"用户收到"） |

另外两条**提醒**（不是差异，是工程事实，供 CEO/运营准备）：

- **PRD §8.3 的三个降级开关已落进 `store_rule`**（`enable_ban` / `enable_waitlist` / `enable_late_cancel`），读库即时生效、30 秒缓存，**关开关不需要发版**。三者的具体降级行为在 §8.1 M7 的验证项里写死，请运营确认这 3 种降级行为是否符合预期。
- **订阅消息模板报备（PRD Q5）是硬外部依赖**：没有模板 ID，M6 只能交付"站内降级"版本，递补确认率指标（≥60%）在该状态下不可评估。请尽早确认谁去申请。

---

## 10. 自查（对照公司规则与模板）

- [x] 每个 P0（S1～S10）都映射到具体模块与里程碑（§1.1、§8.1）
- [x] 架构选型给出理由**与放弃的备选**（§3、§7 ADR）
- [x] 数据模型含字段类型、约束、索引（含 3 个部分唯一索引与 1 个防超卖 CHECK），状态流转图（§4）
- [x] 接口含入参、出参、错误码（§5，24 个端点 + 25 个错误码）
- [x] 5 个关键难点均有可验证的机制，不是口号（§6.2～§6.8）
- [x] 性能/容量目标写明估算依据（§6.10），并声明是估算非实测
- [x] 安全默认为开：参数化 SQL、zod 边界校验、JWT + 角色/门店/归属校验、密钥走环境变量、教练端不返回手机号、日志脱敏（§5.1、§6.6、§2.3）
- [x] 错误处理明确：不吞异常，任务顶层 catch + 日志 + `/healthz` 暴露（§6.7）
- [x] 与 PRD 的 8 处差异已列出并给出实现口径，未擅自改需求（§9）
- [x] **本阶段未写代码**；代码与测试在本方案评审通过后按 §8.1 里程碑推进
