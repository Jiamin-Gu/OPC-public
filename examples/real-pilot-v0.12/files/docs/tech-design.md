# 技术方案：健身房团课预约微信小程序（MVP）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.1（对应 PRD v0.5；第 3 轮审查后修订：名单口径、核对结果重投影、入队串行化与序号唯一、`active_flag` 与对账口径） |
| 依据 | `docs/PRD.md` v0.5（含 v0.4「候补自动转正」、v0.5「主动退出候补 + 宽限期取消率口径」两项变更） |
| 阶段 | MVP，本阶段只写文档、不写代码 |
| 覆盖面 | PRD 的 P0 清单 F1–F10；P1/P2 只做「不阻挡」的设计预留，不实现 |
| 作者 | 工程师 |

依据说明：本文档只覆盖 PRD 中标注为 MUST 的规则（第 3、6、7 章）与 P0 功能。PRD 第 9 章的 10 个待解决问题多为业务/门店口径，其中影响技术实现的项，我先做假设并集中写在第 9 章，未确认前按「假设 + 可逆设计」处理。

# 1. 需求映射

## 1.1 P0 功能 → 实现模块

| PRD 功能 | 小程序端模块 | 后端模块（应用服务） | 依赖的外部能力 |
| --- | --- | --- | --- |
| F1 微信登录 + 手机号授权 + 会员卡绑定校验 | member-app / auth 页 | auth-service（登录态、角色判定）、integration-gateway（会员卡校验） | `wx.login` code2session、`getPhoneNumber` code 换手机号、门店会员卡接口 |
| F2 查看课表（未来 7 天 + 剩余名额 + 我的状态） | member-app / 课表页、课程详情页 | schedule-service（课表查询、剩余名额、我的状态投影） | 课次数据源（门店后台导入，见第 9 章假设 A1） |
| F3 课程详情 + 预约（含并发防超卖） | member-app / 课程详情页 | reservation-service（预约事务）、domain-core（容量规则、冲突判定） | — |
| F4 我的预约 + 取消预约 | member-app / 我的预约页 | reservation-service（取消事务）、waitlist-service（名额分配） | — |
| F5 加入候补队列（FIFO）+ 会员主动退出候补 | member-app / 课程详情页、我的预约页 | waitlist-service（入队、退出、位次计算） | — |
| F6 候补自动转正（无需确认）+ 转正通知 + 宽限期取消 | member-app / 状态卡片、取消入口 | waitlist-service（冲突预检 + 自动转正）、notify-service（先落库后通知）、reservation-service（宽限窗口） | 微信订阅消息（增强路径，尽力而为） |
| F7 教练「我的课程」+ 学员名单 | coach-app / 我的课程页、课程详情页 | attendance-service（课程列表、名单投影）、auth-service（教练身份） | — |
| F8 教练课程签到（含漏签到提醒与截止） | coach-app / 签到按钮 | attendance-service（签到事务）、job-runner（提醒与异常判定）、notify-service | 微信订阅消息（教练提醒） |
| F9 学员到场核对 + no-show 判定 + 只读态「未核实」 | coach-app / 核对页（默认全到场、10 秒可撤销） | attendance-service（提交/撤销/修改、结算）、job-runner（60 分钟与 24 小时两个截止） | — |
| F10 店长异常提醒 + 今日概览 + 补签 + 兜底提交/修改 | manager-app / 概览页、异常页、课程详情页 | attendance-service（补签、店长覆盖）、job-runner（异常与提醒）、notify-service | 订阅消息（店长提醒）、门店内人工跟进（兜底，见第 9 章假设 A4） |

## 1.2 PRD 规则 → 技术约束（防止实现分叉）

| PRD 条目 | 规则（MUST） | 落到哪个实现点 | 怎么保证只有一套实现 |
| --- | --- | --- | --- |
| 7.1 | 排队排序键 = 服务端记录的加入时间，同秒按服务端序列号 | `waitlist_entry.seq` 由服务端在**课次行锁内**分配（`max(seq)+1`，不自增主键以免跨课次共享），并由唯一索引 `uk_session_seq(session_id, seq)` 兜底 | 位次一律由服务端计算返回，会员端只展示；`seq` 只增不回退，退出后重新加入取新的更大值 |
| 7.1 | 一人一位；队列上限 = min(容量, 20) | 唯一索引 `uk_active(session_id, member_id, active_flag)`（一人一位）+ 唯一索引 `uk_session_seq(session_id, seq)`（序号不重复）+ 入队在**同一把课次行锁内**做计数校验 | 队列长度校验必须与 `seq` 分配在同一把锁内完成，否则并发插入可突破上限；唯一索引是最终防线，计数校验只是提前给出友好错误 |
| 7.1 | 主动退出不释放名额、可重新加入（排到队尾） | 退出只改候补条目状态与 `active_flag`，**不调用**名额分配函数 | 分配函数只允许被「取消预约」这一类事件调用（代码层单入口） |
| 7.2 | 释放来源 MVP 只有「会员取消」（含转正后的宽限取消） | `releaseAndAllocate()` 唯一入口，调用方只有 reservation-service 的取消路径 | 退出候补、T-30 关队列**不得**调用该入口（写入接口契约） |
| 7.2 / 7.4 | 分配顺序：≥30 分钟且有「等待中」无冲突者 → 自动转正；否则回公开可约；已开始 → 作废 | `releaseAndAllocate()` 内部单函数实现，事务内一次跑完 | 三种结局在函数返回枚举里穷尽，无第四种 |
| 7.3 / 7.4 | 先落库、后通知；触达失败不回滚、不改变时限 | 业务事务内只写 `notify_outbox`；发送由异步 worker 做 | 业务链路不出现任何「等通知结果」的分支 |
| 7.4 | **MVP 不设转正确认时限**，无「待确认」状态 | 数据库无 `pending_confirm` 类状态；无确认超时任务；会员端无确认/放弃按钮 | 状态集合在领域层用白名单枚举固化，未知值直接拒绝 |
| 7.4 / 7.5 | 转正预约的取消截止 = min(max(开课前 120 分钟, 转正时间 + 15 分钟), T-30) | `calcCancelDeadline(reservation)` 单函数，普通预约与转正预约走同一函数的不同分支 | F4 取消校验与会员端展示的截止时间都取该函数结果 |
| 7.5 | 冲突判定 = 半开区间 `新起 < 已约止 且 已约起 < 新止`，首尾相接不算冲突 | `hasOverlap()` 单函数，F3 预约校验与 F6 转正预检共用 | 该函数在领域层，且是被调用方而非复制品（PRD 明令不得各写一套） |
| 7.6 | 全部参数为固定值、不可配置 | 常量集中在领域层 `policy.ts` 一类常量模块，不从数据库/配置读 | MVP 不做门店级配置，避免出现两套生效值 |

## 1.3 已废除的规则（不得实现）

PRD v0.4/v0.5 已废除「会员在 15 分钟内确认」「待确认状态」「超时未确认则顺延」。因此本方案**不实现**：

- 不建任何「待确认」状态、不设确认时限、不写确认超时任务；
- 名额释放即自动转正（`booked`），随后通知；
- 原任务描述中「候补转正与超时释放」的「超时释放」部分按新 PRD 不适用，替代机制是：**冲突预检 + T-30 截止 + 转正后宽限取消窗口**（见 5.2、5.3、5.8）。

# 2. 架构与技术选型

## 2.1 架构概览

```text
[微信小程序（同一 AppID，按角色分流）]
  ├─ 会员端：课表 / 课程详情 / 预约 / 我的预约 / 候补 / 已自动转正卡片
  ├─ 教练端：我的课程 / 学员名单 / 签到 / 到场核对
  └─ 店长端：今日概览 / 异常提醒 / 补签 / 兜底提交或修改核对
                │  HTTPS + JSON（Bearer token）
                ▼
        [Nginx（TLS、限流、静态资源）]
                ▼
[后端单体服务：Node.js 20 + TypeScript + Fastify]
  ├─ 接口层：路由、鉴权、入参校验、错误码映射
  ├─ 应用服务：auth / schedule / reservation / waitlist / attendance / notify
  ├─ 领域核心 domain-core：状态机、名额分配、冲突判定、取消截止、策略常量（无 IO）
  ├─ 基础设施：MySQL 仓储、微信 API 客户端、通知 outbox worker
  └─ 定时任务 job-runner：T-30 关队列、签到提醒与异常、核对 60 分钟与 24 小时、结算
                ▼
[MySQL 8.0（单实例）：业务表 + 幂等表 + 通知 outbox + 任务幂等表]
                ▼
[外部依赖：微信接口（code2session / getPhoneNumber / 订阅消息）、门店会员卡与课次数据]
```

**架构模式：模块化单体（Modular Monolith）**，单体进程内按模块分目录、模块之间只通过应用服务接口调用，领域核心 domain-core 不含 IO。理由与代价见 ADR-1。

## 2.2 模块划分与依赖方向

依赖方向只有一个：`接口层 → 应用服务 → 领域核心`；基础设施层由应用服务通过接口依赖（依赖注入），**领域核心不依赖数据库、HTTP、微信 SDK**。这样 PRD 7.x 的规则可以脱离环境做纯函数测试。

| 模块 | 职责 | 不负责 |
| --- | --- | --- |
| auth-service | 登录、角色判定（会员/教练/店长）、会员卡状态校验与快照 | 不判约课规则 |
| schedule-service | 课表查询、剩余名额与「我的状态」投影、详情页可操作项 | 不写任何业务状态 |
| reservation-service | 预约、取消、宽限窗口校验、触发名额分配 | 不算分配顺序（交给 domain-core） |
| waitlist-service | 入队、退出、位次计算、冲突预检与自动转正 | 不释放名额（没有这类事件） |
| attendance-service | 签到、核对提交/撤销/修改、结算、店长补签与覆盖 | 不判候补规则 |
| notify-service | outbox 写入、订阅消息下发、额度记录、重试与告警 | 不影响任何业务状态 |
| job-runner | 到点触发的状态收尾与提醒（全部幂等） | 不承担业务正确性（见 5.7） |
| domain-core | 状态机、不变量、`hasOverlap()`、`calcCancelDeadline()`、`releaseAndAllocate()`、策略常量 | 不做 IO、不读配置 |

## 2.3 技术选型

| 选项 | 选择 | 理由 | 放弃的备选 |
| --- | --- | --- | --- |
| 小程序端形态 | **微信原生小程序**（WXML/WXSS/JS） | 只服务微信一个端，不需要跨端；原生调试链最短，订阅消息、手机号授权等能力开箱可用 | Taro / uni-app（编译层带来的调试与升级成本，在单端场景没有收益）、独立 App（PRD 明确不做） |
| 后端语言与框架 | **Node.js 20 + TypeScript + Fastify** | 与小程序端同语言、类型可共享接口定义；Fastify 依赖少、启动快、性能足够；TypeScript 让错误码与 DTO 在编译期对齐 | NestJS（装饰器 + DI 抽象对单人团队偏重）、Spring Boot（部署体量与运维成本高）、Python/FastAPI（类型共享不如同语言顺） |
| 数据库 | **MySQL 8.0（InnoDB）** | 事务 + 行级锁 + 唯一索引正好覆盖「满员并发抢位」与「释放即分配」的需求；门店现有后台多为 MySQL，便于对接会员卡与课次数据 | PostgreSQL（团队运维熟悉度与门店对接成本）、SQLite（并发写能力不足） |
| 并发控制 | **单行悲观写锁 + 条件更新 + 唯一索引** | 抢位是同一课次上极短的热点写，锁的粒度小、持有时长短，一条 SQL 事务即可；不需要额外中间件 | Redis 分布式锁（多引入一个组件与一致性问题）、纯乐观锁重试（失败重试放大流量与用户困惑） |
| 缓存 / 消息队列 | **MVP 都不引入** | 数据量与并发量都很小（见 2.5 估算），且「先落库后通知」用数据库表做 outbox 已经足够 | Redis（缓存课表）、RabbitMQ/Kafka（异步通知）——留到有真实压力再加，属可逆决策 |
| 异步与定时 | **数据库表做任务队列 + 进程内定时轮询** | 与业务表同事务写入，不存在「消息发出但业务回滚」的问题；单实例部署，无需分布式调度 | 延迟队列中间件、外部调度平台 |
| 部署 | **单台云服务器 + Docker + Nginx**（可选微信云托管） | 单店 MVP，一台机器足够；容器化让回滚 = 切回上一个镜像，满足 PRD 第 10 章「30 分钟内部署只读模式」 | K8s（运维成本远超收益） |
| 可观测 | 结构化日志（requestId + memberId）+ 关键指标计数 + 对账告警 | 出问题时按 requestId 串起「请求 → 事务 → 通知」，对账任务直接监测超卖 | 全链路追踪（MVP 阶段收益低） |

## 2.4 微信能力接入

