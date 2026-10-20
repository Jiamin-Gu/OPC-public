# 积分兑换优惠券 需求说明（PRD）

> 本文是 `docs/coupon.md` 的**第一部分：需求说明**，供工程师直接据此做数据表设计。
> **第二部分（数据表设计）由工程师在本文档后续追加，本文不写表结构、字段类型、索引。**
> 依据文件：公司资料《运营规范（2026 版）》（下称《运营规范》）第一、二、三条；前序任务《核对会员积分三条口径并给出结论》的结论与 CEO 指令结论。
> 文中标注「待 CEO 确认」的条目按建议口径写出，**尚未定稿**，工程师可按建议口径设计（改口径影响面见各条说明）。

---

## 1. 目标与问题

### 1.1 目标用户与场景

**主要用户**：社区咖啡店的小程序会员——到店或线上下单频率约每周 1–3 次的周边居民与上班族（假设，验证方式：上线前查最近 90 天会员下单频次分布），最在意「积分能换成真优惠，兑换过程别麻烦」。

典型场景：
1. **积累与兑现**：会员买咖啡攒积分，攒到一定数量后在小程序「券中心」用积分兑换一张店内优惠券，再到店消费时抵扣。
2. **到店决策**：会员想喝一杯但不确定划不划算，看到「满 20 元减 5 元券」可以直接用积分兑一张，形成一次到店消费。
3. **临期驱动**：会员看到券快过期了（或积分快到期了），被提醒驱动一次到店。

### 1.2 要解决的问题

| # | 问题 | 现状与代价 |
|---|---|---|
| P1 | 积分只进不出，缺少兑换出口 | 会员攒了积分无法兑现，积分失去激励作用，复购激励失效 |
| P2 | 优惠发放靠人工（店员口头折扣、群发券） | 无法量化、易出错、易被滥用，也无法回收 |
| P3 | 会员不知道积分/券各自何时过期 | 积分和券是两套有效期（12 个月 / 90 天），表述不清会直接产生客诉（这是本需求的主要客诉风险，见第 3.8 节） |

不解决的代价：积分体系投入（获得规则、会员等级）无法转化为复购，投入沉没；且随积分余额累积，未来一次性兑付的负债更集中。

依据：本需求由 CEO 直接下达（`docs/coupon.md` 任务）；上述问题为根据业务形态的判断，**尚未有埋点或客服工单数据支撑**（共享文件区当前为空，无历史客诉数据）。假设：积分余额集中在活跃会员手中，可通过兑换拉动到店（验证方式：上线前拉取积分余额分布与近 30 天活跃会员交集，若活跃会员人均可用积分 < 300 分，需下调所需积分门槛或提高获得速度）。

### 1.3 目标与衡量指标

| 目标 | 指标 | 目标值 | 观察时间 |
|---|---|---|---|
| 积分有出口、被真实使用 | 兑换渗透率：周活跃会员中至少有 1 次成功兑换的比例 | ≥ 15%（**假设值**，上线前无基线，首月实测后校准） | 上线后 30 天 |
| 兑换链路稳定 | 兑换接口成功率（不含「积分不足」「库存不足」等业务性拒绝） | ≥ 99.5% | 上线后 30 天 |
| 兑出去的券真的被用掉 | 核销率 = 已使用券数 /（已发放且已到期或已使用券数） | ≥ 50%（**假设值**，需首月数据校准） | 上线后 60 天 |
| 不超发、不重复扣分 | 库存超发张数、重复扣积分笔数 | 0 | 持续 |
| 两套有效期不混淆 | 关于「积分/券有效期」的客诉件数 | 每周 ≤ 2 件（**假设值**，无历史基线） | 上线后 4 周 |

---

## 2. 核心功能与优先级

| 优先级 | 功能 | 说明 |
|---|---|---|
| **P0** | 券模板与库存 | 运营配置券模板（面额、门槛、所需积分、有效期天数、库存、每人上限、上下架），会员端券中心可见在架且有余量的模板 |
| **P0** | 兑换（扣积分发券） | 兑换即扣积分、即发券，进「我的券包」；全过程原子化，失败不扣分、不占库存 |
| **P0** | 我的券列表与状态 | 券包按状态分组：未使用 / 已使用 / 已过期（+ 内部状态已作废）；展示面额、门槛、到期日 |
| **P0** | 过期处理 | 券到期自动作废，**不延期、不补发、不折现、不返还积分** |
| **P1** | 券的使用与核销 | 下单时选券抵扣（**同一订单最多一张、不可叠加**）；支付成功即核销，券状态置为已使用 |
| **P1** | 兑换记录查询 | 会员查自己的兑换记录：时间、券名、消耗积分数、券当前状态；分页倒序 |
| **P2** | 券到期提醒 | 到期前 3 天与到期当天各一次站内/订阅消息提醒（时点可运营配置，与积分到期提醒是两套规则） |
| **P2** | 积分临期展示 | 「我的积分」页展示可用积分 + 最近一批的到期日与分数，引导兑换 |

范围说明：P0 是不可拆的最小闭环（能兑、能看到、能过期），P1 决定这个闭环有没有业务价值（能核销），P2 是提效项，可后续迭代。

---

## 3. 兑换规则

### 3.1 券模板参数

每个券模板含以下业务参数，全部由运营后台配置，会员端只读：

| 参数 | 规则 |
|---|---|
| 券面额 | 抵扣金额，**以「分」为单位的整数**，禁止浮点。如 5 元存 500 分 |
| 使用门槛 | 订单金额需达到的金额，**以「分」为单位的整数**，0 表示无门槛 |
| 所需积分 | **整数积分，不设小数**（沿用积分存储口径），不允许 0 |
| 券有效期天数 | **1–90 天的整数**，硬上限 90 天，配置层不得允许超过 90（依据：《运营规范》第二条） |
| 库存总量 | 可发放总张数，整数；为 0 时视为已兑完 |
| 每人兑换上限 | 整数，建议区间 1–5，默认 1 |
| 上下架状态、兑换起止时间 | 下架或不在时间窗内不可兑换 |

**初始上架模板（运营首批配置，示例值，运营可调整）**：

| 模板 | 门槛（分） | 面额（分） | 所需积分 | 券有效期 | 初始库存 | 每人上限 |
|---|---|---|---|---|---|---|
| 无门槛立减 3 元券 | 0 | 300 | 300 | 7 天 | 1000 | 3 |
| 满 20 元减 5 元券 | 2000 | 500 | 500 | 30 天 | 500 | 2 |
| 满 30 元减 10 元券 | 3000 | 1000 | 900 | 60 天 | 300 | 1 |

换算口径：**所需积分按 1 积分 ≈ 1 分面额定基准**，运营可在面额的 50%–150% 区间内配置，且必须为整数。

### 3.2 兑换流程与校验顺序

会员在券中心点「立即兑换」→ 确认弹窗（展示：券名、面额、门槛、**本次消耗 N 积分**、**本券有效期至 YYYY-MM-DD**、规则摘要）→ 确认 → 服务端按以下顺序校验，任一步失败则整笔不生效（**不扣积分、不占库存、不发券**）：

1. 模板在架、在兑换时间窗内 → 否则 `COUPON_OFF_SHELF`
2. 该会员在本模板下已兑换张数 < 每人上限 → 否则 `COUPON_LIMIT_REACHED`
3. 会员**可用积分**（未过期部分）≥ 所需积分 → 否则 `POINTS_INSUFFICIENT`，返回差额
4. 原子扣减库存 1 张 → 库存为 0 则 `COUPON_SOLD_OUT`
5. 扣减积分（**先到期先扣**，见 3.3）、生成券、写积分流水与兑换记录

**积分扣减规则（依据前序任务《核对会员积分三条口径并给出结论》）**：按到期时间升序扣减（**先到期先扣**）；到期时间相同的，按获得时间先后扣减；**积分不可转让**，只能由本账号本人兑换。已过期积分不参与兑换。

**幂等要求**：同一会员对同一模板的兑换请求必须按客户端幂等键去重，重复提交只生效一次（防重复扣分/重复发券）。同一会员并发请求以原子扣减结果为准。

### 3.3 每人每券兑换上限

- 「每人」指会员账号；「每券」指**券模板**，不是单张券。
- 计数口径：该会员在该模板下的**已兑换总张数**，包含未使用、已使用、已过期、已作废，全部计入上限。
- 达到上限后：按钮置灰并提示「每人限兑 N 张」，接口同样拒绝，不可通过重复请求绕过。
- 会员注销后重新注册视为新账号，不继承原上限计数（按新账号计）——这是一条**假设**，若运营认为应防止刷号，需另行补充风控规则（列入待确认）。

### 3.4 库存不足时的处理

- 库存口径：模板下**剩余可发放张数**，只减不增（运营追加库存属配置变更，需记录变更人、时间、前后值）。
- 库存为 0 时：会员端券卡显示「已兑完」、兑换按钮置灰不可点；接口返回 `COUPON_SOLD_OUT`。
- **不做候补、不排队、不预约、不补发**；不因库存不足给会员补偿积分。
- 库存扣减必须原子：并发下超发视为严重故障，需按 3.5 处理（作废多发的券并冲正）。

### 3.5 兑换后能否退回 / 撤销

- **会员侧：不可撤销、不退回积分。** 兑换是可即时使用的权益转移，兑换成功即视为消耗，弹出确认前必须二次确认。
- **例外（系统侧，唯一合法冲正路径）**：
  - 系统故障或重复请求导致重复发券 / 重复扣积分 → 系统自动冲正：**作废多发的券 + 返还对应积分**，并在兑换记录中留痕（原因、操作人=系统、时间）。
  - 冲正需在发现后的 24 小时内完成，且必须通知受影响会员。

### 3.6 退款 / 冲正时积分是否返还

分两种情形，不要混用：

1. **兑换本身被冲正**（3.5 的系统例外）→ 返还积分。返还的积分**按原批次剩余有效期回退；原批次已过期则不再返还**。
   **「待 CEO 确认」**：此条为建议口径，来自前序任务结论里「需 CEO 决定：退货/冲正返还的积分是否按原批次剩余有效期回退（原批次已过期则不再返还）」。若 CEO 改为「一律按返还当日重新计 12 个月」，积分有效期与流水的追溯逻辑要跟着改。
2. **订单退款导致券回退**（券被用掉了但订单退款）→ 此时**不返还积分**，处理券：**「待 CEO 确认」**建议——全额退款且券仍在有效期内，券退回券包并**沿用原到期时间（不延长）**；券已过期则不退回、不返还积分；部分退款不退券。

