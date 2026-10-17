# 技术方案：健身房团课预约微信小程序 · MVP

| 项目 | 内容 |
|---|---|
| 版本 | v1.0（只写文档，不写代码） |
| 作者 | 工程师 |
| 依据 | `docs/PRD.md` **v1.1**（含 CEO 复审后的候补转正规则修订）；本文档所有业务规则以 PRD 为准，冲突时改本文档 |
| 文件命名说明 | PRD 第 1 节把本文档写作「`docs/技术方案.md`」，CEO 的任务指定文件名是 `docs/tech-design.md`。**以 CEO 指定的 `docs/tech-design.md` 为准**，PRD 里的名字视为同一份文档 |
| 与 PRD v1.1 的对应 | 候补转正 = 系统自动、即刻生效、**会员零确认**；系统内**不存在**「转正待确认」状态，接口与状态机都不提供确认动作（AC-6.1/6.1b/6.1c、R-08、决策 D1） |
| 与 PRD v1.1（复审修订）的对应 | 课表卡片状态标签按 AC-1.2 实现 **7 种**（第 7 种为 `已满，候补已结束`，界面文案「已满·候补已结束」）；课程状态术语按 PRD 第 6 节通用约定统一为 `待运营确认` = 本方案 `OPS_PENDING`（界面文案「待门店确认」），AC-4.9 的"驳回"目标态即此（映射见 4.4） |
| 尚未验证的事项 | 本阶段公司无法运行代码，本文档所有方案均**未经过实机验证**；每条方案都给出"如何验证"（见第 10 节）。性能数字是**估算**，依据写在 7.3 |

---

## 1. 需求映射

| PRD 功能 | 实现模块 | 备注 |
|---|---|---|
| P0-1 课表查看与课程详情（7 天、7 种状态标签） | 后端 `CatalogModule` + 小程序 `pages/schedule`、`pages/session-detail` | 课程状态标签是**派生**的（由容量、当前时间、课程事实状态算出），不落库成 7 个课程状态枚举值；接口以派生的 `statusLabel` 返回，避免状态爆炸（见 4.4、5.3.1） |
| P0-2 团课预约（容量、时间冲突、会员身份、不超卖） | `BookingModule.book()` + `MemberModule.assertBookable()` | 不超卖靠"条件更新 + 唯一索引"两层；AC-2.6 是必测项 |
| P0-3 取消预约（T-2h 免费线、释放名额并转正） | `BookingModule.cancel()` → `WaitlistModule.promote()` 同事务 | 取消与转正在**同一个数据库事务**内完成，保证 AC-6.1 的"5 秒内" |
| P0-4 教练签到（窗口、定位、迟到、异常签到审核） | `SignInModule` + 小程序 `pages/coach/*` | 防代签手段与**局限**写在 6.4，不夸大 |
| P0（支撑）最小运营后台 | `OpsModule` + `apps/admin-web`（Web） | 排课、容量、会员名单导入、课程取消、签到补录、异常签到审核、导出、撤销爽约 |
| P1-1 候补排队（FIFO、序号、退出、上限） | `WaitlistModule` | 队列**不单独建表**，用 `bookings` 的 `WAITLISTED` 行按 `id` 升序表达（见 6.2） |
| P1-2 候补自动转正与通知 | `WaitlistModule.promote()` + `NotificationModule` + `JobModule` | 转正同步执行、通知异步下发；**通知不参与任何业务判定**（R-15） |
| P1-3 教练标记未到场 + 爽约处置 | `AttendanceModule` | 含"爽约次数 30 天滚动窗口 + 冻结 7 天 + 撤销重算" |
| 埋点（PRD 第 2 节） | `AnalyticsModule` → `events` 表 | 业务埋点与业务事务解耦，写失败不影响业务 |
| R-15 通知原则 / 站内消息中心 | `NotificationModule` | 消息表同时充当推送队列（数据库即队列），不引入 MQ |
| T-2h / T-15min / T+15min / 课后 24h 触发 | `JobModule`（扫描式幂等定时任务） | 全部设计成"条件扫描 + 幂等键"，漏跑一次能被下次扫描补上（见 6.3） |

**范围外**（PRD 第 3 节已排除，技术侧同样不做）：支付/退款/次卡、私教课、跨门店、会员扫码签到、候补付费插队、短信通道、会员等级、多语言/多时区。

---

## 2. 架构概览

### 2.1 架构模式：模块化单体（单后端服务 + 单数据库）

```
┌─────────────────────┐   ┌──────────────────────┐   ┌─────────────────────┐
│ 微信小程序（会员端） │   │ 微信小程序（教练端）  │   │ 运营后台 Web（PC）   │
│ 原生 + TypeScript    │   │ 同一小程序、独立入口  │   │ 登录/排课/名单/审核  │
└──────────┬──────────┘   └──────────┬───────────┘   └──────────┬──────────┘
           │  HTTPS/JSON  Bearer JWT  │                          │
           └──────────────┬───────────┴──────────────────────────┘
                          ▼
        ┌──────────────────────────────────────────────────────────┐
        │           后端单体服务（Node.js + NestJS）                 │
        │  Adapter 层：Controllers / Guards / DTO 校验               │
        │  Application 层：Booking / Waitlist / SignIn / Attendance  │
        │                  / Ops / Catalog / Notification 用例       │
        │  Domain 层（纯规则，无框架、无 SQL）：                      │
        │    BookingPolicy / WaitlistPolicy / SignInPolicy /         │
        │    NoShowPolicy / TimeWindow / ClosePolicy                 │
        │  Infrastructure：Prisma Repositories / WxClient / Clock     │
        │  Job 层：进程内调度 + MySQL GET_LOCK 单执行者               │
        └───────────────┬───────────────────────┬──────────────────┘
                        │ SQL（参数化，事务）     │ HTTPS
                        ▼                        ▼
              ┌───────────────────┐    ┌──────────────────────────┐
              │ MySQL 8.0（主库）  │    │ 微信开放接口              │
              │ 唯一约束=最后防线  │    │ code2Session / 手机号 /   │
              │ 行锁 + 条件更新    │    │ 订阅消息 / access_token   │
              └───────────────────┘    └──────────────────────────┘
```

**为什么是模块化单体：**

- 规模：MVP 灰度 3 家门店（PRD 第 11 节），估算活跃会员 1500 人、日活 200~300 人、峰值 < 10 QPS（估算依据见 7.3）。这个量级下单体的容量不是瓶颈，拆服务的收益为负。
- 团队：这家公司的工程团队是 1 个人。微服务带来的部署、链路追踪、跨服务事务一致性成本，没有任何一个环节有对等收益。
- 一致性：本项目最关键的正确性要求是**"取消释放名额"和"候补转正"必须原子**（AC-6.1/6.4/6.6）。放在一个数据库事务里是最简单、最容易验证的实现；拆开后需要分布式事务或最终一致，直接违背"优先可逆、优先简单"。
- 模块化单体不是"一坨代码"：Domain 层是纯函数（时间窗判定、队列取人顺序、爽约次数），不依赖 NestJS 与 Prisma，可单测；模块间只允许通过 Application 层接口调用，禁止跨模块直接读对方的表（除只读报表）。

**放弃的备选：**

| 备选 | 为什么不选 |
|---|---|
| 微服务（预约服务 / 候补服务 / 通知服务） | 需要分布式事务或最终一致来处理"扣减 + 转正"，MVP 阶段收益为负；拆分点（候补）恰好是耦合最紧的地方 |
| Serverless（云函数 + API 网关） | 定时任务与长事务不友好；本地开发与调试链路复杂；并发名额扣减仍要落到数据库 |
| 引入消息队列（Kafka/RabbitMQ）处理通知与转正 | 转正必须**同步**（AC-6.1 5 秒内、且需即时占住名额），MQ 只会增加延迟与"消息丢失"面；通知用数据库表轮询即可（ADR-8） |

### 2.2 模块划分与依赖方向

```
Auth ─┐
      ├─→ Member ──→ Catalog（只读门店/排期）        Domain（纯规则，被所有 Application 依赖）
Wx ───┘      │            ▲                               ▲
             ▼            │                               │
          Booking ────────┘                               │
             │  └─→ Waitlist（转正引擎）───→ Notification ─┘
             ├─→ Attendance ─→ NoShowPolicy
             └─→ SignIn
Ops ─→（复用 Member/Booking/Catalog/SignIn/Attendance 的用例，不绕过规则写库）
Job ─→（只调用上述用例 + 自身扫描，不直接改业务表）
```

依赖方向铁律：

1. **Domain 不依赖任何框架、ORM、HTTP**；所有时间窗、状态派生、队列顺序、爽约判定都在 Domain 里，纯函数、可单测。
2. **Application 依赖 Domain + Repository 接口**，不依赖 Prisma 具体实现。
3. **Infrastructure 实现 Repository 接口**，是唯一碰 SQL 的地方。
4. **后台（Ops）与定时任务（Job）复用 Application 用例**，不允许自己写 UPDATE 改业务状态——否则规则会分叉（PRD 明确"规则对三端同时生效"）。
5. 通知的下发**只读业务、不写业务状态**：`NotificationModule` 只能改 `notifications` 表自己的字段。

### 2.3 本阶段不引入的东西（明确写出来，避免后续被当成遗漏）

- **不引入 Redis**：理由与触发条件见 ADR-3。
- **不引入消息队列**：见 ADR-8。
- **不引入 Elasticsearch/搜索**：课表是"门店 + 7 天"的确定性查询。
- **不做读写分离**：单库读量 < 50 QPS（7.3 估算）。
- **不引入行级分布式缓存**：课表查询带 `serverTime`，不允许展示过期缓存（AC-1.7）。

---

## 3. 技术选型

### 3.1 选型总表

| 层 | 选择 | 理由 | 放弃的备选 |
|---|---|---|---|
| 小程序框架 | **微信原生小程序 + TypeScript + 微信开发者工具**（不用跨端框架） | 只有微信一个端（PRD 不做多端）；定位、订阅消息、`getPhoneNumber`、`open-type` 等能力直接用官方 API，不用等框架适配；包体积与首屏最优；出问题能直接对照官方文档 | Taro / uni-app：多端能力现在没有需求，却带来编译层、SDK 版本配对、能力覆盖不全的调试成本 |
| 小程序状态与请求 | 原生 `Page/Component` + 自研 ~150 行 `request` 封装（统一注入 JWT、统一处理业务 `code`、登录态过期自动重登）+ 少量全局 store | 页面少（约 12 个）、状态简单，不需要引入状态管理库 | MobX-miniprogram / Westore：抽象成本大于收益 |
| 后端语言/运行时 | **Node.js 20 LTS + TypeScript（strict）** | 与小程序端共享类型与错误码定义（`packages/shared`），接口字段不会两边漂移；单人全栈只维护一套语言 | Java/Spring Boot：更稳但开发与部署重，本规模无收益；Python/FastAPI：类型与事务表达不如 TS + Prisma 直观 |
| 后端框架 | **NestJS 10**（模块化 + DI + DTO 校验管道 + `@nestjs/schedule`） | 它的 Module/Provider 边界正好等于本方案要的"模块化单体"边界；DTO 校验、守卫、异常过滤器开箱即用，避免 1 个人写出风格不一致的散乱代码 | Fastify/Express 自建分层：需要自己搭校验、DI、调度、日志，容易长出不一致的实现；Spring Boot：见上 |
| ORM / 迁移 | **Prisma 5**（迁移 + 类型安全查询）+ 少量 `$queryRaw` 写条件更新与 `FOR UPDATE` | 迁移可版本化、类型安全；但名额扣减这类"必须由数据库原子完成"的语句用原生 SQL 表达更清楚，不硬套 ORM 抽象 | TypeORM：装饰器与隐式行为多，实体与迁移容易不一致 |
| 数据库 | **MySQL 8.0**（InnoDB，RC 隔离级别，`utf8mb4`） | 关系约束恰好是这个领域的核心（唯一约束 = 防重复预约/防重复签到）；行锁 + `UPDATE ... WHERE` 条件更新是实现"不超卖"的最短路径；国内云 RDS 托管成熟、备份/点恢复工具链完备 | PostgreSQL：技术上等价甚至更好（部分唯一索引更优雅），但本设计用"生成列唯一索引"已覆盖该需求，切换没有收益；不选 MongoDB：事务与唯一约束是核心依赖 |
| 缓存 | **MVP 不引入独立缓存层**（不用 Redis/Memcached） | 见 3.2 与 ADR-3：读量估算 < 50 QPS，缓存收益接近 0；而缓存会引入"名额刚刚被约满"这类**不能有缓存延迟**的一致性问题（AC-2.6） | Redis 做课表缓存 + 分布式锁：触发条件见下 |
| 定时任务 | **服务进程内调度（`@nestjs/schedule`）+ MySQL `GET_LOCK` 单执行者 + `job_runs` 幂等表** | 任务量小（7 个），单实例部署；扫描式幂等设计让"漏跑/重启"都能自愈 | 独立调度服务 / XXL-JOB / 云函数定时器：需要额外部署与跨服务鉴权，收益为负 |
| 认证 | **自签 JWT（HS256，密钥来自环境变量）** + `openid` 决定身份；后台用账号密码 + bcrypt | 无状态、实现短；撤销需求不存在（冻结是业务校验，不是登录态，见 4.2） | 服务端 Session 表：多一次查询、需要清理；微信官方登录态只用于换 openid，不能当会话凭证 |
| 微信登录 | `wx.login` → `code2Session` 拿 `openid/unionid`；手机号用 `getPhoneNumber` 返回的 **code** 换手机号（新版接口），失败降级到 `encryptedData + iv + session_key` 解密 | 手机号是会员名单匹配的唯一键（PRD R-13）；新版 code 方式不需要把 `session_key` 传到前端 | 只用 openid 绑定会员：无法与门店现有"手机号名单"匹配，P0-2 直接做不了 |
| 通知 | **微信订阅消息（best-effort）+ 站内消息中心（兜底）**，`notifications` 表即推送队列 | PRD R-15 要求业务状态不依赖通知；一次授权一条（详见 3.3） | 短信（成本+合规，PRD 已排除）；模板消息（已下线）；自建推送（小程序端不支持） |
| 运营后台前端 | React 18 + Vite + Ant Design | 表格/表单/上传为主，组件库能省掉大量页面工作 | 内嵌在服务端渲染：反而更慢；Electron/桌面端：无需求 |
| 日志与指标 | `pino` 结构化 JSON 日志（含 `requestId`）+ 关键业务指标日志 + 可选 Prometheus 文本端点 | 单实例阶段"日志 + 告警"足够定位问题；不引入 ELK | 全量 APM（SkyWalking/OTel）：MVP 不必要 |
| 部署 | 单台云服务器（容器或 PM2 常驻）+ 云 RDS MySQL 8.0 + HTTPS 域名（小程序要求备案域名） | MVP 规模下最简单可靠；数据库托管换备份与高可用 | K8s：1 个人维护不了 |

### 3.2 关于缓存的具体判断（CEO 任务里点名要回答的一项）

**结论：MVP 不做独立缓存层，用 MySQL 直接承担。** 理由与触发条件：

| 维度 | 判断 |
|---|---|
| 读量 | 估算课表查询峰值 < 50 QPS（见 7.3）。MySQL 8 单实例轻松上千 QPS 简单索引查询，缓存带来的性能收益在这个量级不可测量 |
| 一致性风险 | 课表卡片含 `已预约人数/容量`，是**强一致敏感字段**：AC-2.6 要求并发时不能出现"两个人都成功"。如果缓存课表，就会出现"页面显示还有 1 位、点进去其实满了"。虽然接口有兜底校验，但会制造大量失败体验；而 AC-1.7 明确不接受过期数据 |
| 复杂度成本 | 引入 Redis = 多一个需要部署、监控、备份、做双写一致性与失效策略的组件，收益为 0 时纯亏 |
| 什么条件下加 | 出现下列任一信号再加，且优先加在**只读、非名额敏感**的路径（门店信息、课程模板、鉴权缓存）：① 课表页 P95 > 800ms 或 QPS > 200；② 定时任务需要多副本部署（届时 Redis 锁比 `GET_LOCK` 更好用）；③ 出现跨实例的限流/幂等需求；④ 消息发送量超出数据库轮询能力（> 50 条/秒持续） |

在需要之前，用**便宜的替代手段**覆盖同一目标：数据库索引 + 短事务 + 连接池 + 慢查询日志 + 单点 `Clock`。

### 3.3 微信登录与订阅消息的实现要点（含诚实的技术约束）

**登录与会员匹配（P0-2 的前置）**

1. 小程序调 `wx.login()` 拿 `code` → `POST /api/v1/auth/login`。
2. 后端调 `code2Session` 换 `openid`（`unionid` 有则存）。按 `openid` 查会员：命中 → 签发 JWT；未命中 → 签发 **`scope=BROWSE` 的受限 JWT**（可浏览课表，不能预约），并返回 `needPhoneBind: true`。
3. 小程序用 `<button open-type="getPhoneNumber">` 拿 `phoneCode` → `POST /api/v1/auth/phone`。
4. 后端用 code 调微信手机号接口。**拿到手机号后：规范化（去空格/`+86`/区号）→ HMAC-SHA256（密钥来自环境变量）→ 与 `members.phone_hash` 匹配**。明文手机号只在内存中存在，落库前加密（见 7.2）。
5. 匹配成功 → 绑定 `openid` 到该会员记录，升级 JWT 为 `scope=MEMBER`（带 `memberId`、`storeId`）；匹配失败 → 返回 `20001 MEMBER_NOT_FOUND`，前端按 PRD AC-2.2 展示"未查到你的会员信息，请联系门店开通会员"+ 门店电话，**不产生任何记录**。
6. 教练身份：同一手机号匹配 `coaches` 表 → JWT 的 `identities` 里再加 `coachId`。一次登录即可在"我的"里进入教练端；接口按路径要求对应身份。