| 能力 | 用途 | 关键约束（按 PRD 7.3 的口径） | 失败处理 |
| --- | --- | --- | --- |
| `wx.login` + `auth.code2session` | 换取 openid 建立登录态 | code 只能用一次、5 分钟内有效 | 返回 40102，客户端重新取 code 重试一次 |
| `getPhoneNumber` 换手机号 | 绑定会员卡、判定角色 | 必须由用户点击手势触发；拒绝授权则无法绑定 | 返回 40401/40916，展示门店电话与「联系前台核验」引导 |
| 订阅消息（一次性订阅） | 转正通知、教练签到提醒、店长异常提醒 | 按当前小程序后台的模板池，健身房类目**只有一次性订阅**可用：授权必须由用户点击手势触发（`requestSubscribeMessage`，不能静默/自动调用）、额度按「用户 × 模板」独立计数、一次调用最多 3 个模板、用户可拒绝，且**微信禁止把订阅授权作为使用功能的前提**；因此加入候补只做引导、不做前置，会员拒绝仍可入队。**长期订阅是白名单能力**（政务/医疗/教育等少数类目），若后台出现长期订阅模板，则作为增强路径接入，不改动任何业务规则 | 通知失败不回滚业务；权威路径是小程序内状态卡片与列表内的异常标记 |
| `access_token` | 调用订阅消息接口 | 有效期 2 小时、单实例内需串行刷新 | 进程内缓存 + 提前 5 分钟刷新；获取失败返回 50302 并重试 |

**权威路径 vs 增强路径（MUST）**：转正结果、候补位次、可取消截止时间一律由服务端实时查库返回（`GET /api/v1/me/items`），打开小程序即可见；订阅消息只是「尽力而为」的增强，触达失败不回滚转正、不改变任何时限（PRD 7.3 红线）。教练与店长同理：教练端「我的课程」列表上直接标注「待签到 / 签到异常 / 核对未提交」，店长端有独立的异常列表，页面打开即可见，不依赖推送。

## 2.5 部署与容量估算（假设，非实测）

假设前提（未经验证，上线后必须用真实数据校准）：1 家门店、会员日活 1000、人均每天 20 次读操作 + 2 次写操作、每天 20 节课（7 天约 140 条课次）。

| 项 | 估算 | 依据 |
| --- | --- | --- |
| 读 QPS | 平均 ≈ 0.3，峰值 ≈ 5 | 1000 × 20 ÷ 8 小时工作时段，峰值按 5 倍 |
| 抢位写并发 | 单课次瞬时 ≤ 30 并发（热门课开课前一小时） | 假设单课次容量 ≤ 30、候补 ≤ 20 人同时点击 |
| 写事务耗时 | 单行锁 + 少量读写，目标 P95 < 100ms | 事务范围限制在一个课次行与其关联的少量记录 |
| 数据量 | 一年约 5 万条预约、2 万条通知任务 | 20 节课 × 30 人 × 300 天 |

结论：单实例应用 + 单实例 MySQL 完全够用，**不需要**读写分离、分库分表、缓存层或消息队列。潜在瓶颈有两个：课表查询（用 `idx_store_start` 覆盖，必要时加 30 秒进程内缓存）与微信接口限流（必须缓存 access_token、失败重试），二者都不会影响预约写入的正确性。

# 3. 数据模型

## 3.1 实体与关系

```text
store 1─n course_template 1─n course_session ─1 staff(coach)
                                   │  │  │
                     reservation ──┘  │  └── coach_signin(1:1，每课一条)
                                   │  │
                       waitlist_entry│  └── attendance_item(每会员一条) ─ attendance_submission ─ attendance_log
                                   │
                             member ──┴─ notify_outbox / subscribe_quota / idempotency_record
```

规则要点：**报名（reservation，占名额）与候补（waitlist_entry，不占名额）是两个独立集合**，同一会员对同一课次不得同时出现在两处（PRD 7.1）。所有时间字段统一用 `datetime(3)` 存 UTC，接口以带时区偏移的 ISO-8601 返回（例如 `2025-06-01T19:00:00+08:00`），展示与计算时区固定 `Asia/Shanghai`。

## 3.2 表结构（可直接据此建表）

### member（会员账号）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| member_id | bigint unsigned | PK，自增 | |
| openid | varchar(64) | NOT NULL | 微信身份，登录主键 |
| unionid | varchar(64) | NULL | 预留 |
| phone | varchar(20) | NULL | 手机号授权后写入 |
| name | varchar(50) | NULL | 来自门店系统 |
| avatar_url | varchar(255) | NULL | |
| store_id | varchar(32) | NULL | 单门店 MVP，字段保留 |
| card_no | varchar(32) | NULL | 会员卡号 |
| card_status | varchar(16) | NOT NULL，默认 `unknown` | `unknown`/`active`/`expired`/`suspended` |
| card_expire_at | date | NULL | |
| card_synced_at | datetime(3) | NULL | 最近一次向门店系统校验的时间 |
| role | varchar(16) | NOT NULL，默认 `member` | `member`/`coach`/`manager` |
| status | varchar(16) | NOT NULL，默认 `active` | `active`/`disabled` |
| created_at / updated_at | datetime(3) | NOT NULL | |

索引与约束：唯一索引 `uk_openid(openid)`、`uk_phone(phone)`；普通索引 `idx_store_card(store_id, card_status)`。

### staff（教练/店长白名单）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| staff_id | bigint unsigned | PK | |
| store_id | varchar(32) | NOT NULL，索引 | |
| phone | varchar(20) | NOT NULL | 与会员手机号匹配来判定角色 |
| name | varchar(50) | NOT NULL | |
| role | varchar(16) | NOT NULL | `coach`/`manager` |
| status | varchar(16) | NOT NULL，默认 `active` | |
| created_at / updated_at | datetime(3) | NOT NULL | |

索引与约束：唯一索引 `uk_phone_role(phone, role)`；普通索引 `idx_store_role(store_id, role)`。

### store

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| store_id | varchar(32) | PK | |
| name | varchar(64) | NOT NULL | |
| phone | varchar(20) | NULL | 会员端兜底文案里展示的门店电话 |
| timezone | varchar(32) | NOT NULL，默认 `Asia/Shanghai` | |
| status | varchar(16) | NOT NULL，默认 `active` | |

### course_template（课程）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| template_id | bigint unsigned | PK | |
| store_id | varchar(32) | NOT NULL | |
| name | varchar(64) | NOT NULL | 课程名 |
| duration_min | smallint | NOT NULL | 时长 |
| default_capacity | smallint | NOT NULL | 默认容量 |
| status | varchar(16) | NOT NULL，默认 `active` | |

索引与约束：普通索引 `idx_store(store_id)`。

### course_session（课次，核心表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| session_id | bigint unsigned | PK | |
| template_id | bigint unsigned | NOT NULL | |
| store_id | varchar(32) | NOT NULL | |
| coach_staff_id | bigint unsigned | NOT NULL | 该课次分配的教练 |
| title | varchar(64) | NOT NULL | 课程名快照（无关表读取） |
| start_at / end_at | datetime(3) | NOT NULL | 半开区间 `[start_at, end_at)` |
| capacity | smallint | NOT NULL | 容量 |
| booked_count | smallint | NOT NULL，默认 0 | **当前占用数**（唯一权威计数） |
| status | varchar(16) | NOT NULL，默认 `scheduled` | `scheduled`/`started`/`finished`/`cancelled`（时间派生 + 任务落库） |
| signin_status | varchar(16) | NOT NULL，默认 `pending` | `pending`/`signed`/`makeup`/`abnormal` |
| attendance_state | varchar(16) | NOT NULL，默认 `unsubmitted` | `unsubmitted`/`submitted` |
| coach_edit_deadline_at | datetime(3) | NULL | 教练可提交/修改核对的截止时间 |
| created_at / updated_at | datetime(3) | NOT NULL | |

索引与约束：普通索引 `idx_store_start(store_id, start_at)`（课表查询）、`idx_coach_start(coach_staff_id, start_at)`（教练列表）、`idx_status_start(status, start_at)`（定时任务扫描）。应用层强校验 `0 ≤ booked_count ≤ capacity`，并由对账任务（3.4）持续断言。`coach_edit_deadline_at = min(end_at + 60 分钟, end_at + 24 小时)`；店长补签后重开为 `min(补签时间 + 60 分钟, end_at + 24 小时)`。

### reservation（预约，含候补转正产生的预约）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| reservation_id | bigint unsigned | PK | |
| session_id | bigint unsigned | NOT NULL | |
| member_id | bigint unsigned | NOT NULL | |
| source | varchar(16) | NOT NULL，默认 `normal` | `normal` 自助预约 / `auto_promote` 候补自动转正 |
| status | varchar(16) | NOT NULL，默认 `booked` | `booked`/`cancelled`/`completed`/`absent`/`unverified` |
| active_flag | tinyint | NULL | **未取消 = 1（含 `booked`/`completed`/`absent`/`unverified`），仅 `cancelled` = NULL**（MySQL 唯一索引不约束 NULL）。这是「已约 x/y」、对账（I2）与名单（4.3.2/4.4.5）的统一口径：结算/投影只改 `status`，不改 `active_flag`，也不改 `booked_count` |
| cancel_deadline_at | datetime(3) | NOT NULL | 该预约的取消截止时间（见 5.8 计算规则） |
| promoted_at | datetime(3) | NULL | 自动转正时间（`source = auto_promote` 时非空） |
| created_at | datetime(3) | NOT NULL | |
| cancelled_at / cancelled_by | datetime(3) / varchar(16) | NULL | 取消时间与来源（会员自助） |
| finished_at | datetime(3) | NULL | 结算为 completed/absent/unverified 的时间 |

索引与约束：**唯一索引 `uk_active(session_id, member_id, active_flag)`**（防止同一会员对同一课次重复占位，是幂等的最后一道墙）；普通索引 `idx_member(member_id, status, session_id)`（我的预约）、`idx_session_status(session_id, status)`（统计）、`idx_session_active(session_id, active_flag)`（名单：该课次全部未取消的预约，见 4.3.2）、`idx_cancel_deadline(cancel_deadline_at)`。取消的行置 NULL 不参与唯一约束，因此取消后再预约会新建一条记录。

### waitlist_entry（候补队列条目）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| entry_id | bigint unsigned | PK | |
| session_id | bigint unsigned | NOT NULL | |
| member_id | bigint unsigned | NOT NULL | |
| seq | int | NOT NULL | 课次内单调递增排队号，服务端在课次行锁内分配（`max(seq)+1`），**只增不回退**；退出后重新加入取新的更大值 |
| status | varchar(16) | NOT NULL，默认 `waiting` | `waiting`/`promoted`/`exited`/`closed`（**没有「待确认」**） |
| exit_reason | varchar(24) | NULL | `member_exit`（会员主动退出）/ `conflict`（转正预检发现时间冲突） |
| close_reason | varchar(24) | NULL | `t30` / `course_started` / `course_cancelled` |
| active_flag | tinyint | NULL | 在队 = 1，其余 = NULL |
| created_at | datetime(3) | NOT NULL | 加入时间（排序依据） |
| promoted_at / exited_at / closed_at | datetime(3) | NULL | |

索引与约束：唯一索引 `uk_active(session_id, member_id, active_flag)`（一人一位；退出后可重新入队，历史记录保留）；**唯一索引 `uk_session_seq(session_id, seq)`**（同一课次内排队号不重复，是 PRD「序号连续且不重复」的数据库级保证；退出重入取新的更大 `seq`，历史行保留旧值不会冲突）；普通索引 `idx_queue(session_id, status, seq)`（取队首、算位次、算队列长度）。

### coach_signin（教练签到）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| signin_id | bigint unsigned | PK | |
| session_id | bigint unsigned | NOT NULL | 每课次最多一条 |
| coach_staff_id | bigint unsigned | NOT NULL | 签到教练 |
| signin_at | datetime(3) | NOT NULL | 首次签到时间（重复点击不变） |
| type | varchar(16) | NOT NULL，默认 `normal` | `normal` / `manager_makeup` |
| operator_staff_id | bigint unsigned | NOT NULL | 实际操作人（补签时是店长） |
| reason | varchar(200) | NULL | 补签必填原因 |
| client_ip / device | varchar(45) / varchar(64) | NULL | 追溯字段（防代签取证） |
| created_at | datetime(3) | NOT NULL | |

索引与约束：**唯一索引 `uk_session(session_id)`**（一门课一条签到记录，重复签到天然幂等）。

### attendance_submission（核对提交批次）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| submission_id | bigint unsigned | PK | |
| client_submission_id | varchar(64) | NOT NULL | 客户端生成的幂等键 |
| session_id | bigint unsigned | NOT NULL | |
| submitter_role | varchar(16) | NOT NULL | `coach`/`manager` |
| submitter_id | bigint unsigned | NOT NULL | |
| submitted_at | datetime(3) | NOT NULL | |
| item_count | smallint | NOT NULL | 本次提交人数 |
| version | int | NOT NULL | 课次内单调递增，用于乐观锁 |
| revoke_deadline_at | datetime(3) | NOT NULL | `submitted_at + 10 秒` |
| status | varchar(16) | NOT NULL，默认 `submitted` | `submitted`/`revoked` |
| created_at | datetime(3) | NOT NULL | |

索引与约束：唯一索引 `uk_client_submission(client_submission_id)`（提交幂等）；唯一索引 `uk_session_version(session_id, version)`（版本号不重复）。

### attendance_item（会员到场结果明细）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| item_id | bigint unsigned | PK | |
| session_id / reservation_id / member_id | bigint unsigned | NOT NULL | |
| result | varchar(16) | NOT NULL | `present`/`absent`/`unverified`（`unverified` 只能由系统写，教练不可选） |
| finalized | tinyint | NOT NULL，默认 0 | 1 = 已到「课程结束 + 24 小时」硬上限，不可再改 |
| updated_by_type / updated_by_id | varchar(16) / bigint unsigned | NULL | 最后操作人（教练或店长） |
| version | int | NOT NULL | 随提交版本更新 |
| updated_at | datetime(3) | NOT NULL | |

