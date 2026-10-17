# 测试计划：健身房团课预约微信小程序（MVP）

- 版本：v1.0（MVP 阶段，仅文档、不写代码）
- 作者：测试
- 上游依据：`docs/PRD.md`（v1.1，唯一需求依据）、`docs/tech-design.md`（v1.0，实现口径）、`docs/design.md`（v1.1，页面/文案口径）；参考 `docs/review-tech-design-r1.md`、`docs/review-tech-design-r2.md`（技术方案前两轮审查结论）
- 本文档是开发期测试的执行依据：功能、接口、并发/数据一致性、定时任务、通知降级、兼容性与真机、缺陷分级、回归范围、风险与改进建议。**本阶段只有文档，无运行产物**，因此所有用例标注"需运行验证"，验证方式与断言尽量引用 tech-design §8.1 已给出的可执行断言。

> 与工程师审查的交接约定：本文档按 **PRD v1.1 验收标准为唯一口径**编写，tech-design 中与 PRD 不一致或自相矛盾处，在本计划 §4 逐条列出并给出对测试执行的影响；其中 §4 的 \[P1\]-1 / \[P1\]-2 两条（候补上限计数口径、member 绑定口径）**在修复前对应用例（S6 上限类、S1 绑定类）无法可靠执行**，请工程师在进入开发前处理。

---

## 1. 测试范围

### 1.1 测试对象与依据

| 文档 | 版本 | 本计划中的角色 |
|---|---|---|
| `docs/PRD.md` | v1.1 | **唯一需求依据**。测试用例逐条映射 §5.2 各条 Given/When/Then 与 §5.3 R1–R7 |
| `docs/tech-design.md` | v1.0 | 实现口径。接口（§5.1–§5.5）、数据模型与状态机（§4）、关键难点（§6）、里程碑断言（§8.1） |
| `docs/design.md` | v1.1 | 页面状态、文案强制口径、参数化约定（`{取消截止}`/`{确认时限}`/`{预约截止}` 读生效值） |

### 1.2 要测

1. **功能（S1–S13）**：S1–S10 为 P0 全量；S11–S13 为 P1（含 CSV 兜底与降级路径）。
2. **接口（§5.1–§5.5）**：27 个端点（①～⑪、⑫～⑯、⑰～㉔、㉕㉖㉗）的入参/出参/错误码/幂等/越权；**30 条错误码**逐条可触发且文案可展示；通用约定（zod 校验、未知字段拒绝、服务器时间口径、限流）。
3. **数据一致性与并发**：超卖防护三道防线（原子 UPDATE + 部分唯一索引 + CHECK 约束）、名额锁定（`reserved_count` 与 `status='open'` 的 `waitlist_release` 一一对应）、候补 rank/position 分离、计数实时重算、状态机条件更新。
4. **定时任务（§6.7）**：outbox 发送（2s）、递补超时扫描（30s）、场次结算（5min）、展示缓存刷新/黑名单到期（1h）；重点验证**幂等与可重复执行**。
5. **通知与降级（§6.8）**：mock 微信三分支（成功 / `43101` 未授权 / 超时或 5xx）、确认时限延长至 `min(30min, start_at - now)`、站内红点 + 角标、锁定期间名额不释放。
6. **规则配置与降级开关（S10/§8.3）**：6 参数范围校验、场次级覆盖、30 秒缓存、`enable_ban / enable_waitlist / enable_late_cancel` 三个保险丝。
7. **权限与安全（R7）**：未登录、越权（会员互相看预约、教练点非本场、运营改非本店）、字段最小化（教练端不返回手机号）、注入类输入、未知字段、日志脱敏。
8. **兼容性与真机**：iOS/Android 微信、375/320/≥768pt、开发者工具、PC 微信、弱网/断网、订阅消息与手机号授权真机链路、`custom-tab-bar` 角色化导航、从微信通知进入的返回栈。
9. **性能**：S1"首页 3 秒内可交互"、S2"筛选 2 秒内更新"、课表 7 天接口 P95、开课前集中抢位行锁等待（tech §6.10 估算 < 10ms/事务）。
10. **埋点与指标口径（PRD §9）**：主流程跑完后 `event_log` 须出现 PRD §9 全部事件名；5 条指标 SQL（利用率/爽约率/递补成功率/确认率/点名完成率）口径可算。

### 1.3 不测（及原因）

| 不测内容 | 原因 |
|---|---|
| 支付/次卡/储值/退款、私教预约与佣金、课程评价、教练考勤薪酬排班、社交裂变、积分等级、站内消息中心完整版、短信/电话/App 推送、多门店通卡、H5/App/支付宝、课程视频直播、候补自动确认、扫码二维码签到 | PRD §3 明文"本期一律不实现"，不属于交付承诺；设计已把"签到码校验失败"映射为点名等价异常（design §6.6），按等价异常测 |
| P2 故事 S14–S17（自动确认开关、看板、自助申诉、迟到统计） | PRD §5.5 仅登记不做 |
| 微信订阅消息**真实**送达率与模板审核 | 依赖外部模板报备（PRD Q5，未就绪）；本期用 mock 微信接口测链路，真机实测标注"依赖模板就绪" |
| 教练本人到岗打卡（若 CEO 对 Q1 的答复是"教练本人打卡"而非"教练给会员点名"，则 S8 全部用例作废并重写） | PRD D1 当前口径 = 教练为会员点名；Q1 未最终确认，是测试前置风险（见 §4） |
| 完整门店管理后台（Web） | PRD §3 仅保留轻量排课入口（S11，CSV 兜底） |

### 1.4 测试层级与执行阶段（对应 tech-design §8.1 里程碑）

| 层级 | 内容 | 阶段 |
|---|---|---|
| 单元测试 | `domain/rules` 的 7 个纯函数：`remainingSlots`、rank、`cancelMode`、`confirmDeadline`、`bannedUntil(GREATEST)`、30 天窗口、时间冲突半开区间 | M0/M1 |
| 集成测试 | 事务流程与状态机（预约占位、取消→递补→确认/超时/放弃、结算判定、黑名单重算）、定时任务幂等、CHECK/唯一索引违反 | M3–M7 |
| 接口测试 | §5.1–§5.5 全部端点：参数、错误码、幂等、越权、限流 | M2–M8 |
| 并发/压力测试 | 超卖、抢位、递补竞态、行锁等待 | M3/M4 |
| UI/真机测试 | 页面状态与文案、真机授权链路、弱网、兼容矩阵、无障碍抽查 | M8 |

---

## 2. 重点与风险

| # | 风险点 | 为什么重要 | 测试方式 |
|---|---|---|---|
| R1 | **超卖 / 重复预约 / 重复释放**（数据一致性） | PRD §8.3 回滚条件第 1 条即"超卖或同一会员同一场次 2 条有效预约"，信任崩塌不可逆 | 并发脚本（50 并发抢 5 名额）、幂等用例、CHECK 违反 → critical 告警断言、唯一索引冲突码映射 |
| R2 | **候补递补状态机与名额锁定**（`reserved_count` ↔ open release 一一对应） | 候补是利用率提升的核心杠杆，也是最复杂逻辑，错一处就锁死名额或空转 | §3.1 专项时序/状态机用例 + 每日对账断言；锁定语义用例（他人不可抢） |
| R3 | **候补上限计数口径**（tech-design §6.3 与 §4.2/§4.5/§5.3⑧ 自相矛盾，r2 未修复） | 照 §6.3 实现会导致候补永久误判"已满"，候补价值归零 | 见 §4 \[P1\]-1；修复后跑 S6 上限用例 + 累计进队/出队回归 |
| R4 | **通知送达与降级**（一次性订阅、拒收） | 通知不到＝递补确认率归零，候补价值崩塌 | mock 微信三分支、时限延长（15→30）、红点角标、锁定不释放、失败率 >30% 告警 |
| R5 | **时间边界**（取消截止闭区间、点名窗口 -30/+15、确认时限 `min(15min, start_at)`、临期 20 分钟不递补、30 天窗口 `[now-30d, now)`） | PRD 验收标准几乎全是时间边界；差一秒行为翻转 | 服务器时间注入（now 可控）逐边界用例，含"恰好等于" |
| R6 | **教练不点名导致全体误判爽约** | 点名完成率仅 ≥90%，自动判定会误伤约 10% 场次全员 | 结算 SQL 三分支用例 + `pending_review` 全链路；严禁自动记爽约断言 |
| R7 | **黑名单误伤**（计数口径、撤销、顺延） | 误伤直接流失会员，PRD 设占比 ≤3% 监控 | 重算口径一致性（㉖ 与 403 同源）、撤销只改状态、`banned_until` 取 max 不缩短、边界 2/3/4 次 |
| R8 | **参数化文案写死**（15/20/120 等默认值） | 门店改配置后界面/通知数字与规则不一致（design §3/§9-10） | 把 `waitlistConfirmMinutes` 改成 30、`cancelDeadlineMinutes` 改成 240 后回归 B2/B3/B4 文案与模板 |
| R9 | **member 绑定/建行口径未定**（tech-design §4.1 vs §5.3② 矛盾，r2 未修复） | S1（P0）绑定永久失败或 500，账号体系无来源 | 见 §4 \[P1\]-2；修复前 S1 绑定类用例置为"阻塞" |
| R10 | **权限与字段最小化**（教练点非本场、运营改非本店、手机号泄漏） | R7 明文，隐私合规（Q6）；教练端返回手机号即 P0 安全缺陷 | 越权用例矩阵 + 接口字段断言（教练/会员接口不得含 `phone`） |
| R11 | **自定义 tabBar 角色化导航**（会员 3 tab / 教练 2 tab / 运营多分组） | 原生 tabBar 不支持动态角色，是 design 依赖项，写错则角色进错页面 | 真机逐一角色走查：登录返回 `roles` → tab 数量与页面权限一致；“以会员身份约课”切换 |
| R12 | **微信外部依赖未就绪**（模板报备 Q5、备案、排课数据） | 阻塞 S7 通知链路的真机验收与课表验收 | 未就绪前按"站内降级"口径验收并标注；CSV 导入兜底课表 |
| R13 | **埋点缺失 → 4 周后无法复盘** | PRD §2 指标、§9 看板全部依赖 `event_log` | 主流程冒烟后断言事件名全集；5 条指标 SQL 跑通 |

