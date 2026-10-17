# 技术方案：约课 · 健身房团课预约微信小程序（MVP）

| 项目 | 内容 |
| --- | --- |
| 版本 | v1.1（对应 PRD v1.3、design.md v1.0） |
| 状态 | 待测试复审（第 1 轮审查的 9 项问题已逐条处理，见文末「第 1 轮审查处理记录」） |
| 负责人 | 工程师 |
| 上游输入 | PRD v1.3（docs/PRD.md，唯一事实来源）、docs/design.md v1.0（交互流程与「服务端下发字段」口径） |
| 下游依赖 | 测试计划（docs/test-plan.md） |

**阅读约定**：本方案所有业务规则、状态机、时间口径以 PRD 第 7 节（关键业务规则）、第 10 节（数据口径与状态机）、第 13 节（术语表）为准，方案内不重复推导，只落实现细节。发现与 PRD 冲突时以 PRD 为准并告知产品；本方案补充 PRD 未细化的工程细节（表结构 DDL、接口契约、并发实现顺序、定时任务调度），冲突时以 PRD 第 7 节为最高优先级。**服务端要下发的文案/状态字段以 docs/design.md 第 4.6 节清单为下限**（工程可改名但不可缺字段）。

**v1.1 变更记录（相对 v1.0）**

| # | 变更 | 原因 |
| --- | --- | --- |
| 1 | 6.2 递补循环加「本次释放名额数」上界 + 容量二次校验 | 原伪代码会把整条队列转正 → 超卖（审查 P0-1） |
| 2 | 5.1 新增对象级鉴权规则（会员资源归属、教练场次归属）+ 错误码 10005/10006/10007 | 原方案只有身份校验，越权可取消他人预约（审查 P0-2） |
| 3 | 定名单一幂等表 `idempotency`（4.3 给 DDL），6.4 按接口列清幂等键与重放语义 | 原文档三种互相冲突的说法、且没有对应表（审查 P1-3） |
| 4 | 点名/补录的「取消勾选」回退规则（删 `checkin` 行、回退 `BOOKED`、必要时重判 `NO_SHOW`） | 原「不勾选者保持原状」与 PRD P0-7 矛盾（审查 P1-4） |
| 5 | 判爽约/缺勤任务的扫描区间、终止与幂等条件写明（`start_at+15min ~ start_at+24h`） | 原方案未定义扫描范围，漏跑无补偿（审查 P1-5） |
| 6 | 详情接口对已停课/已结束场次照常返回 200 + 状态字段，10202/10203 只用于写接口 | 原实现做不出 design 5.4 的状态块与历史卡片详情（审查 P1-6） |
| 7 | 补齐 design 4.6 要求的全部 `*_text`/状态字段（课表、详情、我的预约、教练端） | 迟到预约警示、锁定文案、归因句缺失，前端只能用本地时间/自行拼接（审查 P1-7） |
| 8 | 5.9.2 授权时点改为「弹窗 tap 内第一件事同步调用，不 await、不在业务请求之后」 | 原表述允许先发请求再弹授权 → 授权必然失败（审查 P1-8） |
| 9 | 新定义 `position`（实时秩）与 `seq`（绝对序号）的区别 | 两者混用会使 P0-5「B 的位次变为第 1 位」永远不成立（审查 P2-9） |
| 10 | 采纳 9 条 🟡 建议：`upcoming/past` 边界、导入重复手机号去重、`waitlist_count` 只算 `WAITING`、SKIPPED 通知移到事务后、迟到预约无开课提醒（挂待确认）、冻结措辞统一、课程表说明、5.8 映射按 design.md 重写、`days`/排序参数定义 | 审查建议 |

**待确认事项（不影响方案继续，需在开发启动前定）**

| # | 事项 | 来源 | 说明 / 当前假设 |
| --- | --- | --- | --- |
| T1 | ~~交互稿 docs/design.md 尚未产出~~ **已关闭（v1.1）** | design.md v1.0 已产出 | 5.8「页面 ↔ 接口映射」已按 design.md 第 2.1 / 5.x / 7.1 节逐页复核（含登录页、未查询到会员信息页、课表页、详情页、我的预约页、教练工作台、教练签到·点名页、门店信息兜底块、弹窗组）；出参已补 design 4.6 要求的全部 `*_text` 文案字段（5.3/5.4/5.6）；订阅授权时点已与 design 3.1/3.2/4.7 第 5 条统一（5.9.2） |
| T2 | 门店是否已有会员手机号名单 + 教练名单 | PRD Q1 | 当前假设：有，运营按 Excel 导入（P0-S3）；若无手机号数据，登录将降级为「任何微信用户可约、门店现场核验」，需在开发前确认（影响 auth 模块与名单导入） |
| T3 | 「教练上课时签到」的确切含义 | PRD Q4 | 当前假设：按 PRD 已定实现——教练到岗签到 + 会员点名核销，两者同一个页面完成；只做其一将导致爽约规则（P0-8）失去数据来源 |
| T4 | 订阅消息模板个数 | PRD P0-S1 / P0-8 | 预约成功、候补转正、开课提醒、停课通知 4 个必备模板；「限制解除通知」是第 5 个模板（PRD 7.3 要求推送一次解除通知），与微信模板审核周期一起提前申请（R7） |
| T5 | 多门店是否必须支持 | PRD Q6 | 当前假设：单门店；数据模型保留 `store` 表与 `store_id` 外键以便后续扩展，MVP 只有一个门店行 |
| T6 | P0-S2「同场次重复导入 = 更新」是否包含覆盖 `coach_id` | PM 答复（2026 口径） | 影响「临时换人」的操作路径：现在教练端接口限定本人场次（5.1），换人只能靠重导改 `coach_id`。当前假设：**包含覆盖**（否则换人只能由原教练账号操作）。已请 PM 记入 PRD 待解决问题 |
| T7 | 迟到预约（`cancel_deadline` 之后创建）拿不到「开课提醒」（提醒按 `start_at - 120min` 定义，此时该预约还不存在） | PRD P0-S1 口径 | 当前按 PRD 实现（不做补发）；影响：迟到预约者只有「已预约」状态可查。若产品要求覆盖，需要新增「预约成功即发一次含开课时间的确认消息」的模板，属范围变更 |
| T8 | `COACH_ABSENT` 场次是否主动推送会员（design 8.2-A2 未定） | PRD P0-S1 未定义该通知 | 当前按 PRD 实现：**不主动推送**，会员在 `end_at` 后于「我的预约」看到「未开课」；运营确认实际没上成时可走 P0-S4 停课通道补一次推送 |

---

## 1. 需求映射

| PRD 功能（P0） | 实现模块 | 备注 |
| --- | --- | --- |
| P0-1 微信登录 + 身份校验 | `auth`（登录、身份判定、名单校验） | 手机号授权 → 同时查会员名单与教练名单，返回 `is_member` / `is_coach` 四类分支；写操作每次重查名单 |
| P0-2 课表与课程详情 | `schedule`（课表查询、课程详情） | 课表、详情、`cancel_policy_text` 下发；剩余名额/候补人数按占名额口径计算 |
| P0-3 预约 | `booking`（预约）+ `infra`（事务/幂等） | 行锁 + 唯一索引 + `request_id` 幂等，不超卖 |
| P0-4 我的预约 + 取消 | `booking`（查询、取消）+ `waitlist`（递补入口） | 取消按 `cancelable_until` 判定；取消即名额释放并触发递补 |
| P0-5 满员候补排队 | `waitlist`（加入、退出、位次） | FIFO + `seq` 唯一 + 候补上限 = capacity |
| P0-6 候补自动递补转正 | `waitlist`（递补）+ `notification` | 取消/释放同事务内自动转正（无需会员确认，v1.2 定论），推送异步≤10s |
| P0-7 教练到岗签到 + 会员核销 | `checkin`（到岗、点名、补录）+ `noshow`（缺勤判定） | 点名不释放名额；`COACH_ABSENT` 场次整场不判爽约 |
| P0-8 爽约计数与权限限制 | `noshow`（判爽约、计数、限制、定时任务） | 月 3 次 → 限 7 天；计数按自然月 |
| P0-S1 订阅消息通知 | `notification`（模板、发送、重试） | 4~5 个模板；点击动作内同步请求授权、与业务结果解耦 |
| P0-S2 课表数据导入 | `admin`（导入）</br>（`schedule` 复用校验） | Excel 整批校验，任一行非法整批拒绝 |
| P0-S3 名单导入 | `admin`（导入）+ `auth`（供查询） | 会员白名单 + 教练名单，手机号唯一 |
| P0-S4 客服兜底 + 停课通道 | `admin`（停课/恢复/解除限制）+ `audit_log` | 受保护内部入口；停课事务含 `NO_SHOW` 回滚与通知 |
| 全流程共用 | `infra`（事务、时间、错误码、审计、配置、日志） | 公共层，被所有业务模块依赖 |

依赖方向：`web（路由/Controller）→ 业务模块（Service）→ infra（Repository / 事务 / 时间 / 错误码）→ MySQL`。业务规则逻辑只允许出现在业务模块 Service 中，`infra` 不包含任何业务规则。

---

## 2. 架构概览

### 2.1 架构形态

**模块化单体（Modular Monolith），单实例部署**：微信小程序（原生）← HTTPS → 后端服务（Node.js + TypeScript + Express + mysql2）→ MySQL 8；进程内 node-cron 承担定时任务；后端直连微信开放接口（code2session / getPhoneNumber / subscribeMessage.send）。

```
┌──────────────────────────────┐
│  微信小程序（原生）             │
│  · 会员端：登录/课表/详情/      │
│    预约/我的预约/取消/候补      │
│  · 教练端：工作台/到岗/点名/补录 │
│  · 内部运营（受保护入口，      │
│    非小程序，见 2.3）          │
└──────────────┬───────────────┘
               │ HTTPS  /api/v1（JSON）
               ▼
┌──────────────────────────────┐  ┌────────────────────────────┐
│ 后端服务（单实例）              │  │ 微信开放平台（HTTPS 出站）   │
│ ┌──────────────────────────┐ │  │ · code2session 换 openid    │
│ │ Web 层（路由/中间件/鉴权）  │ │  │ · getPhoneNumber 换手机号   │
│ ├──────────────────────────┤ │  │ · subscribeMessage.send     │
│ │ auth │ schedule │ booking│ │  │   （access_token 缓存）     │
│ │ waitlist │ checkin       │ │  └────────────────────────────┘
│ │ noshow │ notification    │ │
│ │ admin（受保护）           │ │
│ ├──────────────────────────┤ │  ┌────────────────────────────┐
│ │ infra：事务/时间/错误码/   │ │  │ node-cron 定时任务（进程内） │
│ │ 审计/配置/结构化日志       │ │  │ · 锁定+候补过期（-30min）   │
│ └──────────┬───────────────┘ │  │ · 开课提醒（-120min）       │
└────────────┼────────────────┘  │ · 缺勤判定+判爽约(+15min)   │
             ▼                    │ · 场次结束(end_at)          │
     ┌───────────────┐            │ · 通知重试（≤3次）          │
     │  MySQL 8       │            └────────────────────────────┘
     │  InnoDB 行锁   │
     │  唯一索引兜底  │
     └───────────────┘
```

关键取舍：**业务状态的一致性全部由「MySQL 事务 + 行锁 + 唯一索引」保证，不放 Redis 锁、不放消息队列**。理由见 ADR-2、ADR-5。定时任务放进程内（单实例），若未来横向扩到多实例再引入分布式调度（ADR-4）。

### 2.2 模块职责边界

| 模块 | 职责 | 关键依赖 |
| --- | --- | --- |
| `auth` | 登录换手机号、查两张名单、签发 JWT、写操作前重查名单（会员/教练身份实时失效） | member_whitelist、coach_roster、member |
| `schedule` | 课表（未来 7 天）、课程详情（含名额/候补/`cancel_policy_text`/`cancelable_until`）、场次状态 | session、booking、waitlist_entry（只读统计） |
| `booking` | 预约（事务内校验+落库）、取消（事务内释放+触发递补）、我的预约 | session（FOR UPDATE）、booking（唯一索引）、member（FOR UPDATE 串行化）、waitlist_entry |
| `waitlist` | 加入候补、退出候补、递补（取消事务内同步执行）、位次查询、`EXPIRED` 批量处理 | waitlist_entry（seq 唯一）、booking、member |
| `checkin` | 教练到岗、点名名单读取/提交（覆盖式）、24h 补录 | coach_roster、coach_checkin、checkin、booking |
| `noshow` | 判爽约（先判缺勤）、计数、限制触发/到期、缺勤标记 `COACH_ABSENT` | booking、member、coach_checkin、checkin |
| `notification` | 订阅消息模板管理、发送、失败重试（≤3）、发送日志 | notification_log |
| `admin` | Excel 导入（课表/名单）、停课/恢复、人工解除限制；全部写 audit_log | 全部表 |
| `infra` | 事务模板、Asia/Shanghai 时钟、统一错误码、请求级日志（request_id）、幂等记录（idempotency 表）、对象级鉴权工具、审计、配置常量、参数化查询 | MySQL |

### 2.3 内部运营入口（MVP 最小受保护入口，非小程序）

PRD 交付了 3 个内部工具类 P0（P0-S2/S3/S4），MVP 不做运营后台（P1-1），工程提供**最小受保护入口**：

- 形态：与管理端共用同一后端进程的一组 `/api/v1/admin/*` 接口，**不归入小程序**，通过独立的内部账号体系登录（用户名+密码，仅配置 2 名内部运营账号）签发短期 admin token，并叠加请求头校验；生产部署时建议再加来源 IP 白名单（见 6.6 安全）。
- 能力边界：仅「课表导入、名单导入、停课、恢复、人工解除限制」，与 PRD 7.5 身份边界一致（教练身份不解锁任何运营能力）。
- 每次操作必须写入 `audit_log`（操作人、时间、目标、变更前后、原因）。

