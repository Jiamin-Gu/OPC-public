# 审查报告：技术方案设计（第 1 轮）

- 审查对象：`docs/tech-design.md`（工程师，v1.0）
- 审查依据：`docs/PRD.md`（v1.1，唯一需求依据）、`docs/design.md`（v1.0，交互与页面口径）、本任务的 5 项要求（架构 / 数据模型 / 接口 / 关键难点 / 里程碑）
- 审查人：测试
- 结论摘要：**修改后能交付**。方案主体（防超卖三道防线、递补原子性、名字实时的 rank、pending_review 兜底、outbox 通知、计数实时重算）经推演成立且与 PRD 口径一致，但发现 4 处必须修改的问题，集中在"计数器维护路径不完整"和"P0 角色/门店/信用链路缺接口"。

---

## 1. 我检查了什么、用什么方式

| 检查项 | 方式 | 依据 |
|---|---|---|
| 需求覆盖 | 把 PRD S1–S13 与 R1–R7 逐条对照 §1.1 需求映射、§4/§5/§6 实现 | PRD §5.1–§5.3 |
| 数据模型 | 逐字段核对 §4 与 PRD §10；检查约束、索引是否支撑 §6 的原子操作 | PRD §10、R1/R2/R5 |
| 接口 | 核对每个端点的入参/出参/错误码能否支撑 S1–S13 的**验收标准**（而不是只支撑主流程） | PRD §5.2 各条 Given/Then |
| 关键难点 | 代入具体数字与状态，推演 §6.2–§6.8 的 SQL 与伪代码 | PRD R1–R7、S7 边界 |
| 文档一致 | PRD ↔ design ↔ tech-design 三方对照（页面状态、文案口径、字段、角色） | 三份文档 |
| 声明核实 | 逐条核对工程师汇报与 §0/§10 里的自述数字 | 产物正文 |

### 1.1 用具体场景推演过的用例（结论：通过）

| 推演场景 | 结果 |
|---|---|
| 50 并发抢 5 名额（§6.2 原子 UPDATE `occupied+reserved < capacity` + CHECK + 部分唯一索引） | 三道防线互不冲突；CHECK 约束在"递补确认 occupied+1/reserved−1"路径下守恒（先加后减，和不变），不会误报 |
| 取消后 `reserved_count` 与 `status='open'` 的 release 数一一对应（§6.4） | 成立；顺延期间 `reserved_count` 不变，只有 filled/abandoned/expired 才动，与 PRD R3 一致 |
| `try_count` 计数路径：建 release（=1）→ A 超时 → +1=2 → B 超时 → +1=3 → C 超时 → abandoned | 恰好 3 次通知后停止，与 PRD R3/S7"同一释放事件连续 3 次无人确认"一致；挂在 release 上而非 entry 上（§9-Q4）判断正确 |
| 门槛 `start_at - now < confirmMinutes + 5`（默认 20 分钟）→ 不建 release、不锁定 | 与 PRD R3/S7 一致，且此时 `remaining` 不扣减，符合"保持空闲可约" |
| 整场无 `check_in` → 结算 SQL `ELSE 'pending_review'`（§6.7） | 结构性保证不会自动记爽约，与 PRD R5/D12/S9 一致 |
| 撤销爽约只改 `booking.status` 后重算 → 计数自动 −1（§5.5 ㉒、§6.7） | 与 PRD R5"严禁再额外减 1"一致 |
| 时间冲突半开区间 `[start_at, end_at)`（§6.2 冲突 SQL） | 与 PRD R6 一致（前一节结束=后一节开始不冲突） |
| 取消幂等（`WHERE status='booked'`，affected=0 返回当前状态，不重复释放） | 与 PRD S5 边界一致 |
| 里程碑加法：1.5+2+3+4+6+5+2+3+2.5=29 人日；4 周版 23.5+1+2=26.5 人日 | 算术正确，与 §0/§8.3 自述一致 |
| 定时任务幂等（结算 SQL 重复执行、`WHERE status='open'` 条件更新、`FOR UPDATE SKIP LOCKED`） | 与 §6.7 声明的幂等性一致 |

### 1.2 声明核实（工程师汇报里的数字）

