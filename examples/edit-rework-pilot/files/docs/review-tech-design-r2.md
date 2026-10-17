# 审查报告：技术方案设计（第 2 轮）

- 审查对象：`docs/tech-design.md`（工程师，v1.0，1210 行）
- 审查依据：`docs/PRD.md`（v1.1，唯一需求依据）、`docs/design.md`（v1.1）、本任务的 5 项要求、`docs/review-tech-design-r1.md`（第 1 轮结论与问题清单）
- 审查人：测试
- 结论摘要：**修改后能交付（changes）**。第 1 轮 4 条必须修改中，3 条（角色链路、门店列表、信用接口）**已真正补齐且可用**，第 4 条（候补计数器）只改了"声明"，**唯一给出实现的 §6.3 SQL 仍是旧写法**，属于未修复；另发现 1 处新的硬矛盾（`member` 建行口径：§4.1 DDL 与 §5.3 ② 互斥，且 §5.3 ② 引用的 §9-Q10 不存在）。两条都必须改，但都是局部改动。

---

## 1. 这一轮我检查了什么、用什么方式

| 检查项 | 方式 | 依据 |
|---|---|---|
| 上一轮 4 条必须修改 | 逐条定位到当前版本的章节原文，判断"声明改了"还是"实现/口径也改了" | r1 报告 + 本文档 |
| 上一轮 10 条建议 + 5 条小改进 | 同上，逐条标记 已改 / 未改 / 部分改 | r1 报告 |
| 需求覆盖复核 | PRD S1–S13 的**验收标准**（不只是主流程）对照 §5 每个端点的入参/出参/错误码 | PRD §5.2 |
| 规则复核 | R1–R7 逐条代入具体数字推演（容量/候补/递补/取消/点名/爽约/冲突） | PRD §5.3 |
| 文档一致性 | PRD ↔ design ↔ tech-design 三方对照（页面状态、文案口径、字段、角色、参数） | 三份文档 |
| 声明核实 | 我自己数了表数、索引数、错误码数、端点数、ADR 数、§9 问题数 | 产物正文 |
| 运行证据 | `list_files` 只有 4 份 markdown，无代码、无命令、无运行输出 | 共享文件区 |

> 说明：本阶段没有可执行产物，因此**没有任何运行证据**可核实；所有结论都是对文档内容的推演。涉及 SQL / 并发 / 定时任务的行为，仍需在开发期按 §8.1 的断言逐条落地验证（见 §6）。

---

## 2. 上一轮问题的复核结果

### 2.1 上一轮 4 条必须修改

| r1 编号 | 工程师的处理 | 我的核实结论（证据位置） |
|---|---|---|
| P1-1 `waitlist_count` 只增不减 → 候补永久误判"已满" | 声明改为"展示缓存、不参与上限判定" | ❌ **未真正修复**。§4.2 字段注释、§4.5 派生值表（"当前队列人数（上限判定用）：`count(...)`…**不用 `waitlist_count` 缓存列**"）、§5.3 ⑧ 都改成"按 `{waiting,notified}` 记录数实时判定"，但 §6.3 **唯一给出的入队 SQL 仍是 `UPDATE class_session SET waitlist_count = waitlist_count + 1 WHERE id=$1 AND waitlist_count < $waitlistCapacity`**（仍以缓存列判定）；递减只写在 §5.3 ⑨（退出）与 ⑪（放弃），§6.4 确认成功、§6.4 超时置 expired、§5.5 ㉓ 整场取消三条路径没有递减；§4.2 承诺的"每日对账自愈"在 §6.7 的 4 个任务里没有落点。→ 本轮必须修改第 1 条（详见 §3） |
| P1-2 认证/角色链路不完整（教练端 P0 进不去） | ① 登录返回角色；新增㉗教练绑定 + `staff_invite` 表；§5.1 回复 design §9-2 | ✅ **已修复**。① 响应含 `roles/coachId/storeIds/defaultStoreId`；§4.2 新增 `staff_invite` 表 + 绑定流程；㉗ `POST /api/auth/coach-bind` 含入参/出参/错误码（`INVITE_CODE_INVALID`/`ROLE_ALREADY_BOUND`）；§5.1 明确**采用 `custom-tab-bar`** 并说明多角色不重签 token、运营=会员 tabBar+「门店管理」分组，与 design §2.2 一致；§8.2 写明"教练身份绑定（邀请码/后台录入）依赖运营、阻塞 S8 验收"。残留仅两处引用问题：§5.1 两处写"见 ADR-10/详见 ADR-10"，而 §7 只有 ADR-1～9（🟡，见 §4） |
| P1-3 `storeId` 必填且无门店列表 → 未绑定会员无法浏览课表（违反 S1） | §5.3 ③ `storeId` 改可选 + §5.1 默认门店回退链 + 新增 ㉕ | ✅ **已修复**。§5.1 回退链（`home_store_id` → 唯一 active 门店 → 列表第一家 active → `404 STORE_NOT_FOUND`）；㉕ `GET /api/stores` 未登录可调、返回 `defaultStoreId`；③ 的 `store` 与 `rule` 一并返回。S1/S2 的"可浏览"链路成立（推演见 §5 场景 1） |
| P1-4 缺会员信用状态接口 → S9 置灰/解除时间/「近 30 天 N 次」无数据源 | 新增 ㉖ | ✅ **已修复**。㉖ `GET /api/me/credit?storeId=` 返回 `noShowCount30d`（**注明走 §6.7 实时重算、不读 `_cache`**）、`noShowThreshold30d`、`bannedUntil`、`banReason`、`banEnabled`、`storeContact`；注明"页面加载时主动调用"，并对应 design §5.1 红条 / §5.3 历史"近 30 天已 N 次" / §5.5 D2；越权限定 `jwt.sub` |