### 2.4 规模与性能估算（假设，待首月校准）

| 项 | 假设值 | 依据 |
| --- | --- | --- |
| 会员规模 | 单店 ≤ 2000 人（阶段 0 种子 10~20 人 → 阶段 1 全量） | PRD 12.1 分阶段上线 |
| 日活 / 高峰 QPS | 日活 ≤ 500；课表浏览峰值（早 8-9 点/晚 6-8 点）约 10~30 QPS；抢课突发峰值 ≤ 50 QPS | 单门店团课运营常识量级 |
| 每日预约类写操作 | ≤ 1000 次（预约+取消+候补+签到） | 每场 10~30 人、每天 ≤ 60 场 |
| 单场次并发预约 | 50~200 并发（压测要求，PRD 7.4 第 9 条） | PRD 压测用例 |
| 课表数据量 | 未来 7 天 ≤ 60 场、`booking` ≤ 每场 30 条 | 单门店排课 |

结论：MySQL 单实例 + 后端单实例足以支撑；单场次预约靠行锁串行化，200 并发下每笔 10~30ms，整体排空约 2~6 秒，配合前端 loading 与幂等可接受。**不引入 Redis 缓存/分布式锁**（课表查库走索引 <10ms；热点名额写路径本身被行锁串行化，加缓存反而引入一致性问题）。性能压测仍按 PRD 7.4 第 9 条执行（见里程碑 M2）。

---

## 3. 技术选型

| 选项 | 选择 | 理由 | 放弃的备选 |
| --- | --- | --- | --- |
| 小程序端 | 微信原生小程序（WXML/WXSS/JS） | 只做微信单端（PRD 3「不跨平台」）；无编译链，`wx.login`/`getPhoneNumber`/`requestSubscribeMessage` 直接可用；原生 API 与微信审核流程兼容性最好 | Taro / uni-app：跨端编译链与运行时抽象，MVP 单端用不上，引入构建与升级成本 |
| 后端语言/运行时 | Node.js 20 LTS + TypeScript | 团队唯一工程师熟悉；微信生态 Node SDK 成熟；TS 提供类型约束，降低状态机/接口契约出错率 | Go / Java：生态与效率对本项目无显著收益，团队维护成本高 |
| Web 框架 | Express 5 + 中间件 | 成熟、依赖少、路由/鉴权/错误处理可控；本方案用分层（route→service→repo）保证模块边界，不需要框架级 DI | NestJS：D 层/装饰器样板多，MVP 单人维护收益不明显（ADR-3） |
| 数据库访问 | mysql2 + 手写 SQL | 预约事务需要 `SELECT ... FOR UPDATE`、部分唯一索引、跨表事务，原生 SQL 最直接可控；避免 ORM 对「生成列唯一索引」「行锁」的抽象损耗 | TypeORM / Prisma：partial index、FOR UPDATE、事务内手写 SQL 支持繁琐，引入额外抽象 |
| 数据库 | MySQL 8.0（InnoDB） | 行锁 + 事务 + 唯一索引是名额一致性的基础；生成列可实现「部分唯一索引」（见 4.2）；云 RDS 自带备份 | PostgreSQL：同样可行但团队 MySQL 更熟；SQLite：不支持并发写行锁语义，排除 |
| 定时任务 | node-cron（进程内，每分钟） | MVP 所有定时任务都是「分钟级扫描」语义，单实例下够用；无额外中间件 | Redis 延迟队列 / BullMQ：需要引入 Redis 与运维，量级下收益为零（ADR-4） |
| 鉴权 | JWT（HS256，有效期 ≥30 天）+ 写操作前重查名单 | 免会话存储；「名单移除立即失效」靠每次写操作重查实现（PRD 7.5 要求） | Session 存储：需要 Redis/DB 会话表，JWT + 重查更简单（ADR-6） |
| 通知发送 | 事务提交后进程内同步调用 + `notification_log` 重试（≤3 次） | 日发送量几百条；「10 秒内推送」靠立即调用保证；失败重试由定时任务兜底 | 消息队列：量级与实时性要求都到不了 MQ 的必要性（ADR-5） |
| Excel 导入 | `xlsx`（SheetJS）解析 + 整批校验 | 成熟库，只读解析即可 | 自研 CSV 解析：不支持门店现有 Excel 模板 |
| 秘密管理 | 环境变量（`.env` 不入库）+ 部署平台密钥 | 微信 AppSecret、JWT secret、MySQL 口令全部走环境变量，代码不出现任何密钥 | 硬编码：禁止 |

所有外部输入（手机号、Excel、接口参数）在**边界校验**；所有 SQL 用参数化查询（mysql2 `execute`）；响应中的手机号一律脱敏（`138****0001`）。

---

## 4. 数据模型

### 4.1 表清单与设计口径

- 时区：所有时间字段存 **Asia/Shanghai 本地时间**（`DATETIME(3)`），应用统一用 `Asia/Shanghai`；场次自然日按门店时区 = 上海时区（MVP 单门店）。**响应一律附 `server_time`**（PRD 7.4 第 10 条、7.6）。
- 主键：`BIGINT AUTO_INCREMENT`（MVP 量级足够，避免 UUID 影响索引与序列问题）。
- 通用审计字段：`created_at` / `updated_at`（由应用写入，不用 DB 时间以保证时区可控）。
- 「部分唯一索引」实现：MySQL 无真正 partial index，统一用**生成列（VIRTUAL）+ 唯一索引**实现，见 4.2。
- **课程信息内联在 `session`，不单建 `course` 表**（与 PRD 10.1 的字段清单一致）：MVP 的排课就是「一行 = 一场次」，课程名/简介/难度/教练都是场次属性，单建课程库只会引入「课程表 ↔ 排课表」同步问题。若未来要做课程库、多场次复用、课程图片，再抽 `course` 表（可逆决策：按 `course_name` 归并即可）。
- 写接口幂等统一落一张 `idempotency` 表（见 4.3），不再为每个接口各设计一套幂等列。
- 命名：表名复数下划线，字段 snake_case。

### 4.2 核心指令：部分唯一索引（防重复占位最后一道闸）

```sql
-- booking：同一会员同一场次最多一条「占名额预约」（BOOKED 或 ATTENDED）
ALTER TABLE booking
  ADD COLUMN active_key TINYINT AS (
    CASE WHEN status IN ('BOOKED','ATTENDED') THEN 1 ELSE NULL END
  ) VIRTUAL,
  ADD UNIQUE KEY uk_booking_member_session_active (member_id, session_id, active_key);

-- waitlist_entry：同一会员同一场次最多一条「候补中」
ALTER TABLE waitlist_entry
  ADD COLUMN waiting_key TINYINT AS (
    CASE WHEN status = 'WAITING' THEN 1 ELSE NULL END
  ) VIRTUAL,
  ADD UNIQUE KEY uk_waitlist_member_session_waiting (member_id, session_id, waiting_key);
```

### 4.3 表结构（DDL 摘要，建表与迁移以本文件为准）

**store（门店）**
```sql
CREATE TABLE store (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(64)  NOT NULL,
  phone       VARCHAR(32)  NOT NULL,          -- 展示给小程序的兜底电话（可拨号）
  address     VARCHAR(128) NOT NULL,
  timezone    VARCHAR(32)  NOT NULL DEFAULT 'Asia/Shanghai',
  status      TINYINT      NOT NULL DEFAULT 1 COMMENT '1=启用',
  created_at  DATETIME(3)  NOT NULL,
  updated_at  DATETIME(3)  NOT NULL
) ENGINE=InnoDB COMMENT='门店（MVP 单行）';
```

**member（会员账号，登录态与爽约计数的事实源）**
```sql
CREATE TABLE member (
  id                         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  phone                      VARCHAR(32)  NOT NULL UNIQUE COMMENT '脱敏展示由应用层做',
  name                       VARCHAR(64)  NULL,
  nickname                   VARCHAR(64)  NULL,
  openid                     VARCHAR(64)  NULL COMMENT 'wx.login 换取，可选存储',
  store_id                   BIGINT UNSIGNED NOT NULL,
  status                     VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE|FROZEN',
  no_show_count_current_month INT UNSIGNED NOT NULL DEFAULT 0,
  count_month                CHAR(7)      NULL COMMENT 'YYYY-MM，爽约计数所属自然月，与 no_show_count_current_month 配套',
  restricted_until           DATETIME(3)  NULL COMMENT '>=now 表示预约/候补受限',
  created_at                 DATETIME(3)  NOT NULL,
  updated_at                 DATETIME(3)  NOT NULL,
  KEY idx_member_store (store_id)
) ENGINE=InnoDB COMMENT='会员账号';
```
> 爽约计数按月归零：每次判爽约/补录/回滚时若 `count_month != 当前月` 则清零重记（PRD 7.3 计数口径，不做滚动窗口；定时任务每月 1 日 00:00 兜底清一次）。

**member_whitelist（会员名单，P0-S3 导入）**
```sql
CREATE TABLE member_whitelist (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  phone           VARCHAR(32)  NOT NULL,
  name            VARCHAR(64)  NOT NULL,
  store_id        BIGINT UNSIGNED NOT NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE|REMOVED',
  imported_at     DATETIME(3)  NOT NULL,
  import_batch_id VARCHAR(64)  NULL,
  UNIQUE KEY uk_mw_phone (phone, store_id)
) ENGINE=InnoDB COMMENT='会员白名单（导入），P0-1 判定依据';
```
> 会员被移除名单：`status = REMOVED`（保留行以便历史追溯），已存在的未来预约保留，只禁新建预约（PRD P0-S3）。

**coach_roster（教练名单，P0-S3 导入；登录与排课绑定的唯一依据）**
```sql
CREATE TABLE coach_roster (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  phone           VARCHAR(32)  NOT NULL,
  name            VARCHAR(64)  NOT NULL,
  store_id        BIGINT UNSIGNED NOT NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE|INACTIVE',
  imported_at     DATETIME(3)  NOT NULL,
  import_batch_id VARCHAR(64)  NULL,
  UNIQUE KEY uk_cr_phone (phone)
) ENGINE=InnoDB COMMENT='教练名单（导入），P0-1/P0-7/P0-S2 校验依据';
```
> 教练功能直接以 `coach_roster` 为主表（不另建 coach 表）；`session.coach_id`、`coach_checkin.coach_id` 均外键到它。`status = INACTIVE`（离职）后：登录不出现工作台入口，历史场次教练记录保留可查。

**session（场次/排期）**
```sql
CREATE TABLE session (
  id                    BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  store_id              BIGINT UNSIGNED NOT NULL,
  course_name           VARCHAR(64)  NOT NULL,
  course_desc           VARCHAR(512) NULL,
  difficulty            VARCHAR(16)  NULL COMMENT '低/中/高',
  coach_id              BIGINT UNSIGNED NOT NULL,       -- -> coach_roster.id
  room                  VARCHAR(64)  NOT NULL,
  start_at              DATETIME(3)  NOT NULL,
  end_at                DATETIME(3)  NOT NULL,          -- 必填（区间重叠判定需要 end_at）
  capacity              INT UNSIGNED NOT NULL COMMENT '占名额预约数上限，唯一权威值',
  cancel_deadline_minutes INT NOT NULL DEFAULT 120 COMMENT 'MVP 全门店固定 120，不做配置',
  status                VARCHAR(16)  NOT NULL DEFAULT 'SCHEDULED' COMMENT 'SCHEDULED|CANCELLED|FINISHED',
  cancel_reason         VARCHAR(32)  NULL COMMENT '停课/未开课归因枚举：STORE_CLOSED|COACH_ABSENT（与 design 4.6/7.2 的文案结构一一对应）',
  coach_absent          TINYINT      NOT NULL DEFAULT 0 COMMENT 'COACH_ABSENT 标记位，不改 status',
  created_by_import     TINYINT      NOT NULL DEFAULT 0,
  import_batch_id       VARCHAR(64)  NULL,
  created_at            DATETIME(3)  NOT NULL,
  updated_at            DATETIME(3)  NOT NULL,
  KEY idx_session_start (store_id, start_at),
  KEY idx_session_coach (coach_id, start_at),
  CHECK (end_at > start_at)
) ENGINE=InnoDB COMMENT='场次（排期）';
```
> `status` 不含 `COACH_ABSENT`（PRD 10.1 明确为标记位，另用 `coach_absent` 列）。

**booking（预约）— 核心表**
```sql
CREATE TABLE booking (
  id                   BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  session_id           BIGINT UNSIGNED NOT NULL,
  member_id            BIGINT UNSIGNED NOT NULL,
  status               VARCHAR(32)  NOT NULL COMMENT 'BOOKED|CANCELLED_BY_MEMBER|CANCELLED_BY_STORE|ATTENDED|NO_SHOW',
  source               VARCHAR(32)  NOT NULL COMMENT 'BOOK|WAITLIST_PROMOTED',
  request_id           CHAR(36)     NOT NULL COMMENT '幂等键（首次请求生成）',
  promoted_at          DATETIME(3)  NULL COMMENT 'source=WAITLIST_PROMOTED 时=转正时刻，且与 created_at 同值',
  grace_cancel_eligible TINYINT     NOT NULL DEFAULT 0 COMMENT 'created_at >= cancel_deadline - 30min',
  grace_cancel_used    TINYINT      NOT NULL DEFAULT 0 COMMENT '该会员该场次是否已用掉宽限取消',
  cancelable_until     DATETIME(3)  NOT NULL COMMENT '唯一取消判定口径，见 7.2 公式；创建/转正时计算落库',
  cancel_reason        VARCHAR(32)  NULL COMMENT 'CANCELLED_BY_STORE 时的归因枚举：STORE_CLOSED|COACH_ABSENT（复制自 session.cancel_reason；自由文本原因只进 audit_log）',
  cancelled_at         DATETIME(3)  NULL,
  cancelled_by         VARCHAR(64)  NULL COMMENT 'member_id / admin 账号',
  created_at           DATETIME(3)  NOT NULL,
  updated_at           DATETIME(3)  NOT NULL,
  UNIQUE KEY uk_booking_request (request_id),
  KEY idx_booking_session_status (session_id, status),   -- 容量统计/点名名单
  KEY idx_booking_member (member_id, status),
  CONSTRAINT fk_booking_session FOREIGN KEY (session_id) REFERENCES session(id)
) ENGINE=InnoDB COMMENT='预约（含候补转正）';
-- 部分唯一索引：见 4.2
```
> `cancelable_until` 计算（PRD 7.2，唯一口径）：`cancelable_until = min( max(cancel_deadline, created_at + 30min), start_at )`；候补转正的 `created_at = promoted_at`。创建预约（含迟到预约/转正）时写入该行；「宽限取消每场次每人 1 次」由 `grace_cancel_used`（会员+场次维度，取消时若命中宽限置 1，同一会员再创建新预约时 `cancelable_until` 直接取 `cancel_deadline` 且不满足宽限）。