| 声明 | 核实结果 |
|---|---|
| "13 张表" | ❌ 正文 §4 实际定义 **18 张**（store/room/course_template/coach/coach_store/member/member_store_credit/class_session/booking/waitlist_entry/waitlist_release/check_in/user_role/store_rule/subscription_message_log/notification_outbox/audit_log/event_log）。数量不影响可用性，但"声称"应与文件可核对（见 §5 小改进） |
| "3 个部分唯一索引" | ❌ §4 只定义了 **2 个**（`booking(session_id, member_id) WHERE status IN (...)`、`waitlist_entry(session_id, member_id) WHERE status IN ('waiting','notified')`） |
| "24 个端点" | ✅ 接口编号 ①～㉔ 共 24 个 |
| "25 个错误码" | ⚠️ §5.2 总表实际 **26 条**，且 §5.3 ② 用到 `PHONE_ALREADY_BOUND` 未列入总表（见 §4 建议 6） |
| "9 条 ADR / 8 处与 PRD 差异" | ✅ ADR-1～9、Q1～Q8 都在 |
| "本阶段未写代码" | ✅ 共享文件区只有 3 份 markdown，无代码文件 |

---

## 2. 必须修改（4 条）

### [P1] `waitlist_count` 只有 +1，没有任何递减路径 → 候补会永久误判"已满"

**位置**：§4.2 `class_session.waitlist_count` 字段定义、§6.3 入队 SQL、§6.4（确认/放弃/超时）、§5.3 ⑨ 退出候补、§5.5 ㉓ 门店取消整场。

**实际情况**：`waitlist_count` 被声明为"`status ∈ {waiting,notified}` 的缓存计数"，并作为入队上限的**判定依据**使用：

```sql
UPDATE class_session SET waitlist_count = waitlist_count + 1
 WHERE id = $1 AND waitlist_count < $waitlistCapacity   -- 0 行 -> WAITLIST_FULL
```

但全文没有一处把它减回去：会员退出候补（⑨）、被下一位顶掉后超时（`promoteNext` 把 entry 置 `expired`）、主动放弃（`declined`）、确认成功（`confirmed` → 离开 waiting/notified 集合）、门店取消整场（㉓ 把 `waiting/notified` 全置 `canceled_auto`）**都没有同步递减，也没有在任何地方清零**。

**会怎么出错（具体情形）**：某场次 `capacity = 20`、候补上限 20。一位会员反复"排队候补 → 退出候补"20 次（每次退出后 PRD R2 允许重新排队，且本方案的部分唯一索引也允许），计数器变成 `waitlist_count = 20`，而实际队列里 0 人。此后**任何会员**点「排队候补」都会因 `waitlist_count < 20` 不成立而返回 `WAITLIST_FULL`，提示"候补已满，可在开课前再来看看"——直到该场次结束都不再有人能排队，而实际名额释放时也无队可递补（候补价值直接归零）。

**期望**：二选一，写进方案并落到具体 SQL：

1. 上限判定改为按记录数实时算（与 PRD R2 口径、与 §6.3 的 rank SQL 同源）：
   `SELECT count(*) FROM waitlist_entry WHERE session_id=$1 AND status IN ('waiting','notified')`，`waitlist_count` 降级为纯展示缓存；或
2. 保留计数器，但在**每一条**离开队列的路径上同事务递减（⑨退出、`promoteNext` 置 expired、`declined`、`confirmed`、超时扫描、㉓ 整场取消清零 `= 0`）。

无论选哪个，建议在 §6.4 的每日对账里加一条与 `reserved_count` 同级的断言：`waitlist_count == count(waiting/notified)`，把"计数器漂移"当成和"锁定泄漏"同一类事故来发现。

**依据**：PRD R2「候补上限 `waitlist_capacity`（ratio 默认 1.0），**按 `status ∈ {waiting, notified}` 的记录数计**」；PRD S6「Given 候补队列人数已达上限…Then 阻止并提示'候补已满，可在开课前再来看看'」——上限应是"当时的队列人数"，不是历史入队次数。

---

### [P1] 认证/角色链路不完整：登录不返回角色，教练身份没有绑定入口 → 教练端（P0）进不去

