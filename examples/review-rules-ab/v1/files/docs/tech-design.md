# 技术方案：健身房团课预约微信小程序（MVP）

| 项目 | 内容 |
|---|---|
| 版本 | v0.1（首版） |
| 作者 | 工程师 |
| 依据 | `docs/PRD.md` v0.2（返工修订版）、CEO 的 MVP 指令 |
| 覆盖范围 | P0-① ~ P0-⑧ 的技术实现方案；P1-⑨⑩ 给出接口与扩展点；P2-⑪ 只留数据基础 |
| 本阶段交付 | **仅文档，不写代码、不运行任何代码**（见第 12 节自查） |
| 状态 | 待产品/测试复审；开工前需业务方回答第 11 节的阻塞项 |

**本方案对 PRD 的态度**：需求以 PRD 为准，不悄悄改需求。PRD 里未定或表述可以有两种读法的地方，我一律做出**明确的假设**并写在第 11 节（标 ❗ 的是开工阻塞项），实现时按假设走，业务方否定则按第 11 节的"若被否定的影响"调整。

---

## 0. 结论摘要（给 CEO 的 12 条）

1. **一个模块化单体**（Node.js + TypeScript + Fastify）+ **一个 PostgreSQL**，不做微服务、不引入 Redis、不引入消息队列。理由见 ADR-1 / ADR-5。
2. **并发抢位的原子性完全交给数据库**：`session.booked_count` 的条件更新（`WHERE booked_count < capacity`）+ `CHECK` 约束 + `booking` 的部分唯一索引 + 事后对账任务，**四层防线保证不超卖**，不依赖应用层互斥锁（第 6.1 节）。
3. **"占位"只有一个入口**：`SeatAllocator.reserve()`。普通预约、候补转正、运营代确认三条路径**共用同一个原子接口**（PRD 要求"完全相同的原子占位接口"），避免三套实现各自出 bug（第 2.2 / 6.1 节）。
4. **候补 FIFO 用全局单调自增主键排序**，不用毫秒时间戳做排序键，也不用 `SKIP LOCKED`（会导致乱序）；同一课次的递补靠**行锁串行**从而天然保序（第 6.3 节）。
5. **名额释放与递补采用"两段式 + 收敛型 Worker"**：取消事务只做"释放名额 + 落一条 `release_event`"，递补由"释放后立即触发 + 每 5 秒兜底扫描"完成。这满足 PRD"5 秒内完成"，且进程崩溃也不会让名额卡住（第 6.4 / 6.6 节）。
6. **超时顺延由服务端判定**，判定时间取**数据库时间**（`now()`），不用应用实例时钟，避免多实例时钟漂移导致判定不一致（ADR-9）。
7. **幂等三层**：`X-Request-Id` + 幂等表 + 数据库唯一约束。连点、网络重试、并发同 ID 三种情况都有确定行为与错误码（第 6.7 节）。
8. **P0 唯一的触达手段"运营待通知任务"就是一张 outbox 表**（`notify_task`）。P1 的微信订阅消息只需给这张表加一个自动发送 worker，**不用改数据模型**——这是本方案里我最满意的一处可逆设计。
9. **所有业务规则数值全部配置化**：取消窗口 120 分钟、预约截止 30 分钟、违约 3 次、冻结 7 天、候补上限 20、递补确认时限 15 分钟、开课前 10 分钟停止递补——T-03 若把时限改成 30 分钟，**只改一个配置值，不改代码**（第 11 节 E-1）。
10. **部署：单台云服务器 + Docker Compose**（Nginx + API + PostgreSQL），镜像本身可直接搬到微信云托管/ECS/K8s，是可逆选择；放弃 Serverless（事务与 5 秒内递补不好保证，见 ADR-11）。
11. **对 PRD 交付范围的技术判断**：PRD 8.2 决策 8 说"新增最小运营后台会拉长工期"。我的估算是**后台（H5）约占 P0 工作量的 30%**，其中"课程导入"和"待通知任务"两块无法用人工表格替代。若工期是硬约束，我同意的唯一代偿方案仍是 PRD 说的"递补整体推 P1"，但我**不建议**——P0 候补会变成空壳（第 10.3 节）。
12. **本环境无法运行代码、没有线上流量**：第 8 节所有容量/性能数字都是**估算**，依据与前提写在估算旁边；第 12 节列出"哪些必须在上线前用压测/集成测试验证"。

---

## 1. 需求映射

| PRD 功能 | 实现模块 | 关键实现点 | 依赖的未定项 |
|---|---|---|---|
| P0-① 查看课表 | `schedule`（查询）+ `booking`/`waitlist`（我的状态）+ `rule`（冻结状态） | 课表卡片的 10 种 UI 状态**由服务端统一计算**（`uiState`），前端只渲染；一次查询批量带出会员的预约/候补/冻结/冲突状态，避免 N+1 | T-08（能否看别店）、T-04（冲突） |
| P0-② 预约 | `booking` + `SeatAllocator` + `idempotency` + `rule` | 条件更新扣名额；同一会员同一课次部分唯一索引；时间冲突靠会员行锁串行化；request_id 幂等 | T-04、T-07 |
| P0-③ 取消 | `booking` + `violation` + `freeze` + `rule_setting` + `release_event` | 一个事务内：置取消状态 + 名额 +1 + 违约计数 + 可能冻结 + 落 `release_event` + 审计；开关决定"是否真的冻结" | T-09、T-10、T-15 |
| P0-④ 候补与递补状态机 | `waitlist` + `SeatAllocator` + `release_event` + `notify` + `worker` | 队首 = `(session_id, status='waiting') ORDER BY id`；递补/顺延/超时全部在 `worker` 中收敛，幂等可重入 | T-03、T-04、T-05 |
| P0-⑤ 教练签到 | `signin` + `org`（教练档案） | 签到窗口判定用 DB 时间；`sign_in` 对 `session_id` 唯一；"缺席/未签到"**派生**不落库 | T-06 |
| P0-⑥ 教练身份与课程归属 | `org` + `schedule`（导入匹配） | 6 位一次性邀请码（唯一 + 24h）；`coach.openid` 唯一约束实现"一人一档"；导入整批事务，未匹配行整体失败 | T-14 |
| P0-⑦ 运营待通知任务 | `notify` + `org`（权限范围） | 唯一键 `(waitlist_id, release_event_id)` 保证重复触发不产生第二条；状态机 5 态；两个写操作都走审计 | T-13、T-15 |
| P0-⑧ 违约开关与人工解冻 | `rule` + `admin` | 全局单开关存 `rule_setting`，"只决定是否触发新冻结，不改历史判定"；解冻仅总部管理员，必填备注 | T-15 |
| P1-⑨ 候补自动通知 | `notify`（outbox worker）+ 微信订阅消息 | 复用 `notify_task`，加 `channel`/`send_result` 字段即可；P0 不加这些字段也能跑 | T-11 |
| P1-⑩ 预约提醒 | `notify` + `worker`（定时扫描） | 独立 `reminder` 表，避免污染 `notify_task` 的状态机 | T-11 |
| P2-⑪ 数据统计 | `report`（只读 SQL 视图） | P0 的埋点即"审计表 + 状态流转时间戳"，不额外建埋点系统 | — |
| PRD 8.4 依赖：排课数据源 | `schedule.import` + 后台 H5 导入页 | **MVP 临时方案**：后台按模板导入 Excel/CSV（含教练手机号），整批原子写入 | ❗T-01 |
| PRD 8.4 依赖：最小后台账号 | `auth`（admin） | 手机号 + 验证码 + 2 角色（`store_ops` / `hq_admin`）；角色与门店范围在中间件统一校验 | ❗T-13 |
| 审计与留痕（PRD 多条验收标准要求） | `audit`（横切） | 所有后台写操作、越权尝试、规则变更、解冻、补签、转归属均写 `audit_log`，可导出 | — |

---

## 2. 架构概览

### 2.1 架构模式与理由

**模块化单体（Modular Monolith）**。三个终端（会员小程序、教练小程序、运营 H5）共用一套领域规则与一个数据库。

- 为什么不是微服务：MVP 团队规模 = 1 名工程师（我自己）；模块数 8 个，但模块间**有强事务耦合**（预约 ↔ 名额 ↔ 候补 ↔ 违约必须在同一事务/同一进程协调）。拆服务会把"事务"变成"分布式事务"，把 PRD 里"不允许超卖""不允许名额空转"这两条硬要求从"数据库保证"降级成"应用补偿保证"。这与"最好的架构是团队真正维护得了的那个"直接冲突。
- 为什么不是"一个文件里的脚本"：PRD 的规则密度很高（取消窗口、违约、冻结、候补排序、递补触发、签到窗口、6 类权限），且**测试要逐条对照验收标准审查**。所以按领域切模块、把规则抽成纯函数、依赖方向单向，是为了"可测"和"可读"，不是为了架构好看。
- 拆分预留：唯一可能先拆出去的是 `worker`（定时任务）——它已经是独立进程入口，未来只需换部署单元，不改代码。

### 2.2 模块划分与依赖方向

```
                         ┌───────────────────────────────┐
   微信小程序            │   会员端 / 教练端（原生小程序） │
   （会员 / 教练）       └───────────────┬───────────────┘
                                        │ HTTPS + JWT（Bearer）
   浏览器                ┌───────────────┴───────────────┐
   （门店运营 / 总部）    │   H5 运营后台（React + Vite）  │
                         └───────────────┬───────────────┘
                                         │
                          ┌──────────────┴───────────────┐
                          │  Nginx（TLS 终止、静态资源、   │
                          │          请求体大小限制、限流） │
                          └──────────────┬───────────────┘
                                         │
   ┌─────────────────────────────────────┴────────────────────────────────────┐
   │  API 服务（Node.js + TypeScript + Fastify，可水平多实例）                  │
   │                                                                          │
   │  ① 接口层 routes/                                                        │
   │     /api/v1/*（会员、教练）      /api/v1/admin/*（运营、总部）             │
   │     横切中间件：鉴权(JWT) → 角色与门店范围(scope) → X-Request-Id →         │
   │                 入参校验(JSON Schema) → 幂等 → 审计 → 错误映射             │
   │                                                                          │
   │  ② 应用服务层（用例编排，含事务边界）                                      │
   │     auth(用户与鉴权) │ org(门店/教练/账号) │ schedule(课程与排期/导入)      │
   │     booking(预约/取消) │ waitlist(候补队列) │ signin(签到) │ notify(通知)  │
   │     rule(违约与开关) │ admin(后台用例) │ report(统计, P2)                  │
   │                                                                          │
   │  ③ 领域内核（纯函数，无 IO、无 SQL、可单测）                               │
   │     SeatAllocator  ← 唯一占位入口（普通预约/候补转正/运营代确认 共用）      │
   │     可约性判定 │ 取消窗口与违约判定 │ 冻结触发判定 │ 候补排序与递补决策      │
   │     签到窗口与迟到判定 │ 课表 uiState 计算                                │
   │                                                                          │
   │  ④ 仓储层 repositories/（Kysely + 手写事务 SQL）                          │
   │                                                                          │
   │  ⑤ Worker（同进程、DB advisory lock 保证同一时刻只有一实例在跑）           │
   │     promote → expire → session_state → freeze_expiry → reconcile → notify │
   └─────────────────────────────────────┬────────────────────────────────────┘
                                         │  单一权威存储
                          ┌──────────────┴───────────────┐
                          │  PostgreSQL 16               │
                          │  事务 / 条件更新 / 唯一约束 /  │
                          │  部分唯一索引 / CHECK / 行锁   │
                          └──────────────────────────────┘
   外部依赖：微信 auth.code2Session（登录）、phonenumber.getPhoneNumber（手机号）、
             subscribeMessage.send（P1）、小程序 HTTPS 合法域名
```

依赖方向（严格单向，禁止反向依赖）：

| 层 | 允许依赖 | 禁止 |
|---|---|---|
| 接口层 routes | 应用服务层、common（错误/校验/鉴权） | 直接写 SQL |
| 应用服务层 | 领域内核、仓储层、common | 依赖 Fastify 的 request/reply 对象（便于测试与复用） |
| 领域内核 | 仅标准库与纯类型 | 任何 IO（DB、HTTP、时间读取需作为参数传入） |
| 仓储层 | 领域类型、DB 客户端 | 依赖 HTTP 上下文 |

**跨模块的三个共享内核（必须只有一份实现）**：

1. `SeatAllocator`（占位/释放）：`reserve()` / `release()`。普通预约、候补转正、运营代确认全部走它。PRD 6.4"候补转正与抢位使用同一套原子规则"、P0-⑦"走与会员自助确认完全相同的原子占位接口"就是靠这个保证。
2. `RuleEngine`（规则数值读取）：所有窗口/阈值从 `rule_setting` 读，带进程内 60 秒缓存 + 写后失效。
3. `AuditWriter`：所有写操作统一留痕，字段一致，导出格式一致。

---

## 3. 技术选型

### 3.1 选型总表

| 层 | 选择 | 版本策略 | 理由（为什么是它） | 放弃的备选与代价 |
|---|---|---|---|---|
| 会员/教练端 | **微信原生小程序（WXML/WXSS + TypeScript）**，`miniprogram-api-typings` | 开工时锁最新稳定基础库 | ① 只在微信内运行，无跨端需求；② 微信能力（`login`、`getPhoneNumber`、`requestSubscribeMessage`、tabBar 红点、`onShow` 生命周期）原生支持最好，出问题能直接对照官方文档；③ 无构建链 → 无"脚手架升级"维护负担；④ 包体积/启动性能最优（首屏要快，会员在门口才打开） | **Taro / uni-app**：跨端收益为 0（不做 App/H5），却引入编译器、插件版本兼容、调试链路变长三类问题。**放弃代价**：若将来要 H5 版会员端，需要重写视图层（但接口层可复用） |
| 小程序状态管理 | 首版**不用第三方**，按页面自持 `data` + 一个 30 行的 `store.js`（订阅式） | — | 页面数 < 10，状态简单；引入 mobx-miniprogram 会在"是否要 observable"上浪费时间 | 若后续页面间共享状态变复杂（如"待确认"倒计时跨页同步），再引入 `mobx-miniprogram`；接口层已把状态收敛到服务端，风险低 |
| 后台 H5 | **React 18 + Vite + TypeScript + Ant Design 5 + TanStack Query** | 开工时锁最新稳定 | 后台全是表格/表单/弹窗（教练档案、待通知任务、开关与留痕、导出），AntD 直接给；TanStack Query 解决请求缓存/重试/失效，比手写 loading/error 少一半代码 | 放弃 Vue（团队熟悉度收益不抵重学成本，且后台非核心）；放弃自研表格（纯浪费时间）。**依赖数量**：这是本项目唯一"依赖较多"的地方，但它是内部工具，不进用户链路 |
| 后端语言 | **Node.js 20 LTS + TypeScript（strict）** | 20.x LTS | ① 与小程序/后台同一语言与类型，接口数据结构可以**共享一份类型定义**（少一类联调 bug）；② 本项目的并发压力是 I/O 型（DB 事务），Node 足够；③ 对照 PRD 的规则密集度，开发与重构速度是主要成本 | 放弃 Go（并发模型优势在本项目用不上：原子性由 DB 保证；代价是双语言、类型无法共享）；放弃 Java/Spring（部署与启动成本高，一人维护不划算） |
| 后端框架 | **Fastify + JSON Schema 校验 + `@fastify/jwt` + `@fastify/multipart`** | Fastify 4.x | ① 轻、快、插件边界清晰；② 内置 JSON Schema 校验，**输入校验在边界完成**（安全默认开启）；③ 不用装饰器/DI 容器，读代码就能追踪调用链（一人维护的关键） | 放弃 NestJS：DI + 装饰器 + 模块系统对 8 个模块的单体是过度抽象，且类型推断在装饰器下更绕。放弃 Express：校验/错误处理要自己拼。**放弃代价**：Fastify 生态比 Express 小，遇到冷门需求要自己写插件（本项目未遇到） |
| ORM / 数据访问 | **Kysely（类型安全 SQL builder）+ `pg` + `node-pg-migrate`（迁移）** | Kysely 0.27+ | ① 本项目核心操作是**条件更新、行锁、部分唯一索引、`ON CONFLICT`**，必须是可读可控的 SQL；② Kysely 给类型安全但**不隐藏 SQL**（`EXPLAIN` 出来的语句与源码一致，好排查）；③ 迁移是纯 SQL 文件，评审时能一眼看出索引/约束是否如本方案所写 | 放弃 Prisma：迁移是"黑盒 diff"，`SELECT ... FOR UPDATE`、部分唯一索引、`ON CONFLICT ... WHERE` 这类写法要退回 `$queryRaw`，等于两套写法并存；生成大量 client 代码。放弃 TypeORM/Sequelize：懒加载与级联的隐式行为在事务代码里是 bug 来源。**放弃代价**：Kysely 需要手写 join/迁移 SQL，比 Prisma 多写代码（我认为换来"可读 + 可调优"是值的） |
| 数据库 | **PostgreSQL 16（InnoDB 语义的对照物）** | 16.x | 详见 ADR-4。关键三条：**部分唯一索引**（`WHERE status='booked'`）天然表达"同一会员同一课次只能有一条有效记录"；**`CHECK` 约束**可做 `booked_count BETWEEN 0 AND capacity` 这道最后防线；`timestamptz` + `now()` 语义清晰，正好配套 ADR-9（时间判定用 DB 时间） | 放弃 MySQL 8：功能都能做，但"同一会员同一课次一条有效预约"要用"`active_flag` 置 NULL 的唯一索引技巧"（可读性差、易被后人改错）；`CHECK` 与部分索引表达力弱一些。放弃 MongoDB：本项目核心是事务与约束，文档库的强项（灵活模式）正好是我们的风险点。**放弃代价**：PG 在托管服务上比 MySQL 贵一点（可接受） |
| 缓存 | **MVP 不引入 Redis**（详见 ADR-5） | — | 需要缓存的只有两处：① 业务规则数值（进程内 60s 缓存 + 写后失效，单条记录，量级 0）；② 课表查询（估算峰值 < 30 QPS，单表 < 100 行/请求，PG 索引足够）。而**不能用缓存的地方更重要**：名额计数、幂等、候补队列、超时判定——这些必须单一权威，引入 Redis 只会制造"缓存与 DB 不一致"的新故障模式 | 放弃 Redis 的代价：若 QPS 增长 10 倍以上，会先到"加索引 → 加读副本 → 才考虑缓存"的顺序。**升级判据**（写进运维手册）：课表接口 P95 > 500ms 且已排除索引问题，或读 QPS > 500 持续 10 分钟 |
| 消息 / 异步 | **MVP 不引入独立消息队列**；用"数据库表 + Worker"表达待办（`release_event`、`notify_task` 就是 outbox），P1 订阅消息用同一个 Worker 发 | — | ① 目前只有两类异步：**递补/超时（必须 ≤5s 收敛、必须可重放）** 和**发通知（可重试）**。用 `SELECT ... FOR UPDATE` + 状态字段 + 幂等 Worker 就能表达，且**可查、可修、可导出**（运营出问题时能直接改表）；② 引入 MQ 会多一个"消息与业务事务不一致"的问题（要么上事务性发件箱、要么容忍丢消息），等于用 MQ 实现 outbox，本末倒置 | 放弃 RabbitMQ/Kafka/BullMQ 的代价：Worker 轮询会给 DB 带来稳定的轻量查询（每 5 秒一次、带索引、期望返回 0 行），且"最坏延迟 = 轮询间隔"。**升级判据**：轮询查询成为 DB 主要负载，或需要"精确到秒级"的延迟任务（届时用 PG 的 `LISTEN/NOTIFY` 唤起 Worker，比引 MQ 更省） |
| 定时任务 | **同进程调度（`setInterval`）+ PG `pg_try_advisory_lock` 保证单实例执行** | — | 任务必须"能重复执行不出错"（幂等），所以单实例只是省资源的优化，不是正确性依赖。用 advisory lock 而不是部署第二套调度组件 | 放弃独立 cron 容器/分布式调度框架：增加部署单元，收益为 0 |
| 微信能力 | `auth.code2Session`、`phonenumber.getPhoneNumber`、`subscribeMessage.send`（P1） | 官方 HTTP 接口，自研薄封装 | `access_token` 用"DB 一行 + 进程内缓存 + 提前 5 分钟刷新"，避免引入 Redis（见第 7 节） | 放弃官方 SDK：多依赖、更新滞后，接口本身是 POST 表单/JSON，自研 60 行够用 |
| 部署 | **单台云服务器 + Docker Compose**（`nginx` + `api` + `postgres`），数据卷持久化，`pg_dump` 每日备份到对象存储 | — | 详见 ADR-11。可逆性最高：镜像是标准 OCI 镜像，随时可搬到微信云托管 / ECS / K8s，不需要改代码 | 放弃微信云托管（顺位第二）：免运维、自带 HTTPS 域名与小程序内网调用，**若团队没有运维人力它更优**；放弃的理由是排障自由度与长期成本（本项目只有一个服务，Compose 已足够简单）。放弃 Serverless 函数：递补需要"5 秒内"且要与取消在同一事务语义下协调，函数冷启动 + 无长连接 + 定时精度差是硬伤 |
| 测试 | **Vitest（单测：领域内核）+ Vitest + 真实 PG 容器（集成测试：并发/事务/约束）** | — | 并发抢位、幂等、部分唯一索引这些**必须在真 PG 上测**（内存 PG 不支持这些语义）。测试计划由测试同学出，本方案只保证"可测性"：领域内核是纯函数、并发场景有可复现脚本 | 放弃 `pg-mem` 之类内存实现：它测不出我们最关心的行锁与约束行为 |