### 2.2 上一轮 10 条建议

| r1 建议 | 核实 |
|---|---|
| 1 ⑩ 确认递补未校验 `start_at`/黑名单 | ✅ §5.3 ⑩ 已按固定顺序补 ① `now >= start_at` → `expired` + `WAITLIST_CLOSED` ② `banned_until > now` → `expired` + 顺延 + `MEMBER_BANNED`。**但 §6.4"确认递补事务流程"未同步这两条**（🟡 §4-1） |
| 2 ③/④ 缺 `confirmDeadlineAt` / 已约人数 | ✅ ③/④ 已补 `occupiedCount`、`reservedCount`、`myWaitlistConfirmDeadlineAt`，并写明"已约人数"口径 = `occupied_count`（含 `attended`） |
| 3 ⑳ 容量下调"先查再改" | ✅ 已改为条件更新 `WHERE capacity + $delta >= occupied_count + reserved_count`，0 行 → `CAPACITY_CONFLICT`，并把 `23514` 也映射为 `CAPACITY_CONFLICT` |
| 4 ⑳ 示例与 §6.4 步骤 4 矛盾 | ✅ 示例已改为 `releasesCreated: 1 / vacantSlots: 1 / notified: 1`，且 `capacity 22 / occupied 20 / reserved 1 / remaining 1` 自洽（推演见 §5 场景 5） |
| 5 §6.7 黑名单重算未写 outbox | ❌ 未改。§6.7 仍是 `UPSERT member_store_credit ... ; INSERT event_log('ban_applied')`，没有 `notification_outbox`，PRD S9「进入黑名单 → 立即发送通知说明原因、区间、解除时间与申诉方式」+ PRD §6.4/§10.3 的 `ban_applied` 仍无实现落点（🟡） |
| 6 `PHONE_ALREADY_BOUND` 未入总表 | ✅ 已入总表（409，"该手机号已被绑定，请联系前台"），并顺带补了 `STORE_NOT_FOUND`/`INVITE_CODE_INVALID`/`ROLE_ALREADY_BOUND` |
| 7 缺手工递补端点 | ❌ 未改。§5.5 仍无 `manual-promote`；且 §4.2 `waitlist_release.origin` 写"运营手动指派用 `manual_promote`，**见 ㉘**"、§5.1 写"接口编号 **①～㉘**"，但全文没有 ㉘（🟡） |
| 8 ㉑ 未定义是否补写 `check_in` | ❌ 未改。§5.5 ㉑ 只写"若产生 `no_show` → 触发重算"，未说明运营判定 `attended` 时是否补 `check_in`（PRD §2 的爽约率分母与点名完成率都以"该场次存在 ≥1 条 `check_in`"为口径）（🟡） |
| 9 §6.2 占位 SQL 与 §5.3 ⑦ 阈值不一致 | ❌ 未改。§6.2 原子占位 SQL 仍是 `AND start_at > now()`，而 §6.2 伪代码是 `now >= session.start_at - rule.bookingDeadlineMinutes`、§5.3 ⑦ 用 `cancel_deadline_minutes`（🟡） |
| 10 ⑭ 步骤 3 分支不可达 | ✅ 已改为"目标：`booking.status='booked'`"，并注明"已结算状态在窗口内不可达；运营事后改结论走 ㉑/㉒" |