**位置**：§5.3 ① `POST /api/auth/login`；§5.4 教练端接口（⑫～⑯，均假定 JWT 带 `role: 'coach'`）；§6.6"教练身份用一次性邀请码绑定到 `openid`"；§8.2 外部依赖。

**实际情况**：`/api/auth/login` 的响应只有 `token` + `member`（id/nickname/avatarUrl/bindStatus/homeStoreId），**不含任何角色信息**；全文没有"教练/运营身份如何绑定进来"的接口，只有 §6.6 一句"教练身份用一次性邀请码绑定到 `openid`，运营端可解绑"（没有端点、没有入参），以及 §8.2 把它列为"依赖运营"的外部项。而 `user_role` 表、`/api/coach/*`、`/api/staff/*` 都依赖"该 openid 在 `user_role` 里已有 role 与 `coach_id/store_id`"这一前提。

**会怎么出错（具体情形）**：教练按 PRD S8 打开小程序（同一个微信 openid 既可能是教练、也可能是会员），客户端拿到的登录响应里没有角色字段，无法判断该渲染哪套 tabBar（design §2.2 明确要求"按 `user_role` 渲染 2～3 个 tab"，并写"请在技术方案中确认"，本方案未回答）；也没有任何接口让门店把教练绑定进来（运营只能手工改库，但方案没说清 `user_role.wechat_openid` 从哪来），于是 M5 排了 5 人日的"教练登录/点名"却无对应设计——教练端是 S8/S9 全链路的前置，进不去就整条 P0 断掉。另外 design §2.2 还要求"教练同时也是会员时提供一次角色切换入口"，而 JWT 只承载单个 `role`，无法表达双角色。

**期望**：

1. 登录响应返回角色（例如 `roles: ["member","coach"]`、`coachId`、`storeId`），或在 JWT 里支持多角色；
2. 补"教练身份绑定"接口（如 `POST /api/auth/coach-bind { inviteCode }`），或明确写清"由运营在后台录入 + 邀请码领取 openid"的具体流程与端点；否则请在 §8.2 写明"M5 依赖运营侧手工入 `user_role`，是上线阻塞项"；
3. 明确回复 design §9-2：是否使用 `custom-tab-bar`（角色化 tabBar 的技术方案）。

**依据**：PRD S8「Given 教练打开小程序并登录（以手机号 + 门店邀请/后台绑定教练身份）」；PRD S8「Given 门店管理人员（运营）进入同一点名页，Then 可执行与教练相同的操作」；PRD R7 权限；design §2.2「实现说明（给工程师）：…这是 D1 依赖项，请在技术方案中确认」。

---

### [P1] `storeId` 必填但没有任何"门店列表"接口 → 未绑定会员无法浏览课表（违反 S1 验收标准）

**位置**：§5.3 ③ `GET /api/schedule`（查询参数 `storeId`(必填)）；§4.1 `member` 表 `home_store_id`（nullable）。

**实际情况**：课表接口把 `storeId` 设为必填，整个 §5 没有门店列表端点；`member.home_store_id` 在 PRD/本方案里都是可空的（新会员、未绑定会员、未设常去门店的会员）。

**会怎么出错（具体情形）**：会员首次打开小程序、尚未绑定门店会员身份（PRD S1 的断言："展示'未绑定门店会员'状态，**可浏览课表**但所有预约/候补按钮置灰"）。此时 `home_store_id = NULL`，客户端没有 `storeId` 可传 → 无法调用 `/api/schedule` → 课表空白。S1 与 S2 的"可浏览"验收标准在真机上无法通过；同理 design B1 的"门店切换"（多门店，Q2 默认按多门店模型实现）也没有门店列表可渲染。

**期望**：增加 `GET /api/stores`（返回 `id/name/address/status` 与当前会员的默认门店），或把 `storeId` 改为可选并在缺省时依次回落 `member.home_store_id` → "唯一 active 门店"；未绑定态下必须能拿到至少一个 active 门店。

**依据**：PRD S1「Given 会员的 openid 未在门店会员表中匹配到有效会员，When 进入首页，Then …可浏览课表但所有预约/候补按钮置灰」；PRD S2「Given 门店有多个，When 会员有 `home_store_id`，Then 默认选中该门店，可手动切换」；PRD §7.2 Q2 默认处理「多门店数据模型 + 单门店先上线，UI 支持切换」。