**waitlist_entry（候补队列）**
```sql
CREATE TABLE waitlist_entry (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  session_id  BIGINT UNSIGNED NOT NULL,
  member_id   BIGINT UNSIGNED NOT NULL,
  seq         INT UNSIGNED NOT NULL COMMENT '场次内从 1 递增，FIFO 的唯一依据，进入即占用、只增不减',
  status      VARCHAR(16) NOT NULL COMMENT 'WAITING|PROMOTED|CANCELLED|EXPIRED|SKIPPED',
  joined_at   DATETIME(3) NOT NULL,
  promoted_at DATETIME(3) NULL,
  skip_reason VARCHAR(64) NULL,        -- SKIPPED 原因：时段冲突/限制期/名单失效
  created_at  DATETIME(3) NOT NULL,
  updated_at  DATETIME(3) NOT NULL,
  UNIQUE KEY uk_wl_session_seq (session_id, seq),
  KEY idx_wl_session_status_seq (session_id, status, seq),  -- 取队首/排位统计
  CONSTRAINT fk_wl_session FOREIGN KEY (session_id) REFERENCES session(id)
) ENGINE=InnoDB COMMENT='候补队列';
-- 部分唯一索引：见 4.2
```

**checkin（会员核销记录）与 coach_checkin（教练到岗）**
```sql
CREATE TABLE checkin (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  session_id  BIGINT UNSIGNED NOT NULL,
  member_id   BIGINT UNSIGNED NOT NULL,
  type        VARCHAR(16) NOT NULL DEFAULT 'MEMBER_ATTEND',
  source      VARCHAR(16) NOT NULL COMMENT 'COACH_SELECT|COACH_RETRO',
  by_coach_id BIGINT UNSIGNED NOT NULL,
  checked_at  DATETIME(3) NOT NULL,
  UNIQUE KEY uk_checkin_session_member (session_id, member_id),
  KEY idx_checkin_session (session_id)
) ENGINE=InnoDB COMMENT='会员到场核销';

CREATE TABLE coach_checkin (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  session_id    BIGINT UNSIGNED NOT NULL,
  coach_id      BIGINT UNSIGNED NOT NULL,
  checked_in_at DATETIME(3) NOT NULL,
  UNIQUE KEY uk_coach_checkin_session (session_id)
) ENGINE=InnoDB COMMENT='教练到岗签到（一场一次）';
```
> 「有核销记录」定义为 checkin 表存在 (session_id, member_id) 行；点名提交（覆盖式幂等）与补录共用此表，补录时 `source = COACH_RETRO`。

**notification_log（订阅消息发送与重试）**
```sql
CREATE TABLE notification_log (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  member_id   BIGINT UNSIGNED NOT NULL,
  template_id VARCHAR(64)  NOT NULL COMMENT '微信模板 ID 或内部模板名',
  scene       VARCHAR(32)  NOT NULL COMMENT 'BOOK_OK|WAITLIST_PROMOTED|REMINDER|SESSION_CANCELLED|RESTRICT_RELEASED',
  ref_type    VARCHAR(32)  NULL COMMENT 'booking/waitlist/session/member',
  ref_id      BIGINT UNSIGNED NULL,
  payload     JSON         NULL,
  status      VARCHAR(16)  NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING|SENT|FAILED_RETRY|FAILED_GIVEUP|NO_AUTH',
  retry_count TINYINT      NOT NULL DEFAULT 0,
  error       VARCHAR(255) NULL,
  created_at  DATETIME(3)  NOT NULL,
  updated_at  DATETIME(3)  NOT NULL,
  KEY idx_nl_status (status, created_at)   -- 重试任务扫描
) ENGINE=InnoDB COMMENT='订阅消息发送记录/重试';
```

**import_batch（导入批次）与 audit_log（人工操作日志）**
```sql
CREATE TABLE import_batch (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  kind          VARCHAR(16) NOT NULL COMMENT 'SESSION|MEMBER|COACH',
  operator      VARCHAR(64) NOT NULL,
  file_name     VARCHAR(255) NULL,
  row_count     INT UNSIGNED NOT NULL DEFAULT 0,
  imported_count INT UNSIGNED NOT NULL DEFAULT 0,
  result        JSON NULL COMMENT '成功/失败明细（失败含行号与原因）',
  created_at    DATETIME(3) NOT NULL
) ENGINE=InnoDB COMMENT='导入批次';

CREATE TABLE audit_log (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  operator    VARCHAR(64)  NOT NULL COMMENT 'admin 账号 / system',
  action      VARCHAR(32)  NOT NULL COMMENT 'SESSION_CANCEL|SESSION_RESTORE|UNRESTRICT|IMPORT|MANUAL_FIX',
  target_type VARCHAR(32)  NOT NULL,
  target_id   BIGINT UNSIGNED NOT NULL,
  before      JSON NULL,
  after       JSON NULL,
  reason      VARCHAR(255) NULL,
  created_at  DATETIME(3)  NOT NULL,
  KEY idx_audit_target (target_type, target_id)
) ENGINE=InnoDB COMMENT='人工操作日志（谁、何时、为什么）';
```

**idempotency（写接口幂等记录，唯一方案）**
```sql
CREATE TABLE idempotency (
  id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  request_id   CHAR(36)     NOT NULL COMMENT '客户端生成的 UUID',
  actor_type   VARCHAR(16)  NOT NULL COMMENT 'MEMBER|COACH|ADMIN',
  actor_id     BIGINT UNSIGNED NULL COMMENT 'member_id / coach_id',
  actor_name   VARCHAR(64)  NULL COMMENT 'admin 账号名（审计用）',
  action       VARCHAR(32)  NOT NULL COMMENT 'BOOK_CREATE|BOOK_CANCEL|WAITLIST_JOIN|WAITLIST_LEAVE|COACH_CHECKIN|ATTENDANCE_SUBMIT|ATTENDANCE_RETRO|SESSION_CANCEL|SESSION_RESTORE|UNRESTRICT|IMPORT',
  request_hash CHAR(64)     NOT NULL COMMENT 'SHA-256(规范化后的关键参数)，识别同 request_id 换参数',
  target_type  VARCHAR(32)  NULL COMMENT 'booking|waitlist|session|member',
  target_id    BIGINT UNSIGNED NULL,
  result_code  INT          NOT NULL COMMENT '首次请求返回的业务 code（0 或业务拒绝码）',
  result_data  JSON         NULL COMMENT '首次响应的 data 快照，用于重放',
  created_at   DATETIME(3)  NOT NULL,
  UNIQUE KEY uk_idem_request (request_id),
  KEY idx_idem_created (created_at)   -- 每日清理 >30 天的行
) ENGINE=InnoDB COMMENT='写接口幂等记录（与业务同事务写入）';
```
> 使用规则（与 6.4 一致）：① 与业务**同事务**写入——事务开始即 `INSERT idempotency`，靠 `uk_idem_request` 把「同 `request_id` 的并发重复请求」串行化；命中唯一键冲突（`ER_DUP_ENTRY`）→ 回滚后在新事务读回既有行：`result_code/result_data` 已就绪则原样重放（`idempotent_replay=true`），`request_hash` 不一致则 10006，最多重试 3 次（每次 50ms）仍读不到结果则返回 10007「请求处理中，请重试」；② 业务拒绝（如 10301 已满）也写入首次结果并**提交**（不写业务数据），保证重放返回同一拒绝；系统错误（5xx/50000）整体回滚、**不写**幂等行，允许客户端用同 `request_id` 重试；③ `booking.request_id` 唯一索引保留，作为预约防重与防超卖的最后一道闸（PRD 10.1）。

### 4.4 状态机（以 PRD 10.2 为准，此处列工程落库要点）

```
booking:（创建）→ BOOKED
  BOOKED → CANCELLED_BY_MEMBER    (自助取消，按 cancelable_until)
  BOOKED → CANCELLED_BY_STORE     (停课；或 COACH_ABSENT 场次在 end_at 被场次结束任务关闭)
  BOOKED → ATTENDED               (教练核销 / 补录)
  ATTENDED → BOOKED               (教练取消勾选后覆盖式提交的回退，见 5.6；同时删除 checkin 行)
  BOOKED → NO_SHOW                (start_at+15min 判爽约；COACH_ABSENT 场次整场跳过)
  NO_SHOW → ATTENDED              (教练 24h 内补录，计数 -1，不低于 0)
  NO_SHOW → CANCELLED_BY_STORE    (start_at 之后执行的停课，回滚爽约计数，见 7.2)
  CANCELLED_BY_STORE → ATTENDED   (仅 COACH_ABSENT 场次 24h 内补录)

waitlist_entry:（加入）→ WAITING
  WAITING → PROMOTED    (递补成功，同事务生成 BOOKED，booking.created_at=promoted_at)
  WAITING → CANCELLED   (会员主动退出)
  WAITING → EXPIRED     (锁定点未转正 / 递补事务开始时已过锁定点)
  WAITING → SKIPPED     (队首但校验不通过，通知会员，取下一人)

session: SCHEDULED → CANCELLED（停课）｜ SCHEDULED → FINISHED（end_at，只读）
         coach_absent 标记位独立于 status；「FINISHED 后不可写」只约束**场次维度**（不再新增预约/候补、
         不再提交点名窗口内的到岗与点名）；`booking` 在 `end_at` 之后仍可被两个受控路径写：
         ① 判爽约任务（扫描区间到 `start_at + 24h`）；② 教练 24h 内的补录 / 取消勾选（见 5.6、6.3）
```
实现要点：
- 同一行 `booking` 的状态流转（如 `CANCELLED_BY_MEMBER` 是旧行变状态，不是新行）；会员取消后再约 → 新 INSERT 一行 `BOOKED`（旧行保持历史），部分唯一索引只约束占名额行，故允许「同场次多行历史」。
- 判爽约仅对 `created_at < session.start_at` 且仍 `BOOKED` 且无 checkin 记录的行；`COACH_ABSENT` 场次整场跳过。
- 全部状态跳转集中在各业务 Service，禁止 `UPDATE ... SET status` 散落。路由层禁止写库。

---

## 5. 接口设计

### 5.1 统一约定

- Base URL：`/api/v1`；全部 JSON；HTTPS。
- 鉴权（身份层）：小程序登录后所有请求带 `Authorization: Bearer <JWT>`；admin 接口带 `X-Admin-Token`。
- **鉴权（对象层，越权防护，必须实现）**：身份校验只回答「你是会员/教练」，不回答「这条资源是你的」。所有针对具体资源的接口在改状态前必须再校验归属，否则返回 10005，且**不写任何记录、不释放名额、不触发递补**：
  - `POST /bookings/:id/cancel`：`booking.member_id == token.member_id`；
  - 教练端 `POST /coach/sessions/:id/checkin`、`GET|PUT /coach/sessions/:id/attendance`、`PUT .../attendance/retro`：`session.coach_id == token.coach_id`（PM 已定口径：**限定本人场次**，不放开代课；理由是 `COACH_ABSENT` 判定、课时统计与 `checkin.by_coach_id` 都依赖「谁上这节课」，换人属于排课变更=运营权限）；`GET /coach/sessions` 也必须按 `coach_id` 过滤，只返回本人场次；
  - admin 接口：`X-Admin-Token` 有效即视为有权限（MVP 仅 2 名内部运营账号，不做细粒度角色）；
  - 资源不存在（含 ID 属于他人、不可见的场景）与无权操作统一返回 10005，不泄露资源是否存在；由此保证不变量：`checkin.by_coach_id` 恒等于 `session.coach_id`。
- 幂等：所有**写接口**（预约/取消/候补加入/退出候补/教练到岗/点名/补录/停课/恢复/解除限制/导入）客户端生成 `request_id`（UUID）随 body/query 提交；服务端**统一落一张 `idempotency` 表**（DDL 见 4.3，`request_id` 唯一）并保留 `booking.request_id` 唯一索引作为预约的最后一道闸。同一 `request_id` 重复请求返回首次结果（`idempotent_replay=true`，code 同首次）；同 `request_id` 但参数摘要（`request_hash`）不一致 → 10006（不重放、不执行）。详见 6.4。
- 响应包统一：
```json
{ "code": 0, "message": "ok", "data": { ... }, "request_id": "...", "server_time": "2026-01-01T12:00:00+08:00" }
```
  `server_time` 服务端当前时间（Asia/Shanghai），前端所有判期以它为基准（PRD 7.2、7.6）——**客户端不使用本地时间做任何业务判定**。
- 时间格式：`YYYY-MM-DDTHH:mm:ss+08:00`；日期 `YYYY-MM-DD`。
- 文案口径：取消相关文案只返回服务端生成的 `cancel_policy_text` / `cancelable_until`，前端**不得**拼接小时数（PRD 7.2 硬性）。
- 脱敏：任何返回给客户端的手机号按 `138****0001` 脱敏。
- 错误码（业务拒绝统一为 HTTP 200 + 非 0 code，HTTP 层只用于传输/网关错误；5xx 表示系统错误）：