---

## 3. 测试用例

> 约定：默认测试数据 = 门店 A（`store_id=1`, active）、默认规则（`cancel_deadline=120`、`waitlist_confirm=15`、`ratio=1.0`、`threshold=3`、`ban_days=7`、`booking_deadline=0`）；场次 X = 今天 19:00–20:00、容量 20、教室容量 20；会员 m1..mn 已绑定、教练 c1 属门店 A、运营 staff1 属门店 A。**服务器时间可注入（now 可控）**；所有时间断言以服务器时间为准。优先级：P0 = 关键路径/验收标准，P1 = 重要边界/异常，P2 = 次要。

### 3.1 S1 微信登录与身份绑定

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S1-01 | S1 首次授权 | m 首次打开，点击「允许」授权，code → ① | 服务端以 openid 创建/匹配 member，返回昵称头像、`roles`、`defaultStoreId`；首页 3 秒内可交互（性能断言） | P0 |
| TC-S1-02 | S1 未绑定可浏览 | openid 未匹配到有效会员，进入首页 | 「未绑定门店会员」状态；**可浏览课表与详情**（走 ㉕→③ 默认门店回退链）；预约/候补按钮置灰，点击提示"请联系前台绑定会员手机号" | P0 |
| TC-S1-03 | S1 拒绝授权 | 用户拒绝授权进入首页 | 可浏览课表与详情但不可预约；页面顶部有「去授权」入口 | P0 |
| TC-S1-04 | S1 手机号绑定成功 | 门店已录入 m1 手机号；m1 授权手机号（②） | 匹配成功 → `bind_status='bound'`，预约按钮立即可用，不需重启 | P0 |
| TC-S1-05 | S1 静默登录 | 已绑定会员再次打开 | 不再弹出授权，静默登录（`wx.login` 无感重登，JWT 7 天过期自动续） | P0 |
| TC-S1-06 | S1 边界：手机号被两 openid 绑定 | 门店预录手机号 138…，openidA 已绑；openidB 授权同号 | 第二个返回 `409 PHONE_ALREADY_BOUND`，提示"该手机号已被绑定，请联系前台"；`UNIQUE(phone) WHERE phone IS NOT NULL` 兜底（23505 映射） | P0 |
| TC-S1-07 | S1 手机号未录入 | m 授权 138…（门店无此号） | `MEMBER_UNBOUND`（"该手机号未被门店录入，请联系前台绑定"） | P0 |
| TC-S1-08 | S1 重复绑定幂等 | 已绑定会员再次走 ② | 返回当前状态成功（bound），不报错、不产生重复行 | P1 |
| TC-S1-09 | S1 越权 | openidA 请求 openidB 的 booking/credit | `BOOKING_NOT_FOUND`/`AUTH_FORBIDDEN`（查询强制 `member_id=jwt.sub`） | P1 |
| TC-S1-10 | **前置阻塞**：预录会员建行 | 运营录入会员手机号到 `member` 表 | **依赖 tech-design §4.1 与 §5.3② 口径统一（§4 \[P1\]-2）**；按修复后口径断言"预录成功 / 绑定时 openid 回填规则成立"，不得出现 500 | P0 |

### 3.2 S2 查看课表与课程详情

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S2-01 | S2 默认 7 天 | 打开首页（③，`dateFrom/dateTo` 缺省） | 默认今天起 7 天（今天+6），按 `start_at` 升序；7 天窗口边界（今天+6 的最后一场在内，第 7 天之后的场次不在） | P0 |
| TC-S2-02 | S2 卡片字段 | 渲染课程卡片 | 至少含：课程名、开始—结束时间、时长、教练姓名与头像、门店名、教室名、剩余名额/容量（3/20）、状态标签 | P0 |
| TC-S2-03 | S2/R1 剩余名额公式 | 构造 `occupied=15, reserved=2, capacity=20` | `remaining = 3`；**课表、详情、预约接口三处同一函数**（单元测试断言同源；客户端不再自算） | P0 |
| TC-S2-04 | S2 已约/候补标签 | m1 已约场次 X（`myState=booked`）；m2 候补中（`myState=waitlist`） | m1 显示"已约"；m2 显示"候补中（第 N 位）"，N = 实时 rank（见 R2，非存储 `position`） | P0 |
| TC-S2-05 | S2 已满 | `remaining=0`（含全部被锁定） | 状态"已满"，主按钮变「排队候补」；对锁定名额的场次**不暴露**"有 1 个名额被锁定" | P0 |
| TC-S2-06 | S2 已开课 / 已取消 | now ≥ `start_at`；场次 `status=canceled` | 已开课不可预约（置灰"已开课"）；已取消显示"已取消"置灰 | P0 |
| TC-S2-07 | S2 详情字段 | 进入详情页（④） | 含简介、难度、器材、教练简介、门店地址与营业时间、容量、已约人数、候补人数、完整规则文案（`{取消截止}`/`{确认时限}`/爽约规则，**读生效值**）、主按钮 | P0 |
| TC-S2-08 | S2 筛选与空态 | 切换日期/按门店或类型筛选 | 2 秒内更新；空结果展示"这天还没有团课"；非法 `dateFrom/dateTo`（跨度 >31 天、from>to）返回 `INVALID_PARAM` | P0 |
| TC-S2-09 | S2 多门店 | m1 有 `home_store_id=1` | 默认选中门店 1，可手动切换门店（㉕ 数据源） | P1 |
| TC-S2-10 | S2 边界：已结束课程 | 早于当前时间的已结束场次 | 不出现在课表（出现在"我的预约-历史"） | P0 |
| TC-S2-11 | S2 网络失败 | 加载课表断网 / 500 | 错误态 +「重试」按钮，**不清空已加载内容**；重试成功后增量刷新 | P0 |
| TC-S2-12 | S2 数据源一致性 | 运营排课最后更新时间字段 | 课表展示"最后更新时间"（PRD §7.1） | P2 |

### 3.3 S3 预约团课

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S3-01 | S3 正常预约 | `remaining≥1`，m1 点「立即预约」（⑤） | 单事务核验容量并占 1 名额，返回成功；`remaining` 立即 -1；按钮变"已约" | P0 |
| TC-S3-02 | S3 边界：最后一个名额 | `occupied=19, capacity=20, reserved=0`，m1 预约 | 成功，`occupied=20, remaining=0`，场次变"已满" | P0 |
| TC-S3-03 | S3 并发：超卖防护 | **50 并发抢 5 个名额**（m1..m50） | 恰好成功 5 人、`occupied_count=5`、无 CHECK 违反、无同会员双条；败者 `SESSION_FULL` + `details.canJoinWaitlist=true` | P0 |
| TC-S3-04 | S3 已满引导候补 | 并发败者 | 失败提示"名额刚被约满，要加入候补吗？"，按钮变「排队候补」 | P0 |
| TC-S3-05 | S3 重复预约 | m1 已约场次 X，再次预约 | `409 DUPLICATE_BOOKING`"你已预约该课程"；**不产生第二条记录**（部分唯一索引 `(session_id, member_id) WHERE status IN ('booked','attended','no_show','excused','pending_review')` 兜底），`occupied_count` 不变 | P0 |
| TC-S3-06 | S3 取消后重约 | m1 曾取消场次 X（`canceled`），再预约 | 允许并**新建**一条 booking（不复用原记录）；原记录保留；数据库可见 2 条（1 条 canceled + 1 条 booked） | P0 |
| TC-S3-07 | S3 时间冲突 | m1 已约 9:00–10:00，预约 9:30–10:30 | `409 TIME_CONFLICT` + `details.conflictWith`（课程名+时间段）；R6 半开区间：前一节结束=后一节开始（9:00–10:00 与 10:00–11:00）**不**冲突 | P0 |
| TC-S3-08 | S3 黑名单 | m1 `banned_until>now` | `403 MEMBER_BANNED` + `details.bannedUntil`；提示"预约权限已暂停至 YYYY-MM-DD HH:MM"+【联系门店】 | P0 |
| TC-S3-09 | S3 未绑定 | m 未绑定，点预约 | `MEMBER_UNBOUND`，引导绑定（按钮本就置灰） | P0 |
| TC-S3-10 | S3 截止时间 | `booking_deadline=0`：`now ≥ start_at` 点预约；`booking_deadline=120`：`now = start_at-119min` 点预约 | `409 SESSION_STARTED`；客户端文案按 `stateTag/denyReason` 区分"已过预约截止时间（开课前 N 分钟）"与"已开课"（design §6.7，**不得**误写"已开课"） | P0 |
| TC-S3-11 | S3 门店停用 | `store.status=inactive` | `STORE_INACTIVE`"该门店暂不可约课" | P1 |
| TC-S3-12 | S3 幂等：重复提交 | 双击/断网重试同一预约请求 | 最多 1 条有效 booking、`occupied` 只 +1；客户端最终状态与服务端一致（部分唯一索引为主，`clientRequestId` 仅追踪） | P0 |
| TC-S3-13 | S3 已锁定名额不可直接约 | 场次有 `open` release（`reserved=1`），普通会员直接预约 | 原子占位 SQL（`occupied+reserved < capacity`）不成立 → `SESSION_FULL`，**不得抢占锁定名额** | P0 |
| TC-S3-14 | S3 注入/未知字段 | 请求带 `category=<script>…`、booking 请求多传未知字段 | zod `strict()` 拒绝未知字段（400）；参数全部参数化，无 SQL 注入、无 XSS 渲染 | P1 |
| TC-S3-15 | S3 行为一致性 | 同一会员并发预约两场互为冲突的课 | 会员行锁串行：最多只有一场成功（后提交的 `TIME_CONFLICT`） | P1 |