---

### [P1] 缺少会员信用状态接口 → S9 的"按钮置灰 + 剩余解除时间"和"N 次"无数据来源

**位置**：§5.3 会员端接口（①～⑪）；§5.2 错误码 `MEMBER_BANNED`；design §5.1/B1、§5.3/B3、§6.5。

**实际情况**：会员端唯一能拿到 `bannedUntil` 的地方是"预约/候补被拒时的 403 `details.bannedUntil`"。`/api/me/bookings` 不返回 `no_show_count_30d` / `banned_until`，也没有 `/api/me/credit` 之类的接口。

**会怎么出错（具体情形）**：

1. 黑名单会员打开首页（不点任何按钮），没有任何请求会产生 403，因此 B1 顶部红色通告条"你在该门店的预约权限已暂停至 YYYY-MM-DD HH:MM [联系门店]"（design §5.1 明确要求）拿不到数据，只能等用户去点「立即预约」才看到提示——不满足 PRD S9"小程序内预约/候补按钮置灰**并显示剩余解除时间**"（这是页面加载时就要呈现的状态）；
2. design §6.5 要求"历史里显示'近 30 天在该店已 N 次'"，同样无接口可取。

**期望**：增加 `GET /api/me/credit?storeId=`（返回 `noShowCount30d`、`noShowThreshold30d`、`bannedUntil`、`banReason`），或把这几个字段并入 `/api/auth/login` 与 `/api/me/bookings` 的响应（注意：`noShowCount30d` 必须走 §6.7 的实时重算口径，不能用 `_cache` 列直读，否则运营/会员看到的次数可能与判定不一致）。

**依据**：PRD S9「Given 会员进入黑名单，Then …小程序内预约/候补按钮置灰并显示剩余解除时间」；PRD R5「判定一律以重算为准，缓存仅供展示」；design §6.5「未点名异常/爽约与黑名单」表格中的会员端文案。

---

## 3. 建议修改（不阻塞交付）