### 3.2 依赖清单（写进 `code/README.md` 时逐个标注用途）

**后端**：`fastify`（HTTP 框架）、`@fastify/jwt`（令牌）、`@fastify/multipart`（课程导入上传）、`@fastify/rate-limit`（登录/邀请码限流）、`pg`（驱动）、`kysely`（查询构建）、`node-pg-migrate`（迁移）、`pino` + `pino-pretty`（日志，生产只用 pino）、`exceljs`（导入解析，替换方案：`csv-parse`，见 E-4）、`zod`（少量非 HTTP 边的校验，如导入行；HTTP 边界优先用 Fastify 的 JSON Schema）。

**小程序**：`miniprogram-api-typings`（类型，dev）。无运行时依赖。

**后台**：`react`、`react-dom`、`vite`、`typescript`、`antd`、`@tanstack/react-query`、`@ant-design/icons`。

**开发/测试**：`vitest`、`testcontainers`（或直接用 `docker compose` 起 PG）、`eslint` + `prettier`。

> 原则：**每个依赖都要在 README 里写清"用途 + 版本 + 为什么不能用标准库替代"**。目前无一个依赖是"因为流行"而引入的。

---

## 4. 数据模型

### 4.1 建模原则

1. **所有业务时间判定用数据库时间**（`now()` / `CURRENT_TIMESTAMP`），应用不生成判定用的时间戳（ADR-9）。写入的时间戳（如 `booked_at`）也一律用 DB 默认值。
2. **只存事实，不存可推导的状态**：会员预约的"已完成"= `session.status='finished' AND booking.status='booked'`（派生）；课程"未签到/缺席"= 过了窗口且无 `sign_in` 行（派生，PRD 不允许伪造签到记录）。**只有需要被索引、被排序、被并发条件更新、需要审计历史的东西才落列**。
3. **唯一约束承担业务规则**：PRD 里"只能一条""只能一次""不能重复"这类规则，一律优先用数据库约束表达（不靠"代码里记得判断"）。
4. **主键用 `bigserial`**（紧凑、索引局部性好）。ID 可被枚举，但**授权不依赖 ID 不可猜**：所有按 ID 的读写都在 SQL 里带上 `member_id` / `store_id` 条件（第 6.9 节）。
5. 时间字段一律 `timestamptz`（存 UTC），展示按门店时区（MVP 全为 `Asia/Shanghai`）；"自然日"筛选在 SQL 里按门店时区换算（`(start_at AT TIME ZONE store.timezone)::date = $date`），不用应用时区。
6. 金额、支付相关字段**一律不建**（PRD 不做支付）。

### 4.2 实体关系

```
store 1─n coach / member / course / session / admin_user
course 1─n session
coach  1─n session（归属可转移，历史可查）
member 1─n booking  n─1 session
member 1─n waitlist n─1 session
session 1─n release_event →（被消费）→ waitlist（队首）
waitlist 1─n notify_task（唯一键 = waitlist_id + release_event_id）
session 1─1 sign_in（教练本人签到）
booking 1─0..1 violation →（触发）→ freeze（member 级）
admin_user / member / coach 1─n audit_log（actor）
```

### 4.3 表结构

> 说明：只列出**必须由数据库表达**的约束与索引；所有表都带 `created_at timestamptz NOT NULL DEFAULT now()`，需要更新的表带 `updated_at`（由触发器或仓储层统一维护），下表不再重复。

#### 4.3.1 `store`（门店）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| code | text | NOT NULL, UNIQUE | 与外部系统对齐的编码 |
| name | text | NOT NULL | |
| timezone | text | NOT NULL DEFAULT 'Asia/Shanghai' | "自然日"与文案时间按此换算 |
| address | text | | 课表卡片展示 |
| status | text | NOT NULL CHECK IN ('active','disabled') | 停用门店不在课表出现 |

索引：`UNIQUE(code)`。
> **教室**：MVP 不建 `room` 表，`session.room_name` 存文本。理由：教室在 MVP 只用于展示，无容量绑定、无教室维度统计（PRD 第 3 节不做运营建课），建表只有"看起来规范"的收益。将来若要做教室维度，加 `room_id` 可空列并回填即可（可逆）。

#### 4.3.2 `course`（课程 / 课程类型）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| store_id | bigint | NOT NULL FK→store | 课程按门店维护 |
| name | text | NOT NULL | 如"哈他瑜伽" |
| type | text | NOT NULL | 课程类型，课表筛选用（如 瑜伽/单车/搏击） |
| duration_min | int | NOT NULL CHECK > 0 | 默认时长，导入时可覆盖 |
| status | text | NOT NULL CHECK IN ('active','archived') | |

索引：`(store_id, type) WHERE status='active'`（课表按类型筛选）。

#### 4.3.3 `session`（排期 / 课次）——**全系统最热的表**

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| store_id | bigint | NOT NULL FK→store | 冗余自 course，用于课表按门店查询（避免每查都 join） |
| course_id | bigint | NOT NULL FK→course | |
| coach_id | bigint | NOT NULL FK→coach | **NOT NULL 是刻意的**：PRD P0-⑥ 要求导入时教练手机号匹配不到则整批失败，所以不存在"没有教练的课次" |
| room_name | text | | 教室/区域文本 |
| start_at / end_at | timestamptz | NOT NULL，CHECK (end_at > start_at) | |
| capacity | int | NOT NULL CHECK (capacity > 0) | 容量 |
| booked_count | int | NOT NULL DEFAULT 0，CHECK (booked_count >= 0 AND booked_count <= capacity) | **名额占用计数（并发原子更新的目标）** |
| waitlist_count | int | NOT NULL DEFAULT 0，CHECK (waitlist_count >= 0) | 候补人数（用于"候补已满"与原子入队） |
| waitlist_limit | int | NOT NULL DEFAULT 20 | 候补上限（T-05 可改：改配置默认值，历史数据不动） |
| status | text | NOT NULL CHECK IN ('draft','published','cancelled','finished') | |
| external_source / external_id | text / text | 联合唯一（部分：`WHERE external_id IS NOT NULL`） | 导入去重：同一外部系统的同一课程不重复导入 |
| cancel_reason | text | | 门店取消原因，展示给已预约会员 |

索引与约束：

| 名称 | 定义 | 用途 |
|---|---|---|
| PK | `(id)` | 抢位更新、按 ID 查详情 |
| `uq_session_external` | `UNIQUE (external_source, external_id) WHERE external_id IS NOT NULL` | 导入幂等（重复导入同一文件不产生新行） |
| `ix_session_store_start` | `(store_id, start_at)` | **课表主查询**（门店 + 日期区间 + 排序） |
| `ix_session_coach_start` | `(coach_id, start_at)` | 教练"我的课程"（P0-⑤） |
| `ix_session_status_start` | `(status, start_at)` | Worker 扫描：到点置 `finished`、关闭队列 |
| `ck_seat_range` | `CHECK (booked_count BETWEEN 0 AND capacity)` | **不超卖的数据库级最后防线** |

> **关于 `ck_seat_range` 的取舍**：它带来一个约束——容量不能上调/下调到低于已约人数（MVP 不做容量调整，PRD 第 3 节明确）。我保留它，因为"超卖"是 PRD 的回滚触发条件之一，用一行 CHECK 换一个结构性保证很值。若将来要支持容量调整，需要把这条 CHECK 改成触发器或放宽（记录在 ADR-6）。

#### 4.3.4 `member`（会员）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| openid | text | NOT NULL, UNIQUE | 微信身份，唯一约束实现"一个微信一个会员" |
| unionid | text | NULL, 非唯一 | 预留（同主体多应用时用） |
| phone | text | NULL, UNIQUE | 手机号（用于运营电话触达）；**日志与导出按 T-15/E-6 脱敏策略处理** |
| name | text | NULL | 会员昵称或自填姓名 |
| store_id | bigint | NULL FK→store | 绑定的门店（未绑定为 NULL） |
| card_no / card_status / card_expire_at | text / text / date | `card_status CHECK IN ('none','active','expired')` | 会员卡状态；T-08 若已有会员系统，则改为"外部卡号 + 每次查询校验"（本表退化为映射表） |
| status | text | NOT NULL CHECK IN ('active','disabled') | 违规封号等场景（MVP 未用，预留） |

索引：`UNIQUE(openid)`、`UNIQUE(phone)`、`(store_id, status)`。
> **不存"是否冻结"字段**：冻结状态以 `freeze` 表为权威（`status='active' AND frozen_until > now()`），避免"两处状态不一致"。课表接口对会员只查一次冻结状态，不存在性能问题。

#### 4.3.5 `coach`（教练档案）——P0-⑥

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| store_id | bigint | NOT NULL FK→store | MVP 一人一店（T-14） |
| name | text | NOT NULL | |
| phone | text | NOT NULL, UNIQUE | 导入匹配用的键 |
| openid | text | NULL, **UNIQUE** | 绑定后写入；唯一约束实现"同一微信只能绑 1 个档案"（PRD P0-⑥） |
| bound_at | timestamptz | NULL | 绑定时间 |
| status | text | NOT NULL CHECK IN ('active','disabled') | 停用后不能登录教练端，历史签到保留 |

索引：`UNIQUE(phone)`、`UNIQUE(openid) WHERE openid IS NOT NULL`、`(store_id, status)`。

#### 4.3.6 `coach_invite_code`（一次性邀请码）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| coach_id | bigint | NOT NULL FK→coach | |
| code | char(6) | NOT NULL, UNIQUE | 6 位；唯一保证同一码不会被两个教练用 |
| expires_at | timestamptz | NOT NULL | 创建 + 24h（可配置） |
| used_by_openid / used_at | text / timestamptz | NULL | 一次性；使用后不可复用 |
| created_by_admin_id | bigint | NOT NULL FK→admin_user | 留痕 |

索引：`UNIQUE(code)`、`(coach_id, expires_at DESC)`、`(expires_at)`（清理过期码）。
> **码空间与暴力猜测**：6 位数字 = 100 万组合，配合"24h 有效 + 一次性 + 同一 openid 10 分钟内 5 次失败即临时锁定（记录在 `auth_throttle`）"（PRD P0-⑥ 末条）足够。`code` 生成时用加密随机源（`crypto.randomInt`），禁止用时间戳。