### 3.4 S4 我的预约

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S4-01 | S4 分组与排序 | m1 有进行中 2 条、历史 3 条 | 「进行中」= 未开始+进行中，按 `start_at` 升序；「历史」= 已结束/已取消/已爽约/候补未成功，按 `start_at` 降序 | P0 |
| TC-S4-02 | S4 分页 | 历史 21 条，`pageSize=20` | 第 1 页 20 条 + `hasMore=true`，第 2 页 1 条；`scope` 缺省/非法参数 400 | P0 |
| TC-S4-03 | S4 卡片字段 | 进行中预约 | 课程名、日期与时间、教练、门店与教室、距开始（"还有 2 小时 15 分"，<1h 显示"还有 45 分钟"，已开课显示"进行中"）、状态标签 | P0 |
| TC-S4-04 | S4 取消/请假入口切换 | 未过截止 → `cancelMode=ontime`；已过截止未开课 → `late`；已开课 → `none` | ontime 显示「取消预约」；late 显示「我要请假」+ 文案"已过取消截止时间，请假将记录 1 次晚取消（不计爽约），名额立即释放"；none 无按钮"已开课，等待教练点名结果"；`cancelMode` 由服务端算，客户端不得本地判断 | P0 |
| TC-S4-05 | S4 候补态位次 | `myState=waitlist`，rank=2 | 显示候补**实时位次** + "有名额释出时会通过微信通知你，请在 `{确认时限}` 分钟内确认（名额会为你锁定至确认截止时间）"（`{确认时限}` 读生效值，**不得写死 15**） | P0 |
| TC-S4-06 | S4 待确认递补 | `myState=pending_confirm`（entry `notified`） | 显示倒计时至 `confirmDeadlineAt`（服务端时间校准）+【确认参加】【放弃】 | P0 |
| TC-S4-07 | S4 边界：候补已失效 | entry `expired/declined/canceled_auto` | 下次刷新后从「进行中」移除，历史显示"候补未成功"（`waitlist_failed`） | P0 |
| TC-S4-08 | S4 空状态 | 无任何记录 | "还没有预约，去看看课表" + 可跳转课表 | P0 |
| TC-S4-09 | S4/R5 `pending_review` 文案 | booking `status=pending_review` | 显示"已结束 · 到场状态待门店确认"，**禁止**显示"已爽约"、不扣减计数 | P0 |
| TC-S4-10 | S4 越权 | 请求他人 bookingId 的取消/详情 | `BOOKING_NOT_FOUND`（404，不泄漏存在性） | P1 |

### 3.5 S5 取消 / 请假

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S5-01 | S5 正常取消 | `now = start_at-180min`（>120），确认取消（⑦） | `status=canceled`、`is_late_cancel=false`（`cancelType=ontime`）、名额立即释放、不计爽约不计次数；提示"已取消，名额已释放" | P0 |
| TC-S5-02 | S5 **边界：截止临界** | `now 恰 = start_at-120min` | 闭区间 → 判为**正常取消**（ontime，无处罚） | P0 |
| TC-S5-03 | S5 请假 | `start_at-120min < now < start_at`（如 now=start_at-60min） | 二次确认弹窗明文"此操作将记录 1 次晚取消，**不计爽约**；名额立即释放"；确认后 `late_canceled`、记 1 次晚取消、不记爽约、**不触发任何预约权限限制**；提示"已请假，名额已释放（记录 1 次晚取消）" | P0 |
| TC-S5-04 | S5 文案红线 | 走查全部相关文案 | 请假弹窗/后置提示**只**可出现"记录 1 次晚取消 / 不计爽约 / 名额立即释放"；**禁止**"记爽约""累计 N 次暂停预约权限"（R4，前端+接口 message 全查） | P0 |
| TC-S5-05 | S5 已开课不可取消 | `now ≥ start_at` 请求取消 | `409 SESSION_STARTED`；客户端无取消/请假按钮 | P0 |
| TC-S5-06 | S5 取消触发递补 | 场次有候补队列，m1 正常取消 | 事务内联触发递补：`release=open`、`reserved=1`、队首 `notified`、outbox 1 条（3 秒内 worker 置 `sent`） | P0 |
| TC-S5-07 | S5 临期空位 | 取消时 `start_at-now < 20min`（`confirmMinutes+5`） | **不发起递补**：不建 release、不锁定、名额保持空闲可约，运营列表标"临期空位"（返回 `promotionSkippedReason=TOO_CLOSE_TO_START`） | P0 |
| TC-S5-08 | S5 退出候补 | 候补中点击「退出候补」（⑨） | entry `status=canceled`，**`position` 不改**；后续会员可见位次（rank）自动前移；不计任何处罚 | P0 |
| TC-S5-09 | S5 退出后重新排队 | 退出后再加入 | 允许，取得**新的更大 `position`**；部分唯一索引 `WHERE status IN ('waiting','notified')` 不阻断新记录 | P0 |
| TC-S5-10 | S5 取消后可重约 | 取消后再次预约 | 满足 S3 全部条件则允许（取消不封号） | P0 |
| TC-S5-11 | S5 门店取消整场 | 运营 ㉓ 取消场次 | 全部有效预约 → `canceled_by_store`、不计爽约不计次数、候补清空（`canceled_auto`）、open release 置 `expired`、`reserved_count=0`、受影响的 booking+候补全员通知 | P0 |
| TC-S5-12 | S5 边界：重复取消幂等 | 同一取消请求发两次 | 只产生 1 条取消记录、只释放 1 个名额（`WHERE status='booked'` 条件更新，affected=0 时返回当前状态）；**不得重复释放导致超卖** | P0 |
| TC-S5-13 | S5 竞态：取消 vs 递补 | 取消请求与递补确认同一名额并发 | 以先到服务端事务为准；败方提示"操作未生效，名额状态已变化，请刷新"；无超卖 | P0 |
| TC-S5-14 | S5 晚取消计数监控 | 产生 1 次 `late_cancel` | `late_cancel_count_7d` 记账（仅监控）；**MVP 不启用惩罚**（§6.5），界面/文案无惩罚表述 | P1 |
| TC-S5-15 | S5 降级开关关闭晚取消 | `enable_late_cancel=false`，已过截止 | 截止后无【我要请假】，恢复硬截止（PRD §8.3 开关 ③） | P1 |