索引与约束：唯一索引 `uk_session_member(session_id, member_id)`；普通索引 `idx_session_result(session_id, result)`（店长概览的三列计数）。

### attendance_log（核对修改日志）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| log_id | bigint unsigned | PK | |
| session_id / reservation_id / member_id | bigint unsigned | NOT NULL | |
| from_result / to_result | varchar(16) | NOT NULL | 原状态 → 新状态 |
| operator_type / operator_id | varchar(16) / bigint unsigned | NOT NULL | |
| submission_id | bigint unsigned | NULL | 归属的提交批次（撤销时可见） |
| created_at | datetime(3) | NOT NULL | |

索引与约束：普通索引 `idx_session_time(session_id, created_at)`。

### notify_outbox（通知任务，先落库后通知）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| outbox_id | bigint unsigned | PK | |
| biz_event_id | varchar(96) | NOT NULL | 业务事件唯一键，例如 `promote:{reservation_id}`、`t30_close:{session_id}` |
| template_key | varchar(48) | NOT NULL | 模板语义键，例如 `promote_success`、`waitlist_closed`、`coach_signin_remind` |
| channel | varchar(16) | NOT NULL | `wx_subscribe`/`inapp` |
| to_member_id | bigint unsigned | NULL | 教练/店长通知收件人 |
| payload | json | NOT NULL | 模板变量（课程名、时间、截止时间等） |
| status | varchar(16) | NOT NULL，默认 `pending` | `pending`/`sent`/`failed`/`skipped_no_quota` |
| attempts | tinyint | NOT NULL，默认 0 | |
| next_retry_at | datetime(3) | NULL | |
| last_error | varchar(255) | NULL | |
| created_at / sent_at | datetime(3) | NOT NULL / NULL | |

索引与约束：**唯一索引 `uk_event_template(biz_event_id, template_key)`**（同一业务事件同一模板只发一次，重放不重复打扰）；普通索引 `idx_status_retry(status, next_retry_at)`。

### subscribe_quota（订阅消息额度计数）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| quota_id | bigint unsigned | PK | |
| member_id | bigint unsigned | NOT NULL | |
| template_id | varchar(48) | NOT NULL | 微信模板 ID |
| remaining | int | NOT NULL，默认 0 | 客户端上报授权成功后 +1，发送成功 -1（尽力计数，不保证准确） |
| last_granted_at / updated_at | datetime(3) | NOT NULL / NULL | |

索引与约束：唯一索引 `uk_member_template(member_id, template_id)`。

### idempotency_record（写操作幂等记录）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | bigint unsigned | PK | |
| member_id | bigint unsigned | NOT NULL | 操作者 |
| scope | varchar(32) | NOT NULL | `reserve`/`cancel`/`waitlist_join`/`waitlist_exit`/`signin`/`attendance_submit`/`makeup_signin` |
| idem_key | varchar(64) | NOT NULL | 客户端生成的 UUID |
| request_hash | varchar(64) | NULL | 防止同键不同参 |
| response_snapshot | json | NULL | 首次成功响应快照，重复请求原样返回 |
| status | varchar(16) | NOT NULL | `processing`/`done` |
| created_at / expire_at | datetime(3) | NOT NULL | MVP 保留 7 天 |

索引与约束：**唯一索引 `uk_scope_key(member_id, scope, idem_key)`**；普通索引 `idx_expire(expire_at)`。

### job_run（定时任务幂等表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | bigint unsigned | PK | |
| job_name | varchar(48) | NOT NULL | `t30_close_queue`/`remind_signin_20`/`remind_signin_5`/`signin_abnormal`/`attendance_overdue_60`/`finalize_24h`/`project_attendance_results`/`reconcile` |
| biz_key | varchar(96) | NOT NULL | 课次 ID、`{session_id}:{核对版本号}`（需要随核对结果重复执行的投影任务）或对账批次号 |
| run_at | datetime(3) | NOT NULL | |
| result | varchar(16) | NOT NULL | `success`/`skipped` |
| detail | json | NULL | |

索引与约束：**唯一索引 `uk_job_biz(job_name, biz_key)`**（同一课次的同一任务只跑一次；需要随核对版本重复执行的任务把版本号写进 `biz_key`，例如 `project_attendance_results` + `10023:4`）。

### audit_log（操作审计）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | bigint unsigned | PK | |
| actor_type / actor_id | varchar(16) / bigint unsigned | NOT NULL | |
| action | varchar(48) | NOT NULL | `reserve`/`cancel`/`waitlist_exit`/`signin`/`makeup_signin`/`attendance_override` 等 |
| target_type / target_id | varchar(32) / bigint unsigned | NOT NULL | |
| before_json / after_json | json | NULL | |
| created_at | datetime(3) | NOT NULL | |

索引与约束：普通索引 `idx_target(target_type, target_id, created_at)`。

## 3.3 状态机

### 3.3.1 课次与签到、核对状态

| 对象 | 状态集合 | 迁移触发 |
| --- | --- | --- |
| course_session.status | `scheduled` → `started` → `finished`（另 `cancelled` 属 P1 预留） | 按服务端时间派生，并由结算任务落库 |
| 签到 signin_status | `pending` → `signed`（教练在窗口内签到）/ `makeup`（店长补签）/ `abnormal`（开课后 15 分钟仍无签到，任务落库 + 通知店长） | 窗口 = 开课前 15 分钟 ～ 开课后 15 分钟 |
| `abnormal` → `makeup` | 店长在「课程结束 + 24 小时」内补签，同时**重开核对窗口**：`coach_edit_deadline_at = min(补签时间 + 60 分钟, 结束 + 24 小时)` | 故事 10 边界 |
| 核对 attendance_state | `unsubmitted` → `submitted`（提交成功或修改成功）→（10 秒内撤销）回退到上一版本；窗口关闭后教练端只读，店长可在结束 + 24 小时内提交或修改，到期 `finalized=1` | 提交/撤销/覆盖/硬上限；**每次结果变化都在同一事务内触发预约状态重投影（3.3.2）** |

### 3.3.2 预约状态机（reservation.status）

| 当前状态 | 触发 | 新状态 | 守卫条件（不满足则拒绝并给错误码） | 副作用 |
| --- | --- | --- | --- | --- |
| （无） | 会员在详情页点预约 | `booked` | 会员卡有效；`booked_count < capacity`；无时间冲突（半开区间）；该课未开始 | `booked_count + 1`；`active_flag = 1`；写 `cancel_deadline_at = start_at - 120 分钟`；审计日志 |
| （无） | 名额释放时自动转正 | `booked` | 该会员为队首中第一个无冲突者；`now < start_at - 30 分钟` | 同事务内：候补条目置 `promoted`、`active_flag = NULL`，`booked_count + 1`，`source = auto_promote`，`promoted_at = now`，`cancel_deadline_at = min(max(start_at - 120 分钟, now + 15 分钟), start_at - 30 分钟)`；提交后写转正通知 |
| `booked` | 会员点取消（普通预约） | `cancelled` | `now < cancel_deadline_at`（= 开课前 120 分钟） | `booked_count - 1`；`active_flag = NULL`；**同一事务内**跑 `releaseAndAllocate()`；写取消日志 |
| `booked` | 会员点取消（转正得到的预约，宽限窗口） | `cancelled` | `now < cancel_deadline_at`（见上分支公式） | 同上；超过截止则返回 40907，提示联系门店前台 |
| `booked` | 取消重复提交 | 不变 | 状态守卫：仅 `booked` 可取消 | 命中幂等键 → 原样返回首次结果；未带键 → 40908，**不二次释放名额** |
| `booked` | 结算/投影：按当前 `attendance_item.result` 投影（`present` → `completed`，`absent` → `absent`） | `completed` / `absent` | 该课存在有效核对（请求路径或补漏任务判定） | 写 `finished_at`；`active_flag` **保持 1**（结算不是取消，`booked_count` 不变，见 I2）；投影由单入口 `projectReservationStatus(result)` 完成 |
| `booked` | 课程结束 + 60 分钟仍无有效核对，或该课签到异常/漏提交 | `unverified`（暂定） | 该课无有效核对 | 写 `finished_at`；`active_flag` 保持 1；`finalized = 0`；不计入 no-show |
| `completed` / `absent` | **教练修改**核对结果（`coach_edit_deadline_at` 前）或**店长覆盖**（`end_at + 24 小时`前），含补签重开后教练重提 | 按新的 `result` 重投影为 `completed` / `absent` | `finalized = 0`；操作人有权限（本课教练在窗口内 / 本店店长在硬上限内） | **同一事务内重投影**：覆盖 `attendance_item.result` → 写 attendance_log（原状态 → 新状态）→ 按新结果重写预约状态与 no-show 口径（`absent ↔ completed` 可来回覆盖） |
| `unverified` | 店长在结束 + 24 小时内覆盖为到场/缺席；或补签重开窗口后教练提交 | `completed` / `absent` | 操作人有权；`now < end_at + 24 小时`；`finalized = 0` | 同上一行；**被覆盖为缺席的会员此时才计入 no-show** |
| `completed` / `absent` / `unverified` | 到 `end_at + 24 小时` 的 `finalize_24h` 任务 | 不变（锁定） | `finalized: 0 → 1` | `finalized = 1`，教练与店长入口同时关闭，`unverified` 成为终态 |
| `cancelled` | 任何操作 | 不变 | 终态（**唯一不可逆的终态**） | 返回 40908；取消释放名额的动作只发生在那一次迁移里 |
| `completed` / `absent` / `unverified`（`finalized = 1`） | 任何操作 | 不变 | 已锁定 | 教练动作返回 40913、店长动作返回 40920 |

**预约状态是 `attendance_item.result` 的投影（MUST，不允许「一次性结算」）**：`completed`/`absent`/`unverified` 不是「结算任务写一次就永远不变」的结果，而是「按当前最终核对结果投影出来的状态」；唯一的锁定时刻是 `end_at + 24 小时`（`finalized = 1`）。因此**所有会改变核对结果的路径都必须在同一个事务内重投影预约状态**：

| 改变核对结果的路径 | 谁做 | 时限 | 投影动作 |
| --- | --- | --- | --- |
| 提交核对（含课程结束前提前提交） | 教练 | 签到后 ～ `end_at + 60 分钟` | 提交事务内按 `items` 重投影该课全部未取消预约 |
| 修改核对（覆盖上一次结果） | 教练 | `end_at + 60 分钟`内 | 同上；`absent ↔ completed` 可来回覆盖 |
| 撤销提交（约 10 秒内） | 教练 | 提交后 10 秒 | 回退到上一版本，并重投影回上一版本的结果 |
| 补签后重开窗口、教练重新提交 | 店长补签 + 教练提交 | `min(补签时间 + 60 分钟, end_at + 24 小时)` | 同上 |
| 店长直接提交/修改（含把 `unverified` 覆盖为到场/缺席） | 店长 | `end_at + 24 小时`内 | 同上；此时判缺席才进 no-show 分子 |
| 60 分钟逾期任务判定「无有效核对」 | 系统 | `end_at + 60 分钟` | 未核对会员投影为 `unverified`（不计入 no-show） |

投影必须与 `attendance_item` 逐条一致：`present → completed`、`absent → absent`、无有效核对 → `unverified`。**定时任务只是补漏，正确性由请求路径保证**（见 5.7）；投影任务可重复执行（按当前 `result` 幂等重算，见 5.4）。

### 3.3.3 候补队列状态机（waitlist_entry.status）

| 当前状态 | 触发 | 新状态 | 守卫条件 | 副作用 |
| --- | --- | --- | --- | --- |
| （无） | 会员点加入候补 | `waiting` | 该课已满；无 active 预约；队列长度 < min(capacity, 20)（**与 `seq` 分配在同一把课次行锁内校验**）；`now < start_at - 30 分钟`；会员卡有效 | 在课次行锁内分配 `seq`（`max(seq)+1`）；返回 `rank`（服务端计算） |
| `waiting` | 名额释放，距开课 ≥ 30 分钟，本人无冲突 | `promoted` | 为队列中第一个无冲突者 | 同一事务内建预约（见 3.3.2 第二行）；提交后发转正通知 |
| `waiting` | 名额释放，距开课 ≥ 30 分钟，本人有冲突 | `exited`（`exit_reason = conflict`） | 冲突判定按 7.5 半开区间 | 名额在**同一事务内继续顺延**给下一位；通知「因与已预约课程时间冲突，已退出本课候补」；不占用、不释放名额 |
| `waiting` | 会员点「退出候补」 | `exited`（`exit_reason = member_exit`） | `now < start_at - 30 分钟`；状态仍为 `waiting` | `active_flag = NULL`；位次由 `seq` 计数实时重算（无需改他人行）；**不产生名额释放** |
| `waiting` | T-30 关队列任务 | `closed`（`close_reason = t30`） | `now ≥ start_at - 30 分钟` | 通知「本课候补已结束（如仍有余位可直接预约）」；加入与退出入口同时收起 |
| `waiting` | 课程取消 / 课程开始（故障防御路径） | `closed` | — | 不占名额 |
| `promoted` / `exited` / `closed` | 任何操作 | 不变 | 终态 | 重复入队/退出返回 40909 或按幂等键返回首次结果 |
| 竞态：退出请求到达时已被转正 | 会员点退出，但同一瞬间名额已转正给他 | 候补条目为 `promoted`，按**取消预约**口径处理 | 以服务端事务先后为准 | 走宽限取消窗口：成功则名额按 `releaseAndAllocate()` 处置；失败返回 40907。不允许出现「已退出但仍被自动约上」或「同一名额既转正又给别人」 |