### 2.3 上一轮 5 条小改进

| r1 小改进 | 核实 |
|---|---|
| 1 §0/§10 自述数字与正文不符 | ⚠️ 部分改。§4 标题已改为**可核对**的"19 张表 / 3 个部分唯一索引 / 1 个 CHECK"（我逐张数：§4.1 七张 + §4.2 七张 + §4.3 五张 = 19 ✅；部分唯一索引 `booking`、`waitlist_entry`、`member.phone` = 3 ✅；错误码总表我逐条数 = 30 ✅，与"共 30 条"一致）。但 **§10 自查仍写"24 个端点 + 25 个错误码"**（实际 27 个端点 ①～⑪+⑫～⑯+⑰～㉔+㉕㉖㉗、30 条错误码），**§0 写"30 人日 + 20% ≈ 36 人日（≈7.5 周）"而 §8.1 合计是"29 人日、≈35 人日、≈7 周"**（🟡） |
| 2 §4.3 `event_log` 误引"§4.4" | ✅ 已改（现为"清单以 PRD §9 为准，不另立重复清单"） |
| 3 §6.8 两种口径并存 | ❌ 未改（§6.8 第 4 点仍同时写"实现位置在 `promote()` 里查 outbox"与"由 worker 事后条件更新"）（🟡） |
| 4 `origin` 的 `session_reopen` | ✅ 已删除并注明"PRD 无'场次重开'动作" |
| 5 `booking.leave()` 命名 | ❌ 未改（§6.4 调用链仍写 `booking.cancel() / booking.leave() / ...`）（💭） |

---

## 3. 本轮必须修改（2 条）

### [P1] 候补上限的判定口径在 §6.3 仍是"缓存计数器"，且递减/对账路径不完整

**位置**：`docs/tech-design.md` §6.3 入队 SQL 与 §4.2 `class_session.waitlist_count` 注释、§4.5 派生值表、§5.3 ⑧ 校验顺序相互矛盾；§4.2 承诺的"每日对账自愈"在 §6.7 无对应任务。

**实际**：§4.2 写"（**展示缓存，不参与上限判定**：上限一律按实时记录数判（§6.3）；每条 entry 离开 `{waiting,notified}` 时同事务 −1；每日对账自愈）"，§4.5 写"上限判定用 `count(status IN ('waiting','notified'))`…**不用 `waitlist_count` 缓存列**"，§5.3 ⑧ 写"锁场次行 + 实时队列人数上限校验（§6.3）"；**但 §6.3 给出的唯一实现仍是**：

```sql
UPDATE class_session SET waitlist_count = waitlist_count + 1
 WHERE id = $1 AND waitlist_count < $waitlistCapacity   -- 0 行 -> WAITLIST_FULL
```

**会怎么出错**：工程师照 §6.3 落库（全文只有这一处给出实现），某场次 `capacity = 20`、候补上限 20。会员反复"入队 → 被通知 → 确认成功"20 次：入队各 +1，而"确认成功"这条离开路径（§6.4 确认流程只写 `occupied+1 / reserved-1`）没有 −1，计数器涨到 20，而队列里实际 0 人 → 之后**所有人**点「排队候补」都因 `waitlist_count < 20` 不成立而返回 `WAITLIST_FULL`「候补已满，可在开课前再来看看」，直到场次结束都不恢复，且名额释放时也无人可递补。同理，超时扫描把 entry 置 `expired`、㉓ 门店取消整场把 `waiting/notified` 置 `canceled_auto` 这两条路径也没有递减（后者更严重：整场取消后该场次计数器永远非 0）。这正是第 1 轮第 1 条问题——**声明改了，实现没改**。若按 §4.5 的 `count` 实现则不会出错，但两种写法会在代码里打架（§6.3 的注释还明确说"`waitlist_count` 与 INSERT 在同一事务"，读起来就是判定依据）。