**订阅消息的硬约束（PRD 已把它写成验收标准，这里给出工程结论）**

- 微信订阅消息是**一次授权对应一次下发**、且**按模板分别计数**（`wx.requestSubscribeMessage` 可以一次传多个 `tmplId`，每个模板各自记一次额度）。因此"转正通知"和"候补失败通知"必须各自授权，**不能互相覆盖**。
- 用户可能拒绝、额度可能用完（同一模板多次授权可累加额度）、模板可能被平台下线。**结论：通知只能 best-effort，任何业务状态不得以"通知发出成功"为前置条件**（R-15）。
- 工程实现：
  - 小程序在"加入候补"这一次点击内一次性请求 3 个模板：转正通知、候补失败通知、课程取消通知。**用户勾 0 个也视为加入候补成功**（AC-6.11），前端只显示一行浅色说明，不做阻断式弹窗。
  - 后端**不因为授权结果而改变候补状态**；授权结果最多用于前端提示与埋点。后端不记录"用户是否授权"来决定发不发——统一尝试发送，失败就落站内消息（AC-6.3）。
  - 模板 ID、跳转页面、字段映射做成**配置项**（环境变量/配置表），模板变动不需要改代码。
  - 发送失败的错误码：`43101`（用户拒收）= 终止重试；`40003`（openid 非法）= 终止重试；`45009`（接口频次超限）、网络错误 = 指数退避重试（最多 3 次）。**任何失败都不回滚业务状态。**
  - `access_token`：存 `wx_tokens` 单行表，提前 5 分钟刷新，刷新时用 `GET_LOCK('wx_token_refresh')` 防并发重复刷新；刷新失败不影响业务接口（只有通知发送依赖它）。
- **PRD 第 9 节待解决问题 4（可用模板有哪些）我无法查证**：我没有微信公众平台后台的访问权限，也不能检索外部资料。需要在实际小程序账号后台确认三件事：①订阅消息模板类目下有哪些可用的模板（转正/候补失败/课程取消三类）；②每个模板的字段上限与必填项；③小程序类目是否允许"预约/到店服务"类模板。**这三点不阻塞开发启动**（模板 ID 配置化 + 站内消息兜底），但阻塞"通知上线前的联调"。如果找不到合适模板，MVP 的降级路径就是"只有站内消息中心"，业务链路完全不受影响。

---

## 4. 数据模型

### 4.1 设计约定

- 所有时间字段 `DATETIME(3)`，**以 UTC 存储**；展示与业务判定统一按 `Asia/Shanghai`（PRD R-16）。禁止在 SQL 里混用 `NOW()` 与应用时间做业务判定，见 ADR-9。
- 主键 `BIGINT UNSIGNED AUTO_INCREMENT`；金额类字段本期没有（不做支付）。
- 手机号属敏感数据：**不存明文**，存 `phone_cipher`（AES-256-GCM）+ `phone_hash`（HMAC-SHA256，用于匹配与唯一约束）+ `phone_last4`（展示/导出用）。
- 外键：核心表之间建立外键（防脏数据），但**不用级联删除**（历史必须保留）。
- 计数冗余：`course_sessions.booked_count / waitlist_count` 是冗余计数，与 `bookings` 同事务更新，另配每日对账任务（见 6.5）。
- 状态机与唯一约束是**最后防线**：即使应用层出 bug，数据库也不允许超卖、不允许同一会员同课重复有效预约。
- MySQL 版本要求 **8.0.16+**（生成列唯一索引、`CHECK` 约束生效、`SKIP LOCKED`、CTE）。

### 4.2 表结构

#### `stores` 门店

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| name | VARCHAR(64) | NOT NULL | |
| phone | VARCHAR(20) | NOT NULL | 门店电话，AC-2.2/AC-4.6 页面展示 |
| address | VARCHAR(200) | NOT NULL | |
| latitude | DECIMAL(10,7) | NOT NULL | 签到定位基准 |
| longitude | DECIMAL(10,7) | NOT NULL | |
| sign_in_radius_m | INT | NOT NULL DEFAULT 300 | PRD 300 米；疫情/漂移时运营可调（PRD 风险项） |
| business_hours | VARCHAR(32) | NULL | **待运营确认**（PRD 待解决 5）。本期仅展示，不做排课硬校验 |
| status | ENUM('ACTIVE','DISABLED') | NOT NULL DEFAULT 'ACTIVE' | |

#### `members` 会员

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| store_id | BIGINT UNSIGNED | NOT NULL, FK stores | 主门店（R-13：只有 1 个） |
| name | VARCHAR(64) | NOT NULL | 门店导入的姓名 |
| phone_hash | CHAR(64) | NOT NULL, **UNIQUE** | HMAC-SHA256，登录匹配键 |
| phone_cipher | VARBINARY(255) | NOT NULL | AES-256-GCM 密文 |
| phone_last4 | CHAR(4) | NOT NULL | 展示与 CSV 导出（PRD AC-7.7） |
| openid | VARCHAR(64) | NULL, **UNIQUE** | 绑定后写入 |
| unionid | VARCHAR(64) | NULL | |
| card_status | ENUM('UNKNOWN','ACTIVE','EXPIRED') | NOT NULL DEFAULT 'UNKNOWN' | **本期不做校验**（PRD 待解决 2），仅记录 |
| status | ENUM('ACTIVE','DISABLED') | NOT NULL DEFAULT 'ACTIVE' | 门店停用会员 |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`UNIQUE(phone_hash)`、`UNIQUE(openid)`、`KEY(store_id, status)`。

> 导入冲突：同一手机号若已存在，导入不覆盖，进"冲突列表"由运营决定（换门店走 4.5 的换店流程）。

#### `coaches` 教练

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| name | VARCHAR(64) | NOT NULL | |
| phone_hash / phone_cipher / phone_last4 | 同上 | NOT NULL | 教练端登录匹配 |
| openid | VARCHAR(64) | NULL, UNIQUE | |
| home_store_id | BIGINT UNSIGNED | NULL, FK stores | 主门店；PRD 假设"教练可跨门店但不可同时段重复排课" |
| status | ENUM('ACTIVE','DISABLED') | NOT NULL DEFAULT 'ACTIVE' | |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

#### `courses` 课程（模板）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| store_id | BIGINT UNSIGNED | NOT NULL, FK stores | 门店级课程库（本期无跨门店模板需求） |
| name | VARCHAR(64) | NOT NULL | 如"燃脂单车" |
| duration_min | INT | NOT NULL | 默认时长 |
| default_capacity | INT | NOT NULL, CHECK 1~50 | R-04 |
| default_waitlist_limit | INT | NOT NULL DEFAULT 0 | 0 = 默认不开放候补 |
| status | ENUM('ACTIVE','ARCHIVED') | NOT NULL DEFAULT 'ACTIVE' | 归档不删 |

#### `course_sessions` 排期（核心表）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| course_id | BIGINT UNSIGNED | NOT NULL, FK courses | |
| store_id | BIGINT UNSIGNED | NOT NULL, FK stores | |
| coach_id | BIGINT UNSIGNED | NOT NULL, FK coaches | |
| room | VARCHAR(32) | NOT NULL DEFAULT '未指定' | 非 NULL 以便教室冲突唯一索引有效 |
| start_at | DATETIME(3) | NOT NULL | T（UTC） |
| end_at | DATETIME(3) | NOT NULL | CHECK (end_at > start_at) |
| capacity | INT | NOT NULL, CHECK 1~50 | 运营可改（R-04） |
| waitlist_limit | INT | NOT NULL DEFAULT 0 | 0 = 不开放候补（AC-5.4） |
| booked_count | INT | NOT NULL DEFAULT 0 | 冗余：有效预约数 |
| waitlist_count | INT | NOT NULL DEFAULT 0 | 冗余：候补中人数 |
| pending_promote_seats | INT | NOT NULL DEFAULT 0 | **待转正名额**（关键设计，见 6.2） |
| waitlist_closed | TINYINT(1) | NOT NULL DEFAULT 0 | T-2h 到达后由任务置 1 |
| status | ENUM('SCHEDULED','SIGNED_IN','OPS_PENDING','COACH_NOT_SIGNED_IN','FINISHED','CANCELLED') | NOT NULL DEFAULT 'SCHEDULED' | 事实状态，见 4.4 映射 |
| roster_status | ENUM('NOT_SUBMITTED','SUBMITTED','AUTO_DEFAULTED') | NOT NULL DEFAULT 'NOT_SUBMITTED' | AC-7.2 |
| roster_submitted_at | DATETIME(3) | NULL | |
| roster_source | ENUM('COACH','OPS','DEFAULT') | NULL | AC-7.2/R-12 判定来源 |
| cancel_reason | VARCHAR(200) | NULL | R-14 |
| cancelled_by | BIGINT UNSIGNED | NULL | ops_users.id |
| cancelled_at | DATETIME(3) | NULL | |
| cancel_overdue | TINYINT(1) | NOT NULL DEFAULT 0 | R-14：T-1h 内强行取消留痕 |
| created_by | BIGINT UNSIGNED | NOT NULL | ops_users.id |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引与约束：
- `UNIQUE uk_store_room_start (store_id, room, start_at)`：同门店同教室同开始时间不可重复排课（后台防手滑）。
- `KEY idx_store_start (store_id, start_at, status)`：会员端课表主查询。
- `KEY idx_coach_start (coach_id, start_at)`：教练端"今天及未来 2 天自己的课"+ 教练时段冲突校验。
- `KEY idx_status_start (status, start_at)`：定时任务扫描（T-2h、T+15min、课后 24h）。
- `KEY idx_promote_sweep (waitlist_closed, start_at, pending_promote_seats)`：转正兜底扫描。
- 教练时段重叠**不能用唯一索引表达**（区间相交），用应用层校验 + 后台单人操作的低并发特性覆盖；并发排课冲突的实际风险可忽略。

#### `bookings` 预约/候补记录（核心表）

设计要点（ADR-6）：**每次"加入"都是一行新记录，历史永久保留**；"有效记录唯一"用生成列唯一索引实现。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | **同时充当候补队列的 FIFO 顺序**（R-07：时间戳相同按系统自增序号） |
| session_id | BIGINT UNSIGNED | NOT NULL, FK course_sessions | |
| member_id | BIGINT UNSIGNED | NOT NULL, FK members | |
| origin | ENUM('BOOK','WAITLIST') | NOT NULL | 这一行的来源（埋点"候补转正"用） |
| status | ENUM('ENROLLED','WAITLISTED','CANCELLED_BY_MEMBER','SESSION_CANCELLED','WAITLIST_EXPIRED','WAITLIST_PROMOTE_FAILED') | NOT NULL | 见 4.3 状态机 |
| active_key | VARCHAR(48) | GENERATED ALWAYS AS (IF(status IN ('ENROLLED','WAITLISTED'), CONCAT(session_id, ':', member_id), NULL)) STORED, **UNIQUE** | 有效态唯一：一个会员对一节课**最多 1 条**有效记录（R-06/AC-2.5），且不能既预约又候补 |
| session_start_at | DATETIME(3) | NOT NULL | 冗余自排期：用于"我的预约"排序与时间冲突校验，避免 JOIN |
| session_end_at | DATETIME(3) | NOT NULL | 同上 |
| waitlist_joined_at | DATETIME(3) | NULL | 候补加入时刻（展示"第 N 位"的依据之一） |
| promoted_at | DATETIME(3) | NULL | 转正时刻（AC-6.1 的 5 秒度量点） |
| promote_fail_reason | ENUM('TIME_CONFLICT','MEMBER_SUSPENDED') | NULL | AC-6.7 + PM 确认的冻结跳过分因 |
| cancel_source | ENUM('MEMBER','SESSION','SYSTEM') | NULL | |
| cancelled_at | DATETIME(3) | NULL | |
| late_cancel | TINYINT(1) | NOT NULL DEFAULT 0 | AC-3.2：[T-2h, T) 取消，计 1 次爽约 |
| attendance | ENUM('UNKNOWN','PRESENT','ABSENT') | NOT NULL DEFAULT 'UNKNOWN' | 对外"未到场"由它派生（4.4） |
| attendance_source | ENUM('COACH','OPS','DEFAULT') | NULL | R-12 判定来源 |
| attendance_set_at | DATETIME(3) | NULL | |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：
- `UNIQUE(active_key)`
- `KEY idx_session_status_id (session_id, status, id)`：候补队列取队首 / 报名名单。
- `KEY idx_member_start (member_id, status, session_start_at)`：我的预约列表 + 时间冲突校验。
- `KEY idx_member_session (member_id, session_id)`：按课查我的记录。

**为什么用"多行历史 + 生成列唯一索引"而不是"一行状态复用"：**

- R-07 要求"退出候补后再加入视为新加入，排到队尾"。如果复用同一行，就必须额外维护一个"课程内自增序号"字段来纠正顺序；而**新行天然满足**：新行的 `id` 一定更大，`ORDER BY id` 就是 FIFO（R-07 的字面要求）。
- 审计价值：退出/被跳过/被取消的每一次"加入"都有独立记录，AC-6.7 的"跳过并移除"、AC-7.5 的爽约明细、PRD 埋点都能直接从表里查，不需要另建流水表（`booking_events` 仍保留用于状态迁移审计）。
- 唯一性不受影响：生成列在 `status ∈ {ENROLLED, WAITLISTED}` 时才有值，`NULL` 不参与唯一约束（MySQL 允许多个 NULL）。

#### `sign_ins` 教练签到

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| session_id | BIGINT UNSIGNED | NOT NULL, FK course_sessions | |
| coach_id | BIGINT UNSIGNED | NOT NULL, FK coaches | 必须 = session.coach_id（AC-4.8） |
| active_session_id | BIGINT UNSIGNED | GENERATED ALWAYS AS (IF(status IN ('CONFIRMED','PENDING_REVIEW'), session_id, NULL)) STORED, **UNIQUE** | 一节课同时只有 1 条有效签到（AC-4.7）；被驳回后允许重新提交，历史保留 |
| signed_at | DATETIME(3) | NOT NULL | **服务端时间**（AC-4.3/AC-4.5 迟到判定的唯一依据） |
| is_late | TINYINT(1) | NOT NULL DEFAULT 0 | signed_at > start_at |
| method | ENUM('SELF_LOCATION','SELF_EXCEPTION','OPS_BACKFILL') | NOT NULL | |
| status | ENUM('CONFIRMED','PENDING_REVIEW','APPROVED','REJECTED') | NOT NULL | |
| latitude / longitude | DECIMAL(10,7) | NULL | 客户端上报 |
| accuracy_m | INT | NULL | 客户端精度，用于判定"定位不可用" |
| distance_m | INT | NULL | **服务端**用 Haversine 计算，不信任客户端算出的距离 |
| reason | VARCHAR(100) | NULL | 异常签到原因（PRD 要求 1~100 字，服务端强制） |
| reviewed_by / reviewed_at / review_note | — | NULL | AC-4.9 |
| request_ip | VARCHAR(45) | NULL | 审计（防代签事后追溯） |
| user_agent | VARCHAR(200) | NULL | 审计 |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

#### `notifications` 消息中心 + 推送队列（一表两用）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| recipient_type | ENUM('MEMBER','COACH','OPS') | NOT NULL | |
| recipient_id | BIGINT UNSIGNED | NOT NULL | members.id / coaches.id / ops_users.id |
| event_type | ENUM('WAITLIST_JOINED','WAITLIST_PROMOTED','WAITLIST_FAILED','WAITLIST_EXPIRED','SESSION_CANCELLED','NO_SHOW_WARNING','BOOKING_FROZEN','BOOKING_UNFROZEN','SIGN_IN_EXCEPTION_REVIEWED','COACH_NO_SIGN_IN_TODO','OPS_TODO') | NOT NULL | 覆盖 AC-6.3/6.9/6.10、R-11、AC-4.11 |
| session_id / booking_id | BIGINT UNSIGNED | NULL | |
| title | VARCHAR(64) | NOT NULL | |
| content | VARCHAR(255) | NOT NULL | **自包含**（R-15）：事件类型 + 课程名 + 时间 + 当前状态 + 可执行操作 |
| payload | JSON | NULL | 结构化为前端字段（课程名、时间、状态、跳转参数） |
| dedupe_key | VARCHAR(128) | NULL, **UNIQUE** | 幂等：如 `WAITLIST_PROMOTED:12345`，保证同一事件只通知一次 |
| read_at | DATETIME(3) | NULL | 站内已读（AC-6.3 未读角标） |
| push_required | TINYINT(1) | NOT NULL DEFAULT 0 | |
| push_template_id | VARCHAR(64) | NULL | 配置化的模板 ID |
| push_page | VARCHAR(128) | NULL | 点击跳转路径 |
| push_status | ENUM('NOT_REQUIRED','PENDING','SENT','FAILED','GAVE_UP') | NOT NULL DEFAULT 'NOT_REQUIRED' | |
| push_attempts | INT | NOT NULL DEFAULT 0 | |
| last_error_code | VARCHAR(32) | NULL | 如 `43101` |
| next_retry_at | DATETIME(3) | NULL | 指数退避 |
| created_at | DATETIME(3) | NOT NULL | |