**并发入队（MUST，对应 PRD 故事 5「序号连续且不重复」）**：入队请求在**同一事务内先对课次行取排他写锁**（与预约、取消、转正同一把锁），在锁内完成「读队列长度 → 校验上限 → 分配 `seq = max(seq) + 1` → 插入」四步。因此同一秒内的两个入队请求必然拿到不同的 `seq`（`uk_session_seq(session_id, seq)` 是数据库层兜底），队列长度也不会被并发插入突破到 `min(capacity, 20)` 以上。位次由 `seq` 计数实时算出，前一位退出或被转正后，后面所有人的位次自动前移，不需要改动他人行，也不会出现「两人都显示第 3 位」。

### 3.3.4 队列位次与「我的状态」投影

位次 = 该课次中 `status = 'waiting'` 且 `seq` 小于本人的条目数 + 1，由服务端在查询时计算（`idx_queue` 支撑），会员端不自行推算。会员端列表的「我的状态」由 `reservation` 与 `waitlist_entry` 两个集合投影得到，取值集合：`none` / `booked`（含 `source = auto_promote`，卡片文案为「已自动转正为预约」）/ `waiting`（带位次）/ `exited`（带退出原因）/ `cancelled` / `completed` / `absent` / `unverified`。**不出现**「待确认」「待你确认」这类中间态文案。

## 3.4 不变量与对账

| 编号 | 不变量 | 保证方式 |
| --- | --- | --- |
| I1 | `0 ≤ booked_count ≤ capacity` | 写入路径全部在课次行锁内做条件更新；接口不直接改计数 |
| I2 | `booked_count == 该课次 active_flag = 1`（未取消）的预约条数`。**该口径在课程结束后依然成立**：结算/投影只改 `status`，不改 `active_flag`、不改 `booked_count` | 预约增删与计数自增/自减在同一事务；`reconcile` 任务每 5 分钟比对并告警。**禁止用 `status='booked'` 做分母**——否则每节刚结束的课都会误报「数据不一致」，把真告警淹没 |
| I3 | 队列长度 `= count(waiting) ≤ min(capacity, 20)`，且同一课次内 `seq` 互不重复 | 入队事务在课次行锁内「校验上限 + 分配 seq + 插入」；`uk_session_seq(session_id, seq)` + `uk_active` 唯一索引兜底 |
| I4 | 同一 `(session_id, member_id)` 最多 1 条 active 预约（未取消）、1 条 active 候补，且二者不同时为 active | `uk_active` 两个唯一索引 + 转正时同一事务内把候补置非 active 再建预约；取消后重新预约会新建记录（旧记录 `active_flag = NULL`，不参与唯一约束） |
| I5 | 每次释放恰好产生一种结局：转正 1 人 / 回公开可约 / 作废；提交后不存在「已释放未分配」 | `releaseAndAllocate()` 单入口、单事务、返回枚举穷尽 |
| I6 | 不存在「课表显示已满但实际有空位」 | 由 I5 保证；`reconcile` 任务额外做「剩余名额 = capacity - booked_count」一致性校验 |
| I7 | 转正预约满足 `promoted_at ≤ cancel_deadline_at ≤ start_at - 30 分钟` | `calcCancelDeadline()` 单函数，写入前断言 |

`reconcile` 任务同时直接监测 PRD 第 10 章的回滚条件（超卖、遗留悬空名额），是「超卖事故数 = 0」这个指标的自动检测手段。**对账断言只使用 I1–I7 的口径**（尤其 I2 用 `active_flag`），因此已结束的课次不会触发告警；另加「投影一致性」断言：`finalized = 1` 的课次，每条预约状态必须等于该课 `attendance_item.result` 的投影结果（无核对时为 `unverified`）。

# 4. 接口设计

## 4.1 通用约定

- 前缀 `/api/v1`；请求与响应均为 JSON；鉴权用 `Authorization: Bearer <token>`（token 内含 member_id 与 role，有效期 2 小时，小程序端静默续期）。
- **服务端时间是唯一时间来源**：所有涉及窗口（取消截止、签到窗口、T-30、核对截止）的判断都用服务端时间，接口不接受客户端时间参数。
- 所有写操作（预约、取消、加入/退出候补、签到、提交核对、补签）**必须带 `idempotencyKey`**（客户端生成的 UUID，一次用户操作生成一个，重试沿用同一个）。
- 统一响应结构：

```json
{
  "code": 0,
  "message": "ok",
  "data": {}
}
```

```json
{
  "code": 40901,
  "message": "名额已被抢完",
  "data": {
    "sessionId": "10023",
    "canJoinWaitlist": true,
    "queueCount": 3,
    "queueLimit": 20
  }
}
```

- HTTP 状态码与业务码同时使用：`200/201` 成功，`400` 参数，`401` 登录态，`403` 权限/会员卡，`404` 资源不存在，`409` 规则冲突，`500/503` 服务端与依赖故障。

## 4.2 会员端接口（F1–F6）

### 4.2.1 POST /api/v1/auth/login（F1 登录）

| 方向 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| 入参 | code | string | 是 | `wx.login` 返回的一次性 code |
| 出参 | token | string | | 登录态 |
| 出参 | expiresIn | number | | 秒 |
| 出参 | role | string | | `member`/`coach`/`manager`（决定默认落地页，满足故事 2 的角色分流） |
| 出参 | memberBound | boolean | | 是否已绑定会员卡 |
| 出参 | member | object | | 已绑定时返回 `memberId`、`nameMasked`、`cardStatus`、`cardExpireAt`、`storeId` |
| 错误码 | 40001 / 40102 / 50001 | | | |

### 4.2.2 POST /api/v1/members/bind-phone（F1 绑定会员卡）

入参：`phoneCode`（`getPhoneNumber` 返回的 code，必填）。
出参：`memberId`、`nameMasked`、`phoneMasked`、`cardStatus`、`cardExpireAt`、`storeId`、`role`。
错误码：`40001`/`40401`（手机号查不到，文案「未查到会员信息，请联系门店前台」，附门店电话）/`40916`（手机号与门店记录不一致，走「联系前台核验」）/`50301`（会员卡校验接口不可用，见第 9 章假设 A2）。

### 4.2.3 GET /api/v1/sessions（F2 课表）

入参（query）：`storeId`（必填）、`from`、`to`（默认今天起 7 天，最长 7 天）。
出参：按日期分组的课次数组，每个课次含：`sessionId`、`title`、`coachName`、`startAt`、`endAt`、`storeId`、`capacity`、`remaining`（= capacity − booked_count，服务端算）、`waitlistCount`、`status`（`scheduled`/`started`/`finished`）、`bookable`、`waitlistJoinable`、`myStatus`、`myWaitlistRank`、`cancelDeadlineAt`、`noticeText`（「课程信息以门店现场为准」）。
空状态：某天无课时返回该日期 `sessions: []`，由客户端展示「今天暂无课程」。
错误码：`40001`/`40101`/`40401`（课表数据源异常时返回「课程信息已更新，请重新查看」）`/50301`。

### 4.2.4 GET /api/v1/sessions/{sessionId}（F2/F3 课程详情）

出参在课表字段基础上增加：`bookedCount`、`canReserve`、`canJoinWaitlist`、`canExitWaitlist`、`waitlistNotice`（**加入候补前必须展示的告知文案**：「有人退课时，系统会自动帮你约上这节课，请及时查看；如不去请在可取消截止时间前自行取消」）、`cancelDeadlineAt`、`myItem`（我的预约/候补条目详情）。
错误码：`40101`/`40402`（课次不存在或已变更）。

### 4.2.5 POST /api/v1/sessions/{sessionId}/reservations（F3 预约）

| 方向 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| 入参 | idempotencyKey | string | 是 | 幂等键 |
| 出参 | reservationId | string | | |
| 出参 | status | string | | 固定 `booked` |
| 出参 | source | string | | 固定 `normal` |
| 出参 | cancelDeadlineAt | string | | `start_at − 120 分钟` |
| 出参 | session | object | | `bookedCount`、`capacity`、`remaining` |
| 错误码 | `40901` 名额已被抢完（附 `canJoinWaitlist`、`queueCount`）/ `40902` 与已预约课程时间冲突（附冲突课程名）/ `40903` 已预约过该课（带同一幂等键时改为 200 + 首次结果）/ `40302` 会员卡已过期或停卡 / `40915` 课程已开始或已结束 / `40001` / `40101` | | | |

```json
{
  "code": 40901,
  "message": "名额已被抢完，是否加入候补？",
  "data": {
    "sessionId": "10023",
    "canJoinWaitlist": true,
    "queueCount": 3,
    "queueLimit": 20,
    "myStatus": "none"
  }
}
```

### 4.2.6 GET /api/v1/me/items（F4/F5/F6 我的预约与候补）

入参（query）：`status`（默认 `all`）、`page`、`pageSize`。
出参：合并列表，每项含：`type`（`reservation`/`waitlist`）、`sessionId`、`title`、`coachName`、`startAt`、`status`（预约状态或候补状态）、`waitlistRank`（候补中时）、`exitReason`（已退出时）、`isAutoPromoted`、`cancelDeadlineAt`、`startCountdownSeconds`、`actionText`、`noticeText`（「未核实」旁的说明「本次未计入缺席，不影响你的记录」）。
说明：这是转正结果的**权威路径**，转正与否与是否收到订阅消息无关；`myStatus` 直接取 `booked`（含转正），客户端不出现任何确认类操作。
错误码：`40101`。

### 4.2.7 POST /api/v1/reservations/{reservationId}/cancel（F4 取消预约，含转正预约的宽限取消）

入参：`idempotencyKey`（必填）、`reason`（可选，字符串）。
出参：`reservationId`、`status`（`cancelled`）、`cancelDeadlineAt`、`released`（boolean）、`assign`（本次名额的处置结果，三种之一）。
错误码：`40907` 已过本课取消截止时间（附该会员的 `cancelDeadlineAt` 与「如需取消请联系门店前台」）/ `40908` 该预约已取消（带同一幂等键时返回 200 + 首次结果）/ `40301` 非本人预约 / `40403` 预约不存在 / `40915` 课程已开始或已结束。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "reservationId": "88012",
    "status": "cancelled",
    "cancelDeadlineAt": "2025-06-01T17:00:00+08:00",
    "released": true,
    "assign": {
      "type": "auto_promoted",
      "promotedReservationId": "88045",
      "promotedAt": "2025-06-01T15:20:11+08:00",
      "remaining": 0
    }
  }
}
```

`assign.type` 取值：`auto_promoted`（自动转正给队首无冲突者）、`back_to_public`（队列已空/全部冲突/已过 T-30，名额回公开可约，附 `remaining`）、`void_course_started`（课程已开始，名额作废）。

### 4.2.8 POST /api/v1/sessions/{sessionId}/waitlist（F5 加入候补队列）

入参：`idempotencyKey`（必填）、`disclosureShown`（boolean，可选，仅记录「告知文案已展示」用于审计，**不作为前置条件，也不要求任何确认动作**）。
出参：`entryId`、`status`（`waiting`）、`rank`、`queueSize`、`queueLimit`。
错误码：`40904` 你已在候补队列中（第 n 位）/ `40905` 候补人数已满 / `40906` 本课不再接受候补（T-30 或课程已开始）/ `40910` 该课仍有余位，请直接预约 / `40302` / `40001`。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "entryId": "5512",
    "status": "waiting",
    "rank": 3,
    "queueSize": 5,
    "queueLimit": 20,
    "waitlistNotice": "有人退课时，系统会自动帮你约上这节课，请及时查看；如不去请在可取消截止时间前自行取消"
  }
}
```

### 4.2.9 DELETE /api/v1/sessions/{sessionId}/waitlist（F5 主动退出候补）

入参：`idempotencyKey`（必填）。
出参：`status`（`exited_member`）、`exitedAt`、`released`（固定 `false`）、`rankShifted`（boolean，说明后续会员位次已前移）。
竞态返回：若请求到达时该会员已被自动转正，则本次按**取消预约**口径处理，返回 `status = "promoted_then_cancelled"`、`cancelled`（boolean）、`reservationId`、`cancelDeadlineAt`；取消失败则返回 `40907`。
错误码：`40909` 你已不在本课候补队列中（重复提交，幂等键命中时返回 200 + 首次结果）/ `40906` 已过退出截止（T-30 或课程已开始，入口已收起）。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "exited_member",
    "exitedAt": "2025-06-01T10:02:31+08:00",
    "released": false,
    "rankShifted": true
  }
}
```

### 4.2.10 POST /api/v1/me/subscribe-grants（F6 订阅消息额度上报，增强路径）

入参：`templateId`（必填）、`granted`（boolean，`requestSubscribeMessage` 的回调结果）、`scene`（`waitlist_join` 等）。
出参：`remaining`（服务端尽力计数的剩余额度）。
说明：仅用于提升触达率；上报失败不影响任何业务，服务端也不把额度当作发消息的硬前置。

## 4.3 教练端接口（F7–F9）

### 4.3.1 GET /api/v1/coach/sessions

入参（query）：`from`、`to`（默认今天起 7 天）。
出参：每节课含 `sessionId`、`title`、`startAt`、`endAt`、`bookedCount`、`capacity`、`waitlistCount`、`promotedCount`、`signinStatus`、`attendanceState`、`signinWindow`（`opensAt`/`closesAt`）、`canSignin`。
空状态：无课时返回空数组，客户端展示「暂无排给你的课程，请联系店长」。
错误码：`40101`/`40301`。