**期望**：二者选一，并把 §6.3 的 SQL 改到与 §4.2/§4.5/§5.3 ⑧ 一致：
1. **按记录数实时判定**（推荐，与 R2 口径及 rank SQL 同源）：§6.3 改为"先 `SELECT ... FROM class_session WHERE id=$1 FOR UPDATE`（或 `UPDATE class_session SET updated_at=now() WHERE id=$1`）锁场次行 → `SELECT count(*) FROM waitlist_entry WHERE session_id=$1 AND status IN ('waiting','notified')` 与生效上限比较 → 超限返回 `WAITLIST_FULL`"；`waitlist_count` 只作展示；或
2. **保留计数器**：在 ⑨ 退出 / ⑪ 放弃 / §6.4 确认成功 / §6.4 超时置 expired / §5.5 ㉓ 整场取消（置 0）**五条路径**同事务递减，并在 §6.7（或 §6.4 对账段）补一条与 `reserved_count` 同级的**每日对账断言** `waitlist_count == count(status IN ('waiting','notified'))`，写明由哪个任务执行（现有 4 个任务都没做这件事）。

**依据**：PRD R2「候补上限 `waitlist_capacity`（ratio 默认 1.0），**按 `status ∈ {waiting, notified}` 的记录数计**」；PRD S6「Given 候补队列人数已达上限…Then 阻止并提示"候补已满，可在开课前再来看看"」。

---

### [P1] `member` 建行/绑定口径自相矛盾，且 §5.3 ② 引用的 §9-Q10 不存在

**位置**：§4.1 `member` 表（`openid text NOT NULL UNIQUE`、`phone text NULL（授权手机号后写入）`）与 §5.3 ② `POST /api/auth/bind-phone` 的服务端流程（"匹配到且 `openid` 为空 → 写入 openid"、"未匹配 → `MEMBER_UNBOUND`（该手机号未被门店录入）"）互相冲突；§5.3 ② 末尾"此假设已列入 **§9-Q10** 请产品确认"，而 §9 只有 Q1～Q8。

**实际**：两处对"member 行由谁创建、绑定写哪个字段"给了互斥的答案——按 §4.1/① 是"登录时以 openid 建行，授权后往**本行**写 phone"；按 §5.3 ② 是"门店先预录一行（有 phone、openid 为空），绑定后回填 openid"。而 §4.1 的 `openid NOT NULL` 使"门店预录、openid 为空的行"根本无法存在。

**会怎么出错**：
1. 门店在后台/CSV 预录会员（PRD S8 前置、PRD S1"门店已在后台录入会员手机号"）时，`INSERT` 直接触发 `NOT NULL` 违反 → 预录失败，绑定体系失去数据来源；
2. 若工程师按 §4.1 实现（登录建行、绑定写 phone），则 §5.3 ② 里"匹配 `member.phone` 且 `openid` 为空"的分支永远不成立，会员授权手机号后只会走到"未匹配 → `MEMBER_UNBOUND`"→ **S1（P0）绑定永久失败**，S3/S5/S6/S7 的按钮全部长期置灰；
3. 若工程师同时按 ① 建行、按 ② 回填 `openid`，两条同 openid 的行会撞 `openid` 唯一约束（23505），错误码总表未映射 → 返回 500 `INTERNAL_ERROR`；
4. 另外 ② 未定义"匹配到且 `openid` 就是我自己"（重复绑定）的分支，只定义了"为空"和"其他 openid"。