| # | 位置 | 问题与具体情形 | 建议 |
|---|---|---|---|
| 1 | §5.3 ⑩ 确认递补事务流程 | 只校验"释放事件是否仍 `open`"，**未校验 `now < session.start_at`、也未校验黑名单**。情形：19:00 开课，19:00:05（超时扫描 30 秒周期还没跑到）会员点「确认参加」，release 仍 `open` → 成功生成 `booked` 预约并 `occupied+1`，该预约进入已开课课程的名单（19:10 教练点名时显示"补位加入"）；黑名单会员在入队后被禁，仍能确认成功。PRD S7 要求"now ≥ start_at → 全部置 expired，候补关闭"，S9 要求"黑名单期不可递补确认" | 在裁决点前加两个条件：`now >= session.start_at` → 关闭 release + entry `expired`（返回 `WAITLIST_CLOSED`）；`banned_until > now` → 返回 `MEMBER_BANNED` 并顺延下一位 |
| 2 | §5.3 ③/④ 响应字段 | session 项只有 `remaining`/`waitlistCount`，**缺 `confirmDeadlineAt`（B1 卡片"待确认递补 · 剩余 mm:ss"、B2 无数据源）与"已约人数"**（PRD S2 详情验收标准明确要求展示"容量、已约人数、候补人数"；当前客户端无法算出 `occupied_count`，因为 `reserved_count` 未返回） | 在 session 项补 `occupiedCount` / `reservedCount` / `myWaitlistConfirmDeadlineAt`；或明确说明"已约人数"用 `capacity - reservedCount - remaining` 在客户端算（需先返回 `reservedCount`） |
| 3 | §5.5 ⑳ 手动加容量 | `delta < 0` 的校验是"先查再 UPDATE"，与 §6.2 立下的原则（判断放进 `WHERE`）不一致。情形：并发预约刚占掉 1 个名额，容量下调检查通过后 UPDATE 撞上 `CHECK (occupied+reserved <= capacity)` → PG 报 23514 → 接口返回 500 `INTERNAL_ERROR`，运营看到"服务异常"而不是"需先取消预约" | 改为条件更新 `UPDATE class_session SET capacity = capacity + $delta WHERE id=$1 AND capacity + $delta >= occupied_count + reserved_count`，0 行 → `CAPACITY_CONFLICT`；并把 23514 也映射为 `CAPACITY_CONFLICT` |
| 4 | §5.5 ⑳ 示例响应 vs §6.4 | 示例写 `"releasesCreated": 2, "notified": 1`（队列只有 1 人却建了 2 个释放事件），与 §6.4 步骤 4「`if !head` → 空位保持空闲可约、返回 `NO_WAITLIST`」矛盾。按示例实现，第二个空位会被 `reserved_count+1` 锁住，最坏 30 秒内谁都不能约（直到超时扫描以 `e.id IS NULL` 兜底释放） | 统一口径：无下一顺位时不建 release、不加 `reserved_count`；示例改成 `"releasesCreated": 1, "vacantSlots": 1` |
| 5 | §6.7 黑名单重算伪代码 | 只写了 `INSERT event_log('ban_applied')`，**没有写 outbox**。PRD S9 要求"立即发送通知说明原因、生效区间、解除时间与申诉方式"，PRD §10.3 的 `biz_type` 也列了 `ban_applied`，§6.8 也配了"黑名单通知"模板 | 在达阈值分支补 `INSERT notification_outbox(biz_type='ban_applied', ...)`，并接入 §6.8 的失败降级（站内不可关闭提示条） |
| 6 | §5.2 错误码总表 | §5.3 ② 使用 `409 PHONE_ALREADY_BOUND`，但总表里没有这条。客户端做错误码→文案映射时会漏掉它，PRD S1 边界要求的提示"该手机号已被绑定，请联系前台"可能落到默认"服务异常" | 补进 §5.2 总表（403/409 归属也顺便确认一下） |
| 7 | §5.5 | PRD S13 要求"手动递补需记录操作人与原因"，但 §5.5 没有手动递补端点（只在 M7 与 4 周版砍法里提到）。design D2 有"手动把名额给某位会员"的操作 | 补 `POST /api/staff/sessions/:id/manual-promote`（写 audit_log `manual_waitlist`），或明确写"本期不做手动递补，仅 CSV/加容量"，让设计和运营知道边界 |
| 8 | §5.5 ㉑ | 未点名异常处理为 `attended` 时**是否补写 `check_in` 记录**没有定义。PRD §2 的爽约率与点名完成率都以"该场次是否存在 `check_in`"为口径，两处口径会因此分叉 | 明确：运营判定是否写 `check_in`（建议写，`marked_by_role='staff'`，并进 `change_log`），或明确只改 `booking.status` 并说明对指标口径的影响 |
| 9 | §6.2 vs §5.3 ⑦/伪代码 | 原子占位 SQL 的条件是 `start_at > now()`，而伪代码用的是 `now < start_at - booking_deadline_minutes`。默认 0 时等价，门店把 `booking_deadline_minutes` 调成非 0 后两处阈值不一致 | 统一为 `start_at - $bookingDeadlineMinutes > now()`，避免将来新增调用点（运营加位、递补）绕过截止规则 |
| 10 | §5.4 ⑭ 步骤 3 | "`booking.status IN ('booked','attended')` 已结算也允许改"实际不可达：结算发生在 `end_at`（≥ `start_at`+45min），而点名窗口在 `start_at+15min` 关闭 | 删掉该分支或注明"实际不可达，运营改结果走 ㉑/㉒" |

---

## 4. 小改进（措辞/一致性，不影响可用性）

1. §0 摘要与 §10 自查里的自述数字与正文不符（13 张表 vs 实际 18、3 个部分唯一索引 vs 实际 2、25 个错误码 vs 总表 26 条），建议改为准确值或改成"共 N 张表（见 §4）"这类可核对表述。
2. §4.3 `event_log` 写"事件名与属性…定义集中在 `domain/events.ts` 与**本文档 §4.4**"，但 §4.4 是状态机，事件定义实际在 §4.3，引用应改。
3. §6.8 第 4 点先说"实现位置在 `promote()` 里：查 outbox 该条是否 `gave_up/failed` 后决定用 15 还是 30 分钟"，紧接着"落地细节"又说由 worker 事后条件更新——两种写法并存，建议只保留后者（前者会让 `promote()` 依赖 outbox 状态，与 §6.2"事务内不查外部状态"的原则冲突）。
4. `waitlist_release.origin` 里的 `session_reopen` 在 PRD 里没有对应业务动作，建议删除或注明来源。
5. §6.4 调用链出现 `booking.leave()`，PRD/方案其他地方都没有"退出预约"这个动作（退出的是候补），建议统一命名避免歧义。