### 4.3.2 GET /api/v1/coach/sessions/{sessionId}（学员名单）

出参：`session`（课次信息）、`signin`（`status`/`signinAt`/`type`）、`attendance`（`state`、`deadlineAt`、`editDeadlineAt`、`counts`：`present`/`absent`/`unverified` 三列并列 **不相加**）、`canEdit`、`roster`（**该课次全部未取消的预约**，即 `active_flag = 1` 的会员：姓名、头像、预约 `status`、`result`、`isAutoPromoted`、`canChooseTwoState`）。

**名单口径（MUST，与 PRD 故事 7 原话一致）**：名单 =「全部已预约且未取消的会员」= `status ∈ {booked, completed, absent, unverified}`，**唯一排除条件是 `cancelled`**；候补中尚未转正的会员不出现在名单里（候补既不是预约、也不是「已取消」）。之所以不按 `status='booked'` 过滤：结算与逾期任务会把状态改成 `completed`/`absent`/`unverified`，而教练的核对窗口要到「课程结束 + 60 分钟」、店长的兜底窗口要到「课程结束 + 24 小时」——**名单必须在这两个窗口里始终可读可改**（教练 19:55 提前提交、20:00 结算、20:20 再打开核对页，看到的仍是同一份名单，而不是空名单）。
边界：0 人预约时返回空 `roster` 且 `canSignin` 仍为 true（空课也要留签到记录）。
错误码：`40301`（你不是该课教练、也不是本店店长，文案「请联系店长处理」，不提供自助代签）/ `40402`。
权限：**本课教练或本店店长都可读取**（店长兜底必须先拿到名单，专用接口见 4.4.5；本接口对店长只读，店长的写操作仍只走 4.4.4）。

### 4.3.3 POST /api/v1/coach/sessions/{sessionId}/signin（F8 签到）

| 方向 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| 入参 | idempotencyKey | string | 是 | 幂等键 |
| 出参 | signinStatus | string | | `signed` |
| 出参 | signinAt | string | | 首次签到时间，重复点击返回同一时间 |
| 出参 | type | string | | `normal` |
| 错误码 | `40301` 非本课教练 / `40911` 签到窗口未开放（附 `opensAt`，文案「开课前 15 分钟开放签到」）/ `40912` 签到已超时（附店长联系方式，文案「签到已超时，请联系店长补签」）/ `40922` 课程已取消 / `40402` | | | |

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "signinStatus": "signed",
    "signinAt": "2025-06-01T18:58:40+08:00",
    "type": "normal",
    "idempotent": true
  }
}
```

### 4.3.4 GET /api/v1/coach/sessions/{sessionId}/attendance（F9 核对页数据）

出参：`items`（会员 + 当前 `result`）、`defaultResult`（固定 `present`，用于「默认全选到场」）、`counts`、`unmarkedCount`、`editDeadlineAt`、`canEdit`、`noticeText`。
错误码：`40301`/`40402`。
说明：`items` 的会员集合**与 4.3.2 的 `roster` 完全同口径**（该课次全部未取消的预约，含 `completed`/`absent`/`unverified`），因此结算之后、60 分钟截止之前打开核对页仍能看到全部会员并修改；`canEdit = false`（超窗口、`finalized = 1`、或签到异常未补签）时 `items` 仍照常返回，面板只读。

### 4.3.5 POST /api/v1/coach/sessions/{sessionId}/attendance-submissions（F9 提交核对）

| 方向 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| 入参 | submissionId | string | 是 | 客户端生成的幂等键 |
| 入参 | expectedVersion | number | 是 | 乐观锁版本（来自上次读取） |
| 入参 | items | array | 是 | 每项 `{ memberId, result }`，`result` ∈ `present`/`absent`（**只能两态**） |
| 出参 | submissionId / submittedAt / version | | | |
| 出参 | counts | object | | `present`/`absent`/`unverified` |
| 出参 | revokeDeadlineAt | string | | `submittedAt + 10 秒` |
| 错误码 | `40914` 还有 n 人未标记（附 `unmarkedMemberIds`）/ `40917` 提交版本冲突，请刷新后重试 / `40913` 核对窗口已关闭（文案「请联系店长，由店长在课程结束后 24 小时内提交或修改」）/ `40301` / `40001` | | | |

**副作用（3.3.2 的投影规则）**：提交或修改成功后，服务端在**同一事务内**按最终 `result` 重投影该课全部未取消预约的状态（`present → completed`、`absent → absent`），逐条写 `attendance_log`；`booked_count` 不变（结算不是取消）。因此课程结束后 60 分钟内再改一次，会把预约状态从 `completed` 改回 `absent`（或反之），会员端出勤记录与 no-show 分子随之变化——这是 PRD 故事 9/10 要求的，不是数据错误。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "submissionId": "30087",
    "submittedAt": "2025-06-01T20:31:02+08:00",
    "version": 4,
    "counts": { "present": 6, "absent": 2, "unverified": 0 },
    "revokeDeadlineAt": "2025-06-01T20:31:12+08:00"
  }
}
```

### 4.3.6 POST /api/v1/coach/sessions/{sessionId}/attendance-submissions/{submissionId}/revoke（F9 提交后约 10 秒内撤销）

入参：`idempotencyKey`（必填）。
出参：`revertedToVersion`（回退到的版本号；若无上一版本则回到 `unsubmitted`）、`counts`。
错误码：`40921` 撤销窗口已过 / `40301` / `40403`。
说明：撤销成功后，服务端在同一事务内按回退后的版本重投影该课预约状态（3.3.2）；因此即使课程已经结束、结算已经发生过，10 秒内的撤销同样能把 `completed`/`absent` 改回上一版本的结果。

## 4.4 店长端接口（F10）

### 4.4.1 GET /api/v1/manager/sessions

入参（query）：`storeId`（必填）、`date`（默认今天）。
出参：每节课含 `sessionId`、`title`、`coachName`、`startAt`、`bookedCount`、`capacity`、`waitlistCount`、`promotedCount`、`signinStatus`、`counts`（`present`/`absent`/`unverified`）、`isEmptyCourse`（报名 0 且已开始）、`canMakeupSignin`、`canOverrideAttendance`。
说明：**不出现「待确认人数」**（自动转正没有待确认状态）。
错误码：`40301`（非本店店长，拒绝访问，只允许 `store_id` 匹配）/ `40101`。

### 4.4.2 GET /api/v1/manager/alerts

入参（query）：`storeId`、`status`（默认 `open`）、`date`。
出参：告警列表，每项 `type`（`signin_abnormal` / `attendance_overdue_60` / `system_anomaly`）、`sessionId`、`startAt`、`coachName`、`coachPhone`、`raisedAt`、`deadlineAt`。
说明：这是店长提醒的**权威路径**（页面打开即可见），订阅消息仅为增强。

### 4.4.3 POST /api/v1/manager/sessions/{sessionId}/makeup-signin（补签）

入参：`idempotencyKey`（必填）、`reason`（必填，≤200 字）。
出参：`signinStatus`（`makeup`）、`operatorId`、`signinAt`、`attendanceEditDeadlineAt`（= `min(补签时间 + 60 分钟, end_at + 24 小时)`）、`reopened`（boolean，核对窗口是否重开）。
错误码：`40918` 已超过补签截止时间（课程结束 + 24 小时）/ `40919` 该课已有有效签到，无需补签 / `40301` / `40402`。

### 4.4.4 POST /api/v1/manager/sessions/{sessionId}/attendance-submissions（店长兜底提交/修改）

入参与出参同 4.3.5，额外入参 `overrideReason`（选填）；`submitterRole = manager`。
权限说明：**不区分教练是否提交过核对**——教练从未提交、已提交但过了 60 分钟、被标记签到异常后补签、补签后教练又没提交，店长都可在「课程结束 + 24 小时」内直接提交或修改；已 `finalized` 的课直接拒绝。名单从 4.4.5 读取（教练端 4.3.2 对本店店长也放行）；提交/修改成功后服务端在**同一事务内**按最终 `result` 重投影本课全部预约状态（3.3.2），因此 `completed` 与 `absent` 可以互相覆盖、no-show 口径随之变化，逐条写 `attendance_log`。
错误码：`40920` 已超过核对硬上限（课程结束 + 24 小时），「未核实」为终态 / `40917` / `40301` / `40402`。

### 4.4.5 GET /api/v1/manager/sessions/{sessionId}（店长读取本课名单）

用途：4.4.4 的入参 `items` 需要 `memberId` 列表，店长在「课程结束 + 24 小时」内提交或修改核对之前必须先能读出本课名单。**这是 F10 兜底路径的读入口**：4.4.1 只给计数、4.4.2 只给告警，缺了本接口店长拿不到名单，兜底提交无法完成。

入参（query）：无（`sessionId` 在路径上）。
出参：与 4.3.2 同构——`session`（课次信息）、`signin`（`status`/`signinAt`/`type`）、`attendance`（`state`、`counts`：`present`/`absent`/`unverified` 三列不相加、`deadlineAt`、`editDeadlineAt`）、`roster`（**该课次全部未取消的预约**：`memberId`、姓名、头像、预约 `status`、`result`、`isAutoPromoted`）、`canOverride`（是否仍在 `end_at + 24 小时`内且 `finalized = 0`）、`unverifiedCount`（需要覆盖的「未核实」人数）。
权限：只允许本店店长（`course_session.store_id == 当前账号 store_id`），否则 40301；不返回任何跨店数据。
错误码：`40301`（非本店店长）/ `40402`（课次不存在）。
说明：本接口与 4.3.2 共用同一个领域层查询函数 `loadRoster(sessionId)`，**名单口径只允许有一份实现**（唯一排除条件 = `cancelled`）。

## 4.5 错误码

| 码 | 含义 | HTTP | 客户端处理建议 |
| --- | --- | --- | --- |
| 0 | 成功 | 200/201 | — |
| 40001 | 参数校验失败 | 400 | 提示通用错误，上报日志 |
| 40100 | 未登录（缺 token） | 401 | 静默重新登录 |
| 40101 | 登录态失效 | 401 | 静默续期后重试一次 |
| 40102 | 微信 code 无效或已使用 | 401 | 重新取 code |
| 40301 | 无权限（非本课教练 / 非本店店长 / 非本人预约） | 403 | 展示对应文案，不重试 |
| 40302 | 会员卡已过期或已停卡 | 403 | 禁用预约按钮 + 「请到前台办理续卡」 |
| 40401 | 会员信息不存在 | 404 | 「未查到会员信息，请联系门店前台」+ 门店电话 |
| 40402 | 课次不存在或已变更 | 404 | 「课程信息已更新，请重新查看」 |
| 40403 | 预约记录不存在 | 404 | 刷新列表 |
| 40901 | 名额已被抢完 | 409 | 引导加入候补（返回体带 `canJoinWaitlist`） |
| 40902 | 与已预约课程时间冲突 | 409 | 提示冲突课程名 |
| 40903 | 已预约过该课 | 409 | 跳转「我的预约」（幂等命中时为 200） |
| 40904 | 你已在候补队列中 | 409 | 展示位次 |
| 40905 | 候补人数已满 | 409 | 按钮置灰 |
| 40906 | 本课不再接受候补 | 409 | 按钮置灰（T-30 或已开始） |
| 40907 | 已过本课取消截止时间 | 409 | 提示联系门店前台 |
| 40908 | 该预约已取消 | 409 | 刷新状态（幂等命中时为 200） |
| 40909 | 你已不在本课候补队列中 | 409 | 刷新状态 |
| 40910 | 该课仍有余位，请直接预约 | 409 | 切换到预约按钮 |
| 40911 | 签到窗口未开放 | 409 | 展示开放时间 |
| 40912 | 签到已超时 | 409 | 引导联系店长补签 |
| 40913 | 核对窗口已关闭 | 409 | 引导联系店长 |
| 40914 | 还有 n 人未标记 | 409 | 高亮未标记会员 |
| 40915 | 课程已开始或已结束 | 409 | 收起操作入口 |
| 40916 | 手机号与门店记录不一致 | 409 | 走「联系前台核验」引导 |
| 40917 | 提交版本冲突 | 409 | 刷新后重试（乐观锁） |
| 40918 | 已超过补签截止时间 | 409 | 收起补签入口 |
| 40919 | 该课已有有效签到，无需补签 | 409 | 收起补签入口 |
| 40920 | 已超过核对硬上限 | 409 | 只读展示 |
| 40921 | 撤销窗口已过 | 409 | 提示不可撤销 |
| 40922 | 课程已取消 | 409 | 展示「课程已取消」 |
| 50001 | 服务内部错误 | 500 | 通用失败提示 + 重试按钮 |
| 50301 | 会员卡校验服务不可用 | 503 | 「暂时无法校验会员卡状态，请稍后重试」 |
| 50302 | 微信接口不可用 | 503 | 提示稍后重试 |

## 4.6 模块之间的接口