**期望**：明确一种口径并让 §4.1 / ① / ② 全部一致，且把该问题真正写进 §9（现在 §9 只有 8 条，被引用的 Q10 不存在）。建议二选一并写清：
- **方案 A**（门店预录为真）：`member.openid` 允许 NULL，新增或明确"门店预录"的写入路径与 `openid` 回填/合并规则（例如绑定时把预录行的 openid 回填、并说明如何处理登录时产生的临时行与 `(openid)` 唯一约束的冲突）；
- **方案 B**（登录建行为真）：删除 ② 的"openid 为空"分支，改为"取 `jwt.sub` 对应的 member 行，用微信返回的手机号与该行 `phone`（或门店名册）比对后写入 `phone` + `bind_status='bound'`"，并写清"该手机号已被其他 openid 绑定"的判定方式。
两种方案都需要在 §9 新增条目（Q10）请产品确认口径，因为 PRD S1 同时写了"以 openid 创建/匹配 member 记录"和"门店已在后台录入会员手机号 → 与记录匹配"。

**依据**：PRD S1「服务端以 `openid` 创建/匹配 member 记录」「Given 门店已在后台录入会员手机号，When 会员在小程序内授权手机号且与记录匹配，Then 绑定成功」「边界：同一手机号被两个 openid 绑定时…提示"该手机号已被绑定，请联系前台"」；PRD §10.1 `member` 字段清单。

---

## 4. 建议（🟡，不阻塞交付）与提醒

| # | 位置 | 内容 |
|---|---|---|
| 1 | §6.4 确认递补事务流程 | §5.3 ⑩ 已补"`now >= start_at` → `WAITLIST_CLOSED`"与"`banned_until > now` → `MEMBER_BANNED` 并顺延"，但 §6.4（评审重点章节）的 confirm 流程只有"释放事件条件更新 + 时间冲突复检"。只读 §6.4 的实现者会漏掉两个安全校验，从而重现第 1 轮建议 1 的漏洞（19:00:05 仍能确认成功、黑名单会员仍能确认）。建议把两条同步进 §6.4 |
| 2 | §6.7 黑名单重算伪代码 | 补 `INSERT notification_outbox(biz_type='ban_applied', ...)`（PRD S9 要求"立即发送通知说明原因、生效区间、解除时间与申诉方式"，PRD §6.4 的降级为"站内提示条（不可关闭）"），否则该通知链路无落点（第 1 轮建议 5 未处理） |
| 3 | §5.5 / §4.2 / §5.1 | 补 `POST /api/staff/sessions/:id/manual-promote`（写 `audit_log` `manual_waitlist`，`origin='manual_promote'`、`try_count` 从 0 起），或明确写"本期不做手动递补"；同时删除或补上被引用的 **㉘**（§4.2 "见 ㉘"、§5.1 "①～㉘"）。PRD S13 要求"手动递补需记录操作人与原因"，§8.1 M7 也把它算进 3 人日 |
| 4 | §5.5 ㉑ | 明确运营把「未点名异常」判为 `attended` 时是否补写 `check_in`（建议补写，`marked_by_role='staff'`，并追加 `change_log`）。否则 PRD §2 的爽约率分母 / 教练点名完成率（口径为"该场次是否存在 ≥1 条 `check_in`"）与 R5 的黑名单计数（按 `booking.status='no_show'`）会对同一场次给出互相矛盾的结论 |
| 5 | §6.2 原子占位 SQL | 统一为 `AND start_at - $bookingDeadlineMinutes > now()`（现为 `start_at > now()`，与同行伪代码/⑦ 的 `bookingDeadlineMinutes` 不一致；默认 0 时等价，门店改配置后不等价）。顺带建议在 ④ 的 `denyReason` 里把"已过预约截止（未开课）"与"已开课"区分开（design §5.2/§6.7/§9-9 明确要求两种文案不同、且"不得写已开课"；目前两者都用 `SESSION_STARTED`，只能靠客户端用 `startAt` 自行推断） |
| 6 | §0 / §8.1 / §10 | 自述数字统一：§10 的"24 个端点 + 25 个错误码"改为 27 / 30（或写"见 §5"）；§0 的"30 人日 + 20% ≈ 36 人日（≈7.5 周）"与 §8.1 的"29 人日、≈35 人日、≈7 周"取一个（我按里程碑逐项加过：1.5+2+3+4+6+5+2+3+2.5 = 29 ✅；4 周版 23.5+1+2 = 26.5 ✅ 算术都对，只是 §0 与 §8.1 不一致） |
| 7 | §5.1 / §7 | §5.1 两处引用"ADR-10"（多角色 + custom-tab-bar 的决策记录），但 §7 只有 ADR-1～9。实质结论已写在 §5.1，建议补 ADR-10 或把引用去掉 |
| 8 | §6.4 / §6.8 | 💭 措辞：§6.4 调用链的 `booking.leave()`（本产品没有"退出预约"动作，退出的是候补）建议改名；§6.8 第 4 点同时保留"`promote()` 里查 outbox 决定 15/30 分钟"与"由 worker 事后条件更新"两种写法，建议只留后者（前者会让事务依赖 outbox 状态，与 §6.2"事务内不做外部调用/查询"的原则冲突） |