索引：`KEY idx_recipient (recipient_type, recipient_id, id DESC)`、`KEY idx_unread (recipient_type, recipient_id, read_at)`、`KEY idx_push_pending (push_status, next_retry_at)`。

#### `no_show_records` 爽约记录

**关键建模决定**：PRD 里"计 1 次爽约"有两个不同来源——AC-7.3 的"未到场"和 AC-3.2 的"[T-2h, T) 取消"。二者共用"30 天累计次数"这个计数器（R-11 的冻结判定），所以放同一张表用 `occurrence_type` 区分，避免两套计数逻辑。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| member_id | BIGINT UNSIGNED | NOT NULL | |
| session_id / booking_id | BIGINT UNSIGNED | NOT NULL | |
| occurrence_type | ENUM('ABSENT','LATE_CANCEL') | NOT NULL | R-11 未到场 / AC-3.2 临期取消 |
| source | ENUM('COACH','OPS','DEFAULT','SYSTEM') | NOT NULL | 判定来源（AC-7.5 展示） |
| occurred_at | DATETIME(3) | NOT NULL | **取课程 `start_at`**，作为 30 天滚动窗口的比较基准（不用创建时间，避免任务延迟导致窗口漂移） |
| status | ENUM('ACTIVE','REVOKED') | NOT NULL DEFAULT 'ACTIVE' | AC-7.6 运营撤销 |
| revoked_by / revoked_at / revoke_reason | — | NULL | |
| created_at | DATETIME(3) | NOT NULL | |

约束：`UNIQUE(booking_id, occurrence_type)`（幂等：任务重跑不会重复计次）、`KEY idx_member_window (member_id, occurred_at, status)`。

#### `member_suspensions` 冻结记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| member_id | BIGINT UNSIGNED | NOT NULL | |
| start_at / end_at | DATETIME(3) | NOT NULL | R-11：从第 3 次记录生成时刻起算 7 天 |
| trigger_no_show_id | BIGINT UNSIGNED | NOT NULL | 触发的那条记录 |
| status | ENUM('ACTIVE','RELEASED') | NOT NULL DEFAULT 'ACTIVE' | |
| released_by / released_at / release_reason | — | NULL | AC-7.6 |
| created_at | DATETIME(3) | NOT NULL | |

索引：`KEY idx_member_active (member_id, status, end_at)`。
幂等：同一会员不允许存在两条 `ACTIVE` 冻结（应用层 + 唯一生成列 `UNIQUE(IF(status='ACTIVE', member_id, NULL))`）。

#### `booking_events` 状态迁移审计流水

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| booking_id / session_id / member_id | BIGINT UNSIGNED | NOT NULL | |
| event_type | VARCHAR(32) | NOT NULL | `BOOKED` / `WAITLIST_JOINED` / `WAITLIST_LEFT` / `PROMOTED` / `PROMOTE_SKIPPED` / `CANCELLED` / `SESSION_CANCELLED` / `WAITLIST_EXPIRED` / `ATTENDANCE_SET` / `NO_SHOW_RECORDED` / `NO_SHOW_REVOKED` / `FROZEN` / `UNFROZEN` |
| from_status / to_status | VARCHAR(24) | NULL | |
| actor_type | ENUM('MEMBER','COACH','OPS','SYSTEM') | NOT NULL | |
| actor_id | BIGINT UNSIGNED | NULL | |
| reason | VARCHAR(200) | NULL | |
| metadata | JSON | NULL | |
| request_id | CHAR(36) | NULL | 与日志串联 |
| created_at | DATETIME(3) | NOT NULL | |

索引：`KEY(booking_id, id)`、`KEY(session_id, id)`、`KEY(member_id, id)`。

#### `job_runs` 定时任务幂等与执行记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK | |
| job_name | VARCHAR(64) | NOT NULL | |
| biz_key | VARCHAR(128) | NOT NULL | 如 `close_waitlist:session:123`、`promote:booking:456`、`auto_roster:session:789` |
| status | ENUM('DONE','FAILED') | NOT NULL | |
| detail | VARCHAR(255) | NULL | |
| executed_at | DATETIME(3) | NOT NULL | |

约束：`UNIQUE(job_name, biz_key)` —— **INSERT 冲突即视为已执行**，这是所有任务幂等的统一机制。索引 `KEY(job_name, executed_at)`。

#### `member_store_changes` 换门店记录

`id / member_id / from_store_id / to_store_id / operated_by / created_at`，`KEY(member_id, created_at)`。R-13：30 天内最多 1 次，由 `OpsModule` 校验。

#### `ops_users` 运营账号

`id / name / phone_hash / password_hash(bcrypt) / role ENUM('ADMIN','STORE_OPS') / store_id NULL / status / last_login_at / created_at`。
`STORE_OPS` 只能操作自己门店的数据（最小权限，7.2）。

#### `wx_tokens` 微信 access_token 单行缓存

`id TINYINT PK(=1) / token_cipher VARBINARY(512) / expires_at DATETIME(3) / refreshed_at DATETIME(3)`。

#### `events` 埋点

`id / event_name VARCHAR(48) / member_id NULL / store_id NULL / session_id NULL / properties JSON / client_at DATETIME(3) NULL / server_received_at DATETIME(3)`，`KEY(event_name, server_received_at)`、`KEY(member_id, server_received_at)`。
写入在**业务事务之外**，失败只记日志（埋点不能影响预约）。PRD 第 2 节要求的比值部分可从 `bookings`/`booking_events` 直接算，埋点表是补充。

#### `idempotency`：**不建表**（决策）

预约、取消、候补入队、签到四个写接口天然幂等，靠"状态机 + 唯一约束"实现，不需要幂等键表：
- 重复预约 → `UNIQUE(active_key)` 冲突 → 返回 `30007` 并把已存在的记录状态返回（等同成功）。
- 重复签到 → `UNIQUE(active_sign_in)` 冲突 → 返回 `60006` + 已有 `signed_at`。
- 重复取消 → 状态已是 `CANCELLED_BY_MEMBER` → 直接返回成功 + `idempotent: true`。
- 重复退出候补 → 状态已是终态 → 返回成功 + `idempotent: true`。

### 4.3 预约记录状态机（含"禁止待确认中间态"的硬约束）

```
                   ┌──────────────── 会员取消（AC-3.1/3.2） ───────────────┐
                   ▼                                                        │
  预约成功 ──▶ ENROLLED ───── 门店取消课程（R-14）────▶ SESSION_CANCELLED    │
                │  ▲                                                        │
                │  │ 转正成功（AC-6.1：同一事务内 WAITLISTED→ENROLLED，     │
                │  │            无中间态、无确认动作、无响应时限）           │
                │  │                                                        │
  加入候补 ──▶ WAITLISTED ──── 会员退出候补（AC-5.5）──▶ CANCELLED_BY_MEMBER ◀┘
                 │ │
                 │ ├── 转正失败：时间冲突（AC-6.7）/ 权限冻结（PM 确认 A）──▶ WAITLIST_PROMOTE_FAILED
                 │ │        （从队列移除，写消息中心，继续转正下一人）
                 │ └── T-2h 关闭未排上（AC-6.9）──▶ WAITLIST_EXPIRED
                 │
  门店取消课程（AC-6.10）──▶ SESSION_CANCELLED

  课后判定（不改变 status，只改 attendance）：ENROLLED + attendance=ABSENT ⇒ 对外展示"未到场"
```

**硬约束（对应 AC-6.1b，任何人不得违反）：**

- 状态枚举里**没有** `PROMOTION_PENDING` / `AWAITING_CONFIRM` 之类的中间态；`WaitlistPolicy` 不提供"确认转正"的方法。
- 公开接口里**没有**"确认转正"端点；`WAITLISTED → ENROLLED` 只能由 `promote()` 在服务端触发。
- `notifications` 与前端**不得**出现倒计时/确认按钮文案；消息中心文案固定为"你已被排上，如来不了请在 T-2h 前免费取消（不计爽约）"。
- 若通知发送失败，转正**已经生效**，不回滚（AC-6.2）。

### 4.4 PRD 展示状态 ←→ 内部状态的映射（PRD 是准绳，这里保证字面对齐）

**课程卡片/详情的 7 个外部标签**（AC-1.2，术语与 PRD v1.1 复审修订一致）都是**派生**的，查询时按 `now` 与字段算出，不落库（接口以 `statusLabel` 返回，枚举值见 5.3.1）：

| PRD 标签 | 派生条件（以服务端 `now` 判定，UTC） |
|---|---|
| `可预约` | `status='SCHEDULED'` 且 `booked_count < capacity` 且 `now < start_at - 15min` 且剩余 > 3 |
| `仅剩 N 位` | 同上，且 `0 < capacity - booked_count ≤ 3`（`N = capacity - booked_count`） |
| `已满，可候补` | `status='SCHEDULED'` 且 `booked_count ≥ capacity` 且 `now < start_at - 2h` 且 `waitlist_limit > 0` 且 `waitlist_count < waitlist_limit` |
| `已满，候补已结束`（AC-1.2 第 7 种，界面文案「已满·候补已结束」） | `status='SCHEDULED'` 且 `booked_count ≥ capacity` 且 `start_at - 2h ≤ now < start_at - 15min`；**标签置灰、详情页"加入候补"按钮禁用** + 原因文案"本站候补已于开课前 2 小时结束"（AC-1.2/AC-5.3/R-09） |
| `报名已截止` | `start_at - 15min ≤ now < start_at`（**无论是否满员**，AC-1.2/AC-1.4；满员课在 T-15min 之后从第 7 种标签切到本标签） |
| `已开课` | `start_at ≤ now < end_at`（或 `status='SIGNED_IN'` 时为"已签到开课"） |
| `已取消` | `status='CANCELLED'`（不展示剩余名额，AC-1.5） |

上述 7 个标签**互斥**，按此顺序命中（与 UX-design 6.2 的"状态优先级"一致）：`已取消` > `已开课` > `报名已截止` > `已满，候补已结束` > `已满，可候补` > `仅剩 N 位` > `可预约`。接口只回传命中的 `statusLabel` 与原始数字（`bookedCount/capacity/remaining`），标签本身不落库。

> **一个 PRD/UX 未覆盖的组合（工程默认，待 PM + 设计师确认）**：课程已满、`now < start_at - 2h`，但 `waitlist_limit = 0`（本站不开放候补）或候补队列已达上限（AC-5.4）。此时 AC-1.2 的 7 种标签都不命中（AC-5.4 只规定"加入失败"的提示，UX 6.5 只定了详情页 CTA 的禁用文案）。工程默认渲染为 **`已满`（不可候补）**、按钮禁用 + 原因文案（"候补人数已满" / "本站未开放候补"），`statusLabel = FULL_NOT_WAITLISTABLE`（见 5.3.1）。已列入 11.1 第 10 条。

> 已结束（`now ≥ end_at`）不在课表出现，只在"我的预约-历史"（R-01）。

**"我的预约"记录状态映射：**

| PRD 展示 | 内部 |
|---|---|
| `已预约` | `ENROLLED` 且 `attendance='UNKNOWN'` 且 `now < end_at` |
| `未到场` | `ENROLLED` 且 `attendance='ABSENT'` |
| `已取消`（会员） | `CANCELLED_BY_MEMBER` |
| `课程取消` | `SESSION_CANCELLED` |
| `候补中` | `WAITLISTED`（附带动态序号 `position`） |
| `候补结束未排上` | `WAITLIST_EXPIRED` |
| `候补转正失败` | `WAITLIST_PROMOTE_FAILED` + `promote_fail_reason`（文案：时间冲突 / 预约权限冻结） |

**课程事实状态机（教练/运营视角）：**

```
SCHEDULED ──教练窗口内签到成功──▶ SIGNED_IN ──(到达 end_at，任务置位)──▶ FINISHED
    │                               
    ├──异常签到提交──▶ OPS_PENDING ──运营通过──▶ SIGNED_IN
    │                        └────运营驳回──▶ 保持 OPS_PENDING（= PRD 的 `待运营确认`；`sign_ins` 置 REJECTED、
    │                                          通知教练重新处理，AC-4.9）
    ├──T+15min 未签到（任务）──▶ COACH_NOT_SIGNED_IN ──运营补录──▶ SIGNED_IN（source=OPS_BACKFILL）
    └──运营取消──▶ CANCELLED（终态，其他迁移全部禁止）
```

**术语映射（PRD 第 6 节通用约定 + AC-4.9/AC-4.11；把测试计划 S-2 指出的易混点在这里定死）**：

- PRD 的课程状态 `待运营确认`（AC-4.4/AC-4.9/AC-7.4、R-10/R-11；会员端/教练端展示文案「待门店确认」，见 UX-design 2.3）= 本方案 **`OPS_PENDING`**。
- AC-4.9「驳回 = 课程状态回到 `待运营确认`」= 本方案**回到 `OPS_PENDING`**（`sign_ins` 保留一条 `REJECTED` 历史、通知教练重新处理）。**不得**落到 `COACH_NOT_SIGNED_IN`——后者专指"到 T+15min 仍无任何有效签到"（AC-4.11，展示「教练未签到」）。
- 二者对外**一律不计爽约**，展示层都不得出现「未到场」（AC-7.4 / R-11）。

**"课程是否实际发生"（决定是否计爽约）** 定义：存在 `status ∈ {CONFIRMED, APPROVED}` 的 `sign_ins`（含 `OPS_BACKFILL`），或课程状态为 `SIGNED_IN`。`OPS_PENDING`（含被驳回后仍停留在此态的课）与 `COACH_NOT_SIGNED_IN` **一律不计爽约**（AC-7.4 / R-11）；被驳回的课只有在教练重新提交并获得通过、或运营补录之后，才算"实际发生"、才可能产生爽约记录。

### 4.5 关键约束清单（测试可直接逐条断言）

| # | 约束 | 落点 |
|---|---|---|
| C-1 | 同一会员对同一节课最多 1 条有效记录（不可既约又候补） | `UNIQUE(bookings.active_key)` |
| C-2 | 一节课同时最多 1 条有效签到 | `UNIQUE(sign_ins.active_session_id)` |
| C-3 | 预约人数不超过容量 | `bookings` 与 `booked_count` 同事务 + 条件更新 `WHERE booked_count < capacity` + 对账任务 |
| C-4 | 候补人数不超过 `waitlist_limit` | 条件更新 `WHERE waitlist_count < waitlist_limit` |
| C-5 | 同一教练同一时段不可重复排课 | 应用层校验（区间相交） |
| C-6 | 同一门店同一教室同一开始时间不可重复排课 | `UNIQUE(store_id, room, start_at)` |
| C-7 | 同一会员最多 1 条 ACTIVE 冻结 | 生成列唯一索引 + 应用层 |
| C-8 | 同一预约同一爽约类型只计 1 次 | `UNIQUE(no_show_records.booking_id, occurrence_type)` |
| C-9 | 同一事件只通知一次 | `UNIQUE(notifications.dedupe_key)` |
| C-10 | 同一任务对同一业务对象只执行一次 | `UNIQUE(job_runs.job_name, biz_key)` |
| C-11 | 手机号唯一（登录匹配键） | `UNIQUE(members.phone_hash)` |
| C-12 | 容量、时长等取值边界 | `CHECK` 约束 + DTO 校验（1~50；`end_at > start_at`；异常签到原因 1~100 字） |

---

## 5. 接口设计

### 5.1 通用约定

- 前缀 `/api/v1`；HTTPS；请求与响应 `application/json; charset=utf-8`。
- 认证：`Authorization: Bearer <JWT>`。JWT payload：`{ sub, openid, scope: 'BROWSE'|'MEMBER'|'COACH'|'OPS', memberId?, coachId?, storeId?, exp }`；`BROWSE` 只能访问只读课表接口。
- 响应包统一：

```json
{ "code": 0, "message": "ok", "data": { }, "requestId": "b3f1...-uuid" }
```

- **HTTP 状态码与业务 `code` 都返回**，客户端以 `code` 分支为准，服务端日志与告警按 HTTP 状态分类：
  - `200` 成功（`code=0`）
  - `400` 参数校验失败（`10001`）
  - `401` 未登录/令牌失效（`10002`）
  - `403` 无权限（`10003`，如非本门店、非本人执教）
  - `404` 资源不存在（`30001`）
  - `409` 业务规则冲突（`code ≥ 20001`，如满员、冲突、截止）
  - `429` 触发限流（`10004`）
  - `500` 服务端错误（`10005`，`message` 不含堆栈，堆栈只进日志）
- 分页统一 `page`（从 1 起）+ `pageSize`（默认 20，最大 50），响应 `data.list / data.total / data.page / data.pageSize`。
- **所有 `POST/DELETE` 写接口都接受可选 `clientRequestId`（UUID）**，仅用于日志串联（幂等不依赖它，见 4.2 末节）。
- 时间字段一律 ISO 8601 带时区偏移（`2026-03-05T19:00:00+08:00`），服务端内部按 UTC 处理。**请求里带的时间只用于查询过滤，绝不用于业务判定**（R-16）；判定基准在响应里回传 `serverTime`，前端展示倒计时以它为准。