| 调用方 → 被调方 | 契约要点 |
| --- | --- |
| reservation-service → domain-core | `hasOverlap(a, b) → boolean`；`calcCancelDeadline(reservation, session, now) → datetime`；`releaseAndAllocate(session, now) → AllocationResult`（枚举：`auto_promoted(memberId, reservationId)` / `back_to_public(remaining)` / `void_course_started`） |
| waitlist-service → domain-core | 入队校验（满员、上限、T-30、卡状态）；退出校验（T-30 前、仍为 waiting）；`releaseAndAllocate()` 中的冲突预检复用 `hasOverlap` |
| reservation-service → notify-service | 事务内写 outbox（`biz_event_id` + `template_key`），**不等待发送结果** |
| attendance-service → domain-core | `calcCoachEditDeadline(session, signin)`、`calcAttendanceHardLimit(session)`；`projectReservationStatus(result)` 把 `present`/`absent`/无核对投影为 `completed`/`absent`/`unverified`（提交、修改、撤销、店长覆盖、补签重开、逾期任务六条路径共用这一个函数） |
| attendance-service → reservation-service | 结果变化后的重投影走 `applyProjection(sessionId, version)`（幂等：按当前 `attendance_item.result` 重算，与执行次数无关）；投影只改预约 `status` 与 `finished_at`，不动 `booked_count`、不动 `active_flag` |
| waitlist-service → course_session 行锁 | 入队与退出都在课次行锁内完成（含 `seq` 分配与队列上限校验），与 reservation-service 的抢位共用同一把锁 |
| job-runner → 各服务 | 只调用幂等收尾动作（关队列、提醒、结算、finalize），不直接改计数 |
| 所有服务 → audit_log | 写操作统一落审计（谁、何时、什么、原值 → 新值） |

# 5. 关键难点与方案

## 5.1 满员并发抢位：怎么保证不超额预约（并发）

**三重防线，缺一不可：**

1. **唯一索引（幂等的最后一道墙）**：`uk_active(session_id, member_id, active_flag)` 保证同一会员对同一课次只能有一条有效预约，重复点击不会产生第二条记录。取消置 `active_flag = NULL` 后该位置释放，历史记录保留。
2. **行锁串行化 + 条件更新（防超卖的主防线）**：所有会改变占用数或排队序号的写操作（**预约、取消、自动转正、加入候补、退出候补**）都在**同一事务**内先对课次行取行级排他写锁，再用「仅当 `booked_count < capacity` 时才自增」的条件更新占用计数。受影响行数为 0 即代表名额已被抢完，返回 40901（并带上 `canJoinWaitlist`，让失败者一键加入候补）。同一课次的并发请求被行锁串行化，因此不存在「两个事务都读到还有 1 个名额」的窗口；**加入候补也走同一把锁**，是为了让「队列长度校验 + `seq` 分配 + 插入」成为原子的——否则同一秒的两个入队请求会算出同一个 `seq`（两人都显示第 3 位），或把 19 人的队列插成 21 人。
3. **对账任务（防静默数据腐化）**：`reconcile` 每 5 分钟校验 `booked_count` 与该课次 `active_flag = 1`（未取消）的预约条数是否一致、是否满足 `0 ≤ booked_count ≤ capacity`，不一致即告警并写入异常事件（同时触发店长与工程值班通知）。这正是 PRD「超卖事故数 = 0」与第 10 章回滚条件的自动监测手段。

**为什么不用乐观锁或分布式锁**：乐观锁（版本号 + 重试）在只剩最后 1 个名额时会让大量请求重试，放大了峰值且用户体验是「点了没反应」；分布式锁（Redis）需要多引入一个组件和处理锁超时、锁失效的一致性问题，而本场景的热点只是「单个课次行」，数据库行锁的粒度已经足够小、持有时长在毫秒级。这是「不引入不必要中间件」的取舍，代价是同一课次的写请求完全串行（本场景可接受，见 2.5 估算）。

**如何验证不会超卖（需要在有运行环境时执行，本阶段未执行）：**

| 验证项 | 方法 | 通过标准 |
| --- | --- | --- |
| 单课次并发抢位 | 测试环境同一课次发起 200 并发预约（容量设为 20、已占 19），重复 50 轮 | 成功数恰好 = 剩余名额；`booked_count ≤ capacity`；无负数；失败者全部拿到 40901 且可加入候补 |
| 最后 1 个名额抢位 | 容量 1、两个不同会员同时请求 | 恰好 1 人成功 |
| 并发「取消 + 抢位」 | 1 人取消（触发自动转正）同时 3 人抢位、重复 50 轮 | `booked_count` 始终 ≤ capacity；被转正者与抢位者不共享同一名额；无「已满但有空位」 |
| 同会员重复提交 | 同一幂等键并发 5 次、以及 2 秒内点 3 次 | 只生成 1 条预约记录，占用只 +1 |
| 并发入队（序号唯一） | 课已满、队列已有 19 人，同一课次 50 并发加入候补，重复 20 轮 | 成功数 ≤ 1（队列上限 20）；`count(distinct seq) = count(waiting)`；无两条 `waiting` 记录 `seq` 相同；失败者全部拿到 40905 |
| 并发入队（位次确定） | 同一秒内 5 个不同会员并发加入同一课次候补 | 5 个 `seq` 互不相同且连续，5 个位次互不相同（不会两人都显示「第 3 位」） |
| 并发「取消 + 抢位 + 入队」混合 | 1 人取消、3 人抢位、3 人加入候补同时发起，重复 50 轮 | `booked_count ≤ capacity`；`count(waiting) ≤ min(capacity, 20)`；被转正者与抢位者不共享名额 |
| 结算与结果重投影 | 注入时间依次执行「提交缺席 → 教练改成到场 → 店长覆盖为缺席」 | 预约状态每次都跟着变（`absent → completed → absent`）；`attendance_log` 三条；每一步 `reconcile` 都不报不一致 |
| 名单在窗口内始终可读 | 注入时间：19:55 教练提前提交、20:00 结算、20:20 教练再打开核对页；另跑一遍「教练始终未提交、店长 24 小时内兜底」 | 两种情形名单都返回全部未取消会员（含 `completed`/`absent`/`unverified`），教练可改、店长可从 4.4.5 读到名单并提交 |
| 数据一致性巡检 | 跑完整轮次后执行对账断言 | I1–I7 全部成立 |

## 5.2 取消 → 自动转正 → 回公开可约的时序

**取消是一个「名额分配事件」，不是一次记录删除**（PRD 7.2/7.5）。全部动作在**一个事务**内完成，顺序固定：

1. 锁课次行；
2. 校验预约属于该会员且状态为 `booked`，否则返回 40908；
3. 用 `calcCancelDeadline()` 校验取消截止时间（普通预约 = 开课前 120 分钟；转正得到的预约 = `min(max(开课前 120 分钟, promoted_at + 15 分钟), T-30)`），超时返回 40907；
4. 预约置 `cancelled`、`active_flag = NULL`、`booked_count − 1`；
5. 调用 `releaseAndAllocate(session, now)`：若课程已开始 → 作废（结束）；否则若距开课 ≥ 30 分钟且有 `waiting` 条目 → 从 `seq` 最小的一位开始做冲突预检，无冲突者**立即自动转正**（建预约、`booked_count + 1`、候补置 `promoted`），有冲突者**移出队列**（`exited` + `conflict`）后取队首中的下一位继续；队列已空或全部冲突或距开课 < 30 分钟 → 名额回到公开可约（对外 `remaining + 1`，先到先得）；
6. 提交事务；
7. **提交成功后**才写 `notify_outbox`（转正通知 / 冲突退出通知 / 取消结果），发送由异步 worker 做。

要点：第 5 步一定在第 6 步之前完成，所以**不存在「已释放但未分配」的中间态**，也不会出现「课表显示已满但实际有空位」。因为名额在取消事务内就已经归属到一位真实会员（或立即对外可约），T-30 之前不会出现「锁定但无人持有」。

**T-30（开课前 30 分钟）之后的行为**：不再有任何自动转正，释放出的名额**直接回到公开可约**；同时 T-30 关队列任务把仍为 `waiting` 的会员统一置 `closed` 并通知「本课候补已结束（如仍有余位可直接预约）」，入队与退出口同时收起。

## 5.3 退出候补与自动转正的竞态

两个请求可能同时到达（PRD 故事 5 的竞态要求）。两者都先对**同一课次行**取锁，因此服务端天然获得一个确定的先后顺序，不存在「同时成功」：

- 退出先提交：候补条目置 `exited(member_exit)`；此后的任何释放都不会再把他选为转正对象（选择条件是 `status = 'waiting'`）。
- 转正先提交：候补条目已是 `promoted`、且已生成正式预约；此时到达的退出请求按**取消预约**口径处理——用 `calcCancelDeadline()` 判断是否还在宽限窗口内，在窗口内则取消成功、名额按 5.2 重新分配；已过截止则返回 40907。这正是 PRD 要求的「以服务端事务先后为准」，且不会出现「已退出但仍被自动约上」或「同一名额既转正又给别人」。

## 5.4 幂等实现

统一采用**「客户端幂等键 + 业务唯一约束」双保险**：

| 场景 | 幂等键 | 业务侧最终保证 | 重复请求的行为 |
| --- | --- | --- | --- |
| 预约 | `(member_id, 'reserve', idempotencyKey)` | `uk_active(session_id, member_id, active_flag)` | 幂等键命中 → 原样返回首次 `reservationId`（`idempotent: true`）；键不同但已预约 → 40903 |
| 取消预约 | `(member_id, 'cancel', idempotencyKey)` | 状态守卫：只有 `booked` 能迁到 `cancelled`，**释放动作只发生在那一次迁移里** | 幂等键命中 → 返回首次结果；键不同重复取消 → 40908，**不二次释放名额** |
| 自动转正 | 释放事件的 `biz_event_id`（如 `cancel:{reservation_id}`） | 分配前在同一事务内二次确认候补条目仍为 `waiting`、且 `booked_count < capacity` | 同一释放事件重放不会产生第二个转正者 |
| 加入候补 | `(member_id, 'waitlist_join', idempotencyKey)` | `uk_active(session_id, member_id, active_flag)` | 幂等键命中 → 返回首次 `entryId` 与位次；否则 40904 |
| 退出候补 | `(member_id, 'waitlist_exit', idempotencyKey)` | 状态守卫：只有 `waiting` 能迁到 `exited` | 重复提交 → 返回首次结果或 40909，**不重复改动他人位次** |
| 教练签到 | `(member_id, 'signin', idempotencyKey)` | **唯一索引 `uk_session(session_id)`**：一门课一条签到记录 | 重复点击 → 返回首次 `signinAt`（`idempotent: true`），不新增记录、不改首次时间 |
| 提交核对 | `client_submission_id` 唯一索引 | `uk_session_version(session_id, version)` + 乐观锁 `expectedVersion` | 同一 `client_submission_id` 重放 → 返回首次结果；版本落后 → 40917 |
| 结果重投影（预约状态） | 无客户端键（服务端内部调用） | **幂等函数 `applyProjection(sessionId, version)`：按当前 `attendance_item.result` 重算**，与执行次数无关 | 同一版本重复投影结果相同；新版本（提交/修改/覆盖）投影一次即生效；`finalized = 1` 后拒绝任何投影；投影不改 `booked_count`/`active_flag` |
| 店长补签 | `(member_id, 'makeup_signin', idempotencyKey)` | `uk_session(session_id)` | 重复 → 返回首次结果；已有效签到 → 40919 |
| 定时任务 | `uk_job_biz(job_name, biz_key)` | 任务体本身幂等（置状态、写 outbox 有唯一键） | 重复执行只产生一次副作用 |

实现方式：写操作在业务事务内先插入幂等记录（唯一索引冲突即代表重复请求），成功后写入 `response_snapshot`；重复请求直接返回快照。这样即使客户端在弱网下重试，也不会产生两条互相矛盾的记录（故事 3/4/5/8 的验收标准）。

## 5.5 签到幂等与防代签

**幂等**：`coach_signin` 上的唯一索引 `uk_session(session_id)` 让「重复签到」在数据库层面不可能成立。服务端逻辑：命中已有记录 → 返回首次 `signinAt` 与 `idempotent: true`，不更新任何字段（PRD 要求「只保留首次签到时间，后续点击提示已签到（19:02）」）。

**防代签（MVP 的取舍）**：PRD 待解决问题 8 明确 MVP「只记录、不校验地理位置」，因此本方案**不做**地理围栏或蓝牙校验，改用四层可追溯的替代措施：

1. **身份绑定**：签到接口校验当前登录账号的手机号必须匹配 `course_session.coach_staff_id` 对应的 `staff` 记录，否则返回 40301 并写审计（代班教练无法自助代签，与故事 7 的「请联系店长处理」一致）。
2. **时间窗口**：只在「开课前 15 分钟 ～ 开课后 15 分钟」内允许签到，窗口外返回 40911/40912，压缩「提前很久代签」的空间。
3. **可追溯字段**：记录 `client_ip`、`device`、操作人、时间，并写 `audit_log`；店长补签必须填原因并记 `operator_staff_id`，谁代签在数据上可查。
4. **异常信号**：`signin_abnormal`（开课后 15 分钟未签到）会同时通知店长与工程值班，异常集中出现时可被人工发现。

设计上预留了 `geo` 字段（入参接受但 MVP 不校验），将来要接地理围栏时是**可逆变更**：只需在入参校验里增加一步，不需要改数据模型。

## 5.6 订阅消息与通知触达失败补偿

**机制**：一切通知先写 `notify_outbox`（与业务同事务），再异步下发。

| 消息 | 收件人 | 触发点 | 额度不足/未授权时 |
| --- | --- | --- | --- |
| 已自动为你预约（含课程、时间、可取消截止） | 被转正会员 | 转正事务提交后 | 置 `skipped_no_quota`，不重试；会员打开小程序时从状态卡片看到同一结果 |
| 因时间冲突已退出本课候补 | 被预检出冲突的会员 | 分配事务提交后 | 同上，会员端「已退出候补」条目可见 |
| 本课候补已结束（如仍有余位可直接预约） | T-30 时的 `waiting` 会员 | 关队列任务 | 同上 |
| 签到提醒（开课前 20 分钟、5 分钟） | 教练 | 定时任务 | 教练端「我的课程」列表的 `pending` 标记为权威路径 |
| 签到异常（含教练姓名电话） | 店长 | 定时任务 | 店长端异常列表为权威路径 |
| 核对未提交提醒 | 店长 | 结束 + 60 分钟任务 | 同上 |