### 3.6 S6 满员后加入候补

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S6-01 | S6 正常入队 | `remaining=0`、`now<start_at`、队列 0 人，m1 点候补（⑧） | 创建 `waitlist_entry`：`waiting`、`position=1`（=max+1）；页面显示"已加入候补，第 1 位"（实时 rank） | P0 |
| TC-S6-02 | S6 边界：候补第 1 位 | 队列中第 1 位（`position` 最小） | rank=1；名额释放时**先**被通知（FIFO 取队首） | P0 |
| TC-S6-03 | S6 边界：候补末位/上限 | 容量 20 → `waitlist_capacity=20`；第 21 人加入 | `409 WAITLIST_FULL`"候补已满，可在开课前再来看看"；上限按 `status∈{waiting,notified}` **实时记录数**计（**依赖 §4 \[P1\]-1 修复**，不得用历史入队次数） | P0 |
| TC-S6-04 | S6 已在候补 | m1 已入队再点候补 | `409 WAITLIST_DUPLICATE` + `details.rank`"你已在候补队列第 N 位"，不重复入队 | P0 |
| TC-S6-05 | S6 已有预约 | m1 已约该课再点候补 | `DUPLICATE_BOOKING`"你已预约该课程"；进行中候补不能与 `booked` 并存（R2） | P0 |
| TC-S6-06 | S6 订阅消息引导 | 提交入队成功 | MVP 强制引导订阅消息授权；可跳过，但页面标注"不授权将无法及时获知递补"；授权结果写 `subscription_message_log`（S12 对账） | P0 |
| TC-S6-07 | S6 黑名单 | m1 黑名单期点候补 | `MEMBER_BANNED`，提示同 S3 | P0 |
| TC-S6-08 | S6 时间冲突 | m1 入队时已有时间重叠的有效预约 | `409 TIME_CONFLICT`（候补也受 R6 约束——候补成功＝会占位） | P0 |
| TC-S6-09 | S6 已开课 | `now ≥ start_at` 点候补 | `WAITLIST_CLOSED`"已开课，候补已关闭" | P0 |
| TC-S6-10 | S6 边界：同一秒并发入队 | 两会员同一秒入队（并发请求） | 场次行锁串行 → `position` 严格唯一、无重复（`UNIQUE(session_id, position)` 恒成立） | P0 |
| TC-S6-11 | S6 position 只增不改 | A(position=5) 退出、B(position=6) 仍在 | B 的 `position` 不变，可见 rank 从 2 → 1（实时重算，**不重排 position**） | P0 |
| TC-S6-12 | S6 门店取消 | 场次取消，队列有 `waiting` | 全部 `canceled_auto` 并通知队列会员（S5-11 覆盖字段级断言） | P0 |
| TC-S6-13 | S6 降级开关关闭候补 | `enable_waitlist=false`，满员 | 满员不可候补，按钮禁用"已满，无法候补"（PRD §8.3 开关 ②） | P1 |

### 3.7 S7 候补自动递补与通知确认（含专项时序/状态机，见 §3.1）

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S7-01 | S7 释放→锁定→通知 | 队列 [a(1), b(2)]，m_cancel 取消 | **5 秒内**（系统侧发起，worker 2s 轮询）：① 建 `waitlist_release(open, try_count=1)` + `reserved_count+1`（按 R1 公式 `remaining = capacity - occupied - reserved`：取消使 `occupied` −1、锁定使 `reserved` +1，故满员场次的 `remaining` 仍是 0、其他会员见〔已满〕且预约得 `SESSION_FULL`；PRD S7 原文"剩余名额同步减 1"的措辞见 §4 \[P2\]-5）；② 队首 a 置 `notified`，写 `notified_at`、`confirm_deadline_at = min(notified_at+15min, start_at)`、`current_entry_id=a`、outbox 1 条；③ 发微信订阅消息 | P0 |
| TC-S7-02 | S7 锁定语义 | release 为 open 期间，其他会员（含刚取消的原会员）查看课表/点预约 | 「剩余名额」已扣除锁定名额，直接预约失败并引导候补；**不得抢占锁定名额** | P0 |
| TC-S7-03 | S7 确认成功 | a 在期限内点「确认参加」（⑩） | release → `filled`、`occupied+1 / reserved-1`、entry → `confirmed` 并落地 `booking(source=waitlist, status=booked)`；**不**向 b 发通知；`remaining` 恒等于 `capacity-occupied-reserved` | P0 |
| TC-S7-04 | S7 确认时名额已被占用 | a 点确认但 release 已 `filled/abandoned`（其他途径占用） | `WAITLIST_ENTRY_STATE_CONFLICT`"名额已被占用，本次候补未成功"；entry → `expired`；有下一顺位则同事务顺延，无则释放锁定 → 空闲 | P0 |
| TC-S7-05 | S7 超时顺延 | a 超时未操作，定时任务扫描（30s） | a → `expired`；名额**仍锁定在同一 release 上**（`reserved_count` 不变）、`try_count+1`、b 置 `notified` 并通知；`confirm_deadline_at` 重新 = min(now+15, start_at) | P0 |
| TC-S7-06 | S7 放弃 | a 在期限内点「放弃」（⑪） | entry → `declined`；**不计爽约、不计晚取消**（D7）；有下一顺位则顺延（`try_count+1`），无下一顺位则 release → `abandoned`、`reserved_count-1` | P0 |
| TC-S7-07 | S7 队列空 | 满员场次有人取消，且该场次队列无 `waiting`（取消前队列本就为空） | **不创建 release、`reserved_count` 不变**（tech §6.4 步骤 4、§6.5、§5.5⑳）：取消后 `occupied` −1，`remaining` 由 0 → 1，其他会员**可直接预约成功**；接口返回 `promotionSkippedReason=NO_WAITLIST`；`event_log` 记 `slot_left_vacant` | P0 |
| TC-S7-08 | S7 已开课收尾 | `now ≥ start_at` 仍有 `waiting/notified` | 全部置 `expired`；所有 `status=open` release → `expired`、`reserved_count` 归零；候补关闭 | P0 |
| TC-S7-09 | S7 临期不递补 | 释放时 `start_at-now < 20min` | 不创建 release、不锁定，名额标"临期空位"（S5-07 复用） | P0 |
| TC-S7-10 | S7 连续 3 次无人确认 | 同一 release 内 a 超时、b 超时、c 超时 | `try_count` 1→2→3：第 3 次后 **不再继续递补**，release → `abandoned`、`reserved_count-1`、标"临期空位"并记日志；`try_count` 按**单个 release**计数（新释放事件从 0 起） | P0 |
| TC-S7-11 | S7 通知失败降级 | mock 第三方返回 `43101`（未授权）/ 5xx 重试 4 次失败 | 记 `subscription_message_log.fail/unauthorized`；站内红点 + 「我的预约」角标；**确认时限延长为 `min(30min, start_at-now)`**；锁定期间名额不释放给他人 | P0 |
| TC-S7-12 | S7 通知内容 | 递补通知渲染（订阅消息+站内状态） | 明确含：课程名、门店、开课时间、`confirmDeadlineAt`、确认入口；B4 显示"名额已为你锁定至 XX:XX，课表上的剩余名额已同步扣除" | P0 |
| TC-S7-13 | S7 递补成功后取消 | 递补确认成功后马上取消 | 适用 S5 全部规则（按确认后剩余时间）；剩余时间不足正常取消 → **允许请假且仅记晚取消，不记爽约** | P0 |
| TC-S7-14 | S7 边界：确认 vs 直接预约并发 | 同一名额，a 确认与 m_x 直接预约并发 | 竞争同一名额，先提交事务者胜；另一方失败并给明确文案；**绝不超卖** | P0 |
| TC-S7-15 | S7 边界：多名额同时释放 | 队列 [a,b]，加容量 +2 | 逐个串行递补：两个独立 release、各自 `try_count` 独立、a/b 各被通知一次、`reserved_count` = open release 数（=2） | P0 |
| TC-S7-16 | S7 确认时已开课 | `now ≥ start_at` 时点确认 | ⑩ 顺序校验①：release → `expired`、entry → `expired`、`reserved-1`、`409 WAITLIST_CLOSED`（**依赖 §4 \[P1\]-3 §6.4 与 §5.3⑩ 同步**） | P0 |
| TC-S7-17 | S7 确认时已入黑名单 | 入队后被禁，点确认 | ⑩ 顺序校验②：entry → `expired` 并顺延下一位、`403 MEMBER_BANNED`（R5 惩罚范围含"不可递补确认"） | P0 |
| TC-S7-18 | S7 确认时时间冲突复检 | 入队后又约了时间重叠的课，确认时发现 | `409 TIME_CONFLICT` + 文案"已为你把名额顺延给下一位"；entry → `expired`、release 回 `open` 并顺延（PRD §9-Q7 / tech §9-Q7 口径；**依赖工程师实现**） | P1 |
| TC-S7-19 | S7 幂等：重复确认/重复放弃 | 同一 entry 确认两次 | 条件更新 `WHERE status='open'` 裁决：第二次 `WAITLIST_ENTRY_STATE_CONFLICT`；`occupied` 只 +1 一次 | P0 |

#### 3.7.1 候补递补时序专项（时间轴用例）

场景：容量 20、默认规则（15 分钟确认、20 分钟临期）。**T0 之前场次已满**：`occupied=20`、`reserved=0`，队列 `[a(1), b(2), c(3)]` 均已 `waiting`——按 PRD S6，只有"剩余名额 = 0 且 `now < start_at`"才能入队，所以队列非空必然以"已满"为前置，不能与 `occupied=19` 并存。T0 会员 m_cancel 取消后：`occupied=19`、`reserved=1`、队列 3 人不变。

> 时刻精度：递补超时扫描周期 30 秒，下表时刻存在 ±30 秒误差，断言一律以"该次扫描执行后"的状态为准。