### 5.2 错误码总表

| code | 语义 | HTTP | data 附加字段 | 对应 PRD |
|---|---|---|---|---|
| 0 | 成功 | 200 | — | — |
| 10001 | 参数校验失败 | 400 | `fields` | — |
| 10002 | 未登录或登录态失效 | 401 | — | — |
| 10003 | 无权限 | 403 | — | AC-4.8/AC-2.2 |
| 10004 | 请求过于频繁 | 429 | `retryAfterSeconds` | 防刷 |
| 10005 | 服务端错误 | 500 | — | — |
| 20001 | 未匹配到会员信息 | 403 | `storePhone`、`storeName` | AC-2.2 |
| 20002 | 预约/候补权限被冻结 | 403 | `frozenUntil`、`noShowCount30d` | AC-2.9、R-11 |
| 20003 | 非本门店课程 | 403 | — | R-13 |
| 30001 | 课程不存在 | 404 | — | — |
| 30002 | 课程已被门店取消 | 409 | `cancelReason` | AC-1.5/AC-4.10 |
| 30003 | 报名已截止（T-15min 后） | 409 | `bookingClosedAt` | AC-1.4/AC-2.7 |
| 30004 | 课程已开始，不可预约 | 409 | `startAt` | AC-2.8 |
| 30005 | 名额刚刚被约满 | 409 | `waitlistAvailable`、`waitlistLimit`、`bookedCount`、`capacity` | **AC-2.6** |
| 30006 | 与已预约课程时间冲突 | 409 | `conflict: {sessionId, courseName, startAt, endAt}` | AC-2.4、R-05 |
| 30007 | 已有有效预约/候补记录 | 409 | `booking: {id, status}`（等同成功，前端刷新为最新态） | AC-2.5、R-06 |
| 40001 | 候补已结束（≥T-2h） | 409 | `waitlistClosedAt` | AC-5.3、R-09；对应卡片标签 `已满，候补已结束`（AC-1.2 第 7 种，前端在该标签下禁用按钮、正常不发请求） |
| 40002 | 仍有名额，可直接预约 | 409 | `remaining` | AC-5.3 |
| 40003 | 候补人数已满 | 409 | `waitlistLimit` | AC-5.4 |
| 40004 | 当前不在候补中（重复操作，**按幂等成功处理**：HTTP 200 + `code=0` + `idempotent: true`） | 200 | `idempotent` | AC-5.5 |
| 50001 | 课程已开始，无法取消 | 409 | `startAt` | AC-3.3 |
| 50002 | 课程已被门店取消，无需取消 | 409 | — | AC-3.4 |
| 60001 | 签到窗口未开启 | 409 | `windowOpenAt` | AC-4.2 |
| 60002 | 签到窗口已关闭 | 409 | `windowCloseAt`、`storePhone` | AC-4.6 |
| 60003 | 非本人执教的课 | 403 | — | AC-4.8 |
| 60004 | 不在门店签到范围 | 409 | `distanceM`、`radiusM`、`allowException: true` | AC-4.4 |
| 60005 | 定位不可用或精度过低 | 409 | `allowException: true` | AC-4.4 |
| 60006 | 已签到 | 409 | `signedAt`、`isLate`（等同成功） | AC-4.7 |
| 60007 | 本课已被取消 | 409 | — | AC-4.10 |
| 60008 | 课程未结束，名单不可提交 | 409 | `endAt` | AC-7.1 |
| 60009 | 名单提交窗口已关闭（>24h） | 409 | `rosterDeadlineAt` | AC-7.2 |
| 60010 | 未到场名单含无效会员 | 409 | `invalidMemberIds` | AC-7.1 |
| 70001 | 换门店频率超限（30 天 1 次） | 409 | `nextAllowedAt` | R-13 |

> 约定：**幂等类错误（30007/60006/重复取消/重复退出候补）返回 HTTP 200 + `code=0` 并带 `idempotent: true`**，避免客户端把"重复点击"当失败弹错误提示（AC-2.10）。表里标 409 的是"需要用户看到并处理"的情况。

### 5.3 会员端接口

| # | 方法 | 路径 | 说明 |
|---|---|---|---|
| 1 | POST | `/api/v1/auth/login` | `{code}` → `{token, scope, member?: {id,name,storeId,storeName,storePhone}, needPhoneBind}` |
| 2 | POST | `/api/v1/auth/phone` | `{phoneCode}` → `{member}` 或 `20001` |
| 3 | GET | `/api/v1/stores/current` | `{id, name, phone, address, businessHours}` |
| 4 | GET | `/api/v1/sessions` | **课表查询**（下详） |
| 5 | GET | `/api/v1/sessions/{id}` | 课程详情（含规则文案、`myBooking`、`waitlistPosition`） |
| 6 | POST | `/api/v1/sessions/{id}/bookings` | **预约**（下详） |
| 7 | DELETE | `/api/v1/bookings/{id}` | **取消预约**（下详） |
| 8 | POST | `/api/v1/sessions/{id}/waitlist` | **加入候补**（下详） |
| 9 | DELETE | `/api/v1/sessions/{id}/waitlist` | **退出候补** → `{status:'CANCELLED_BY_MEMBER', idempotent}`；后续序号自动前移（序号是动态计算的，无写操作） |
| 10 | GET | `/api/v1/me/bookings` | `?scope=upcoming|history&page`，返回 4.4 的映射状态 + `cancelFreeUntil`（=T-2h）、`cancelDeadlineAt`（=T）、`bookingClosedAt`（=T-15min） |
| 11 | GET | `/api/v1/me/notifications` | 消息中心列表（AC-6.3） |
| 12 | GET | `/api/v1/me/notifications/unread-count` | 未读角标 |
| 13 | POST | `/api/v1/me/notifications/{id}/read` | 标记已读 |
| 14 | GET | `/api/v1/me/no-shows` | 爽约记录（AC-7.5）：明细 + 30 天累计 + 冻结状态与到期时间 |
| 15 | POST | `/api/v1/me/events` | 埋点上报（批量），失败不影响任何业务 |
| 16 | POST | `/api/v1/me/wx-subscribe-result` | 上报订阅授权结果（可选，仅埋点用，**不参与业务判定**） |

#### 5.3.1 `GET /api/v1/sessions` 课表查询

入参（query）：`storeId`（缺省取会员主门店）、`from`（YYYY-MM-DD，默认今天）、`to`（默认 `from+6`，服务端强制 ≤ 7 天且不与今天重叠过远）。

响应（节选）：

```json
{
  "code": 0,
  "data": {
    "serverTime": "2026-03-05T10:00:00+08:00",
    "timezone": "Asia/Shanghai",
    "store": { "id": 1, "name": "望京店", "phone": "010-12345678" },
    "days": [
      {
        "date": "2026-03-05",
        "sessions": [
          {
            "id": 1001,
            "courseName": "燃脂单车",
            "coachName": "李教练",
            "room": "A 教室",
            "startAt": "2026-03-05T19:00:00+08:00",
            "endAt": "2026-03-05T20:00:00+08:00",
            "capacity": 20,
            "bookedCount": 20,
            "remaining": 0,
            "statusLabel": "FULL_WAITLISTABLE",
            "waitlistOpen": true,
            "waitlistCount": 3,
            "waitlistLimit": 20,
            "bookingClosedAt": "2026-03-05T18:45:00+08:00",
            "waitlistClosedAt": "2026-03-05T17:00:00+08:00",
            "myBooking": { "id": 9001, "status": "WAITLISTED", "waitlistPosition": 2 }
          }
        ]
      }
    ]
  }
}
```

行为细节：
- 查询条件：`store_id = ? AND start_at >= 今天00:00 AND start_at < 今天00:00 + 7 天 AND end_at > now AND status <> 'CANCELLED'`（PRD R-01：含已开始未结束的课；已结束不展示）。已取消的课**要展示**（AC-1.5 要求卡片显示"已取消"），所以额外用 `OR status='CANCELLED'` 或去掉 status 过滤后在应用层剔除长期取消的课——实现上取"7 天窗口内全部排期（含已取消、已结束），应用层按 `now` 与状态过滤"，因为单门店 7 天数据量 ≤ 300 行。
- 排序：`start_at ASC`，按 `start_at` 归组日期（AC-1.6：23:00 的课归当天）。
- `statusLabel` 是**派生枚举**（7 个值 + 1 个工程补充值），与 AC-1.2 的标签一一对应，前端**只按它渲染**、不自己由剩余名额推算：`BOOKABLE` = 可预约 / `ALMOST_FULL` = 仅剩 N 位（`remaining ≤ 3`）/ `FULL_WAITLISTABLE` = 已满，可候补 / `FULL_WAITLIST_CLOSED` = 已满，候补已结束（**AC-1.2 第 7 种**，`T-2h ≤ now < T-15min`，按钮禁用）/ `BOOKING_CLOSED` = 报名已截止 / `IN_PROGRESS` = 已开课 / `CANCELLED` = 已取消；`FULL_NOT_WAITLISTABLE`（工程补充）= 已满且候补未开放或队列已满（4.4 的待确认项，见 11.1 第 10 条）。命中条件与对应按钮/文案见 4.4 与 UX-design 6.2。
- `myBooking`：一次查询批量取该会员在本批次 sessions 上的有效记录（`idx_member_start` 反查），避免 N+1。
- `waitlistPosition`（AC-5.2）：`SELECT COUNT(*) FROM bookings WHERE session_id=? AND status='WAITLISTED' AND id < ?`（`idx_session_status_id` 覆盖），只在有 `WAITLISTED` 记录时算。
- 未登录可访问（`BROWSE`），此时 `myBooking = null`。

#### 5.3.2 `POST /api/v1/sessions/{id}/bookings` 预约

入参：`{ "clientRequestId": "uuid（可选）" }`。

服务端事务步骤（顺序即校验顺序，任一步失败整体回滚）：

1. `SELECT ... FROM members WHERE id = ? FOR UPDATE` —— **会员级行锁**，把同一会员的并发预约/候补串行化（解决"两个并发请求都通过时间冲突校验"的竞态，见 6.1）。
2. 校验会员状态：`status='ACTIVE'`（否则 `10003`）；`members.store_id = session.store_id`（否则 `20003`）。
3. 校验冻结：`member_suspensions` 有 `ACTIVE` 且 `end_at > now` → `20002`（返回 `frozenUntil`）。
4. 取排期行：`SELECT ... FROM course_sessions WHERE id = ? FOR UPDATE`（后续条件更新其实已足够，但显式锁便于在同事务内一致读取 `capacity/pending` 用于返回）。
5. 时间窗校验（`Clock.now()`，PRD R-02）：
   - `status='CANCELLED'` → `30002`
   - `now >= start_at` → `30004`
   - `now >= start_at - 15min` → `30003`
6. 时间冲突校验（PRD R-05/AC-2.4，区间相交、首尾相接不算）：
   ```sql
   SELECT id, session_id, session_start_at, session_end_at
     FROM bookings
    WHERE member_id = ?
      AND status IN ('ENROLLED','WAITLISTED')
      AND session_start_at < :endAt
      AND session_end_at   > :startAt
    LIMIT 1;
   ```
   命中 → `30006` + 冲突课程信息（拼课程名需再查一次，数据量 1 行）。
7. 名额占位（**不超卖的唯一判定**，ADR-4）：
   ```sql
   UPDATE course_sessions
      SET booked_count = booked_count + 1, updated_at = :now
    WHERE id = :sessionId AND booked_count < capacity;
   ```
   `affectedRows = 0` → 名额已满 → `30005`（带 `waitlistAvailable`，前端按 AC-2.6 引导进入候补）。
8. 插入预约行：
   ```sql
   INSERT INTO bookings (session_id, member_id, origin, status, session_start_at, session_end_at, created_at, updated_at)
   VALUES (:sessionId, :memberId, 'BOOK', 'ENROLLED', :startAt, :endAt, :now, :now);
   ```
   若命中 `UNIQUE(active_key)`（`ER_DUP_ENTRY`）→ 捕获并返回 **`code=0` + `idempotent: true`** + 已存在记录状态（等价于 AC-2.5 的"已预约"）。**注意：此分支必须回滚第 7 步的 `booked_count + 1`**（回滚靠事务，捕获异常后抛出让事务回滚，再在事务外查已有记录返回）。
9. 写 `booking_events(BOOKED)`；埋点 `预约提交`（结果=成功/满员/冲突/无权限）在事务提交后写（异步、失败不影响）。

响应：

```json
{ "code": 0,
  "data": { "bookingId": 9001, "status": "ENROLLED", "sessionId": 1001,
            "bookedCount": 21, "remaining": 0, "serverTime": "2026-03-05T10:00:01+08:00" } }
```

#### 5.3.3 `DELETE /api/v1/bookings/{id}` 取消预约

事务步骤：

1. 取 `bookings` 行 `FOR UPDATE`；校验归属（`member_id = 当前会员`，否则 `10003`）；取 `course_sessions` 行 `FOR UPDATE`。
2. 幂等分支：状态已是 `CANCELLED_BY_MEMBER` → 返回 `code=0, idempotent=true`。
3. 状态校验：`SESSION_CANCELLED` → `50002`；`WAITLISTED` → 走"退出候补"逻辑（AC-3.7）；`ATTENDED/未到场`等课后状态不可取消（`50001`）。
4. 时间判定（PRD R-03）：
   - `now < start_at - 2h` → 免费取消，`late_cancel = 0`，**不计爽约**（AC-3.1）。
   - `start_at - 2h ≤ now < start_at` → 允许取消，`late_cancel = 1`，**插入 `no_show_records(occurrence_type='LATE_CANCEL', source='SYSTEM', occurred_at=start_at)`**，并触发 R-11 的处置判定（30 天内累计 n ≥ 3 → 冻结 7 天）。响应必须回传 `noShowCounted: true, noShowCount30d: n`，对应 AC-3.2 的前端提示。
   - `now ≥ start_at` → `50001`（AC-3.3）。
5. `UPDATE bookings SET status='CANCELLED_BY_MEMBER', cancel_source='MEMBER', cancelled_at=:now, late_cancel=...`；`UPDATE course_sessions SET booked_count = booked_count - 1, updated_at=:now`。
6. **候补转正（PRD R-08/AC-3.5，同一事务内）**：
   ```sql
   -- 只有当"释放发生在 [now, T-2h) 且候补未关闭"时，才产生一个待转正名额
   UPDATE course_sessions
      SET pending_promote_seats = pending_promote_seats + 1
    WHERE id = :sessionId
      AND waitlist_closed = 0
      AND :now < start_at - INTERVAL 2 HOUR
      AND waitlist_count > 0;
   ```
   然后调用 `WaitlistService.promote(sessionId)`（6.2 的算法，**复用同一事务**）。若第 6 步不满足条件（T-2h 后释放），名额直接回课表先到先得（AC-6.5），**不产生** `pending_promote_seats`。
7. 写 `booking_events(CANCELLED)`。

响应：
```json
{ "code": 0,
  "data": { "bookingId": 9001, "status": "CANCELLED_BY_MEMBER", "noShowCounted": false,
            "noShowCount30d": 0, "promotedCount": 1, "bookedCount": 19, "remaining": 1,
            "serverTime": "2026-03-05T10:00:02+08:00" } }
```
> `promotedCount` 只回传数量，**不回传被转正会员的任何身份信息**（隐私；取消者不需要知道谁补位）。

#### 5.3.4 `POST /api/v1/sessions/{id}/waitlist` 加入候补

事务步骤（前 1~2 步同预约）：

1. `SELECT members ... FOR UPDATE`；身份/门店/冻结校验。
2. 取排期行 `FOR UPDATE`；校验：
   - `status='CANCELLED'` → `30002`；`now >= start_at` → `30004`。
   - **`now >= start_at - 2h`** → `40001`（AC-5.3"本站候补已于开课前 2 小时结束"）。**正常路径不会走到这里**：该区间内课表卡片已显示 `已满，候补已结束`、详情页按钮禁用（AC-1.2 第 7 种标签），本分支只用于防客户端绕过前端直接调接口。
   - **`booked_count < capacity`** → `40002`（AC-5.3 第二种提示"仍有名额，可直接预约"）。
   - `waitlist_limit = 0` 或 `waitlist_count >= waitlist_limit` → `40003`（AC-5.4；`waitlist_limit=0` 表示不开放候补）。
3. 时间冲突校验（同预约第 6 步）→ `30006`（AC-5.6）。
4. 占位：`UPDATE course_sessions SET waitlist_count = waitlist_count + 1 WHERE id=? AND waitlist_count < waitlist_limit`；`affectedRows=0` → `40003`。
5. 插入 `bookings(origin='WAITLIST', status='WAITLISTED', waitlist_joined_at=:now)`；`UNIQUE(active_key)` 冲突 → `code=0, idempotent=true` + 既有记录（AC-5.6"最多 1 条"）。
6. 计算序号：`SELECT COUNT(*) FROM bookings WHERE session_id=? AND status='WAITLISTED' AND id < :newId` + 1 → 返回 `position`（AC-5.1/AC-5.2）。**注意这必须在同一事务内、拿到新行 id 之后计算**。
7. 写 `notifications(WAITLIST_JOINED, recipient=会员, push_required=0)`（消息中心可见，对应 AC-6.3 的"候补中"事件类型）。