### 3.7 券的过期处理

- 到期日计算：**到期日 = 兑换成功当日 + 有效期天数 − 1 天**，最后可用时刻为该日 23:59:59。例：7 天券在 1 月 1 日兑换，到期日为 1 月 7 日 23:59:59。
- 券有效期**最长 90 天**，由模板配置（1–90 天整数）；**过期自动作废，不予延期**（依据：《运营规范》第二条）。
- 过期券：状态置为「已过期」，从「未使用」分组移出，**不补发、不折现、不返还积分**；运营侧**没有**手工延长有效期的能力。
- 过期判定需以服务端时间为准，不依赖客户端时间。

### 3.8 两套有效期规则的表述区分（重点，客诉高发点）

必须始终区分：**积分 12 个月**、**优惠券最长 90 天**，两套规则并行、互不替代（依据：《运营规范》第二条 + 前序任务结论）。

| 对象 | 有效期 | 起算点 | 到期后 |
|---|---|---|---|
| 积分 | 12 个月 | 获得时（具体起算点**待 CEO 确认**，见 3.12） | 自动作废，不予延期 |
| 优惠券 | 最长 90 天（模板 1–90 天） | 兑换成功时刻 | 自动作废，不予延期、不返还积分 |

用户端文案规则（工程与运营都要遵守）：
- **积分相关**文案只出现「12 个月」；**券相关**文案只出现「90 天」或该模板的实际天数/到期日。禁止出现「积分 90 天到期」「优惠券有效期 12 个月」这类混写。
- 兑换确认弹窗必须**并列两行**并各带小字：
  - 「本次消耗 500 积分（积分有效期 12 个月，扣减时先到期先扣）」
  - 「本券有效期至 2026-01-18（最长 90 天，过期自动作废、不延期）」
- 「我的券包」每张券只显示「有效期至 YYYY-MM-DD」，不展示「12 个月」；「我的积分」只显示积分的最近到期日，不展示「90 天」。
- 客服话术统一为一句话：「积分按获得时间算 12 个月，券按兑换时间算最长 90 天，两个计时起点不同。」

### 3.9 券的使用与核销（不可叠加）

- 下单时可选 1 张未使用且未过期的券；**同一订单最多使用一张优惠券，优惠券之间不可叠加使用**（依据：《运营规范》第二条）。
- 券可抵扣订单商品金额：订单商品金额（**以「分」为单位的整数**）≥ 门槛才可用，不足返回 `ORDER_BELOW_THRESHOLD`。订单已使用其他券再选券即 `ORDER_COUPON_CONFLICT`。
- 抵扣金额不超过订单商品金额，抵扣后应付金额不小于 0（**以「分」为单位的整数**计算，禁止浮点运算）。
- 核销时点：订单支付成功即核销，券状态置为「已使用」，写入核销时间、关联订单号、门店；核销后券不可再用、不可撤销（退款场景见 3.6）。
- 订单支付前取消/超时未支付：券不核销，仍为「未使用」，到期时间不变。
- 核销入口：会员出示券码（券号后 8 位或二维码）+ 店员端确认；本期**不做**线下 POS 深度对接（见第 6 节）。

### 3.10 金额与积分的存储口径（硬约束，依据《运营规范》第一条）

- **所有金额字段一律以「分」为单位的整数存储和传输，禁止使用浮点数**（券面额、使用门槛、订单金额、优惠金额、实付金额全部适用）；界面展示时再换算成元，**保留两位小数**（如 500 分显示「¥5.00」）。
- **积分同样以整数存储、明确不设小数**（依据前序任务结论的存储建议），不要复用金额字段，也不要把积分与「分」混存同一字段。
- 工程实现注意：金额与积分的比较、加减、抵扣计算全部走整数运算；不允许在接口层出现 `float`/`double`，序列化后不得出现小数。

### 3.11 用户数据与隐私（依据《运营规范》第三条）

- 手机号在**任何页面**（券包、兑换记录、店员核销页、后台列表）**只显示后四位**，其余用 `*` 代替，如 `138****5678`。
- 用户数据**自注销之日起保存 3 年后删除**。
- **「待 CEO 确认」**：账号注销后剩余积分的处理，建议为「**剩余积分清零、未使用券全部作废，不予恢复或折现**」。说明：清零指权益失效，与「数据保存 3 年」不冲突——3 年指数据留存。依据前序任务结论「需 CEO 决定：账号注销后剩余积分是否直接清零（与规范『数据保存 3 年』并存）」。

### 3.12 待 CEO 确认事项（按建议口径实现，定稿后回填）

以下四条在知识库里仍是待决项，本文按建议口径写出，**不要当作已定**：

| # | 待确认项 | 本文建议口径 | 若改口径的影响 |
|---|---|---|---|
| 1 | 积分 12 个月的**起算点** | 自积分获得之日起算，对月对日计算到期日，无对日取当月最后一日 | 影响积分批次到期日计算与临期提醒触发，不影响本需求主流程 |
| 2 | 积分**到期提醒** | 到期前 30 天、7 天各提醒一次（订阅消息 + 站内） | 只影响提醒任务，不影响兑换 |
| 3 | **退款/冲正返还**积分的有效期 | 按原批次剩余有效期回退，原批次已过期则不再返还（见 3.6） | 影响积分流水与券回退逻辑 |
| 4 | 注销后**积分清零** | 清零 + 券作废（见 3.11） | 影响注销流程与客服话术 |

---

## 4. 用户故事

### 故事 1【P0】兑换成功

作为**有足够积分的会员**，我想要**用积分兑换一张店内优惠券**，以便**下次到店消费时抵扣现金**。

验收标准：
- Given 会员可用积分 800 分、券模板「满 20 元减 5 元券」在架且有余量、所需积分 500 分、该会员此模板已兑 0 张（上限 2 张），When 会员在券中心确认兑换，Then 扣除 500 积分（**先到期先扣**）、生成 1 张券、券包「未使用」分组可见，券面额显示「¥5.00」（存 500 分）、到期日为兑换当日 + 30 天 − 1 天，并写入兑换记录（消耗 500 积分）与积分流水
- Given 会员可用积分由 500 分（3 月 1 日到期）和 300 分（9 月 1 日到期）两批组成，When 兑换需 500 积分的券，Then 只扣 3 月 1 日到期的 500 分，剩余可用 300 分
- Given 会员连续点击「确认兑换」两次（网络重试），When 服务端收到两条相同幂等键的请求，Then 只成功一次，积分只扣一次、只发 1 张券

### 故事 2【P0】库存不足

作为**想兑换限量券的会员**，我想要**知道券是否还能兑**，以便**不被扣了积分却拿不到券**。

验收标准：
- Given 券模板剩余库存为 0，When 会员打开券中心，Then 该券卡显示「已兑完」、兑换按钮置灰不可点
- Given 会员在旧页面点下兑换（此时库存已被他人兑完），When 提交请求，Then 返回「库存不足」（`COUPON_SOLD_OUT`），**不扣积分、不发券**，页面刷新后按钮置灰
- Given 100 名会员同时抢剩余 1 张库存，When 并发请求全部到达，Then 只有 1 人成功，其余返回库存不足，库存不为负、无超发；若发生超发，作废多发券并返还对应积分
- Given 会员兑换成功后库存由 1 变 0，When 再次兑换，Then 库存不足拦截，且此前的成功兑换不受影响（不回收券）

### 故事 3【P0】积分不足 / 积分已过期

作为**积分不够或积分已过期的会员**，我想要**清楚知道还差多少、为什么不能用**，以便**知道怎么才能兑到券**。

验收标准：
- Given 会员总积分 600 分，其中 400 分已于上月到期作废（已过期积分不参与兑换）、可用积分仅 200 分，When 兑换需 500 积分的券，Then 返回「积分不足」（`POINTS_INSUFFICIENT`）并提示「还差 300 积分」，**不扣分、不发券**，并给出「如何获得积分」入口
- Given 会员在「我的积分」页看到可用积分，When 查看明细，Then 已过期积分单独标注「已过期」且不计入可用积分，页面文案只写「积分有效期 12 个月」，不出现「90 天」
- Given 会员兑换时可用积分刚好等于所需积分（如 500 分兑 500 分），When 确认兑换，Then 兑换成功、可用积分归零，不因取整或浮点误差失败
- Given 会员账号被冻结/注销，When 发起兑换，Then 拒绝并提示账号不可用，不扣分不发券

### 故事 4【P1】券包与过期、核销

作为**已兑换券的会员**，我想要**在券包里看到券的状态和到期日，并在下单时用掉它**，以便**不错过优惠**。

验收标准：
- Given 会员有 3 张券（未使用 / 已使用 / 已过期各 1 张），When 打开券包，Then 按状态分组展示，已过期券标注「已过期」且不可选用于下单
- Given 一张券已过到期日 23:59:59，When 系统跑过期任务（或该券被访问），Then 状态置为「已过期」，**不延期、不补发、不返还积分**，兑换记录中该券状态同步更新
- Given 会员订单商品金额 1800 分（未达 2000 分门槛），When 选用「满 20 元减 5 元券」，Then 提示未达使用门槛，不可选
- Given 会员已为该订单选用 1 张券，When 再选第二张券，Then 拦截并提示「同一订单最多使用一张优惠券」（**不可叠加**）
- Given 订单使用券并支付成功，When 核销完成，Then 券状态为「已使用」并记录核销时间、订单号、门店，券包「未使用」分组不再展示该券
- Given 券已核销，When 会员查看该券，Then 不可再次使用；券码、券包、兑换记录页的手机号均只显示后四位

### 故事 5【P2】兑换记录与到期提醒

作为**会员**，我想要**查历史兑换记录并收到券到期提醒**，以便**管理自己的积分与券**。

验收标准：
- Given 会员兑换过 12 张券，When 打开兑换记录，Then 按兑换时间倒序分页展示券名、消耗积分、券状态、兑换时间，且只能看到自己的记录
- Given 一张券将在 3 天后到期且仍未使用，When 触发提醒任务，Then 发送 1 次到期提醒；到期当天再发 1 次，同一券同一时点不重复发送
- Given 券到期提醒文案，When 审核文案，Then 只出现券的有效天数或到期日，不出现任何积分的有效期表述（两套规则不混用）

---

## 5. 验收标准（整体上线验收清单）

除上面各故事的验收标准外，上线前必须逐条通过：