**补偿与去重**：唯一索引 `uk_event_template(biz_event_id, template_key)` 保证同一业务事件同一模板恰发一次，重放不重复打扰；失败重试 3 次（30 秒 / 2 分钟 / 10 分钟退避），仍失败置 `failed` 并计入告警指标，由工程值班查看，**不回滚任何业务状态**。额度用客户端上报的 `subscribe_quota` 尽力维护（服务端无法可靠预知剩余额度，见第 9 章假设 A3），额度为 0 时直接跳过，不排队等待。

**额度语义（不要按「授权一次 = 能发一条」实现）**：一次性订阅的额度按「用户 × 模板」独立计数；授权必须由用户点击手势触发（`requestSubscribeMessage`，不能静默或自动批量调用）；一次调用最多带 3 个模板 ID、每个模板各自独立授权；用户勾选「总是保持以上选择」后不再弹窗但仍计一次授权，所以**部分用户手上可能同时握着多次额度**——既不能假设只有 1 次，也不能假设一定有。用户点过「拒绝」后再调用不会再弹窗，只能引导去「设置 → 订阅消息」开启，**这条路径基本等于失效，不作为触达失败的补偿手段**。`subscribe_quota.remaining` 只是尽力计数：宁可多发一次（失败即 `skipped_no_quota`/`failed` 并计数）也不要因为本地计数为 0 而漏发；本表不参与任何业务判断。

**红线（MUST）**：业务正确性不依赖消息送达——转正结果、位次、可取消截止时间全部由服务端状态决定并在小程序内实时可见；通知失败不延长任何时限、不保留名额、也不改归属。上线前必须在小程序后台「订阅消息」页确认可用模板并留档（PRD 7.3 要求），本文档不写死模板 ID。

## 5.7 定时任务的边界与时间口径

**所有"到点"逻辑都用「请求内实时判定」而不是「等定时任务」**：预约、取消、加入/退出候补、签到、提交核对都在自己的事务里用服务端当前时间判断窗口，因此任务延迟或漏跑**不会**造成错误分配或越权操作。定时任务只负责三件事：状态收尾、通知、结算。

| 任务 | 触发 | 做的事 | 幂等键 |
| --- | --- | --- | --- |
| `t30_close_queue` | 每分钟扫描 `start_at` 在 `[now, now + 31 分钟)` 且存在 `waiting` 的课次 | 把 `waiting` 置 `closed(t30)` + 发「本课候补已结束」 | `(t30_close_queue, session_id)` |
| `remind_signin_20` / `remind_signin_5` | 每 5 分钟扫描 | 未签到教练提醒（订阅消息，仅增强） | `(job, session_id)` |
| `signin_abnormal` | 开课后 15 分钟 | `signin_status = abnormal` + 通知店长；该课结算时不做 no-show 判定 | `(job, session_id)` |
| `attendance_overdue_60` | 结束 + 60 分钟 | 未提交核对的课：未核对会员置 `unverified`（暂定），写 attendance_item 并**投影**预约状态，通知店长 | `(job, session_id)` |
| `finalize_24h` | 结束 + 24 小时 | 锁定前**再按当前 `result` 投影一次**（覆盖最后一刻的修改），然后 `finalized = 1`，关闭教练与店长入口，「未核实」成为终态 | `(job, session_id)` |
| `project_attendance_results` | 每分钟（**仅补漏**；权威触发点是提交/修改/撤销/覆盖时的同一事务） | **按当前 `attendance_item.result` 幂等重算**该课预约状态（`present → completed`、`absent → absent`、无有效核对 → `unverified`）；签到异常的课不做 no-show 判定，一律 `unverified` | `biz_key = {session_id}:{核对版本号}`——同一课次可随每次提交/修改/覆盖各投影一次。**原 `session_finish_settle` 用 `(job_name, session_id)` 一次性键，会让「覆盖后重投影」永远跑不了，故改为带版本号** |
| `reconcile` | 每 5 分钟 | 校验 I1–I7，异常告警 | `(reconcile, 批次号)` |

**时间口径统一**：所有判定使用服务端 UTC 时间（主机 NTP 同步，偏差监控），业务展示统一按 `Asia/Shanghai` 转换；PRD 7.6 的固定参数（120 分钟、30 分钟、15 分钟宽限、60 分钟、24 小时、20 人上限）集中在领域层常量模块，全系统只有一份。

## 5.8 时间冲突判定与取消截止时间的共用实现

PRD 7.5 明确要求 F3 预约校验与 F6 转正预检**共用同一份实现**，因此：

- `hasOverlap(新起, 新止, 已约起, 已约止) = (新起 < 已约止) && (已约起 < 新止)`，半开区间，首尾相接不算冲突（19:00–20:00 与 20:00–21:00 允许；19:00–20:00 与 19:30–20:30 拒绝）。参与判定的范围**只含 `status = 'booked'` 的正式预约**，候补与已退出的记录都不参与（PRD 7.5）。查询用 `idx_member(member_id, status, session_id)` 按会员取当前有效预约集合，再在内存里用同一函数判定，避免两套 SQL 条件。
- `calcCancelDeadline(reservation, session, now)`：普通预约返回 `start_at − 120 分钟`；`source = auto_promote` 返回 `min(max(start_at − 120 分钟, promoted_at + 15 分钟), start_at − 30 分钟)`。F4 取消校验、会员端展示的「可取消截止时间」、以及转正时的落库值都调用它，保证「展示的截止时间」与「实际判定」永远一致（这正是 PRD v0.5 强调的指标口径问题在实现层的镜像）。
- 兜底测试：把 PRD 7.4 的完整例子（容量 20、队列 A–H、开课前 120/100/40/30 分钟各有人取消）做成表驱动的用例，用**注入时间**的方式跑，断言每一步的转正对象、剩余名额与通知对象都符合 PRD 描述。

## 5.9 弱网与客户端一致性

- 小程序端**不做本地乐观推断**：剩余名额、位次、候补状态、可取消截止时间一律以服务端返回为准（PRD 强调「位次以服务端为准，不允许本地自行计算」）。
- 预约/取消/退出/签到失败时保留同一 `idempotencyKey` 重试；成功后以服务端返回刷新页面，避免「本地显示成功但服务端没有」。
- 核对页断网时在本地暂存标记并明确提示失败 + 重试按钮，重试成功后以最后一次成功提交为准（不产生两条互相矛盾的记录）。
- 列表页与详情页均带 `updatedAt`，客户端发现过期（如 40402/40915）时只刷新、不猜测。

# 6. 风险

| 风险 | 影响 | 可能性 | 应对（技术侧） | 残留风险 / 谁承接 |
| --- | --- | --- | --- | --- |
| 并发抢位导致超卖 | 高（数据不可信，触发回滚） | 低 | 行锁 + 条件更新 + 唯一索引 + `reconcile` 对账告警（5.1） | 无已知残留；上线前必须完成并发压测 |
| 释放后名额出现「悬空」或显示不一致 | 高（课表与事实不符） | 低 | `releaseAndAllocate()` 单入口、单事务、结局枚举穷尽（5.2）；I5/I6 不变量 | 无已知残留 |
| 自动转正把会员绑上一节不想上的课、no-show 上升 | 中高 | 中 | 退出候补入口（转正前可退）+ 宽限取消窗口 + 转正通知带截止时间；`promoted_at` 与 `cancel_deadline_at` 在转正时一次算好，避免展示与判定不一致（5.8） | 残留：T-30 前 1 分钟内被转正者宽限窗口只剩约 1 分钟（PRD 已接受，不扣次不降权） |
| 订阅消息触达失败，会员不知道已被转正 | 中高（体验） | 高 | 权威路径为小程序内状态卡片与开课倒计时；outbox 重试与去重；额度不足直接跳过并计数（5.6） | 残留：完全不打开小程序的会员只能靠到店现场沟通 |
| 教练漏签到 / 漏提交核对 | 中高（数据缺失） | 中 | 两次提醒 + 签到异常通知店长 + 补签后重开核对窗口 + 60 分钟与 24 小时两个截止 + 未核实终态不计入 no-show（5.7） | 残留：店长不处理则「未核实」成为终态 |
| 教练误操作把到场改成缺席（影响会员记录） | 中 | 中 | 默认全到场 + 计数可见 + 10 秒撤销 + 60 分钟内可改 + 店长 24 小时可改 + 全程日志（3.3.1、attendance_log） | 无已知残留 |
| `booked_count` 与预约记录静默不一致（数据腐化） | 高 | 低 | `reconcile` 每 5 分钟校验并告警；回滚条件自动化（3.4）；**口径统一为 I2（`active_flag = 1`），已结束课次不再误报** | 需在预演期确认告警能真正触达值班人，且没有被误报淹没 |
| 核对结果被修改/覆盖后预约状态没有重投影（会员出勤记录与 no-show 永久错） | 中高 | 低 | 3.3.2「预约状态 = `attendance_item.result` 的投影」：提交、修改、撤销、店长覆盖、补签重开、逾期任务六条路径都在同一事务内重投影；投影任务按版本号幂等可重复；`finalized = 1` 之后才锁定 | 无已知残留；测试必须覆盖「提交缺席 → 改到场 → 店长再覆盖为缺席」三步断言 |
| 并发入队导致序号重复或队列突破上限（PRD 故事 5「序号连续且不重复」是 MUST） | 中 | 低 | 入队在同一把课次行锁内「校验上限 + 分配 seq + 插入」，`uk_session_seq(session_id, seq)` 兜底（3.3.3、5.1） | 无已知残留；压测需断言 `count(distinct seq) = 条数`、队列 ≤ min(容量, 20) |
| 核对/兜底窗口内名单读不出来（教练改不了、店长没有接口读名单） | 中高 | 低 | 名单口径 = 该课次全部未取消预约（唯一排除 `cancelled`）；店长专用读接口 4.4.5，教练端 4.3.2 对店长放行，两者共用 `loadRoster()` | 无已知残留；测试需覆盖「20:00 结算后 20:20 打开核对页」与「教练始终未提交、店长 24 小时内兜底」两条路径 |
| 会员卡校验接口不可用，会员被挡在预约外 | 高（会员侧不可用） | 中 | 本地快照（`card_status`/`card_synced_at`）+ 接口失败时用快照并提示，返回 50301；预约前置校验失败不写入脏数据（4.2.2） | 残留：快照过期可能放进已停卡会员（PRD 已接受此代价） |
| 微信接口限流或 access_token 刷新失败 | 中 | 中 | token 进程内缓存 + 提前 5 分钟刷新 + 单飞刷新；失败返回 50302 并重试，不阻塞业务表写入 | 无已知残留 |
| 定时任务漏跑（进程重启、时钟漂移） | 中 | 低 | 正确性不依赖任务（5.7）；任务幂等键 + 启动时补扫窗口；主机 NTP 偏差监控 | 残留：通知可能延迟到下一轮扫描 |
| 课次数据来源不稳定，显示过期课程 | 中 | 中 | 课表只读 + 详情页标注「课程信息以门店现场为准」+ 40402 时提示重新查看 | 上游问题，需 PRD 待解决问题 1 的结论 |
| 单人团队的运维与发布风险 | 中 | 中 | 模块化单体、单一数据库、Docker 镜像化部署（回滚 = 切镜像）、关键指标告警 | 需在预演期演练一次只读模式回滚（30 分钟内） |

# 7. 里程碑

每个里程碑都要求「可运行、可验证」，但不以「全部功能做完」为交付标准，而是以「该里程碑的规则被验证过」为标准。**本阶段只写文档、不写代码，下表所有验证动作都尚未执行**，是否通过需在编码阶段执行后如实记录。

| 里程碑 | 内容 | 如何验证（编码后执行） |
| --- | --- | --- |
| M0 骨架与身份（约 1 周） | 建表（第 3 章全部表与索引）、后端骨架与错误码框架、微信登录 + 手机号绑定 + 角色分流、门店会员卡校验对接 | 接口单测覆盖 40102/40401/40916/40302/50301 分支；真机验证三种角色默认落地页；核对建表 DDL 的索引与唯一约束与本文档一致 |
| M1 课表只读（约 0.5 周） | 课表查询、剩余名额与「我的状态」投影、空状态与已结束态 | 用固定数据集比对 7 天分组、剩余名额、`myStatus`；断言「候补被转正后卡片直接显示已预约」，不出现任何确认类文案 |
| M2 预约与防超卖（约 1.5 周） | 预约事务、幂等、时间冲突判定（半开区间）、会员卡前置校验 | 5.1 的四组并发/幂等测试全部通过；首尾相接用例必须成功、真正重叠用例必须失败 |
| M3 取消与候补自动转正（约 2 周） | 取消事务、`releaseAndAllocate()`、加入/退出候补、冲突预检、T-30 关队列、宽限取消窗口 | 把 PRD 7.4「完整例子」与故事 6「并发释放」做成注入时间的表驱动用例；断言任意时刻 `booked_count ≤ capacity`、无悬空名额、退出与转正的竞态结果符合 5.3；并发入队断言 `count(distinct seq) = count(waiting)` 且队列不超 `min(容量, 20)` |
| M4 签到与核对（约 1.5 周） | 签到窗口与幂等、名单（未取消口径 + 店长读接口 4.4.5）、默认全到场、提交/撤销/修改、60 分钟与 24 小时截止、**结果重投影**与未核实结算、店长补签与覆盖 | 覆盖 40911/40912/40914/40913/40917/40920 分支；断言重复签到返回首次时间；断言「提交缺席 → 改到场 → 店长覆盖为缺席」三步后预约状态与 no-show 口径同步变化；断言结算后名单仍完整可读、`reconcile` 不误报；断言签到异常的课不产生 no-show、未核实可在 24 小时内被覆盖为缺席并计入 no-show |
| M5 店长兜底与通知（约 1 周） | 今日概览、异常列表、订阅消息模板接入与留档、outbox worker 与重试、对账任务与告警 | 模拟订阅消息失败/额度不足：断言业务状态不变、outbox 置 `skipped_no_quota`、小程序内状态卡片仍正确；`reconcile` 人为制造不一致能触发告警 |
| M6 内部预演与灰度准备（约 1 周） | 只读模式开关（回滚用）、日志与指标、部署脚本、双轨核对表格 | 演练一次「30 分钟内部署只读模式」；跑一轮全链路（预约 → 取消 → 自动转正 → 签到 → 核对 → 店长兜底）；逐日记录「候补自动转正率」「宽限期取消率」「自动转正课 no-show 率」用于校准参数 |