响应：`{ waitlistId, position, queueLength, waitlistClosedAt, bookedCount, remaining }`。

> 说明（AC-5.2）：序号是**动态计算**的，不落库。前面的人退出后，其他人下次查询自然看到更小的序号，不需要任何"序号前移"的写操作。这是"用查询代替同步"的简化。

#### 5.3.5 `GET /api/v1/me/bookings` 我的预约

- `scope=upcoming`：`status ∈ ('ENROLLED','WAITLISTED')` 且 `session_end_at > now`，按 `session_start_at ASC`（AC-2.10）。
- `scope=history`：其余全部，按 `session_start_at DESC`。
- 每条返回：课程名、教练、教室、时间、4.4 映射后的 `displayStatus`、`displayStatusText`、`cancelFreeUntil`/`cancelDeadlineAt`、`waitlistPosition`（仅 `WAITLISTED`）、`promoteFailReason`（仅 `WAITLIST_PROMOTE_FAILED`）。

### 5.4 教练端接口

| # | 方法 | 路径 | 说明 |
|---|---|---|---|
| 1 | POST | `/api/v1/coach/auth/login` | `{code, phoneCode}` → 绑定 `coaches.openid`，返回带 `coachId` 的 JWT；未匹配 → `20001` 语义的"未查到教练信息" |
| 2 | GET | `/api/v1/coach/sessions` | `?from&to`（默认今天~未来 2 天，AC-4.1）：只返回 `coach_id = 自己` 的课，含 `bookedCount/capacity`、`needCheckInCount`、`signIn` 状态、`rosterStatus` |
| 3 | POST | `/api/v1/coach/sessions/{id}/sign-in` | **签到**（下详） |
| 4 | POST | `/api/v1/coach/sessions/{id}/sign-in/exception` | **异常签到**：`{reason(1~100字), latitude?, longitude?, accuracy?}` → 课程 `OPS_PENDING`，写运营待办（AC-4.4/4.9） |
| 5 | GET | `/api/v1/coach/sessions/{id}/roster` | 课后名单：`status='ENROLLED'` 的会员（姓名/昵称、预约时间），**不含候补**（AC-5.7），默认 `attendance=UNKNOWN`（前端默认全勾"到场"） |
| 6 | PUT | `/api/v1/coach/sessions/{id}/roster` | 提名单：`{ "absences": [memberId,...] }`（下详） |

#### 5.4.1 `POST /api/v1/coach/sessions/{id}/sign-in` 教练签到

入参：`{ "latitude": 39.99, "longitude": 116.47, "accuracy": 30, "clientRequestId": "uuid" }`。

服务端步骤：

1. 取排期行；`coach_id ≠ 当前 coachId` → **`60003`（防代签第一道：身份绑定）**，且**不泄露**该课是否存在敏感信息前先判权限。
2. `status='CANCELLED'` → `60007`（AC-4.10）。
3. 已存在有效签到（`active_session_id` 唯一）→ `code=0, idempotent=true` + `{signedAt, isLate}`（AC-4.7）。
4. 时间窗（服务端 `Clock.now()`，PRD R-10）：
   - `now < start_at - 15min` → `60001` + `windowOpenAt`（AC-4.2）。
   - `now > start_at + 15min` → `60002` + `storePhone`（AC-4.6）。
5. 定位校验：
   - 缺经纬度，或 `accuracy > 200m` → `60005`（`allowException: true`）。
   - Haversine 距离 `distance > stores.sign_in_radius_m` → `60004` + `distanceM`（AC-4.4）。
6. 插入 `sign_ins(status='CONFIRMED', method='SELF_LOCATION', signed_at=:now, is_late=(:now > start_at), distance_m, accuracy_m, request_ip, user_agent)`；`UNIQUE(active_session_id)` 冲突 → 返回 `60006` 幂等成功。
7. `UPDATE course_sessions SET status='SIGNED_IN'`。
8. 写 `booking_events`/审计日志；埋点 `教练签到`（距开课分钟数、是否迟到）。

响应：`{ signInId, signedAt, isLate, distanceM, sessionStatus:'SIGNED_IN' }` → 前端按 AC-4.5 提示"已签到（迟到）"或"签到成功"。

#### 5.4.2 `PUT /api/v1/coach/sessions/{id}/roster` 提交到场名单

1. 身份校验（本人执教，否则 `10003`）；`status='CANCELLED'` → `60007`（本课已被取消）。
2. 时间：`now < end_at` → `60008`（AC-7.1）；`now > end_at + 24h` → `60009`（AC-7.2）。
3. `absences` 里的 id 必须都在本课 `ENROLLED` 名单内，否则 `60010`（防越权标记别人）。
4. 事务内：
   - `UPDATE bookings SET attendance = CASE WHEN id IN (:absences) THEN 'ABSENT' ELSE 'PRESENT' END, attendance_source='COACH', attendance_set_at=:now WHERE session_id=? AND status='ENROLLED'`。
   - `UPDATE course_sessions SET roster_status='SUBMITTED', roster_submitted_at=:now, roster_source='COACH'`。
   - **爽约生成（仅当"课程实际发生"，AC-7.4）**：若该课无已确认签到 → **不生成任何 `no_show_records`**，仅在 `booking_events` 记一条 `ATTENDANCE_SET` 并保留 `attendance` 供运营后续决定（默认不计）。
   - 若课程已确认发生：对每个 `ABSENT` 会员插入 `no_show_records(ABSENT, source='COACH', occurred_at=start_at)`（`UNIQUE` 保证幂等）。
   - 对每个受影响会员调用 `NoShowPolicy.evaluate(memberId, now)`：统计 30 天窗口内 `ACTIVE` 记录数（`occurred_at > now - 30d`）→ n ≥ 3 且无 `ACTIVE` 冻结 → 创建 `member_suspensions(start_at=now, end_at=now+7d)`，**并执行 PM 确认的增补动作：自动退出该会员名下所有 `WAITLISTED` 记录**（原因=预约权限冻结，写消息中心，`waitlist_count` 相应减少，`booking_events` 记 `WAITLIST_LEFT`）。
   - 写 `notifications(NO_SHOW_WARNING / BOOKING_FROZEN)`（R-11："n ≤ 2 仅记录并站内提醒"）。
5. 响应：`{ submittedAbsences: n, noShowRecorded: m, frozenMembers: k }`。

#### 5.4.3 `GET /api/v1/coach/sessions` 说明

- 默认范围 `[今天 00:00, 今天+2 天 24:00)`（AC-4.1）；服务端强制 `coach_id = 自己`，**不接受客户端传 coachId**。
- 每节课返回：`id, courseName, room, startAt, endAt, capacity, bookedCount, needCheckInCount, sessionStatus, signIn: {signedAt, isLate, status} | null, rosterStatus`。
- 学员姓名在教练端展示为**姓 + "会员"或昵称**，不展示手机号（最小化敏感信息暴露）。

### 5.5 运营后台接口（Web）

| # | 方法 | 路径 | 说明 |
|---|---|---|---|
| 1 | POST | `/api/v1/ops/auth/login` | 账号密码 → JWT（`scope='OPS'`，带 `role`、`storeId?`） |
| 2 | POST | `/api/v1/ops/members/import` | CSV 上传（手机号、姓名、有效期）→ `{created, updated, conflicts:[{row, phoneLast4, reason}]}`（PRD 待解决 1 的实现） |
| 3 | GET | `/api/v1/ops/members` | `?keyword&storeId&page`（`keyword` 匹配姓名或手机号 hash，手机号明文只在请求内计算 hash，不落日志） |
| 4 | POST | `/api/v1/ops/members/{id}/store-change` | `{toStoreId}` → 30 天 1 次校验（`70001`） |
| 5 | GET/POST | `/api/v1/ops/courses` | 课程模板增改 |
| 6 | POST | `/api/v1/ops/sessions` | 排课：`{courseId, coachId, room, dateList[], startTime, durationMin, capacity, waitlistLimit}`；校验教练时段重叠（C-5）、教室冲突（C-6）、容量 1~50、`waitlistLimit ≤ capacity` |
| 7 | PATCH | `/api/v1/ops/sessions/{id}` | 改容量/候补上限/教室/教练；**改时间受限**（ADR-10） |
| 8 | POST | `/api/v1/ops/sessions/{id}/cancel` | `{reason}`：`now < start_at - 1h` 正常；否则允许但置 `cancel_overdue=1`（R-14）。全员 `SESSION_CANCELLED` + 候补全员 `SESSION_CANCELLED` + 写消息中心（AC-6.10） |
| 9 | GET | `/api/v1/ops/sessions/{id}/roster` | 报名名单（含候补列表与序号） |
| 10 | PUT | `/api/v1/ops/sessions/{id}/roster` | 运营代提交/修正到场名单（来源=`OPS`；PRD AC-7.4 的"由运营在后台决定是否计"，**默认不计**：只有运营显式勾选未到场才生成记录） |
| 11 | POST | `/api/v1/ops/sessions/{id}/sign-in-backfill` | 签到补录：`{coachId, signedAt, reason}` → `sign_ins(method='OPS_BACKFILL', status='CONFIRMED')`，课程 `SIGNED_IN`；与教练自助签到**来源可区分**（AC-4.11） |
| 12 | POST | `/api/v1/ops/sign-ins/{id}/review` | `{decision:'APPROVE'|'REJECT', note}`：通过 → `APPROVED` + 课程 `SIGNED_IN`；驳回 → `REJECTED` + 课程**回到 `OPS_PENDING`**（= PRD AC-4.9 的 `待运营确认`，界面「待门店确认」）+ 通知教练重新处理（不得落 `COACH_NOT_SIGNED_IN`，见 4.4 术语映射） |
| 13 | POST | `/api/v1/ops/no-shows/{id}/revoke` | `{reason}`：`REVOKED` + 重算 30 天次数 + 若 < 3 则释放冻结（AC-7.6） |
| 14 | GET | `/api/v1/ops/sessions/{id}/roster.csv` | 导出（字段：课程、时间、教练、会员姓名、手机号后 4 位、状态，AC-7.7） |
| 15 | GET | `/api/v1/ops/metrics/daily` | 灰度期观测：O1~O6 各分子/分母（PRD 第 2 节） |
| 16 | POST | `/api/v1/internal/jobs/{jobName}/run` | 手动触发任务（仅 `ADMIN` + IP 白名单），用于补跑与灰度演练 |

### 5.6 模块间接口（内部）

```
BookingService.book(memberId, sessionId, opts)      -> BookResult | DomainError
BookingService.cancel(memberId, bookingId, opts)    -> CancelResult | DomainError（内部调用 WaitlistService.promote）
WaitlistService.join(memberId, sessionId, opts)     -> {waitlistId, position} | DomainError
WaitlistService.leave(memberId, sessionId, opts)    -> {left: boolean, idempotent: boolean}
WaitlistService.promote(sessionId)                  -> {promoted: number[], failed: {bookingId, reason}[]}   // 必须在调用方事务内
SignInService.signIn(coachId, sessionId, loc, opts) -> SignInResult | DomainError
AttendanceService.submitRoster(actor, sessionId, absences) -> RosterResult
NoShowPolicy.evaluate(memberId, now)                -> {count30d, frozenUntil|null, createdSuspensionId|null}
NotificationService.enqueue(tx, event)              -> notificationId        // 只写表，不发送
NotificationService.dispatchBatch(limit)            -> {sent, failed, gaveUp} // worker 调用
ErrorCode（shared 包）：与 5.2 表一一对应的常量，被小程序端复用
```

`DomainError` 是 Domain 层唯一允许抛出的错误类型（携带 `code` + `data`），由 Adapter 层的异常过滤器统一转成 HTTP 响应；Domain 层不 import 任何 HTTP 相关代码。

---

## 6. 关键难点与方案

### 6.1 高并发下的名额扣减与超卖防护

**要解决的问题**：AC-2.6——容量只剩 1 个时两个会员同时提交，只能 1 人成功，且失败者能看到"名额刚刚被约满"并进入候补。

**方案：三层防线**

| 层 | 机制 | 挡住什么 |
|---|---|---|
| 1（正确性主防线） | `UPDATE course_sessions SET booked_count = booked_count + 1 WHERE id=? AND booked_count < capacity`，以 `affectedRows` 作为"是否占到名额"的唯一判据 | 并发扣减导致超卖（"读-改-写"竞态） |
| 2（重复请求兜底） | `UNIQUE(bookings.active_key)` | 同一会员并发/重试双写、同一会员既约又候补（R-06） |
| 3（时间冲突竞态） | 事务开头 `SELECT members ... FOR UPDATE` 锁会员行 | 同一会员并发预约两节时间重叠的课，两个请求都通过冲突校验 |
| 4（数据自愈） | 每日 03:00 对账任务：`booked_count ≠ COUNT(booking.status='ENROLLED')` 或 `booked_count > capacity` → 记 ERROR 日志 + 告警 + 修数（以明细表为准） | 任何未预见的写路径遗漏 |

**为什么不用 `SELECT ... FOR UPDATE` 后再判断剩余名额**：两条路都能保证正确，但"先锁定读、再应用层比较、再写"把判断放在应用层，多一次往返和一次可能的死锁面；条件更新把"比较 + 写入"合成一条由 InnoDB 原子执行的语句，代码更短、竞态面更小。**代价**：拿不到"失败时到底是满了还是被取消"的额外信息——所以 `30005` 之后由前端再拉一次课表/详情即可。

**为什么不用 Redis 原子扣减（`DECR`/Lua）**：见 ADR-3。核心矛盾是"Redis 扣减成功 + MySQL 写预约失败"需要补偿，等于用分布式一致性换一个当前不存在的性能问题。

**事务与隔离级别**：`READ COMMITTED` + 显式行锁。理由：业务只依赖"单行原子更新"与"按主键/索引加锁"，不需要范围锁；RC 能显著减少 gap 锁与死锁概率。事务必须**短**：预约事务内只有 5 条左右 SQL、无外部 HTTP 调用（**通知、埋点全部在事务外**）。

**限流与削峰**：同会员写接口 5 次/分钟（`10004`）、同 IP 60 次/分钟；这防的是误点/脚本，不是防超卖（超卖靠数据库）。不做排队/令牌桶。

**如何验证**（本阶段不能运行，写明方法）：
1. 单元测试：`BookingPolicy` 的时间窗与名额边界（`booked == capacity - 1`、`now == start_at - 15min` 整点）。
2. 集成测试：对"容量 1 + 20 个并发请求"断言恰好 1 个 200 且 `booked_count = 1`（用 20 个并发连接 + 真实 MySQL，不能用 SQLite，SQLite 的锁语义不同）。
3. 幂等测试：同一 `memberId + sessionId` 并发 5 次 → 恰好 1 条 `ENROLLED` + 4 次 `30007/idempotent`。
4. 对账测试：人为把 `booked_count` 改错 → 跑对账任务 → 被修正并告警。
5. 生产演练（灰度期）：门店首节课做小规模并发观察（PRD 内测阶段的"并发验证"目标）。

### 6.2 候补队列的顺序与自动转正

**队列的表达**（ADR-6）：不单独建队列表。队列 = `bookings WHERE session_id=? AND status='WAITLISTED' ORDER BY id ASC`。

- R-07 要求"按时间戳升序，时间戳相同按系统自增序号"——`id` 是全局单调自增，**天然就是"加入顺序"的严格全序**，且不用处理时间戳并列。`waitlist_joined_at` 只用于展示与埋点。
- 序号 `position = COUNT(*) WHERE session_id=? AND status='WAITLISTED' AND id < :myId + 1`，动态计算（AC-5.2 无需写操作）。
- 退出候补 = 该行置 `CANCELLED_BY_MEMBER`、`waitlist_count - 1`；后面的人序号自然变小。
- 重新加入 = 插入新行（`id` 更大）→ 天然排到队尾（R-07）。
- 队列上限：`waitlist_limit` 用条件更新占位（C-4）。

**待转正名额 `pending_promote_seats`——为什么必须有这个字段**（这是本方案里最关键的一个设计决定）：

- 直觉做法是"转正条件 = `booked_count < capacity` 且有候补"。但这会**违反 R-04**：运营扩大容量后，`booked_count < capacity` 成立，激进的扫描会把扩容名额转正给候补队列，而 PRD 明确要求"新增名额**不**自动转正候补，需运营手动通知"。
- 所以本方案把"名额释放"这个**事件**显式记下来：只有"会员取消/运营取消某条预约"在 `[now, T-2h)` 内发生时，才对 `pending_promote_seats + 1`；容量扩张**不增加**它。
- `promote()` 消费它：每成功转正 1 人，`pending_promote_seats - 1`、`booked_count + 1`。两个条件同时成立才能转正（`pending_promote_seats > 0 AND booked_count < capacity`），所以既不会超卖，也不会把扩容名额送出去。
- 兜底任务据此扫描："`pending_promote_seats > 0`" 就是"有名字未定的名额"，即使取消事务中途崩溃（不可能，同事务）或进程重启，也能被下一轮扫描补上。

**`promote(sessionId)` 算法（在调用方事务内执行，带行锁）**