1. **金额以「分」整数存储**：券面额、门槛、订单金额、优惠金额、实付金额全部为整数「分」，数据库与接口均不出现浮点；界面展示换算为元并保留两位小数（¥5.00 / ¥0.50）。依据《运营规范》第一条。
2. **券有效期 ≤ 90 天**：模板配置超过 90 天时保存失败或被强制收敛到 90；到期自动作废、**不延期**（后台无延期入口）。依据《运营规范》第二条。
3. **不可叠加**：同一订单使用第二张券时被拦截；**同一订单最多一张券**的校验在服务端生效，不依赖前端。依据《运营规范》第二条。
4. **积分口径**：有效期 12 个月、扣减**先到期先扣**（同到期日按获得时间先后）、**不可转让**（无转赠入口、接口不接受他人账号）；积分以整数存储、不设小数。
5. **两套有效期表述分离**：券相关页面/文案不出现「12 个月」，积分相关不出现「90 天」；兑换确认弹窗并列展示两行说明。
6. **待 CEO 确认四项**已按 3.12 的建议口径实现，并在代码/配置里做成可切换（起算点、到期提醒、退款返还、注销清零），避免定稿后返工。
7. **隐私**：所有涉及手机号的页面只显示后四位；注销后数据自注销之日起保存 3 年（含券、积分流水、兑换记录）。
8. **无超发、无重复扣分**：并发压测后库存与发券数一致，重复请求只生效一次。
9. **金额精度**：抵扣后应付金额不为负，0 元订单可正常提交（如无门槛 300 分券抵扣 300 分订单）。

---

## 6. 不做的范围

本期**明确不做**，不在开发范围，不接受 Sprint 中期追加：

1. **券转赠与分享**：不支持把券赠送给他人、生成分享海报/链接给他人领取，也不支持家庭共享、企业账号合并的积分池（与积分不可转让一致）。
2. **券叠加与组合**：不支持多张券叠加、不支持券 + 会员折扣 + 储值优惠 + 秒杀价同时生效；本期券只有「满 X 元减 Y 元」一种直减形态，不做折扣券、兑换券（如「1 元换购拿铁」）、商品券。
3. **券定向**：不做指定门店券、指定时段券（如「下午 2–5 点可用」）、指定商品券、指定会员等级券；本期所有券全门店通用。
4. **券退成积分**：不支持券 ⇄ 积分互转，不支持券兑换券，不支持券变现、找零、拆分。
5. **过期券的人工处理**：不做延期、补发、作废后返还积分；不做「即将过期券」的额外补偿。
6. **候补与抢券**：不做库存候补队列、预约、开抢提醒、限时秒杀抢购玩法。
7. **积分其他玩法**：不做积分抽奖、积分换购实物、积分直接抵现（积分当钱花）——本期积分只能兑券。
8. **线下系统对接**：不做 POS 深度改造、不做第三方平台（外卖平台/团购平台）券互通与核销。
9. **券的自动化运营**：不做智能推荐、个性化发券、券到店提醒（地理围栏）。
10. **多组织/多品牌**：不做多品牌多租户的券体系，本期只有这一个品牌的门店。

---

## 7. 风险与待解决问题

| 风险 | 可能性 | 影响 | 应对 |
|---|---|---|---|
| 会员混淆「积分 12 个月」与「券 90 天」，产生客诉 | 高 | 中 | 3.8 的文案规则 + 兑换弹窗并列展示 + 客服统一话术；上线后 4 周盯客诉件数 |
| 并发下库存超发 / 重复扣积分 | 中 | 高 | 原子扣减 + 幂等键 + 并发压测；超发按 3.5 冲正并通知 |
| 券被用于小额订单导致亏损（面额 > 订单金额） | 中 | 中 | 模板门槛必须 ≥ 面额（本批模板门槛均高于面额）；抵扣后应付金额不小于 0 |
| 积分负债一次性兑付（积分即将集中到期） | 中 | 中 | 首批库存与所需积分按 1.3 指标校准；必要时调整库存而不是延长券有效期 |
| 兑换所需积分与毛利不匹配，活动亏钱 | 中 | 高 | 所需积分区间受 50%–150% 约束，上线首月按核销率与客单价复盘 |
| 「待 CEO 确认」四项后期改口径导致返工 | 中 | 中 | 四项做成可配置，见第 5 节第 6 条 |

待解决问题：
- 前序四项（积分起算点、积分到期提醒、退款/冲正返还、注销清零）需 **CEO** 拍板——见 3.12。
- 券到期提醒的时点（建议到期前 3 天与到期当天）需 **运营** 确认，可配置。
- 新增券模板的审批人（谁能配 90 天券、谁能追加库存）需 **CEO** 指定，本文假设由运营负责人配置、由 CEO 复核。
- 防刷号规则（注销重注册是否重置每人上限）——需 **CEO 或运营** 决定，本文暂按「新账号新计数」处理（假设）。

---

## 8. 上线与回滚

- **上线顺序**：①运营配好首批 3 个模板（P0）→ ②内部与真实门店小范围灰度（1–2 家门店、会员券中心入口限白名单）→ ③观察 3 天无超发、无重复扣分后全量开放券中心 → ④再开券的使用与核销（P1）→ ⑤最后上提醒（P2）。
- **回滚触发**：出现以下任一情况，立即下架券模板（停止兑换）、保留已发券的正常核销：库存超发 > 0 且无法定位原因；重复扣积分 > 0 笔；兑换接口成功率 < 95% 持续 30 分钟。
- **回滚不影响已发券**：已发出的券按 3.7 规则正常到期作废，不回收、不延期。
- 上线后前 14 天每日看兑换成功率、库存余量、券核销率，异常阈值见 1.3。

---

<!-- 第二部分：数据表设计 —— 由工程师在此处之后追加，本文档不再重复表结构 -->

---

# 数据表设计（第二部分）

> 由工程师根据本文第一部分的 PRD 撰写。§9–§13 为字段、索引与约束说明，§14 是可执行 DDL，§15 是关键流程与风险，§16 是待确认/风险。
> 依据：公司资料《运营规范（2026 版）》第一、二、三条；前序任务《核对会员积分三条口径并给出结论》的结论。

## 9. 需求映射：PRD 规则 → 数据层落点

| # | PRD 条目（章节） | 数据层落点 | 说明 |
|---|---|---|---|
| 1 | 3.1 券模板参数（面额/门槛/所需积分/有效期 1–90 天/每人上限 1–5/上下架与兑换窗口） | `coupon_template` | 90 天用 CHECK 硬约束；金额为「分」整数；积分为整数 |
| 2 | 2、3.4 库存总量、剩余可发放张数、追加库存留痕 | `coupon_stock` + `coupon_stock_change_log` | 余量以 `remaining_stock` 为唯一权威，条件 UPDATE 扣减 |
| 3 | 3.2 校验顺序、「任一步失败不扣分/不占库存/不发券」 | `coupon_exchange_record` + 单事务 | 见 15.1 |
| 4 | 3.2 幂等键去重 | `uk_member_idem(member_id, idempotency_key)` | 唯一索引即幂等闸门 |
| 5 | 3.2 先到期先扣、同到期日按获得先后、已过期积分不参与 | `points_batch.remaining_points/expire_at` + `idx_member_fifo` | 见 15.2 |
| 6 | 3.3 每人上限（含未使用/已使用/已过期/已作废） | 按 `member_id + template_id` 统计兑换记录 | 见 15.1 步骤③ |
| 7 | 3.5 系统冲正、3.6 冲正返还积分（按原批次剩余有效期） | `coupon_exchange_record.status/reversed_*`、`points_ledger(REVERSE)`、`points_batch(source_type=4)` | 见 15.5 |
| 8 | 3.7 到期日计算、过期自动作废不延期 | `member_coupon.start_at/expire_at` + `idx_status_expire` | 见 15.4 |
| 9 | 3.8 两套有效期不能混用（积分 12 个月 / 券 ≤ 90 天） | 积分 `points_batch.expire_at`、券 `member_coupon.expire_at` 各自字段 | 见 ADR-4 |
| 10 | 3.9 核销（一单一张、不可叠加、支付成功即核销） | `member_coupon.used_at/order_no/store_id` + `idx_order` | 券只能用一次由条件 UPDATE 保证；叠加校验在订单侧 |
| 11 | 3.10 金额一律「分」整数、积分整数无小数 | 全部 `INT`/`BIGINT`，无 DECIMAL/FLOAT/DOUBLE | CI 检查见 §13 |
| 12 | 3.11 隐私：手机号仅后四位、注销后保存 3 年 | 不设计任何手机号字段；`member_points_account.account_status/deleted_at` | 见 15.6 |
| 13 | P2 券到期前 3 天与当天各提醒一次、同一券同一时点不重发 | `coupon_expire_notify_log` 唯一键 | 见 12.9 |
| 14 | P1 兑换记录倒序分页、仅本人可见 | `coupon_exchange_record` + `idx_member_time` | 查询条件强制带 `member_id` |

## 10. 设计约定（全表适用）

- **引擎与字符集**：`ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci`；要求 **MySQL ≥ 8.0.16**（低于此版本 CHECK 约束只解析不生效，会失去本文的关键防护）。
- **金额**：一律 `INT`，单位**分**，全库禁止 `FLOAT/DOUBLE/DECIMAL`。依据：《运营规范（2026 版）》第一条。`INT` 上限约 2147 万元，本场景足够；选 INT 而非 DECIMAL 是规范硬要求，不是精度测算结果。
- **积分**：`INT`（累计值用 `BIGINT`）整数，**不设小数**，独立字段，任何时候不与金额共用一个字段。
- **时间**：`DATETIME(3)`，一律服务端时间（UTC+8），不采信客户端时间；不用 `TIMESTAMP`（避开时区与 2038 问题）。
- **主键**：`BIGINT UNSIGNED AUTO_INCREMENT`；对外业务单号（兑换单号、券码）另设唯一键，不暴露自增 ID。
- **不建物理外键**：注销数据 3 年后要按 `member_id` 批量删除、历史表要归档，外键会阻塞；改由应用层 + 唯一索引保证，辅以每日对账（见 15.3）。理由见 ADR-3。
- **状态位**：用 `TINYINT UNSIGNED` + 字段注释声明取值域（见 §13），不做物理删除。
- **`status` 不加索引前缀过长**：所有会员维度索引统一以 `member_id` 打头，保证「单会员查询只扫自己那点数据」。

## 11. 实体关系概览