#### 4.3.7 `admin_user`（后台账号）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| phone | text | NOT NULL, UNIQUE | 登录名（T-13：若复用现有后台账号，本表退化为映射） |
| name | text | NOT NULL | |
| role | text | NOT NULL CHECK IN ('store_ops','hq_admin') | 门店运营 / 总部管理员 |
| store_id | bigint | NULL FK→store | **CHECK ((role='store_ops') = (store_id IS NOT NULL))`**：门店运营必须有门店，总部管理员必须没有 |
| status | text | NOT NULL CHECK IN ('active','disabled') | |

索引：`UNIQUE(phone)`、`(role, store_id)`。

#### 4.3.8 `booking`（预约记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| session_id | bigint | NOT NULL FK→session | |
| member_id | bigint | NOT NULL FK→member | |
| store_id | bigint | NOT NULL FK→store | 冗余，供后台按店筛选与统计（写入时取自 session） |
| status | text | NOT NULL CHECK IN ('booked','cancelled_free','cancelled_violation','cancelled_by_store') | 取消分两类是为了违约计数可追溯；"已完成"不落状态（派生） |
| source | text | NOT NULL CHECK IN ('mini_app','waitlist_promote','ops_confirm') | PRD P0-② 要求记录来源；也是运营代确认的统计口径 |
| booked_at | timestamptz | NOT NULL DEFAULT now() | 首次成功写入时间（幂等重放返回它） |
| cancelled_at | timestamptz | NULL | |
| cancel_reason | text | NULL | |
| request_id | text | NULL | 创建它的客户端请求 ID（便于与幂等表对照排查） |

索引与约束（**本项目最重要的三条约束之一在这里**）：

| 名称 | 定义 | 用途 |
|---|---|---|
| `uq_booking_active` | `UNIQUE (session_id, member_id) WHERE status = 'booked'` | **同一会员同一课次只能有 1 条有效预约**（PRD 6.4）；取消后可再约（新记录），历史保留可审计 |
| `ix_booking_member_status` | `(member_id, status)` | "我的预约"列表；时间冲突校验（配合 session join） |
| `ix_booking_session_status` | `(session_id, status)` | 后台查看某课次名单；对账任务统计人数 |
| `ix_booking_store_booked` | `(store_id, booked_at DESC)` | 后台按店统计/导出 |

> **为什么用"部分唯一索引"而不是"复用一行 + 状态回滚"**：复用一行会让"同一会员第 3 次预约同一课"覆盖掉前两次的取消历史，而 PRD 要求违约与取消可追溯、可导出，也要能回答"这位会员到底取消过几次"。多行 + 部分唯一索引同时拿到"只能一条有效"和"历史完整"。

#### 4.3.9 `waitlist`（候补记录）——P0-④ 的核心

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | **同时就是排队序号**（全局单调递增，见下） |
| session_id / member_id / store_id | bigint | NOT NULL | |
| status | text | NOT NULL CHECK IN ('waiting','pending_confirm','promoted','expired','cancelled','skipped') | 见 4.4 状态机 |
| join_at | timestamptz | NOT NULL DEFAULT now() | 展示用（"你前面还有 N 人"、留痕） |
| pending_since | timestamptz | NULL | 进入"待确认"的**名额释放时刻**（PRD 规定计时起点） |
| confirm_deadline | timestamptz | NULL | 截止的**绝对时间**；CHECK：`status <> 'pending_confirm' OR confirm_deadline IS NOT NULL` |
| release_event_id | bigint | NULL FK→release_event | 本次分配对应的名额释放事件 |
| promoted_booking_id | bigint | NULL FK→booking | 转正产生的预约记录 |
| skip_reason_code / skip_reason | text / text | NULL | 被跳过原因（PRD P0-④ 第 10 条要求会员能看到原因） |

索引与约束：

| 名称 | 定义 | 用途 |
|---|---|---|
| `uq_waitlist_active` | `UNIQUE (session_id, member_id) WHERE status IN ('waiting','pending_confirm')` | "会员未重复加入"（PRD 6.3）+ 幂等 |
| `ix_waitlist_queue` | `(session_id, status, id)` | **取队首**：`WHERE session_id=$1 AND status='waiting' ORDER BY id LIMIT 1` |
| `ix_waitlist_deadline` | `(status, confirm_deadline) WHERE status = 'pending_confirm'` | 超时扫描（每 10 秒，期望返回极少行） |
| `ix_waitlist_member` | `(member_id, status)` | "我的候补" |
| `ck_waitlist_deadline` | `CHECK (status <> 'pending_confirm' OR confirm_deadline IS NOT NULL)` | 防止出现"待确认但没截止时间"的不可关闭状态 |

> **排序键的选择（ADR-7）**：用 `id`（全局 `bigserial`）排序，而不是 `join_at` 毫秒时间戳，也不用"每课次自增序号"。理由：① `id` 由数据库序列生成，**天然单调、天然全局有序**，同毫秒也不会撞；② 不需要在写路径上加"算下一个序号"的锁或计数（那会与抢位事务抢同一把锁）；③ PRD 说的"毫秒时间戳 + 单调整数序号（同毫秒按序号）"，用 `(join_at 展示, id 排序)` 完全满足。代价：不同课次的队列序号不连续（对用户不可见，无影响）。

#### 4.3.10 `release_event`（名额释放事件）——把"名额外泄"变成可查的待办

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| session_id / store_id | bigint | NOT NULL | |
| cause | text | NOT NULL CHECK IN ('booking_cancel','promote_expired') | MVP 只有这两种（PRD：名额释放的唯一来源是会员取消；递补过期会把名额放回池子） |
| source_booking_id / source_waitlist_id | bigint | NULL | 谁释放的（追溯） |
| seats | int | NOT NULL DEFAULT 1 CHECK (seats > 0) | 保留批量释放能力（MVP 恒为 1） |
| consumed_by_waitlist_id | bigint | NULL FK→waitlist | 被分配给了谁 |
| consumed_at | timestamptz | NULL | NULL = **尚未消费** → 这就是"名额空转"的待办清单 |

索引：`(session_id, consumed_at)`（Worker 找未消费事件）、`(consumed_at) WHERE consumed_at IS NULL`（全站兜底扫描）。
> 这张表是 PRD"不出现名额空着但队列没动"的技术表达：**只要有一条 `consumed_at IS NULL` 的释放事件，Worker 就会反复尝试消费它**，直到成功或确认"无人可递补"（此时置 `consumed_at` 并记录 `skip_reason`）。

#### 4.3.11 `notify_task`（运营待通知任务）——P0-⑦，也是 P1 的 outbox

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| store_id / session_id / waitlist_id / release_event_id | bigint | NOT NULL | |
| status | text | NOT NULL CHECK IN ('pending','notified','promoted','expired','cancelled') | 见 4.4 |
| confirm_deadline | timestamptz | NOT NULL | **与会员端同源**（同一个值写入，不允许后台另算） |
| notify_method | text | NULL CHECK IN ('phone','wechat','front_desk') | 标记已通知时必填 |
| notify_remark | text | NULL | |
| marked_by_admin_id / marked_at | bigint / timestamptz | NULL | 留痕 |
| closed_at | timestamptz | NULL | 进入终态的时间（统计"未通知即过期"用） |
| channel / send_result | text / jsonb | P1 增加 | P1 订阅消息的发送结果（P0 不建这两列，P1 迁移新增，不影响已有数据） |

索引与约束：

| 名称 | 定义 | 用途 |
|---|---|---|
| `uq_notify_task` | `UNIQUE (waitlist_id, release_event_id)` | **幂等**：同一名额释放事件重复触发不产生第二条（PRD P0-⑦） |
| `ix_notify_task_store` | `(store_id, status, confirm_deadline)` | 运营任务列表 + 到期高亮排序（本店） |
| `ix_notify_task_all` | `(status, confirm_deadline)` | 总部管理员全量视图 |

#### 4.3.12 `sign_in`（签到记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| session_id | bigint | NOT NULL, **UNIQUE** FK→session | 一个课次只有一条签到记录（教练本人） |
| coach_id | bigint | NOT NULL FK→coach | |
| signed_at | timestamptz | NOT NULL DEFAULT now() | **签到成功时刻**（幂等重放返回它） |
| is_late | boolean | NOT NULL | 由领域内核按 `signed_at > start_at` 计算后落库（便于统计，不重复算） |
| source | text | NOT NULL CHECK IN ('coach','admin_manual') | 补签只能由总部管理员做（PRD 6.5） |
| operator_admin_id / remark | bigint / text | NULL | 补签必填备注 |
| request_id | text | NULL | |

索引：`UNIQUE(session_id)`、`(coach_id, signed_at DESC)`。
> **"缺席/未签到"不落库**：它是"过了 `start_at + 15min` 且无 `sign_in` 行"的派生结果。不落库的原因：① 落库需要一个"保证一定跑成功"的定时任务去补写，多一个故障点；② 落库会让"签到"这个事实与"没签到"这个判断混在一张表，容易被人手工改成"已签到"（审计风险）。后台查询用 LEFT JOIN 计算。

#### 4.3.13 `violation`（违约记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| member_id / session_id / booking_id / store_id | bigint | NOT NULL；`booking_id` **UNIQUE** | 一次取消最多产生一条违约（幂等） |
| occurred_at | timestamptz | NOT NULL DEFAULT now() | |
| expires_at | timestamptz | NOT NULL | = `occurred_at + interval '30 days'`（滚动窗口，配置化） |
| reason_code | text | NOT NULL DEFAULT 'late_cancel' | MVP 只有一种 |
| created_at | timestamptz | NOT NULL DEFAULT now() | |

索引：`UNIQUE(booking_id)`、`(member_id, expires_at)`（滚动计数：`COUNT(*) WHERE member_id=$1 AND expires_at > now()`）、`(expires_at)`（清理/归档）。
> **不用定时任务让违约记录"失效"**：滚动 30 天的口径就是 `expires_at > now()`，是查询条件，不是数据变更。少一个任务、少一类"任务没跑导致规则错"的故障。（PRD 6.2"每笔违约记录在 30 天后自动失效"由此满足。）
> **开关关闭期间照常计数**（PRD 8.2 决策 10）：本表**不**记录开关状态、不做条件写入；开关只影响 `freeze` 表的写入。

#### 4.3.14 `freeze`（冻结记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| member_id | bigint | NOT NULL | |
| violation_id | bigint | NULL FK→violation | 触发它的违约 |
| reason | text | NOT NULL CHECK IN ('violation_rule','manual_other') | |
| frozen_at / frozen_until | timestamptz | NOT NULL | 默认 7 天（配置化） |
| status | text | NOT NULL CHECK IN ('active','lifted','expired') | |
| lifted_by_admin_id / lifted_at / remark | bigint / timestamptz / text | NULL | 人工解冻必填备注（PRD P0-⑧） |

索引：`(member_id, status)`（判定会员当前是否被冻结）、`(status, frozen_until)`（到期扫描）。
> **只有一条"当前冻结"的保证**：同一会员在已冻结期间再次违约不重复冻结（`WHERE status='active' AND frozen_until > now()` 存在则跳过）。这条规则 PRD 没有明说，我按"冻结是权限开关、不是叠加惩罚"处理，列为 E-5 让业务确认。
> **开关关闭不会自动解除已有冻结**（PRD P0-⑧ 明确）：所以"解除"只有两条路——到期扫描置 `expired`，或管理员手动置 `lifted`。代码里不存在"读开关决定是否解除"的分支。

#### 4.3.15 `rule_setting`（规则配置，含违约开关）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| key | text | PK | 如 `violation_freeze_enabled` |
| value | jsonb | NOT NULL | 如 `{"enabled": false}` |
| updated_by_admin_id | bigint | NULL | |
| updated_at | timestamptz | NOT NULL | |
| remark | text | NULL | 变更备注（PRD 要求必填） |

初始数据（迁移里 seed）：
- `violation_freeze_enabled = {"enabled": false}` ← **上线首月默认"只提醒不冻结"**（PRD 6.2）
- `cancel_free_window_minutes = 120`
- `book_cutoff_before_start_minutes = 30`
- `book_open_days_before = 7`
- `violation_threshold_count = 3` / `violation_window_days = 30` / `freeze_days = 7`
- `waitlist_limit_per_session = 20`
- `waitlist_confirm_minutes = 15`（**T-03 若定 30，只改这里**）
- `waitlist_promote_stop_before_start_minutes = 10`
- `sign_in_window_before_minutes = 30` / `sign_in_window_after_minutes = 15`
- `invite_code_ttl_hours = 24`

机制性参数（批处理间隔、重试次数）放**环境变量**，不放这里：业务方可能改的是数值，改坏系统的是机制。变更开关与这些数值都走 `PUT /admin/settings/*` → 写 `rule_setting` + `audit_log`（留痕含前后值，PRD P0-⑧ 验收标准）。

#### 4.3.16 `audit_log`（操作流水 / 留痕，横切所有模块）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| actor_type | text | NOT NULL CHECK IN ('member','coach','admin','system') | |
| actor_id | bigint | NULL | system 为空 |
| actor_role | text | NULL | `store_ops` / `hq_admin` |
| store_id | bigint | NULL | 数据范围（门店运营只看本店） |
| action | text | NOT NULL | 命名空间式：`coach.create`、`coach.invite_code.generate`、`session.transfer_coach`、`session.import`、`booking.cancel`、`violation.freeze`、`rule.switch.update`、`member.unfreeze`、`sign_in.manual`、`auth.forbidden`、`auth.throttled` |
| target_type / target_id | text / text | NULL | 如 `('coach','12')` |
| before / after | jsonb / jsonb | NULL | 关键字段前后值（用于"可被审计"） |
| remark | text | NULL | |
| request_id / ip | text / inet | NULL | 与幂等表、访问日志串联排查 |

索引：`(target_type, target_id, created_at DESC)`、`(store_id, created_at DESC)`、`(actor_id, created_at DESC)`、`(action, created_at DESC)`。
> 越权尝试（PRD 多条验收标准要求"403 且留痕"）也写这张表（`action='auth.forbidden'`），并用同一条中间件路径写，避免"某些接口忘了留痕"。

#### 4.3.17 `idempotency`（幂等记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| actor_key | text | NOT NULL | 如 `member:12`、`admin:3` |
| scope | text | NOT NULL | 如 `booking.create`、`booking.cancel`、`waitlist.join`、`waitlist.confirm`、`sign_in.create`、`rule.switch.update` |
| request_id | text | NOT NULL | 客户端生成（UUID v4） |
| status | text | NOT NULL CHECK IN ('in_progress','succeeded','failed') | `in_progress` 用于拦"并发同 ID" |
| response_code | int | NULL | 首次响应状态码 |
| response_body | jsonb | NULL | 首次响应体（重放时原样返回） |
| expires_at | timestamptz | NOT NULL | 默认 24h，之后可清理 |

索引：**`UNIQUE (actor_key, scope, request_id)`**（唯一约束即幂等的最后防线）、`(expires_at)`（清理）。

#### 4.3.18 `auth_throttle`（登录/邀请码尝试计数）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| key | text | NOT NULL | 如 `openid:oXXX`、`phone:138xxxx` |
| action | text | NOT NULL | `coach.bind`、`admin.login`、`member.bind` |
| window_start_at | timestamptz | NOT NULL | 窗口起点 |
| fail_count | int | NOT NULL DEFAULT 0 | |
| locked_until | timestamptz | NULL | 触发锁定时间 |

索引：`UNIQUE(key, action)`。
> 用 DB 而不是内存计数：多实例部署时内存计数会被绕过（安全默认开启）。

#### 4.3.19 `course_import_batch`（导入批次留痕）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | bigserial | PK | |
| store_id / uploaded_by_admin_id | bigint | NOT NULL | |
| file_name / file_sha256 | text | NOT NULL | 同文件重复上传可识别 |
| total_rows / created_rows / updated_rows / failed_rows | int | NOT NULL DEFAULT 0 | |
| unmatched_rows | jsonb | NULL | **未匹配教练的行清单（行号 + 手机号 + 原因）**，PRD P0-⑥ 要求 |
| status | text | NOT NULL CHECK IN ('succeeded','failed','partial_not_applied') | 原子导入：失败即 `failed`，不产生半成品数据 |
| error_detail | text | NULL | |

### 4.4 状态机（全部落库可查、转折时间都有字段）

**`session.status`**
```
draft ──发布/导入──> published ──到点(start_at)──> finished
                        │
                        └──门店取消──> cancelled   （整条候补队列作废；已预约记录置 cancelled_by_store，不计违约）
```

**`booking.status`**
```
（创建）──> booked ──会员取消(≥120min 或开关不冻结的口径见 PRD 6.2)──> cancelled_free
                  ├─会员取消(<120min)──────────────────────────────> cancelled_violation（同时可产生 violation/freeze）
                  ├─门店取消课程──────────────────────────────────> cancelled_by_store
                  └─（展示态，不落库）session.finished 时显示"已完成"
```
> "已完成"不落库的理由：它是时间的函数，落库必须靠定时任务保证不漏，且一旦任务没跑就会看到"已结束但仍显示已预约"。派生查询：`status='booked' AND session.status='finished'` → 展示"已完成"。

**`waitlist.status`**
```
（加入）──> waiting ──名额释放并被选为队首──> pending_confirm ──确认──> promoted（生成 booking，source=waitlist_promote）
               │                                  ├─超时(DB now() ≥ confirm_deadline)──> expired  → 顺延下一位
               │                                  ├─会员放弃（P0-④ 第 12 条）──────────────> cancelled → 立即顺延
               │                                  └─分配前校验不通过（时间冲突/被冻结/课程取消）──> skipped（记录 skip_reason_code）
               ├─会员主动退出候补────────────────────────────────────────────────────────> cancelled → 立即顺延
               └─开课时刻队列关闭仍未轮到─────────────────────────────────────────────────> expired（reason=queue_closed_at_start）
```
**`notify_task.status`**
```
（生成，随队首进入 pending_confirm）──> pending ──运营标记──> notified ──> promoted（会员自助确认 或 运营代确认成功）
                                            │                    │
                                            │                    └─> expired（超时顺延）
                                            └─> expired（未通知即过期，是可统计的运营问题，PRD 风险表要求）
课程被取消 ───────────────────────────────────────────────────────> cancelled
```

**`coach_invite_code`**：创建（未使用）→ 已使用 / 已过期（`used_at` 与 `expires_at` 表达，不设 status 字段）。
**`freeze.status`**：`active` → `expired`（到期扫描）/ `lifted`（仅总部管理员）。

### 4.5 一致性对账（PRD"超卖=0"的技术手段）

有一个 Worker 任务 `reconcile`（每 5 分钟）：

```sql
-- 找不一致的课次（返回 0 行是正常状态）
SELECT s.id,
       s.booked_count,
       COUNT(b.id) FILTER (WHERE b.status = 'booked') AS actual,
       s.waitlist_count,
       COUNT(w.id) FILTER (WHERE w.status IN ('waiting','pending_confirm')) AS actual_waitlist
FROM session s
LEFT JOIN booking  b ON b.session_id = s.id
LEFT JOIN waitlist w ON w.session_id = s.id
WHERE s.start_at > now() - interval '1 day'
GROUP BY s.id
HAVING s.booked_count <> COUNT(b.id) FILTER (WHERE b.status = 'booked')
    OR s.waitlist_count <> COUNT(w.id) FILTER (WHERE w.status IN ('waiting','pending_confirm'));
```

发现不一致时：**只告警、不自动修**（自动修会把真实故障悄悄抹掉）。同时提供 `POST /admin/health/recount`（内部令牌）做人工修正并留痕。这条对账是 PRD 10.2"超卖 ≥1 次即回滚"的**检测手段**——没有它，回滚条件无法被观测。

---

## 5. 接口设计

### 5.1 通用约定

| 项 | 约定 |
|---|---|
| 基地址 | `https://api.<domain>/api/v1`（会员/教练）、`/api/v1/admin`（后台） |
| 认证 | `Authorization: Bearer <JWT>`；会员/教练/后台三种角色的 JWT 载荷不同（`sub`, `typ: member|coach|admin`, `roles`, `storeId`），密钥从环境变量读 |
| 幂等 | 所有写接口**必须**带 `X-Request-Id: <UUID>`；服务端按 `(actor, scope, requestId)` 去重，重放返回**首次状态码 + 首次响应体**（HTTP 头 `X-Idempotent-Replay: true`） |
| 响应格式 | 成功 `{"data": ..., "requestId": "..."}`；失败 `{"error":{"code":"SESSION_FULL","message":"...","details":{...}},"requestId":"..."}` |
| 时间格式 | ISO 8601 带时区 `2024-06-01T19:00:00+08:00`；前端**不自己算倒计时起点**，只渲染服务端返回的绝对时间 |
| 分页 | 游标：`?cursor=<opaque>&limit=20`，返回 `{"items":[...],"nextCursor":null}`。课表按日查询用日期参数不用游标 |
| 输入校验 | 每个路由声明 JSON Schema（`additionalProperties: false`），超出字段直接 400 |
| 权限 | 中间件按角色 + 门店范围注入 `scope`；**所有仓储查询强制带 scope 条件**，越权写 `audit_log(action='auth.forbidden')` 并返回 403 |
| 错误码 | 见 5.6，前后端**共用同一份常量**（后端生成、小程序/后台从同一 TS 包导入，避免文案错配） |
| 限流 | 登录/绑定/邀请码：`@fastify/rate-limit` + `auth_throttle` 双重；导出类接口限制并发 1/人 |

### 5.2 会员端接口

#### 5.2.1 `POST /auth/wechat/login` —— 微信登录

| 项 | 内容 |
|---|---|
| 入参 | `{ "code": "081Abc...", "phoneCode": "可选，来自 getPhoneNumber" }` |
| 逻辑 | `wx.login` 的 code → 调用微信 `auth.code2Session` → 拿 `openid`；`phoneCode` 存在则调 `phonenumber.getPhoneNumber` 取手机号；按 `openid` upsert member；签发 JWT（有效期 7 天，`/auth/refresh` 可续） |
| 出参 | `{"token":"...","expiresAt":"...","profile":{"memberId":12,"storeId":null,"roles":["member"],"coachId":null},"needBind":{"phone":true,"store":true}}` |
| 错误 | `WECHAT_CODE_INVALID(400)`（code 用过或过期，客户端应重新 `wx.login`）、`WECHAT_API_UNAVAILABLE(503)`（微信侧失败，可重试）、`THROTTLED(429)` |
| 备注 | 同一 openid 既是会员又可能是教练：`roles` 里可能同时有 `member` 和 `coach`，小程序按角色显示入口；`coachId` 来自 `coach.openid` 唯一索引的命中 |

#### 5.2.2 `POST /auth/member/bind` —— 绑定门店与手机号

| 项 | 内容 |
|---|---|
| 入参 | `{ "storeId": 1, "phoneCode": "..." }`（或 T-08 若已有会员系统：`{storeId, phone, verifyCode}`） |
| 出参 | `{"member":{"id":12,"storeId":1,"phone":"138****1234","cardStatus":"active","cardExpireAt":"2025-12-31"}}` |
| 错误 | `PHONE_ALREADY_BOUND(409)`（该手机号已绑定其他微信，PRD 未覆盖，见 E-7）、`STORE_NOT_FOUND(404)`、`STORE_DISABLED(409)` |
| 备注 | **无有效会员卡的会员仍允许绑定**（能看课表），但预约接口会拒绝 `NO_ACTIVE_CARD`（PRD P0-② 末条：拒绝并引导绑定/联系门店） |

#### 5.2.3 `GET /stores`

出参：`{"items":[{"id":1,"name":"XX 店（体育中心店）","address":"..."}]}`。未登录也可调用（绑定前需要选择门店），加 IP 级限流。

#### 5.2.4 `GET /sessions` —— 课表（P0-①）

| 项 | 内容 |
|---|---|
| 入参 | `storeId`(必填) `date=YYYY-MM-DD`(可选，默认今天) `courseType`(可选) `cursor`/`limit` |
| 权限 | 需登录；未绑定门店时返回 `STORE_NOT_BOUND(409)`（PRD P0-①：未绑定不展示课程数据） |
| 出参 | `{"items":[{"sessionId":301,"courseName":"哈他瑜伽","courseType":"瑜伽","coachName":"王教练","roomName":"1 号教室","startAt":"...","endAt":"...","durationMin":60,"capacity":20,"remaining":3,"waitlistCount":5,"waitlistLimit":20,"uiState":"AVAILABLE","uiStateReason":null,"myBookingId":null,"myWaitlistId":null,"myWaitlistStatus":null,"myWaitlistPosition":null,"myConfirmDeadline":null,"conflictWithSessionId":null}],"store":{"id":1,"timezone":"Asia/Shanghai"},"serverTime":"..."}` |
| `uiState` 枚举 | `AVAILABLE` 可预约 / `FULL_WAITLISTABLE` 已满可候补 / `WAITLIST_FULL` 候补已满 / `CLOSED` 已截止（<30min） / `STARTED` 进行中 / `FINISHED` 已结束 / `CANCELLED` 已取消（**不返回给会员**，直接过滤掉） / `TIME_CONFLICT` 与已约课程冲突 / `FROZEN` 权限冻结（`uiStateReason` 里给解冻时间） / `PENDING_CONFIRM` 该会员对这门课正处于待确认 |
| 备注 | ① `uiState` 由服务端计算（领域内核纯函数），**前端不做任何状态推断**——否则 10 种状态会在两端各实现一遍，早晚不一致；② 一次查询用三条批量子查询取"我的预约/我的候补/我的冻结"，不做 N+1；③ 返回 `serverTime` 供前端校时（**倒计时仍以服务端时间为准**，前端只在本地显示时用偏移量，避免手机时间不准导致会员误判"还有很久"） |

#### 5.2.5 `GET /sessions/:id` —— 课程详情 + 规则摘要

出参在列表字段基础上增加：`{"bookable":true,"bookingCutoffAt":"...","cancelFreeUntil":"...","cancelRuleText":"距开课不足 2 小时取消将记 1 次违约","waitlistRuleText":"...","promoteConfirmMinutes":15,"promoteCutoffAt":"..."}`。
> 文案：**服务端给"文案 key + 参数"，前端渲染**。理由：PRD P0-③ 要求"提醒版 vs 冻结版两套文案，由开关决定"，文案必须跟着规则走；把长中文放后端便于统一改措辞、也便于测试用同一 key 断言。

#### 5.2.6 `POST /bookings` —— 预约（P0-②）

| 项 | 内容 |
|---|---|
| 入参 | `{"sessionId":301}`，Header `X-Request-Id` |
| 成功出参 | `{"booking":{"id":9001,"sessionId":301,"status":"booked","source":"mini_app","bookedAt":"...","remaining":2},"violation":{"count30d":0,"frozenUntil":null}}` |
| 错误码 | `SESSION_FULL(409)`、`ALREADY_BOOKED(409)`（不同 requestId 但已有有效预约，PRD 要求提示"你已预约该课程"）、`TIME_CONFLICT(409, details.conflictSessionId)`、`BOOKING_CLOSED(409)`（<30min）、`SESSION_CANCELLED(409)`、`SESSION_NOT_PUBLISHED(409)`、`MEMBER_FROZEN(403, details.frozenUntil)`、`NO_ACTIVE_CARD(403)`、`STORE_SCOPE_FORBIDDEN(403)`、`DUPLICATE_IN_PROGRESS(409)`（同一 requestId 正在处理中，前端 1 秒后重试即可）、`RATE_LIMITED(429)` |
| 备注 | 事务与并发细节见 6.1；返回 `remaining` 让前端立刻刷新卡片，避免"我约成了但列表还显示 3 个位置" |

#### 5.2.7 `GET /bookings` —— 我的预约

入参：`status=upcoming|history`（`upcoming` = 未开始且状态为 `booked`）、`cursor`、`limit`。
出参项：`{"id":9001,"sessionId":301,"courseName":"...","startAt":"...","storeName":"...","roomName":"...","coachName":"...","status":"booked","displayStatus":"已预约","canCancel":true,"cancelPreview":{"willCountViolation":false,"copyKey":"cancel.no_penalty"},"source":"mini_app"}`。
> `canCancel` / `cancelPreview` 由服务端算：让"能不能取消、会不会计违约"在列表页就能看到（PRD 8.1：会员最在意"我取消会不会被罚"）。

#### 5.2.8 `GET /bookings/:id/cancel-preview` —— 取消前预览（不做写入）

出参：`{"canCancel":true,"willCountViolation":true,"violationCountAfter":2,"willFreeze":false,"frozenUntil":null,"copyKey":"cancel.violation_warning","copyParams":{"remainingMinutes":95,"threshold":3,"freezeDays":7},"seatWillGoToWaitlist":true,"waitlistCount":5}`。
> 为什么单独一个接口：PRD P0-③ 要求二次确认弹窗**明确写出**"距开课不足 2 小时…将记 1 次违约…累计 3 次暂停 7 天"，这些数字必须由服务端按当时的开关与计数实时算（`copyKey` 决定"提醒版/冻结版"）。前端自己算数字一定会和规则漂移。

#### 5.2.9 `POST /bookings/:id/cancel` —— 取消（P0-③）

| 项 | 内容 |
|---|---|
| 入参 | `{"reasonCode":"optional"}`，Header `X-Request-Id` |
| 成功出参 | `{"booking":{"id":9001,"status":"cancelled_violation","cancelledAt":"..."},"violation":{"counted":true,"count30d":2,"frozenUntil":null},"seatReleased":{"released":true,"willPromote":true,"copyKey":"cancel.seat_to_waitlist"}}` |
| 错误码 | `CANCEL_NOT_ALLOWED_STARTED(409)`（课程已开始）、`BOOKING_NOT_ACTIVE(409)`（已取消，见幂等说明）、`BOOKING_NOT_FOUND(404)`（或不属于本人）、`STORE_SCOPE_FORBIDDEN(403)` |
| 幂等 | ① 同 `X-Request-Id` 重放 → 返回首次结果；② 不同 ID 但记录已取消 → 返回 `200` + `{"alreadyCancelled":true,"booking":{...},"violation":{"counted":false}}`（**不重复计数**，PRD 明确违约只 +1） |
| 备注 | 事务内还做：写 `violation`（若计违约）→ 判定是否触发 `freeze` → 写 `release_event` → 写 `audit_log`。递补不在本事务（见 6.6） |

#### 5.2.10 `POST /waitlist` —— 加入候补（P0-④）

| 项 | 内容 |
|---|---|
| 入参 | `{"sessionId":301}`，Header `X-Request-Id` |
| 成功出参 | `{"waitlist":{"id":5501,"sessionId":301,"status":"waiting","position":6,"aheadCount":5,"joinAt":"..."}}` |
| 错误码 | `WAITLIST_FULL(409)`、`WAITLIST_CLOSED(409)`（≤30min）、`ALREADY_WAITLISTED(200 幂等返回原记录)`、`ALREADY_BOOKED(409)`、`TIME_CONFLICT(409)`、`MEMBER_FROZEN(403)`、`SEAT_AVAILABLE(409, details.hint="当前还有名额，请直接预约")`（避免用户误入候补队列占位） |
| 备注 | `position` 用 `COUNT(*) WHERE status IN ('waiting','pending_confirm') AND id < :id` + 1 计算（`ix_waitlist_queue` 覆盖） |

#### 5.2.11 `GET /waitlist` —— 我的候补（P0 唯一权威入口）

入参：`status=active|history`、`cursor`、`limit`。
出参项：
```json
{"id":5501,"sessionId":301,"courseName":"哈他瑜伽","startAt":"2024-06-01T19:00:00+08:00",
 "storeName":"XX 店","coachName":"王教练","status":"pending_confirm","displayStatus":"有位子了，请确认",
 "confirmDeadline":"2024-06-01T18:05:00+08:00","confirmDeadlineText":"18:05",
 "position":null,"aheadCount":0,"canConfirm":true,"canAbandon":true,
 "skipReasonCode":null,"skipReason":null,"serverTime":"2024-06-01T17:52:11+08:00"}
```
返回**绝对时间 `confirmDeadline` 与格式化文本**，并提供 `serverTime`；PRD 明确"不要显示'还剩 15 分钟'"（页面打开时倒计时可能已被运营通知过程消耗）。前端本地只做"到期前 60 秒变红"的显示优化，判定一律在服务端。

#### 5.2.12 `GET /waitlist/pending-count`

出参 `{"count":1}`，供 `wx.showTabBarRedDot`（进小程序与 `onShow` 时拉取）。

#### 5.2.13 `POST /waitlist/:id/confirm` —— 确认参加（候补转正）

| 项 | 内容 |
|---|---|
| 入参 | 空体，Header `X-Request-Id` |
| 成功出参 | `{"booking":{"id":9100,"status":"booked","source":"waitlist_promote"},"waitlist":{"id":5501,"status":"promoted"}}` |
| 错误码 | `SEAT_UNAVAILABLE(409)`（名额被并行流程占用或课程取消 → 服务端立即重新判定队首，PRD P0-④ 第 8 条）、`CONFIRM_EXPIRED(409)`（已过 `confirm_deadline`）、`WAITLIST_NOT_PENDING(409)`、`MEMBER_FROZEN(403)` |
| 幂等 | 同 `X-Request-Id` 重放返回首次结果；不同 ID 但已 `promoted` → 200 + `{"alreadyPromoted":true,"booking":{...}}`（PRD P0-④ 第 13 条） |
| 备注 | 走 `SeatAllocator.reserve(source='waitlist_promote')`，与普通预约同一原子路径 |

#### 5.2.14 `POST /waitlist/:id/abandon` —— 放弃/退出候补

出参 `{"waitlist":{"id":5501,"status":"cancelled"},"promotionTriggered":true}`；不计违约（PRD 6.2）。触发下一顺延（同步触发一次 + Worker 兜底）。重复提交幂等返回 200。

### 5.3 教练端接口

#### 5.3.1 `POST /coach/bind` —— 邀请码绑定（P0-⑥）

| 项 | 内容 |
|---|---|
| 入参 | `{"inviteCode":"482913"}`，Header `X-Request-Id`（需已微信登录，JWT 内有 openid） |
| 成功出参 | `{"coach":{"id":7,"name":"王教练","storeId":1,"storeName":"XX 店","boundAt":"..."}}` |
| 错误码 | `INVITE_CODE_INVALID(400)`（不存在/已使用/已过期 → 统一文案"邀请码无效或已过期"，不区分以免被枚举）、`OPENID_ALREADY_BOUND(409, details.coachName)`（PRD 要求提示"该微信已绑定教练：xxx"）、`THROTTLED(429)`（同一 openid 10 分钟内 5 次失败 → 提示稍后再试，写 `auth_throttle` 与 `audit_log`） |
| 备注 | 绑定是 `UPDATE coach SET openid=$1, bound_at=now() WHERE id=$2 AND openid IS NULL` + `UPDATE coach_invite_code SET used_by_openid=$1, used_at=now() WHERE code=$2 AND used_at IS NULL`，两条在同事务；`UNIQUE(openid)` 兜住并发双击 |

#### 5.3.2 `GET /coach/me`
出参：`{"coach":{"id":7,"name":"王教练","storeId":1,"storeName":"XX 店","status":"active"}}`；`status='disabled'` 时小程序显示"账号已停用，请联系门店"且不进课程列表（PRD P0-⑤ 末条）。

#### 5.3.3 `GET /coach/sessions` —— 我的课程（P0-⑤）

| 项 | 内容 |
|---|---|
| 入参 | `from`/`to`（可选）。**服务端强制裁剪到 `[今天-7, 今天+7]`**，越界直接 422 并返回允许范围 |
| 出参 | `{"items":[{"sessionId":301,"courseName":"哈他瑜伽","startAt":"...","endAt":"...","durationMin":60,"storeName":"XX 店","roomName":"1 号教室","bookedCount":18,"signIn":{"uiState":"AVAILABLE","signedAt":null,"isLate":null},"coachConflict":false}]}`
| 备注 | 数据范围由 `WHERE coach_id = :jwtCoachId AND start_at BETWEEN ...` 保证（PRD：不含其他门店或其他教练的课程）；`coachConflict=true` 表示该教练同一时段还有其他课（PRD T-06：系统需对运营报出该冲突） |

`signIn.uiState` 枚举：`NOT_YET`（未到时间）/ `AVAILABLE`（可签到）/ `SIGNED` / `SIGNED_LATE` / `TIMEOUT`（已超时未签到，提示联系门店）/ `CANCELLED`。

#### 5.3.4 `POST /coach/sessions/:id/sign-in` —— 签到

| 项 | 内容 |
|---|---|
| 入参 | 空体，Header `X-Request-Id` |
| 成功出参 | `{"signIn":{"id":401,"sessionId":301,"signedAt":"...","isLate":false}}` |
| 错误码 | `SIGN_IN_NOT_IN_WINDOW(409, details:{"windowStart":"...","windowEnd":"...","serverTime":"..."})`、`COACH_DISABLED(403)`、`SESSION_CANCELLED(409)`、`SESSION_NOT_FOUND(404)` |
| 幂等 | 同 `X-Request-Id` 重放 → 返回首次记录；不同 ID 但已签到 → `200 {"alreadySigned":true,"signIn":{...首次时间...}}`（PRD："签到时间取首次成功写入的服务端时间"） |

### 5.4 后台接口（H5）

#### 5.4.1 认证

| 接口 | 说明 |
|---|---|
| `POST /admin/auth/send-code` | `{phone}` → 发验证码（**MVP 用假实现：固定码写入日志 + 仅白名单手机号可登录**，见 E-3；接短信服务商为独立小任务）。限流：同手机号 60 秒 1 次、10 分钟 5 次 |
| `POST /admin/auth/login` | `{phone, code}` → `{token, profile:{adminId, name, role, storeId}}`；失败 5 次锁 10 分钟 |
| `GET /admin/me` | 当前账号与数据范围 |

#### 5.4.2 教练档案与归属（P0-⑥）

| 方法 路径 | 入参 | 出参/错误 |
|---|---|---|
| `GET /admin/coaches` | `storeId?`(hq 可省) `status?` `keyword?` `cursor` | 列表；**门店运营强制注入自己的 storeId** |
| `POST /admin/coaches` | `{name, phone, storeId}` | 创建；`PHONE_EXISTS(409)`；越权 `STORE_SCOPE_FORBIDDEN(403)` + 留痕 |
| `PATCH /admin/coaches/:id` | `{name?, storeId?, status?}` | 停用/改档；留痕含前后值 |
| `POST /admin/coaches/:id/invite-codes` | 空体 | `{code:"482913","expiresAt":"..."}`；每次生成一条新记录（旧码不失效，但**建议前台只展示最新的**） |
| `DELETE /admin/coaches/:id/openid` | `{remark}` | 解绑微信（换手机/换人场景），留痕 |
| `POST /admin/sessions/transfer-coach` | `{fromCoachId, toCoachId, sessionIds:[...]}` | 转移**未来课程**归属；`SESSION_ALREADY_STARTED(409)`；留痕（before/after） |

#### 5.4.3 排课导入（T-01 的 MVP 兜底方案）

| 方法 路径 | 说明 |
|---|---|
| `GET /admin/import/sessions/template` | 下载模板（`store_code, course_name, course_type, coach_phone, room_name, start_at, end_at, capacity`） |
| `POST /admin/import/sessions` | `multipart/form-data`：`file` + `storeId` + `dryRun=true/false`。**先 dryRun 校验并返回未匹配行**，再正式导入（正式导入仍做同一套校验，整批原子） |
| 出参 | `{batchId:12,totalRows:48,created:45,updated:3,unmatched:[{row:7,coachPhone:"138...",reason:"COACH_NOT_FOUND"},{row:22,...}],status:"succeeded"}` |
| 错误 | `IMPORT_COACH_NOT_MATCHED(409, details.unmatched)`（**整批失败，不产生半成品**，PRD P0-⑥ 验收标准）、`IMPORT_SCHEMA_INVALID(400, details.rows)`、`IMPORT_CONFLICT_COACH_TIME(409, details.rows)`（同一教练同时段两门课，按 T-06 至少上报） |
| 备注 | 同文件重复上传：`file_sha256` + `uq_session_external`（`external_id = 文件名:行号:开始时间` 的哈希）保证不重复建课；重复导入同一文件结果幂等 |

#### 5.4.4 课次与名单

| 方法 路径 | 说明 |
|---|---|
| `GET /admin/sessions` | `storeId?` `date` `status?` → 列表含 `capacity/bookedCount/remaining/waitlistCount/signInStatus`（`signInStatus` 由 LEFT JOIN 计算，含"缺席/未签到"派生态） |
| `GET /admin/sessions/:id/detail` | 返回 `bookings[]`（会员姓名/手机号脱敏/来源/状态）、`waitlist[]`（位次/状态/`confirmDeadline`/被跳过原因）、`signIn`、`releaseEvents[]`（未消费的会高亮） |
| `POST /admin/sessions/:id/cancel` | **不在 MVP**（PRD 第 3 节：运营不能调课/改容量）。但"课程被取消"这个状态需要有人能置——来自导入数据的 `status` 变更（同一外部 ID 再次导入时把状态更新为 cancelled）。写在这里是为了让"课程取消"链路明确、可测 |

#### 5.4.5 待通知任务（P0-⑦）

| 方法 路径 | 入参 | 出参/错误 |
|---|---|---|
| `GET /admin/notify-tasks` | `storeId?` `status?` `dueWithinMinutes?` `cursor` | 列表，按 `confirm_deadline ASC`；每项含 `dueSoon`（≤ 10 分钟，PRD 要求置顶高亮）、`waitlistId`、`memberName`、`memberPhone`(脱敏)、`position`、`courseName`、`startAt`、`confirmDeadline` |
| `POST /admin/notify-tasks/:id/mark-notified` | `{method:"phone|wechat|front_desk", remark?}` | `status: notified` + 留痕；已终态时 `TASK_NOT_ACTIONABLE(409)`；重复提交幂等（同 requestId） |
| `POST /admin/notify-tasks/:id/promote` | `{remark}`（必填） | 调 `SeatAllocator.reserve(source='ops_confirm')`；成功 → `{"booking":{...},"task":{"status":"promoted"}}`；`SEAT_UNAVAILABLE(409)`（名额已被占/课程取消）、`TASK_NOT_ACTIONABLE(409)` |
| `GET /admin/notify-tasks/export` | 同筛选 | CSV/XLSX 明细（PRD：创建时间、状态流转时间、操作人、通知方式、是否代确认、是否超时） |

#### 5.4.6 违约、开关与解冻（P0-⑧）

| 方法 路径 | 权限 | 说明 |
|---|---|---|
| `GET /admin/violations/members` | 门店运营（本店）/总部 | 滚动 30 天违约会员清单：`{memberId, name, phoneMasked, storeId, violationCount30d, lastViolationAt, freeze:{status, frozenUntil}}` |
| `GET /admin/settings/violation-freeze` | 都有读权限 | `{"enabled":false,"effectiveFrom":"...","updatedBy":"张三","updatedAt":"...","impact":{"activeFreezeCount":3,"violationMemberCount30d":41}}` |
| `PUT /admin/settings/violation-freeze` | **仅 hq_admin** | `{enabled, remark}`（remark 必填）→ 改 `rule_setting` + 留痕（前后值）。门店运营调用 → `HQ_ONLY_FORBIDDEN(403)` + 留痕 |
| `POST /admin/members/:id/unfreeze` | **仅 hq_admin** | `{remark}`（必填）→ `freeze.status='lifted'` + `lifted_by/lifted_at` + 留痕；`NO_ACTIVE_FREEZE(409)` |
| `POST /admin/sign-ins/manual` | **仅 hq_admin** | `{sessionId, signedAt, remark}`（必填）→ 补签（`source='admin_manual'`）；`SIGN_IN_EXISTS(409)`；留痕 |
| `GET /admin/audit-logs` | 按角色范围 | `storeId? action? targetType? targetId? from? to? cursor` + 导出 |

### 5.5 运维接口

| 接口 | 说明 |
|---|---|
| `GET /healthz` | 进程存活（不查 DB） |
| `GET /readyz` | 检查 DB 连通 + Worker 心跳时间（`worker_heartbeat` 表一行） |
| `GET /internal/metrics`（内部令牌） | 关键计数：当日预约成功/失败（按 code 分）、递补成功数、超时顺延数、未消费 `release_event` 数、`reconcile` 最近结果、`notify_task` 未通知即过期数 |

### 5.6 错误码清单（前后端共用常量）

| code | HTTP | 触发场景 | 前端处理建议 |
|---|---|---|---|
| `AUTH_REQUIRED` | 401 | 未带/过期令牌 | 静默重登（`wx.login`）后重放一次 |
| `FORBIDDEN` / `STORE_SCOPE_FORBIDDEN` / `HQ_ONLY_FORBIDDEN` | 403 | 越权（含他店、非总部管理员改开关） | 提示"无权限"；**服务端已留痕** |
| `MEMBER_FROZEN` | 403 | 会员在冻结期预约/候补 | 用 `details.frozenUntil` 展示"预约权限暂停至 …" |
| `NO_ACTIVE_CARD` | 403 | 无有效会员卡 | 引导绑定/联系门店 |
| `VALIDATION_ERROR` | 400 | 入参不合法（JSON Schema） | 展示字段级错误（开发期会发现的问题） |
| `NOT_FOUND` | 404 | 资源不存在或不属于当前会员 | 刷新列表 |
| `SESSION_FULL` | 409 | 抢位时名额已被占 | 提示"名额已满"并给**"加入候补"一键入口**（PRD P0-② 要求保留课程上下文） |
| `ALREADY_BOOKED` | 409 | 已有有效预约 | 提示"你已预约该课程" |
| `TIME_CONFLICT` | 409 | 与已约课程时间重叠 | 提示冲突课程名与时间 |
| `BOOKING_CLOSED` | 409 | 距开课 ≤ 30 分钟 | 卡片置灰 |
| `SESSION_CANCELLED` / `SESSION_NOT_PUBLISHED` | 409 | 课程状态不允许 | 刷新课表 |
| `CANCEL_NOT_ALLOWED_STARTED` | 409 | 课程已开始仍要取消 | 提示"课程已开始，无法取消" |
| `BOOKING_NOT_ACTIVE` | 409 | 取消一个已取消的预约（异常路径） | 刷新"我的预约" |
| `WAITLIST_FULL` / `WAITLIST_CLOSED` | 409 | 候补满 / 已停止加入 | 卡片置灰 |
| `ALREADY_WAITLISTED` | 200 | 已在队列 | 跳"我的候补" |
| `SEAT_AVAILABLE` | 409 | 有名额却点了候补 | 引导去预约 |
| `SEAT_UNAVAILABLE` | 409 | 确认/代确认时名额已不可用 | 提示"名额已不可用"，服务端已重新判定队首 |
| `CONFIRM_EXPIRED` | 409 | 超时后点确认 | 展示"已过期并顺延给下一位" |
| `WAITLIST_NOT_PENDING` | 409 | 非待确认状态点确认 | 刷新"我的候补" |
| `DUPLICATE_IN_PROGRESS` | 409 | 同 requestId 并发处理中 | 1 秒后重试一次 |
| `SIGN_IN_NOT_IN_WINDOW` | 409 | 不在签到窗口 | 展示窗口时间 |
| `COACH_DISABLED` / `COACH_NOT_BOUND` | 403 | 教练停用/未绑定 | 提示联系门店/去绑定 |
| `INVITE_CODE_INVALID` | 400 | 邀请码无效/已用/过期 | 统一文案 |
| `OPENID_ALREADY_BOUND` | 409 | 该微信已绑其他教练 | 展示已绑定教练姓名 |
| `IMPORT_COACH_NOT_MATCHED` | 409 | 导入有未匹配教练行 | 导出/展示未匹配行清单 |
| `TASK_NOT_ACTIONABLE` | 409 | 操作已终态的任务 | 刷新列表 |
| `RATE_LIMITED` / `THROTTLED` | 429 | 限流/尝试过多 | 提示稍后再试 |
| `WECHAT_API_UNAVAILABLE` | 503 | 微信接口失败 | 可重试 |
| `INTERNAL` | 500 | 未预期异常 | 展示"系统繁忙" + 上报 requestId |

---

## 6. 关键难点与方案

这一节是本方案的核心。每条都给出：**问题 → 方案 → 为什么这样 → 怎么验证**。

### 6.1 满员与并发抢位的原子性（不超卖）

**问题**：PRD 6.4 要求"名额扣减必须在 1 次原子操作内完成，剩余名额不允许为负数，任何情况下不允许超卖"；P0-② 明确给出"1 个名额 × 2 人同时提交"的场景。

**方案（四层防线，任何一层单独失效都不至于超卖）**：

1. **应用层：一条条件更新语句**（在事务内，先于插入 `booking`）：
   ```sql
   UPDATE session
      SET booked_count = booked_count + 1, updated_at = now()
    WHERE id = $1
      AND status = 'published'
      AND booked_count < capacity;   -- ← 关键条件
   -- 若 affectedRows = 0 → 说明满员/已取消/已结束 → 返回 SESSION_FULL / SESSION_CANCELLED
   ```
   `UPDATE ... WHERE` 在 InnoDB/PG 上会对该行加写锁并**基于最新已提交数据**判断条件（PG 在 `READ COMMITTED` 下会重新检查 WHERE 条件，这是"竞争后重查"语义），因此两个并发事务必然只有一个能把 `booked_count` 从 19 变成 20，另一个看到 `booked_count = 20`，条件不成立、影响 0 行。
2. **唯一约束**：`uq_booking_active (session_id, member_id) WHERE status='booked'` 兜住"同一会员重复提交/重复插入"。
3. **CHECK 约束**：`ck_seat_range (booked_count BETWEEN 0 AND capacity)` 是数据库结构的最后防线，任何代码路径写坏计数都会直接报错回滚，而不是静默超卖。
4. **事后对账**：`reconcile` 任务（4.5）每 5 分钟比对 `booked_count` 与 `booking` 实际条数，不一致即告警（这是 PRD 回滚条件"超卖 ≥1 次"的检测手段）。

**为什么不用乐观锁版本号**：乐观锁要"读版本 → 写时校验版本"，在"抢最后一个位置"这种**热点行**场景下会大量失败重试，用户体验是"点了没反应/反复失败"，而我们真正要的语义是"条件满足就成功、不满足就明确失败"，条件更新一条语句表达得更准确、更快。**为什么不用 Redis 预扣减**：会引入"Redis 与 DB 不一致"的第二种真相；并且"谁来补回预扣减的名额"（超时/崩溃）本身又是一个新故障点，MVP 不值得。

**为什么不用 `SELECT ... FOR UPDATE` 再判断**：也可以（`SELECT booked_count FROM session WHERE id=$1 FOR UPDATE` 然后判断），但多一次往返、且必须显式加锁；条件更新让数据库一句话完成"加锁 + 校验 + 更新"。**唯一需要显式行锁的地方是"时间冲突校验"（见 6.2）**。

**验证方式**（写进测试计划，需要真 PG）：
- 集成测试：`capacity=1`，20 个并发请求 → 断言恰好 1 个 201、其余 `SESSION_FULL`，最终 `booked_count=1`、`booking` 有效条数 = 1。
- 压测脚本：`capacity=20` × 200 并发（含 100 个不同会员、100 个重复会员）→ 断言无超卖、无负数、`reconcile` 返回 0 行。
- 断言 DB 约束生效：直接 `UPDATE session SET booked_count = capacity + 1` 应报 `ck_seat_range`。

### 6.2 同一会员的并发预约与时间冲突（T-04）

**问题**：会员同一时段只能有 1 个有效预约。若同一会员同时（两个请求）预约两门时间重叠的课，两条请求各自"看不到对方的未提交数据"，都可能通过校验；PRD 还要求候补也做冲突校验（避免递补成功后又冲突）。

**方案**：预约/候补事务开始时，**先对该会员行加锁**：
```sql
SELECT id FROM member WHERE id = $1 FOR UPDATE;
```
然后做冲突校验：
```sql
SELECT b.id, s2.id AS conflict_session_id, s2.start_at, s2.end_at
  FROM booking b JOIN session s2 ON s2.id = b.session_id
 WHERE b.member_id = $1
   AND b.status = 'booked'
   AND s2.status <> 'cancelled'
   AND tstzrange(s2.start_at, s2.end_at, '[)') && tstzrange($2, $3, '[)');
```
有交集 → `TIME_CONFLICT`。因为同一会员的写事务被行锁串行化，第二个请求会在锁释放后看到第一个的提交，从而正确拒绝。

**锁顺序与死锁分析**（必须写下来，否则后人加代码会踩）：
- 本事务固定顺序：`member` 行锁 → `session` 行锁（条件更新）。一次预约只涉及**一个** `session` 行，所以不存在"两个事务交叉持有 session 行"的环；`member` 行锁也不会与另一事务形成环（另一事务锁的是别的 member，然后等同一个 session 行等待即可，无环）。
- **禁止**在事务中按任意顺序锁多行 `session`（例如"批量代确认"）——若未来要批量操作，必须先按 `session_id` 升序排序后依次加锁，这条写进代码规范。
- 锁持有时间极短（单条 UPDATE + 一次插入），且同一会员的并发请求本来就应该串行。

**为什么不加 PG 的 `EXCLUDE` 约束**（`btree_gist` + 时间范围排他）：它要求把"课次时间区间"冗余到 `booking` 行上，且需要额外维护 `btree_gist` 扩展与触发器；规则一旦要改（比如"允许连堂课"、"只对不同门店课次限制"）就要重建索引。行锁方案在应用层，规则变化只改一处纯函数，**可逆性更好**。

**为什么不用 Redis 分布式锁**：同上，多一个组件的真相来源，且锁失效/超时会把并发问题变成偶发数据错误。

**验证方式**：集成测试并发提交同一会员的两门重叠课 → 断言恰好 1 个成功、另一个 `TIME_CONFLICT`；并发提交同一会员的"预约 + 加入候补（重叠）"→ 断言不允许同时成立。

### 6.3 候补队列的先进先出与"谁拿到释放的名额"

**问题**：PRD 6.3 要求"加入时间 FIFO（同毫秒按序号）+ 不按等级"；同时"释放的名额不重新公开先到先得，只给队列"。

**方案**：
1. **排序键 = `waitlist.id`**（全局 `bigserial`）。理由见 ADR-7。
2. **取队首必须保序**：
   ```sql
   SELECT w.id, w.member_id
     FROM waitlist w
    WHERE w.session_id = $1 AND w.status = 'waiting'
    ORDER BY w.id
    LIMIT 1;
   ```
   **刻意不用 `FOR UPDATE SKIP LOCKED`**：`SKIP LOCKED` 会让并发 Worker 跳过被锁的队首去拿第二位，**直接破坏 FIFO**（这是很多实现的隐性 bug）。正确做法是让"同一课次的递补"整体串行化。
3. **串行化手段**：所有会影响某课次名额或队列的操作，都在事务开始时锁 `session` 行（`SELECT ... FROM session WHERE id=$1 FOR UPDATE` 或直接用 6.1 的条件更新语句，它自带行锁）。同一课次的"释放 + 递补 + 入队 + 顺延"因此天然排队执行，顺序 = 提交顺序。
4. **候补入队的原子性**：`waitlist_count` 也要原子校验上限（同 6.1 的手法）：
   ```sql
   UPDATE session SET waitlist_count = waitlist_count + 1
    WHERE id = $1 AND status = 'published' AND waitlist_count < waitlist_limit;
   -- 影响 0 行 → WAITLIST_FULL
   ```
   再插入 `waitlist` 行（`uq_waitlist_active` 兜住重复加入）。
5. **被跳过的会员**：分配前校验（时间冲突 / 被冻结 / 课程已取消 / 该会员已通过别的途径约上这门课）不通过 → 该候补记录置 `skipped` + `skip_reason_code`，继续看下一位。**注意**：`skipped` 是终态（需重新排队）。PRD 只要求"能看到被跳过的原因"，没写"是否保留位次"，我按"出队 + 显示原因"实现，列入第 11 节 E-8 待产品确认。

**验证方式**：① 100 个会员按顺序加入候补 → 断言 `position` 严格递增、取队首结果与加入顺序一致；② 并发 20 个会员同时加入 → 断言队列顺序 = 事务提交顺序（用返回的 `id` 递增校验）、无重复、`waitlist_count` 准确；③ 队首被冻结/时间冲突时 → 断言跳过并顺延到下一位、`skip_reason_code` 正确。

### 6.4 递补的触发时机（"名额不空转"）

**问题**：PRD 6.3 要求"名额释放的原子操作成功后，由服务端在 5 秒内把名额分配给队首"，且"不依赖推送、不依赖运营操作"；P0-④ 第 5 条把它写成硬验收标准；风险表里还有一条"名额空着但队列没动"。

**方案：两段式 + 收敛型 Worker**

- **第一段（释放，同步、短事务）**：会员取消 → 事务内完成 `booking` 状态、`booked_count - 1`、`violation`/`freeze`、**插入一条 `release_event(consumed_at = NULL)`**、审计。事务提交后**立即**调用一次 `promoteOnce(sessionId)`（进程内函数调用，不等待、失败不影响取消结果）。
- **第二段（递补，独立事务）**：`promoteOnce(sessionId)`：
  1. 开事务，锁 `session` 行；
  2. 取一条未消费的 `release_event`（`FOR UPDATE`）；
  3. 若无 → 提交退出（可能已被别的调用消费，正常）；
  4. 校验 session 状态（`cancelled` → 关闭整个队列；`finished` / 距开课 ≤ 10 分钟 → 停止递补，把事件置为"无人可递补"）；
  5. 循环取队首：校验通过 → 该 `waitlist` 置 `pending_confirm`、写 `pending_since = now()`、`confirm_deadline = LEAST(now() + interval '<waitlist_confirm_minutes> minutes', session.start_at - interval '10 minutes')`、`release_event.consumed_by_waitlist_id` 写入、`consumed_at = now()`、插入 `notify_task`（唯一键兜幂等）、审计；校验不通过 → 该记录置 `skipped`，继续下一位（最多循环 N=队列长度，防死循环）；
  6. 队列空 → 事件置"无人可递补"（`consumed_at` + 备注"队列为空"）并**告警**（这意味着名额会空置，PRD 关注这个）。
- **兜底（Worker `promote`，每 5 秒）**：扫描"存在未消费的 `release_event`"或"`session.booked_count < capacity` 且存在 `waiting` 队列 且 `start_at > now() + 10min` 且无 `pending_confirm`"。这保证了：**即使第一段的即时调用失败（进程崩溃、异常），5 秒内也会被 Worker 收敛**。

**为什么是"状态收敛"而不是"事件驱动 + 消息队列"**：① 收敛型逻辑是**幂等**的——重复执行只是再查一遍"该不该递补"；事件驱动要实现"恰好一次"就必须上事务性发件箱 + 幂等消费，等于自己实现 outbox，而我们要的语义其实只是"最终把名额给人"；② 出问题时（PRD 要求可回滚）我能直接 `SELECT` 看到"哪条释放事件没被消费"，而不是去 MQ 控制台猜消息去哪了；③ 少一个中间件就少一类运维故障。**代价**：最坏延迟 = 轮询间隔（5 秒），且轮询会产生稳定的轻量空查询（走索引，期望 0 行）。这与 PRD 的"5 秒内"要求一致。

**为什么不是"在取消事务里直接递补"**：取消事务会变长（要循环取队首、可能要跳过多个人、要插任务），事务越长锁越久，热点课次的取消会被排队；而且递补里的失败（会员被冻结等）会让"取消"这个用户动作也跟着失败——**用户取消必须成功**。两段式把"用户动作"和"系统协调"解耦，代价是引入了"短暂最终一致"窗口（≤5 秒，且由 `release_event` 保证不丢）。

**验证方式**：① 集成测试：取消后不调用任何 Worker，仅靠 `promoteOnce` → 断言 5 秒内出现 `pending_confirm` 与 `notify_task`；② 杀掉进程后重启 → 断言 Worker 在 5 秒内收敛（模拟崩溃恢复）；③ 重复调用 `promoteOnce` 10 次 → 断言只产生 1 条 `pending_confirm`、1 条 `notify_task`、名额不多不少；④ 队列里全是"被冻结"会员 → 断言事件被置为"无人可递补"并产生告警。

### 6.5 递补确认超时与顺延（重新计时、不晚于开课前 10 分钟）

**方案**：Worker `expire`（每 10 秒）：
```sql
-- 用数据库时间判定，不用应用时钟
SELECT id, session_id FROM waitlist
 WHERE status = 'pending_confirm' AND confirm_deadline <= now()
 ORDER BY confirm_deadline
 LIMIT 50;
```
逐条处理（每条一个事务，锁 `session` 行）：
1. `waitlist` → `expired`，`skip_reason_code='confirm_timeout'`；
2. 插入 `release_event(cause='promote_expired')`；
3. `notify_task` → `expired`（`closed_at = now()`）；
4. 立即调用 `promoteOnce(session_id)`（顺延或停止）。

**关键规则**（PRD 明确定义，这里落成实现细节）：
- 顺延后的下一位 `confirm_deadline = LEAST(now() + <N> 分钟, start_at - 10 分钟)`——**重新计时，但永远不晚于开课前 10 分钟**；
- 若 `now() ≥ start_at - 10 分钟` → 停止递补：名额不再分配，队列中剩余 `waiting` 记录置 `expired(reason='promote_stopped')`，会员端显示"本次未排上"（PRD P0-④ 第 14 条在 `session_state` 任务里做最终清尾）；
- **判定误差**：实际顺延时刻 = `confirm_deadline` + [0, 10 秒]（轮询间隔）。这是刻意接受的：PRD 只要求"超时后顺延"，没有要求秒级精确；误差只影响"会员可能多拿到 ≤10 秒"，对会员有利，不会造成状态不一致。若将来要精确，用 PG `LISTEN/NOTIFY` 在写入 `confirm_deadline` 时唤起 Worker（不改数据模型）。
- `expired` **不计违约**、不影响候补资格（PRD 6.2），只在 `waitlist` 上留痕（放弃次数可由统计查询得出）。

**验证方式**：把 `waitlist_confirm_minutes` 配置为 0（测试用）→ 断言自动顺延依次进行、每位的 `confirm_deadline` 都满足"≤ 开课 -10 分钟"、最终队列被清空且无遗留 `pending_confirm`；把配置改为 15 与 30 → 断言只影响时限，流程不变（**这就是 T-03 只用改配置的验证**）。

### 6.6 取消与递补的事务一致性

**问题**：PRD 要求"取消成功且该课程存在候补队列时，服务端在 5 秒内完成流转，不出现名额空着但队列没动的中间态"；P0-④ 第 5 条同样要求。同时"取消"是会员的一次操作，不能因为它顺带要做的事失败而失败。

**方案：两段式 + `release_event` 作为唯一凭据（outbox 模式的最小实现）**

| 事务 | 内容 | 一致性保证 |
|---|---|---|
| T1 取消（同步，短） | `booking` → `cancelled_*`；`booked_count - 1`；违约/冻结；**插入 `release_event(consumed_at=NULL)`**；审计 | 这几件事**必须**原子：否则会出现"名额已释放但没记录释放事件"（Worker 再也发现不了）或"事件存在但名额没释放"（Worker 会白分配）。`release_event` 与 `booked_count` 同事务，正是为了二者不可分割 |
| T2 递补（同步触发 + Worker 兜底） | 消费 `release_event`：队首 → `pending_confirm` + `confirm_deadline` + `notify_task` | 消费是"标记 `consumed_at` + 修改队列状态"的同一事务；重复执行因 `consumed_at IS NOT NULL` 与 `uq_notify_task` 而无副作用 |

**关键取舍**：放弃"T1 与 T2 合并成一个事务"（强一致），换取"取消永远成功 + 事务短 + 崩溃可恢复"。**代价**：存在 ≤5 秒的最终一致窗口，且**必须**有 Worker 兜底（把它当成 P0 的必要组件，不是可选优化）。**为什么可以接受**：会员感知不到 5 秒；PRD 自己给的就是"5 秒内"而不是"同一事务"；而且 PRD 的风险表明确把"名额空转"当成要避免的故障，`release_event` 就是它的可观测表达。

**回滚/人工处置路径**（PRD 10.2 要求可回滚）：出现异常时，运营/工程师可以：
- 查"未消费的 `release_event`"定位卡住的名额；
- 用 `POST /admin/sessions/:id/detail` 看到队列与事件；
- 紧急情况下由后台"代为转正"（P0-⑦ 已有能力）把名额给人，或把事件置为"无人可递补"。

**验证方式**：① 取消 + 立即查 `release_event` → 断言必然存在且 `consumed_at` 为空；② 在 T1 提交后、T2 执行前注入异常（集成测试里用测试钩子）→ 断言 Worker 兜底完成递补；③ 并发"取消 + 另一个会员预约这门课" → 断言只有一个拿到该名额，且 `booked_count` 与 `booking` 条数一致。

### 6.7 幂等（重复点击、网络重试、并发同 ID）

**问题**：PRD 多处要求幂等：预约（连点/重试只 1 条）、取消（违约只 +1）、候补确认（只转正 1 次）、签到（只 1 条，时间取首次）、开关变更（只 1 条留痕）。

**方案：三层，每层解决不同问题**

| 层 | 机制 | 解决 |
|---|---|---|
| L1 请求层 | `X-Request-Id`（客户端生成 UUID）+ `idempotency` 表，`UNIQUE(actor, scope, request_id)`。事务内先 `INSERT ... ON CONFLICT DO NOTHING`：插入成功 → 该请求是首次，继续执行业务，提交时把结果写入该行；插入失败 → 已有记录：若 `succeeded/failed` → 返回首次响应（加 `X-Idempotent-Replay: true`），若 `in_progress` → 返回 `409 DUPLICATE_IN_PROGRESS`（客户端 1 秒后重试） | 连点、网络重试、并发同 ID |
| L2 领域层 | 业务状态判定：如"已有有效预约 → `ALREADY_BOOKED`"、"已签到 → 200 返回首次记录"、"已取消 → 200 不重复计数" | **不同** `X-Request-Id` 但意图重复（用户换了设备再点一次） |
| L3 结构层 | `uq_booking_active`、`uq_waitlist_active`、`UNIQUE(sign_in.session_id)`、`uq_notify_task`、`UNIQUE(invite_code.code)`、`UNIQUE(violation.booking_id)` | 兜住所有"代码漏判"与竞态；捕获 PG 错误码 `23505` 并映射为对应的业务错误/幂等响应 |

**实现要点（容易做错的地方）**：
- 幂等行与业务写入**必须在同一事务**，否则会出现"幂等行说成功了，但业务回滚了"（下次重放会返回一个不存在的成功结果）。做法：业务异常 → 回滚整个事务（幂等行一起回滚）→ 向前端返回错误；这是正确的，因为"失败的请求不占用 requestId，客户端可换新 ID 重试或重发同 ID"。
- **失败的响应也要落幂等记录**（在同一事务里以 `status='failed'` + 响应体提交）——否则"客户端超时 + 服务端其实成功了"这类场景无法被正确重放。这里有一个取舍：业务错误（如 `SESSION_FULL`）也写幂等记录，会让用户在"重试"时看到同样的 `SESSION_FULL`（即使后来有名额了）。**解决办法**：只有写操作**成功**与**明确不可重试的业务错误**（`FROZEN`、`NO_ACTIVE_CARD`、`SESSION_CANCELLED`）落幂等记录；**可重试的竞争失败**（`SESSION_FULL`、`TIME_CONFLICT`、`SEAT_UNAVAILABLE`）**回滚幂等行**，让用户重试能拿到新结果。这条规则要写进代码注释与测试。
- `request_id` 的生成：小程序网络层统一生成（每个逻辑动作一个新 UUID），**重试复用同一个 ID**（这才是幂等生效的前提）。要写进前端开发约定。

**验证方式**：对每个写接口做三组测试：同 ID 串行重放（10 次）→ 断言业务只生效 1 次、响应体一致；同 ID 并发（10 并发）→ 断言 1 个成功 + 9 个 `DUPLICATE_IN_PROGRESS` 或重放结果，业务只 1 条；不同 ID 重复意图 → 断言 L2/L3 生效，返回明确业务码。

### 6.8 定时任务清单（正确性靠幂等，不靠"只跑一次"）

**执行方式**：同进程 `setInterval`；每个任务用 `pg_try_advisory_lock(<固定 key>)` 保证全站同一时刻只有一个实例在执行（多实例部署时其余实例跳过本次）。任务内逐条处理、每条一个短事务、可安全重入。**每次执行记录**：任务名、扫描行数、处理行数、耗时、异常（写日志 + `audit_log(action='system.*')`）。

| 任务 | 间隔 | 内容 | 幂等保证 |
|---|---|---|---|
| `promote` | 5s | 消费未消费的 `release_event`；补齐"有名额 + 有队列 + 无待确认"的课次 | `consumed_at` + `uq_notify_task` + session 行锁 |
| `expire` | 10s | 超时的 `pending_confirm` → `expired` + 顺延 | 状态条件更新（`WHERE status='pending_confirm'`） |
| `session_state` | 30s | 到点 `published → finished`；开课时刻清尾：把未成功的 `waiting`/`pending_confirm` 置 `expired`（PRD P0-④ 第 14 条）；置 `notify_task` 终态 | 条件更新 + 状态机单向 |
| `freeze_expiry` | 1min | `freeze` 到期 → `expired`（代价：不影响"是否冻结"的判定，判定用 `frozen_until > now()`，所以任务漏跑也不会误冻） | 幂等；判定不依赖任务 |
| `reconcile` | 5min | 4.5 的对账与超卖告警 | 纯查询，天然幂等 |
| `import_cleanup` / `idempotency_cleanup` | 1h | 清理过期 `coach_invite_code`（可选标记）、`idempotency`（>24h）、`auth_throttle` 过期窗口 | 删除类任务按时间条件，天然幂等 |
| `notify`（P1） | 5s | 消费 `notify_task` 中待发送的订阅消息 | 发送结果写回 + 错误重试上限 |

**心跳与可观测**：每轮任务更新一行 `worker_heartbeat(name, last_run_at, last_duration_ms, last_error)`；`/readyz` 检查心跳新鲜度（> 60 秒未更新即不健康）——**这是"定时任务悄悄死了"的检测手段**，PRD 风险表里"名额空转"最可能的根因就是它。

**为什么不需要"违约记录过期"任务**：见 4.3.13，滚动窗口是查询条件。

### 6.9 权限、越权与数据范围（PRD 每条验收标准都有 403）

**方案**：统一中间件产出 `scope = {actorType, actorId, role, storeId|null}`：
- 会员/教练：`scope.actorId` 即会员/教练 ID；所有仓储查询**必须**带 `member_id = scope.actorId` 或 `coach_id = scope.actorId`（不用"先查出来再在代码里比"——那是漏判的来源）。
- 门店运营：所有后台查询强制注入 `store_id = scope.storeId`；写操作的目标数据先 `SELECT ... WHERE id=$1 AND store_id=$2`，查不到 → 403（**不返回"存在但无权"与"不存在"的区别**，减少枚举面）。
- 总部管理员：`storeId = null` → 不加门店条件。
- 任何 403/越权尝试写 `audit_log(action='auth.forbidden')`，同一条中间件路径（不用每个 handler 记得写）。
- 单测/契约测试：为每个后台接口写"他店数据 → 403 + 留痕"的用例（测试同学可以按这条审）。

### 6.10 时间与时区（判定口径统一）

- 所有"当前时间"判定在 SQL 中完成（`now()` / `CURRENT_TIMESTAMP`），**领域内核接收 `now` 作为参数**（便于单测与"配置为 0 分钟"的时间旅行测试）。
- 前端不做任何"现在有没有过截止时间"的判定（只做展示与轮询）。
- "自然日"筛选按门店时区：`WHERE (s.start_at AT TIME ZONE st.timezone)::date = $date`。
- 文案与列表的绝对时间统一带门店时区偏移输出（`+08:00`），前端不做本地时区换算。
- **NTP**：服务器启用时间同步（`timedatectl` 校验），DB 与 API 在同一台机器 → 时差问题被消掉。这也是 ADR-9 "只信 DB 时间"能成立的前提。

### 6.11 课程导入的原子性与教练匹配（P0-⑥ 的硬要求）

- 整批导入在**一个事务**内：解析 → 校验每一行（字段格式、教练手机号能否在**本店**档案中匹配、时间格式、容量 > 0、同教练同时间段冲突）→ 任何一行失败 → 整体回滚，返回全部问题行（行号 + 手机号 + 原因码）。
- **绝不允许"静默丢弃未匹配行"**（PRD 明确）：未匹配行必须在响应里逐行列出，并写进 `course_import_batch.unmatched_rows` 供运营复盘与导出。
- 先提供 `dryRun=true` 让运营在上传正式数据前看到问题清单（体验上很重要：48 行里错 1 行要重传，必须先预览）。
- 去重：`external_id = sha256(文件名 + 行号 + 开始时间)`，配合 `uq_session_external` → 同文件重复导入无副作用；同一外部 ID 但内容变化 → 更新（含 `status` 变 `cancelled` → 触发"课程取消"处理：不发预约、队列作废、`booking` 置 `cancelled_by_store`）。

---

## 7. 与微信能力的对接

### 7.1 登录（P0 必须）

| 步骤 | 小程序 | 服务端 |
|---|---|---|
| 1 | `wx.login()` → 拿 `code`（5 分钟有效、一次性） | — |
| 2 | `POST /auth/wechat/login {code, phoneCode?}` | 调 `https://api.weixin.qq.com/sns/jscode2session?appid=&secret=&js_code=&grant_type=authorization_code` → `openid`（+ `session_key`） |
| 3 | 存 JWT | 按 `openid` upsert `member`；签发自有 JWT（`typ=member`）；若 `coach.openid` 命中则同时给 `coachId` |

要点：
- **`appid`/`secret` 从环境变量读**，不写进代码、不进日志（规则 6）。`session_key` **不下发客户端、不落库**（P0 用不到解密能力，因为我们不解密用户信息；手机号用 `phoneCode` 换取）。若将来要做加密数据解析，再评估是否落库并加密存储。
- `code` 是一次性的：服务端返回 `WECHAT_CODE_INVALID` 时，小程序必须重新 `wx.login` 再试（网络层统一处理，最多重试 1 次）。
- JWT：HS256，密钥环境变量；有效期 7 天；无 refresh token（过期即静默重登，会员无感）。**不使用微信自定义登录态**（我们自己签发，便于后台复用同一套鉴权）。
- 教练身份**不靠昵称/手机号**匹配（PRD 6.5 明确禁止宽松匹配）：只认 `coach.openid` 唯一索引命中。

### 7.2 手机号获取（P0 依赖）

- 优先：`<button open-type="getPhoneNumber" bindgetphonenumber>` → 拿动态 `code` → 服务端调 `phonenumber.getPhoneNumber` 换取手机号（**需要小程序已认证且类目允许**，见 E-2）。
- 兜底（若类目/权限拿不到）：后台由运营代录手机号 + 会员端展示脱敏号码让会员确认。**这条兜底必须留，否则绑定流程会卡死**（PRD T-08 的会员身份校验依赖手机号）。
- 手机号在日志与后台列表按 `138****1234` 脱敏；导出/查看完整号码需要 `hq_admin` 或按门店授权（PRD 运营需要电话触达，所以门店运营可看本店会员完整号码，但**每次查看写审计**——这是我对 PRD 的加固，见 E-6）。

### 7.3 订阅消息（P1-⑨⑩，P0 只留扩展点）

- **P0 不做推送**（PRD 明确），因此 P0 **不申请、不使用**订阅消息能力；`notify_task` 就是 P0 的触达载体。
- P1 的实现路径（**不改 P0 数据模型**）：
  1. 会员在小程序内调用 `wx.requestSubscribeMessage({tmplIds:[...]})` 授权（一次性订阅，每次授权可发 1 条）；授权动作记录在 `member_subscription` 表（P1 新增：`member_id, template_id, granted_at, consumed_at`）。
  2. Worker `notify` 扫描 `notify_task`（P1 增加 `channel`/`send_result` 列），对状态为 `pending` 且会员有可用授权的任务调用 `subscribeMessage.send`：
     `POST https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=...`，`{touser, template_id, page, data}`。
  3. 返回 `errcode` 处理：`40001/42001`（token 失效）→ 刷新 token 重试 1 次；`43101`（用户拒绝/未授权）→ 标记"触达失败"，写入 `send_result` 并计入"运营未触达"统计（PRD P1-⑨ 要求向运营报警）；`45009`（频率限制）→ 退避重试。
  4. **失败不影响流程**：递补的超时与顺延完全由 P0 的服务端机制负责，推送只是"更快知道"。
- **模板内容**：需要 3 个模板——"候补有位/请确认"、"预约成功提醒"、"递补即将过期"。字段（`thing`/`time`/`phrase` 之类）需与运营一起在微信后台申请，**申请要提前做**（T-11），因为审核周期不可控。
- `access_token` 管理：DB 一行 `wechat_access_token(token, expires_at)` + 进程内缓存，提前 5 分钟刷新；多实例用 `pg_try_advisory_lock` 避免并发刷新（刷新接口有频率限制）。

### 7.4 其他小程序侧对接

| 能力 | 用途 | 注意 |
|---|---|---|
| tabBar 红点 | "我的候补"有待确认时提示（PRD P0-④ 第 6 条） | `wx.showTabBarRedDot`；进入小程序与 `onShow` 时调 `GET /waitlist/pending-count`；**不做长连接**（成本高、收益低），改为进入页面时轮询（15 秒）+ 下拉刷新 |
| `onShow` 刷新 | 从通知/后台切回小程序时刷新"我的候补" | 因为待确认状态会在服务端变化，页面停留期间数据会过期 |
| 合法域名 | `request` 白名单需配 `https://api.<domain>` | 必须是 HTTPS + 备案域名（E-2） |
| 隐私协议 | 手机号属于个人信息 | 需在小程序配置"用户隐私保护指引"，声明手机号用途（绑定会员卡与上课通知） |
| 分享/转发课表 | **MVP 不做**（不在 PRD 范围） | 避免引入分享带参越权 |
| 微信支付 | **MVP 不做**（PRD 明确不接资金） | 无 |

---

## 8. 质量属性与运维

### 8.1 可靠性（失败时怎么办）

| 失败 | 行为 | 依据 |
|---|---|---|
| 微信接口（code2Session/getPhoneNumber）超时 | 返回 `503 WECHAT_API_UNAVAILABLE`，客户端可重试；**登录失败不写半成品会员** | 无静默降级，避免"假登录" |
| 订阅消息发送失败（P1） | 标记 `send_result`，不影响递补流程，计入"触达失败"统计 | PRD P1-⑨ |
| Worker 未运行 | 递补/超时/清尾停止 → **这是最严重的降级**。由 `/readyz` 心跳检查 + 告警兜底；`release_event` 未消费数作为仪表盘指标 | PRD 回滚条件之一 |
| DB 不可用 | 全站 503（课表也无法提供）；**不引入缓存意味着没有"读降级"**——这个取舍是刻意的：宁可明确报错，也不要展示过期名额（会员按过期数据跑到门店，是更差的体验） | 与 ADR-5 一致 |
| 抢位竞争失败 | 明确业务错误 + 引导候补（PRD P0-② 要求） | PRD P0-② |
| 递补确认时名额已不可用 | `SEAT_UNAVAILABLE` + 服务端重新判定队首，不留"待确认但无名额" | PRD P0-④ 第 8 条 |
| 重复提交 | 幂等返回首次结果 | 第 6.7 节 |

### 8.2 安全

- **认证**：JWT（HS256）+ 密钥环境变量；后台登录用验证码 + 失败锁定；邀请码一次性 + 24h + 防暴力。
- **授权**：角色 + 门店范围在中间件统一注入，仓储查询强制带范围（6.9）；越权 403 + 留痕。
- **输入校验**：HTTP 边界 JSON Schema（`additionalProperties: false`）；导入文件限制大小（≤ 2MB）与行数（≤ 2000）、只接受 xlsx/csv、解析时不执行任何公式（用 `exceljs` 读取值）；文件名不参与路径拼接。
- **注入**：全部走 Kysely 参数化，**禁止字符串拼 SQL**；`ORDER BY`/表名等不能参数化的地方用白名单映射。
- **敏感数据**：`appid/secret/JWT 密钥/DB 密码` 只从环境变量读（`.env` 不提交、`.env.example` 只放占位符）；日志打印手机号、openid 一律脱敏；错误响应不回显堆栈与 SQL。
- **最小权限**：DB 应用账号只有必要的 `SELECT/INSERT/UPDATE/DELETE`（**无 `DROP/ALTER`**，迁移用另一个账号）；导出接口有权限 + 审计 + 限流。
- **限流**：登录/绑定/邀请码/导出接口限流（`@fastify/rate-limit` + `auth_throttle`）。
- **审计**：所有后台写操作、越权尝试、规则变更、解冻、补签、转归属、导入都留痕（4.3.16）。

### 8.3 性能估算（**全部是我的假设，不是实测**）

**假设**（在没有真实数据的前提下，我用它做容量判断；请业务方按实际修正）：
- 门店数 10；每店每天 20 节课次；每店会员 5000；日活率 10% → 全站日活 5000。
- 每人每天打开小程序 2 次、每次 10 个请求（课表 + 我的预约 + 我的候补等）→ 全站 **10 万请求/天**。
- 请求集中在 3 个小时（早 9 点、晚 18–20 点），峰值系数取 20 → **峰值约 25 QPS**；单节课抢位峰值：一节 20 人课程，开课前 1 分钟内 20–40 个并发。

| 量级 | 估算 | 结论 |
|---|---|---|
| API 峰值 | ~25 QPS（普通查询） | Node 单实例轻松（数千 QPS 级） |
| 抢位热点 | 单课次 ≤ 40 并发，单事务 < 10ms，`session` 行锁串行化 → 单课次上限约 100 次/秒 | **余量 2 倍以上**；瓶颈是行锁而非 CPU |
| 课表查询 | 单门店单日 < 100 行，走 `ix_session_store_start`，索引扫描 | P95 目标 ≤ 500ms（PRD 指标）有充分余量 |
| 数据量 | `booking` 每天 ~5000 行 → 每年 ~180 万行；`waitlist` 更少 | 单表 PG 无压力；P2 再加归档（按 `start_at` 分区或冷表） |
| Worker 轮询 | `promote` 每 5 秒一次走索引（`consumed_at IS NULL` 通常 0 行）、`expire` 每 10 秒一次 | 对 DB 是噪声级负载 |
| 存储 | 一年数据（含审计）估计 < 5GB | 单机数据卷足够；每日 `pg_dump` 备份到对象存储 |

**瓶颈与扩容顺序**（写进运维手册，避免盲目加组件）：
1. 加索引/优化 SQL（课表、我的预约）；
2. API 水平扩多实例（Worker 有 advisory lock，天然安全）；
3. DB 升配 / 加只读副本（课表查询读副本）；
4. 最后才考虑缓存（判据见 3.1）。

### 8.4 可观测性（出问题靠什么定位）

| 手段 | 内容 |
|---|---|
| 结构化日志 | `pino` JSON；每条含 `requestId`、`actorType/actorId`、`route`、`statusCode`、`errorCode`、`durationMs`。日志脱敏手机号/openid |
| 业务事件日志 | 每次预约/取消/加入候补/转正/签到/递补/顺延/冻结/解冻打一条**业务语义**日志（含 `sessionId`、`waitlistId`、`bookingId`、原因码），便于"某会员说约不上"时 30 秒内定位 |
| 关键指标（`/internal/metrics` + 仪表盘） | ① `release_event` 未消费数（**最重要**：> 0 持续 1 分钟即告警）；② `waitlist` 处于 `pending_confirm` 的数量；③ 超时顺延率；④ 未通知即过期的任务数；⑤ 预约失败码分布；⑥ `reconcile` 不一致行数（> 0 立刻告警）；⑦ Worker 心跳；⑧ 签到缺席数 |
| 告警阈值（建议） | `release_event 未消费 > 0` 持续 1 分钟；`reconcile 不一致 > 0`；Worker 心跳 > 60 秒未更新；接口错误率 > 1% 持续 5 分钟（PRD 的回滚条件之一是 >1% 持续 30 分钟，告警要更早）；DB 连接数 > 80% |
| 排障入口 | 后台"课次详情"已能看到预约/候补/签到/释放事件；再加一个只读的"按会员查看"（P0 后台顺手做，运营天天要用：会员说"我约不上"，直接搜手机号看到他的预约、候补、违约、冻结） |

### 8.5 可维护性

- 领域内核纯函数 + 单测覆盖全部规则数值分支（这类测试可以逐条对照 PRD 的 Given/When/Then，测试同学审起来最省事）。
- 一个模块一个目录，公共错误码/类型/工具各一处；跨模块调用只走应用服务层，禁止直接调别的模块的仓储。
- 迁移文件只增不改（新增一条迁移而不是改旧的），保证任何环境能从零重建。
- ADR（第 9 节）与 README 一起维护：改关键决策必须补一条 ADR。

---

## 9. 决策记录（ADR）

> 格式：背景 / 决策 / 备选方案 / 影响与代价。

### ADR-1：模块化单体，不拆微服务
**背景**：8 个功能模块，但预约↔名额↔候补↔违约之间有强事务耦合；团队只有 1 名工程师。
**决策**：一个可部署单元（含 API 与 Worker），模块内按领域分目录，依赖方向单向。
**备选**：(a) 微服务（预约服务/候补服务/通知服务）——把"不允许超卖""名额不空转"从数据库保证降级为跨服务补偿，运维与调试成本远超收益；(b) 一个大文件——PRD 规则密度高，测试要逐条对照验收标准，无法维护。
**影响与代价**：单点部署（可用多实例缓解）；模块边界靠代码规范而非进程隔离；唯一预留的拆分点是 Worker（已是独立入口）。

### ADR-2：小程序用原生框架
**背景**：只在微信内运行，无 App/H5 需求。
**决策**：原生小程序 + TypeScript，不用跨端框架。
**备选**：Taro / uni-app（跨端收益为 0，引入编译器与插件兼容成本）；小程序 + 自建 webview（体验与审核风险）。
**影响与代价**：若将来要出 H5 版会员端，视图层需重写（接口层与类型可复用）。

### ADR-3：后端 Node.js + TypeScript + Fastify
**背景**：规则密集、IO 型负载、一人维护、与小程序/后台共享类型。
**决策**：Node 20 LTS + TS strict + Fastify + Kysely + node-pg-migrate。
**备选**：NestJS（抽象过重）、Java/Spring（部署与开发成本）、Go（类型无法共享、语言数量增加）。
**影响与代价**：需要自己维护少量基础设施（日志、错误映射、迁移），换来可读性与可调试性；CPU 密集场景不在本项目内。

### ADR-4：数据库选 PostgreSQL 16
**背景**：核心需求是"约束与并发正确性"：同一会员同课次唯一有效记录、名额不超卖、队列保序、行锁串行、时间判定统一。
**决策**：PostgreSQL 16 单库。
**备选**：MySQL 8（同样可行，但"有效记录唯一"要靠 `active_flag` 置 NULL 的技巧，可读性差、易被改错；部分索引与 `CHECK` 表达力弱）；MongoDB（放弃事务与约束的强项）。
**影响与代价**：托管成本略高；团队需熟悉 PG 的 `READ COMMITTED` 重查语义与 advisory lock（写在本文档与 README 里）。

### ADR-5：不引入 Redis，不引入独立消息队列
**背景**：两处候选用途——课表/配置缓存，以及递补的异步协调。
**决策**：都不用。缓存用进程内 60 秒（配置项）；异步用"表 + Worker"（`release_event` / `notify_task` 作为 outbox）。
**备选**：Redis 缓存课表（制造第二真相，且有"过期名额"风险）；Redis 锁（锁失效会变成偶发数据错误）；MQ（需要事务性发件箱才能不丢消息，等于用 MQ 实现 outbox）。
**影响与代价**：轮询延迟（最坏 = 间隔，5–10 秒，满足 PRD 的 5 秒要求需任务间隔 5 秒）；轮询对 DB 有稳定轻量负载；**升级判据已写进 3.1 与 8.3**（达到即升级，不是"永远不用"）。

### ADR-6：名额用 `session.booked_count` 冗余计数 + 条件更新
**背景**：抢位必须"一次原子操作完成"，且要能立刻判断"是否满员"。
**决策**：`booked_count`（含 `waitlist_count`）由原子条件 `UPDATE` 维护，配 `CHECK (0 ≤ booked_count ≤ capacity)`；与 `booking` 表的一致性由同事务 + 定时对账保证。
**备选**：每次 `COUNT(*)`（要加锁、慢、且无法单语句原子）；Redis 计数（第二真相 + 补偿复杂）。
**影响与代价**：多了一个需要维护一致性的冗余字段（用对账兜住）；容量不可下调到低于已约人数（MVP 不做容量调整，若将来要做需放宽 CHECK 并加校验，属可逆改动）。

### ADR-7：候补排序用全局单调主键 `id`
**背景**：FIFO 需要"同毫秒也确定顺序"，且不能在写路径上加锁。
**决策**：`waitlist.id`（bigserial）即排序键；`join_at` 只用于展示。
**备选**：毫秒时间戳（同毫秒并列，需要额外 tie-breaker）；每课次自增序号（要锁 `session` 行或维护计数，与抢位事务争同一把锁）；`created_at`（精度与并列问题同前）。
**影响与代价**：不同课次的序号不连续（对用户不可见）；依赖"序列单调"这一 PG 特性（标准行为）。

### ADR-8：递补用"两段式 + 收敛型 Worker"，不用"取消事务内递补"
**背景**：PRD 要求 5 秒内完成递补，同时"取消必须成功"、要能回滚、要可排查。
**决策**：取消事务只做释放 + 落 `release_event`；递补由"提交后立即触发一次 + 每 5 秒兜底扫描"完成，且整个递补是**幂等可重入**的。
**备选**：(a) 同一事务内递补——事务变长、用户动作可能因系统协调失败而失败；(b) MQ 事件驱动——需要事务性发件箱与幂等消费，复杂度更高且难排查。
**影响与代价**：引入 ≤5 秒的最终一致窗口；**必须有 Worker 兜底**（列为 P0 必要组件）；换来"取消永远成功、崩溃可恢复、状态可查可修"。

### ADR-9：所有业务时间判定用数据库时间
**背景**：多实例部署、超时判定必须"唯一客观"（PRD 6.3 明确"服务端时刻"）。
**决策**：判定用 `now()`（SQL 内）；领域内核接收 `now` 参数；前端只展示、不判定；返回 `serverTime` 供前端校时。
**备选**：应用层 `Date.now()`（多实例时钟漂移会导致"同一会员在两个页面上看到不同的剩余时间"，甚至"未超时被判超时"）。
**影响与代价**：领域单测需注入时间（反而更好测）；要求服务器启用 NTP。

### ADR-10：时间冲突校验用"会员行锁"，不用 `EXCLUDE` 约束或 Redis 锁
**背景**：同一会员同一时段只能有 1 个有效预约，且"预约 + 候补"两条路径都要校验。
**决策**：写事务先 `SELECT ... FOR UPDATE` 锁 `member` 行，再做区间重叠查询（`tstzrange &&`）。
**备选**：`EXCLUDE USING gist`（需要把课次时间冗余到 `booking`、要触发器、规则变化需重建索引）；Redis 锁（第二真相）。
**影响与代价**：同一会员的写请求串行（本来就是对的语义）；必须遵守"锁顺序 member → session"与"禁止乱序批量锁"的规范（已写入第 6.2 节）。

### ADR-11：单机 Docker Compose 部署
**背景**：一个 API 服务 + 一个 DB；无运维团队；需要可回滚、可复现。
**决策**：`nginx` + `api` + `postgres` 三容器，数据卷持久化，每日 `pg_dump` 到对象存储；镜像为标准 OCI 镜像。
**备选**：微信云托管（免运维、自带 HTTPS；顺位第二，若无人运维则更优）；Serverless 云函数（无长连接/定时精度差，与"5 秒内递补"和事务语义冲突）；K8s（规模不匹配）。
**影响与代价**：自己负责补丁、备份验证、证书续期（写成 runbook）；换来排障自由与"随时可迁移"的可逆性。

### ADR-12：幂等三层（请求层 / 领域层 / 结构层）
**背景**：PRD 在预约、取消、候补确认、签到、开关变更五处要求幂等。
**决策**：`X-Request-Id` + `idempotency` 表 + 唯一约束三层；并**区分"可重试的竞争失败"（回滚幂等行）与"不可重试的业务错误"（落幂等记录）**。
**备选**：仅靠唯一约束（无法返回"首次结果"，用户体验是"点两次看到两个不同错误"）；仅靠领域状态判断（并发同 ID 会插入两条）。
**影响与代价**：多一张表和一套规则；前端必须"重试复用同一 requestId"（写进前端约定，否则幂等不生效）。

### ADR-13：后台用独立 H5（React），不做小程序管理端
**背景**：后台需要表格、批量导入、导出、审计查询；运营在电脑前工作。
**决策**：React + Vite + AntD 的独立 H5，与 API 同域；登录用手机号 + 验证码 + 2 角色。
**备选**：小程序管理端（表格/导入/导出体验差，且要过审）；复用现有后台系统（T-13 未确认；若存在则本方案的后台退化为"嵌入/对接"，ADR 需重估）。
**影响与代价**：多一个前端工程与部署产物；换来后台功能可靠交付。

### ADR-14："未签到/缺席"派生，不落库
**背景**：PRD 要求"超过窗口未签到标记为缺席/异常"。
**决策**：查询时计算（`start_at + 15min < now()` 且无 `sign_in` 行）；补签由总部管理员手动落 `sign_in(source='admin_manual')`。
**备选**：定时任务写"缺席记录"（多一个必须成功的任务；且容易被人为改为"已签到"，破坏审计）。
**影响与代价**：报表查询需要 LEFT JOIN 计算（可接受）；好处是"签到事实"只有一处、不可伪造。

### ADR-15：规则数值放 `rule_setting`，机制参数放环境变量
**背景**：PRD 多个数值待业务确认（T-03 的 15/30 分钟、T-05 的 20 人、T-07 的窗口等），且 P0-⑧ 要求开关可在后台改并留痕。
**决策**：业务数值进 `rule_setting`（可后台改 + 留痕 + 进程内 60 秒缓存 + 写后失效）；批处理间隔、重试次数等机制参数进环境变量。
**备选**：全部硬编码（T-03 一改就要发版）；全部放配置中心/Redis（多一个组件）。
**影响与代价**：需要一条"配置变更"的审计链路（P0-⑧ 本来就要）；好处是**"T-03 若定 30 分钟只改一个值"**，且业务方能自助调整而不用排开发。

---

## 10. 里程碑与依赖顺序

### 10.1 里程碑（每个里程碑都能独立运行与验证；顺序即依赖顺序）

| # | 里程碑 | 内容 | 依赖 | 如何验证（可执行） |
|---|---|---|---|---|
| **M0** | 骨架与地基 | 仓库结构、`docker compose`（nginx+api+postgres）、迁移工具、错误类型与错误码常量、鉴权中间件（JWT + 角色 + 门店范围）、`audit_log`、`idempotency`、日志与请求 ID、`healthz/readyz`、Worker 框架与心跳 | —（但 T-13 影响后台账号模型，见 11 节） | `docker compose up` 起服务；`/healthz` 200；迁移能从零建出全部表与约束；一个示例写接口的幂等与审计集成测试通过 |
| **M1** | 主数据与排课导入 | `store/course/coach/admin_user/rule_setting` 迁移与后台接口；教练档案 CRUD + 邀请码；**课程导入（dryRun + 原子 + 未匹配行清单）**；规则数值读写接口 | M0；**阻塞：T-01（导入格式与谁提供数据）、T-14（绑定方式）** | 用 PRD P0-⑥ 验收标准逐条测：新增教练、越权 403+留痕、导入含未匹配行 → 整批失败且返回行清单、重复导入同一文件不重复建课 |
| **M2** | 课表查询（只读） | `session` 查询 + `uiState` 领域计算 + `GET /sessions`、`/sessions/:id`；`GET /stores` | M1 | 构造覆盖 10 种 `uiState` 的数据集 → 单测逐一断言；接口返回与设计文档一致（对拍字段） |
| **M3** | 预约与取消（含违约与开关） | `SeatAllocator.reserve/release`、`booking`、`violation`、`freeze`、`rule_setting` 开关、`GET /bookings`、`cancel-preview`、`cancel` | M2；T-04/T-07/T-10 影响规则数值（已在配置里给默认值） | ① **并发压测**：`capacity=1` × 20 并发 → 恰好 1 成功、无超卖；② 幂等三组测试；③ 时间冲突并发测试；④ 开关两态下的取消文案与冻结行为（PRD P0-③/P0-⑧ 验收标准） |
| **M4** | 候补与递补状态机 + 运营待通知任务 | `waitlist`、`release_event`、`notify_task`、`promote`/`expire` Worker、会员端 4 个候补接口、后台任务接口 | M3；T-03（时限数值）、T-05（队列上限） | ① FIFO 保序测试（100 人顺序加入 + 并发加入）；② 取消 → 5 秒内 `pending_confirm` + 任务生成；③ 超时顺延（把时限配置为 0 分钟跑）；④ 重复触发不产生第二条任务；⑤ 跳过被冻结/冲突会员并显示原因；⑥ `release_event` 未消费数在正常流程后归零 |
| **M5** | 教练签到 | `sign_in`、教练端绑定与"我的课程"、签到窗口与迟到判定、缺席派生、后台签到视图 | M1（教练档案）；T-06 | PRD P0-⑤ 的 10 条验收标准逐条测（含 `coach getPhoneNumber` 替换为真实邀请码绑定）；重复签到幂等；停用教练登录被拒 |
| **M6** | 后台补齐与权限收口 | 违约会员清单、开关页、人工解冻、补签、审计查询与导出、任务导出、教练"按会员查看" | M3/M4/M5；T-15（权限口径） | 每个后台接口的"他店数据 → 403 + 留痕"用例；开关变更前后值与留痕（含幂等）；导出字段与 PRD P0-⑦/P2 口径一致 |
| **M7** | 试运行与上线门槛 | 阶段 0/1/2 的并行运行（PRD 10.1）、压测复跑、告警与仪表盘接入、备份与恢复演练、回滚演练（关预约入口 + 开关切回只提醒） | M0–M6 | **上线门槛清单**：超卖 = 0、预约接口成功率 ≥ 99%（压测）、`reconcile` 0 行、Worker 心跳正常、回滚演练通过（PRD 10.2） |
| **P1-1** | 订阅消息 | `member_subscription`、`notify_task.channel/send_result`、`notify` Worker、模板申请与联调 | M4（`notify_task` 已在）+ 模板过审（T-11） | 授权率与触达率统计；未授权会员仍能在时限内流转（PRD P1-⑨ 第 4 条） |
| **P1-2** | 预约提醒 | `reminder` 表 + 扫描任务（开课前 2 小时、候补未排上、确认剩余 3 分钟） | P1-1 | 三条提醒的触发时间与文案；未授权不阻塞 |
| **P2** | 报表 | 只读 SQL 视图 + 导出 | 数据积累 2–4 周 | 指标口径与 PRD 2 节一致 |

### 10.2 依赖与关键路径

```
M0 ─> M1 ─┬─> M2 ─> M3 ─> M4 ─> M6 ─> M7 ─> 上线
          └─────────> M5 ────────┘
                          (M5 只依赖 M1，可与 M3/M4 并行——但只有我一名工程师，实际串行，所以 M5 排在 M4 之后)
阻塞项：M1 依赖 T-01（导入数据来源与格式）、T-13（后台账号）；M1 的邀请码绑定依赖 T-14；M4 的数值依赖 T-03/T-05
```

**说明**：单工程师串行执行，M5（教练签到）虽不阻塞主链，但 PRD 把 P0-⑤ 列入 P0，因此**不放到最后**；若工期紧张，可执行的顺序调整是"先 M0–M4 + M5，把 M6 的部分后台页面（导出、审计查询）压到最后"，但**"待通知任务"与"违约开关"两个页面不能省**（PRD 8.2 决策 8 已论述）。

### 10.3 工期与范围（对 CEO 的诚实提示）

- 我不给"人天数字"（没有真实团队与历史速率数据，编出来是假精确）。我给**相对占比**：M0 10%、M1 15%、M2 10%、M3 20%、M4 25%、M5 8%、M6 7%、M7 5%。其中 **M4（候补与递补）是最贵的一块**，因为它有完整的并发、幂等、超时、顺延逻辑；**M1 里"课程导入"是隐藏成本**（格式对齐、原子性、错误行清单、重复导入）。
- 若业务方要砍范围，唯一代偿方案仍是 PRD 8.2 决策 8 说的"递补整体推 P1"。**我同意 PRD 的结论：不建议**——P0 只排队不递补，候补就是空壳，且 M6 的"待通知任务"页也随之失去意义，反而省不了多少工夫（`waitlist` 表和入队/退出逻辑还得做）。
- **真正的压缩空间**在 M6 的导出与审计查询界面（可用 SQL 导出临时替代），以及 M2 的筛选维度（先只做"门店 + 日期"，课程类型筛选放 M2 末尾）。

---

## 11. 待确认问题与技术假设

> 标注 ❗ 的是**开工阻塞项**：没有答案我就按"我的假设"实现，若后续被否定，返工范围写在最后一列。

| 编号 | 问题 | 我的假设（未答复即按此实现） | 若被否定的影响 |
|---|---|---|---|
| ❗E-1（对应 T-03） | 递补确认时限取 15 还是 30 分钟？ | 15 分钟，且 `LEAST(释放时刻 + 15min, 开课 - 10min)`；值放 `rule_setting.waitlist_confirm_minutes` | **只改一个配置值**，无代码改动（已在 6.5 验证方式里覆盖 0/15/30 三种） |
| ❗E-2 | 小程序是否已认证、能否调用 `phonenumber.getPhoneNumber`？HTTPS 域名与备案是否就绪？ | 可以（若不可用，走"运营代录手机号 + 会员确认"兜底） | 若不能取手机号：绑定流程改为后台代录，`member.phone` 可靠性下降，运营电话触达受影响 |
| ❗E-3（对应 T-13） | 后台账号从哪来？ | **新建最小账号体系**（手机号 + 验证码 + 2 角色）；短信服务商先不接，用"白名单手机号 + 固定验证码（写日志）"过渡 | 若要求复用现有系统：M0/M1 的鉴权与账号表要改，M6 的权限收口要重做（返工中等） |
| ❗E-4（对应 T-01） | 排课数据谁提供、什么格式、什么频率？ | 运营提供 Excel（字段见 5.4.3 模板），后台手工导入，**每次导入整批替换未开始的课程** | 若有现成系统：改为对接接口/直连只读库（M1 的导入模块替换为同步器），工作量相当但联调时间不确定 |
| ❗E-5（对应 T-14） | 教练绑定方式？一人多店？ | 6 位一次性邀请码（24h、一次性、防暴力）；MVP 一人一店（`coach.store_id` 单值） | 若一人多店：需加 `coach_store` 多对多表并调整"我的课程"与签到权限校验（返工偏大，建议 MVP 不做） |
| E-6（对应 T-15） | 门店运营能否看本店会员完整手机号？ | 可以看（电话触达是 P0-⑦ 的必要能力），但**每次查看会员详情写审计**；导出默认脱敏，需 `hq_admin` | 若要求全面脱敏：运营任务列表只显示脱敏号，触达能力下降（P0-⑦ 的"代为转正"体验受损） |
| E-7 | 同一手机号绑定多个微信 / 换微信怎么办？ | 一个手机号只能绑一个 `member`（`UNIQUE(phone)`）；换微信由后台解绑（`DELETE /admin/coaches/:id/openid` 是教练的；会员需后台加一个解绑操作，M6 补） | 需要新增会员解绑接口（小） |
| E-8 | 候补被跳过（时间冲突/冻结）后是否保留位次？ | **出队（`skipped` 终态）+ 显示原因**，需重新排队 | PRD 未定义；若要求保留位次，需引入"暂停/恢复"状态与恢复时的时间校验（中等返工，`waitlist` 状态机改动） |
| E-9 | 会员在已冻结期间再次违约，是否延长冻结/叠加？ | 不叠加：`freeze` 只有一条 active，等它到期；违约照常计数 | 若要求叠加：`freeze` 判定加"延长"逻辑（小），但要防"越罚越久"的产品争议，需业务明确 |
| E-10 | 同一会员同一时段能否同时"预约一门 + 候补另一门"？ | **不允许**（时间冲突校验对预约与候补一视同仁，PRD T-04 的倾向） | 若允许：递补成功时需要"放弃另一门"的处理路径，状态机更复杂（建议不允许） |
| E-11 | 会员在场馆外/异地能否预约？（无定位校验） | 可以（PRD 第 3 节不做 GPS） | 若要限制，需要门店地理围栏数据（不在 MVP） |
| E-12 | 课程被取消后已预约记录的处理？ | `booking.status='cancelled_by_store'`，不计违约；候补队列整条置 `expired` + `notify_task` 置 `cancelled` | PRD 已隐含此设计，仅需确认文案 |
| E-13 | "自然日"的边界是否按门店当地 00:00–23:59？ | 是（`store.timezone`），中国门店 = UTC+8 | 若按 UTC，课表日期会错（必须澄清） |
| E-14 | 上线首月"只提醒不冻结"的**结束时间**由谁、在哪天切换？ | 由总部管理员在后台手动切换（有留痕）；建议运营在转换前 3 天群发公告 | 若要求"自动到期切换"，加一个 scheduled job（小），但自动切换的客诉风险更高 |

---

## 12. 自查

**每个 P0 功能都有对应实现方案吗**

| PRD | 对应设计 | 状态 |
|---|---|---|
| P0-① 课表 | 5.2.4 / 5.2.5、`uiState` 领域计算、`ix_session_store_start` | ✅ |
| P0-② 预约 | 5.2.6、6.1（四层防超卖）、6.2（冲突）、6.7（幂等） | ✅ |
| P0-③ 取消 | 5.2.8 / 5.2.9、4.3.13 / 4.3.14（违约与冻结）、ADR-15（开关） | ✅ |
| P0-④ 候补与递补 | 5.2.10–5.2.14、6.3–6.6、`waitlist`/`release_event` 状态机 | ✅ |
| P0-⑤ 教练签到 | 5.3.3 / 5.3.4、`sign_in`（`UNIQUE(session_id)`）、ADR-14（缺席派生） | ✅ |
| P0-⑥ 教练身份与归属 | 5.3.1、5.4.2、4.3.5 / 4.3.6、6.11（导入原子性） | ✅ |
| P0-⑦ 待通知任务 | 5.4.5、4.3.11（`uq_notify_task` 幂等） | ✅ |
| P0-⑧ 违约开关与解冻 | 5.4.6、4.3.15 / 4.3.14、6.9（`HQ_ONLY_FORBIDDEN` + 留痕） | ✅ |
| 每条验收标准里的"权限 403 + 留痕" | 4.3.16（`audit_log`）、6.9（中间件统一注入与留痕） | ✅ |

**接口与方案一致吗**：错误码表（5.6）与各接口列出的错误一一对应；`uiState`、`waitlist.status`、`notify_task.status` 三处枚举在 4.4 与 5.x 使用同一套取值；`confirm_deadline` 只由服务端计算并同时写入 `waitlist` 与 `notify_task`（同源），不存在两处各算一遍。

**有没有硬编码密钥、未处理异常、未校验输入**：`appid/secret/JWT 密钥/DB 密码` 全部环境变量（7.1、8.2）；错误码清单覆盖业务分支，`INTERNAL` 兜底但不回显堆栈；所有 HTTP 边界 JSON Schema 校验、导入文件有大小/行数/类型限制；SQL 全参数化（8.2）。

**我没做的事（不含糊）**：
1. **本阶段没有写任何代码，也没有运行任何东西**——所以本文档里的所有性能数字（8.3）、并发行为（6.1–6.4）都是**设计推导与估算**，不是实测结果。其中"条件更新防超卖""行锁串行化保序""部分唯一索引行为"是 PG 的既定语义，但仍必须在真库上用集成测试与压测确认（验证方式已逐条写在 6.x 与 10.1）。
2. 没有真实用户流量与线上环境，容量目标全部基于 8.3 列出的假设（10 店 / 5000 日活 / 10 万请求每天 / 峰值 25 QPS），**这些数字不是调研结果**。
3. 未做微信侧实测：`code2Session`、`getPhoneNumber`、`subscribeMessage.send` 的参数与错误码以官方文档为准，联调时需按实际返回校准（7.1–7.3 已列出预期错误码）。
4. 未验证短信服务商、对象存储备份、证书签发等外部依赖（M0/M7 的 runbook 里解决）。

**需要测试同学重点覆盖（我建议的优先级）**：
1. 并发抢位（`capacity=1` × N 并发）与 `booked_count` 对账 —— 对应 PRD"超卖 = 0"的上线门槛；
2. 取消 → 递补的端到端（含 Worker 杀掉重启、重复触发、超时顺延、被跳过）；
3. 五个写接口的幂等三组测试（同 ID 串行 / 同 ID 并发 / 不同 ID 重复意图）；
4. 后台越权矩阵（门店运营 × 他店数据 × 每个接口 → 403 + 留痕）；
5. 导入的未匹配行与重复导入；
6. 规则开关两态 × 取消窗口边界（恰好 120 分钟、恰好 0 分钟、已开始）；
7. `uiState` 十种状态的渲染与按钮可用性（对照设计稿）。

**给设计与产品的两个交流点**：
1. 5.2.4 的 `uiState` 与设计稿的课程卡片状态需要**一一对齐命名**（设计稿若有"候补已满/已截止"之外的态，请提出来）；
2. `copyKey`（服务端给文案 key）意味着**所有涉及规则数字的文案由后端控制**，请设计把"提醒版/冻结版""候补有位/超时顺延"等文案按 key 交付，不要写死在界面里（这样规则一变文案不用改前端）。