```
function promote(sessionId) -> {promoted[], failed[]}:
  s = SELECT * FROM course_sessions WHERE id = :sessionId FOR UPDATE      # 锁排期行，序列化并发转正
  if s.status == 'CANCELLED': pending=0; return
  if now >= s.start_at - 2h:                                              # R-08 / AC-6.5：T-2h 后不转正
      return                                                              # 注意：调用方也不会增加 pending
  if s.booked_count >= s.capacity:                                        # 容量被缩小到低于已约人数（R-04）
      s.pending_promote_seats = 0; return                                 # pending 已无意义，清零避免兜底任务反复扫
  while s.pending_promote_seats > 0 and s.booked_count < s.capacity:
      wb = SELECT * FROM bookings
            WHERE session_id = :sessionId AND status = 'WAITLISTED'
            ORDER BY id ASC LIMIT 1 FOR UPDATE                            # 取队首（FIFO）
      if wb == null:
          s.pending_promote_seats = 0                                     # 队列已空：名额回课表，先到先得
          break
      # ---- 跳过判定（顺序固定，PM 确认：时间冲突优先，再权限冻结；只记第一个命中的原因）----
      if hasTimeConflict(wb.member_id, s.start_at, s.end_at):             # R-05 / AC-6.7
          fail(wb, 'TIME_CONFLICT');      continue                        # 从队列移除 + 写消息中心 + 继续下一人
      if isSuspended(wb.member_id, now):                                  # PM 确认方案 A
          fail(wb, 'MEMBER_SUSPENDED');   continue
      # ---- 转正（系统单方面动作：无确认、无倒计时、无中间态，AC-6.1b）----
      UPDATE course_sessions
         SET booked_count = booked_count + 1, pending_promote_seats = pending_promote_seats - 1
       WHERE id = :sessionId
      UPDATE bookings SET status = 'ENROLLED', promoted_at = :now WHERE id = wb.id
      INSERT booking_events(event_type='PROMOTED', actor_type='SYSTEM', ...)
      INSERT job_runs(job_name='promote_one', biz_key=CONCAT('booking:', wb.id))   # 幂等键（UNIQUE，冲突则忽略）
      NotificationService.enqueue(WAITLIST_PROMOTED, recipient=wb.member_id,      # 事务内只写表
          dedupe_key=CONCAT('WAITLIST_PROMOTED:', wb.id), push_required=1,
          content='你已排上《课程名》MM-DD HH:mm，来不了请在 T-2h 前免费取消，不计爽约')
      promoted.push(wb.id)

function fail(wb, reason):
  UPDATE bookings SET status='WAITLIST_PROMOTE_FAILED', promote_fail_reason=:reason WHERE id = wb.id
  UPDATE course_sessions SET waitlist_count = waitlist_count - 1 WHERE id = wb.session_id
  INSERT booking_events(event_type='PROMOTE_SKIPPED', reason=:reason, actor_type='SYSTEM')
  NotificationService.enqueue(WAITLIST_FAILED, dedupe_key=CONCAT('WAITLIST_FAILED:', wb.id), push_required=1)
```

**对应 PRD 验收标准**：

| AC | 本方案如何满足 |
|---|---|
| AC-6.1 / 6.1b（5 秒内自动转正、无中间态） | 转正在取消的**同一事务内**完成，耗时毫秒级；状态机直接 `WAITLISTED → ENROLLED`，没有待确认态，接口层没有确认端点 |
| AC-6.1c（从未打开的会员照样按已预约判定） | 转正只依赖数据库状态，通知是旁路；课后判定读 `bookings.status='ENROLLED'` 与 `attendance`，与"是否看过通知"无关 |
| AC-6.2（拒绝授权也成立） | `enqueue` 与 `dispatch` 完全解耦（6.6）；`push_status` 失败不回滚任何业务字段 |
| AC-6.3（消息中心兜底） | `notifications` 行在事务内写好，`push_status='PENDING'`；即使推送失败，站内记录仍在 |
| AC-6.4（一次释放 N 个名额转正 N 人） | `while` 循环，每次成功消耗 1 个 `pending_promote_seats` |
| AC-6.5（T-2h 后释放不转正） | 取消事务里的 `pending + 1` 带 `:now < start_at - 2h` 条件；`promote()` 开头再判一次（双保险） |
| AC-6.6（逐位推进） | 每轮取队首，转正后下一轮取到的是新的队首 |
| AC-6.7（时间冲突跳过、不卡队列） | `hasTimeConflict` → `fail` → `continue`；`pending` 不被消耗，继续尝试下一位 |
| 冻结会员被轮到（PM 确认 A 的 6 条口径） | ①判定时点=转正那一刻读 `member_suspensions`；②跳过→从队列移除→记 `WAITLIST_PROMOTE_FAILED(原因=预约权限冻结)`→写消息中心→继续下一人；③无人可转正则 `pending=0`，名额回课表；④不自动回队，可重新排队尾；⑤转正成功后才被冻结的不追溯（转正已是 `ENROLLED`，冻结不影响已有预约）；⑥两种原因同时命中只记第一个（时间冲突优先） |
| AC-6.8（转正后 T-2h 前取消仍免费） | 转正不写 `late_cancel`、不产生爽约记录；取消走 5.3.3 的统一规则 |
| AC-6.9（T-2h 队列关闭） | `close_waitlists` 任务（6.3） |
| AC-6.10（课程取消 → 全员退出候补） | 后台取消事务内批量置 `SESSION_CANCELLED` + `waitlist_count=0` + `pending=0` + 逐条消息 |

**幂等与并发**：

- 同一课的并发转正被排期行锁串行化；重复执行时 `pending` 已为 0 或 `booked_count` 已满，循环自然不做事。
- `job_runs(job_name='promote_one', biz_key='booking:{id}')` 的唯一约束是"同一个候补行只被转正一次"的硬保证（即使未来出现两条并发路径）。
- 兜底任务 `promote_missing`（每 30 秒）只扫 `pending_promote_seats > 0`，条件写入 `job_runs(job_name='promote_missing', biz_key='session:{id}:{pending值}')` 防止同轮重复。

### 6.3 取消截止时间与定时任务

**时间基准（R-16）**：所有临界点判定用**服务端时钟**（`Clock` 单例，`Asia/Shanghai` 换算，DB 存 UTC）。
- 为什么用应用时钟而不是 `NOW()`：临界点逻辑（`now == T-15min` 拒绝、`now == T-2h` 关队列）需要与定时任务、单元测试共用同一时间源，应用侧单例便于**注入可控时钟**做边界测试。
- 前置要求：服务器与数据库都开 NTP 同步；应用启动时记录一次与 DB 的时钟偏差（`SELECT NOW(3)`），偏差 > 2 秒则启动告警（不能静默跑偏）。
- **不信任客户端时间**：请求里带的时间只用于查询过滤。

**任务清单**（全部为"扫描式幂等"：条件查询 + 唯一键 + 条件更新，重跑安全）

| 任务 | 频率 | 扫描条件 | 动作 | 幂等键 | 失败处理 |
|---|---|---|---|---|---|
| `close_waitlists` | 每 1 分钟 | `status='SCHEDULED' AND waitlist_closed=0 AND start_at <= now + 2h`（不依赖 `waitlist_count`，避免计数不一致时扫不到） | 该课所有 `WAITLISTED` → `WAITLIST_EXPIRED`；`waitlist_closed=1`；`waitlist_count=0`；`pending_promote_seats=0`（T-2h 后不再转正，清零避免兜底任务反复扫）；逐条 `WAITLIST_EXPIRED` 消息（AC-6.9） | `close_waitlist:session:{id}` | 单课失败记 ERROR 继续其他；下次扫描条件仍成立会重试 |
| `promote_missing` | 每 30 秒 | `pending_promote_seats>0 AND waitlist_closed=0 AND status<>'CANCELLED' LIMIT 50` | 逐课调用 `promote()`（6.2） | `promote_sweep:session:{id}:{pending}` | 同上（这是"取消事务已转正但队列还有残留"的兜底） |
| `coach_no_sign_in_todo` | 每 1 分钟 | `status = 'SCHEDULED' AND start_at <= now - 15min` | 课程置 `COACH_NOT_SIGNED_IN`；写运营待办 `COACH_NO_SIGN_IN`（AC-4.11）。**`OPS_PENDING` 的课不被本任务改写**（AC-4.4 要求它保持 `待运营确认`；它的运营待办在提交异常签到时已写入；运营超时未审核的兜底口径待 PM 确认）；**该课会员一律不计爽约**（由 5.4.2 的"课程实际发生"判定天然保证） | `coach_no_signin:session:{id}` | 下次扫描补偿 |
| `auto_submit_roster` | 每 10 分钟 | `status <> 'CANCELLED' AND roster_status='NOT_SUBMITTED' AND end_at <= now - 24h` | 全部 `ENROLLED` 置 `attendance='PRESENT', source='DEFAULT'`；`roster_status='AUTO_DEFAULTED'`（AC-7.2/R-12）；**不生成任何爽约记录** | `auto_roster:session:{id}` | 同上 |
| `finish_sessions` | 每 5 分钟 | `status IN ('SIGNED_IN','COACH_NOT_SIGNED_IN') AND end_at <= now` | 置 `FINISHED`（仅用于审计与列表分区，展示层本已按 `now` 派生） | `finish:session:{id}` | 同上 |
| `unfreeze_expired` | 每 10 分钟 | `member_suspensions WHERE status='ACTIVE' AND end_at <= now` | 置 `RELEASED` + `BOOKING_UNFROZEN` 消息（R-11 到期自动恢复） | `unfreeze:{suspensionId}` | 同上 |
| `dispatch_notifications` | 每 10 秒 | `push_status='PENDING' AND (next_retry_at IS NULL OR next_retry_at <= now) LIMIT 50`（`FOR UPDATE SKIP LOCKED`） | 调微信订阅消息接口（6.6） | 行级状态流转（`PENDING→SENT/FAILED/GAVE_UP`） | 退避重试 3 次后 `GAVE_UP` |
| `reconcile_counters` | 每日 03:00 | 昨日有变动的课 | 校验 `booked_count`/`waitlist_count` 与明细一致（6.1 防线 4） | `reconcile:{date}` | 不一致 → ERROR 日志 + 告警 + 修数 |
| `refresh_wx_token` | 每 30 分钟 | `expires_at < now + 10min` | 刷新 access_token | `GET_LOCK('wx_token_refresh')` | 失败重试，不影响业务 |

**调度实现**：`@nestjs/schedule` 在进程内注册 cron；每个任务执行前先 `SELECT GET_LOCK('job:{name}', 0)`，拿不到就跳过本次（保证即使将来多实例部署，同一时刻只有一个执行者），执行完 `RELEASE_LOCK`。任务包装器统一提供：超时（如 60 秒）、异常捕获（不抛出到进程）、结构化日志（`job_name, scanned, affected, duration_ms, error`）、指标上报。

**为什么用"扫描式 + 幂等键"而不是"事件驱动的一次性任务"**：
- 一次性任务（如"在 17:00 给这节课关队列"）在进程重启/漏跑后不会自愈，需要额外的补偿机制去发现"哪些漏了"——这本身就是在写扫描逻辑。
- 扫描式设计下，"漏跑"等于"下一轮照常命中"，不需要补偿；代价是每轮多几次索引查询（7 个任务合计 < 100 次查询/分钟，可忽略）。

**临界点精度**：T-2h/T-15min 的**业务判定**在请求路径上实时按 `Clock.now()` 计算（AC-2.7 要求整点拒绝），定时任务只负责"状态位/通知/批量清理"这类最终一致的工作，**任务延迟不影响用户体验与正确性**：
- 报名截止（T-15min）**没有**定时任务，纯请求时判定。
- 候补加入关闭（T-2h）也是请求时判定 `now >= start_at - 2h` 直接拒绝，任务只负责把存量 `WAITLISTED` 批量置为 `WAITLIST_EXPIRED` 与发消息（AC-6.9 的到期结算）。即使任务晚跑 1 分钟，用户也不会在 T-2h 后加进队列。

### 6.4 签到防代签

**能防住什么、防不住什么，先写清楚**：定位在客户端采集，可被虚拟定位/模拟器伪造。因此本方案的定位是"**把代签成本抬高 + 留下可追溯证据 + 异常走人工审核**"，不是密码学意义上的防伪造。PRD 决策 D4 已接受这个代价（对应 P2 才考虑门店二维码/设备）。

| 手段 | 实现 | 挡住 |
|---|---|---|
| 身份绑定（主要手段） | 签到接口从 JWT 取 `coachId`，**不接受客户端传 `coachId`**；校验 `session.coach_id === coachId`，不符直接 403 `60003` | 会员/其他教练替签（这是最主要、也最有效的一道） |
| 时间窗 | 服务端时间 `[T-15min, T+15min]`，窗口外 `60001/60002` | 提前批量签到、事后补签 |
| 定位距离 | 客户端上报经纬度 + `accuracy`；**服务端 Haversine 计算**距离，与 `stores.sign_in_radius_m`（默认 300）比较；缺经纬度或 `accuracy > 200m` 视为定位不可用 `60005` | 在家签到、跨店签到；精度太差时无法判断时宁可要人工审核 |
| 单次性 | `UNIQUE(sign_ins.active_session_id)`；`signed_at` 由服务端写入 | 重复签到、一节课多份记录（AC-4.7） |
| 异常签到闭环 | 定位失败/超距 → 提交原因（1~100 字，服务端强制）→ 课程 `OPS_PENDING` → 运营 24 小时内通过/驳回；驳回后教练可重新提交（生成新的 `sign_ins` 行，历史保留） | 定位漂移误拦；同时把"人工判定"变成有记录的动作（AC-4.4/4.9） |
| 补录来源可区分 | 运营补录写 `method='OPS_BACKFILL'`，与教练自助签到在数据上可区分 | 事后审计"到底谁签的"（AC-4.11） |
| 审计痕迹 | `sign_ins` 记 `request_ip`、`user_agent`、`signed_at`、`distance_m`；`booking_events` 记状态迁移；结构化日志含 `requestId` | 事后追溯刷签模式（如同一 IP 短时间批量签到多节课、距离全为 299m 这类可疑模式可人工排查） |
| 反作弊监控 | 灰度期统计"异常签到占比"与"距离分布"，异常占比 > 10% 时放宽半径或改扫码（PRD 风险表已写） | 规则调优有数据依据 |

**明确不做**（避免过度设计）：不做设备指纹/风控 SDK（小程序环境无法可靠取设备 ID、合规风险高）；不做"同一手机多账号签到"检测（教练账号由门店发放，不在本期）；不做离线签到（离线状态无法保证时间窗与单次性）。

### 6.5 数据一致性与对账

| 风险 | 检测方式 | 修复 |
|---|---|---|
| `booked_count` 与明细不一致 | 每日 `reconcile_counters`；也可随时手动触发 | 以 `bookings` 明细为准重算，写入 ERROR 日志与告警（说明发生了未预见的写路径遗漏，必须排查根因） |
| `booked_count > capacity`（超卖发生了） | 对账任务 + 实时告警 | 立即告警（这是 PRD 第 11 节定义的**回滚触发条件**）：停止该门店线上预约入口、人工处理当天名单 |
| 有效预约数 > 容量但用户看到"可预约" | 条件更新天然防止 | — |
| 会员同时持有两节冲突课 | 时间冲突校验 + 会员行锁 | 对账任务补充检查 `status ∈ (ENROLLED,WAITLISTED)` 内的区间相交对，发现则告警 |
| 冻结记录与 30 天次数不一致 | 撤销爽约后重算（AC-7.6 要求"重算"） | `NoShowPolicy.evaluate` 可随时重算，作为唯一口径 |

---

### 6.6 通知（best-effort）与消息中心

**原则（R-15）**：通知发送与业务状态**完全解耦**，用"数据库即队列"（ADR-8）。

```
业务事务内：NotificationService.enqueue(tx, event)   →  INSERT notifications(..., push_status='PENDING')
                                                        （与业务状态同事务，原子；只写表，不做任何网络调用）
事务提交后：dispatch_notifications 任务（每 10 秒）
             ├─ 取 PENDING 且到期重试的 ≤50 条
             ├─ GET_LOCK 保证单执行者；FOR UPDATE SKIP LOCKED 防重复取
             ├─ 调 wx.subscribeMessage.send({touser:openid, template_id, page, data})
             ├─ 成功 → SENT
             ├─ 43101（用户拒收）/ 40003（openid 无效）→ FAILED（不重试，站内已有记录）
             ├─ 45009（频次超限）/ 网络异常 → attempts+1, next_retry_at = now + 2^attempts × 30s
             └─ attempts > 3 → GAVE_UP（记日志 + 告警，业务无感）
```

要点：
- `enqueue` 在**事务内**保证"业务状态变了就一定有一条站内记录"；`dispatch` 在**事务外**保证"推送失败只影响推送"。
- `dedupe_key` 唯一约束保证同一事件（如某条预约的转正）**只通知一次**，即使任务重跑或代码被多条路径调用。
- 消息内容自包含（R-15/AC-6.3）：`{事件类型, 课程名, 时间, 当前状态, 可执行操作}`，例如转正消息固定带"来不了请在 T-2h 前免费取消，不计爽约"。
- 模板 ID/字段映射**配置化**；查不到可用模板时降级为仅站内消息（见 3.3）。
- 消息中心未读数：`COUNT(*) WHERE recipient=? AND read_at IS NULL`（`idx_unread` 覆盖）。