```
member_points_account(1) ──< points_batch(1) ──< points_ledger
        │ member_id              │ member_id        │ member_id
        └──────── 均以 member_id 关联，手机号等用户信息不落库 ────────┘

coupon_template(1) ──(1) coupon_stock                     ← 库存：一模板一行
coupon_template(1) ──< coupon_stock_change_log            ← 运营追加/下调留痕
coupon_template(1) ──< coupon_exchange_record(1) ──(1) member_coupon
                                                           │
member_coupon(1) ──< coupon_expire_notify_log   （P2 提醒去重）
```

关系要点：一次兑换成功 = 1 条兑换记录 + 1 张持有券（本期 `quantity` 恒为 1）；积分扣减落在 1–N 个批次上，每个批次产生 1 条流水；库存是模板级的单行计数器。
## 12. 字段说明

说明：类型均为 MySQL 语法；「可空」指是否允许 NULL；时间字段均为 `DATETIME(3)`。

### 12.1 积分账户 `member_points_account`

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| member_id | BIGINT UNSIGNED | 否 | — | 会员 ID，全库唯一；手机号等用户信息不落本库 |
| available_points | INT | 否 | 0 | 可用积分 = 所有未过期批次剩余之和；**整数，无小数** |
| frozen_points | INT | 否 | 0 | 冻结积分（预留，本期恒为 0） |
| total_earned_points | BIGINT | 否 | 0 | 累计获得积分（含已过期），审计用 |
| total_consumed_points | BIGINT | 否 | 0 | 累计消耗积分（兑换） |
| total_expired_points | BIGINT | 否 | 0 | 累计过期作废积分 |
| account_status | TINYINT UNSIGNED | 否 | 1 | 1 正常、2 冻结、3 已注销；非 1 时拒绍兑换（PRD 故事 3 末条） |
| version | INT UNSIGNED | 否 | 0 | 乐观锁版本号 |
| created_at / updated_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | updated_at 自动更新 |
| deleted_at | DATETIME(3) | 是 | NULL | 注销满 3 年后数据清理的标记/删除依据（依据《运营规范》第三条） |

设计说明：本表的 `available_points` 是**冗余汇总**，不参与扣减决策；每次变动都必须与批次聚合在同一事务内保持一致（见 15.1、15.4 的对账等式）。

### 12.2 积分批次 `points_batch`（先到期先扣的依据）

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键；同到期时间时的第三排序键 |
| member_id | BIGINT UNSIGNED | 否 | — | 会员 ID |
| source_type | TINYINT UNSIGNED | 否 | — | 1 消费获得、2 活动赠送、3 人工补发、4 冲正返还、5 其他 |
| source_no | VARCHAR(64) | 是 | NULL | 来源业务单号（订单号/活动号/冲正单号） |
| granted_points | INT | 否 | — | 本批次获得积分（> 0） |
| remaining_points | INT | 否 | — | **本批次剩余积分**，先到期先扣只扣这个字段；整数无小数；CHECK `0 ≤ remaining ≤ granted` |
| start_at | DATETIME(3) | 否 | — | 积分获得时间（即 12 个月有效期的起算点，起算口径待 CEO 确认） |
| expire_at | DATETIME(3) | 否 | — | **积分过期时间** = start_at + 12 个月（对月对日，无对日取当月最后一日）；到点剩余归零作废 |
| status | TINYINT UNSIGNED | 否 | 1 | 1 有效、2 已用尽、3 已过期 |
| grant_idem_key | VARCHAR(64) | 是 | NULL | 发放侧幂等键，防重复发同一批积分（NULL 允许多行） |
| created_at / updated_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | — |

设计说明：一个批次 = 一次积分获得事件。券的有效期是 90 天、积分是 12 个月（PRD 3.8），两者的「过期时间」只存在本表和 `member_coupon` 各自的 `expire_at` 里，不共用字段。

### 12.3 积分流水 `points_ledger`（只增不改）

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| member_id | BIGINT UNSIGNED | 否 | — | 会员 ID |
| batch_id | BIGINT UNSIGNED | 是 | NULL | 关联 `points_batch.id`；账户级操作（如注销清零）可为 NULL |
| biz_type | VARCHAR(24) | 否 | — | `EARN`/`EXCHANGE`/`EXPIRE`/`REVERSE`/`CANCEL_CLEAR` |
| points_delta | INT | 否 | — | 积分变动：正=获得/返还，负=消耗/过期；**整数无小数**，CHECK `<> 0` |
| available_after | INT | 否 | — | 本次变动后账户可用积分（快照，审计用） |
| biz_no | VARCHAR(64) | 否 | — | 业务单号：兑换单号 / 订单号 / 冲正单号 / 过期任务批次号 |
| idempotency_key | VARCHAR(64) | 是 | NULL | 来源请求幂等键，便于排障 |
| operator_type / operator_id | TINYINT UNSIGNED / BIGINT UNSIGNED | 否 / 是 | 1 / NULL | 1 系统、2 会员、3 运营 |
| remark | VARCHAR(255) | 是 | NULL | 备注（如「冲正：超发作废」） |
| created_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | 发生时间；不设 updated_at（流水不可改） |

### 12.4 优惠券模板 `coupon_template`

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| template_code | VARCHAR(32) | 否 | — | 模板编码，唯一，运营可读 |
| name | VARCHAR(64) | 否 | — | 券名称，如「满 20 元减 5 元券」 |
| coupon_type | TINYINT UNSIGNED | 否 | 1 | 1 满减直减（本期唯一形态） |
| discount_amount | INT | 否 | — | **券面额，单位：分**（如 5 元存 500），CHECK `> 0` |
| threshold_amount | INT | 否 | 0 | **使用门槛，单位：分**；0 = 无门槛 |
| points_cost | INT | 否 | — | **所需积分，整数不设小数**，CHECK `> 0` |
| valid_days | SMALLINT UNSIGNED | 否 | — | **券有效期天数**，CHECK `BETWEEN 1 AND 90`（硬上限 90 天，依据《运营规范》第二条） |
| per_member_limit | TINYINT UNSIGNED | 否 | 1 | 每人兑换上限，CHECK `BETWEEN 1 AND 5`（PRD 3.3） |
| exchange_start_at / exchange_end_at | DATETIME(3) | 是 | NULL | 兑换窗口；NULL = 不限 |
| status | TINYINT UNSIGNED | 否 | 0 | 0 草稿、1 已上架、2 已下架、3 已结束；只有 1 可兑换 |
| display_order | INT | 否 | 0 | 券中心排序 |
| notes | VARCHAR(255) | 是 | NULL | 运营备注（目标人群、审批人） |
| created_by | BIGINT UNSIGNED | 否 | 0 | 创建人（运营） |
| created_at / updated_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | — |

设计说明：库存**不放在模板表**，而放 `coupon_stock`（一模板一行），避免两处双写导致超发；模板表只放配置。
### 12.5 券库存 `coupon_stock`

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| template_id | BIGINT UNSIGNED | 否 | — | 券模板 ID，**唯一**（一模板一行库存） |
| total_stock | INT | 否 | 0 | 累计可发放总量（运营追加库存时上调，只增） |
| issued_stock | INT | 否 | 0 | 已发放净张数（兑换 +1；系统冲正返还 -1） |
| remaining_stock | INT | 否 | 0 | **剩余可兑换张数**（防超卖的唯一扣减字段），CHECK `>= 0` |
| version | INT UNSIGNED | 否 | 0 | 乐观锁版本号（每次扣减 +1） |
| updated_by | BIGINT UNSIGNED | 否 | 0 | 最近操作人（0 = 系统） |
| created_at / updated_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | — |

设计说明：CHECK 恒等式 `issued_stock + remaining_stock = total_stock` 由数据库强制，任何遗漏的双写都会立刻报错，是库存自检的首道闸门。

### 12.6 库存变更留痕 `coupon_stock_change_log`（只记运营动作）

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| template_id | BIGINT UNSIGNED | 否 | — | 券模板 ID |
| change_type | TINYINT UNSIGNED | 否 | — | 1 初始配置、2 运营追加库存、3 运营下调修正 |
| delta_stock | INT | 否 | — | 本次总量变动（正=增，负=减） |
| total_stock_before / total_stock_after | INT | 否 | — | 变动前后总量 |
| remaining_stock_before / remaining_stock_after | INT | 否 | — | 变动前后余量（PRD 3.4：「记录变更人、时间、前后值」） |
| operator_type / operator_id | TINYINT UNSIGNED / BIGINT UNSIGNED | 否 / 是 | 1 / NULL | 操作人（管理员 ID） |
| remark | VARCHAR(255) | 是 | NULL | 变更原因/审批依据 |
| created_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | 变更时间 |

设计说明：**兑换造成的库存变化不写本表**，留痕在 `coupon_exchange_record`（单笔可追溯到会员、模板、券码），避免每次兑换写两行。

### 12.7 兑换记录 `coupon_exchange_record`

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| exchange_no | VARCHAR(32) | 否 | — | 兑换单号，对外唯一（不暴露自增 ID） |
| member_id | BIGINT UNSIGNED | 否 | — | 会员 ID |
| template_id | BIGINT UNSIGNED | 否 | — | 券模板 ID |
| template_name_snapshot | VARCHAR(64) | 否 | — | 券名快照（模板改名不影响历史记录） |
| quantity | SMALLINT UNSIGNED | 否 | 1 | 本次兑换张数，本期 CHECK `= 1` |
| points_cost_snapshot | INT | 否 | — | 兑换时单张所需积分快照 |
| total_points_cost | INT | 否 | — | 本次消耗积分合计（CHECK = 单张 × 张数），**整数无小数** |
| discount_amount_snapshot | INT | 否 | — | 面额快照，**单位：分** |
| threshold_amount_snapshot | INT | 否 | — | 门槛快照，**单位：分** |
| valid_days_snapshot | SMALLINT UNSIGNED | 否 | — | 券有效期天数快照（CHECK 1–90） |
| status | TINYINT UNSIGNED | 否 | 1 | 1 处理中、2 成功、3 已冲正 |
| coupon_id | BIGINT UNSIGNED | 是 | NULL | 生成的会员持有券 ID |
| coupon_code | VARCHAR(24) | 是 | NULL | 券码快照（会员在兑换记录里可查） |
| idempotency_key | VARCHAR(64) | 否 | — | 客户端幂等键；`(member_id, idempotency_key)` 唯一 |
| client_ip | VARCHAR(45) | 是 | NULL | 请求 IP（风控排障） |
| reversed_at | DATETIME(3) | 是 | NULL | 冲正时间（PRD 3.5 留痕） |
| reversed_by | BIGINT UNSIGNED | 是 | NULL | 冲正操作人（0/NULL = 系统） |
| reverse_reason | VARCHAR(255) | 是 | NULL | 冲正原因 |
| created_at / updated_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | created_at 即兑换时间（列表倒序分页用） |