---

## 5. 做得好的地方（建议保留）

1. **防超卖不是口号**：把判断写进 `UPDATE ... WHERE`，再加部分唯一索引与 CHECK 约束兜底，并明确"CHECK 违反 → critical 日志 + 告警"，与 PRD §8.3 的回滚条件对齐。三道防线在"预约""递补确认""门店加位"三条写入路径上都推演得通。
2. **递补只有一个裁决点**（`waitlist_release` 的 `WHERE status='open'` 条件更新），把"确认 vs 直接预约 vs 运营加位 vs 超时"的竞争收敛成一条分支，这是本方案最关键的正确性设计。
3. **把 PRD 的抽象规则翻译成了可断言的机制**：`reserved_count` 与 `count(open release)` 的每日对账、`/healthz` 暴露任务最近运行时间、结算 SQL 的 `ELSE 'pending_review'`、`try_count` 挂在释放事件上——都是"能写测试断言"的东西，而不是描述性语言。
4. **诚实标注不确定项**：§9 的 8 处差异逐条给了实现口径与影响面（Q3/Q4 若不改会出真 bug，工程师主动指出并给了取舍），§6.10 明确写"是估算非实测"，§8.2 把外部依赖的阻塞关系写清楚——这些都不需要修改。
5. **与设计的对接意识**：教练端"不返回手机号"、名单变化标"补位加入"、`cancelMode` 由服务端算（不让客户端用本地时间判断）都落到了接口层。

---

## 6. 交付结论

- **结论：修改后能交付（changes）**。文档层面不需要重写，4 条必须修改都是局部补充/口径统一，预计 0.5～1 人日可完成；但**在补齐 4 条之前不应进入 M4/M5 开工**，因为其中两条（角色链路、门店列表）会直接让"教练点名""未绑定浏览课表"两个 P0 验收项在真机上跑不通，另外两条会导致候补与黑名单在运行一段时间后出现错误行为。
- **必须修复清单**：
  1. `waitlist_count` 的递减路径（或改为按 `waiting/notified` 记录数实时判定）；
  2. 登录返回角色 + 教练身份绑定流程/接口 + 回应 design §9-2 的 `custom-tab-bar` 确认；
  3. 门店列表接口（或 `storeId` 可选 + `home_store_id` 回落），保证未绑定会员可浏览课表；
  4. 会员信用状态接口（`bannedUntil` + `noShowCount30d`），支撑 S9/design 的置灰、解除时间与"N 次"文案。
- **不需要运行验证的部分**：本阶段只有文档，没有可执行命令与运行结果；上述结论全部基于对文档内容的推演，涉及 SQL/并发/定时任务的行为需在开发期按 §8.1 的断言逐条落地验证（尤其"50 并发抢 5 名额""确认 vs 直接预约并发""同一结算任务连跑 3 次"）。
- **后续步骤**：`docs/test-plan.md`（测试范围、风险、S1–S13 用例表、运行证据核实）尚未产出，等本轮技术方案口径确定后我会基于定稿版本编写，避免用例跟着口径反复改。

---

## 7. 需要 CEO / 产品经理回答的两点

1. **PRD §9-Q5（订阅消息模板报备）由谁在什么时候发起？** 技术方案已把"M6 只能交付站内降级版、递补确认率 ≥60% 在该状态下不可评估"作为硬前置写明，需要有人认领，否则 M6 的验收标准要跟着下调。
2. **PRD §7.2 Q2（单店/多店）**：本方案按"多门店数据模型 + UI 支持切换"实现（与 PRD 默认处理一致），因此"门店列表接口"是必须项而不是可选项；若 CEO 决策为单门店上线，仍建议保留接口（改动更小），请确认。