---

## 7. 质量属性检查

### 7.1 可靠性

| 场景 | 行为 |
|---|---|
| 单个写请求失败（DB 抖动/超时） | 返回 `10005`，事务整体回滚，客户端保留原状态并提示重试（AC-3.6 明确不允许"界面显示已取消但实际未取消"）；前端不做乐观更新，状态以服务端响应为准 |
| 通知全链路失败（拒收/额度/模板下线） | 业务不受影响（AC-6.2）；站内消息兜底 |
| 微信 `code2Session` 失败 | 不签发新 token，前端提示重试；已登录用户用现有 token 继续（JWT 有效期 7 天） |
| 进程重启 | 扫描式定时任务自愈（6.3）；服务启动时先跑一轮 `promote_missing` / `close_waitlists`，弥补停机窗口 |
| DB 主从/RDS 故障 | 依赖云 RDS 的自动切换（MVP 不做自建高可用）；期间接口降级为 503，前端提示"系统繁忙，请稍后重试" |
| 超卖发生（最坏情况） | 按 PRD 第 11 节回滚：关闭该门店线上预约入口、保留查询、人工处理名单、修复后重新灰度 |
| 定时任务重跑 | 幂等键 + 条件更新，已验证设计上不会重复计爽约/重复通知/重复转正 |

### 7.2 安全

| 项 | 措施 |
|---|---|
| 认证 | JWT HS256，密钥取自环境变量；`exp` 7 天；`scope` 与 `identities` 决定可访问范围；`BROWSE` 只能读课表 |
| 授权 | 每次写操作都在**服务端**重新校验归属：会员只能操作自己的 `bookingId`；教练只能签到/提名单自己的课；后台 `STORE_OPS` 只能操作自己门店数据（越权返回 403，且不泄露资源是否存在） |
| 输入校验 | 所有入参用 DTO（`class-validator`）白名单校验：枚举值、长度、数值范围（容量 1~50、原因 1~100 字）、日期格式、分页上限 50；拒绝未知字段 |
| SQL 注入 | 全部走 Prisma 参数化查询；少量原生 SQL 也用参数绑定，禁止字符串拼接 |
| 敏感数据 | 手机号不存明文（AES-256-GCM 密文 + HMAC 哈希）；日志与错误信息里手机号只出现后 4 位、`openid` 掩码（前 6 后 4）；教练端与消息内容不展示完整手机号 |
| 密钥管理 | 微信 `AppSecret`、JWT 密钥、手机号加密密钥、DB 密码一律从环境变量读取，**不写进代码、不进日志、不进仓库**；`.env.example` 只写变量名 |
| 最小权限 | DB 账号只授予所需库的 CRUD（不给 DDL，迁移用独立账号）；后台按角色分权；内部任务接口仅 `ADMIN` + IP 白名单 |
| 限流 | 写接口按会员/IP 限流（`10004`）；登录接口按 IP + openid 限流防撞库 |
| 审计 | `booking_events` + 后台操作日志（谁在什么时候改了容量/取消了课/撤销了爽约）；签到留 IP/UA |
| 传输 | 全站 HTTPS（小程序要求已备案域名 + 合法 TLS 证书） |
| 隐私合规 | 遵循最小必要原则：只取手机号（业务必需），不取用户画像/位置常驻；定位只在签到动作内采集并在响应后丢弃（不长期存轨迹） |

### 7.3 性能（估算，含依据）

**估算依据（写明假设，非实测）**：

| 假设 | 值 | 来源 |
|---|---|---|
| 灰度规模 | 3 家门店，会员约 1500 人 | PRD 第 11 节灰度计划 |
| 日活比例 | 15% → 约 225 人/天 | 假设（无真实数据） |
| 人均日操作 | 8 次（看课表 3 + 详情 2 + 预约/取消/签到 3） | 假设，与 PRD 第 1.2 节频率一致 |
| 日请求总量 | 225 × 8 ≈ 1800 次/天 | 计算 |
| 高峰集中度 | 40% 请求集中在 19:00–21:00 | 假设（晚间约课高峰） |
| 峰值 QPS | 1800 × 40% ÷ 7200s ≈ 0.1 QPS 平均；按峰值系数 10 → **约 1 QPS** | 计算 |
| 全量规模（50 店） | 约 3.75 万会员、日活 5600、日请求 4.5 万 → 平均 1.3 QPS、峰值约 **10 QPS** | 按同比例外推 |

**结论**：单实例 Node（1 核 2G）+ MySQL 8 在 10 QPS 下有约两个数量级的余量。真正的压力点是**单节课名额的并发争抢**（同一行热点），而不是总 QPS。

| 潜在瓶颈 | 量化 | 缓解 |
|---|---|---|
| 单节课名额行锁竞争 | 极端场景：20 个请求抢 1 个名额，串行等待约 20 × 单事务耗时（估 3~5ms）≈ 100ms | 事务极短（无外部调用）、条件更新单语句、RC 隔离；不需要队列 |
| 定时任务扫描 | 7 个任务合计 < 100 次索引查询/分钟 | 全部走 `idx_status_start` / `idx_promote_sweep`，`LIMIT` 批次 |
| 课表查询 | 单门店 7 天 ≤ 300 行，索引范围扫描 | `idx_store_start` 覆盖；`myBooking` 批量反查避免 N+1 |
| 数据增长 | `bookings`：50 店 × 10 课/天 × 20 人 × 365 ≈ **365 万行/年**；`booking_events` 约 3~5 倍 | MVP 灰度期（3 店）仅约 22 万行/年；上全量前评估按 `session_start_at` 做年度归档表（历史记录只读查询） |
| 消息发送 | 峰值估计 < 1 条/秒 | 每 10 秒批量 50 条，远超需求 |

**未验证声明**：以上均为估算，未在真实环境压测。需要压测时按 6.1 的验证方法做"容量 1 + 20 并发"与"课表查询 50 并发"两组。

### 7.4 可维护性

- 模块边界 = NestJS Module 边界，跨模块访问只能通过 Provider 公开的方法（禁止跨模块直接用 Prisma 模型）。
- 业务规则集中在 Domain（`BookingPolicy`/`WaitlistPolicy`/`SignInPolicy`/`NoShowPolicy`/`TimeWindow`），纯函数、有单测；改动规则只改一处。
- 错误码集中在 `packages/shared`，小程序端共用（文案与码一一对应），不会出现两端理解的码不一致。
- 三端（会员/教练/后台）复用同一套 Application 用例，规则不会分叉（例如"T-2h"只在 `TimeWindow` 里算一次）。
- 数据库约束做了"文档化"（本文件第 4 节即为准），迁移脚本进仓库、按序执行。

### 7.5 可观测性

| 需要回答的问题 | 靠什么 |
|---|---|
| 这次预约为什么失败？ | 结构化日志：`requestId, memberId, sessionId, code, errorCode, latencyMs`；`code` 直接映射 PRD 的失败原因 |
| 转正有没有按时发生？ | `booking_events(PROMOTED)` + `promoted_at - 事件时刻` 的延迟分布；`promote_missing` 任务的 `affected` 计数（长期 > 0 说明取消事务有遗漏，需要排查） |
| 通知到达率如何？ | `notifications.push_status` 统计（SENT / FAILED / GAVE_UP）+ 错误码分布；对应 PRD 的 O7 观测 |
| 超卖有没有发生？ | `reconcile_counters` 结果 + 实时断言 `booked_count > capacity` 的告警（最高优先级告警） |
| 定时任务是否在跑？ | 每次执行写 `job_runs` + 日志；"某任务 N 分钟无执行记录"告警 |
| 教练定位是否误拦？ | 异常签到占比、`distance_m` 分布（PRD 风险表已定阈值 10%） |
| 微信登录是否异常？ | `code2Session` 失败率与错误码（如 `40029` code 无效、`45011` 频控） |
| 关键业务指标（PRD 第 2 节） | `events` 表 + `/api/v1/ops/metrics/daily`：预约成功率、候补转正率、到场率、爽约率、按时签到率 |

告警（MVP 用日志匹配 + 简单阈值，不引入 APM）：

| 级别 | 条件 |
|---|---|
| P0 | 出现 `booked_count > capacity`；对账不一致；预约接口 5xx 率 > 1% |
| P1 | 通知 `GAVE_UP` 比例 > 20%；某定时任务 30 分钟未执行；`promote_missing` 连续 3 轮有产出（说明取消路径可能漏转正） |
| P2 | 异常签到占比 > 10%；`code2Session` 失败率 > 5% |

---

## 8. 关键难点与风险

| 风险 | 影响 | 应对 |
|---|---|---|
| 并发预约超卖（AC-2.6 不通过） | 高：会员到店没位置，信任崩塌，PRD 定义了回滚条件 | 条件更新 + 唯一约束 + 会员行锁 + 每日对账（6.1）；列为一号必测项 |
| 候补转正的时序竞态（取消与转正、多次释放） | 高：名额丢失或重复占用 | 全部收敛到 `promote()` 一个入口 + 排期行锁 + `pending_promote_seats` 计数 + `job_runs` 幂等键（6.2） |
| 误用"扩容触发转正"违反 R-04 | 中：运营扩大容量会一次性放走多个名额，与 PRD 规则冲突 | 用 `pending_promote_seats`（事件驱动）而不是 `booked_count < capacity`（状态驱动）作为转正条件（6.2 已专门说明） |
| 定时任务漏跑/重启导致队列不结算 | 中：候补会员一直显示"候补中"，体验差 | 扫描式幂等设计 + 启动时补跑一轮 + 任务无执行记录告警（6.3） |
| 通知送达率低，转正会员不知道 | 中：O4 到场率下降 | 转正不依赖通知（D1）；站内消息中心 + 未读角标；订阅消息模板能否用需在真实账号查证（3.3） |
| 定位漂移误拦签到 | 中：教练弃用系统 | 异常签到 + 运营 24h 审核；半径可配；灰度期观测占比（6.4） |
| 定位可伪造（防代签不彻底） | 低~中：个别教练找人代签 | 本方案只能抬高成本 + 留痕（6.4），PRD 决策 D4 已接受；真正的解法是门店端设备/二维码（P2） |
| `ACCESS_TOKEN`/`AppSecret` 泄露 | 高：可冒用小程序发消息 | 只在服务端使用，环境变量注入，禁止进日志；泄露后可在公众平台重置 |
| 手机号加密密钥丢失 | 高：会员无法登录匹配 | 密钥与密文分开备份（KMS/密钥管理），写进运维手册；同时保留 `phone_hash` 是**不可逆**的，密钥丢失只影响"解密查看"，不影响登录匹配（降低了这个风险的破坏力——这也是选 hash+密文双存的原因之一） |
| 单人维护导致知识集中 | 中：出故障只有 1 人能修 | 本文件 + ADR 即为"为什么"的载体；关键运维动作（回滚、补跑任务、修数）写成 runbook（P0 后补） |
| 无真实规模数据，容量估算偏差 | 低：可能过早/过晚扩容 | 目标值已标为假设（PRD 已声明）；估算依据写在 7.3，灰度后按实测校准 |
| 教练不签到/不提名单 | 中：爽约数据失真（PRD R-12 已知偏差） | 技术侧：3 次点击内完成、默认全部到场（零操作路径）、待办提醒；不歪曲数据（`attendance_source` 保留来源，便于后续审计） |

---

## 9. 决策记录（ADR）

### ADR-1：采用模块化单体 + 单数据库

**背景**：MVP 要覆盖会员端、教练端、运营后台、定时任务；最关键的正确性要求是"取消释放名额 + 候补转正"的原子性；工程团队只有 1 人。
**决策**：一个后端服务（NestJS）+ 一个 MySQL 库；用 Module 与 Domain 层划清边界；不为部署形态买单，只为模块边界买单。
**备选方案**：① 微服务（预约/候补/通知拆开）；② Serverless；③ 单体但无模块边界（纯 Controller 直写 SQL）。
**影响与代价**：单点故障（靠云 RDS + 单实例容错与快速重启缓解）；将来若某个模块需要独立扩展，须先拆事务边界——但这个拆分在业务量增长到需要时再做，届时"取消与转正"的原子性需求已经在代码里看得见（`promote()` 单入口），拆起来不会失控。**放弃了**"团队的独立部署能力"，因为在 1 人团队里它不存在。

### ADR-2：Node.js + TypeScript + NestJS + Prisma + MySQL 8.0

**背景**：需要一套让一个人能同时写小程序、后台、服务端，并且类型不漂移的技术栈；核心依赖是事务、行锁、唯一约束。
**决策**：见上。
**备选方案**：Java/Spring Boot（生态稳、事务成熟，但开发与运维更重）；Python/FastAPI（轻快，但 TS 全栈共享类型与校验的优势更大）；Taro/uni-app（多端，无需求）。
**影响与代价**：Node 在 CPU 密集场景弱——本系统没有 CPU 密集逻辑（都是 IO + 一次 SQL）；Prisma 的迁移在生产环境需要规范（禁用 `migrate dev`，只用 `migrate deploy`）；少量关键 SQL 需要原生语句，不能全靠 ORM 抽象。

### ADR-3：MVP 不引入 Redis

**背景**：CEO 任务里点名要回答"缓存"；候补转正与定时任务在直觉上"需要分布式锁"。
**决策**：不引入 Redis。并发控制用 MySQL（行锁 + 条件更新 + `GET_LOCK`）；读性能用索引 + 短事务；缓存需求等到出现可测量的问题再加。
**备选方案**：① Redis 做课表缓存 + 分布式锁；② Redis 做名额原子扣减（Lua）。
**影响与代价**：**放弃了**"用缓存扛住更高 QPS"的能力（当前估算峰值 1~10 QPS，用不上）；`GET_LOCK` 只在同一个 MySQL 实例内有意义（单库部署下完全够）。**触发条件**（任一出现就重新评估）：课表 P95 > 800ms 或 QPS > 200；定时任务需要多副本；需要跨实例限流/幂等；消息吞吐 > 50 条/秒。届时优先给"只读、非名额敏感"的路径加缓存，**不给课表的人数/剩余名额加缓存**（会破坏 AC-2.6 的体验与 AC-1.7 的要求）。

### ADR-4：名额扣减用"条件更新"，时间冲突校验用"会员行锁"

**背景**：AC-2.6 要求并发不超卖；R-05 要求同一会员不能同时持有两节时间重叠的课——后者是"跨行查询 + 写入"的竞态，单靠唯一约束无法表达（区间相交不是相等）。
**决策**：① 名额：`UPDATE ... WHERE booked_count < capacity` 以 `affectedRows` 为准；② 会员级写操作：事务开头 `SELECT members ... FOR UPDATE` 串行化；③ 唯一约束只做最后防线（有效记录唯一）。
**备选方案**：① 全程 `SELECT ... FOR UPDATE` 悲观锁；② 乐观锁版本号 + 重试；③ 只靠应用层校验（会有竞态，不可接受）。
**影响与代价**：会员行锁意味着**同一会员**的并发写会串行（这是期望行为，会员自己的并发操作本来就该串行，且同一会员多设备同时操作概率极低）；不同会员之间无争抢（锁粒度是会员行，不是全局）。放弃"零锁等待"，但换来规则简单、可解释、可测。

### ADR-5：候补转正在业务事务内同步执行；通知异步且不参与判定；禁止"转正待确认"中间态

**背景**：AC-6.1 要求 5 秒内自动转正；AC-6.1b 明确禁止任何"待确认"中间态与倒计时；R-15 要求通知失败不回滚状态；CEO 复审明确否决了"转正后限时确认"。
**决策**：`WAITLISTED → ENROLLED` 只由 `promote()` 在服务端触发，与"名额释放"在**同一事务**内完成；状态机里没有中间态；接口不提供确认端点；通知只写表（同事务）+ 异步发送（事务外），任何发送失败不回滚、不重试业务。
**备选方案**：① 事务内直接调微信接口（会把外部依赖放进事务，锁持有时间不可控）；② 用 MQ 做转正（延迟不可控，且需要分布式事务）；③ 转正后给会员 15 分钟确认（CEO 已否决：等待期会让名额空置，且通知不可靠）。
**影响与代价**：取消接口的响应时间包含了最多"容量个"转正循环（实测路径上是几条 SQL，估算 < 20ms）；代价是取消请求要等转正写完——可接受，因为转正本身必须"即刻生效"。另外，转正可能转给"已经来不了但懒得取消"的会员（D1 已认下的代价，用 O4 观测，缓解手段是转正消息里写明可免费取消）。

### ADR-6：`bookings` 多行历史 + 生成列唯一索引；候选队列用 `id` 排序

**背景**：R-06 要求"同一会员同一节课最多 1 条有效记录"；R-07 要求"退出后再加入排到队尾"；同时要保留历史用于审计与埋点。
**决策**：每次"加入"插新行；用 MySQL `STORED` 生成列 `active_key`（仅有效态有值）建立唯一索引来强制"有效态唯一"；FIFO 顺序直接用自增 `id`。
**备选方案**：① 一行复用 + 额外维护"课程内队列序号"；② 单独的 `waitlist_queue` 表；③ 唯一索引直接用 `(session_id, member_id)` 不支持多行历史。
**影响与代价**：表行数增长更快（每年约 365 万行，见 7.3），需要归档策略；生成列是 MySQL 特性，**绑定了数据库选型**（这也是放弃 PostgreSQL 的代价被抵消的地方——PostgreSQL 用部分唯一索引会更优雅，但方案 ① 反而需要更多代码）。单独队列表被否决：会有"预约表与队列表双写不一致"的风险，而队列本就是"预约记录的一个状态"。