---

## 5. 我用具体场景推演过（通过 / 不通过）

| # | 场景（代入具体数字/状态） | 推演结果 |
|---|---|---|
| 1 | 未绑定会员首次进入：`home_store_id = NULL` → ㉕（未登录 `defaultStoreId = null`）取列表第一家 active 门店 → ③ 传该 `storeId` → 得到 `sessions` + `rule` → B1 渲染 + 按钮置灰 + 黄条 | ✅ 通过（S1/S2 的"可浏览"可验收） |
| 2 | 教练链路：① 返回 `roles:['member','coach']`、`coachId=7` → 客户端按 roles 渲染 2 个 tab → ⑫ 今日课 → ⑬ 名单（无手机号）→ ⑭ 点名（窗口 `[start_at-30min, start_at+15min]`，服务端时间） | ✅ 通过（措施本身成立；§5.1 的 ADR-10 引用是唯一瑕疵） |
| 3 | 50 并发抢 5 个名额：原子 UPDATE（`occupied+reserved < capacity`）+ 部分唯一索引 + CHECK | ✅ 通过（结构上不会超卖；需开发期并发测试坐实） |
| 4 | 取消 → 锁定 → 确认 → 释放：取消 1 人 → `release=open`、`reserved=1`、队首 `notified`、outbox 1 条 → 确认 → `occupied+1 / reserved-1`，`filled` | ✅ 通过（`remaining` 全程等于 `capacity-occupied-reserved`） |
| 5 | 运营加容量 `delta=+2`、队列 1 人：`capacity 20→22`、`occupied 20`、`reserved 1`、`remaining 1`、`releasesCreated=1`、`vacantSlots=1` | ✅ 通过（示例与 §6.4 步骤 4 口径一致，不再锁死第二个空位） |
| 6 | 连续 3 次超时：`try_count` 1→2→3 → `abandoned` + `reserved-1` | ✅ 通过（与 PRD R3 一致） |
| 7 | 19:00 开课、19:00:05 点「确认参加」（超时扫描 30s 未到）：§5.3 ⑩① → `expired` + `reserved-1` + `WAITLIST_CLOSED` | ✅ 通过（但 §6.4 未同步，见 §4-1） |
| 8 | 黑名单：3 次 `no_show` → ㉖ 返回 `bannedUntil` → B1 红条 + 按钮置灰；运营 ㉒ 撤销 → 重算 → 立即解除 | ✅ 通过（通知落点缺失，见 §4-2） |
| 9 | 候补上限漂移：`capacity 20` 的场次"入队→确认成功"重复 20 次后计数器 = 20、队列 0 人 | ❌ **不通过**（§3 第 1 条） |
| 10 | 门店已录入会员手机号 → 会员授权手机号绑定（§4.1 建表 → §5.3 ② 流程） | ❌ **不通过**（§3 第 2 条） |

---

## 6. 运行证据核实