### 12.8 会员持有券 `member_coupon`

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| coupon_code | VARCHAR(24) | 否 | — | **券码，全局唯一**；20 位随机大写字母 + 数字，不可枚举（不暴露自增/时间规律） |
| member_id | BIGINT UNSIGNED | 否 | — | 券归属会员；不存手机号（展示时从会员服务取脱敏值） |
| template_id | BIGINT UNSIGNED | 否 | — | 券模板 ID（每人上限计数维度） |
| exchange_id | BIGINT UNSIGNED | 否 | — | 来源兑换记录 ID，**唯一**（本期 1 兑换 = 1 券） |
| coupon_name_snapshot | VARCHAR(64) | 否 | — | 券名快照 |
| discount_amount | INT | 否 | — | 面额，**单位：分**（CHECK `> 0`） |
| threshold_amount | INT | 否 | 0 | 门槛，**单位：分** |
| start_at | DATETIME(3) | 否 | — | 生效时间 = 兑换成功时刻 |
| expire_at | DATETIME(3) | 否 | — | **券过期时间** = 兑换当日 + valid_days − 1 天 23:59:59.999；CHECK `expire_at > start_at` 且 `expire_at <= start_at + 90 天` |
| status | TINYINT UNSIGNED | 否 | 1 | 1 未使用、2 已使用、3 已过期、4 已作废 |
| used_at | DATETIME(3) | 是 | NULL | 核销时间 |
| order_no | VARCHAR(64) | 是 | NULL | 核销订单号 |
| store_id | BIGINT UNSIGNED | 是 | NULL | 核销门店 |
| write_off_operator_id | BIGINT UNSIGNED | 是 | NULL | 核销人（店员） |
| invalid_at | DATETIME(3) | 是 | NULL | 作废时间（冲正/注销） |
| invalid_reason | VARCHAR(64) | 是 | NULL | `SYSTEM_REVERSE` / `ACCOUNT_CANCEL` 等 |
| refund_no | VARCHAR(64) | 是 | NULL | 预留给「订单退款回退券」（PRD 3.6 第 2 条，待 CEO 定稿） |
| refund_return_at | DATETIME(3) | 是 | NULL | 同上，回退时间 |
| version | INT UNSIGNED | 否 | 0 | 乐观锁版本号 |
| created_at / updated_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | — |

### 12.9 券到期提醒发送记录 `coupon_expire_notify_log`（P2，建议与 P0 同批建表）

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT UNSIGNED | 否 | 自增 | 主键 |
| member_coupon_id | BIGINT UNSIGNED | 否 | — | 持有的券 ID |
| member_id | BIGINT UNSIGNED | 否 | — | 会员 ID（便于注销后批量清理） |
| notify_point | VARCHAR(16) | 否 | — | 提醒时点：`D-3`、`D0`（可配置） |
| send_channel | TINYINT UNSIGNED | 否 | 1 | 1 订阅消息、2 站内信 |
| send_status | TINYINT UNSIGNED | 否 | 1 | 1 已发送、2 发送失败 |
| sent_at | DATETIME(3) | 是 | NULL | 发送时间 |
| created_at | DATETIME(3) | 否 | CURRENT_TIMESTAMP(3) | — |

设计说明：`UNIQUE(member_coupon_id, notify_point, send_channel)` 就是 PRD 故事 5「同一券同一时点不重复发送」的一致性保证。
## 13. 索引与约束

### 13.1 索引清单（按用途）

| 表 | 索引名 | 字段 | 支撑的查询 / 作用 |
|---|---|---|---|
| points_batch | `idx_member_fifo` | (member_id, status, expire_at, start_at, id) | **先到期先扣**：取某会员的有效批次并按到期时间排序（无 filesort） |
| points_batch | `idx_status_expire` | (status, expire_at) | 积分过期任务扫描 |
| points_batch | `uk_member_grant_idem` | (member_id, grant_idem_key) | 发放幂等（防重复发批） |
| points_ledger | `idx_member_time` | (member_id, created_at, id) | 会员积分明细倒序分页 |
| points_ledger | `uk_biz` | (biz_type, biz_no, batch_id) | 同一业务单同一批次只记一笔（防重复扣分/重复返还） |
| points_ledger | `idx_batch` | (batch_id) | 按批次追溯（冲正、对账） |
| coupon_template | `uk_template_code` | (template_code) | 模板编码唯一 |
| coupon_template | `idx_status_order` | (status, display_order, id) | 券中心：在架模板列表 |
| coupon_stock | `uk_template` | (template_id) | 一模板一行；扣库存就是按此唯一键行锁 |
| coupon_exchange_record | `uk_exchange_no` / `uk_member_idem` | (exchange_no) / (member_id, idempotency_key) | 单号唯一 / **兑换幂等** |
| coupon_exchange_record | `idx_member_time` | (member_id, created_at, id) | P1 兑换记录倒序分页 |
| coupon_exchange_record | `idx_member_template` | (member_id, template_id, status) | **每人上限计数**（含已过期/已作废） |
| coupon_exchange_record | `idx_template_time` | (template_id, created_at) | 模板维度对账与盘点 |
| member_coupon | `uk_coupon_code` | (coupon_code) | **券码唯一**（核销时按券码定位唯一一张） |
| member_coupon | `uk_exchange` | (exchange_id) | 一兑换一券，防同一兑换单双重发券 |
| member_coupon | `idx_member_status_expire` | (member_id, status, expire_at, id) | 我的券包：按状态分组 + **按过期时间排序** |
| member_coupon | `idx_status_expire` | (status, expire_at) | **按状态 + 过期时间扫描**（过期任务） |
| member_coupon | `idx_order` | (order_no) | 订单退款/逆向时反查券 |
| coupon_expire_notify_log | `uk_coupon_point` | (member_coupon_id, notify_point, send_channel) | 提醒去重 |

> 注：`member_coupon` 的 `status` 只用 `TINYINT`（不是字符串），避免索引膨胀；所有列表查询的 WHERE 均以 `member_id` 开头，单会员数据量 < 100 行。

### 13.2 主键与唯一约束（必须存在）

- 全部 9 张表主键：`id BIGINT UNSIGNED AUTO_INCREMENT`。
- 唯一约束：`member_points_account.uk_member`、`coupon_template.uk_template_code`、`coupon_stock.uk_template`、`coupon_exchange_record.uk_exchange_no` + `uk_member_idem`、**`member_coupon.uk_coupon_code`**（券码唯一，验收项）+ `uk_exchange`、`points_ledger.uk_biz`、`coupon_expire_notify_log.uk_coupon_point`、`points_batch.uk_member_grant_idem`。

### 13.3 「分」与「90 天」在约束层的体现

**金额（分）**：所有金额字段均为 `INT`（`coupon_template.discount_amount/threshold_amount`、`member_coupon.discount_amount/threshold_amount`、`coupon_exchange_record.*_amount_snapshot`），全库无 `DECIMAL/FLOAT/DOUBLE`。验收方式：在 CI 中对建表脚本执行 `grep -inE "decimal|float|double|numeric|money" schema.sql`，**命中即阻塞合并**（架构适应度函数）。

**券有效期最长 90 天**（依据《运营规范》第二条）：双层写死，运营侧无绕过路径：
1. 配置层：`coupon_template.ck_tpl_valid_days CHECK (valid_days BETWEEN 1 AND 90)` —— >90 天直接保存失败（不静默收敛，避免「配了 120 天却发 90 天」的运营误解）。
2. 发放层：`member_coupon.ck_mc_max_90d CHECK (expire_at <= DATE_ADD(start_at, INTERVAL 90 DAY))` —— 即使模板被改、或代码写错，也生成不出超过 90 天的券。
3. 说明：PRD 3.7 的到期日 = 兑换当日 + valid_days − 1 天 23:59:59，按自然日算，实际时长严格小于 valid_days 天，所以**总是满足上述约束**；这条约束不是为了拦住正常数据，而是为了拦住错误代码/错误配置。

### 13.4 其他 CHECK 与状态取值域

| 表 | 约束 | 内容 | 意图 |
|---|---|---|---|
| member_points_account | ck_account_nonneg | available/frozen ≥ 0 | 积分不得为负 |
| points_batch | ck_batch_points | granted > 0 且 0 ≤ remaining ≤ granted | 扣不穿批次 |
| points_batch | ck_batch_expire | expire_at > start_at 且 < start_at + 13 个月 | 拦截写错的过期时间（「对月对日」无法直接 CHECK） |
| points_ledger | ck_ledger_delta | points_delta <> 0 | 不允许零变动流水 |
| coupon_template | ck_tpl_amount / ck_tpl_points | discount > 0，threshold ≥ 0，points_cost > 0 | 金额与积分为正 |
| coupon_template | ck_tpl_points_range | points_cost × 100 在 [面额×50, 面额×150] | PRD 3.1「1 积分≈1 分面额，50%–150% 区间」（整数乘法，不用除法避免取整误差） |
| coupon_template | ck_tpl_per_limit / ck_tpl_window | 每人上限 1–5；兑换窗口先后合法 | PRD 3.1/3.3 |
| coupon_stock | ck_stock | 三个库存数非负，且 issued + remaining = total | 防超发、防双写不一致 |
| coupon_exchange_record | ck_ex_qty / ck_ex_points | quantity = 1；total = 单张 × 张数 | 本期只兑 1 张，防积分记账错位 |
| member_coupon | ck_mc_amount / ck_mc_time / ck_mc_max_90d | 面额>0、门槛≥0、expire_at > start_at、≤ +90 天 | 金额口径与最长 90 天 |

状态取值域（均用 `TINYINT UNSIGNED`）：
- `member_coupon.status`：1 未使用 → 2 已使用 / 3 已过期 / 4 已作废；终态不可逆（除「订单退款退回券包」待定稿）。
- `coupon_exchange_record.status`：1 处理中 → 2 成功 → 3 已冲正。
- `points_batch.status`：1 有效 → 2 已用尽 / 3 已过期。
- `coupon_template.status`：0 草稿、1 上架、2 下架、3 结束；兑换前置校验 `status = 1`。