| 时刻 | 动作 | `waitlist_release` | `reserved_count` | entry 状态 | 通知 | 断言 |
|---|---|---|---|---|---|---|
| T0 | m_cancel 取消 | R1：open, try=1, current=a | 0→1 | a=notified | outbox 1 条（a） | 按 R1 公式 `remaining = 20-19-1 = 0`（取消前也是 0：取消使 `occupied` −1、锁定使 `reserved` +1，**不产生额外的 -1**）；其他会员点预约得 `409 SESSION_FULL`、卡片显示〔已满〕 |
| T0+5min | a 未操作 | R1：open, try=1 | 1 | a=notified | — | 倒计时还剩 10min；`confirm_deadline_at` 未变 |
| T0+15min | 超时扫描 | R1：open, try=2, current=b | 1（**不变**） | a=expired, b=notified | outbox 1 条（b） | 名额仍锁在同一 release |
| T0+17min | b 点「放弃」 | R1：open, try=3, current=c | 1 | b=declined, c=notified | outbox 1 条（c） | b 不计任何处罚 |
| T0+32min | c 超时（`try_count` 达 3：三次通知 a→b→c 均未确认） | R1→**abandoned** | 1→0 | c=expired | event_log 记 `slot_left_vacant` | **不是 T0+25min**：c 于 T0+17min 被通知，`confirm_deadline_at = min(T0+17min+15min, start_at) = T0+32min`；到点后名额恢复空闲可约（`remaining = 1`）、运营端"临期空位"高亮 |

变体 A（确认成功路径）：a 在 T0+10min 确认 → R1 `filled`、`occupied 19→20`、`reserved 1→0`、b/c 保持 `waiting`；不再发通知。
变体 B（通知未送达）：T0 时 mock `43101` → a 的 `confirm_deadline_at` 由 worker 事后条件更新为 `min(now+30min, start_at)`，红点+角标，锁定不释放。
变体 C（释放时已临期）：T0 时 `start_at-now = 18min` → **不建 release**、`reserved` 不变、名额空闲可约、`promotionSkippedReason=TOO_CLOSE_TO_START`。
变体 D（开课早于 c 的确认截止，即 `start_at ≤ T0+32min`）：结局**不是** `abandoned`——c 的 `confirm_deadline_at` 被 `start_at` 截断，`start_at` 到点后的超时扫描（`s.start_at <= now` 分支）把 R1 置 `expired`、c 置 `expired`、`reserved_count - 1`，该名额**作废、不可再约**（PRD S7"开课后释放出的名额不可再约"），候补关闭。因此"名额恢复空闲可约"只在 `abandoned`（未开课）分支成立。

#### 3.7.2 状态机覆盖矩阵

| 实体 | 状态 → 目标 | 迁移触发 | 用例 |
|---|---|---|---|
| `waitlist_entry` | waiting→notified | 取队首/顺延（§6.4） | TC-S7-01/05 |
| | notified→confirmed | 确认成功 | TC-S7-03 |
| | notified→declined | 主动放弃 | TC-S7-06 |
| | notified→expired | 超时/冲突/名额被占/开课 | TC-S7-05/04/16/18 |
| | waiting→canceled / canceled_auto | 会员退出 / 门店取消 | TC-S5-08 / TC-S6-12 |
| `waitlist_release` | open→filled | 确认成功 | TC-S7-03 |
| | open→abandoned | 顺延途中已无下一顺位 / `try_count` 达 3（开课分支走下行 `expired`，由 TC-S7-08/16 断言） | TC-S7-06/10（原 TC-S7-07 已按 tech §6.4 步骤 4 改为"队列空时不建 release"） |
| | open→expired | 开课/场次取消 | TC-S7-08 / TC-S5-11 |
| `booking` | booked→canceled/late_canceled/canceled_by_store | 取消/请假/门店取消 | TC-S5-01/03/11 |
| | booked→attended/no_show/pending_review | 结算（§6.7 SQL） | 见 3.9 S9 |
| `class_session` | scheduled→ongoing→finished；任意→canceled | 时间推进/门店取消 | TC-S2-06 / TC-S5-11 |

### 3.8 S8 教练上课前点名

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S8-01 | S8 教练登录 | 教练按手机号+邀请码绑定（㉗） | roles 含 `coach`、`coachId` 非空、JWT 重签；邀请码一项不符 → `INVITE_CODE_INVALID`；已绑其他身份 → `ROLE_ALREADY_BOUND` | P0 |
| TC-S8-02 | S8 我今天的课 | c1 进入（⑫） | 今天排期场次按 `start_at` 升序；每项含课程名、时间、门店、预约人数/容量、候补人数、点名状态 | P0 |
| TC-S8-03 | S8 窗口未开 | `now = start_at-31min` | 点名入口置灰"开课前 30 分钟开放点名" | P0 |
| TC-S8-04 | S8 **边界：窗口下沿** | `now 恰 = start_at-30min` | 进入窗口，可点名（tech ⑭ 闭区间 `start_at-30min ≤ now`） | P0 |
| TC-S8-05 | S8 名单字段 | ⑬ 名单 | 全部有效预约（含递补成功者），每行头像、姓名/昵称、预约时间、晚预约标记；默认全部「未标记」；**不含手机号**（R7） | P0 |
| TC-S8-06 | S8 标记已到/未到 | ⑭ 标记 present / absent | 生成/更新 `check_in(result=present/absent)`，写 `marked_at`、`marked_by`，行状态立即变更；`UNIQUE(booking_id)` 一次预约一条最终记录 | P0 |
| TC-S8-07 | S8 反复修改 | 先 absent 再 present | 可修改，以最后一次为准；`change_log` 追加变更记录（by/role/from/to/at） | P0 |
| TC-S8-08 | S8 迟到=present | 会员迟到，`start_at < now ≤ start_at+15min` 标记已到 | 记为 `present`（MVP 不区分迟到） | P0 |
| TC-S8-09 | S8 **边界：窗口上沿** | `now 恰 = start_at+15min` | 仍可标记（tech ⑭ 闭区间 `now ≤ start_at+15min`）；`now = start_at+15min+1s` → `ROSTER_WINDOW_CLOSED`，只读 | P0 |
| TC-S8-10 | S8 整场未点名 | 场次结束，**无任何 check_in** | 该场次全部有效预约 → `pending_review`：不置 `no_show`、不写 `no_show_marked_at`、不计数、不入黑名单；运营列表"未点名异常"高亮，可整场批量（全 present/全 absent/整场 excused） | P0 |
| TC-S8-11 | S8 部分未标记 | 场次有 ≥1 条 check_in，但部分预约未标 | 仅**明确标 absent** 的计 `no_show`；未标记者 → `pending_review`、**不自动计数**（不得因"场次已点名"推定未标记者=未到） | P0 |
| TC-S8-12 | S8 运营兜底 | staff1 进入点名页操作 | 可执行与教练相同操作；`marked_by_role='staff'`，操作日志记录实际操作人 | P0 |
| TC-S8-13 | S8 服务器时间 | 教练手机时间调快/调慢 | 一切窗口判定以服务器时间为准，客户端仅展示 | P0 |
| TC-S8-14 | S8 边界：名单变化 | 点名后有人取消/递补进来（isSupplement） | 名单自动刷新；新入会者标"补位加入"；已点名记录不丢失 | P0 |
| TC-S8-15 | S8 无预约场次 | 场次 0 预约 | 点名页"暂无预约"，不阻塞 | P0 |
| TC-S8-16 | S8 越权 | 教练 c2（非本场）请求 ⑬/⑭ | `ROSTER_FORBIDDEN`（`user_role.coach_id = session.coach_id`）；给非本场次 booking 标记 → 拒绝 | P0 |
| TC-S8-17 | S8 批量操作 | 【全部到场】/【全部未到】 | 只影响**未标记**行，不覆盖已修改行；二次确认；部分行非法 → 整批回滚并返回该 `bookingId` | P0 |
| TC-S8-18 | S8 幂等：重复标记 | 同一行重复 PUT | 结果一致（ON CONFLICT + change_log 追加），不产生多行 check_in | P1 |
| TC-S8-19 | S8 离线补交（P1） | 无网络标记多行 → 恢复网络 | 行内"待提交"→ 自动提交；失败明确提示"未提交成功，请重试"，**不得静默丢弃**；⑯ 服务端仍用服务器时间校验窗口 | P1 |
| TC-S8-20 | S8 运营处理未点名异常 | ㉑ 单条/整场 → attended/no_show/excused | 只允许 `pending_review` 记录；写入原因与审计（`resolve_unmarked`）；产生 no_show 则触发重算 | P0 |