| 待核实的声明 | 证据 | 结论 |
|---|---|---|
| "本阶段未写代码" | 共享文件区只有 `docs/PRD.md`、`docs/design.md`、`docs/tech-design.md`、`docs/review-tech-design-r1.md` 四份 markdown | ✅ 属实（**没有可运行的命令与输出可供核实**） |
| §4 自述"19 张表 / 3 个部分唯一索引 / 1 个 CHECK" | 我逐张清点 §4.1（7）+§4.2（7）+§4.3（5）= 19；部分唯一索引 `booking(session_id,member_id) WHERE ...`、`waitlist_entry(session_id,member_id) WHERE status IN ('waiting','notified')`、`member(phone) WHERE phone IS NOT NULL` = 3 | ✅ 一致 |
| §5.2 自述"共 30 条错误码" | 我逐条清点总表 = 30 条（含新增的 `PHONE_ALREADY_BOUND`/`STORE_NOT_FOUND`/`INVITE_CODE_INVALID`/`ROLE_ALREADY_BOUND`） | ✅ 一致 |
| §10 自述"24 个端点 + 25 个错误码" | `①～⑪` + `⑫～⑯` + `⑰～㉔` + `㉕㉖㉗` = 27 个端点；错误码 30 条 | ❌ 未更新（见 §4-6） |
| §0"30 人日 + 20% ≈ 36 人日（≈7.5 周）" | §8.1 里程碑加法 = 29 人日、＋20% ≈ 35、≈7 周 | ❌ 两处不一致（见 §4-6） |
| §9 自述"以下 8 处差异" + 被引用与被引用的条目 | §9 有 Q1～Q8 ✅；但 §5.3 ② 引用 **§9-Q10**、§4.2/§5.1 引用 **㉘**、§5.1 引用 **ADR-10** 均不存在 | ❌ 引用悬空（Q10 并入 §3 第 2 条，㉘/ADR-10 见 §4-3、§4-7） |
| 接口 SQL、锁、定时任务的实际行为（并发、递补、结算、通知降级） | 无运行证据 | ⏳ **需要运行验证**。验证方法已由文档给出：§8.1 各里程碑的断言（50 并发抢 5、"取消→release=open/reserved=1/队首 notified/outbox 1 条"、"同一结算任务连跑 3 次结果相同"、mock 微信三分支）。这些断言我逐条读过后认为**可执行、且能覆盖验收标准**，但必须在开发期真正跑出来才能算通过；本轮不视为已验证 |

---

## 7. 交付结论

**修改后能交付（changes）**。

- 值得保留的做法（本轮复核后确认更稳）：㉖ 把 `noShowCount30d` 明确为**实时重算、不读缓存列**，与 403 `MEMBER_BANNED` 同一口径；㉕/③ 的默认门店回退链把"未绑定也能浏览"这条 P0 验收补成了可实现的东西；⑩ 的"固定顺序校验 → 条件更新作为唯一裁决点"把"确认 vs 直接预约 vs 运营加位 vs 超时"的竞争收敛成一条分支；⑳ 的示例与 §6.4 步骤 4 口径终于一致（不再把空位锁死 30 秒）；§4 的自述数字改成了可核对的值。这些不需要再动。
- **必须修复（决定能否进入 M4/M5 开发）**：
  1. §6.3 的入队 SQL 与 §4.2/§4.5/§5.3 ⑧ 统一（实时记录数判定，或补齐 5 条递减路径 + 每日对账任务）；
  2. `member` 建行/绑定口径统一（§4.1 与 §5.3 ② 二选一），并把被引用的 §9-Q10 真正补出来。
- 两条都是局部改动（预计 0.5 人日以内），不涉及架构返工；修完后我不再要求新东西，可以直接按 §8.1 进入开发与测试用例编写。
- 本阶段仍无运行证据：文档里的 SQL/并发/定时任务行为必须按 §8.1 的断言在开发期落地验证，我不会把"文档写了"当成"测试通过"。
- `docs/test-plan.md` 我会在本轮口径定稿后立刻产出（S1–S13 用例、边界值、运行证据核实表），避免用例跟着口径再改一遍。

---

## 8. 需要 CEO / 产品经理回答（沿用第 1 轮，仍未解决）

1. **PRD Q5 订阅消息模板报备由谁在何时发起？** 没有模板 ID，M6 只能交付"站内降级"，而递补确认率 ≥60% 在该状态下不可评估。
2. **PRD Q2 单店还是多店？** 方案按"多门店数据模型 + UI 支持切换"实现，因此 ㉕ 门店列表是必须项；若确认为单店也建议保留该接口（改动更小）。
3. **本轮新增：`member` 与门店会员名册的关系**（§3 第 2 条）。PRD S1 同时写了"以 openid 创建/匹配 member 记录"与"门店已在后台录入会员手机号"，需要产品明确"门店预录"到底写在哪个实体、绑定写 openid 还是 phone。