| code | 含义 | 典型场景 |
| --- | --- | --- |
| 0 | 成功 | — |
| 10001 | 未登录 / 登录态失效 | token 缺失/过期 |
| 10002 | 无权限 | 访问教练接口而非教练 |
| 10003 | 会员身份失效 | 白名单被移除后写预约/候补 |
| 10004 | 教练身份失效 | 教练被移出名单后写点名 |
| 10005 | 无权操作该资源 | 对象级鉴权失败：A 取消 B 的预约；教练操作他人场次（文案：这不是你的课程，如需代课请联系门店） |
| 10006 | request_id 复用但参数不一致 | 同一 request_id 换了 session_id/booking_id 等 |
| 10007 | 请求处理中 | 同 request_id 的并发重复请求尚未提交完成（可安全重试） |
| 10101 | 手机号获取失败 | getPhoneNumber 换号失败 |
| 10102 | 非会员非教练 | 登录返回「未查到会员信息」分支 |
| 10201 | 场次不存在 | session_id 无效 |
| 10202 | 场次已停课 | **仅用于写接口**（预约/候补）的拒绝；`GET /sessions/:id` 对停课场次照常返回 200 + `session_status=CANCELLED` |
| 10203 | 场次已结束 | **仅用于写接口**的拒绝；详情接口照常返回 200 + `session_status=FINISHED` |
| 10204 | 场次已锁定 | 已到 `start_at - 30min`，拒绝新预约/新候补（含重放旧请求） |
| 10205 | 已开课 | 拒绝预约（第二道防线） |
| 10206 | 查询参数非法 | `date`/`days`/`tab` 越界（仅今天 ~ 未来 6 天，`days` ∈ 1..7） |
| 10301 | 场次已满 | 引导加入候补（正常业务分支） |
| 10302 | 已预约该课程 | 同一场次已占名额 |
| 10303 | 时段冲突 | 与既有有效预约时间区间重叠 |
| 10304 | 限制期 | `restricted_until` 在未来 |
| 10401 | 候补已满 | 候补人数已达 capacity |
| 10402 | 已在候补 | 重复加入 |
| 10403 | 已预约不可候补 | 互斥 |
| 10404 | 场次未满不可候补 | 还有名额时应走预约 |
| 10405 | 候补记录不存在/已转正 | 退出候补时 |
| 10501 | 已过可取消截止 | `now >= cancelable_until` |
| 10502 | 订单状态不可取消 | 已取消/已到场/已停课/爽约 |
| 10503 | 宽限取消已用 | 同场次已用过一次宽限取消 |
| 10601 | 不在点名窗口 | 窗口外提交点名/到岗 |
| 10602 | 名单已冻结 | 锁定点后提交点名（补录除外） |
| 10701 | 导入整批拒绝 | data 内含行号错误明细 |
| 10702 | 无管理权限 | admin token 无效 |
| 50000 | 系统错误 | 未捕获异常；前端统一错误态+重试 |

幂等重放：同一 `request_id` 的重复请求返回**首次结果**，`data.idempotent_replay = true`，code 仍为 0（PRD 7.4 第 6 条、7.2 取消幂等）。

### 5.2 登录与身份（P0-1）

**`POST /auth/session`**（静默登录，可选）
入参：`{ code: wx.login 的 code }`
出参：`{ openid 可用标记, token_placeholder }`；失败码 10001。
> MVP 以手机号为准，openid 仅作稳定识别备用；如不做，可略过该接口。

**`POST /auth/login`**（手机号一键登录，幂等）
入参：`{ phone_code: getPhoneNumber 返回的 code, phone_iv?, phone_encrypted?, request_id }`
处理（服务端唯一权威，PRD 7.5）：
1. 用 `phone_code` 调微信 `getPhoneNumber` 换明文手机号（失败码 10101）；
2. 查 `member_whitelist(phone, status=ACTIVE)` → `is_member`；查 `coach_roster(phone, status=ACTIVE)` → `is_coach`；
3. 四类分支入库/签发 JWT（`phone, member_id?, coach_id?`，exp = now + 60 天，满足「≥30 天」）；
4. 都不是 → 返回 `code 10102` + `data: { is_member: false, is_coach: false, store: { name, phone, address }, message_text: "暂未查询到你的会员信息，请联系门店前台" }`（HTTP 200）。**必须带 `store` 与 `message_text`**：design 5.2 的兜底页要渲染门店名称/可拨号电话/地址，只回错误码会让该页无法实现。不签发可预约 token。
出参：`{ is_member, is_coach, member_id?, coach_id?, token, expire_at, store: { name, phone, address }, cancel_policy_text }`

**`GET /auth/me`**（刷新身份，免登录进入时调用）
出参：同上（无 token 换发）；每次写操作由服务端重查名单，此接口负责「再次打开小程序免登录 + 拿最新身份标记」（PRD P0-1：已登录再次打开直接进课表；白名单移除后刷新标记为失效）。
出错分支：会员名单位置 ACTIVE 但 coach INACTIVE 等，按 7.5 四类分支重算。

### 5.3 课表与课程详情（P0-2）

**`GET /sessions?date=YYYY-MM-DD&days=1..7`**（课表，默认今天，未来 7 天）
入参约束：`date` 必须是今天 ~ 未来第 6 天（共 7 天窗口），`days` ∈ 1..7 且 `date + days - 1` 不越出窗口；越界返回 **10206**（不静默截断——静默截断会让前端以为拿到了完整数据）。
出参 `data.sessions[]`：
```json
{
  "session_id": 1, "course_name": "搏击", "difficulty": "中",
  "coach_name": "李教练", "room": "A 教室",
  "start_at": "...", "end_at": "...",
  "capacity": 20,
  "booked_count": 20,          // 占名额预约数 = BOOKED + ATTENDED
  "full": true,                // booked_count >= capacity
  "remaining_seats": 0,        // capacity - booked_count，服务端算（前端不做减法）
  "seat_state": "full",        // open|full|waitlist_full|cancelled|finished|locked（design 4.6 枚举）
  "waitlist_count": 3,         // **只统计 status = WAITING**（转正/过期后自然下降）
  "session_status": "SCHEDULED",
  "locked": false,             // now >= start_at - 30min
  "card_hint_text": null,      // 课表卡片第 4 行文案：已停止预约 / 门店已取消本课 / 本课因故未能开课
  "lock_hint_text": null,      // locked=true 时下发（示例：已到开课前 30 分钟，本场次已停止预约与候补）
  "my_status": "none",         // none|booked|waitlisted|promoted|restricted|readonly_coach
  "my_waitlist_position": 4,   // my_status=waitlisted 时；位次口径见 5.5
  "my_skip_reason_text": null  // 我的候补被 SKIPPED 时的原因（design 5.6）
}
```
规则：`remaining_seats` / `seat_state` / `card_hint_text` / `lock_hint_text` 全部由服务端给（design 4.7 第 3 条：前端不做数字运算），`full=true` 时前端展示「已满 · 候补 N 人」，**不展示「剩余 0 位」这类歧义文案**；`booked_count` 同一口径贯穿课表/详情/教练端（PRD 7.4 第 1 条，含 `ATTENDED`）；`waitlist_count` 只统计 `WAITING`。**已停课 / 已结束场次照常返回**（用 `seat_state` + `card_hint_text` 表达），不返回错误码。

**`GET /sessions/:id`**（课程详情）
出参 = 5.3 课表单项的全部字段 + 以下追加字段：
```json
{
  "course_desc": "...", "cancel_deadline_minutes": 120,
  "cancel_policy_text": "开课前 2 小时可免费取消（截止 18:00）",
  "can_book": false, "can_waitlist": true, "can_cancel": false,
  "cancelable_until": null,            // 我已预约时返回（唯一判定口径）
  "cancelable_until_text": null,       // 「可自助取消至 18:00」展示文案（服务端拼，前端不拼小时数）
  "cancel_notice_text": null,          // 取消确认弹窗内关于名额释放的说明；锁定点后取消时追加「名额不再顺延」
  "late_booking_warning_text": null,   // **迟到预约期非空**：提交前的确认弹窗必须展示这句（P0-3 验收）
  "waitlist_confirm_text": null,       // 候补确认弹窗文案（自动转正告知，全流程唯一一次确认）
  "waitlist_hint_text": null,          // 「有空位将自动为你预约并通知你」
  "lock_hint_text": null,              // 锁定态说明
  "restriction_text": null,            // 限制期文案（含到期日 + 时:分）
  "restricted_until": null,            // 限制到期时刻（下发给前端做兜底展示）
  "skip_reason": null, "skip_reason_text": null,      // 我的候补被 SKIPPED 时
  "reason": null, "reason_text": null, // 已停课 STORE_CLOSED / 未开课 COACH_ABSENT 的归因句
  "store": { "name": "...", "phone": "...", "address": "..." }
}
```
- `can_book/can_waitlist/can_cancel` 与**上表所有文案字段**都由服务端一次算好（含锁定点、满员、限制期、身份、迟到预约期、停课/缺勤），前端只按值渲染（PRD 7.2 硬性、P0-2/P0-3/P0-5 验收、design 4.6/4.7 第 1~3 条）。**前端不得用本机时间判断是否处于迟到预约期，也不得拼接任何时间文案。**
- 边缘：`locked=true` → `can_book=false, can_waitlist=false`；已满且未锁定 → `can_book=false, can_waitlist=true`（候补未满时）；限制期 → `can_book=false` + `restriction_text` + `restricted_until`；迟到预约期（`cancel_deadline ≤ now < start_at - 30min`）→ `late_booking_warning_text` 非空。
- **已停课 / 已结束 / 已锁定场次一律返回 200 + 场次信息**（`session_status=CANCELLED|FINISHED`、`seat_state`、`can_book=false`、`reason`/`reason_text`），10202/10203 **只用于写接口**（预约、候补）的拒绝。理由：PRD P0-2 要求「已停课点击进入详情后无预约入口」，design 4.6 与 5.4 定义了这三种详情页状态块与「无按钮」形态，且历史卡片（CANCELLED/FINISHED）也要能点进详情；返回错误码会导致这些页面只能走错误态。

### 5.4 预约 / 取消 / 我的预约（P0-3 / P0-4）

**`POST /bookings`**（预约，幂等）
入参：`{ session_id, request_id }`
成功出参：`{ booking_id, status: "BOOKED", booked_count, remaining_seats, full, cancelable_until, cancelable_until_text, late_booking_warning_text }`
业务拒绝（部分）：10201/10202/10203/10204/10205/10301/10302/10303/10304/10003/10005/10006。
`10301`（已满）时 `data` 附 `{ waitlist_count, can_waitlist, waitlist_confirm_text }`，前端在同一弹窗引导「加入候补」（P0-3 验收）。
幂等重放返回首次 booking 结果（`idempotent_replay=true`）。
> 迟到预约警示不走本接口：它**必须在提交前的确认弹窗里展示**，所以 `late_booking_warning_text` 由 `GET /sessions/:id` 下发；本接口回显只为兜底与日志。

**`POST /bookings/:id/cancel`**（取消，幂等）
入参：`{ request_id }`
处理：同一事务——**对象级鉴权** `booking.member_id == token.member_id`（10005）→ 校验 `now < cancelable_until`（10501）与状态可取消（10502）→ `booking → CANCELLED_BY_MEMBER`（宽限取消命中则 `grace_cancel_used=1`）→ 名额释放 → 按 7.6 判断是否递补（锁定点前递补，事务内同步执行，**递补数量上界 = 本次释放名额数**；锁定点后不递补）。
出参：`{ booking_id, status: "CANCELLED_BY_MEMBER", released: true, released_count: 1, promoted_count: 1, waitlist_count_after }`
失败：10005/10501/10502/10503/10003/10006。重复取消（同 request_id）返回首次结果，**不二次释放、不二次递补**（PRD 7.2）。