### 3.9 S9 爽约判定与黑名单

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S9-01 | S9 结算三分支 | `end_at` 已过，预约状态 booked：A 标 present / B 标 absent / C 未标记 | A→`attended`；B→`no_show` + 写 `no_show_marked_at`（**不做增量计数**）；C→`pending_review`；结算 SQL 可重复执行（连跑 3 次结果相同） | P0 |
| TC-S9-02 | S9 整场未点名不计爽约 | 场次无任何 check_in | 全部 `pending_review`；`no_show_count_30d=0`；不触发黑名单（复用 TC-S8-10） | P0 |
| TC-S9-03 | S9 计数窗口边界 | 构造两端点：`end_at = now-30d`（恰在窗口内 `[now-30d, now)`）与 `end_at = now-30d-1s` | 前者计入、后者不计入；判定一律实时重算（**不读 `_cache` 列**） | P0 |
| TC-S9-04 | S9 阈值边界 | 30 天内 no_show 恰 2 次 / 恰 3 次 | 2 次不入黑名单；3 次 → `banned_until = now + 7d`、`ban_reason=no_show_3_in_30d` | P0 |
| TC-S9-05 | S9 黑名单期再爽约 | 黑名单期内又判 1 次 | `banned_until = max(原, 新判定时刻+7d)`（只延长不缩短）；更新 `ban_reason` 与通知文案 | P0 |
| TC-S9-06 | S9 进入黑名单的通知 | 触发黑名单 | 立即发送通知（原因、生效区间、解除时间、申诉方式）；**依赖 §4 \[P1\]-4 补 outbox**，否则该链路无落点；站内不可关闭提示条 + 按钮置灰显示剩余解除时间 | P0 |
| TC-S9-07 | S9 到期自动解除 | `banned_until ≤ now`，缓存刷新任务 | 自动解除（`banned_until=null`），会员可正常预约；计数按 `end_at` 锚点自然衰减实时重算，**不做手动清零** | P0 |
| TC-S9-08 | S9 撤销爽约 | 运营 ㉒ 撤销（`no_show→attended`） | **只改 `booking.status`** + 触发重算 → 计数自动 -1；**严禁任何"计数减 1"语句**（R5）；重算后 < 阈值 → 立即解除黑名单；写审计（`revoke_no_show`/`release_ban`） | P0 |
| TC-S9-09 | S9 边界：同一天两节未到 | 会员同一天两节课均 no_show | 记 **2 次**（按场次计数，不按天） | P0 |
| TC-S9-10 | S9 撤销后重算回退 | 开课后判 no_show，门店补记到课（㉒） | 只改状态 → 计数实时回退、黑名单状态重算 | P0 |
| TC-S9-11 | S9 黑名单期不涉及名额 | 黑名单期间名额释放 | 该会员（本无预约）不参与递补，无异常 | P1 |
| TC-S9-12 | S9 一约一到一未到 | 两场预约：A 到场、B 未到 | 仅 B 计爽约 | P0 |
| TC-S9-13 | S9 惩罚范围 | 黑名单期该门店预约/候补/递补确认、浏览课表、其他门店 | 本店：预约/候补/确认全拒（`MEMBER_BANNED`）；浏览课表不受影响；其他门店不受影响 | P0 |
| TC-S9-14 | S9 口径一致性 | ㉖ `noShowCount30d` vs 403 `MEMBER_BANNED` 判定值 | 同一会员同一时刻两者一致（都走实时重算）；`banEnabled=false` 时不显示红条、不禁用 | P0 |
| TC-S9-15 | S9 例外的场景不计爽约 | 门店取消 / 系统故障 excused / 递补后请假 / 开课前正常取消 / 整场未点名 | 五类均不产生 `no_show`、不计数、不触发黑名单 | P0 |

### 3.10 S10 门店规则配置

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S10-01 | S10 展示生效值 | ⑰ 加载 | 展示 6 参数当前生效值（默认 120/15/1.0/3/7/0） | P0 |
| TC-S10-02 | S10 范围校验 | `cancel_deadline_minutes=-1 / 2000`、`no_show_threshold_30d=1 / 6` | 越界 → `400 RULE_INVALID` + 取值范围提示；DB CHECK 作为最后防线 | P0 |
| TC-S10-03 | S10 不追溯 | 修改 `cancel_deadline=240` 后，已存在的预约 | 已发生/已显示状态不受影响；仅生效时间之后的新操作按新值 | P0 |
| TC-S10-04 | S10 审计 | 每次规则变更 | `audit_log` 记录变更人、before/after、时间、reason | P0 |
| TC-S10-05 | S10 场次级覆盖 | 场次 X 设 `waitlist_confirm_minutes=30`（店级 15） | 该场次确认时限 = 30；会员端规则文案读取场次生效值 | P0 |
| TC-S10-06 | S10 文案 1 分钟更新 | 修改参数后立即进 B2/B3/B4 | 30 秒缓存失效 → 会员端规则文案 **1 分钟内**更新（含 B4 说明、模板占位同步） | P0 |
| TC-S10-07 | S10 **边界：极端值** | `cancel_deadline_minutes` 设为 >7 天（覆盖整个发布窗口） | 保存返回 200 + `warning`"该设置将导致所有预约都无法取消"，需二次确认（`confirmed:true`） | P0 |
| TC-S10-08 | S10 参数化文案回归 | `waitlistConfirmMinutes=30`、`cancelDeadlineMinutes=240` | B1/B2/B3/B4 与订阅消息模板数字**全部同步**为 30/240（不得残留 15/120 写死；design §3/§9-10） | P0 |
| TC-S10-09 | S10 降级开关 | ③ 开关（enable_ban / enable_waitlist / enable_late_cancel）分别关闭 | 对应行为立即生效（30 秒缓存），无需发版；见 TC-S5-15 / TC-S6-13 / TC-S9-14 | P1 |
| TC-S10-10 | S10 越权 | staff of 门店 B 改门店 A 规则；普通会员调 ⑰/⑱ | `AUTH_FORBIDDEN` | P0 |

### 3.11 S11–S13（P1）

| 编号 | 需求条目 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-S11-01 | S11 排课/CSV 导入 | CSV 导入未来 7 天 100 场次 | 课表可见、字段正确；按周复制课表、单场新建/修改；取消需二次确认并触发会员通知 | P1 |
| TC-S11-02 | S11 名单导出 | 导出 CSV | 含会员昵称、预约时间、到场状态，可打开 | P1 |
| TC-S12-01 | S12 授权引导 | S3 预约后、S6 入队后引导一次授权 | 授权状态与 `subscription_message_log` 对账一致；未授权时 B3 顶部持续提示条 | P1 |
| TC-S13-01 | S13 加容量 | `delta=+2`、队列 2 人 | 每个新空位生成独立 release（try 从 0 起）逐个串行通知；`releasesCreated=2`、`notified=2`；无下一顺位的空位 → `vacantSlots`，**不建 release、不加 reserved** | P1 |
| TC-S13-02 | S13 减容量 | `delta=-1` 且 `occupied+reserved = capacity` | 条件更新 0 行 → `409 CAPACITY_CONFLICT`"需先取消预约或等待递补结果"；`23514`（CHECK 违反）也映射为该码，**不得**对用户报 500 | P1 |
| TC-S13-03 | S13 手动递补 | 运营把名额手动给某会员 | **依赖 §4 \[P1\]-5 补 ㉘ 接口**（PRD S13 要求记录操作人与原因、不得超容量）；修复前此用例不可执行 | P1 |

### 3.12 通用与全局用例（跨故事）

| 编号 | 范围 | 步骤与测试数据 | 预期结果 | 优先级 |
|---|---|---|---|---|
| TC-G-01 | 认证 | 未登录调任意业务接口 | `401 AUTH_REQUIRED`；客户端静默重登后重试一次；JWT 过期/篡改 → 401 | P0 |
| TC-G-02 | 越权矩阵 | 会员访问他人 booking/候补/credit；教练访问非本场；运营访问非本店 | 一律 404/403（不泄漏存在性）；`jwt.sub` 强制限定；教练端不返回 `member.phone` | P0 |
| TC-G-03 | 角色化导航 | 同一 openid 为 member+coach | ① 返回 `roles:['member','coach']`；tab 渲染 2 套；「以会员身份约课」只切视图不重签 token；只有 ㉗ 才重签 | P0 |
| TC-G-04 | 时间口径 | 客户端伪造时间/时区；DB 为 UTC 瞬时 + `TZ=Asia/Shanghai` | 一切判定用服务器时间；自然周 周一 00:00–周日 23:59:59（Asia/Shanghai）统计正确 | P0 |
| TC-G-05 | 幂等通用 | 同一写接口重复提交（预约/取消/候补/确认/点名） | 由唯一索引 + 状态机条件更新保证最多 1 次生效；`clientRequestId` 只做追踪 | P0 |
| TC-G-06 | 限流 | 同一会员 1 秒内并发多个写操作 / 同一 IP 高频 | `429 RATE_LIMITED`，按钮临时禁用 | P1 |
| TC-G-07 | 注入 | 昵称/课程名/筛选参数含 `<script>`、`' OR 1=1--`、超长字符串 | 参数化 SQL 无注入；渲染转义无 XSS；zod 限制长度 → 400 | P1 |
| TC-G-08 | 性能 | 首页首屏 3 秒可交互（S1）；筛选 2 秒内更新（S2）；课表 7 天 P95（3 门店量级） | 达标；开课前峰值 100 人抢 1 名额锁等待可接受（监控 `pg_locks`） | P1 |
| TC-G-09 | 定时任务幂等 | 同一结算/超时/缓存任务连跑 3 次 | 结果与跑 1 次相同（§6.7 幂等 SQL）；任务异常顶层 catch + 日志 + `/healthz` 暴露 `lastJobRuns` | P0 |
| TC-G-10 | 对账 | 每日对账 SQL | `reserved_count == count(waitlist_release WHERE status='open')`；`waitlist_count == count(status IN ('waiting','notified'))`（§4 \[P1\]-1 修复后）；漂移即 critical | P0 |
| TC-G-11 | 埋点 | 跑通主流程（登录→课表→预约→取消→候补→递补确认→点名→结算→黑名单→撤销） | `event_log` 出现 PRD §9 全部事件名；5 条指标 SQL 能出数 | P0 |
| TC-G-12 | 回滚演练 | 关闭 enable_ban / enable_waitlist / enable_late_cancel | 2 小时内可回滚到上一阶段（读库即时生效，无需发版）；演练记录 | P1 |