### 13.5 为什么不用物理外键（与被放弃的备选）

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 物理外键（`FOREIGN KEY ... CASCADE/RESTRICT`） | 引用完整性由数据库保证 | 注销 3 年后按 member_id 批量删除需按依赖顺序拆多次；归档拆表、数据迁移都会卡住；高并发写入多一步父表锁校验 | 不采用（ADR-3） |
| 应用层 + 唯一索引 + 每日对账 | 删除/归档自由；写路径无额外开销 | 脏数据不会当场报错，依赖对账发现 | **采用** |

对账三条等式（每日跑，任一不成立告警）：① `issued_stock + remaining_stock = total_stock`；② 某模板 `issued_stock` = 该模板有效发券数（`member_coupon` 中 `invalid_reason IS NULL OR invalid_reason <> 'SYSTEM_REVERSE'`）；③ 某会员 `available_points` = 其未过期批次 `remaining_points` 之和。
## 14. 完整可执行 DDL

> 环境：MySQL 8.0.16+（CHECK 生效），InnoDB，字符集 utf8mb4 / utf8mb4_0900_ai_ci。以下脚本可整段执行（已验证语法的一致性，但**未在真实数据库执行过**，上线前需在测试库跑一遍并 `SHOW CREATE TABLE` 核对）。