**`GET /my/bookings?tab=upcoming|past`**（我的预约）
Tab 边界（与 design 5.6 一致）：`tab=upcoming` = `session.start_at > server_time`（**已取消的未来预约仍留在 upcoming**，直到开课才移入 past——design 5.6 的取舍：取消后立刻消失会让会员以为没取消成功）；`tab=past` = `session.start_at <= server_time`。排序：upcoming 按 `start_at` 升序，past 按 `start_at` 降序；`tab` 非法 → 10206。
出参 `data.bookings[]`：
```json
{
  "booking_id": 10, "session_id": 1, "course_name": "...",
  "start_at": "...", "end_at": "...", "coach_name": "...",
  "booking_status": "BOOKED",        // BOOKED|ATTENDED|CANCELLED_BY_MEMBER|CANCELLED_BY_STORE|NO_SHOW
  "display_status": "已预约（候补转正）",   // 服务端映射的状态标签文案
  "cancelable_until": "...", "cancelable_until_text": "可自助取消至 18:00",
  "can_cancel": true, "can_cancel_hint_text": null,   // 不可取消时的置灰原因（含门店电话，可拨号）
  "cancel_policy_text": "...", "cancel_notice_text": "...",
  "reason": null, "reason_text": null,               // 已停课 / 未开课的归因句（design 4.6/7.2）
  "noshow_hint_text": null,                          // 爽约卡片的异议出路（门店电话可拨号）
  "waitlist": { "status": null }     // 候补中时：{ "status":"WAITING", "position": 3, "waitlist_hint_text": "有空位将自动为你预约并通知你" }
}
```
- 覆盖 PRD P0-4 全部状态标签：已预约（含候补转正）/已取消/已到场/爽约/已停课/**未开课**/**候补中 · 第 N 位**/**候补已结束（未轮到）**；候补卡片在「即将开始」Tab，作为订阅消息未送达时**唯一兜底入口**（P0-4/P0-5 v1.2）。
- `display_status`、`cancelable_until_text`、`reason_text` 等文案全部服务端生成（含「本次为候补转正，可自助取消至 X」这类宽限提示），前端不拼时间。
- 附加出参：`data.subscribe_hint_visible`（订阅未授权/失败/额度用尽 → 前端展示顶部兜底提示条，design 4.7 第 9 条）。

### 5.5 候补（P0-5 / P0-6）

**`POST /sessions/:id/waitlist`**（加入候补，幂等）
入参：`{ request_id }`
成功出参：`{ waitlist_id, position, seq, waitlist_count, waitlist_confirm_text: "有空位将自动为你预约并通知你，不需要你再确认" }`
**位次口径（两个字段必须分开算，不许实现成同一个值）**：
- `seq` = 进入即占用、只增不减的**绝对序号**（`MAX(seq)+1`，`uk_wl_session_seq` 兜底）；
- `position` = **实时秩** = 该场次 `status = 'WAITING'` 且 `seq` 小于自己的条数 + 1。例：A（seq=1）转正后，B（seq=2）的 `position` 必须变成 1（PRD P0-5 验收）；位次无需写库更新，读时按公式算即可。
失败：10204（锁定）/10304/10401/10402/10403/10404/10003/10005/10006。注意加入候补的**唯一一次确认弹窗**发生在客户端（告知自动转正规则），接口本身只做入队；弹窗文案取详情接口的 `waitlist_confirm_text`。

**`DELETE /sessions/:id/waitlist?request_id=...`**（退出候补，幂等）
成功出参：`{ waitlist_id, status: "CANCELLED", waitlist_count_after }`；失败 10405（已是 PROMOTED/EXPIRED 等）/10005/10006。

**递补（无独立用户接口，系统行为 P0-6）**：由取消/释放事务在服务端同步执行，见 6.2。

**位次查询**：并入 `GET /sessions/:id`（`my_waitlist_position`）与 `GET /my/bookings`，前端刷新即更新（P0-5「位次与服务端不一致时下拉刷新更新」）。

### 5.6 教练工作台 / 签到 / 点名（P0-7）

> 本节全部接口统一做**对象级鉴权**：`session.coach_id == token.coach_id`（PM 已定，见 5.1），否则 10005「这不是你的课程，如需代课请联系门店」且**不写任何记录**。

**`GET /coach/sessions?date=YYYY-MM-DD`**（今日课程列表，按 `coach_id` 过滤，只返回本人的场次）
出参 `data.sessions[]`：
```json
{
  "session_id": 1, "course_name": "搏击", "room": "A 教室", "start_at": "...", "end_at": "...",
  "booked_count": 20,                 // 占名额口径 BOOKED + ATTENDED
  "checked_in": true, "checked_in_at": "...",
  "attendance_submitted": true, "attendance_submitted_at": "...",
  "window_state": "open",             // not_open|open|closed|retro_closed（design 5.7/5.8 的按钮可用性来源）
  "window_hint_text": "开课前 30 分钟可开始点名",
  "coach_absent": false,              // 判缺勤后置 true
  "coach_absent_text": null           // 「本场未检测到签到，已通知门店运营」（仅教练端可见，design 5.7）
}
```
> `booked_count` 仍为占名额口径（BOOKED+ATTENDED）。教练非会员登录时落地此页（auth 已处理）。

**`POST /coach/sessions/:id/checkin`**（到岗签到，幂等）
入参：`{ request_id }`；窗口 `start_at - 30min ~ start_at + 15min` 内（10601 窗口外拒绝，用 `window_hint_text` 说明原因）。
出参：`{ checked_in_at }`；`coach_checkin` 唯一键防重，重复签到幂等返回原时间。

**`GET /coach/sessions/:id/attendance`**（点名名单）
出参：
```json
{
  "window_state": "open", "list_frozen": false,
  "window_hint_text": "名单在开课前 30 分钟冻结 · 点名截止 19:30（开课后 15 分钟）",
  "members": [
    { "member_id": 3, "name": "张三", "nickname": null, "phone_masked": "138****0001",
      "source": "BOOK", "source_text": "自助预约",
      "checked": true, "attendance_state": "checked",   // none|checked|no_show
      "state_text": null }                              // no_show 时下发「未到（爽约）」，design 5.8
  ]
}
```
名单 = 该场次全部占名额预约（BOOKED + ATTENDED）。**「名单冻结」与「取消实时剔除」的统一口径**：`now >= start_at - 30min` 后名单**不再新增**（此时已无新预约/新候补/递补入口），但**会员在此之后取消仍然实时剔除该行**（PRD 7.6「名单冻结」与 PRD 7.2「教练点名名单实时更新」的统一解释；`list_frozen=true` 只表示「不再新增」）。

**`PUT .../attendance` 与 `PUT .../attendance/retro` 共用的覆盖式提交规则**
入参：`{ request_id, checked_member_ids: [member_id, ...] }`（`checked_member_ids` 必须都是该场次占名额名单的成员，否则 10206；数组去重）
处理：
1. **勾选者** → 写 `checkin`（`uk_checkin_session_member` 防重，`source = COACH_SELECT | COACH_RETRO`），`booking → ATTENDED`；允许的来源状态：`BOOKED`、`NO_SHOW`（计数 −1 不低于 0）、`CANCELLED_BY_STORE`（仅 `reason = COACH_ABSENT` 的场次）；
2. **未勾选者的回退规则（覆盖式语义，必须实现，PRD P0-7）**：若该会员当前为 `ATTENDED` → **删除对应 `checkin` 行**、`booking` 回退为 `BOOKED`；此时若 `now ≥ start_at + 15min` 且 `booking.created_at < session.start_at` → **在本次提交的同一事务内立即重判 `NO_SHOW`**（计数 +1、判定是否触发限制），否则保持 `BOOKED` 交由 +15min 扫描任务判；
3. 未被勾选且当前已是 `NO_SHOW` 者保持 `NO_SHOW`（不重复计数）；
4. 覆盖式幂等：同一场次重复提交（或同 `request_id` 重放）以**最新一次名单为准**，写 `audit_log` 提交版本与变更条数。
时间窗：`PUT /attendance` 在 `start_at - 30min ~ start_at + 15min`；`PUT /attendance/retro` 在 `start_at ~ start_at + 24h`（`end_at` 之后仍允许，见 6.3）。**纠错窗口与补录窗口一致（都是 24 小时）**，与 PRD P0-7「24 小时内取消勾选」对齐。
失败：10004/10005/10601/10602/10006。网络失败重试以服务端最终内容为准（PRD P0-7）。
> 回退必须删 `checkin` 行，否则会出现「`booking = BOOKED` 但 `checkin` 表仍有记录」的矛盾数据，点名名单与爽约判定会读到不一致状态。

### 5.7 停课 / 恢复 / 人工解除（admin，P0-S4，受保护）

所有 admin 写接口入参均含 `request_id`（幂等，见 6.4），并把「谁、何时、为什么」写入 `audit_log`（自由文本原因只进 audit_log，不进业务表的枚举字段）。

**`POST /admin/sessions/:id/cancel`**
入参：`{ request_id, reason_code: "STORE_CLOSED", note }`（`reason_code` MVP 只有 `STORE_CLOSED` 一个可选值，枚举校验；`note` = 自由文本原因，写 audit_log）
同事务：`session → CANCELLED` 且 `session.cancel_reason = reason_code`；全部 `BOOKED/ATTENDED → CANCELLED_BY_STORE`（`booking.cancel_reason` 复制 session 的枚举值）；全部 `WAITING → EXPIRED`；若 `start_at` 之后执行，把已有 `NO_SHOW → CANCELLED_BY_STORE` 并回滚对应会员当月计数（−1 不低于 0）；写 `audit_log`（含回滚条数）；事务提交后推送停课通知（全体预约者+候补者，失败重试 ≤3，SLA 内另有人工渠道兜底）。
出参：`{ session_id, session_status: "CANCELLED", cancelled_count, waitlist_expired_count, noshow_rolledback_count, audit_log_id }`

**`POST /admin/sessions/:id/restore`**（误停恢复）
入参：`{ request_id, note }`；仅 `end_at` 前可恢复（P0-S4 v1.3）；恢复后 `session → SCHEDULED` 且清空 `cancel_reason`，但已过锁定点则不再接受新预约/新候补/递补；原 `CANCELLED_BY_STORE` 预约**不自动恢复**（会员需重新预约）；已回滚 `NO_SHOW` 不恢复；写第二条 `audit_log`。
出参：`{ session_id, session_status: "SCHEDULED", still_locked: true|false, audit_log_id }`

**`POST /admin/members/:id/unrestrict`**（人工解除限制）
入参：`{ request_id, note }`；`member.restricted_until = 过去时间`；写 `audit_log`（谁、何时、为什么）。
出参：`{ member_id, restricted_until, audit_log_id }`

**`POST /admin/imports/sessions` | `/members` | `/coaches`**（Excel 导入，P0-S2/P0-S3）
入参：`multipart/form-data`：`file`、`kind`（SESSION|MEMBER|COACH）、`request_id`；服务端整批校验（必填、手机号格式、教练存在性、时间冲突、重复场次判更新）。
规则细节：
- **同一手机号重复出现 = 去重合并，不算非法行**（PRD P0-S3 明确要求「不报错」）；其余非法行（手机号格式错、缺姓名、教练不存在、时间冲突）→ **整批拒绝**并返回 `data.errors[] = { row, reason }`（10701），不做部分导入。
- 教练名单以**手机号**为唯一键（姓名可重复，不作为键）；课表导入模板必须含教练手机号列；同一场次重复导入（门店+开始时间+课程名）= 更新而非新增，**更新范围包含 `coach_id`**（即「临时换人」的操作路径，见 T6）。
- 成功写 `import_batch` + `audit_log`，课表 1 分钟内可见。

### 5.8 页面 ↔ 接口映射（已按 docs/design.md v1.0 第 2.1 / 5.x 节逐页复核，T1 关闭）

| 页面（design 2.1 / PRD 9.1） | 接口 | 关键出参字段（文案来源，前端只做枚举→视觉映射） |
| --- | --- | --- |
| 1 登录 / 授权页 | `POST /auth/login`、`GET /auth/me` | `is_member`/`is_coach`/`store{name,phone,address}`；失败 10101 → 「授权未完成，请重试」 |
| 2 未查询到会员信息页（兜底，无预约入口、无 TabBar） | `POST /auth/login` 返回 10102 的 `data` | `store{...}`、`message_text`（**10102 必须带 data**，否则兜底页渲染不出门店电话/地址） |
| 3 课表页 | `GET /sessions` | `seat_state`、`remaining_seats`、`waitlist_count`、`card_hint_text`、`lock_hint_text`、`my_status`、`my_waitlist_position`、`my_skip_reason_text` |
| 4 课程详情页 | `GET /sessions/:id` → `POST /bookings`（预约）/ `POST /sessions/:id/waitlist`（候补） | `can_book`/`can_waitlist`/`can_cancel`、`cancel_policy_text`、`cancelable_until(_text)`、`late_booking_warning_text`、`waitlist_confirm_text`、`waitlist_hint_text`、`lock_hint_text`、`restriction_text`、`reason(_text)`、`skip_reason(_text)`、`store` |
| 5 我的预约页（即将开始 / 已结束） | `GET /my/bookings?tab=upcoming\|past` → `POST /bookings/:id/cancel`、`DELETE /sessions/:id/waitlist` | `display_status`、`cancelable_until_text`、`can_cancel_hint_text`、`cancel_notice_text`、`reason_text`、`noshow_hint_text`、`waitlist{status,position,waitlist_hint_text}`、`subscribe_hint_visible` |
| 6 教练工作台首页 | `GET /coach/sessions`（按 `coach_id` 过滤） | `booked_count`、`checked_in(_at)`、`attendance_submitted(_at)`、`window_state`、`window_hint_text`、`coach_absent(_text)` |
| 7 教练签到·点名页 | `POST /coach/sessions/:id/checkin`、`GET\|PUT /coach/sessions/:id/attendance`、`PUT .../attendance/retro` | `window_state`/`window_hint_text`、`list_frozen`、`members[].source_text`、`members[].attendance_state`/`state_text` |
| 8 门店信息与联系（兜底区块，非独立页） | 复用 auth / sessions 详情的 `store` 字段 | `store.phone`（可拨号）、`cancel_policy_text` |
| 9 弹窗层（自绘：预约 / 候补 / 取消 / 退出候补 / 名额被抢） | 无独立接口 | 见下方「弹窗文案来源」 |
| 10 Toast / 页内错误条 / 骨架屏 / 兜底提示条 | 无独立接口 | 错误码的 `message` 由服务端下发；兜底提示条看 `subscribe_hint_visible` |
| — 内部停课工具（非小程序，2 名内部运营账号） | `POST /admin/sessions/:id/cancel`、`/restore`、`/admin/members/:id/unrestrict`、`/admin/imports/*` | 计数出参（`cancelled_count` 等） |

**弹窗文案来源（对齐 design 4.6 / 5.5）**

| 弹窗 | 文案字段 | 来源接口（必须在**打开弹窗前**已拿到） |
| --- | --- | --- |
| 预约确认（含迟到预约警示） | `late_booking_warning_text` | `GET /sessions/:id` |
| 候补确认（自动转正告知，全流程唯一一次确认） | `waitlist_confirm_text` | `GET /sessions/:id`；被拒时 `POST /bookings` 的 `data` 也回带 |
| 取消确认 | `cancel_notice_text`（宽限取消场景由服务端追加句） | `GET /sessions/:id`、`GET /my/bookings` |
| 名额被抢 | `waitlist_count`、`waitlist_confirm_text` | `POST /bookings` 的 `10301` `data` |
| 退出候补 | 静态文案（design 5.5 ⑤） | 无动态字段 |

> 页面上的「剩余名额」「候补人数」「位次」「可取消截止时刻」一律取服务端字段，前端不做加减、不做本地时间判定（design 4.7 第 2/3 条、PRD 7.2）。

### 5.9 与微信开放平台的对接（登录 / 手机号 / 订阅消息）

#### 5.9.1 微信登录（openid）与手机号授权

- **静默登录**：小程序 `wx.login()` 取 `code` → `POST /auth/session` → 后端调 `jscode2session`（需 AppSecret，环境变量）换 `openid`，可选存 `member.openid`；`openid` 仅作稳定身份标识，**MVP 不依赖它做业务判定**（身份以手机号为准，PRD 7.5）。
- **手机号一键登录**：页面 <button open-type="getPhoneNumber"> 触发授权 → 取得 `phone_code` → `POST /auth/login` → 后端调 `phonenumber.getPhoneNumber`（一次性 code 换明文手机号，需要小程序为认证主体、具备相应接口权限）→ 同一手机号同时查 `member_whitelist` 与 `coach_roster` 返回 `is_member` / `is_coach` 四类分支（PRD 7.5）。
- 拿不到手机号（用户拒绝或接口失败）→ code 10101，前端展示可重试的授权入口，不进入约课流程。
- 登录成功签发 JWT（payload 含 `phone, member_id, coach_id`），有效期 60 天（≥ PRD 要求的 30 天）；**再次打开小程序免登录**：`wx.login` + 本地 token 有效时直接 `GET /auth/me` 刷新身份标记。
- 隐私合规：小程序后端配置《用户隐私保护指引》，`getPhoneNumber` 回调需在隐私授权通过后调用；手机号仅用于身份匹配与脱敏展示，不用于营销。

#### 5.9.2 订阅消息（一次性订阅）

- **模板**（提前 2 周申请，R7）：预约成功（课程名/时间/门店）、候补转正成功（与预约成功文案区分，注明候补转正）、开课提醒、停课通知，共 4 个必备模板 + 1 个「限制解除通知」（PRD 7.3/P0-S1；T4）。模板 ID 配在服务端配置，不在客户端硬编码。
- **授权时点（与 PRD 7.5、design 3.1/3.2/4.7 第 5 条统一，工程照此实现）**：`wx.requestSubscribeMessage({ tmplIds })` 必须由**用户手势**触发，且 `wx.showModal` 的系统回调不算手势 —— 所以确认弹窗必须是自绘组件。具体实现顺序写死：
  1. 在自绘弹窗「确认预约」/「确认加入候补」按钮的 **tap 处理函数里第一件事同步调用** `requestSubscribeMessage`：**不 await、不放在业务请求之后**。若先 `await POST /bookings` 再调用，微信会判定为无手势调用而失败，会员将永远收不到「预约成功 / 候补转正成功」订阅消息（P0-S1 与指标 G 直接落空）；
  2. 业务请求与授权请求**完全解耦**：同意 / 拒绝 / 回调失败都不影响业务，一律以业务接口返回为准；**绝不允许**「等授权成功才提交预约」，也不因授权失败而回滚或提示失败（PRD 7.5、P0-3、P0-S1）；
  3. 用户点「再看看 / 算了」时：不发业务请求，也不请求授权；
  4. 建议客户端加「本会话已成功授权过就不再请求」的内存标记（design 8.3，待真机确认）；
  5. 未授权/额度用尽的兜底：`GET /my/bookings` 返回 `subscribe_hint_visible=true`，前端在「我的预约」页顶部展示兜底提示条（design 4.7 第 9 条，这也是会员确认转正结果的唯一兜底入口）。
- **发送（服务端）**：事件发生（预约成功 / 候补转正 / 开课提醒 / 停课 / 限制解除）后调 `subscribeMessage.send`（需 `access_token`，稳定版缓存：内存缓存 + 过期续期 + 失败重拉）；一次性订阅额度按次消耗、被用户拒绝或额度用尽时返回特定错误码，**不影响业务状态**（ADR-5）。
- **通知与业务解耦**：预约/转正/停课等事务先提交，再触发发送；发送失败写 `notification_log(status=FAILED_RETRY)` 由定时任务重试 ≤3 次，仍失败置 `FAILED_GIVEUP` 并告警（PRD P0-S1）。
- **兜底**：通知全方位未达时，会员靠「我的预约」页候补卡片（候补中 · 第 N 位 → 已预约(候补转正)）确认结果（PRD P0-4/P0-5 v1.2）；指标 G（转正后 5 分钟推送成功率 ≥90%）按 `notification_log` 统计，<70% 时启动 P1 站内提示（PRD R2）。

---

## 6. 关键难点方案

### 6.1 名额并发与超卖防护（PRD 7.4）

**方案：事务 + 行锁顺序 + 部分唯一索引兜底 + 幂等。**

预约事务（单场次，严格顺序）：
```
BEGIN;
  -- ① 锁场次行（所有该场次的预约/取消/递补/候补操作在此行上进行串行）
  SELECT ... FROM session WHERE id = ? FOR UPDATE;
  -- ② 服务端时间校验：now < start_at - 30min（10204），now < start_at（10205 二道防线）
  -- ③ 锁会员行：SELECT ... FROM member WHERE id = ? FOR UPDATE（串行化同一会员的并发约课，防止两笔重叠预约同时通过冲突校验）
  -- ④ 冲突校验：该会员其它 BOOKED/ATTENDED 预约 JOIN session，
  --    where b.member_id=? and b.status in ('BOOKED','ATTENDED')
  --    and s.start_at < new.end_at and s.end_at > new.start_at   -- 区间重叠，首尾相接不算（7.5 v1.3）
  -- ⑤ 容量校验：COUNT(booked+attended) < capacity，否则回 10301
  -- ⑥ INSERT booking（写入 cancelable_until = min(max(cancel_deadline, created_at+30min), start_at)）
  --    唯一索引 uk_booking_member_session_active 兜底（10302）
COMMIT;
```
- 同一场次的预约/取消/递补都先拿 `session` 行锁 → 该场次名额操作**全局串行**，杜绝「两个释放并发各递补一次」与「第 capacity+1 个有效预约」。
- 行锁获取顺序固定为 `session → member`，避免死锁；若出现死锁（MySQL 检测）整体重试 ≤2 次。
- 「成功后剩余名额」只在锁内重新 COUNT 后返回；**禁止前端按本地计数 -1**。
- 容量统计走 `idx_booking_session_status(session_id, status)` 索引。
- 压测验收（PRD 7.4 第 9 条）：单场 20 容量，50/200 并发预约同一场 → 恰好 20 条 BOOKED，其余 10301；重复同 request_id 100 次 → 1 条记录。

### 6.2 候补队列顺序与自动递补触发（PRD 7.1 / 7.6）

- **入队 FIFO**：`seq = 场次当前 max(seq)+1`（`SELECT MAX(seq)` 后 +1 或 `INSERT ... SELECT COALESCE(MAX(seq),0)+1` 原子化），`uk_wl_session_seq(session_id, seq)` 唯一索引兜底冲突重试 ≤3 次（PRD 7.4 第 7 条）。候补人数 ≥ capacity → 10401。
- **递补（与释放同事务；数量必须按「本次释放名额数」设上界）**：
```
BEGIN;
  SELECT session FOR UPDATE;                     -- 串行化该场次的名额操作（预约/取消/递补同一把锁）
  if now >= start_at - 30min:                    -- 已过锁定点：不递补
     所有 WAITING → EXPIRED; COMMIT; return;
  released := 本次释放的有效预约数;               -- 会员自助取消 = 1；同一事务内多次释放则累加；
                                                 -- 停课释放 / 判爽约释放 = 0（这两种都不递补）
  promoted := 0;
  skipped := [];
  while promoted < released:                     -- ★ 上界 = 本次释放名额数，绝不允许把整条队列转正
     占用名额数 := COUNT(booking WHERE session_id=? AND status IN ('BOOKED','ATTENDED'));
     if 占用名额数 >= capacity: break;           -- ★ 二次保险：绝不产生第 capacity+1 个有效预约
     SELECT * FROM waitlist_entry WHERE session_id=? AND status='WAITING'
       ORDER BY seq ASC LIMIT 1 FOR UPDATE;      -- 严格 FIFO 队首（FIFO 唯一依据是 seq）
     if 无 WAITING: break;
     校验队首会员：身份(名单)有效 / 未限制 / 无时间区间重叠(BOOKED/ATTENDED)
       └ 不满足 → waitlist → SKIPPED + skip_reason（收进 skipped，**提交后再通知**）
       └ 满足   → INSERT booking(source=WAITLIST_PROMOTED, created_at=promoted_at=now, cancelable_until=公式)
                  waitlist → PROMOTED；promoted += 1
  COMMIT;
  -- 事务提交之后（事务外）才做外部调用：逐个 send(转正成功) / send(被跳过)，失败写 notification_log 由重试兜底
```
- **同一事务**保证：一次取消恰好递补一个人（3 个并发取消 → 递补人数 = 释放名额数，PRD P0-6 验收）。`released` 上界 + 容量二次校验是防超卖的关键，**不许省略**（省略后 20 容量释放 1 个名额会把队列里 19 人全部转正，占名额数变成 39）。
- **位次口径**：转正 / 退出 / 跳过都只改变后续会员的 `position`（实时秩 = 同场次 `WAITING` 中 `seq` 更小的条数 + 1），**无需写库更新**，读接口按公式算（`seq` 不变）；不要用 `seq` 冒充 `position`（见 5.5）。
- **10 秒推送**：事务提交后进程内立即调用 `notification.send(转正成功)`；失败写 `notification_log` 由重试任务兜底（≤3 次）；通知未送达**不影响转正生效**（v1.2）。**所有外部调用（含 SKIPPED 通知）都放在事务提交之后**，不在事务内做网络请求（与 ADR-5 一致）。
- **EXPIRED 两个触发点**：① `start_at - 30min` 定时任务批量置 EXPIRED；② 递补事务开始时已过锁定点（边界竞态）→ 全部置 EXPIRED、名额空置（宁少卖不多卖，7.1/7.6）。
- **转正幂等/唯一**：`uk_booking_member_session_active` 保证转正插入在极端并发下不重复；递补在 session 行锁内是唯一写者。

### 6.3 取消截止时间与定时任务（PRD 7.2 / 7.3 / 10.3）

- **取消判定单一口径**：`cancelable_until` 在创建/转正时计算**落库**（`booking.cancelable_until`），取消接口只比较 `now < cancelable_until`；前端只展示服务端下发的值，不做第二套判断（v1.3 硬性）。公式与示例严格对齐 PRD 7.2（含「宽限取消每场次每人 1 次」：取消命中宽限置 `grace_cancel_used=1`，同会员该场次后续再建预约不再享有宽限）。
- **锁定点之后的取消**：只要 `now < cancelable_until` 仍可自助取消（宽限窗口可跨越锁定点），此时释放的名额因已过锁定点**不递补**、点名名单实时剔除该会员（7.2「锁定点之后仍可取消」）。
- **定时任务（node-cron，每分钟扫描；时间以 DB 与服务端时钟为基准）**：

| 任务 | 触发条件（扫描） | 动作（对齐 PRD 10.3） |
| --- | --- | --- |
| 场次锁定 + 候补过期 | `start_at - 30min` 到点 | 拒绝新预约/新候补（主要由写接口校验负责任，定时任务兜底）；全部 `WAITING → EXPIRED`；停止递补 |
| 开课提醒 | 扫描 `start_at - 120min ≤ now < start_at - 120min + 1min` 且 `status=SCHEDULED` 的场次 | 对当时已存在的占名额预约（BOOKED/ATTENDED）发送提醒订阅消息（幂等：`notification_log` 已有同 `scene+ref` 记录则不重发）。**迟到预约（T-120 之后创建）拿不到本次提醒，按 PRD 口径不做补发**，见 T7 |
| 教练缺勤判定 | 每个 tick 扫描 `session.status IN ('SCHEDULED','FINISHED') AND coach_absent = 0 AND now >= start_at + 15min AND now <= start_at + 24h` | 无 `coach_checkin` 且无任何 `checkin` 记录 → `coach_absent = 1` + 推送内部运营。幂等条件 = `coach_absent = 0`（重复执行无副作用）。**同一 tick 内先跑本任务再跑判爽约任务**（PRD 7.5 执行顺序） |
| 自动判爽约 | 与缺勤任务同一扫描区间 `now >= start_at + 15min AND now <= start_at + 24h`，`status IN ('SCHEDULED','FINISHED')`，且**已判定过是否缺勤**（`coach_absent` 已定：先跑缺勤任务，或在本任务内联判定「无 `coach_checkin` 且无任何 `checkin` 记录」并落标记） | 先判 `COACH_ABSENT`：是 → **整场跳过**；否 → 仅处理 `booking.created_at < session.start_at` 且仍 `BOOKED` 且无 `checkin` 记录的行 → `NO_SHOW`，计数 +1，月 3 次则 `restricted_until = now + 7 天` + 通知。幂等条件 = 「仍为 `BOOKED` 且无核销」；**区间上界取 `start_at + 24h`**（与补录窗口对齐，保证：① 点名/补录里「取消勾选」后的重判有兜底；② 任务漏跑能被下一轮补偿）。超过 `start_at + 24h` 仍有未处理的行 → 记 `WARN` 告警并交人工，**不静默判** |
| 场次结束 | `end_at` 到点（`now >= end_at AND status = 'SCHEDULED'`） | `session → FINISHED`（`session` 本身此后只读）；若 `coach_absent = 1`，把仍为 `BOOKED` 的预约置 `CANCELLED_BY_STORE`（`cancel_reason = COACH_ABSENT`，不计爽约、不计入指标分母）。幂等 = 按状态过滤；**`booking` 的写不受「FINISHED 只读」限制**：`end_at` 之后的判爽约（区间到 `start_at+24h`）与教练补录仍要写 `booking`，这是 PRD P0-7「24h 内补录」的硬要求，「FINISHED 只读」只约束**场次维度**（不再新增预约/候补、不可再提交点名窗口内的点名） |
| 通知重试 | 每分钟 | `notification_log` 中 FAILED_RETRY 重发，≤3 次后置 FAILED_GIVEUP 并告警 |
| 限制到期通知 | `restricted_until` 到期 | `member.restricted_until` 变过去时间后推送解除通知（去重） |
| 月计数归零 | 每月 1 日 00:00 | `count_month != 当前月` 的 member 清零 `no_show_count_current_month`（写侧也兜底） |

> 任务必须幂等（按状态+时间条件过滤，重复执行不产生副作用）；每次执行写执行日志（时间、扫描条数、变更条数）用于定位。缺失该任务时，所有判爽约/缺勤任务的补偿窗口都是 `start_at + 24h`（与补录窗口对齐），超出后告警走人工，避免「漏跑一次就永久错账」。任务与写接口共享同一套业务规则（判爽约条件、锁定点判定），不允许各写一份。

### 6.4 幂等与重复提交（PRD 7.4 第 5/6 条、7.2）

**唯一落库方案：一张 `idempotency` 表（DDL 与使用规则见 4.3）。不再有「复用目标表唯一键」或 `cancel_idempotency` 这类第二套说法。**

| 写接口 | 幂等键 | 首次结果快照 | 重放语义 |
| --- | --- | --- | --- |
| `POST /bookings` | `idempotency.request_id` + `booking.request_id` 唯一索引（最后一道闸） | booking 结果（含 `booked_count`、`cancelable_until`） | 返回首次结果，不再 `INSERT`（唯一键 10302 兜底） |
| `POST /bookings/:id/cancel` | `idempotency.request_id` | `{released, released_count, promoted_count, waitlist_count_after}` | 返回首次结果，**不二次释放、不二次递补**（PRD 7.2）。**禁止用 `booking.status` 判重**——同一会员「先取消 → 再预约 → 再取消」的第二条预约仍是合法的可取消请求，状态判重会误判 |
| `POST /sessions/:id/waitlist` | `idempotency.request_id` + `uk_waitlist_member_session_waiting` | `{waitlist_id, position, seq}` | 返回首次结果 |
| `DELETE /sessions/:id/waitlist` | `idempotency.request_id` | `{waitlist_id, status}` | 返回首次结果 |
| `POST /coach/sessions/:id/checkin` | `idempotency.request_id` + `uk_coach_checkin_session` | `{checked_in_at}` | 返回首次结果 |
| `PUT .../attendance`、`PUT .../attendance/retro` | `idempotency.request_id` | 提交摘要（名单 hash + 变更条数） | **覆盖式幂等**：同 `request_id` 重放返回首次结果；**不同 `request_id` 的再次提交以最新名单为准**（这是业务语义，不是幂等冲突），并写 `audit_log` 提交版本 |
| `POST /admin/sessions/:id/cancel`、`/restore`、`/admin/members/:id/unrestrict`、`/admin/imports/*` | `idempotency.request_id` | 计数出参 | 返回首次结果，不二次变更（PRD P0-S4） |

- **参数摘要 `request_hash`**：对规范化后的关键参数算 SHA-256（预约 = `session_id`；取消 = `booking_id`；候补 = `session_id`；点名 = `session_id` + 排序后的 `checked_member_ids`；导入 = 文件内容的 hash）。同 `request_id` 但 hash 不同 → **10006**（不重放、不执行），避免客户端复用 UUID 造成「点 A 却生效成 B」。
- **并发同 `request_id`**：靠 `uk_idem_request` 串行化（详见 4.3 规则①），读不到首次结果时返回 10007 让客户端重试，客户端带同一 `request_id` 重试是安全的。
- **`booking.request_id` 唯一索引必须保留**（PRD 10.1 要求）：它是防重复占位的最后一道闸，即使幂等表异常也不会产生第二条预约。
- 网络中断恢复：客户端重发同 `request_id`；或用「我的预约」页以服务端状态兜底（PRD P0-3/P0-4）。
- 幂等记录保留 30 天（每日任务清理更早的行）；`request_id` 由客户端每次**新意图**生成，重放只发生在分钟级，30 天足够。

### 6.5 时间口径与边界（PRD 7.4 第 10 条、7.6 补充）

- 全部业务判期用服务端时间（Asia/Shanghai）；边界（恰等于 `start_at - 30min`）按已锁定处理（宁严不松）；恰在 `cancelable_until` 前 1 秒允许取消（7.2「允许成功」）。
- `start_at - 120min`（取消截止）与 `start_at - 30min`（锁定点）是两个独立实现的时间点，禁止用一个常量推导另一个（PRD 5.3/7.6 警示）。
- 响应带 `server_time` 供前端校准；前端禁止用本地时间判断可用性。

### 6.6 安全与可观测性

- 边界校验：手机号、`request_id`（UUID 格式）、枚举（`status`/`source`/`seat_state`/`reason_code`）、`date`/`days`/`tab`、`checked_member_ids`（数组、去重、必须是本场次占名额名单成员）、Excel 行内所有字段，全部在入口校验；SQL 全部参数化（mysql2 `execute`）。
- 密钥：微信 AppSecret、JWT secret、MySQL 口令全走环境变量，不入代码/日志。
- 最小权限 + 对象级鉴权：admin 接口独立 token + 2 名内部运营账号（环境变量配置）+ 建议 IP 白名单；教练接口每次写操作重查 `coach_roster.status` **并校验 `session.coach_id == token.coach_id`**；会员写操作重查 `member_whitelist.status` **并校验 `booking.member_id == token.member_id`**（规则见 5.1）。越权一律 10005：不写记录、不释放名额、不触发递补，也不泄露资源是否存在。
- 日志：请求级结构化日志（request_id、path、耗时、错误码），写操作记录前后值；`audit_log` 覆盖一切人工/管理操作；`notification_log` 覆盖通知链路。
- 错误处理：所有可能失败处给出明确错误码与中文文案；未捕获异常统一 50000 + 告警（前端展示错误态与重试）。

### 6.7 风险表

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| R3 超卖/重复占位（PRD） | 极高 | 本方案 6.1 行锁+唯一索引+幂等；压测用例在 M2 强制通过；超卖即回滚条件（PRD 12.2） |
| R2 订阅消息到达率低，会员不知已转正 | 高 | 点击动作内同步请求授权且与业务解耦；「我的预约」候补卡片兜底；指标 G 监控（<70% 启动 P1 站内提示） |
| R7 订阅模板审核周期不可控 | 中 | 提前 2 周申请 4+1 模板，工程师负责推进（在里程碑 M0 即启动） |
| R8 教练不点名 → 爽约规则失效 | 高 | 签到页 15 秒内完成；`COACH_ABSENT` 兜底避免误伤会员；指标 D 监控 |
| R9 停课 SLA 2 小时失守 | 中 | 停课通道动作原子化（P0-S4 事务）；失败告警；SLA 频繁失守提前立项 P1-1 |
| R11 教练身份判定错误（只查会员名单） | 高 | auth 双名单同时查 + 四类分支；阶段 0 用非会员教练账号验证（M1 验证项） |
| 定时任务重复执行/漏执行（如判爽约与缺勤顺序颠倒） | 高 | 任务幂等 + 执行日志 + 「先判缺勤再判爽约」顺序写死并有回归用例（M4） |
| 单实例/单库故障 | 中 | 云 RDS 自动备份 + 应用无状态多实例伸缩（数据库仍是单点，MVP 可接受，纳入告警）；回滚方案见 PRD 12.2 |
| 时区/边界竞态（锁定点前后 1 秒、取消边界） | 中 | 全部服务端判期 + `server_time` 下发 + 边界用例覆盖（M2/M3） |

---

## 7. 决策记录

### ADR-1：模块化单体 + 单实例部署，不拆微服务、不加消息中间件
- 背景：单店 MVP，日活预计 ≤500，写操作 ≤1000/天；业务强依赖跨表事务（取消+递补必须同事务）。
- 决策：后端一个进程（Express），MySQL 单实例；不加 Redis、不加 MQ、不拆服务。
- 备选：微服务拆分（booking/waitlist 分开）、引入 Kafka/RabbitMQ、Redis 延迟队列。
- 影响与代价：放弃横向扩展灵活性与「队列重试」基建；换来一致性最简单、运维最少、可逆（未来拆服务时模块边界已按 2.2 划好，迁移成本可控）。拆服务会把「取消+递补同事务」变成分布式事务难题，与 PRD 一致性要求直接冲突。

### ADR-2：名额一致性用 MySQL 行锁 + 唯一索引，不用 Redis 分布式锁
- 背景：名额绝不能超卖（PRD 指标 F 为 0 容忍）；热点是单场次名额。
- 决策：写路径（预约/取消/递补/候补）统一先 `SELECT session FOR UPDATE` 串行化；`booking`/`waitlist_entry` 部分唯一索引兜底。
- 备选：Redis SETNX 分布式锁 + 缓存计数。Redis 单点故障/超时会让「抢到锁」与「数据库提交」之间产生不一致窗口，且 MVP 单实例根本不需要分布式。
- 影响与代价：单场次并发预约被串行化（200 并发约 2~6 秒排空，可接受）；换来「无缓存一致性心智负担」。

### ADR-3：Express + TypeScript + mysql2 手写 SQL，不用 NestJS / ORM
- 背景：需要 `FOR UPDATE`、生成列部分唯一索引、跨表事务、分钟级定时任务扫描；这些在 ORM 里要么不支持、要么绕。
- 决策：Express 5、mysql2、node-cron；分层 route→service→repo 保证模块边界。
- 备选：NestJS（DI/模块化样板，单人维护收益低）；TypeORM/Prisma（部分唯一索引、事务内手写 SQL 都很别扭）。
- 影响与代价：手写 SQL 样板偏多、schema 迁移用 SQL 迁移脚本（migration 目录管理）；换来对事务与索引的完全掌控。

### ADR-4：进程内 node-cron 定时任务，不引入分布式调度
- 背景：全部定时任务是「分钟级全量扫描 + 幂等更新」，单实例足够。
- 决策：node-cron 每分钟 cron，任务注册表 + 幂等条件 + 执行日志；部署单实例，若多实例则先加 leader 竞选（环境变量锁）再考虑分布式调度。
- 备选：BullMQ + Redis、云函数定时触发器。
- 影响与代价：MVP 无调度中心；换来零中间件。任务幂等性（6.3）已设计，未来迁移成本低。

### ADR-5：订阅消息「事务提交后立即调用 + notification_log 重试」，不用消息队列
- 背景：候补转正要求「10 秒内推送」，但通知失败不能回滚业务（v1.2）。
- 决策：转正/预约/停课等事务提交成功后，进程内同步调 `notification.send`；失败写 `notification_log(status=FAILED_RETRY)`，每分钟重试 ≤3 次；不存在「等通知成功才提交」的路径。
- 备选：发消息到队列由消费者发送。MVP 日发送几百条，队列带来的持久化/重投/监控成本大于收益。
- 影响与代价：进程崩溃瞬间的通知可能延迟（由重试任务补偿）；换来发送链路与业务状态彻底解耦，通知丢失不影响名额流转。

### ADR-6：JWT 无状态登录 + 每次写操作重查名单，实现「身份实时失效」
- 背景：PRD 要求「名单移除后立即失效」（会员被移除不能约课；教练被移出不能点名），同时要求登录态 ≥30 天。
- 决策：JWT（HS256，exp 60 天）作登录态；所有写接口（预约/取消/候补/点名/补录）在事务内重查白名单/教练名单状态，不靠 token 失效。
- 备选：Redis 会话表（可主动踢出，但引入 Redis）；token 黑名单（复杂度高）。
- 影响与代价：每次写操作多 1~2 次索引查询（毫秒级）；换取无需会话存储、实时失效语义精确。

### ADR-7：写接口幂等统一落一张 `idempotency` 表，靠目标表状态判重
- 背景：PRD 要求「同一 `request_id` 重复提交只产生一条记录、第二次返回首次结果」，涉及 11 个写接口；其中取消还要求「不二次释放、不二次递补」，且失败结果也要能被重放（如「已满」被重试时仍返回已满）。
- 决策：新建 `idempotency` 表（`request_id` 唯一 + `request_hash` + 首次 `result_code/result_data`），与业务**同事务**写入；`booking.request_id` 唯一索引保留为预约的最后一道闸。
- 备选：① 每个接口各加幂等列（如 `booking.last_cancel_request_id`）——列数随接口膨胀，失败结果无处存，且取消接口要靠 `booking.status` 判重（会误判「先取消→再预约→再取消」）；② 只靠目标表状态判重——同样是失败结果无处存、语义不稳。
- 影响与代价：多一张表和一次 INSERT（同事务，无额外往返）；所有写接口共享一套中间件，实现与测试只需覆盖一处。代价：幂等记录自身需要清理（30 天），且「业务拒绝也提交幂等行」这一点必须显式实现，否则重放会返回不同结果。

---

## 8. 里程碑与验证（按 P0 功能拆分，每个里程碑可运行可验证）

| 里程碑 | 内容（对应 PRD P0） | 如何验证 / 通过标准 |
| --- | --- | --- |
| M0 骨架 + 登录与名单 | 项目骨架、DB schema + migration、错误码/响应/日志、`idempotency` 表 + 幂等中间件、对象级鉴权工具、auth（P0-1）、名单导入（P0-S3）、订阅模板申请启动（R7）；`GET /auth/me` 与 10102 兜底 data | 纯会员/纯教练/双身份/都不是 四类账号登录分支全对（10102 带 `store` 与 `message_text`）；非会员教练直接进工作台；移除名单后写操作被拒（10003/10004）；导入 500 手机号、重复手机号去重不报错、非法行整批拒绝并报行号；token 60 天可用；同 `request_id` 重放返回首次结果、换参数返回 10006；A 取消 B 的预约 → 10005 且名额不变 |
| M1 课表与详情 | schedule：课表/详情/`cancel_policy_text`/名额统计（P0-2）、课表导入（P0-S2） | 课表返回未来 7 天、已满场次显示「已满·候补 N 人」；锁定点后 `can_book/can_waitlist=false` 且文案由服务端下发；导入校验（必填/教练存在/时间冲突/重复场次判更新）全过；接口 <200ms（索引验证） |
| M2 预约与取消（并发与幂等核心） | booking：预约/我的预约/取消 + 6.1 事务 + 6.4 幂等（P0-3/P0-4）；`cancelable_until` 公式落库；`my/bookings` 的 upcoming/past 边界 | 压测：单场 20 容量 50/200 并发 → 恰好 20 条 BOOKED，其余 10301；同 request_id 100 次 → 1 条；同会员工时区重叠被拒、首尾相接允许；迟到预约/候补转正 `cancelable_until` 各示例（18:00/17:59/18:30 等）断言一致；取消重放不二次释放/递补（`released_count=1`、`promoted_count=1`）；已取消的未来预约仍在 upcoming Tab |
| M3 候补与自动递补 | waitlist：加入/退出/位次/递补（P0-5/P0-6）；锁定点 EXPIRED | FIFO 严格按 seq；队首校验不通过 SKIPPED 通知并顺延（通知在事务提交后）；**一次取消只允许转正 1 人**（队列还有 19 人时占名额数必须仍是 capacity，不得超卖）；3 并发取消递补 3 人；`position` 为实时秩（A 转正后 B 显示第 1 位）；锁定点边界（事务开始时已过锁定点 → 不递补、全部 EXPIRED）；转正 10 秒内生效且无会员确认弹窗/时限；`cancelable_until` 保证每次转正 ≥30 分钟 |
| M4 教练签到 + 爽约/限制 + 定时任务 | checkin + noshow（P0-7/P0-8）；缺勤判定/判爽约/锁定/场次结束/月归零/限制到期（6.3） | 点名不改剩余名额（20 人课点 5 人仍显示已满）；`COACH_ABSENT` 场次整场不判爽约且 end_at 转 `CANCELLED_BY_STORE`；先判缺勤再判爽约（回归用例）；**误勾后取消勾选 → 删 `checkin` 行 → 回退 `BOOKED` → 若已过 +15min 立即重判 `NO_SHOW`**；判爽约漏跑一轮后能在 24h 窗口内补偿；月 3 次触发限制、7 天自动解除并通知；24h 补录计数回滚（3→2）；点名窗口外拒绝（10601）；教练操作他人场次返回 10005 且不写记录 |
| M5 通知 + 停课/客服兜底 | notification（P0-S1）+ admin 停课/恢复/解除（P0-S4）+ 门店信息兜底 | 预约/转正/提醒/停课 4 类消息发送成功，失败重试 ≤3 并记录；拒绝授权不影响预约/转正结果，「我的预约」候补卡片兜底可见；停课事务（含 `start_at` 后执行的 `NO_SHOW` 回滚）原子完成 + audit_log 完整；恢复仅限 `end_at` 前且原预约不自动恢复 |
| 收尾 | 全链路回归、压测复跑、告警/监控接入、按 PRD 12.1 出阶段 0 内部验证包 | 4 条主流程（预约→取消→转正→签到）各跑通 ≥10 次；无超卖；与 design.md v1.0 逐页复核交互与文案字段一致性（T1 已关闭，用 5.8 映射表逐页签核） |

**验证环境说明**：当前公司无线上环境，本方案定义的「如何验证」在开发环境/压测脚本执行；上线前仍须按 PRD 第 12.1 节分阶段走内部验证与灰度。测试同学可据此编写测试计划（docs/test-plan.md），P0 验收标准已在 PRD 第 8 节逐条给出，本方案补充工程可测断言（并发、边界、幂等）。

---

## 附：对上游（PRD v1.3）的假设与备注

1. 本方案把 `member` 与 `member_whitelist` 拆为两张表（PRD 10.1 同样列出两者）：白名单是「导入的身份登记」，`member` 是「登录后产生的账号/爽约计数」，一手机号一行；预约校验身份以白名单 status 为准。
2. PRD 要求「已使用的宽限取消，同会员同场次再创建的预约直接不可自助取消」——落库实现为：创建预约时若 `(member_id, session_id)` 历史存在 `grace_cancel_used=1`，则新预约 `cancelable_until = cancel_deadline`（宽限不重复给）。此为 PRD 7.2「同一会员同一场次最多 1 次宽限取消」的工程化。
3. 「候补上限 = capacity」与「已满才可候补」均由 `waitlist_count` 与 `booked_count` 实时计算，锁定点后由 10204 挡在入口。
4. `cancel_policy_text` 由服务端按 `cancel_deadline_minutes` 生成并随 auth/详情/我的预约返回（示例含具体截止时刻），前端不做拼接（PRD 7.2 硬性）。
5. **判爽约任务的扫描区间**（`start_at + 15min ~ start_at + 24h`）与 PRD 10.3 的文字「`start_at + 15min` 后每分钟扫描」并不矛盾：PRD 给的是**起始时刻**，本方案补出**终止条件**（对齐补录窗口 24h），否则「取消勾选后重判」与「任务漏跑补偿」都无法实现。此处若与 PRD 后续修订冲突，以 PRD 为准并回改本节。
6. **教练端对象级鉴权（限定本人场次）**是 PRD 7.5 未定义、由 PM 在本次返工中口头确认的新增口径；本方案已按此实现（5.1/5.6），并已请 PM 补进 PRD（v1.4 变更记录 + 7.5 条目）。在 PRD 更新前，本方案这一条**先于 PRD 生效**。

---

## 第 1 轮审查处理记录（逐条回应）

### 必须修改（9 项）

| # | 审查意见 | 处理结果 |
| --- | --- | --- |
| 1 [P0] | 6.2 递补循环无上界，会把整条队列转正 → 超卖 | **已改**。6.2 伪代码新增 `released := 本次释放的有效预约数`，循环条件改为 `while promoted < released`，并在每轮插入前二次校验 `COUNT(BOOKED,ATTENDED) < capacity`；补出「停课/判爽约释放 = 0，不递补」；新增说明句点明「省略后 20 容量释放 1 个名额会转正 19 人」。M3 增加对应断言。 |
| 2 [P0] | 缺对象级鉴权约定与错误码 | **已改**。5.1 新增「鉴权（对象层，越权防护，必须实现）」：会员资源校验 `booking.member_id`、教练资源校验 `session.coach_id`（PM 已确认取「限定本人场次」，理由与代价写在 5.1；`GET /coach/sessions` 一并按 `coach_id` 过滤），admin 走 token；资源不存在与无权统一 10005 且不泄露存在性；错误码表新增 10005/10006/10007；5.6 顶部与 6.6 同步；M0/M4 补越权断言（「A 取消 B 的预约 → 10005 且不释放名额」）。 |
| 3 [P1] | 幂等落库方案三种说法冲突、无对应表 | **已改**。删除 `cancel_idempotency` / `booking.last_cancel_request_id` 两套说法，定名为**一张 `idempotency` 表**并在 4.3 给出完整 DDL（`request_id` 唯一、`action`、`target_id`、`request_hash`、首次 `result_code/result_data`）；4.3 附使用规则（同事务写入、冲突重放、业务拒绝也落库提交、系统错误不落库）；6.4 重写为按 11 个写接口逐行列明「幂等键 / 首次结果快照 / 重放语义」，并明确**禁止用 `booking.status` 判重**（会误判「先取消→再预约→再取消」）、同 `request_id` 但 `request_hash` 不同返回 10006；新增 ADR-7 记录取舍。 |
| 4 [P1] | 教练「取消勾选」后的状态回退未定义，与 P0-7 矛盾 | **已改**。5.6 将 `PUT /attendance` 与 `/attendance/retro` 合并为「共用的覆盖式提交规则」，新增第 2 条：未勾选且当前为 `ATTENDED` → **删除 `checkin` 行 + 回退 `BOOKED`**；若 `now ≥ start_at + 15min` 且 `created_at < start_at` → **同一事务内立即重判 `NO_SHOW`**；并明确「未勾选者保持原状」只适用于其它状态；6.3 判爽约扫描区间上界改为 `start_at + 24h` 使重判有兜底；4.4 状态机加 `ATTENDED → BOOKED`；M4 加验证项。 |
| 5 [P1] | 判爽约任务的扫描区间与终止条件未定义 | **已改**。6.3 重写「教练缺勤判定」「自动判爽约」「场次结束」「开课提醒」四行：给出完整扫描条件（`status IN ('SCHEDULED','FINISHED') AND now >= start_at+15min AND now <= start_at+24h`）、幂等条件（`coach_absent = 0` / 「仍为 `BOOKED` 且无核销」）、终止与补偿规则（超窗仍未处理 → `WARN` 告警交人工，不静默判），并说明与 PRD 10.2「FINISHED 后不可写」的关系（只约束场次维度）。同步在附录备注 5 说明与 PRD 10.3 措辞的关系。 |
| 6 [P1] | 详情接口用 10202/10203 拒绝已停课/已结束场次 | **已改**。5.3 详情改为「已停课/已结束/已锁定一律返回 200 + 场次信息（`session_status`、`seat_state`、`can_book=false`、`reason`/`reason_text`）」，并写明理由（P0-2 与 design 4.6/5.4 的状态块与历史卡片）；错误码表 10202/10203 标注为**仅用于写接口**。 |
| 7 [P1] | 出参缺 design 4.6 要求的 `*_text`/状态字段 | **已改**。课表出参补 `remaining_seats`/`seat_state`/`card_hint_text`/`lock_hint_text`/`my_skip_reason_text`；详情出参补 `cancelable_until_text`/`cancel_notice_text`/`late_booking_warning_text`（**提交前**从详情下发）/`waitlist_confirm_text`/`waitlist_hint_text`/`lock_hint_text`/`restriction_text`/`restricted_until`/`skip_reason(_text)`/`reason(_text)`；我的预约补 `can_cancel_hint_text`/`reason_text`/`noshow_hint_text`/`subscribe_hint_visible`；教练端补 `window_state`/`window_hint_text`/`coach_absent(_text)`/`source_text`/`attendance_state`/`state_text`/`list_frozen`。并新增 5.8「弹窗文案来源」表，注明「打开弹窗前必须已拿到」。 |
| 8 [P1] | 5.9.2 允许「先发业务请求、再弹授权」 | **已改**。改为写死顺序：弹窗「确认预约/确认加入候补」的 tap 处理函数里**第一件事同步调** `requestSubscribeMessage`，**不 await、不放在业务请求之后**；并写明「若先 `await POST /bookings` 再调用会因无手势而失败，P0-S1 与指标 G 直接落空」；补充「点「再看看 / 算了」时不发请求也不请求授权」与会话内已授权标记。与 design 3.1/3.2/4.7 第 5 条一致。 |
| 9 [P2] | `position` 口径未定义 | **已改**。5.5 显式区分：`seq` = 绝对序号（只增不减），`position` = 实时秩（同场次 `WAITING` 中 `seq` 更小的条数 + 1），不写库、读时计算；6.2 补同一条目；M3 加断言「A 转正后 B 显示第 1 位」。 |

### 🟡 建议（9 条，全部采纳）

| # | 建议 | 处理结果 |
| --- | --- | --- |
| ① | `GET /my/bookings` 的 upcoming/past 边界未定义 | **已补**。5.4 定义 `tab=upcoming` = `session.start_at > server_time`（已取消的未来预约仍留在 upcoming，与 design 5.6 一致），`past` 反之；排序列明；非法 `tab` → 10206。 |
| ② | 名单导入重复手机号应「去重合并不报错」 | **已补**。5.7 写明「同一手机号重复出现 = 去重合并，不算非法行；其余非法行整批拒绝」，M0 加验证项。 |
| ③ | `waitlist_count` 应只统计 `WAITING` | **已补**。5.3 课表与详情出参注释显式声明；附录备注 3 同步。 |
| ④ | 递补循环内对 SKIPPED 发通知应移到事务提交后 | **已改**。6.2 伪代码改为收进 `skipped` 列表，提交后再发送，并附「所有外部调用都在事务提交之后（与 ADR-5 一致）」。 |
| ⑤ | 迟到预约拿不到开课提醒 | **已注明**。6.3 开课提醒行补说明；新增 T7（不做补发；若要覆盖需新增模板，属范围变更）。 |
| ⑥ | 「名单冻结」与「取消即时剔除」表述易混 | **已统一**。5.6 改为「`start_at - 30min` 后名单**不再新增**，但会员取消仍**实时剔除**；`list_frozen=true` 只表示不再新增」，并注明这是 PRD 7.6 与 7.2 的统一解释。 |
| ⑦ | 任务要求含「课程」表，方案内联在 session | **已注明**。4.1 新增 bullet：课程信息内联 `session`（与 PRD 10.1 一致），并给出未来抽 `course` 表的可逆迁移路径。 |
| ⑧ | 5.8 映射表缺页面、T1 应关闭 | **已改**。T1 标为已关闭；5.8 按 design 2.1 的 10 个页面层级 + 内部停课工具重写，并新增「弹窗文案来源」表；同时修复 10102 不带 `data` 导致 design 5.2 兜底页无法渲染的问题（5.2）。 |
| ⑨ | `days` 超范围、我的预约排序方向未定义 | **已补**。新增错误码 10206；5.3 写明 `date`/`days` 窗口约束（越界报错、不静默截断）；5.4 写明两个 Tab 的排序方向。 |

> 未采纳的意见：无。唯一需要说明的是 **建议 ⑥** 与 **意见 5** 都涉及「名单冻结 / FINISHED 只读」两组措辞：本方案选择「保留 PRD 原文用词 + 在工程侧给出唯一解释」而不是自行改写 PRD 口径，避免与产品文档出现两套说法；若 PM 后续需要调整 PRD 措辞，本方案 5.6/6.3 两处随之一并改。
> 另外，**教练端对象级鉴权**属 PM 在返工中新增的口径（超出 PRD v1.3 范围），已按 PM 要求在本方案先落地，并已请其补 PRD 变更记录（见附录备注 6、T6）。