### ADR-7：定时任务采用"扫描式 + 幂等键 + `GET_LOCK` 单执行者"

**背景**：T-2h/T+15min/课后 24h 都需要"到点做事"；进程可能重启、任务可能重跑。
**决策**：所有任务都是"条件查询 + 幂等键 + 条件更新"，可在任意时刻重复执行；用 `job_runs` 唯一键记录"某业务对象已处理"，用 `GET_LOCK` 保证同时只有一个执行者。
**备选方案**：① 一次性定时任务（到点触发一次）；② 独立调度系统（XXL-JOB/云函数定时器）；③ 事件驱动 + 消息队列。
**影响与代价**：每轮都有少量无效查询（成本可忽略）；放弃"精确到秒的任务调度"（业务上不需要——临界点判定都在请求路径上实时做，任务只做最终一致的收尾）。

### ADR-8：通知用"数据库即队列"轮询，不引入 MQ

**背景**：需要"业务状态与推送解耦 + 可重试 + 可观测"；推送量估算 < 1 条/秒。
**决策**：`notifications` 表带 `push_status/attempts/next_retry_at`，后台 worker 每 10 秒批量取送，失败退避重试，最终失败落站内消息。
**备选方案**：RabbitMQ/Kafka（运维成本高）；进程内内存队列（重启丢消息）；同步发送（违反 R-15）。
**影响与代价**：单表轮询在量级增长时会成为瓶颈（> 50 条/秒时需重新评估，届时换 Redis Streams 或 MQ 也只需替换 `dispatch` 实现，业务代码不变——这是把可逆性留在"发送侧"的设计）。

### ADR-9：时间以服务端时钟为准，DB 存 UTC，判定统一走 `Clock`

**背景**：R-16 要求"以服务器时间为准，不信任客户端"；AC-2.7 要求 T-15min 整点拒绝；多个模块（预约/取消/签到/任务）都要判定时间窗。
**决策**：DB 存 UTC（`DATETIME(3)`）；业务时区固定 `Asia/Shanghai`；所有判定走注入式 `Clock`；响应回传 `serverTime` 供前端展示倒计时；启动时校验应用与 DB 的时钟偏差并在超阈值时告警。
**备选方案**：① 直接用 DB `NOW()` 判定（分布在各条 SQL 里，难以测试与统一）；② 存本地时间不带时区（夏令时/迁移会出坑，虽然中国大陆不用夏令时，但"不带时区的时间"是常见事故源）。
**影响与代价**：需要保证服务器 NTP 开启（已在 6.3 写成前置要求）；前端不能用本地时间做业务判断，倒计时要基于 `serverTime` 计算偏移。

### ADR-10：运营修改课程时间受限（只允许"距开始 > 24h 或无人预约"）

**背景**：改时间会破坏已预约会员的时间冲突状态（PRD AC-6.7 提到"课程时间被运营调整过"说明这个情况可能发生），而 `bookings` 冗余了 `session_start_at/session_end_at`（用于排序与冲突校验）。
**决策**：允许改 `capacity / waitlist_limit / room / coachId`；改 `start_at/end_at` 需满足 `now < start_at - 24h` **或** `booked_count = 0 且 waitlist_count = 0`，否则拒绝并提示"请先取消该课再重新排课（会通知已预约会员）"。允许时同事务更新 `course_sessions` 与所有相关 `bookings` 的冗余时间字段。
**备选方案**：① 完全禁止改时间（运营临场调整会被逼着"取消+重排"，反而伤害会员）；② 允许任意改（会让已有预约的冗余时间与真实时间不一致，冲突校验出错）。
**影响与代价**：给运营留了逃生口，但把"高风险改动"限制在低影响窗口内。**这是需要 PM/运营确认的一条**（见 11.4）。

---

## 10. 里程碑与工期估算

### 10.1 估算前提

- **1 名工程师全职**；不含设计（设计师并行）、不含测试手工回归（测试并行）。
- 只算"能上线的最小可用"，不含 P2 与美化。
- **本阶段（只写文档）公司不能运行代码**，因此下面的"验证方式"写的是"要跑什么"，里程验收必须等到有可运行环境。
- 微信小程序**备案 + 类目审核 + 域名备案**需 1~2 周，与开发并行，**必须在 M0 就启动**，否则阻塞上线（这是唯一可能拖后整个计划的外部依赖）。

### 10.2 里程碑

| 里程碑 | 内容 | 人日 | 如何验证 |
|---|---|---|---|
| **M0 骨架与横向能力** | 仓库结构、Prisma 迁移、`Clock`/错误码/日志/事务助手/埋点、NestJS 模块骨架、小程序与后台脚手架、环境变量与 CI、启动时时钟偏差校验 | 3（后端 2 + 端 1） | 迁移能在空库跑通并可回滚；`GET /healthz` 返回版本与 DB 连通；单测框架可跑通一条 `TimeWindow` 用例 |
| **M1 身份与课表（P0-1）** | 微信登录、手机号绑定与会员匹配、JWT、`GET /sessions`/`/sessions/{id}`、门店与课程/排期数据模型、小程序课表页与详情页（含 7 种状态标签，含 `T-2h ≤ now < T-15min` 的 `已满，候补已结束`） | 5（后端 3 + 端 2） | 单测：状态派生 7 个分支（含"满员 + 候补已关闭"边界）+ 7 天窗口边界（23:00 归当天）；AC-1.1~1.7 逐条跑；用真实门店数据核对排期 |
| **M2 预约与取消（P0-2/P0-3）** | `POST /sessions/{id}/bookings`、`DELETE /bookings/{id}`、条件更新扣减、会员行锁、时间冲突、幂等分支、我的预约页 | 6（后端 4 + 端 2） | **并发测试（必做）**：容量 1 + 20 并发 → 恰好 1 成功、`booked_count = 1`；AC-2.1~2.10、AC-3.1~3.4、3.6 逐条；重复提交 → 幂等；对账任务在人为改错计数后能修正 |
| **M3 教练签到（P0-4）+ 最小运营后台** | 教练端登录与课表、`sign-in`/`exception`、后台排课/会员导入/课程取消/签到补录/异常审核/导出、签到页与后台页面 | 9（后端 5 + 端 2 + 后台 2） | AC-4.1~4.11 逐条；用可控时钟测窗口 4 个临界点；用假坐标测 300m 边界与精度不足；改系统时间验证 `signed_at` 取服务端时间；后台导入 50 条名单并核对 `phone_hash` 匹配 |
| **M4 候补与转正（P1-1/P1-2 状态部分）** | `waitlist` 入队/退出、`pending_promote_seats`、`promote()`、`close_waitlists`/`promote_missing` 任务、候补列表与序号展示 | 7（后端 5 + 端 2） | AC-5.1~5.7、AC-6.1/6.1b/6.1c/6.4/6.5/6.6/6.7/6.8/6.9/6.10 逐条；专项：①队列 FIFO（含退出后再入队排尾）；②一次释放 3 个名额 → 前 3 人按顺序转正；③队首时间冲突 → 跳过且不卡队列；④队首被冻结 → 跳过并继续（PM 口径 A）；⑤扩容容量**不**触发转正（R-04 反例）；⑥任务重复执行不产生重复转正 |
| **M5 通知与消息中心（P1-2 通知部分）** | `notifications` 表与 `enqueue`、`dispatch_notifications` 任务、退避重试、消息中心页与未读角标、订阅授权接入 | 3（后端 2 + 端 1） | AC-6.2/6.3/6.11；专项：**全部拒收（模拟 43101）时全流程仍 100% 通过**；重复 enqueue 只产生一条（`dedupe_key`）；推送失败不影响任何业务字段 |
| **M6 未到场与爽约（P1-3）** | 教练名单提交、`auto_submit_roster`、`no_show_records`（含 `LATE_CANCEL`）、R-11 冻结与自动退出候补、撤销重算、爽约记录页、CSV 导出 | 4（后端 3 + 端 1） | AC-7.1~7.7 逐条；专项：①30 天窗口边界（第 3 次要冻结）；②课程未确认发生 → 不计爽约（AC-7.4）；③撤销后次数重算并按需解冻；④冻结瞬间自动退出其候补（PM 增补）；⑤24h 未提交 → 默认全部到场 |
| **M7 内测与压测** | 1 家门店内测、并发脚本、对账与告警、runbook（回滚/补跑/修数）、灰度埋点报表 | 5 | 6.1 的 5 项验证；PRD 内测阶段目标：P0 链路全通 + 无超卖；O1~O6 指标可读出数 |
| **M8 灰度与观测** | 3 家门店灰度、候补开放、指标校准、按 PRD 触发条件调参（定位半径、模板） | 3（含值守与修数） | PRD 灰度 2 周目标；本文件 7.5 的告警全部接入 |
| **合计** | 后端 + 小程序端 + 后台 | **约 45 人日 ≈ 9 周（1 人）** | 不含微信备案等待（并行）、不含测试手工回归 |

> **工期风险**：45 人日是"一个人做完三端 + 服务端"的估算，没有缓冲余量。如果只有 1 名工程师，建议 CEO 在范围上做取舍（见下），而不是压缩验证时间——M2/M4 的并发与转正测试是**不可跳过**的（它们对应 PRD 定义的回滚条件）。

### 10.3 可裁剪项（给 CEO 的范围建议，按"先砍哪个"排序）

| 裁剪 | 省 | 代价 |
|---|---|---|
| 运营后台先做"CSV 导入 + 排课 + 名单导出"三个页面，签到审核/撤销爽约先用内部任务接口 + SQL 兜底 | 约 3 人日 | 门店异常处理要工程介入，只在灰度期可接受 |
| 消息中心从"完整列表页"降级为"首页一条提示 + 列表页复用我的预约" | 约 1 人日 | AC-6.3 仍满足（有记录可看），体验略降 |
| 教练端名单页先做"默认全到场 + 勾选未来人"（已经是 PRD 要求的最小形态）——**不建议再砍** | 0 | 砍掉就没有爽约数据（O6 失效） |
| **不建议砍**：M2 的并发测试、M4 的转正专项、通知拒收专项 | — | 这三项对应 PRD 的回滚触发条件与核心目标 O3 |

---

## 11. 假设与待确认问题

### 11.1 我按最合理理解继续的假设（如与 CEO/运营不一致请指出，我改文档）

1. **会员名单来源 = 运营在后台用 CSV 导入**（手机号、姓名、有效期）。PRD 第 9 节待解决问题 1 说这会阻塞 P0-2；我按"没有现成会员系统可对接"设计，因此 `members` 表自带导入能力，并保留 `card_status` 字段以便将来对接。
2. **会员卡有效期本期不校验**（PRD 待解决 2 的默认假设一致），`card_status` 只记录。
3. **爽约次数包含两种来源**：`ABSENT`（AC-7.3）与 `LATE_CANCEL`（AC-3.2），共用 R-11 的 30 天滚动窗口。这是我对 PRD 的解读——AC-3.2 说临期取消"计 1 次爽约"，而 R-11 的冻结阈值说"30 天内累计次数 n"，两者必须共用同一个计数器才有意义。**这条影响冻结触发时机，请 PM 确认。**
4. **课程"实际发生"的判定**：存在已确认签到（含运营补录/审核通过）。`OPS_PENDING` 与 `COACH_NOT_SIGNED_IN` 的课一律不计爽约（PRD AC-7.4/4.11）。运营补录后**不会自动**补计爽约，需要运营在后台显式提交名单（默认不计，与 AC-7.4"默认不计"一致）。
5. **冻结生成时自动退出该会员名下所有 `WAITLISTED` 记录**（PM 已在对话中确认，属范围增补，会写进下一版 PRD）。
6. **教练可跨门店执教，但同一时段不可重复排课**（PRD 待解决 5 的默认假设）；门店营业时间不做排课硬校验，`business_hours` 仅展示。
7. **候补队列上限默认 = 课程容量**（PRD 待解决 6），由运营按课配置，`0` 表示不开放候补。
8. **埋点写入 `events` 表**（而非仅日志），以便后台直接出灰度指标；写失败不影响业务。
9. **文档文件名**以 CEO 指定的 `docs/tech-design.md` 为准（PRD 里写的 `docs/技术方案.md` 视为同一份）。
10. **"已满但候补未开放 / 队列已满"的卡片标签**：AC-1.2 的 7 种标签只覆盖"已满 + 候补开放中（`now < T-2h`）"与"已满 + 候补已关闭（`T-2h ≤ now < T-15min`）"两种时间态，未定义 `waitlist_limit = 0`（本站不开放候补）或队列已达上限（AC-5.4）时卡片显示什么。我按 **`已满`（不可候补，按钮禁用 + "候补人数已满"/"本站未开放候补"）** 渲染，并在 4.4 标注为待 PM/设计师确认项。

### 11.2 需要 CEO / 运营决定的事

| # | 问题 | 影响 | 我的建议 |
|---|---|---|---|
| 1 | 会员名单从哪来、谁导入（PRD 待解决 1） | **阻塞 P0-2 上线** | 后台 CSV 导入 + 运营准备首批名单（已按此设计） |
| 2 | 是否加短信作为第二通知通道（PRD 待解决 3） | 涉及成本；影响 O4 | 本期不加（通知不参与业务判定，链路已闭环）；按 O4/O7 决定 |
| 3 | 运营后台是否接受"先三个页面"的裁剪（10.3） | 影响工期 3 人日 | 建议接受，异常处理暂时走内部接口 |
| 4 | 工期与范围取舍：45 人日无缓冲 | 影响上线时间 | 建议按 10.3 裁剪，或把 P1-3（爽约处置）推到灰度后 |

### 11.3 需要 PM 确认的两处细节

| # | 问题 | 我的默认实现 |
|---|---|---|
| 1 | `LATE_CANCEL` 是否计入 R-11 的 30 天爽约次数（11.1 第 3 条） | 计入 |
| 2 | 运营补录签到后，是否自动对"已被标记未到场"的会员补计爽约 | 不自动，需运营显式提交（默认不计） |

### 11.4 需要 PM/运营确认的一条设计限制

**ADR-10**：课程时间只允许在"距开始 > 24h"或"无人预约"时修改；否则要求先取消该课再重新排课。如果运营的实际工作方式需要更灵活（例如临场把 19:00 的课改到 20:00），请告诉我，我会改成"允许改 + 通知已预约会员 + 重新校验冲突 + 登记异常待办"的更复杂方案（约 +1.5 人日）。

### 11.5 我无法完成、需要真实环境/权限的事（诚实声明）

1. **订阅消息模板查证**（PRD 待解决 4）：我没有微信公众平台后台权限，无法确认可用模板、字段上限、类目限制。**设计上已做兜底**（模板 ID 配置化 + 站内消息），但上线前必须有人用真实账号查证。
2. **所有方案的实机验证**：本阶段公司不能运行代码，本文档的并发、时序、幂等结论都是**设计与推理结果，未经运行验证**；验证方法已逐条写在第 6、10 节。
3. **容量与性能目标**：无真实流量，7.3 是估算（依据已列出），需灰度后校准。
4. **微信小程序备案与类目审核**：需公司主体资料，建议 M0 启动。

---

## 12. 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 本文档 v1.0 | 初版：架构、选型、数据模型、接口、难点方案、ADR、里程碑 | CEO 任务 |
| 本文档 v1.0 | 候补转正写成"系统自动、即刻生效、零确认"；状态机显式禁止"待确认中间态"；接口不提供确认端点 | 依据 PRD **v1.1**（CEO 复审否决"转正后限时确认"）：AC-6.1/6.1b/6.1c、R-08、决策 D1 |
| 本文档 v1.0 | 新增"冻结会员被轮到转正 → 跳过并继续"的 6 条口径与"冻结时自动退出其候补" | PM 在评审对话中确认方案 A 并作为范围增补 |
| 本文档 v1.0（同步 PRD v1.1 复审修订） | ① 课表标签由 6 种改为 **7 种**：4.4 派生表新增 `已满，候补已结束`（满员且 `T-2h ≤ now < T-15min`，置灰 + 按钮禁用），补"派生顺序"与"已满但候补未开放/队列已满"这一 PRD/UX 未覆盖组合的工程默认（待确认，见 11.1 第 10 条）；② 5.3.1 新增 `statusLabel` 派生枚举（7 值 + 1 工程补充值），前端只按它渲染；③ 4.4 补术语映射：PRD `待运营确认`（界面「待门店确认」）= `OPS_PENDING`，AC-4.9"驳回"的目标态由 `COACH_NOT_SIGNED_IN` 改为回到 `OPS_PENDING`，`COACH_NOT_SIGNED_IN` 只保留给 AC-4.11 的 T+15min 未签到；④ 6.3 `coach_no_sign_in_todo` 不再扫 `OPS_PENDING`，避免把待审核的课改写成"未签到"；⑤ 同步 5.2 `40001`、5.3.4、10.2 M1 的标签引用 | PM《PRD》v1.1 复审修订（AC-1.2/1.3/4.9/5.3、第 6 节通用约定、第 10 节）；对应测试计划 Q-1、S-2 |