### 3.13 兼容性与真机矩阵

| 维度 | 覆盖项 | 用例要点 |
|---|---|---|
| 平台 | iOS 微信 8.0.x / Android 微信 / 开发者工具 / PC 微信 | 登录授权弹窗、`getPhoneNumber`、订阅消息授权、`custom-tab-bar`（2/3 tab）、`safe-area-inset-bottom`、返回栈 |
| 屏幕 | 320pt（SE）/375pt（基准）/≥768pt（平板/PC） | 日期条 7 天一行/滚动、底部操作栏不遮卡片、关键信息不 `ellipsis` 截断 |
| 弱网 | 无网/2G/抖动 | 课表错误态+重试保留已加载；写操作 loading+5s 超时+明确失败；点名离线暂存（P1）；断网重试不产生重复数据 |
| 通知进入 | 递补通知→B4、黑名单通知→B5、课程取消通知→B3 | `redirectTo`/兜底 `switchTab` 返回 B1；倒计时显示不因刷新失败而误判过期 |
| 无障碍抽查 | 对比度（4.5:1）、44px 热区、焦点顺序、读屏 | 标签色实测值 ≥ 4.5:1（design §7.1）；禁用控件必须配原因文案 |
| 深色模式 | 不做 | 仅浅色主题；确认不因系统深色出现不可读（design §4.1） |

---

## 4. 评审发现的问题（缺陷分级与对测试执行的影响）

> 依据：PRD v1.1（唯一口径）+ tech-design v1.0 + design v1.1。前两轮技术审查（r1/r2）已给出总体结论 **修改后能交付**；以下把与**测试执行直接相关**的问题列出（含 r2 未修复项与本计划新发现项）。严重程度：P0 阻塞 / P1 严重 / P2 一般 / P3 轻微。

| # | 严重程度 | 位置 | 实际情况 | 期望情况 | 依据 | 修改建议 |
|---|---|---|---|---|---|---|
| \[P1\]-1 | **P1（阻塞 S6 上限类用例）** | tech-design §6.3 入队 SQL vs §4.2/§4.5/§5.3⑧ | §4.2/§4.5/§5.3⑧ 声明"上限按 `{waiting,notified}` 实时记录数判定、`waitlist_count` 仅展示"，但 §6.3 唯一给出的 SQL 仍是 `UPDATE class_session SET waitlist_count=waitlist_count+1 WHERE waitlist_count < $waitlistCapacity`；确认成功/超时置 expired/门店取消整场三条离开路径无递减 | 全文一种口径，且所有离开路径一致 | PRD R2「候补上限…按 `status ∈ {waiting, notified}` 的记录数计」；PRD S6 上限提示 | 按 r2 结论二选一：① 改实时 `count` 判定（与 rank SQL 同源）；② 补齐 5 条递减路径 + 每日对账任务。**修复前 TC-S6-03/TC-G-10 不可执行** |
| \[P1\]-2 | **P1（阻塞 S1 绑定类用例）** | tech-design §4.1 `member`（`openid NOT NULL UNIQUE`）vs §5.3②（"匹配到且 `openid` 为空 → 写入 openid"）；② 引用不存在的 §9-Q10 | 门店预录（openid 为空）与建表约束互斥；按任何一方实现，S1 绑定（P0）不是失败就是 500 | 明确一种口径（预录回填 openid / 登录建行写 phone），§4.1/①/② 一致，并把该问题写进 §9 | PRD S1「以 openid 创建/匹配 member」「门店已在后台录入会员手机号 → 与记录匹配」 | r2 方案 A/B 二选一，请产品经理定口径。**修复前 TC-S1-04/06/07/10 不可执行** |
| \[P1\]-3 | P1 | tech-design §6.4 确认递补流程未含"`now >= start_at`→`WAITLIST_CLOSED`"与"黑名单→`MEMBER_BANNED` 顺延"（§5.3⑩ 已含但未同步） | 只读 §6.4 的实现者会漏掉两个安全校验，19:00:05 仍可确认成功、黑名单会员仍可确认 | §6.4 与 §5.3⑩ 顺序校验一致 | PRD S7"now≥start_at 全部置 expired"、R5"黑名单期不可递补确认" | 把 ⑩ 的两条校验同步进 §6.4（r2 §4-1） |
| \[P1\]-4 | P1 | tech-design §6.7 黑名单重算伪代码 | 达阈值只 `INSERT event_log('ban_applied')`，未写 `notification_outbox`；PRD S9"进入黑名单立即发送通知"无实现落点 | 补 outbox（`biz_type='ban_applied'`）并接入 §6.8 降级 | PRD S9、PRD §6.4/§10.3 `ban_applied` | r2 §4-2；TC-S9-06 依赖此项 |
| \[P1\]-5 | P1（P1 故事） | tech-design §5.5 无手动递补端点；§4.2 引"见 ㉘"、§5.1 引"①～㉘"但全文无 ㉘ | PRD S13 要求"手动递补需记录操作人与原因"，无接口可验；引用悬空 | 补 `POST /api/staff/sessions/:id/manual-promote`（audit `manual_waitlist`）或明确"本期不做" | PRD S13；r2 §4-3 | TC-S13-03 依赖此项 |
| \[P1\]-6 | P1 | tech-design §5.5 ㉑ | 运营判 `attended` 时是否补写 `check_in` 未定义 → 爽约率分母/点名完成率口径（"该场次存在 ≥1 条 check_in"）与黑名单计数（按 `booking.status='no_show'`）可能对同一场次结论分叉 | 明确补写（`marked_by_role='staff'` + change_log）或明确只改状态并说明指标影响 | PRD §2 口径定义 | r2 §4-4 |
| \[P1\]-7 | P1（配置非 0 时） | tech-design §6.2 原子占位 SQL `start_at > now()` vs §6.2 伪代码/§5.3⑦ `bookingDeadlineMinutes` | 默认 0 等价；门店把 `booking_deadline_minutes` 调非 0 后，占位 SQL 绕过截止规则（运营加位/递补路径同隐患） | 统一为 `start_at - $bookingDeadlineMinutes > now()` | PRD R4 `booking_deadline_minutes` 为可配参数 | r2 §4-5；TC-S3-10 需在配置非 0 时复测 |
| \[P1\]-8 | P1 | tech-design §6.8 通知降级两种写法并存 | 同时写"`promote()` 里查 outbox 决定 15/30 分钟"与"worker 事后条件更新"；前者让事务依赖外部状态，与 §6.2"事务内不查外部"冲突 | 只保留 worker 事后条件更新一种 | tech-design §6.2 自身原则 | r2 §4-8；TC-S7-11 断言倒计时更新机制按定稿口径 |
| \[P1\]-9 | P1 | **PRD R3 措辞 vs PRD S7/tech §4.4** | R3 把"会员主动放弃（declined）"列为"立即结束该释放事件"五情形之一，却又说"有下一顺位时转给下一位"；若放弃即结束并 `reserved-1`，名额就无法继续锁定给下一顺位，与 S7"名额仍锁定在同一释放事件上（try+1）"矛盾 | 统一为：有下一顺位时 release 保持 `open`、`try+1`、entry→`declined`；无下一顺位时 `abandoned`、`reserved-1`（tech §4.4 已如此） | PRD R3、S7、tech §4.4 状态机 | 请产品经理修订 R3 措辞；本计划按 S7/tech 口径执行（TC-S7-06） |
| \[P2\]-1 | P2 | PRD S8 点名窗口端点闭合未写明 | `[start_at-30min, start_at+15min]` 恰好在 `+15:00` 能否标记未定义；tech ⑭ 已按闭区间（`≤`）实现 | 明确闭区间并写进 PRD | PRD S8、tech ⑭ | 本计划按闭区间（TC-S8-04/09）；请产品经理确认 |
| \[P2\]-2 | P2 | PRD S7"5 秒内"通知 | 5 秒指"系统侧发起通知"，不含微信网络耗时（tech Q8 已澄清）；测试需按此口径计时 | 写进测试口径：以 outbox 写入→worker 取出发送为计时区间（2 秒轮询），端到端 2–4 秒 | PRD S7、tech Q8 | 已在 TC-S7-01 注明口径 |
| \[P2\]-3 | P2 | tech-design §5.2 错误码 vs §5.3 ② | `PHONE_ALREADY_BOUND` 已入总表（r2 修复）；剩余核对：30 条错误码逐条需有可触发用例 | 已列入 TC 矩阵逐条核对 | tech §5.2 | 测试执行时逐条触发并断言文案（尤其 409 类客户端必须刷新） |
| \[P2\]-4 | P2 | tech-design §6.4 调用链 `booking.leave()` | 本产品没有"退出预约"动作，退出的是候补；命名易致实现者误解 | 改名或注明 = 退出候补 | tech §6.4 | r2 §4-8 💭 |
| \[P3\]-1 | P3 | tech-design §0/§8.1/§10 自述数字 | §0"30 人日+20%≈36/7.5 周" vs §8.1"29 人日≈35/7 周"；§10"24 端点+25 错误码" vs 实际 27/30 | 统一数值 | 可核对正文 | r2 §4-6；不影响功能，测试计划直接按 27/30 执行 |
| \[P3\]-2 | P3 | tech-design §5.1 引用"ADR-10" | §7 只有 ADR-1～9，引用悬空（实质结论已在 §5.1） | 补 ADR-10 或删引用 | tech §7 | r2 §4-7 |
| \[P3\]-3 | P3 | PRD §7.2 Q1（教练"签到"语义） | 当前口径=教练为会员点名；CEO 指令原文"教练上课时签到"存在歧义，未正式确认 | CEO 确认；若为"教练本人打卡"，S8 全部用例与数据模型作废重写 | PRD D1/Q1 | 进入开发前请 CEO 明确 |