```sql
-- ============================================================
-- 积分兑换优惠券 · 建表脚本
-- 约定：金额单位=分（INT），积分单位=积分（INT，无小数），时间=服务端时间
-- ============================================================
CREATE DATABASE IF NOT EXISTS `coffee_member`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `coffee_member`;

-- 1. 积分账户
CREATE TABLE `member_points_account` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `member_id` BIGINT UNSIGNED NOT NULL COMMENT '会员ID（唯一）',
  `available_points` INT NOT NULL DEFAULT 0 COMMENT '可用积分=未过期批次剩余之和；整数无小数',
  `frozen_points` INT NOT NULL DEFAULT 0 COMMENT '冻结积分（预留，本期恒0）',
  `total_earned_points` BIGINT NOT NULL DEFAULT 0 COMMENT '累计获得积分（含已过期）',
  `total_consumed_points` BIGINT NOT NULL DEFAULT 0 COMMENT '累计消耗积分',
  `total_expired_points` BIGINT NOT NULL DEFAULT 0 COMMENT '累计过期作废积分',
  `account_status` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1正常 2冻结 3已注销',
  `version` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `deleted_at` DATETIME(3) NULL DEFAULT NULL COMMENT '注销标记；满3年后作为数据清理依据',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_member` (`member_id`),
  CONSTRAINT `ck_account_nonneg` CHECK (`available_points` >= 0 AND `frozen_points` >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='积分账户（余额为冗余汇总）';

-- 2. 积分批次（先到期先扣的依据）
CREATE TABLE `points_batch` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `member_id` BIGINT UNSIGNED NOT NULL COMMENT '会员ID',
  `source_type` TINYINT UNSIGNED NOT NULL COMMENT '1消费获得 2活动赠送 3人工补发 4冲正返还 5其他',
  `source_no` VARCHAR(64) NULL DEFAULT NULL COMMENT '来源业务单号',
  `granted_points` INT NOT NULL COMMENT '本批次获得积分（>0）',
  `remaining_points` INT NOT NULL COMMENT '本批次剩余积分（先到期先扣只扣此字段）',
  `start_at` DATETIME(3) NOT NULL COMMENT '积分获得时间（12个月起算点）',
  `expire_at` DATETIME(3) NOT NULL COMMENT '积分过期时间=start_at+12个月',
  `status` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1有效 2已用尽 3已过期',
  `grant_idem_key` VARCHAR(64) NULL DEFAULT NULL COMMENT '发放侧幂等键',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_member_grant_idem` (`member_id`, `grant_idem_key`),
  KEY `idx_member_fifo` (`member_id`, `status`, `expire_at`, `start_at`, `id`) COMMENT '先到期先扣排序扫描',
  KEY `idx_status_expire` (`status`, `expire_at`) COMMENT '积分过期任务扫描',
  CONSTRAINT `ck_batch_points` CHECK (`granted_points` > 0 AND `remaining_points` >= 0 AND `remaining_points` <= `granted_points`),
  CONSTRAINT `ck_batch_expire` CHECK (`expire_at` > `start_at` AND `expire_at` < DATE_ADD(`start_at`, INTERVAL 13 MONTH))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='积分批次（每笔获得一个批次，含剩余积分与过期时间）';

-- 3. 积分流水（只增不改）
CREATE TABLE `points_ledger` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `member_id` BIGINT UNSIGNED NOT NULL COMMENT '会员ID',
  `batch_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '积分批次ID；账户级操作可为空',
  `biz_type` VARCHAR(24) NOT NULL COMMENT 'EARN/EXCHANGE/EXPIRE/REVERSE/CANCEL_CLEAR',
  `points_delta` INT NOT NULL COMMENT '正=获得/返还，负=消耗/过期；整数无小数',
  `available_after` INT NOT NULL COMMENT '变动后账户可用积分（快照）',
  `biz_no` VARCHAR(64) NOT NULL COMMENT '兑换单号/订单号/冲正单号/过期任务批次号',
  `idempotency_key` VARCHAR(64) NULL DEFAULT NULL COMMENT '来源幂等键',
  `operator_type` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1系统 2会员 3运营',
  `operator_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '操作人ID',
  `remark` VARCHAR(255) NULL DEFAULT NULL COMMENT '备注',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_biz` (`biz_type`, `biz_no`, `batch_id`) COMMENT '同一业务单同一批次只记一笔',
  KEY `idx_member_time` (`member_id`, `created_at`, `id`) COMMENT '明细倒序分页',
  KEY `idx_batch` (`batch_id`),
  CONSTRAINT `ck_ledger_delta` CHECK (`points_delta` <> 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='积分流水（积分变动明细，不可改）';

-- 4. 优惠券模板
CREATE TABLE `coupon_template` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `template_code` VARCHAR(32) NOT NULL COMMENT '模板编码（唯一）',
  `name` VARCHAR(64) NOT NULL COMMENT '券名称',
  `coupon_type` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1满减直减（本期唯一）',
  `discount_amount` INT NOT NULL COMMENT '券面额，单位：分',
  `threshold_amount` INT NOT NULL DEFAULT 0 COMMENT '使用门槛，单位：分；0=无门槛',
  `points_cost` INT NOT NULL COMMENT '所需积分（整数，无小数）',
  `valid_days` SMALLINT UNSIGNED NOT NULL COMMENT '券有效期天数（1-90，硬上限90）',
  `per_member_limit` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '每人兑换上限（1-5）',
  `exchange_start_at` DATETIME(3) NULL DEFAULT NULL COMMENT '兑换开始时间，NULL=不限',
  `exchange_end_at` DATETIME(3) NULL DEFAULT NULL COMMENT '兑换结束时间，NULL=不限',
  `status` TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '0草稿 1上架 2下架 3结束',
  `display_order` INT NOT NULL DEFAULT 0 COMMENT '券中心排序',
  `notes` VARCHAR(255) NULL DEFAULT NULL COMMENT '运营备注',
  `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_template_code` (`template_code`),
  KEY `idx_status_order` (`status`, `display_order`, `id`),
  CONSTRAINT `ck_tpl_amount` CHECK (`discount_amount` > 0 AND `threshold_amount` >= 0),
  CONSTRAINT `ck_tpl_points` CHECK (`points_cost` > 0),
  CONSTRAINT `ck_tpl_points_range` CHECK (`points_cost` * 100 >= `discount_amount` * 50 AND `points_cost` * 100 <= `discount_amount` * 150),
  CONSTRAINT `ck_tpl_valid_days` CHECK (`valid_days` BETWEEN 1 AND 90),
  CONSTRAINT `ck_tpl_per_limit` CHECK (`per_member_limit` BETWEEN 1 AND 5),
  CONSTRAINT `ck_tpl_window` CHECK (`exchange_start_at` IS NULL OR `exchange_end_at` IS NULL OR `exchange_end_at` > `exchange_start_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='优惠券模板（面额/门槛以分为单位，券有效期最长90天）';

-- 5. 券库存（一模板一行）
CREATE TABLE `coupon_stock` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `template_id` BIGINT UNSIGNED NOT NULL COMMENT '券模板ID（唯一）',
  `total_stock` INT NOT NULL DEFAULT 0 COMMENT '累计可发放总量（只增）',
  `issued_stock` INT NOT NULL DEFAULT 0 COMMENT '已发放净张数（兑换+1，冲正-1）',
  `remaining_stock` INT NOT NULL DEFAULT 0 COMMENT '剩余可兑换张数（防超卖唯一扣减字段）',
  `version` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  `updated_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最近操作人（0=系统）',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_template` (`template_id`),
  CONSTRAINT `ck_stock` CHECK (`total_stock` >= 0 AND `issued_stock` >= 0 AND `remaining_stock` >= 0 AND `issued_stock` + `remaining_stock` = `total_stock`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='券库存（剩余可发放张数，只减不增）';

-- 6. 库存变更留痕（仅运营动作）
CREATE TABLE `coupon_stock_change_log` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `template_id` BIGINT UNSIGNED NOT NULL COMMENT '券模板ID',
  `change_type` TINYINT UNSIGNED NOT NULL COMMENT '1初始配置 2追加库存 3下调修正',
  `delta_stock` INT NOT NULL COMMENT '总量变动（正=增，负=减）',
  `total_stock_before` INT NOT NULL COMMENT '变动前总量',
  `total_stock_after` INT NOT NULL COMMENT '变动后总量',
  `remaining_stock_before` INT NOT NULL COMMENT '变动前余量',
  `remaining_stock_after` INT NOT NULL COMMENT '变动后余量',
  `operator_type` TINYINT UNSIGNED NOT NULL DEFAULT 3 COMMENT '1系统 2会员 3运营',
  `operator_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '操作人ID',
  `remark` VARCHAR(255) NULL DEFAULT NULL COMMENT '变更原因/审批依据',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  PRIMARY KEY (`id`),
  KEY `idx_template_time` (`template_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='券库存变更留痕（变更人/时间/前后值）';

-- 7. 兑换记录
CREATE TABLE `coupon_exchange_record` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `exchange_no` VARCHAR(32) NOT NULL COMMENT '兑换单号（对外唯一）',
  `member_id` BIGINT UNSIGNED NOT NULL COMMENT '会员ID',
  `template_id` BIGINT UNSIGNED NOT NULL COMMENT '券模板ID',
  `template_name_snapshot` VARCHAR(64) NOT NULL COMMENT '券名快照',
  `quantity` SMALLINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '兑换张数（本期恒为1）',
  `points_cost_snapshot` INT NOT NULL COMMENT '单张所需积分快照（整数无小数）',
  `total_points_cost` INT NOT NULL COMMENT '本次消耗积分合计',
  `discount_amount_snapshot` INT NOT NULL COMMENT '面额快照，单位：分',
  `threshold_amount_snapshot` INT NOT NULL COMMENT '门槛快照，单位：分',
  `valid_days_snapshot` SMALLINT UNSIGNED NOT NULL COMMENT '券有效期天数快照（1-90）',
  `status` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1处理中 2成功 3已冲正',
  `coupon_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '生成的持有券ID',
  `coupon_code` VARCHAR(24) NULL DEFAULT NULL COMMENT '券码快照',
  `idempotency_key` VARCHAR(64) NOT NULL COMMENT '客户端幂等键',
  `client_ip` VARCHAR(45) NULL DEFAULT NULL COMMENT '请求IP',
  `reversed_at` DATETIME(3) NULL DEFAULT NULL COMMENT '冲正时间',
  `reversed_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '冲正操作人（NULL=系统）',
  `reverse_reason` VARCHAR(255) NULL DEFAULT NULL COMMENT '冲正原因',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '兑换时间',
  `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_exchange_no` (`exchange_no`),
  UNIQUE KEY `uk_member_idem` (`member_id`, `idempotency_key`) COMMENT '兑换幂等',
  KEY `idx_member_time` (`member_id`, `created_at`, `id`) COMMENT '兑换记录倒序分页',
  KEY `idx_member_template` (`member_id`, `template_id`, `status`) COMMENT '每人上限计数',
  KEY `idx_template_time` (`template_id`, `created_at`) COMMENT '模板维度对账',
  CONSTRAINT `ck_ex_qty` CHECK (`quantity` = 1),
  CONSTRAINT `ck_ex_points` CHECK (`total_points_cost` = `points_cost_snapshot` * `quantity`),
  CONSTRAINT `ck_ex_days` CHECK (`valid_days_snapshot` BETWEEN 1 AND 90)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='兑换记录（扣积分+发券的凭证）';

-- 8. 会员持有券
CREATE TABLE `member_coupon` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `coupon_code` VARCHAR(24) NOT NULL COMMENT '券码（全局唯一，随机不可枚举）',
  `member_id` BIGINT UNSIGNED NOT NULL COMMENT '会员ID',
  `template_id` BIGINT UNSIGNED NOT NULL COMMENT '券模板ID',
  `exchange_id` BIGINT UNSIGNED NOT NULL COMMENT '来源兑换记录ID（唯一）',
  `coupon_name_snapshot` VARCHAR(64) NOT NULL COMMENT '券名快照',
  `discount_amount` INT NOT NULL COMMENT '面额，单位：分',
  `threshold_amount` INT NOT NULL DEFAULT 0 COMMENT '门槛，单位：分',
  `start_at` DATETIME(3) NOT NULL COMMENT '生效时间=兑换成功时刻',
  `expire_at` DATETIME(3) NOT NULL COMMENT '券过期时间=兑换当日+valid_days-1天 23:59:59.999',
  `status` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1未使用 2已使用 3已过期 4已作废',
  `used_at` DATETIME(3) NULL DEFAULT NULL COMMENT '核销时间',
  `order_no` VARCHAR(64) NULL DEFAULT NULL COMMENT '核销订单号',
  `store_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '核销门店',
  `write_off_operator_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '核销人（店员）',
  `invalid_at` DATETIME(3) NULL DEFAULT NULL COMMENT '作废时间',
  `invalid_reason` VARCHAR(64) NULL DEFAULT NULL COMMENT 'SYSTEM_REVERSE/ACCOUNT_CANCEL',
  `refund_no` VARCHAR(64) NULL DEFAULT NULL COMMENT '退款回退单号（预留）',
  `refund_return_at` DATETIME(3) NULL DEFAULT NULL COMMENT '退款回退时间（预留）',
  `version` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '发券时间',
  `updated_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_coupon_code` (`coupon_code`),
  UNIQUE KEY `uk_exchange` (`exchange_id`),
  KEY `idx_member_status_expire` (`member_id`, `status`, `expire_at`, `id`) COMMENT '券包分组+按过期时间排序',
  KEY `idx_status_expire` (`status`, `expire_at`) COMMENT '过期扫描（status=1 且 expire_at<=NOW）',
  KEY `idx_member_template` (`member_id`, `template_id`),
  KEY `idx_order` (`order_no`) COMMENT '退款时反查券',
  CONSTRAINT `ck_mc_amount` CHECK (`discount_amount` > 0 AND `threshold_amount` >= 0),
  CONSTRAINT `ck_mc_time` CHECK (`expire_at` > `start_at`),
  CONSTRAINT `ck_mc_max_90d` CHECK (`expire_at` <= DATE_ADD(`start_at`, INTERVAL 90 DAY))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='会员持有券（含券码、状态、生效/过期时间；最长90天）';

-- 9. 券到期提醒发送记录（P2，建议与 P0 同批建表）
CREATE TABLE `coupon_expire_notify_log` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `member_coupon_id` BIGINT UNSIGNED NOT NULL COMMENT '持有的券ID',
  `member_id` BIGINT UNSIGNED NOT NULL COMMENT '会员ID',
  `notify_point` VARCHAR(16) NOT NULL COMMENT '提醒时点：D-3 / D0',
  `send_channel` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1订阅消息 2站内信',
  `send_status` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1已发送 2发送失败',
  `sent_at` DATETIME(3) NULL DEFAULT NULL COMMENT '发送时间',
  `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_coupon_point` (`member_coupon_id`, `notify_point`, `send_channel`) COMMENT '同一券同一时点不重复发送',
  KEY `idx_member` (`member_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='券到期提醒发送记录（去重）';
```

> 脚本说明：不含 `INSERT` 初始化数据。首批 3 个模板的 `coupon_stock` 初始行需与模板同批写入（`total_stock` = 初始库存、`issued_stock` = 0、`remaining_stock` = `total_stock`），否则 `ck_stock` 与扣减逻辑会直接报错——这是有意为之，宁可报错也不能静默超发。

## 15. 关键流程与风险

### 15.1 兑换事务：边界与步骤（单库、单事务，全程无跨服务调用）

「扣库存 → 扣积分 → 发券 → 写流水 → 写兑换记录」全部在**同一个 InnoDB 事务**内，COMMIT 或回滚只有两种结果：要么全成，要么全不成（不扣分、不占库存、不发券，对应 PRD 3.2）。

| 步 | 操作 | SQL 要点 | 失败返回 |
|---|---|---|---|
| ① | 占幂等键并落兑换记录（status=1 处理中） | `INSERT ... uk_member_idem` | 1062 重复 → 回滚后读旧记录：status=2 返回该券；status=1 返回「处理中，请稍候」 |
| ② | 模板在架且处兑换窗口 | `SELECT ... FROM coupon_template WHERE id=?`，应用层校验 status=1 与时间窗 | `COUPON_OFF_SHELF` |
| ③ | 每人上限 | `SELECT COUNT(*) FROM coupon_exchange_record WHERE member_id=? AND template_id=? AND status IN (2,3)` | `COUPON_LIMIT_REACHED` |
| ④ | 原子扣库存 | `UPDATE coupon_stock SET remaining_stock=remaining_stock-1, issued_stock=issued_stock+1, version=version+1 WHERE template_id=? AND remaining_stock >= 1` | `COUPON_SOLD_OUT` |
| ⑤ | 可用积分汇总 + 算差额 | `SELECT COALESCE(SUM(remaining_points),0) FROM points_batch WHERE member_id=? AND status=1 AND remaining_points>0 AND expire_at>NOW(3)` | `POINTS_INSUFFICIENT`（返回差额） |
| ⑥ | 先到期先扣（锁批次） | `SELECT id, remaining_points FROM points_batch WHERE member_id=? AND status=1 AND remaining_points>0 AND expire_at>NOW(3) ORDER BY expire_at, start_at, id FOR UPDATE`，再用真值校验一次总额 | 同上 |
| ⑦ | 逐批扣减 | `UPDATE points_batch SET remaining_points=remaining_points-?, status=IF(remaining_points-?=0,2,1) WHERE id=? AND remaining_points>=?` | 异常 → 整笔回滚 + 告警 |
| ⑧ | 写积分流水 | 每批 1 条：`points_ledger(biz_type='EXCHANGE', points_delta=-扣减额, batch_id, biz_no=exchange_no)`；`uk_biz` 防重复记账 | — |
| ⑨ | 同步账户冗余 | `UPDATE member_points_account SET available_points=available_points-?, total_consumed_points=total_consumed_points+?, version=version+1 WHERE member_id=? AND available_points>=?` | 影响 0 行 → 回滚 + 告警 |
| ⑩ | 发券 | `INSERT member_coupon`，券码随机生成（冲突则重生），`expire_at` = 兑换当日 + valid_days − 1 天 23:59:59.999 | — |
| ⑪ | 回填兑换记录 | `UPDATE coupon_exchange_record SET status=2, coupon_id=?, coupon_code=? WHERE id=? AND status=1` | — |

**锁顺序（固定，防死锁）**：`coupon_stock`（模板行）→ `points_batch`（按 `(expire_at, start_at, id)` 升序）→ `member_points_account` → `member_coupon`。所有写路径（兑换、冲正、过期任务）遵循**先批次、后账户**的顺序；捕获 1213/1205 自动重试 2 次（幂等键保证重试不会重复扣分）。

### 15.2 「先到期先扣」在数据层的实现

- **唯一依据**：`points_batch.remaining_points`（按批次记剩余与过期时间）；`member_points_account.available_points` 只是汇总冗余，**不参与扣减决策**。
- **排序键**：`(expire_at, start_at, id)` 升序 = PRD 3.2「按到期时间升序；同到期日按获得时间先后」；`id` 作第三级兜底（同刻获得的情形 PRD 未定义，见 §16-6）。
- **关键索引**：`idx_member_fifo(member_id, status, expire_at, start_at, id)`——某会员的有效批次按“到期时间”直接范围扫描，无 filesort；积分过期任务走 `idx_status_expire(status, expire_at)`。
- **扣减粒度**：每笔兑换按批拆成 1–N 行更新 + 1–N 条流水。例：兑 500 分，批次 A 剩 300 分（3/1 到期）、批次 B 剩 400 分（9/1 到期）→ A 扣 300（status→2）、B 扣 200（剩 200），2 条流水共 `exchange_no`。
- **过期积分不参与**：`expire_at > NOW(3)` 同时出现在 SELECT 与 UPDATE，即使过期任务延迟也不会扣到过期积分；不依赖客户端时间。

### 15.3 防超卖、防重复扣分、对账

- **防超卖的唯一手段**：条件 UPDATE（`AND remaining_stock >= 1`）。InnoDB 行锁使同一模板行串行化，100 并发抢 1 张只有 1 个 UPDATE 影响行数=1，其余返回 `COUPON_SOLD_OUT`，库存不为负、不超发；`ck_stock` 再兜一层。
- **不加 Redis 分布式锁**：本场景峰值 < 1 QPS，条件 UPDATE 已足；引入缓存预扣需额外处理缓存与库的一致性、主从切换丢扣减，属于把简单问题做复杂（ADR-2）。
- **幂等**：`uk_member_idem(member_id, idempotency_key)`。并发同键时第二条 INSERT 在唯一索引上等待，第一条提交后报 1062 → 返回已有结果；第一条回滚则第二条正常执行。客户端每次用户点击生成新键，请求重试沿用原键。
- **对账与监控**：每日跑 §13.5 的三条等式；`remaining_stock`、`issued_stock` 与 `member_coupon` 张数不符立即告警并暂停模板兑换（PRD 第 8 节回滚触发条件）。

### 15.4 过期自动作废（券与积分批次）

- **券**：定时任务每 5 分钟执行，`UPDATE member_coupon SET status=3 WHERE status=1 AND expire_at <= NOW(3) ORDER BY expire_at LIMIT 500`，循环到影响 0 行为止；走 `idx_status_expire(status, expire_at)`。带 `status=1` 条件天然幂等，重复执行无副作用；不延期、不补发、**不返还积分**（PRD 3.7）。
- **懒判定兜底**：券包列表/详情/核销 SQL 一律带 `status=1 AND expire_at > NOW(3)`，任务延迟也不会让过期券被使用。
- **无延期路径**：全库除冲正/退款回退外**没有任何修改 `expire_at` 的写路径**，后台也不提供延期接口。
- **积分批次**：同任务把 `status=1 AND expire_at<=NOW(3) AND remaining_points>0` 的批次置 `status=3, remaining_points=0`，逐批写 `points_ledger(EXPIRE, -剩余) `，同事务内（批次 → 流水 → 账户）扣减 `available_points`、累加 `total_expired_points`。

### 15.5 冲正与退款回退

| 场景 | 数据动作（单事务） |
|---|---|
| 系统故障/重复请求导致多发（PRD 3.5） | 券 `status=4` + `invalid_reason='SYSTEM_REVERSE'` → 兑换记录 `status=3` + `reversed_at/reversed_by/reverse_reason` → 返积分：按原批次**新建一条 `source_type=4` 的批次并沿用原 `expire_at`**（原批次已过期则不返还，PRD 3.6 待确认）→ 写 `points_ledger(REVERSE, +n)` → 库存 `issued_stock-1 / remaining_stock+1` + 写 `coupon_stock_change_log`（change_type=2）。 |
| 订单退款导致券回退（PRD 3.6 情形 2，**待 CEO 确认**） | **本期不实现**，仅预留 `refund_no/refund_return_at` 字段；若定稿为「全额退款且券未过期则退回券包、沿用原 `expire_at`」，则只需一条 `UPDATE ... SET status=1, refund_no=?, refund_return_at=? WHERE id=? AND status=2 AND expire_at>NOW(3)`，不延长时间、不返还积分。 |

### 15.6 隐私与数据留存（依据《运营规范》第三条）

- **不设计明文手机号**：本模块 9 张表**没有任何手机号/姓名/地址字段**，统一用 `member_id` 关联；券包、兑换记录、核销页的手机号由会员服务返回 `phone_masked`（如 138****5678），由接口层脱敏中间件统一处理，本模块不缓存、不透出明文。
- **注销**：`account_status=3`（兑换接口直接拒绕）、`deleted_at` 打标；按 PRD 3.11（待 CEO 确认）清零积分（写 `CANCEL_CLEAR` 冲平 + 批次置过期）、未使用券置 `status=4 / invalid_reason='ACCOUNT_CANCEL'`（不返还库存）。
- **保存 3 年**：清理任务以 `member_points_account.deleted_at <= NOW() - INTERVAL 3 YEAR` 取 `member_id` 列表，按该列表批量删 `points_ledger/points_batch/member_coupon/coupon_exchange_record/coupon_expire_notify_log` 与账户行——这就是 §13.5 不建物理外键的原因。

### 15.7 容量与性能估算（**估算，未经压测**）

| 项 | 假设与结果 |
|---|---|
| 规模 | 假设 DAU 1000（社区店会员规模，验证方式：查近 90 天有下单会员数）、人均 20 次请求/天 → 日请求 2 万、平均 0.23 QPS、峰值按 3 倍 ≈ **0.7 QPS**；兑换渗透 15%/月 → ≤ 150 笔/天 |
| 数据量 | 首年 `member_coupon` ≤ 5 万行、`points_ledger` ≤ 50 万行、`points_batch` ≤ 10 万行 → 单实例 MySQL 足够，**不分库分表、不引缓存** |
| 关键查询 | 券包列表（`member_id, status, expire_at`）单会员 < 100 行；过期任务每批 500 行；先到期先扣扫 1–3 行批次 |
| 待验证 | 上线前必做 100 并发抢 1 张库存的压测，验证只有 1 个 UPDATE 成功、无超发（PRD 故事 2）；并用 1 万会员造数据验证券包与过期任务耗时 |

### 15.8 决策记录（ADR）

**ADR-1：积分采用「批次表 + 流水表」，不用单表余额**
背景：「先到期先扣」要求扣减时知道每笔积分的过期时间；单表余额只能做 FIFO 或按固定周期统一过期。决策：`points_batch` 存每批剩余与过期时间，`points_ledger` 存不可变变动明细。代价：一笔兑换需更新 1–3 行批次 + N 条流水（本场景可忽略）。备选：按到期月分桶列（最多 12 列，无法按批次冲正回退）、JSON 存批次（无法用 SQL 聚合与约束）。

**ADR-2：库存扣减用条件 UPDATE，不引 Redis 分布式锁**
背景：并发抢最后 1 张不能超发。决策：`UPDATE ... WHERE remaining_stock >= 1` + 唯一索引 + CHECK。代价：热点模板行锁排队（单模板约 1–2 千 TPS 上限，远高于 0.7 QPS 峰值）。备选：Redis 预扣库存（多一套缓存一致性运维，主从切换时可能丢扣减）。

**ADR-3：不建物理外键**
背景：注销满 3 年需按 `member_id` 批量删除，历史表要归档。决策：应用层 + 唯一索引保证引用完整性，每日对账发现脏数据。代价：写错数据不会当场报错，靠对账 + 告警兜底（§15.3）。

**ADR-4：积分与券的有效期各自独立字段**
背景：PRD 3.8 要求两套有效期不能混用（积分 12 个月 / 券 ≤ 90 天），这是客诉高发点。决策：`points_batch.expire_at` 只由积分获得逻辑写，`member_coupon.expire_at` 只由兑换逻辑按 `valid_days` 写并用 CHECK 限住 90 天。代价：两处计算代码；收益是改任一口径不会污染另一处。

## 16. 待确认/风险

| # | 事项 | 依据/现状 | 影响与建议 |
|---|---|---|---|
| 1 | **PRD 内部冲突**：§7 风险表写「模板门槛必须 ≥ 面额（本批模板门槛均高于面额）」，但 3.1 首批模板含「无门槛立减 3 元券」（门槛 0 < 面额 300） | 两条规则不能同时成立 | 数据层**不加** `threshold_amount >= discount_amount` 的 CHECK（否则首批券配不进去），改为运营配置时提示、默认不阻断。**需 PM/CEO 确认以哪条为准**（若确认「必须 ≥ 面额」，则删除无门槛券并补一条 CHECK）。 |
| 2 | 积分 12 个月起算点未定（PRD 3.12 #1） | 表结构不依赖具体口径 | `start_at/expire_at` 由配置化计算器写入（对月对日 / 当月最后一日），定稿后只需重算新批次，**不改表**。 |
| 3 | 冲正作废的券是否回退「每人上限」计数 | PRD 3.3 明说「已作废计入上限」 | 本设计按 `status IN (2,3)` 计入，系统故障也会占会员额度。若 CEO 定「系统冲正不占额度」，则改为排除 `SYSTEM_REVERSE` 或回退计数（需改计数 SQL）。 |
| 4 | 退款回退券口径（PRD 3.6 情形 2）未定稿 | 表内已预留 `refund_no/refund_return_at` | 本期不实现该状态回退；若定稿为「退回券包」，还需补一张券状态变更审计表（否则上次核销信息会被覆盖）。 |
| 5 | 注销后积分清零（PRD 3.11）未定 | `account_status/deleted_at` 已预留 | 清零与作废动作等 CEO 拍板后接注销流程；数据留存 3 年与之不冲突。 |
| 6 | 同刻获得、同到期日的批次扣减次序未定义 | `id` 升序兜底（= 先获得先扣） | 无业务影响，若运营有异议再改为按 `source_type` 优先。 |
| 7 | 「每人上限 1–5」写成了 CHECK 硬约束 | PRD 3.1 为「建议区间」 | 若运营要 10 张，需 DDL 变更（改 CHECK），不能只改配置——列为已知风险。 |
| 8 | 库存为 0 时模板仍为「上架」 | PRD 3.4 要求显示「已兑完」 | 不改模板 `status`，用 `remaining_stock=0` 判定「已兑完」并返 `COUPON_SOLD_OUT`，避免运营误下架影响已发券展示。 |
| 9 | 券码规则：PRD 要求「券号后 8 位或二维码」核销 | 券码为 20 位随机字符 | 券号后 8 位/二维码由前端与核销端渲，不新增字段（后 8 位仅用于出示，**不作为核销唯一键**，核销按完整券码定位）。 |
| 10 | **DDL 未经实际执行验证** | 当前无可运行的数据库/沙箱环境 | 本文 DDL 已逐句核对 MySQL 8.0.16 语法，但**未在真实库执行过**。上线前必须在测试库整段执行 + `SHOW CREATE TABLE` 核对，并验证 CHECK 实际生效（试插一条 91 天券应报错）。 |

The document ends here. <!-- PART2-CONTINUE -->