# 8. 决策记录（ADR）

## ADR-1：模块化单体 + MySQL，不引入缓存与消息队列

背景：MVP 只服务 1 家门店、容量估算为峰值读 QPS 个位数、单课次写并发 ≤ 30（2.5），团队只有 1 名工程师。备选方案：(a) 微服务拆分（预约服务 / 通知服务 / 签到服务）+ Redis + MQ；(b) 模块化单体 + MySQL 单实例 + 数据库表做 outbox。
决策：选 (b)。所有 P0 逻辑在一个进程、一个数据库里完成，模块边界靠目录与依赖方向约束（2.2）。
影响与代价：放弃「独立扩展通知能力」和「无状态横向扩容」；换来的是部署简单、事务边界清晰（「释放即分配」能在单事务里跑完，这是防悬空名额的关键）、单人可维护。将来真的需要拆分时，边界已经划好（waitlist/reservation/attendance 三个应用服务就是拆分候选），属可逆决策。

## ADR-2：并发防超卖用「行锁 + 条件更新 + 唯一索引」，不用分布式锁与乐观锁重试

背景：PRD 要求「任何情况下报名人数不超过容量」「超卖事故数 = 0」，且失败者要拿到明确提示并可直接加入候补。备选方案：(a) Redis 分布式锁；(b) 乐观锁版本号 + 重试；(c) 数据库行级写锁 + 条件更新。
决策：选 (c)，并把唯一索引作为幂等的最后一道墙，另加 `reconcile` 对账任务。
影响与代价：同一课次的写请求串行化（本场景毫秒级事务，可接受）；热点课次的抢位体验是「先到先得，失败立刻提示加入候补」。放弃的是分布式锁带来的「跨库/跨服务互斥」能力，MVP 不需要。可逆：将来若迁移到多实例且出现跨库写入，再评估把互斥上移到数据库之外的方案。

## ADR-3：名额释放即自动转正、通知走 outbox，业务不依赖订阅消息

背景：CEO 明确要求把「15 分钟限时确认」改为自动转正；同时微信订阅消息在健身房类目下只有一次性订阅、下发不保证到达（PRD 7.3、ADR 背景见 PRD 第 8 章关键决策 1）。备选方案：(a) 先发通知、等会员确认后再落库；(b) 释放即落库转正，通知纯异步。
决策：选 (b)。自动转正与名额释放同一事务完成，通知在提交后由 outbox worker 下发；触达失败不回滚、不改时限。
影响与代价：会员可能在已放弃这节课的情况下被约上（PRD 已确认接受），因此必须配套「转正前可随时退出候补」与「转正后宽限取消窗口」，并用「宽限期取消率」「自动转正课 no-show 率」两个指标监控；若指标超标，PRD 的 P1 已预留「切回限时确认」的可配置项，届时只需新增确认状态与超时任务，不影响已有数据模型。

## ADR-4：幂等统一用「客户端幂等键 + 业务唯一约束」双保险

背景：PRD 在预约、取消、退出候补、签到、提交核对上都要求幂等（重复点击、弱网重试、刷新后重复提交）。备选方案：(a) 只靠业务唯一约束（如状态守卫）；(b) 只靠幂等键表；(c) 两者都用。
决策：选 (c)。幂等键负责「同一次用户操作重试返回同一结果」，唯一约束与状态守卫负责「任何路径都不可能产生重复占位/重复释放」。
影响与代价：多一张 `idempotency_record` 表与一次插入开销（毫秒级），换来的是客户端可以安全重试、服务端不可能因重放破坏不变量。幂等记录保留 7 天（有清理任务），过期后依赖业务唯一约束仍然正确。

## ADR-5：定时任务只做收尾与通知，正确性放在同步请求路径

背景：T-30 关队列、签到提醒与异常判定、60 分钟与 24 小时截止都需要「到点触发」；单实例部署、无调度平台。备选方案：(a) 引入延迟队列中间件；(b) 所有判定放进定时任务；(c) 请求内实时判定 + 幂等定时任务收尾。
决策：选 (c)。窗口与截止判定全部写在业务事务里（因此任务漏跑不会造成错误分配），定时任务只做状态收尾、通知和结算，全部用 `job_run` 幂等键保护。
影响与代价：放弃「精确到秒的定时触发」，通知可能延迟到下一轮扫描（分钟级）；换来的是没有中间件依赖、且正确性不依赖调度系统的可靠性。这符合「不引入不必要依赖」和「优先可逆决策」的原则。

## ADR-6：预约状态改为「核对结果的投影」，结算任务可重复执行

背景：PRD 故事 9 允许教练在「课程结束 + 60 分钟」内修改并覆盖上一次核对结果，故事 10 允许店长在「课程结束 + 24 小时」内把教练已提交的结果或「未核实」覆盖为「到场/缺席」，并写明「被覆盖为缺席的会员此时才计入 no-show」。备选方案：(a) 课程结束时结算一次、把预约状态写成终态（原方案）；(b) 预约状态作为 `attendance_item.result` 的投影，结果每变一次就重投影一次，锁定时刻推迟到「课程结束 + 24 小时」。

决策：选 (b)。`completed`/`absent`/`unverified` 都只是投影值，**唯一不可逆的终态是 `cancelled`**；重投影必须发生在结果变化的**同一事务**内（提交、修改、撤销、店长覆盖、补签重开、逾期任务六条路径），定时任务只做补漏，并用带版本号的幂等键支持重复执行。

影响与代价：`completed` 与 `absent` 之间可以互相覆盖（会员端出勤记录与 no-show 计数随之变化，这是 PRD 要求的），代价是「出勤记录」在 24 小时内不是最终值，会员端与店长端必须以服务端实时值为准。同时 `session_finish_settle` 那种「一课一次」的任务键必须废弃，改为「按当前 `result` 幂等重算 + 版本号键」。可逆：若要回到一次性结算，只需把投影触发点收回结算任务并去掉版本号。

## ADR-7：候补入队与抢位共用同一把课次行锁

背景：PRD 7.1 要求「同一秒内多人加入时，以服务端自增序列号较小者优先」，故事 5 要求「序号连续且不重复」，队列上限 = min(容量, 20)。原方案只对「会改变占用数的操作」取课次行锁，入队是「读计数 + 插入」，并发下两个请求会算出同一个 `seq`、位次分不出先后，还能把 19 人的队列插成 21 人。备选方案：(a) 入队不取锁，靠 `(session_id, seq)` 唯一索引冲突后重试；(b) 入队与抢位共用课次行锁串行化。

决策：选 (b)，并保留 `uk_session_seq(session_id, seq)` 作为数据库层兜底。

影响与代价：同一课次的入队与抢位完全串行（毫秒级事务；按 2.5 估算，单课次峰值约「30 个抢位 + 20 个入队」个请求，可接受）；换来的是 `seq` 一定唯一、位次一定可解释、队列上限不可能被并发突破——这三条都是 PRD 的 MUST，属于「正确性优先于吞吐」。放弃的是「入队不影响抢位吞吐」这个理论收益，在 MVP 规模下没有实际价值。可逆：若将来入队成为热点，可改为「预分配号段 + 唯一索引冲突重试」而不改对外行为。

## ADR-8：用 `active_flag = 1`（未取消）统一「占名额 / 对账 / 名单」三个口径

背景：原口径把「终态」置 `active_flag = NULL`，但对账断言写的是 `booked_count == active 且 status='booked' 的预约条数`，于是每节刚结束的课（状态变成 `completed`/`absent`/`unverified`）都会在 5 分钟后的对账里报「数据不一致」，而它正是 PRD 第 10 章超卖回滚条件的唯一自动监测手段；教练与店长的名单也按 `status='booked'` 过滤，在本该使用名单的窗口里读到空名单。备选方案：(a) 结算时把 `booked_count` 清零；(b) 统一口径为「`active_flag = 1` = 未取消」，`booked_count` 在结算时不变。

决策：选 (b)。`active_flag = 1` 覆盖 `booked`/`completed`/`absent`/`unverified`，只有 `cancelled` 置 NULL；对账断言（I2）与名单查询（4.3.2 / 4.4.5）都用这一个口径。

影响与代价：`active_flag` 的语义从「当前正在报名中」变为「这条预约没有被取消」，必须在文档与代码注释里写清（3.2 已注明）；好处是对账口径在课程前后一致、名单在核对与兜底窗口内始终可读、历史「已约 x/y」也不会被改坏。放弃的是「用 `booked_count` 表示当前在场人数」这一直觉，改为「`booked_count` = 该课次的报名量（未取消数）」。

# 9. 待确认事项与假设

以下为本文档自行做出的假设（未经验证），编码前希望与产品/门店确认；其中 A1–A3 与 PRD 第 9 章的待解决问题重合。

| 编号 | 假设 | 影响 | 若不成立会怎样 |
| --- | --- | --- | --- |
| A1 | 课次数据由门店后台/店长维护后**导入/同步到本系统**（MVP 提供只读展示与同步任务），不要求实时双向 | 决定 `course_session` 是本系统主表还是外部投影 | 若必须实时读门店系统，则课表接口需加超时与降级缓存，且 `booked_count` 仍在本地维护 |
| A2 | 会员卡「是否有效」由门店系统提供（有效期、状态），本地保留快照字段、同步频率每天 1 次 + 预约时实时校验 | 决定 `member.card_status` 是权威值还是快照 | 若门店系统无接口，则改为门店后台人工维护卡状态（P1 项），需产品确认 |
| A3 | 订阅消息额度由客户端上报授权结果来尽力计数（服务端拿不到微信侧的真实剩余额度） | 决定 `subscribe_quota` 的准确性 | 若不允许客户端上报，则额度表删除，改为「一律尝试下发、失败即跳过」，功能不受影响 |
| A4 | 店长若未收到订阅消息，靠打开小程序看到异常列表 + 门店内部沟通（微信群）跟进 | 影响异常处置的及时性 | 若要求强触达，则需引入短信/电话（PRD 明确不做，需重开范围） |
| A5 | 单店部署，`store_id` 只做预留；店长只能看本店数据，通过 `store_id` 匹配校验 | 权限模型 | 若要多店，权限中间件需扩展为「一账号多店」，属 P2 |
| A6 | 会员手机号在门店系统中唯一；同一手机号既是会员又是教练时按 `staff` 白名单优先级判定为教练/店长角色 | 角色分流 | 若存在一号多角色，需增加角色切换入口（当前设计会以白名单角色为准并隐藏会员端功能） |
| A7 | 小程序端需要展示的「剩余名额」不区分「被转正占用」与「原始占用」，两者都算已占用（课表显示 0） | 与 PRD 7.2 一致 | 无需变更，仅在此记录口径 |
| A8 | 通知模板与模板 ID 需上线前在小程序后台确认并留档；本文档不写死模板 ID | 上线前置动作 | 若健身房类目无法申请到所需模板，则通知能力退化为「仅小程序内可达」，业务仍成立 |
| A9 | 名单口径 = 该课次全部未取消的预约（含 `completed`/`absent`/`unverified`），与 PRD 故事 7 原话「全部已预约且未取消的会员」一致；已结束课次展示的「已约 x/y」仍是 `booked_count`（= 未取消预约数），不因结算清零 | 名单、对账、店长兜底三处共用同一口径 | 若产品要求「已结束课次不展示历史名单」，只需前端隐藏，接口与数据模型不变 |
| A10 | 教练在课程结束前提交的核对，其投影立即生效（预约状态先变成 `completed`/`absent`），课程结束后仍可修改；`finalized = 1` 之前一切可覆盖 | 结算与投影的时序（ADR-6） | 若产品不接受「结束前就落出勤结果」，只需把投影触发点推迟到课程结束后，状态机与接口不变 |

另外，本文档与 PRD 的一处口径差异已按新 PRD 处理：原始任务描述里的「候补先到先得 + 15 分钟内确认 + 超时释放」已被 PRD v0.4 废止，本文档按**自动转正（无确认环节）**实现，且不再有「确认超时任务」；相应的兜底能力由「冲突预检 + T-30 截止 + 转正后宽限取消窗口」承担（见 1.3、5.2、ADR-3）。