---

## 5. 缺陷分级与回归范围

### 5.1 缺陷分级

| 级别 | 定义（本项目实例） | 处理时限 |
|---|---|---|
| **P0 阻塞** | 数据损坏或丢失（超卖、同一会员同场次 2 条有效预约、重复释放名额）；安全漏洞（越权查看他人数据、教练端泄漏手机号）；与 PRD 核心验收标准矛盾（如绑定永久失败）；回滚条件 3 项（超卖、通知到达率 <30%、名额反复锁死、点名窗口计算错误）任一命中 | 立即修复，修复后全量回归对应模块 |
| **P1 严重** | 重要功能缺陷（候补入队/递补/取消阈值/点名窗口边界行为错误）、关键错误处理缺失（降级链路、幂等）、验收标准未满足 | 当轮修复，阻塞里程碑晋级 |
| **P2 一般** | 边界情况处理不当（30 天窗口端点、争夺竞态提示）、提示不清楚、次要功能（P1 故事部分路径） | 本轮或下轮修复 |
| **P3 轻微** | 文案、格式、自述数字不一致、命名 | 不阻塞；集中修复 |

### 5.2 回归范围（按改动面）

| 触发改动 | 回归用例（必跑） |
|---|---|
| 名额/容量逻辑（`occupied_count`/`reserved_count`/capacity 相关 SQL、约束） | TC-S3-01/02/03/12/13、TC-S5-06/12/13、TC-S7-01/02/03/07/15、TC-S13-01/02 + 并发脚本 + CHECK 对账 |
| 数据模型/迁移（DDL、索引、CHECK） | 全量回归（S1–S13 冒烟 + §3.12 TC-G 全集） |
| 候补/递补（队列、rank、release、try_count、超时任务） | TC-S6 全集、TC-S7 全集 + 时序专项 + TC-G-10 |
| 通知（outbox、微信交互、降级） | TC-S7-01/11/12、TC-S9-06、TC-S12-01、mock 三分支 |
| 结算/黑名单（结算 SQL、重算、撤销、缓存刷新） | TC-S8-10/11/20、TC-S9 全集、定时任务幂等 |
| 规则配置参数与文案（含默认值变更） | TC-S10 全集 + 参数化文案回归（B2/B3/B4/模板） |
| 页面文案/状态（会员端文案红线） | 全文案走查（请假口径、pending_review、锁定说明、"已过预约截止"vs"已开课"） |
| 角色/权限/鉴权 | TC-G-01/02/03 + 真机角色导航 |
| 定时任务周期/实现 | TC-G-09 + 对账断言 |

---

## 6. 运行证据核实

> 本阶段（MVP 文档期）**没有任何可运行产物**：共享文件区仅有 `docs/PRD.md`、`docs/design.md`、`docs/tech-design.md`、`docs/review-tech-design-r1.md`、`docs/review-tech-design-r2.md` 五份 markdown，无代码、无命令、无运行输出。因此本节所有验收标准均标注 **未验证（需开发期运行）**，验证方法与断言引用 tech-design §8.1（工程师已给出可执行断言），测试执行时按下列方式收集证据。

| 验收标准（PRD） | 验证方式（开发期） | 结论 |
|---|---|---|
| S1 登录/绑定（含手机号、未绑定浏览、手机号重复绑定） | 接口测试：①/②/㉕/㉗；**前置：修复 §4 \[P1\]-2** | 未验证（需运行） |
| S2 课表 7 天/筛选/剩余名额公式三处一致 | 单元测试 `remainingSlots` 同源断言 + 接口测试 ③/④ | 未验证（需运行） |
| S3 预约（占位、并发、幂等、冲突、截止、黑名单） | **并发脚本 50 抢 5**（断言成功 5、无 CHECK 违反、无同会员双条）+ 接口测试 ⑤ | 未验证（需运行） |
| S4 我的预约分组/分页/状态/候补位次 | 接口测试 ⑥ + UI | 未验证（需运行） |
| S5 取消/请假（闭区间边界、幂等、触发递补、临期空位） | 时间注入接口测试 ⑦（`now == start_at-120min` 断言 ontime） | 未验证（需运行） |
| S6 候补（上限、rank、并发 position、退出重排） | 集成测试入队（**前置：修复 §4 \[P1\]-1**）+ 并发 position 唯一断言 | 未验证（需运行） |
| S7 递补全链路（锁定、确认、超时、放弃、3 次上限、降级） | 集成测试：取消→`release=open/reserved=1/队首 notified/outbox 1 条`；连续 3 次超时→`abandoned`；确认 vs 直接预约并发只有一方成功；临期不建 release | 未验证（需运行） |
| S8 点名（窗口闭区间、批量、pending_review、越权、change_log） | 接口测试 ⑫–⑯：窗口外 409、非本场 403、重复标记幂等且留 change_log；结算 SQL 三分支 | 未验证（需运行） |
| S9 爽约/黑名单（30 天窗口、阈值、max 延长、撤销只改状态、自动解除） | 集成测试：3 次 no_show→`banned_until≈now+7d`；黑名单期再记→只延长；撤销→计数回退且自动解除；**前置：修复 §4 \[P1\]-4** | 未验证（需运行） |
| S10 规则配置（范围、场次级覆盖、1 分钟更新、降级开关） | 接口测试 ⑰/⑱：`cancel_deadline=2000` 被拒、>7 天 warning 二次确认、改参数后文案 1 分钟内更新 | 未验证（需运行） |
| 定时任务幂等/可观测 | 同一结算/超时/缓存任务连跑 3 次结果相同；`/healthz` 返回 `lastJobRuns` | 未验证（需运行） |
| 通知链路与降级 | mock 微信三分支：`success/unauthorized/fail` 对应日志状态；`43101`→时限 = `min(30min, start_at)` 且锁定未释放 | 未验证（需运行） |
| 数据一致性守护 | 对账 SQL：`reserved_count == count(open release)`；CHECK 违反 → `critical` 日志 + 告警 | 未验证（需运行） |
| 埋点 | 主流程跑完 → `event_log` 出现 PRD §9 全部事件名；5 条指标 SQL 出数 | 未验证（需运行） |
| 首页 3 秒可交互 / 筛选 2 秒更新 | 真机性能采样（弱网档位） | 未验证（需运行） |

---

## 7. 交付结论

**结论：文档可提交工程师审查（修改后能交付）**，理由与必须处理事项如下：

1. **本测试计划**以 PRD v1.1 验收标准为唯一口径编写，S1–S13 全部故事均含正常流程、边界、异常、权限用例，并单列候补递补时序/状态机专项（§3.7）、缺陷分级与回归范围（§5）、运行证据核实表（§6）。**待工程师审查通过即算完成**。
2. **进入开发前必须先修复 2 条 P1（来自技术方案审查 r2）**：\[P1\]-1 候补上限计数口径（§6.3 与声明矛盾）、\[P1\]-2 `member` 建行/绑定口径（§4.1 与 §5.3② 矛盾、§9-Q10 悬空）。两者直接阻塞 S6/S1 的相应用例执行。
3. **建议同轮处理**：\[P1\]-3～\[P1\]-8（§6.4 确认校验同步、黑名单通知 outbox、手动递补 ㉘、㉑ check_in 口径、占位 SQL 截止规则、通知降级两种写法）与 PRD R3「放弃」措辞修订（\[P1\]-9）、点名窗口端点闭合确认（\[P2\]-1）。
4. **需要 CEO/产品经理回答的**：PRD Q1（教练"签到"= 点名会员 vs 本人打卡，影响 S8 全部用例与数据模型）、Q5（订阅消息模板报备，影响 S7 真机验收口径）、Q2（单店/多店，本计划按多门店测）。
5. **本阶段无运行证据**：§6 全部验收标准标注"未验证（需运行）"，验证方法已引用 tech-design §8.1 的可执行断言；测试不会把"文档写了"当作"测试通过"。