# 技术方案：小账本 · 大学生生活费记账微信小程序（MVP）

| 项目 | 内容 |
|------|------|
| **文档状态** | Draft v0.2（已按测试第 1 轮审查意见 `docs/review-tech-plan-r1.md` 逐条修订，待第 2 轮审查；通过后转 Accepted） |
| **作者** | 工程（架构） |
| **版本** | v0.2 |
| **上游依据** | `docs/PRD.md` v0.2（P0-1 ~ P0-4、AC-1.x ~ AC-4.x、第 8/9/10 章）、`docs/design.md` v1.0（第 5 章首页 4 态、第 7/8 章） |
| **时间基准** | 与 PRD 一致：**D0 = 三方评审通过日**，W1/W2… 为周次。本方案默认 D0 后首个工作日为 W1 第 1 天。 |
| **范围** | 仅 MVP / P0。P1（预算、同步增强、数据导出）、P2（支付账单导入、图表、提醒）只做**不可逆设计**的预留，不排开发期。 |
| **本阶段产出物** | 仅文档。**不含代码**，本文出现的目录/接口/集合名均为约定，待评审通过后交付编码。 |

---

## 0. 结论摘要（TL;DR）

### 0.1 架构结论

| 问题 | 结论 |
|------|------|
| 是否使用微信云开发 | **是**。后端 = 微信云开发（云函数 + 云数据库），不是自建服务器。理由：免运维/免域名备案/天然拿到 openid、单端单主体、团队小、P0 无复杂事务与报表需求。**代价与对策见 ADR-001：通过「单一 API 入口 + 自定义 token + 数据访问层」隔离平台，保留迁移到自建 Node.js 服务的路径。** |
| 小程序技术 | **微信小程序原生 + TypeScript**，不引入 Taro / uni-app（单端投放，跨端收益为 0）。 |
| 数据库 | **云开发数据库（MongoDB 兼容文档库）**，集合口径统一为：**P0 共 6 个集合 = 3 个业务集合（`users` / `transactions` / `categories`）+ 3 个辅助集合（`sessions` / `events` / `txn_dedup`）**；另有 **1 个 P1 预留集合 `budgets`（P0 只建集合、不写入、不暴露接口）**。见 4.1。 |
| 金额 | 全部以**整数「分」**存储与传输，禁止浮点数与字符串金额参与运算（见 7.1）。 |
| 时间 | 业务日期用 `'YYYY-MM-DD'` 字符串 + 冗余 `month`（`'YYYY-MM'`）；时间戳用 UTC 毫秒数；**「今天」一律取服务端 `serverDate`**（对齐 AC-1.8 / PRD 8.4）。 |
| 登录 | `wx.login → 服务端换 openid → 签发自定义 opaque token`，token 存 `sessions` 集合，7 天有效，过期由前端静默重登一次自动续期。 |
| 幂等 | `clientRequestId` 去重键**独立存 `txn_dedup` 集合**（唯一索引 + `expireAt = 创建时间 + 24h`），**严格按 PRD 8.2：窗口内视为重复、超窗放行新建**（见 4.8 / ADR-009）。 |
| P0 四项功能可行性 | **功能上全部可行**（无需削减任何 P0 功能），但**人日需求 > W1–W3 可用产能，档位 B 裁剪后仍有 3.7 人日缺口**，处置方式为「顺延 W4 内测启动 2 天，不压缩 W3 自测」，另见 0.2 / 0.3 的待决策项与 8.2 产能账户。 |

### 0.2 P0 可行性评估（PRD 逐条）

| P0 功能 | 结论 | 关键依据 / 风险 |
|---------|------|-----------------|
| **P0-1 微信授权登录与用户初始化**（AC-1.1~1.8） | ✅ 可行 | `wx.login` 静默拿 code 不需要用户点击（不需要授权弹窗），AC-1.1/1.2/1.3 可满足。**AC-1.8（服务端 `serverDate`）通过统一响应包携带 `serverDate` 实现，任何接口调用都能校正「今天」**。冷启动首屏 ≤2.5s **有云函数冷启动风险**，对策见 7.5；建议把该 AC 的口径明确为「热启动 P95 ≤ 2.5s，冷启动允许 3s + 骨架屏」。 |
| **P0-2 手动记账**（AC-2.1~2.18） | ✅ 可行 | 幂等（AC-2.15）由 `clientRequestId` + **`txn_dedup` 去重键集合的唯一索引**在服务端兜底，**去重窗口 24h、超窗放行新建，与 PRD 8.2 语义一致**（v0.2 已按审查意见 1 修订，见 4.8 / ADR-009）；写失败不假成功（AC-2.14/2.18）走「无乐观更新 + 幂等重试」。**AC-2.10 的 3 秒撤销**在实现上是「删除接口 + 提示条」，技术上无风险，但 3 秒是否够（PRD OQ-6）属交互决策，不影响排期。 |
| **P0-3 账单流水列表**（AC-3.1~3.15） | ✅ 可行 | 分页采取游标（`date + createdAt + _id`）而非 `skip`，避免深分页性能退化；月度合计随第一页返回，避免二次请求。 |
| **P0-4 月度统计与剩余可用额度**（AC-4.1~4.14） | ✅ 可行 | **主卡 4 态（空账本/无收入基线/正常/历史月）由服务端判定 `cardState` 并下发结构化数值，前端只做渲染分支，不在两端各写一套状态推导**——这是让 AC-4.13/4.14 可测的关键设计。**历史月只切主卡口径，Top5 与最近记录仍返回所选月份数据**（v0.2 已按审查意见 3 修正示例与设计要点）。2000 条记录 P95 ≤ 500ms（AC-4.11）经索引设计可达，**但冷启动不在此口径内**，见 7.4 / 0.3（需 PM 确认口径）。 |

**结论：PRD 的 P0 功能范围不需要削减，且在 2 名工程师前提下技术上可在 MVP 周期内交付**。但排期**不是「刚刚好」**：按 8.1 人日估算（档位 A ≈ 35.0 人日）与 W1–W3 可用产能（2 人 × 3 周 = 30 人日）对比，**存在 −5.0 人日缺口**；采用 8.3 档位 B 裁剪后仍有 −3.7 人日，须占用 W4 前 2 个工作日收尾（详见 8.2 产能账户）。下列 5 项需 PM/测试/CEO 决策，否则会在 W2–W3 产生返工或验收争议：

| # | 需确认事项 | 影响 | 建议 |
|---|-----------|------|------|
| 1 | **性能 AC 口径**：**AC-1.1（首屏 ≤2.5s）、AC-2.16（保存 P95 ≤800ms）、AC-4.11（统计 P95 ≤500ms）** 是否包含云函数冷启动？`[v0.2 修订：审查意见 7 —— 原仅列 AC-1.1/AC-4.11，现补 AC-2.16]` | 冷启动 1–3s 会击穿这三个 AC，导致测试判定失败（AC-2.16 的 800ms 同样无法容纳冷启动） | 口径改为「**热调用（同一实例 5 分钟内）**：AC-1.1 ≤2.5s、AC-2.16 ≤800ms、AC-4.11 ≤500ms；冷启动场景以『骨架屏 + ≤3s 可交互』验收」。属测试与 PM 共同确认项。 |
| 2 | **分类「最近 30 天频次排序」**（PRD 6.2）目前**没有对应 AC**，属隐性需求，需要一次额外聚合查询 | 实现 +0.5 人日 + 每日一次聚合开销 | 二选一：① PM 补 AC（我按现状实现）；② 降级为「固定顺序 + 本地记住最近 3 次使用的分类置顶」，语义更可测、成本更低（design.md 8.5 亦建议固定顺序）。**建议 ②**。 |
| 3 | **OQ-2 数据删除合规底线**：P0 是否需要小程序内「清空账本 / 注销」入口 | 若上线前才决定，会挤到 W3 且可能影响审核 | 工程建议：**P0 就做**（1 个接口 + 1 个设置页入口，约 0.5 人日），因为「用户无法删除自己的数据」是审核与合规硬伤。 |
| 4 | **`docs/PRD.md` 未定义分页游标语义与「加载到底」判定** | 前端实现会自行拍脑袋，测试无据可依 | 本方案 5.4.8 已定义 `cursor` 契约，请 PM 知悉并在 PRD 追加或引用本文。 |
| 5 | **幂等实现依赖云数据库事务**（跨集合「去重键 + 记录」原子写） | 若平台事务不支持/语义有差异，需走「补偿 + 孤儿键清理」退化路径（见 4.8 / R-9） | W1 实测确认（8.5 清单），**不需要 PM 决策**，但需测试知悉退化路径的验证方式（10.2 已列用例）。 |

### 0.3 需 CEO / 依赖方决策的事项

1. **小程序主体资质与类目**（PRD 12.1 已列，Owner = CEO）：未就绪则 **W1 无法真机联调，全排期顺延**。这是关键路径上的第一风险。
2. **云开发套餐与费用**：云开发为按量/套餐收费（免费版配额有限，具体额度以腾讯云控制台当前政策为准，W1 核对）。预估 MVP/灰度期 **每月百元以内量级**，但需 CEO 确认可开通付费套餐。另：企业主体小程序认证费（约 300 元/年）是否走公司主体，需 CEO 定。
3. **是否接受「后端绑定微信云开发」这一平台风险**（ADR-001 的核心权衡）：若 CEO 认为未来 6 个月内必须要多端（App/H5）或必须自建后端，则应现在就选自建，**不要等到有用户再迁**。
4. **测试环境「时间旅行」开关**（见 10.3）：为验证 AC-1.8 / AC-4.13 / AC-4.14 与跨月、幂等窗口场景，工程计划在 **dev/体验版环境**提供可注入服务端日期（及幂等键过期）的调试参数。生产环境强制关闭并在 CI 校验。此项需 CEO 确认「允许存在调试开关」的口径（安全审查关注点）。
5. **人力与排期**（v0.2 新增，见 8.2）：按 2 名工程师全职，档位 B 裁剪后仍需占用 W4 前 2 个工作日收尾，**W4 的 20 人内测窗口由 5 天压缩到 3 天**（仍在 W4 周内，不突破 PRD 13 章 Gate）。若 CEO 只能给 1 名工程师，则需**整体顺延约 2 周**，或由 PM 同意降级部分 AC（工程不建议）。

---

## 1. 对架构有约束的需求条款（来自 PRD，只摘约束）

| PRD 条款 | 对架构的硬约束 |
|----------|----------------|
| 第 5 章 / 11 章 | P0 仅有 4 个功能；不做预算、图表、离线上传、支付导入、自定义分类、社交。**架构不得为 P1/P2 提前建设**（不做架构宇航员）。 |
| PRD 7.3 | 所有业务数据读写必须绑定当前 openid，**服务端强制校验，不信任客户端传入的用户标识**。 |
| PRD 8.2 | 金额以「**分**」（整数）存储；`date` 为业务日期且**不得晚于服务端 `serverDate`**；统计归月以 `date` 为准；需 `(openid, date)` 复合索引。 |
| PRD 8.2 / AC-2.15 | `clientRequestId` 幂等键，**去重窗口 = 同一 openid 下 24 小时**；**窗口内重复只落库一次并返回首次结果，超出窗口的同值视为新请求**；需在技术方案中说明**存储位置与清理策略** → 见 4.8（`txn_dedup` 集合）。 |
| PRD 8.4 / 6.4.3 / AC-1.8 | 全系统 `Asia/Shanghai`；「今天」= 服务端 `serverDate`；**禁止使用设备本地时间**；服务端需拦截未来日期。 |
| PRD 9 章 | 首屏 ≤2.5s、统计 P95 ≤500ms、**保存 P95 ≤800ms（AC-2.16）**、主包 ≤1.5MB、请求超时 8s、接口成功率 ≥99.5%、崩溃率 <0.5%、限流、日志脱敏。 |
| PRD 第 10 章 | 11 类埋点事件必须可查、可聚合（含 `duration_ms`、`main_card_state` 等参数）。 |
| PRD 6.5 | **所有写操作失败时界面数值必须与云端一致，禁止客户端先行变更造成假成功。** |
| PRD 6.4.1 / 6.4.2 | 主卡 4 态状态机；**历史月仅主卡切换为「结余回顾」口径**（不展示日均可用/剩余天数/进度条）——**未取消 Top5 与最近记录**。 |
| PRD 12.3 OQ-1 | 静默登录可行性由工程确认 → 本方案结论：**可行**，`wx.login` 不触发授权弹窗（见 6.1）。 |

---

## 2. 整体架构

### 2.1 容器级架构图（C4 Level 2）

```
┌──────────────────────────────────────────────────────────────┐
│  用户手机 · 微信客户端                                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  小账本小程序（原生 + TypeScript，主包 ≤1.5MB）            │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │  │
│  │  │ pages/   │ │ pages/   │ │ pages/   │ │ pages/     │ │  │
│  │  │ home     │ │ bills    │ │ detail   │ │ settings   │ │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │  │
│  │       └────────────┴────────────┴─────────────┘        │  │
│  │                    │                                   │  │
│  │        ┌───────────▼────────────┐  ┌─────────────────┐ │  │
│  │        │ services/api.ts (唯一出口) │  │ store (内存态)  │ │  │
│  │        │ 统一 token / serverDate  │  │ 统计&列表缓存    │ │  │
│  │        │ 错误码映射 / 重试 / 埋点   │  └─────────────────┘ │  │
│  │        └───────────┬────────────┘                       │  │
│  │    utils: amount(分) / date(Asia-Shanghai) / validate    │  │
│  └────────────────────┼────────────────────────────────────┘  │
└───────────────────────┼──────────────────────────────────────┘
                        │ wx.login() + wx.cloud.callFunction({name:'api', data:{action, token, payload}})
                        │ （不直连数据库！见 7.3）
┌───────────────────────▼──────────────────────────────────────┐
│  微信云开发环境（dev / prod 双环境隔离）                        │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  云函数 api（Node.js 18，单函数 + action 路由）           │  │
│  │  1) 鉴权中间件：token → sessions → openid → user.status  │  │
│  │  2) 限流中间件（写接口 60 次/分钟/用户）                   │  │
│  │  3) 参数校验（金额整数分 / 分类白名单 / 日期 ≤ serverDate） │  │
│  │  4) 业务处理器：auth / txn / stats / category / event     │  │
│  │  5) 统一响应包（code/message/data/serverDate/requestId） │  │
│  └───────────┬───────────────────────────┬────────────────┘  │
│              │                           │                    │
│  ┌───────────▼───────────┐   ┌───────────▼────────────────┐  │
│  │ 云数据库（Mongo 兼容）  │   │ 定时触发器（Timers）        │  │
│  │ —— P0 6 个集合 ——      │   │ · 清理过期 sessions        │  │
│  │ users        (业务)    │   │ · 清理 90 天前 events      │  │
│  │ transactions (业务)    │   │ · 物理清理软删>30天 txn     │  │
│  │ categories   (业务/只读)│   │ · 清理过期/孤儿 txn_dedup  │  │
│  │ sessions     (辅助)    │   │ · 可选：冷启动预热 ping      │  │
│  │ events       (辅助)    │   └────────────────────────────┘  │
│  │ txn_dedup    (辅助)    │                                    │
│  │ —— P1 预留 ——          │                                    │
│  │ budgets（建集合不写入） │                                    │
│  └───────────────────────┘                                    │
│  权限：所有集合设为「仅管理端（云函数）可读写」                  │
└──────────────────────────────────────────────────────────────┘
                        │
                        │ HTTPS（仅服务端调用，AppSecret 只存在云函数环境变量）
                        ▼
        https://api.weixin.qq.com/sns/jscode2session   （兼容分支，见 6.1）
        微信运维中心 / 云函数日志                        （监控）
```

### 2.2 分层与依赖方向（强制）

```
小程序端：
  pages/*（视图）
      ↓ 只能向下
  services/api.ts（唯一网络出口）+ store（内存态）
      ↓
  utils/*（纯函数：金额、日期、校验）—— 不 import 任何 wx.* API
      ↓
  constants/*（分类、错误码、文案）

服务端（云函数 api）：
  handlers/*（路由与编排）
      ↓
  domain/*（纯业务规则：金额校验、主卡状态机、月份口径、幂等判定）
      ↓                    ↑ 依赖倒置
  repositories/*（数据访问）─→ 云数据库 / 微信接口
```

**三个红线（CI 自动检查，见 8.4）**：
1. ❌ 小程序端出现 `wx.cloud.database()` / `db.collection()` → 阻断合并（必须走 `services/api.ts`）。
2. ❌ `utils/` 或 `domain/` 中出现 `wx.*` / 云数据库 SDK 调用 → 阻断合并（保证纯函数可单测、可复用到未来多端）。
3. ❌ `domain/` 中出现「直接取 `new Date()` 当作今天」→ 阻断合并（「今天」只能来自入参 `serverDate`）。

### 2.3 运行时视图：一次记账的完整调用链

```
用户点「保存」
 1. 前端：金额字符串 → utils/amount.parseToCents("12.5") = 1250（纯函数，带单测）
 2. 前端：生成 clientRequestId 并**先写入本地 storage**（防 App 被杀后重试产生重复）
 3. 前端：POST api { action:"txn.create", token, payload:{ clientRequestId, type, amount, categoryId, date, note } }
 4. 云函数：token 校验 → 取 openid → 校验 user.status=active
 5. 云函数：限流检查（60 次/分钟/用户）
 6. 云函数：校验 amount 为整数且 1..10,000,000；categoryId 属于 type；date ≤ serverDate
 7. 云函数：**幂等闸门**（唯一索引在 `txn_dedup`，窗口 24h，见 4.8 / 5.4.3）
      └─ 事务内 add txn_dedup{openid, clientRequestId, expireAt = now+24h, requestHash, txnId}
           ├─ 唯一键冲突 → 读既有键：
           │     ├─ 窗口内(expireAt > now) 且 requestHash 一致 → code 0 + idempotent:true + 首次 _id（AC-2.15）
           │     ├─ 窗口内但 requestHash 不一致 → 10101 IDEMPOTENT_CONFLICT
           │     └─ 已过窗(expireAt ≤ now) → **删除过期键后重插 → 视为新请求（PRD 8.2）**
           └─ 事务内 insert transactions（month 由 date 派生）
      └─ 唯一冲突重试 ≤2 次；仍失败 → 50001（宁可报错，不产生假成功）
 8. 云函数：返回 { code:0, data:{ _id, ... }, serverDate }
 9. 前端：清除本地 clientRequestId → **invalidate 统计与列表缓存** → 重新拉 stats.monthly
        （不做乐观更新，AC-2.14/2.18 要求失败不假成功）
10. 前端：展示提示条「已记 ¥12.50」+ 撤销 / 再记一笔（3 秒）
```

### 2.4 环境与部署

| 环境 | 用途 | 小程序版本 | 云开发环境 | 备注 |
|------|------|-----------|-----------|------|
| `dev` | 日常开发、联调、测试用例、时间旅行调试 | 开发版 / 体验版 | `xiaozhangben-dev` | 允许 `debugServerDate`、允许造数脚本 |
| `prod` | 内测、灰度、正式 | 体验版 / 正式版 | `xiaozhangben-prod` | 时间旅行开关强制关闭（CI 校验 + 环境变量缺失即视为关闭） |

- AppSecret 仅存放于**云函数环境变量**，不进代码仓库、不下发前端、不写入日志。
- 发布方式：小程序代码包提交审核 + 云函数灰度发布（云函数支持按版本/灰度，若当前控制台不支持则退化为「先发布新 action，再切前端开关」）。
- 回滚：小程序后台版本回退（对齐 PRD 13 章）+ 云函数「维护开关」（`config` 集合中的 `maintenance` 标记，为 true 时所有写接口返回 50301，前端展示维护提示）。

---

## 3. 技术栈选型

### 3.1 选型总表

| 层 | 选择 | 版本/说明 |
|----|------|-----------|
| 小程序框架 | **微信小程序原生（WXML/WXSS/TS）** | 官方 TS 模板 + `miniprogram-api-typings` |
| 前端构建 | 微信开发者工具内置编译（TS → JS）+ npm 仅用于类型 | 不引入 webpack/vite，减少构建链 |
| UI | 自研少量样式（WXSS 变量 + BEM 命名） | 不引入 Vant/TDesign：包体与依赖成本 > 收益 |
| 图标 | 单色 SVG 内联 / iconfont 子集 | 满足主包 ≤1.5MB |
| 状态管理 | 自研 `store`（约 100 行：缓存 + 订阅 + invalidate） | 不引入 Redux/MobX（P0 只有 4 个页面） |
| 日期工具 | 自研 `utils/date.ts`（约 40 行） | 不引入 moment/dayjs（体积与 UTC 陷阱） |
| 后端运行 | **微信云开发云函数，Node.js 18** | 单函数 `api` + action 路由；esbuild 打包压缩冷启动体积 |
| 数据库 | **云开发数据库**（MongoDB 兼容文档库） | **P0 6 个集合 = 3 业务（users/transactions/categories）+ 3 辅助（sessions/events/txn_dedup）**；另有 P1 预留 `budgets`（建集合不写入） |
| 接口风格 | 云函数调用 + `action` 路由（类 RPC） | 互联网协议形式为 `wx.cloud.callFunction`，非 REST |
| 鉴权 | 自定义 opaque token（`sessions` 集合） | 见 ADR-004 |
| 幂等 | `txn_dedup` 集合唯一索引 + 24h `expireAt` | 见 ADR-009 / 4.8 |
| 定时任务 | 云开发定时触发器 | 清理与预热 |
| 监控 | 微信运维中心（异常/性能）+ 云函数日志 + 自建 `events` 集合 | 见 3.4 |
| CI | 轻量脚本（lint + 类型检查 + 纯函数单测 + 红线 grep + 包体检查） | 编码阶段交付，不含运行时（本阶段不写代码） |

### 3.2 选型对比矩阵 A：后端载体（微信云开发 vs 自建 Node.js）

| 维度 | 权重 | 微信云开发（云函数+云数据库） | 自建 Node.js（Koa + 云服务器/云托管 + MySQL/PostgreSQL） |
|------|------|------------------------------|--------------------------------------------------------|
| 上手/交付速度 | 30% | **9**（无服务器、无域名、无备案、无运维） | 4（服务器、HTTPS 证书、备案、部署流水线、备份） |
| 运维成本（小团队） | 20% | **9** | 5 |
| 合规成本 | 15% | **8**（微信云托管域名已备案，无需自备案） | 5（需 ICP 备案 + 等保意识，周期数周） |
| 查询/事务能力 | 15% | 6（Mongo 兼容，聚合能力够用；云函数端有限事务支持） | **9**（SQL、JOIN、事务、报表） |
| 扩展与成本可控性 | 10% | 6（按量计费，规模大时单位成本高于自建） | **8** |
| 可迁移性/厂商锁定 | 10% | 4（锁在云开发 SDK 与调用方式） | **9** |
| **加权得分** | | **7.65** | **5.60** |

**结论：选微信云开发**。自建后端的优势（事务、报表、成本）全部是 **P1/P2 或规模期** 的需求；P0 的痛点是「小团队 6 周内上线且不运维」。**锁定风险用 ADR-001 的防腐层（单一 API 入口 + 自定义 token + repositories 层）降到「迁移 = 重写 repositories + 换部署」，而不是重写业务规则。**

> 注：v0.2 新增的 `txn_dedup` 原子性依赖云数据库事务能力（4.8）。若 W1 实测发现事务语义不足以支撑，退化路径为「补偿 + 孤儿键清理」，**不改选型结论**（该退化仅在幂等的并发边界上放宽，仍满足 AC-2.15）。

### 3.3 选型对比矩阵 B：小程序框架（原生 vs Taro vs uni-app）

| 维度 | 权重 | 原生 + TS | Taro 3（React） | uni-app（Vue） |
|------|------|-----------|-----------------|----------------|
| 单端（仅微信小程序）适配度 | 35% | **10** | 6（跨端抽象带来额外构建与调试成本） | 6 |
| 团队心智负担 / 调试体验 | 25% | **9** | 6（H5 与小程序行为差异、循环引用、社区坑） | 6 |
| 包体与启动性能 | 20% | **9**（无运行时框架层） | 6 | 6 |
| 未来多端（App/H5）能力 | 20% | 3（需要重写视图层） | **9** | **9** |
| **加权得分** | | **8.35** | 6.60 | 6.60 |

**结论：原生 + TypeScript**。PRD 11 章明确「MVP 只做微信小程序，App/H5 需 D30 留存达标后再评估」——为一个被明确排除的需求付费（构建链、包体、调试成本）不符合「不做架构宇航员」。**反转成本已对冲**：业务逻辑（金额、日期、状态机、校验）全部落在 `utils/`（纯函数、可单测、与框架无关），未来换 Taro 只重写视图层。

### 3.4 选型对比矩阵 C：数据库

| 维度 | 权重 | 云开发数据库（文档） | 云数据库 MySQL（TencentDB） | 
|------|------|---------------------|----------------------------|
| 与云函数集成 / 交付速度 | 30% | **10**（同环境、免连接池、免网络配置） | 6（需 VPC、连接池、白名单） |
| 数据结构匹配度（账单/分类/预算） | 25% | **9**（文档天然贴合，无 JOIN 需求） | 8（关系建模也很自然） |
| 索引与聚合（按月经求和、Top5） | 20% | 7（单集合聚合，够用；复杂报表弱） | **9** |
| 成本 | 15% | **8**（含在云开发套餐内） | 5（独立实例最低配也有固定费用） |
| 事务 | 10% | 5（有限支持） | **9** |
| **加权得分** | | **8.35** | **7.30** |

**结论：云开发数据库**。P0 全部查询形如「按 openid 取某月记录」与「按 openid+month 分组求和」，**没有 JOIN**，文档库完全胜任；写入热点是 `transactions`，单文档写入天然原子；唯一需要事务的地方是幂等（`txn_dedup` + `transactions` 两集合原子写，见 4.8），云函数端事务可覆盖，且有退化方案。

### 3.5 明确**不引入**的技术（每个都说明理由与重启条件）

| 不引入 | 理由 | 重新考虑的条件 |
|--------|------|----------------|
| Redis / 缓存中间件 | P0 QPS 峰值个位数，没必要 | 峰值 QPS > 200 或统计 P95 稳定 > 300ms |
| 消息队列 | 无异步工作流；埋点上报是「尽力而为」 | 引入订阅消息批量推送（P2） |
| CDN / 对象存储 | P0 无图片上传（无头像、无小票） | 拍照记账（明确不做）/ 用户头像（明确不做） |
| 预聚合汇总表 `monthly_stats` | 复杂度换性能，当前不划算（<5000 条/用户） | 见 7.4：**单用户记录 > 1 万条或统计 P95 > 300ms** |
| Docker / K8s / 微服务 | 复杂度守恒：只会把复杂度搬到基础设施 | 出现独立的第二个客户端（App）或团队 > 3 名后端 |
| 第三方埋点 SDK（如神策、GA） | 需求是要「能查到 11 个事件」，自建 `events` 集合即可 | PM 要求漏斗分析/留存看板（P2 后评估） |
| UI 组件库 | 4 个页面，自研样式体积更小、无版本风险 | 页面数 > 10 或需要复杂表单组件 |
| 「幂等中间件」（如基于 Redis 的分布式锁） | 唯一索引 + 24h 过期足以满足 AC-2.15；引锁会引入新单点 | 写 QPS 需要跨实例协调（远超 P0 量级） |

---

## 4. 数据模型设计

> **集合口径（全文档统一，v0.2 修订：审查意见 4）**：
> **P0 = 6 个集合** = 3 个业务集合（`users` / `transactions` / `categories`）+ 3 个辅助集合（`sessions` / `events` / `txn_dedup`）；
> 另 **1 个 P1 预留集合 `budgets`**：**P0 只建集合、不写入、不暴露接口**，唯一需要在 P0 做的是让 `stats.monthly` 响应预留 `progressBase` 字段（P0 恒为 `'income'`），避免 P1 改契约。

### 4.1 集合总览与关系

```
users (1) ────< (N) transactions            [openid]
users (1) ────< (N) sessions                [openid]
users (1) ────< (N) events                  [openidHash，脱敏]
users (1) ────< (N) txn_dedup               [openid，24h 过期]
users (1) ────< (N) budgets                 [openid]（P1，P0 只建集合不写入）
categories (只读，1) ────< (N) transactions  [categoryId，服务端校验合法性与 type 匹配]
```
文档库无外键，**所有跨集合一致性由云函数在事务边界内自行保证**（P0 唯一的跨集合写入是 `txn.create` 的「去重键 + 记录」，见 4.8）。

### 4.2 `users`（用户表）

| 字段 | 类型 | 必填 | 说明 / 约束 |
|------|------|------|-------------|
| `_id` | string | ✅ | 系统生成 |
| `openid` | string | ✅ | **唯一索引**。P0 用户唯一标识（见下方 unionid 说明） |
| `unionid` | string \| null | ❌ | 预留字段，P0 恒为 null（除非主体已绑定开放平台账号） |
| `createdAt` | number(ms, UTC) | ✅ | 首次登录时间 |
| `lastActiveAt` | number(ms, UTC) | ✅ | 每次成功鉴权后异步更新（**不阻塞响应**） |
| `status` | string | ✅ | `active` / `deleted`（软删除，合规用） |
| `deletedAt` | number(ms) \| null | ❌ | 用户请求删除数据时写入 |
| `schemaVersion` | int | ✅ | 当前 1，为将来字段演进预留 |
| `rateLimit` | object | ❌ | `{ windowStart: number, count: int }`（固定窗口限流，见 5.1.6） |

**索引**：`openid`（唯一）。

**unionid 取舍**：P0 只用 `openid`。理由：MVP 只有一个小程序、一个主体，「同一用户在多应用间统一」无收益；获取 unionid 需绑定微信开放平台账号（成本与流程开销），且 PRD 明确「换微信号 = 新账本」。字段保留以备将来（P1 若做公众号/App 才填充）。

### 4.3 `transactions`（账单记录表）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `_id` | string | ✅ | 系统生成 |
| `openid` | string | ✅ | **服务端写入，永不接受客户端传入**（PRD 7.3） |
| `clientRequestId` | string | ✅ | 客户端生成的 UUID v4，≤64 字符。**仅作审计与排查用途**：幂等判定由 `txn_dedup` 集合承载（见 4.8），**本集合上不建唯一索引**（v0.2 修订：审查意见 1） |
| `type` | string | ✅ | `expense` / `income` |
| `amount` | number(int) | ✅ | 单位**分**，`1 ≤ amount ≤ 10000000`（0.01–100,000.00 元）。**必须是整数**，服务端 `Number.isInteger` 校验 |
| `categoryId` | string | ✅ | 必须存在于 `categories` 且 `categories.type === type` |
| `note` | string | ❌ | ≤30 字（按 Unicode 码点计，不按 UTF-16 长度）；空则存空字符串，不存 null（简化前端判空） |
| `date` | string | ✅ | `YYYY-MM-DD`，业务日期，**≤ 服务端 serverDate**；统计归月唯一依据 |
| `month` | string | ✅ | `YYYY-MM`，**由服务端从 `date` 派生**（冗余字段，专为索引与聚合优化，见 7.4）。**`txn.create` 与 `txn.update` 都必须重算**（见 5.4.4） |
| `createdAt` | number(ms, UTC) | ✅ | 服务端时间；同日排序依据 |
| `updatedAt` | number(ms, UTC) | ✅ | 服务端时间 |
| `deleted` | boolean | ✅ | 软删除标记，默认 false |
| `deletedAt` | number(ms) \| null | ❌ | 软删除时间；定时任务 30 天后物理清理 |
| `source` | string | ✅ | 固定 `manual`；为 P2「微信支付导入」预留区分 |
| `schemaVersion` | int | ✅ | 当前 1 |

**索引（v0.2 修订：移除原 `(openid, clientRequestId)` 唯一索引，幂等职责迁到 4.8）**：

| 索引 | 类型 | 用途 |
|------|------|------|
| `(openid, month, deleted, date desc, createdAt desc)` | 复合 | 列表分页与按日分组（AC-3.1/3.3）、历史月查询 |
| `(openid, month, deleted, type)` | 复合 | 月度统计聚合：income/expense 分别求和（AC-4.1） |
| `(openid, deleted, month, categoryId)` | 复合 | Top5 分类聚合 + 30 天频次排序（AC-4.7） |

> 索引数量控制在 3 个（另 `txn_dedup` 2 个、`sessions` 3 个、`users` 1 个、`categories` 1 个）：云开发数据库每个集合的索引数量有上限（W1 核对具体值），且每个索引都带来写放大，而 `transactions` 是唯一写入热点。**若索引数量或聚合能力受限，优先砍掉第 3 个（Top5 可退化为「取本月全部记录后在云函数内聚合」，2000 条内可接受）**。

### 4.4 `categories`（分类表，P0 **只读**）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `_id` | string | ✅ | 稳定 ID，如 `exp_food` / `inc_salary`（**前端与埋点都引用它，永不复用/改名**） |
| `name` | string | ✅ | 中文名，如「餐饮」 |
| `type` | string | ✅ | `expense` / `income` |
| `icon` | string | ✅ | 图标标识（前端映射到本地资源，**不存 URL**） |
| `hint` | string | ❌ | 例：「食堂/外卖/饮料」 |
| `order` | int | ✅ | 默认顺序（PRD 6.2 的 15 个分类顺序） |
| `enabled` | boolean | ✅ | 下架分类用；P0 全 true |
| `isSystem` | boolean | ✅ | 恒 true（P0 不允许自定义分类） |
| `updatedAt` | number(ms) | ✅ | 版本比对用 |

**索引**：`(type, order)`。

**实现方式与理由（ADR-005）**：
- 服务端以 **`categories` 集合为校验白名单的唯一来源**（必须，否则无法满足 PRD 8.2「必须属于对应 type 的合法分类」）。
- **前端同时内置一份同源常量**作为兜底：当 `category.list` 接口失败时，记账面板仍可用内置常量渲染（保证弱网下能打开面板），**但保存时仍以服务端校验为准**。
- 这份同源由**编码阶段的脚本从同一份 JSON 生成**（`constants/categories.json` → 种子脚本 + 前端常量），避免两处手写漂移。
- 数据由**幂等种子脚本**写入（按 `_id` upsert），不通过小程序端写入。

### 4.5 `budgets`（预算表，**P1 预留，P0 不写数据**）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `_id` | string | ✅ | 系统生成 |
| `openid` | string | ✅ | 归属 |
| `month` | string | ✅ | `YYYY-MM` |
| `amount` | int(分) | ✅ | 月度预算 |
| `alertThreshold` | int | ✅ | 默认 80（%） |
| `createdAt` / `updatedAt` | number(ms) | ✅ | — |

**索引**：`(openid, month)` **唯一**。
**P0 处理**：集合建好、不写入、不暴露接口。唯一需要 P0 做的是**让 `stats.monthly` 的响应结构预留 `progressBase: 'income' | 'budget'` 字段**（P0 恒为 `income`），避免 P1 改接口契约。

### 4.6 `sessions`（会话表，鉴权用）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `_id` | string | ✅ | 系统生成 |
| `tokenHash` | string | ✅ | **SHA-256(token)，绝不明文存 token** |
| `openid` | string | ✅ | 归属 |
| `createdAt` | number(ms) | ✅ | 签发时间 |
| `expireAt` | number(ms) | ✅ | `createdAt + 7 天` |
| `revokedAt` | number(ms) \| null | ❌ | 主动吊销（用户删数据/注销时写入） |
| `lastUsedAt` | number(ms) | ✅ | 用于异常检测（可选） |

**索引**：`tokenHash`（唯一）、`(openid)`、`expireAt`（定时清理用）。
**清理**：定时触发器每日删除 `expireAt < now` 或 `revokedAt != null` 且超过 24h 的会话。

### 4.7 `events`（埋点与错误上报）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `_id` | string | ✅ | 系统生成 |
| `name` | string | ✅ | 事件名，白名单校验（PRD 第 10 章的 11 类 + `client_error`） |
| `params` | object | ❌ | 事件参数。**服务端按事件名过滤白名单字段**，丢弃未知字段（防意外收集敏感信息） |
| `openidHash` | string | ✅ | `sha256(openid + salt)` 前 16 位，**不存明文 openid**（PRD 9 章日志脱敏） |
| `ts` | number(ms) | ✅ | 客户端事件时间（可能与服务端有偏差，仅作参考） |
| `serverTs` | number(ms) | ✅ | 服务端接收时间（**分析统计以此为准**） |
| `serverDate` | string | ✅ | 服务端自然日，便于按天聚合 |
| `appVersion` | string | ✅ | 客户端版本，便于灰度对比 |
| `env` | string | ✅ | `dev` / `prod` |

**索引**：`(name, serverDate)`、`(serverDate)`。
**清理**：保留 90 天，定时触发器清理（避免无界增长）。
**写入策略**：前端**批量上报**（每 10 条或退出/切后台时 flush，单次最多 50 条）；上报表失败**不重试、不阻塞 UI**（埋点丢一点没关系，绝不影响记账）。

### 4.8 `txn_dedup`（幂等去重键表）`[v0.2 新增，审查意见 1]`

> **为什么单独建一个集合而不用 `transactions` 上的唯一索引**：PRD 8.2 要求「窗口（24h）内重复只落库一次；**超出窗口的同值视为新请求**」。`transactions` 上的 `(openid, clientRequestId)` 唯一索引一旦建立就是**永久**约束，只要记录还在（≥30 天）就无法放行同值新建，与 PRD 语义冲突。把去重键独立出来并带 `expireAt`，可以让「**判窗**」与「**清理**」解耦：**判窗只看 `expireAt`，因此即使清理任务延迟甚至失败，语义仍然正确**。见 ADR-009。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `_id` | string | ✅ | 系统生成 |
| `openid` | string | ✅ | 归属（**必须来自会话，不接受客户端传入**） |
| `clientRequestId` | string | ✅ | 客户端生成的 UUID，≤64 字符 |
| `txnId` | string | ✅ | 首次创建的 `transactions._id`（重复提交时据此返回首次结果） |
| `requestHash` | string | ✅ | `sha256(openid|clientRequestId|type|amount|categoryId|date|note)`，用于判定「内容是否一致」 |
| `createdAt` | number(ms, UTC) | ✅ | 首次创建时间 |
| `expireAt` | number(ms, UTC) | ✅ | `createdAt + 24h`（**去重窗口，PRD 8.2**） |
| `schemaVersion` | int | ✅ | 当前 1 |

**索引**：
| 索引 | 类型 | 用途 |
|------|------|------|
| `(openid, clientRequestId)` | **唯一** | 并发重复提交的**唯一正确性闸门**（AC-2.15） |
| `expireAt` | 单列 | 清理任务扫描（若平台支持 TTL 索引，优先用 TTL 自动过期） |

**存储位置与清理策略（对 PRD 8.2「工程需说明存储位置与清理策略」的正式答复）**：

| 项 | 结论 |
|----|------|
| 存储位置 | 独立集合 `txn_dedup`，键 = `(openid, clientRequestId)`；**不再使用 `transactions` 上的唯一索引** |
| 窗口判定 | **读键的 `expireAt`**：`expireAt > now` → 窗口内（重复）；`expireAt ≤ now` → 已过窗（视为新请求） |
| 过窗行为 | **放行新建**：在与首次创建同一事务内删除过期键并重插（`expireAt = now + 24h`），随后插入新记录。**这就是 PRD 8.2「超出窗口的同值视为新请求」的实现**。 |
| 清理策略 | 定时触发器每日清理 `expireAt < now` 的键（若平台支持 TTL 索引则用 TTL）。**清理是空间回收手段，不是语义依据**——清理延迟不影响判窗正确性 |
| 孤儿键（有键无记录） | 仅在「事务不可用的退化路径」下可能出现：`txn.create` 先插键、记录插入失败后删键补偿失败。**两级兜底**：① 命中窗口内的键但 `txnId` 对应记录查不到 → 视为孤儿键，删除并放行新建；② 定时任务清理「`createdAt` 早于 5 分钟且 `txnId` 无对应记录」的键 |
| 命中但记录已软删除 | 用户撤销（软删）后若有旧请求重放：返回 `30002 NOT_FOUND`，**不复活记录**，前端按「记录已被删除」处理（防御性分支，正常不会发生） |

### 4.9 金额与时间字段约定（全局，硬性）

| 项 | 约定 | 理由 |
|----|------|------|
| 金额存储 | `number`，整数，单位分 | 见 7.1 |
| 金额传输 | JSON number（分），**不传「元」字符串给接口** | 避免两端格式化差异；格式化只在前端展示层做 |
| 业务日期 | string `'YYYY-MM-DD'`（Asia/Shanghai 自然日） | 字符串比较即时间比较，天然免疫时区；直接对应 PRD 8.2 |
| 归属月份 | string `'YYYY-MM'`，冗余存，**写入与更新时都由服务端从 `date` 派生** | 让统计走等值索引而非范围扫描（7.4） |
| 时间戳 | number，UTC 毫秒 | `Date.now()` 本身是 UTC，不受设备时区影响；避免 `Date` 对象跨端序列化差异 |
| 「今天」 | 服务端 `serverDate` | PRD 6.4.3 / AC-1.8 |

### 4.10 数据生命周期

| 数据 | 生命周期 | 机制 |
|------|----------|------|
| 正常记录 | 永久（随用户数据） | — |
| 软删除记录 | 软删后 30 天 | 定时触发器物理删除 |
| `txn_dedup` 键 | **24h（`expireAt`）** | 定时触发器（或 TTL）；**过期即语义失效，清理仅为回收空间** |
| sessions | 7 天或吊销后 24h | 定时触发器 |
| events | 90 天 | 定时触发器 |
| 用户注销/清空 | **立即物理删除**（用户主动请求） | `user.clearData` / `user.delete` 接口（见 5.4.2、7.3、10.4） |

---

## 5. 接口设计

### 5.1 通用约定

#### 5.1.1 调用形式

```js
wx.cloud.callFunction({
  name: 'api',
  data: { action: 'txn.create', token: '<token>', payload: { /* ... */ } }
})
```

**为什么是「单云函数 + action」而不是「一个接口一个云函数」**：见 ADR-007。**为什么 token 放在 `data` 里而不是 header**：云开发调用云函数没有 HTTP header 语义；若将来切到 HTTP 网关，契约中的 `token` 字段直接映射为 `Authorization: Bearer <token>`，前端只改 `services/api.ts` 一处。

#### 5.1.2 统一响应包

```json
{
  "code": 0,
  "message": "ok",
  "data": { },
  "serverDate": "2025-09-22",
  "serverTime": 1758499200000,
  "requestId": "req_01H3Z9M2QK7F"
}
```

**`serverDate` 出现在每一个响应里**（不只是登录接口）——这是 AC-1.8 的实现方式：客户端任何一次成功请求都会用响应中的 `serverDate` 校正本地「今天」基准，因此用户把设备时间改错后，只要有一次成功请求即被纠正；若完全无网络，则所有记账入口本就不可用（AC-1.4）。

#### 5.1.3 契约字段命名

- 请求与响应字段名**与数据库字段名一致**（如 `date`、`clientRequestId`、`categoryId`），**不做重命名映射**。理由：PRD 已用这些名字定义业务语义，多一层命名映射 = 多一处不一致 bug 来源（映射只有在有第二个客户端时才值得）。

#### 5.1.4 鉴权

- 除 `auth.login` 与 `health.ping` 外，所有 action **必须有有效 token**。
- 服务端从 `sessions` 反查 `openid`，**绝不接受客户端传入的 `openid`**（PRD 7.3）。若 payload 中出现 `openid` 字段 → 直接忽略。
- `users.status !== 'active'` → 返回 `20005`。

#### 5.1.5 幂等（v0.2 修订：审查意见 1）

| 操作 | 幂等键 | 重复提交行为 |
|------|--------|--------------|
| `txn.create` | `clientRequestId`（**唯一索引落在 `txn_dedup`，窗口 24h**） | 窗口内命中且 `requestHash` 一致 → `code 0` + `data.idempotent = true` + 返回首次结果；内容不一致 → `10101`；**已过窗 → 视为新请求，正常新建**（PRD 8.2）；命中但记录已软删 → `30002` |
| `txn.update` | `_id`（天然幂等） | 重复提交相同字段 → 结果一致，`updatedAt` 可能被刷新 |
| `txn.delete` | `_id`（天然幂等） | 已删除再删 → `code 0` + `data.alreadyDeleted = true`（不给用户报错） |
| `auth.login` | 无（无副作用，可无限重放） | — |

#### 5.1.6 限流

| 维度 | 阈值 | 超限返回 |
|------|------|----------|
| 写接口（`txn.*`） | 每 openid 每分钟 60 次 | `42901` |
| 读接口（`stats.*` / `txn.list`） | 每 openid 每分钟 120 次 | `42901` |
| `auth.login` | 每 openid 每分钟 10 次 | `42901` |
| `event.report` | 每 openid 每分钟 20 次（批量） | `42901`（静默丢弃，前端不提示） |

实现：**固定窗口计数存 `users.rateLimit`（原子自增）**，不引入 Redis（3.5）。超限时前端表现：读接口展示错误态 + 重试；写接口提示「操作太频繁，请稍后再试」，**且不产生假成功**。

#### 5.1.7 分页

- 游标分页（**不用 `skip`**）：`cursor = base64({ date, createdAt, _id })`（业务日期 + 创建时间 + id，保证全序不跳项）。
- 默认 `limit = 20`（对齐 PRD 每页 20 条），最大 50。
- 响应含 `nextCursor`（无更多时为空串）与 `hasMore`。

#### 5.1.8 错误处理原则

- 云函数**永不向客户端抛裸异常**：所有异常在入口统一捕获，映射为错误码 + `requestId`，并写结构化日志。
- **`message` 字段面向用户**（可直接展示的简短中文），**技术细节只进日志**（含 `requestId`，便于测试与客服凭 requestId 定位）。

### 5.2 错误码表（唯一来源，前后端共用）

> **数量口径（v0.2 修订：审查意见 6）**：本表共 **24 条记录 = 1 条成功码（`0`）+ 23 条错误码**。
> ⚠️ **更正声明**：v0.1 的交付汇报中曾称「28 条错误码」，与上表实际条数不符，属汇报口径错误，**以本表 23 个错误码（+成功码 `0`，共 24 条记录）为准**。
> **测试可直接引用 code 做断言**；错误码一旦发布只增不改（改 = 破坏兼容）。

| # | code | 常量名 | 触发场景 | HTTP 映射（迁移时） | 前端表现 |
|---|------|--------|----------|---------------------|----------|
| 1 | `0` | `OK` | 成功（含幂等命中） | 200 | — |
| 2 | `10001` | `INVALID_PARAM` | 通用参数非法 | 400 | 「参数有误」，不展示给用户，上报 |
| 3 | `10002` | `MISSING_PARAM` | 缺少必填参数 | 400 | 同上 |
| 4 | `10003` | `AMOUNT_INVALID` | 金额非整数分 / 超过两位小数（AC-2.4） | 400 | 「金额最多两位小数」 |
| 5 | `10004` | `AMOUNT_OUT_OF_RANGE` | `amount < 1` 或 `> 10000000`（AC-2.3/2.5） | 400 | 「单笔金额不能超过 100000 元」 |
| 6 | `10005` | `NOTE_TOO_LONG` | 备注 > 30 字（AC-2.9） | 400 | 前端已限制，实际为防御 |
| 7 | `10006` | `DATE_INVALID` | 日期格式非法 / 晚于 `serverDate`（AC-2.8）、或早于滚动 12 个月窗口 | 400 | 「不能记录未来日期」 |
| 8 | `10007` | `CATEGORY_INVALID` | 分类不存在 / 与 `type` 不匹配（AC-2.6/2.12） | 400 | 「请选择分类」 |
| 9 | `10008` | `TYPE_INVALID` | `type` 非 `expense/income` | 400 | 上报 |
| 10 | `10009` | `MONTH_INVALID` | `month` 非法或为未来月份（AC-3.9） | 400 | 「不能查看未来月份」 |
| 11 | `10101` | `IDEMPOTENT_CONFLICT` | **窗口（24h）内**同 `clientRequestId` 但内容不同 | 409 | 提示重试并重新生成 id，上报 |
| 12 | `20001` | `UNAUTHENTICATED` | 无 token | 401 | 触发静默重登（见 6.3） |
| 13 | `20002` | `TOKEN_EXPIRED` | token 不存在 / 过期 / 已吊销 | 401 | 触发静默重登（见 6.3） |
| 14 | `20003` | `WX_CODE_INVALID` | `code2session` 失败、code 无效/已用过 | 401 | 登录失败态 + 禁用记账入口（AC-1.4） |
| 15 | `20004` | `IDENTITY_MISMATCH` | 上下文 openid 与 code 换取结果不一致 | 401 | 拒绝请求 + 安全告警（防重放） |
| 16 | `20005` | `USER_DISABLED` | `users.status = deleted` | 403 | 「账本已删除」页 |
| 17 | `30001` | `NOT_OWNER` | 操作不属于当前 openid 的记录（越权） | 403 | 「无权限」，上报 + 安全告警 |
| 18 | `30002` | `NOT_FOUND` | 记录不存在 / 已删除（含幂等命中但记录已软删） | 404 | 「记录已被删除」并刷新列表 |
| 19 | `42901` | `RATE_LIMITED` | 触发限流 | 429 | 「操作太频繁，请稍后再试」 |
| 20 | `50000` | `INTERNAL_ERROR` | 未捕获异常 | 500 | 「服务开小差了」+ 重试 |
| 21 | `50001` | `DB_ERROR` | 数据库异常（**含幂等事务重试 2 次后仍冲突**） | 500 | 同上 |
| 22 | `50002` | `UPSTREAM_TIMEOUT` | 微信接口超时 | 504 | 同上 |
| 23 | `50301` | `MAINTENANCE` | 维护开关开启 | 503 | 维护页（回滚方案用） |
| 24 | `50003` | `CLIENT_TOO_OLD` | 客户端版本低于服务端最低要求 | 426 | 强制升级提示 |

### 5.3 接口清单

| # | action | 方法语义 | 鉴权 | 幂等 | 说明 | 对应 AC |
|---|--------|----------|------|------|------|---------|
| 1 | `auth.login` | 登录初始化 | ❌ | 天然 | `wx.login` code → openid → 建用户 → 签发 token → 返回 `serverDate` | AC-1.1~1.8 |
| 2 | `user.clearData` | 清空账本数据 | ✅ | 是 | 二次确认后软删全部记录 + 吊销会话（OQ-2 建议 P0 做） | 合规 |
| 3 | `user.delete` | 注销账号 | ✅ | 是 | 物理删除全部数据 + 吊销会话 | 合规 |
| 4 | `txn.create` | 新建记录 | ✅ | `clientRequestId`（`txn_dedup`） | — | AC-2.1~2.18 |
| 5 | `txn.update` | 编辑记录 | ✅ | `_id` | 只允许改 `amount/categoryId/note/date/type`；**`date` 变更时必须重算 `month`**（5.4.4） | AC-3.7/3.14 |
| 6 | `txn.delete` | 删除记录（撤销同此接口） | ✅ | `_id` | 软删除 | AC-2.10/2.18、AC-3.6/3.15 |
| 7 | `txn.detail` | 记录详情 | ✅ | — | 详情页与编辑前刷新 | AC-3.5 |
| 8 | `txn.list` | 按月分页列表 + 月度合计 | ✅ | — | 首屏返回该月合计 | AC-3.1~3.13 |
| 9 | `stats.monthly` | 首页统计（主卡 + 三列 + Top5 + 最近 3 笔） | ✅ | — | **含主卡状态机判定；历史月仅主卡切口径** | AC-4.1~4.14 |
| 10 | `category.list` | 分类列表（含个性化排序） | ✅ | — | 30 天频次排序（待 PM 决策，见 0.2 #2） | AC-2.12 |
| 11 | `event.report` | 埋点/错误批量上报 | ✅ | 天然 | 尽力而为，失败不重试 | 第 10 章 |
| 12 | `health.ping` | 预热与探活 | ❌ | 天然 | 定时触发器调用，抗冷启动 | 7.5 |

**P0 明确不提供的接口**：预算相关（P1）、导出（P1）、图表（P2）、支付导入（P2）、社交（不做）。

### 5.4 接口详述

#### 5.4.1 `auth.login`

**请求**
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | string | ✅* | `wx.login` 返回的 code（5 分钟有效、一次性）。*走云开发 `callFunction` 时服务端已能拿到 OPENID 上下文，此时 `code` 可作为二次校验；**若未来走 HTTP 网关则必填**（见 6.1） |
| `appVersion` | string | ✅ | 客户端版本 |
| `scene` | string | ❌ | 进入场景值（埋点用） |
| `isFirstLaunch` | boolean | ❌ | 是否首次启动（埋点用） |

**响应 `data`**
```json
{
  "token": "a3f1...c92d",
  "expireAt": 1759104000000,
  "isNewUser": false,
  "user": { "nickname": "同学", "avatar": "default" },
  "serverDate": "2025-09-22",
  "categoryVersion": 3
}
```
| 字段 | 类型 | 说明 |
|------|------|------|
| `token` | string | 64 位十六进制随机串（客户端只存这个，**不存 openid**） |
| `expireAt` | number | 过期时间（7 天后） |
| `isNewUser` | boolean | 首次创建 → 前端展示空态引导（AC-1.2） |
| `user` | object | 仅占位称呼与默认头像标识，**无任何个人信息**（AC-1.3） |
| `serverDate` | string | 「今天」的唯一来源（AC-1.8） |
| `categoryVersion` | int | 分类版本号；与本地缓存不一致时才拉 `category.list`（省一次请求） |

**可能的错误**：`20003`、`20004`、`42901`、`50000`、`50002`。
**副作用**：`users` 不存在则创建（`isNewUser: true`）；写 `sessions`；异步更新 `lastActiveAt`。

#### 5.4.2 `user.clearData` / `user.delete`

**请求**：`{ confirmText: "清空" }`（服务端二次校验字符串，防止误调）。
**响应**：`{ clearedCount: 37 }`。
**语义**：`clearData` = 软删除该 openid 全部 `transactions` + 吊销全部会话（**立即生效，用户下次进入即新账本**）；`delete` = 物理删除全部数据（含 `txn_dedup` 键）+ 吊销会话 + `users.status = deleted`。
**理由**：PRD 11 章要求提供数据删除途径，OQ-2 待 PM/CEO 决策；工程建议 P0 至少实现 `clearData`。

#### 5.4.3 `txn.create`

**请求 `payload`**
| 字段 | 类型 | 必填 | 校验 |
|------|------|------|------|
| `clientRequestId` | string | ✅ | 非空 ≤64 字符；**服务端不生成，必须由客户端生成** |
| `type` | string | ✅ | `expense` / `income` |
| `amount` | number | ✅ | 整数，`1..10000000`（分） |
| `categoryId` | string | ✅ | 存在于 `categories` 且类型匹配 |
| `date` | string | ✅ | `YYYY-MM-DD`，`≤ serverDate`，且不早于滚动 12 个月窗口 |
| `note` | string | ❌ | ≤30 字符（按码点） |

**响应 `data`**
```json
{
  "id": "65f0a1...",
  "idempotent": false,
  "createdAt": 1758499200000
}
```

**服务端写入的字段**：`openid`、`month`（从 `date` 派生）、`createdAt`、`updatedAt`、`deleted:false`、`source:"manual"`、`schemaVersion:1`。

**幂等执行流程（实现细节，测试按此设计用例）**
```
1) 计算 requestHash = sha256(openid|clientRequestId|type|amount|categoryId|date|note)
2) 在事务内：
   a. add txn_dedup { openid, clientRequestId, requestHash, txnId:null, createdAt, expireAt: now+24h }
      ├─ 成功 → 继续 b
      └─ 唯一冲突 → 读既有键（见下表分支），按分支返回或替换过期键后重试
   b. add transactions {...}
   c. update txn_dedup.txnId = 新记录 _id
3) 提交；唯一冲突导致的事务失败最多重试 2 次（并发同 id 的第二个请求通常在第 2 次重试时看到对方的键）
```

| 分支 | 条件 | 返回 |
|------|------|------|
| 窗口内重复 | `expireAt > now` 且 `requestHash` 一致，且 `txnId` 记录存在未删 | `code 0`，`data = { id: 首次 _id, idempotent: true, createdAt: 首次时间 }`（**AC-2.15**） |
| 窗口内冲突 | `expireAt > now` 且 `requestHash` 不一致 | `10101` |
| 已过窗 | `expireAt ≤ now` | 事务内删除过期键 → 重插新键 → **正常新建一条记录**（PRD 8.2） |
| 孤儿键 | `expireAt > now` 但 `txnId` 无对应记录 | 删除孤儿键 → 正常新建（退化路径兜底，见 4.8） |
| 命中但记录已软删 | `txnId` 记录 `deleted = true` | `30002`（不复活记录） |

**对应 AC**：AC-2.2/2.4/2.5/2.6/2.7/2.8/2.9/2.14/2.15/2.16。

#### 5.4.4 `txn.update` `[v0.2 修订：审查意见 2]`

**请求**：`{ id, amount?, categoryId?, note?, date?, type? }`（至少一个字段）。

**关键规则（必须全部实现，缺一即产生「列表按新日期、统计仍旧月」的数据不一致）**

1. **`date` 变化 → 必须重算冗余 `month`**：服务端在**同一次 update 语句内**同时写入新的 `date` 与 `deriveMonth(date)`，**禁止分两次写**（避免中间态）。
2. **`month` 无条件每次重算**（即使 `date` 未变）：若历史数据出现 `month` 与 `date` 不一致（如旧数据迁移、手工订正），编辑一次即自愈。**这条与第 1 条并不冲突：第 1 条是必须性，第 2 条是自愈策略。**
3. **`date` 校验复用创建期同一函数**：格式合法 → `≤ serverDate`（否则 `10006`）→ 不早于**滚动 12 个月窗口**（否则 `10006`）。窗口定义：`date >= firstDayOf(serverDate 所在月 − 11 个月)`，即 `[当前月−11, 当前月]` 共 12 个自然月（与记账面板日期选择器范围一致，PRD 6.2）。
4. **`type` 变更时必须同时校验 `categoryId` 属于新 type**，否则返回 `10007`（避免出现「收入 + 餐饮」的脏数据）。
5. `amount` / `note` / `categoryId` 的校验与创建期完全一致（复用 `domain/validate`）。
6. 越权与不存在：`where({ _id, openid })`；受影响 0 行时区分 `30001`/`30002`（同 5.4.5）。

**响应**：`{ id, updatedAt, month, idempotent: true }`（同 id 重复提交结果一致；**响应带回重算后的 `month`，便于测试与前端断言**）。

**对应 AC**：AC-3.7、AC-3.14；跨月编辑的一致性见 10.2 专项用例。

#### 5.4.5 `txn.delete`

**请求**：`{ id }`。
**响应**：`{ id, alreadyDeleted: false }`。
**实现**：`where({ _id: id, openid })` → 更新 `deleted:true, deletedAt`。**受影响行数 0 且记录存在但非本人 → `30001`；记录不存在 → `30002`**（越权与不存在必须区分，便于安全审计）。
**对应 AC**：AC-2.10、AC-2.18、AC-3.6、AC-3.15。

#### 5.4.6 `txn.detail`

**请求**：`{ id }`；**响应**：单条完整字段。
**用途**：详情页打开时刷新（避免列表缓存数据过期）；编辑前取最新值。

#### 5.4.7 `stats.monthly`（最关键的接口）

**请求** `payload`
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `month` | string | ❌ | `YYYY-MM`；缺省 = 服务端当前月（`serverDate` 所在月） |

**响应 `data`（示例：2025-09，收入 1500 支出 1200，今天 9 月 22 日）**
```json
{
  "month": "2025-09",
  "monthState": "current",
  "cardState": "normal",
  "daysInMonth": 30,
  "serverDate": "2025-09-22",
  "card": {
    "title": "本月还剩",
    "mainAmount": 30000,
    "isOverspent": false,
    "remainingDays": 9,
    "dailyAvailable": 3333,
    "progressPercent": 80,
    "progressBase": "income",
    "showProgress": true,
    "noIncomeGuide": false
  },
  "totals": { "income": 150000, "expense": 120000, "balance": 30000 },
  "topCategories": [
    { "categoryId": "exp_food", "name": "餐饮", "icon": "food", "amount": 60000, "percent": 50 }
  ],
  "recentTransactions": [ { "id": "...", "type": "expense", "amount": 1200, "categoryId": "exp_food", "note": "", "date": "2025-09-22" } ]
}
```

**响应 `data`（示例：历史月 2025-08，收入 1600 支出 1450）`[v0.2 修订：审查意见 3 —— 修正为空数组的错误示例]`**
```json
{
  "month": "2025-08",
  "monthState": "history",
  "cardState": "history",
  "daysInMonth": 31,
  "serverDate": "2025-09-22",
  "card": {
    "title": "8月结余",
    "mainAmount": 15000,
    "isOverspent": false,
    "remainingDays": null,
    "dailyAvailable": null,
    "progressPercent": null,
    "progressBase": null,
    "showProgress": false,
    "noIncomeGuide": false
  },
  "totals": { "income": 160000, "expense": 145000, "balance": 15000 },
  "topCategories": [
    { "categoryId": "exp_food",      "name": "餐饮",     "icon": "food",      "amount": 90000, "percent": 62 },
    { "categoryId": "exp_transport", "name": "交通",     "icon": "transport", "amount": 30000, "percent": 21 },
    { "categoryId": "exp_market",    "name": "超市零食", "icon": "market",    "amount": 25000, "percent": 17 }
  ],
  "recentTransactions": [
    { "id": "...", "type": "expense", "amount": 2500, "categoryId": "exp_market",    "note": "宿舍囤货",   "date": "2025-08-30" },
    { "id": "...", "type": "expense", "amount": 1200, "categoryId": "exp_transport", "note": "",           "date": "2025-08-28" },
    { "id": "...", "type": "expense", "amount": 1500, "categoryId": "exp_food",      "note": "和同学聚餐", "date": "2025-08-25" }
  ]
}
```
> 该月确实有 1450 元支出，因此 **`topCategories` 与 `recentTransactions` 必须有值**；`percent` 之和为 100（62+21+17）；`recentTransactions` = 该月最后 3 笔（`date` 倒序、同日 `createdAt` 倒序）。
> **`topCategories` / `recentTransactions` 为空数组的唯一合法情形是「所选月份确实无支出 / 无记录」**（如历史月无记录的 H0 态，design.md 5.2 / PRD AC-4.6）。

**响应 `data`（示例：状态 B 无收入基线，收入 0 支出 120）**
```json
{
  "month": "2025-09", "monthState": "current", "cardState": "no_income",
  "daysInMonth": 30, "serverDate": "2025-09-22",
  "card": { "title": "本月还剩", "mainAmount": -12000, "isOverspent": true,
            "remainingDays": null, "dailyAvailable": null,
            "progressPercent": null, "progressBase": null, "showProgress": false,
            "noIncomeGuide": true },
  "totals": { "income": 0, "expense": 12000, "balance": -12000 },
  "topCategories": [ /* 本月支出分类按金额降序，非空 */ ], "recentTransactions": [ /* 本月最近 3 笔，非空 */ ]
}
```

**设计要点（重要，直接决定 AC-4.13/4.14 可测性）**
1. **`cardState` 由服务端判定**（`empty` / `no_income` / `normal` / `history`），**前端只按枚举渲染分支，不自行推导状态**——避免两端各写一套状态机导致口径漂移。
2. **历史月只切「主卡」口径，其余区块仍按所选月份返回真实数据**（v0.2 明确，审查意见 3）：`totals` / `topCategories` / `recentTransactions` 对当前月与历史月**走同一条计算路径**（同一 `month` 等值过滤），**历史月唯一的变化是 `card` 的字段与 `cardState='history'`**（`remainingDays` / `dailyAvailable` / `progressPercent` / `progressBase` 为 `null`，`showProgress=false`）。**不得因历史月而返回空数组**；空数组只表示「该月确实没有数据」。
3. **不下发拼接好的展示文案，只下发结构化数值**（`mainAmount` / `dailyAvailable` / `remainingDays` / `progressPercent`）与**短标题关键词**（`card.title`，如「本月还剩」/「8月结余」）；金额格式化（`¥1,234.00`）与「平均每天可用 ¥33.33（剩 9 天）」这类句式在前端 `utils/format` 统一完成。**两处最终文案以 `docs/design.md` 3.5 / 7.3 / 5.2 为唯一来源**，服务端只提供标题关键词，避免文案改动变成服务端发版。
4. **不适用字段一律返回 `null`**（而非 0 或省略），前端据此隐藏元素——**这是 AC-4.13「不得出现剩 N 天」最可测的实现方式**：测试只需断言 `card.dailyAvailable === null && card.showProgress === false`（历史月/无收入态）。
5. `progressPercent` 已封顶 100（AC-4.5），`isOverspent` 表达「>100%」或「结余为负」，避免前端二次判断。
6. `daysInMonth` 由服务端按 `Asia/Shanghai` 计算下发，避免前端手写「大小月/闰年」逻辑出错（AC-4.13 需要「共 31 天」）。
7. `noIncomeGuide` 显式下发（而非让前端按 `mainAmount < 0` 猜），保证 AC-4.14 的引导副信息与点击预选「收入/生活费」的跳转条件只有一个判定来源。

**对应 AC**：AC-4.1~4.14、AC-1.8。

#### 5.4.8 `txn.list`

**请求**：`{ month?: 'YYYY-MM', cursor?: string, limit?: 20 }`
**响应**
```json
{
  "month": "2025-09",
  "summary": { "expense": 120000, "income": 150000, "count": 23 },
  "groups": [
    { "date": "2025-09-12", "weekday": "周五",
      "expenseSum": 4500,
      "items": [ { "id": "...", "type": "expense", "amount": 1200, "categoryId": "exp_food", "categoryName": "餐饮", "note": "", "createdAt": 1758... } ] }
  ],
  "nextCursor": "eyJkIjoiMjAyNS0wOS0xMiIsImMiOjE3NTg...",
  "hasMore": true
}
```
**关键规则**
- `summary` 只在 `cursor` 为空时（第一页）返回，**与首页 `stats.monthly` 的同月数值必须绝对一致**（PRD 9 章数据一致性要求，测试需做交叉断言）。
- 服务端**按日期分组**返回（组头合计 `expenseSum` 只统计支出，AC-3.2）。
- 分页边界**不得在同一日中间断开后重复/跳项**：游标含 `_id` 保证全序稳定（AC-3.3/3.10）。
- 请求 `month` 为未来月 → `10009`（AC-3.9）。
- 日期与星期由服务端算（`weekday`），前端不重复实现（避免农历/时区坑）。
- 历史月与当前月走同一路径（不做特殊分支）。

#### 5.4.9 `category.list`

**请求**：`{ version?: 3 }`（与本地缓存一致时返回 `notModified: true`，省流量）
**响应**：`{ version: 3, notModified: false, items: [ { id, name, type, icon, hint, order } ] }`
**排序**：默认 `order`；启用个性化时，服务端额外聚合「该用户最近 30 天各分类使用次数」，把使用过的分类按次数降序前置，未使用的按 `order` 在后（**前 10 个仍保证不滚动可见**，PRD 6.2）。
**注意**：该个性化排序待 PM 决策（0.2 #2；design.md 8.5 建议固定顺序）。

#### 5.4.10 `event.report`

**请求**：`{ events: [ { name, ts, params } ] }`（≤50 条）
**响应**：`{ accepted: 12, rejected: 0 }`
**规则**：`name` 白名单 + `params` 字段白名单，非法事件**静默丢弃并计数**（不报错，避免影响前端）；失败不重试。

---

## 6. 登录鉴权流程

### 6.1 时序图

```
小程序                                    云函数 api                       微信服务器
  │                                          │                                │
  ├─1. wx.login() ──────────────────────────►│                                │
  │     返回 code（静默，无授权弹窗，5 min）    │                                │
  │                                          │                                │
  ├─2. callFunction(action=auth.login, {code})►                                │
  │                                          ├─3a. 云开发上下文取 OPENID ◄─────┤（callFunction 自动携带）
  │                                          │      cloud.getWXContext().OPENID │
  │                                          ├─3b. 【兼容分支】若上下文中无 OPENID（HTTP 触发）
  │                                          │      GET jscode2session(appid,secret,code) ──►│
  │                                          │      ◄── openid / session_key ──────────────┤
  │                                          ├─3c. 若 3a 与 3b 的 openid 同时存在且不一致
  │                                          │      → 返回 20004 并安全告警（防重放）
  │                                          ├─4. users 按 openid 查/建（唯一索引）
  │                                          ├─5. 生成 64 位随机 token
  │                                          │      sessions 写入 sha256(token)/openid/expireAt
  │                                          ├─6. 返回 { token, expireAt, isNewUser, user, serverDate }
  │◄─────────────────────────────────────────┤                                │
  ├─7. token 存 wx.storage；serverDate 更新本地「今天」基准
  └─8. 后续所有请求 data.token = token
```

**OQ-1 结论（对 PRD 的正式回复）**：**静默登录可行**。`wx.login` 不在微信「用户隐私接口」清单内，调用它**不会弹出授权弹窗**，因此 AC-1.1（全程无点击）与 AC-1.2（不弹昵称/头像弹窗）可实现。**但需满足一个前置条件**：小程序需在 MP 后台配置《用户隐私保护指引》（声明收集 openid 用于账号识别与数据存储），否则一旦引入其他隐私接口会触发弹窗。**W1 需实际在真机验证一次，若微信政策有变，退化方案 = 一次点击的「开始使用」引导（AC-1.1 需同步修订）**（design.md 4.2 已备好该页设计）。

### 6.2 token 设计（ADR-004）

| 项 | 设计 |
|----|------|
| 形态 | **opaque 随机 token**（crypto random 32 字节 → 64 位 hex），**不是 JWT** |
| 存储 | 服务端 `sessions.tokenHash = sha256(token)`；客户端只存 token 本身（`wx.setStorageSync('token')`） |
| 有效期 | 7 天（`expireAt`） |
| 传输 | `callFunction` 的 `data.token`（迁移后为 `Authorization: Bearer`） |
| 吊销 | `revokedAt` 置位（注销、清空数据、疑似泄露） |
| 校验 | 查 `sessions` → 校验 `expireAt` / `revokedAt` → 取 `openid` → 校验 `users.status = active` |

**为什么不用 JWT**：JWT 的卖点是「无状态免查库」，但我们**每次请求都必须查一次 `users.status`**（要支持用户注销/清空数据立即失效），省不掉这次查询；而 JWT 要把 openid 明文放进客户端可见的 payload，且**无法主动吊销**——两点都直接与合规要求冲突。opaque token + 索引查询的延迟增量 <30ms，可接受。

### 6.3 失败与自动续期（AC-1.4 / AC-1.7）

```
请求返回 20001 / 20002（token 缺失/过期）
  → 前端置全局 loginState = 'refreshing'，串行化（并发请求只触发一次重登）
  → 重跑 wx.login → auth.login
      ├─ 成功 → 用新 token 重放**原请求**（仅当原请求是幂等接口：所有 P0 接口都幂等，故安全）
      └─ 失败 → loginState = 'failed'
            → 首页展示错误态 + 「重新加载」（design.md 4.3 稿）
            → **禁用全部记账入口**：首页/账单页悬浮「＋」、两个空态的「记第一笔」
              （AC-1.4 明确列举的 4 个入口，隐藏或置灰且不可命中）
```

- **同一微信会话内再次进入**（AC-1.7）：本地已有 token → 直接渲染缓存的 `stats.monthly`（标记 `stale`）+ 后台刷新，不阻塞式 loading。
- **token 校验失败的请求绝不降级为「无 token 也能写」**（禁止产生无法归属 openid 的数据）。

### 6.4 服务端时间基准（AC-1.8 / PRD 8.4）

| 机制 | 实现 |
|------|------|
| 下发 | 每个响应包都带 `serverDate` / `serverTime` |
| 客户端缓存 | 内存 + storage 存 `{ serverDate, localTsAtSync }`；本会话复用 |
| 重新拉取触发 | ① 当前请求响应带回的 `serverDate` 与缓存不同；② 本地已跨自然日；③ 本地时间与服务端时间偏差 > 5 分钟（用 `serverTime - localTsAtSync` 与 `Date.now()` 比对） |
| 服务端旁路校验 | `txn.create/update` 校验 `date ≤ serverDate`（PRD 8.4 要求），并按 `date` 派生/重算 `month` |
| 测试可测性 | dev 环境支持 `debugServerDate` 注入（见 10.3） |

### 6.5 越权与数据隔离（PRD 7.3）

| 措施 | 说明 |
|------|------|
| 数据库权限 | **所有集合（含 `txn_dedup`）设为「仅管理端（云函数）可读写」**，小程序端无权直连数据库 |
| openid 来源 | 只来自 `sessions`；payload 中的 `openid` 一律忽略（**含 `txn_dedup` 的键，绝不接受客户端传入**） |
| 写操作条件 | 所有 `update/delete` 的 `where` 必须同时含 `_id` 与 `openid`；受影响行数为 0 时区分 `30001`/`30002` |
| 读操作条件 | 所有查询强制带 `openid` 等值条件（索引第 1 列就是 openid，天然防漏） |
| 测试要求 | 用 A 用户 token 操作 B 用户记录 id → 必须返回 `30001`，且 B 数据不变（测试计划中列为专项） |

---

## 7. 关键难点与风险

### 7.1 金额精度（用整数「分」存储）

**问题**：二进制浮点无法精确表示 0.1，`0.1 + 0.2 = 0.30000000000000004`；记账产品不允许出现分位偏差。

**方案（三层防御）**

| 层 | 措施 |
|----|------|
| 输入层（前端） | **字符串解析，禁止 `parseFloat(x) * 100`**。`parseToCents("12.5")`：按 `.` 拆分为整数/小数部分，校验小数部分 ≤2 位，补零后 `整数部分 * 100 + 小数部分`（全程整数运算）。反例：`parseFloat("0.29") * 100 = 28.999999999999996` → `Math.round` 侥幸正确，但 `1.005 * 100 = 100.49999` 会错 → **必须字符串法**。金额输入用 `type="digit"` 且以字符串状态存储（`value: string`），只在提交时转分 |
| 传输层 | JSON number 传整数分；服务端 `Number.isInteger(amount)` 校验，非整数直接 `10003` |
| 存储/聚合层 | 云数据库 `number` 为 IEEE754 double，**整数在 2^53（≈9.007e15 分 ≈ 9 万亿分 = 900 亿元）以内精确无损**。P0 单笔上限 10,000,000 分、单用户年累计量级 10^7 分，**距离 2^53 有 8 个数量级余量**，聚合求和（`$sum`）结果同样精确。结论：**整数分 + double 存储在本场景是安全的**（关键前提：写入的一定是整数，这是第二层校验的意义） |
| 展示层 | `formatCents(1250) = "¥12.50"`；千分位 `formatCents(120000) = "¥1,200.00"`；负数 `-¥50.00`（PRD 6.5 / design.md 3.5） |

**测试要求（已在 PRD 14.2 提出，技术侧配合）**：`utils/amount` 需有纯函数单测覆盖 `0.1+0.2`、`1.005`、`0.005`、100 笔 0.01 累加、`100000`/`100001` 边界、`-0`、空串、超长小数。

### 7.2 离线 / 弱网处理（P0 不做离线记账）

PRD 11 章明确 P0 不做离线记账与补传。技术侧只做「**确定性的失败语义**」，不做「猜用户想写什么」：

| 场景 | 处理 |
|------|------|
| 请求超时 | 8s（PRD 9 章）；超时后展示可重试错误态，**禁止无限 loading** |
| 重试策略 | 仅幂等接口自动重试 1 次（`txn.create/update/delete`、读接口），退避 300ms/800ms，最多 2 次；**非幂等绝不自动重试**（P0 所有写接口都幂等，故此规则安全） |
| `clientRequestId` 持久化 | **发起前先写 storage，成功后清除**；失败重试**复用同一 id**（否则去重失效，AC-2.15 会失败）。若 App 被杀，下次启动读到残留 id 且用户未重复提交 → 清理即可（不自动补传，P0 不做离线） |
| 写失败 | **禁止乐观更新/假删除**（AC-2.14/2.18/3.14/3.15）；失败即保留用户输入 + 展示可重试错误态 |
| 读失败 | 有缓存则展示缓存 + 「数据可能不是最新」轻提示；无缓存则错误态 + 重新加载；**绝不展示其他月份/其他用户数据**（AC-3.11） |
| 断网检测 | `wx.onNetworkStatusChange`：断网时把记账入口置灰并提示「网络未连接」（比让用户填完再失败体验更好） |
| 首屏 | 已登录 → 缓存 stats 渲染（标记 stale）+ 后台刷新（AC-1.7）；未登录失败 → 错误态 + 禁用记账入口（AC-1.4） |

### 7.3 数据安全与隐私合规

| 项 | 措施 |
|----|------|
| **最小采集** | 只采集 openid + 记账数据。**不采集**昵称/头像/手机号/位置/通讯录（PRD 7.2）。`user` 响应只有「同学」占位 |
| 数据库权限 | 所有集合「仅管理端可读写」，前端无直连能力（6.5） |
| 凭证管理 | AppSecret/token 签名 salt/日志脱敏 salt 只存**云函数环境变量**；不落代码仓库、不进日志 |
| 越权防护 | 所有读写绑定会话 openid（6.5）；测试专项覆盖 |
| 接口限流 | 5.1.6（防刷与防拖库） |
| 日志脱敏 | 日志中 openid 只出现 `sha256` 前缀（8 位）；**禁止打印完整 payload**（备注是用户私密文本） |
| 备注内容安全 | 备注**仅自己可见、不对外传播**，因此 P0 不接入内容安全接口（`msgSecCheck`）。**风险与对策**：若后续做分享/好友互看，必须补内容安全；已在 WR-3 登记 |
| 隐私协议 | MP 后台配置《用户隐私保护指引》，声明：收集 openid（账号识别）、记账数据（核心功能）、数据存储位置与删除方式（见 10.4，W1 需核对微信最新模板要求） |
| 数据删除权 | `user.clearData` / `user.delete`（5.4.2）；PRD OQ-2 待决策，工程建议 P0 就做 |
| 传输安全 | 全程 HTTPS（微信云开发调用天然加密） |
| 未成年人合规 | 大学生群体默认成年；若审核要求，帮助文档中说明「数据仅本地与云端账本使用」 |

### 7.4 统计查询性能优化

**目标**：AC-4.11 —— 2000 条记录时 `stats.monthly` P95 ≤ 500ms（口径待 PM 确认是否含冷启动，见 0.2 #1）。

| 优化 | 做法 |
|------|------|
| **冗余 `month` 字段** | 统计用 `month = '2025-09'` 等值查询，而非 `date` 范围扫描；等值 + 复合索引可让数据库直接定位区间，避免全集合扫描。**前提是 `month` 与 `date` 始终同步**（5.4.4 规定 update 必须重算，否则会出现「列表按新日期、统计仍旧月」的不一致） |
| **索引最左前缀** | 3 个业务索引第 1 列都是 `openid`，第 2 列是 `month`；查询条件与排序键顺序一致（`month, deleted, date desc, createdAt desc`）→ 复用同一索引完成「过滤 + 排序 + 分页」，避免内存排序 |
| **聚合分两步** | ① `match(openid, month, deleted:false)` → ② `group by type / categoryId` 做 `$sum`。**先过滤再分组**，且分组结果集极小（≤15 个分类） |
| **聚合必须显式 `limit`** | 云开发聚合结果默认分页返回（云函数端条数上限有限），分组后需显式 `.limit()`，否则会静默截断导致统计值偏小——**这是最容易踩的坑，W1 需实测确认** |
| **当前月与历史月同一条代码路径** | 历史月不额外增加查询：`card` 的差异由 `cardState` 判定，`totals`/`topCategories`/`recentTransactions` 都按请求的 `month` 计算（5.4.7 要点 2）。**性能口径对两者一致** |
| **一次请求返回首页全部所需** | 主卡 + 三列 + Top5 + 最近 3 笔合并在 `stats.monthly` 一次返回（1 次云函数调用 ≈ 1-2 次 DB 查询），而不是前端发 4 个请求（AC-4.1/4.7 + 首屏 ≤2.5s） |
| **列表合计随第一页返回** | `txn.list` 首屏带 `summary`，翻页不重复计算（AC-3.1 月度合计条） |
| **分页用游标** | 避免 `skip(n)` 在深分页时线性退化（AC-3.3） |
| **卡片/分类缓存** | `category.list` 带版本号，命中缓存返回 `notModified`，不查库 |
| **`lastActiveAt` 异步更新** | 不加入响应关键路径（每次鉴权后后台更新，失败可忽略） |
| **写路径不加额外读** | 幂等键的插入/读取是**点查**（唯一索引），不做范围扫描；`txn_dedup` 的 `expireAt` 清理是低频定时任务，不占用用户请求路径 |
| **云函数冷启动优化** | 依赖精简（不引入 axios/lodash/orm，用 esbuild 只打包用到的代码）；定时触发器每 5 分钟 `health.ping` 保活（注意：云开发实例数不定，保活只能缓解不能根治）；把 `users` 查询与 `sessions` 查询**并行**（`Promise.all`）而非串行 |
| **兜底方案（ADR-006）** | 若实测 P95 > 300ms 或单用户 > 1 万条：新增 `monthly_stats` 预聚合集合（`openid + month → incomeSum/expenseSum/txnCount`），在写操作后用「读-改-写 + 乐观锁（version）」维护。**这是有代价的方案**（引入一致性与并发问题），故**只在触发条件下启用**，不提前建设 |

### 7.5 其他风险

| 风险 | 说明 | 对策 |
|------|------|------|
| **云函数冷启动** | **AC-1.1（首屏 ≤2.5s）、AC-2.16（保存 P95 ≤800ms）、AC-4.11（统计 P95 ≤500ms）** 都可能被冷启动（1–3s）击穿 | ① 依赖精简 + 单函数复用连接；② 定时保活；③ 前端骨架屏（AC-4.12 本就要求）+ 首屏用缓存渲染；④ **已请 PM/测试明确三项性能 AC 的口径（0.2 #1）** |
| **云开发平台限额未知** | 单次查询条数上限、聚合条数上限、索引数量上限、**事务能力与语义**、免费套餐配额等需以控制台实际为准 | 见 8.5「W1 平台限额核对清单」，**W1 必须实测并把结论回填本文** |
| **平台锁定** | 见 ADR-001 | 防腐层 + 自定义 token；迁移成本已量化（重写 repositories + 换部署，业务规则不动） |
| **微信审核 / 类目** | 记账工具类目、隐私协议、数据删除途径 | W1 完成资质与隐私配置；P0 提供数据删除接口；文案不涉金融承诺 |
| **单点故障** | 云开发故障 = 全站不可用 | 接受（MVP 阶段无多活价值）；回滚方案见 2.4 |
| **跨月/时区错误** | 设备时间被改导致归月错乱 | 服务端 `serverDate` + `date ≤ serverDate` 校验 + `month` 由服务端派生并在 update 时重算（AC-1.8/2.7/2.8） |
| **埋点数据可信度** | 客户端时间/事件顺序不可信 | `events.serverTs` 为准；关键指标（首日记账转化、次周留存）可从 `transactions` 直接算（不依赖埋点），埋点仅做漏斗补充 |
| **成本增长** | `events` 与 `txn_dedup` 无界增长 | 分别 90 天、24h 清理；只存必要字段；批量上报 |

### 7.6 风险登记表（供测试评审引用）

| ID | 风险 | 概率 | 影响 | 处置 | 触发后动作 |
|----|------|------|------|------|-----------|
| R-1 | 冷启动导致首屏/保存/统计超时 | 高 | 中 | 7.5 | 调整 AC 口径或加保活/降级为缓存优先 |
| R-2 | 云开发聚合 `limit` 截断导致统计偏小 | 中 | 高 | 7.4 | 显式 `limit` + 用大数据集实测（测试造 2000 条） |
| R-3 | `clientRequestId` 未持久化导致重复提交 | 中 | 高 | 7.2 | 单测 + 弱网杀进程用例（AC-2.15） |
| R-4 | 越权访问他人记录 | 低 | 极高 | 6.5 | 立即回滚 + 复盘（PRD 13 章回滚条件） |
| R-5 | 未来日期入库（设备时间造假） | 中 | 中 | 6.4 | 服务端拦截 + 测试改设备日期用例（AC-1.8/2.8） |
| R-6 | 金额精度偏差 | 低 | 极高 | 7.1 | 纯函数单测 + 累加用例，阻断合并 |
| R-7 | 小程序资质/类目未就绪 | 中 | 高 | 0.3 | 排期顺延，CEO 决策 |
| R-8 | 时间旅行调试开关泄漏到生产 | 低 | 高 | 10.3 | CI 校验 + 生产环境变量缺失即关闭 |
| **R-9** | **云数据库事务能力不足（跨集合原子写不可用/语义有差异），影响幂等实现（4.8）** | **中** | **中** | **4.8 / 8.5** | **退化为「先插键 → 再插记录 → 失败删键补偿」+ 孤儿键两级兜底；AC-2.15 仍可满足（并发窗口由唯一索引保证），但需按 10.2 的用例验证退化路径** |
| **R-10** | **`month` 与 `date` 失同步（编辑跨月漏重算）导致列表与统计不一致** | **低** | **高** | **5.4.4 / 7.4** | **`month` 每次 update 无条件重算（自愈）+ 10.2 交叉断言用例；CI 红线：repositories 层的 update 语句不得缺少 `month` 字段** |

---

## 8. 开发里程碑排期

### 8.1 工作量估算（人日；1 人日 = 1 名工程师 1 个工作日）`[v0.2 修订：修正 v0.1 的小计加法错误，并计入 txn_dedup 幂等实现成本]`

| 模块 | 工作内容 | 人日 |
|------|----------|------|
| 工程准备 | 双云环境搭建、代码骨架（小程序端 + 云函数端）、TS 配置、错误码/常量、CI 红线脚本、日志与监控接入 | 2.0 |
| 登录鉴权 | `auth.login`（OPENID 上下文 + `code2session` 兼容分支）、`sessions`、token 校验中间件、限流中间件、`serverDate` 下发 | 2.5 |
| 数据模型 | **6 个 P0 集合** + 索引 + 种子脚本（分类）+ 定时触发器（含 `txn_dedup` 过期键清理） | 1.5 |
| 记账接口 | `txn.create/update/delete/detail`：校验、**`txn_dedup` 事务化幂等（含退化路径与冲突重试）**、软删除、越权判定、**update 重算 `month`** | 3.0 |
| 列表接口 | `txn.list`：游标分页、按日分组、月度合计、星期计算 | 1.5 |
| 统计接口 | `stats.monthly`：聚合、**主卡 4 态状态机**、Top5、最近 3 笔、`daysInMonth` | 2.0 |
| 分类接口 | `category.list` + 版本号 + （可选）30 天频次排序 | 0.5 ~ 1.0 |
| 前端-公共 | 脚手架、`services/api.ts`（token/重登/重试/错误码映射/`serverDate` 校正）、store、`utils/amount|date|format`（含单测） | 2.0 |
| 前端-首页 | 骨架屏、主卡 4 态渲染、三列、Top5、最近 3 笔、月份切换（含历史月口径）、错误态 | 3.0 |
| 前端-记账面板 | 半屏弹层、数字键盘、金额字符串处理、分类网格、日期选择器（禁未来）、校验提示、保存/撤销提示条、键盘遮挡适配 | 3.5 |
| 前端-账单与详情 | 列表分组/分页/空态/错误态、详情、编辑、删除二次确认、失败态保留内容 | 3.0 |
| 前端-登录与全局 | 静默登录、token 续期、登录失败禁用 4 个入口、网络状态处理、tabBar | 1.5 |
| 埋点与错误上报 | 11 类事件 + 批量上报 + `client_error` | 1.0 |
| 联调与缺陷修复 | W3 内部自测期间的前后端联调、缺陷修复（不含测试发现的探索性缺陷） | 3.0 |
| 合规与发布准备 | 隐私指引、数据删除接口、帮助文案、体验版提交、回滚开关 | 1.5 |
| **小计** | | **31.5 ~ 32.0** |
| 缓冲（10%） | 估算偏差与集成摩擦 | 3.2 |
| **合计（档位 A）** | | **≈ 35.0 人日** |

> **v0.1 勘误**：v0.1 同一张表的明细行相加应为 30.5~31.0，却写作「小计 32.5~33.0」，属加法错误；本版按明细逐项重算（并在记账接口 +0.5、数据模型 +0.5 计入 `txn_dedup` 幂等方案）：**小计 31.5~32.0，含缓冲合计 ≈35.0 人日**。

### 8.2 产能账户与排期表 `[v0.2 新增：审查意见 5]`

#### 8.2.1 产能账户（可用 vs 需求 vs 差额）

| 项 | 人日 | 说明 |
|----|------|------|
| **可用产能（W1–W3）** | **30.0** | 2 名工程师全职 × 3 周 × 5 天。W4 及之后按 PRD 13 章属于内测/灰度/发布阶段，**默认不排新功能开发** |
| **需求（档位 A，含缓冲）** | **35.0** | 见 8.1；其中纯开发 31.5~32.0 + 缓冲 3.2 |
| **差额（档位 A）** | **−5.0** | 需求 > 可用产能 |
| 处置 1：档位 B 裁剪（8.3） | −1.3 | 见 8.3；需求降至 **≈33.7**，差额 **−3.7** |
| 处置 2：W4 前 2 个工作日用于收尾（2 人 × 2 天） | −4.0 | 可用产能增至 **34.0**；差额 **+0.3（闭合）** |
| **最终结论** | | **档位 B + W4 前 2 天收尾 → 可行，余量 0.3 人日**；代价：W4 的 20 人内测启动由 W4 D1 顺延至 **W4 D3**（内测窗口 5 天 → 3 天，仍在 W4 周内，**不突破 PRD 13 章 Gate**） |

**缓冲去向（明确写死，避免排期时被无声吃掉）**

| 缓冲项 | 人日 | 用途 | 若被消耗完后仍超期 |
|--------|------|------|-------------------|
| 估算偏差与集成摩擦（10%） | 3.2 | 前后端接口对齐、联调返工 | **先顺延 W4 内测启动（逐天）**，**不得压缩 W3 自测** |
| W3 自测窗口 | 3 天（D1–D3） | 全 AC 回归 + 专项 + 性能压测 | **3 天为硬下限**；不足时顺延 W4，而不是缩短自测 |
| W4 收尾预留 | 4.0（2 人 × 2 天） | 缺陷清零、体验版提审、合规材料 | 若 W4 D3 仍未清零，则内测启动再顺延，并同步告知 PM |

> **硬规则**：**差额为负时，一律「顺延 W4 内测启动」消化，禁止用压缩 W3 自测窗口的方式消化**。W3 自测是唯一能拦住 AC 级缺陷的窗口，压缩它等于把风险转移到 W5 灰度（PRD 13 章回滚条件极严）。

#### 8.2.2 排期表（与 PRD 13 章 Gate 对齐）

| 周次 | 目标 | 交付 | 并行事项（非工程） |
|------|------|------|-------------------|
| **W1 D1–D2** | 技术方案评审通过；**关闭阻塞项** | 本文转 Accepted；OQ-1/OQ-2/0.2 的确认项有结论 | CEO：主体资质 + 云开发套餐 + 人力确认；PM：补 AC / 决策分类排序 |
| **W1 D1–D5** | 环境与骨架、登录鉴权打通、数据模型（6 集合）+ 种子数据、**云开发平台限额与事务能力实测并回填本文** | 可在真机完成「打开 → 登录 → 拿到 `serverDate`」 | 设计：首页 4 态 + 记账面板 + 图标（PRD 14.1） |
| **W2 D1–D5** | 全部 P0 接口（txn/stats/category，含 `txn_dedup` 幂等）+ 前端 4 页面 + 状态机 + 埋点；**W2 末前后端联调通过** | 主流程端到端可用（记账 → 列表 → 统计 → 撤销 → 编辑 → 删除） | W1 访谈结论回填；分类命名定稿 |
| **W2 末** | **提交体验版审核（预留审核周期）** | 体验版版本 | — |
| **W3 D1–D3** | 自测：全 AC 回归、金额/时区/**跨月编辑一致性**/越权/弱网/失败态/状态迁移专项、2000 条性能压测 | 缺陷列表 + 压测报告 | 设计走查、PM 验收 |
| **W3 D4–D5** | 缺陷清零 + 回归；PRD 13 章「内部自测 Gate」 | 自测通过报告 | — |
| **W4 D1–D2** | **收尾开发**（8.2.1 处置 2）：缺陷修复、体验版提审、合规材料、回滚演练 | 可内测版本 | — |
| **W4 D3–D5** | 20 名真实用户内测（窗口 3 天） | 内测报告（首日记账转化 ≥50%、耗时中位数 ≤20s） | PM 收集反馈，**不新增功能** |
| **W5** | 灰度 200 人 | 崩溃率 <0.5%、保存失败率 <1%、无越权 | 每日指标巡检 |
| **W6** | 正式发布 | 全量 | — |

**关键路径**：① 主体资质（CEO，最长外部依赖）→ ② 登录鉴权打通 → ③ 前端 4 页面 → ④ 自测。**任何一项在 W1 未就绪，W3 自测与 W4 内测需按天顺延。**

### 8.3 产能不足时的裁剪优先级（按「先砍成本高的、后砍验收必需的」）

| 档位 | 前提 | 裁剪内容 | 结果 |
|------|------|----------|------|
| **A（不裁剪）** | 2 名工程师全职 | — | **≈35.0 人日**；W1–W3 可用 30.0 → **缺口 −5.0**，即使吃掉全部 3.2 缓冲仍有 −1.8，**无法在不牺牲自测窗口的前提下闭合** |
| **B（推荐）** | 2 名工程师全职 | ① 分类频次排序 → 固定顺序（省 0.5，且与 design.md 8.5 建议一致）；② 记录详情由独立页 → 复用弹层组件（省 0.5）；③ 埋点先做 7 个核心事件（省 0.3）；④ `user.delete`/`user.clearData` 保留（合规不可砍） | **≈33.7 人日**；配 8.2.1 处置 2（W4 前 2 天）后**可行，余量 0.3** |
| **C（1 名工程师）** | 1 名工程师 | 档位 B 基础上再砍 Top5/最近 3 笔（0.5）、`txn.detail`（0.3）、编辑改「删除+重记」（0.3，**会破坏 AC-3.7，需 PM 同意降级**） | ≈32.6 人日 ÷ 1 人 ≈ 6.7 周 → **必须整体顺延约 2 周**（W1–W7 开发，内测/灰度/发布后移）。**不建议**为了保住 6 周而砍到破坏 AC —— 那是把验收风险转移到测试与用户 |

> **给 CEO 的明确建议**：MVP 按 **2 名工程师 + 档位 B** 排，并接受 **W4 内测启动顺延 2 天**；若只能 1 名工程师，**不要压缩测试窗口、也不要砍 AC**，而是把「内测 / 灰度 / 发布」整体顺延 2 周（PRD 13 章的 Gate 逻辑不变）。

### 8.4 质量门禁（编码阶段交付，本阶段只约定）

| 门禁 | 内容 | 失败处理 |
|------|------|----------|
| 类型检查 | TS 严格模式通过 | 阻断合并 |
| 纯函数单测 | `utils/amount`、`utils/date`、`domain/cardState`、`domain/validate`、`domain/idempotency` 覆盖率 ≥90% | 阻断合并 |
| 红线 1 | 小程序端不出现 `db.collection(` / `wx.cloud.database(` | 阻断合并 |
| 红线 2 | `utils/`、`domain/` 不出现 `wx.` 与云数据库 SDK | 阻断合并 |
| 红线 3 | 不出现裸 `new Date()` 作为「今天」（除 `utils/date` 内部） | 阻断合并 |
| 红线 4 | 生产配置中 `ENABLE_TIME_TRAVEL` 必须为未设置/false | 阻断发布 |
| **红线 5（v0.2 新增）** | **`transactions` 的 update 语句必须同时携带重算后的 `month`**（防 R-10） | 阻断合并 |
| 包体 | 主包 ≤1.5MB | 阻断发布 |
| 平台限额核对 | 8.5 清单结论已回填本文 | 阻断 W2 启动 |
| 契约回归 | 引用 AC 编号的接口用例全部通过 | 阻断自测 Gate |

### 8.5 W1 必须实测并回填的平台限额核对清单

| 待核对项 | 为何重要 |
|----------|----------|
| 云数据库单次查询返回条数上限（小程序端 / 云函数端） | 影响 `txn.list` 分页与统计聚合实现 |
| 聚合（aggregate）结果条数默认值与上限、pipeline 限制 | **直接影响 R-2（统计值被静默截断）** |
| 单集合索引数量上限、复合索引字段数上限、是否支持唯一复合索引与排序方向 | 影响 4.3 / 4.8 的索引设计 |
| **云函数端事务能力：`runTransaction` 是否支持跨集合读写、唯一索引冲突的错误语义、事务内读是否能读到并发未提交写入** | **直接决定幂等实现走「事务」还是「补偿 + 孤儿键清理」退化路径（R-9 / 4.8），并影响 10.2 用例设计** |
| 是否支持 TTL 索引（数据自动过期） | 影响 `events` / `txn_dedup` / `sessions` 的清理方式（本方案已按「不支持 TTL 也能工作」设计） |
| 云函数单次执行超时上限、内存选项、Node 运行时可选版本 | 影响冷启动与批量清理任务 |
| 定时触发器最小间隔与并发限制 | 影响清理与保活方案 |
| 免费/付费套餐的数据库容量、调用次数、外网流量配额与价格 | 影响成本与 `events` 保留期 |
| 云函数灰度发布能力 | 影响回滚方案（2.4） |

---

## 9. 架构决策记录（ADR）

### ADR-001：MVP 后端采用微信云开发（云函数 + 云数据库），而非自建 Node.js 服务

**状态**：提议中（待 CEO/评审确认平台绑定风险）

**背景**：团队规模小、周期 6 周、P0 无复杂事务与报表；自建后端需要服务器、HTTPS、ICP 备案、部署流水线、备份与运维，均为**与产品价值无关的固定成本**。

**决策**：后端 = 云开发云函数（Node.js 18）+ 云数据库；小程序端**不直连数据库**，全部经云函数。

**备选方案**
1. 云托管/云服务器 + Koa + MySQL：查询与事务能力更强、可迁移性好；但交付慢（备案周期数周）、运维成本高，在 6 周 MVP 中不划算。
2. Serverless 函数计算（非微信生态）+ 自建 API 网关：需要自处理鉴权与域名，且拿 openid 要自己调 `code2session`，比云开发多一层。

**影响**
- 更容易：上线速度、免运维、免备案、openid 上下文天然可信。
- 更难：复杂报表/跨集合事务（P1 才会遇到）；厂商锁定（用 repositories 层 + 自定义 token 对冲）；**部分平台限额不透明（见 8.5，必须在 W1 实测）**。
- 迁移路径：`repositories/*` 是唯一与云数据库耦合的层；接口契约（action + 统一响应包 + 自定义 token）与平台无关 → 迁移 = 重写 repositories + 换部署入口，**业务规则与前端视图层不动**。

### ADR-002：小程序端采用原生 + TypeScript，而非 Taro / uni-app

**状态**：已接受

**背景**：PRD 明确 MVP 只有微信小程序一种形态，App/H5 需 D30 留存 ≥25% 后再评估。

**决策**：原生 + TS，不用跨端框架、不用 UI 组件库。

**备选方案**：Taro 3 / uni-app —— 为「被明确排除的多端需求」提前支付构建链、包体、调试成本，且这类框架在小程序端的行为差异会消耗大量排期。

**影响**
- 更容易：包体控制（≤1.5MB）、启动性能、调试（无框架中间层）、招人与交接（原生是微信小程序最大公约数）。
- 更难：未来跨端需要重写视图层。
- 对冲：金额/日期/校验/状态机全部落在 `utils/` 与 `domain/`（纯函数、可单测、与框架无关），换框架时逻辑可移植。**反转成本 = 重写 4 个页面的视图层，约 6–8 人日**（可接受，因为触发条件是「留存达标」，那时才有预算）。

### ADR-003：金额以整数「分」存储与传输

**状态**：已接受（PRD 8 章硬性要求）

**背景**：`0.1 + 0.2 != 0.3`；记账产品的分位偏差 = 数据不可信。

**决策**：DB / 接口 / 埋点全链路整数分；前端字符串解析输入；服务端 `Number.isInteger` 校验；展示层统一格式化。

**备选方案**
1. `Decimal` / 字符串存金额：精确但云数据库无原生 Decimal 类型，需全链路字符串运算，复杂度高、聚合需额外转换。
2. 浮点存「元」：直接不可接受（累加误差、显示 `¥12.499999`）。

**影响**：double 在 2^53 内精确，本场景余量 8 个数量级（7.1）；代价是所有金额展示必须走 `formatCents`，禁止裸数字拼接。

### ADR-004：鉴权用「自定义 opaque token + sessions 集合」，而非 JWT，也不用云开发隐式 openid

**状态**：已接受

**背景**：PRD 7.3 要求服务端强制校验 openid；合规要求支持「用户删除数据后立即失效」；PRD OQ-1 要求工程确认登录方案。

**决策**：`wx.login` → 服务端（优先用云开发 OPENID 上下文，兼容分支用 `code2session`）→ 签发 64 位随机 token，服务端存 `sha256(token)`，7 天有效，可吊销。

**备选方案**
1. **纯 JWT**：免查库但**无法吊销**，且 openid 明文进客户端；而我们本来就要查 `users.status`，无状态优势不成立。
2. **完全依赖云开发上下文（不用 token）**：最省事，但把信任边界绑定在「调用方式」上，未来换 HTTP 网关即全废，且没有吊销语义。
3. **JWT + 黑名单**：等于两者缺点叠加。

**影响**：每次请求多一次索引查询（<30ms）；换来可吊销、可审计、平台无关的鉴权契约。token 泄露风险通过「不下发 openid / 不落日志 / 只存 hash」控制。

### ADR-005：分类以服务端集合为白名单，前端内置同源常量兜底

**状态**：已接受

**背景**：PRD 6.2 要求分类与服务端类型校验；PRD 11 章明确 P0 不做自定义分类。

**决策**：`categories` 集合为校验唯一来源；前端内置同源常量（由同一份 JSON 生成）保证弱网下面板可渲染；P0 不提供分类写接口。

**备选方案**：① 分类硬编码在服务端代码里（省一个集合，但改分类要发版云函数，且埋点/前端常量易漂移）；② 让用户自定义分类（PRD 明确排除，且会引出一个完整的 CRUD + 迁移 + 统计脏数据问题）。

**影响**：多一个只读集合与种子脚本（成本 0.3 人日）；换来「改分类 = 改数据 + 发版本号」，P2 若有用户自定义分类可平滑扩展（加 `owner_openid` 字段即可）。

### ADR-006：月度统计走实时聚合，暂不引入预聚合汇总表

**状态**：已接受（带触发条件）

**背景**：AC-4.11 要求 2000 条记录时 P95 ≤500ms。

**决策**：`stats.monthly` 实时聚合（`match + group`），依赖 `(openid, month, deleted, type)` 索引。

**备选方案**：`monthly_stats` 汇总集合（读快，但每次写都要同步维护，引入并发覆盖、编辑/删除/撤销的补偿逻辑、以及「汇总与明细不一致」整类 bug）。

**影响**：读路径依赖索引质量，需在 W3 用「单用户 2000 条」实测（8.4 门禁）。**触发条件**：单用户记录 > 1 万条 或 统计 P95 > 300ms → 启用预聚合（ADR 需追加）。这是「可逆性优先于最优」的典型取舍：先选容易改的。

### ADR-007：后端用「单云函数 `api` + action 路由」，而非一接口一云函数

**状态**：已接受

**背景**：P0 有 12 个 action，共享鉴权、限流、校验、响应包装与错误映射。

**决策**：单函数 + `action` 路由。

**备选方案**：一接口一函数 —— 部署粒度更细、故障面更小，但共享代码要靠层（layer）或复制，冷启动次数更多（AC-1.1/2.16/4.11 对延迟敏感），且 12 个函数的版本管理成本高于收益。

**影响**：单函数部署 = 一次影响全部接口（用 `action` 级开关 + 灰度缓解）；函数体积需控制（esbuild 只打包用到的代码）。达到「单接口需要独立扩缩容」（预计 QPS > 200）时再拆分。

### ADR-008：P0 不做离线记账，只做「失败语义确定性 + 幂等重试」

**状态**：已接受（对齐 PRD 11 章）

**背景**：PRD 明确 P0 不做离线记账与补传；且未登录/未初始化时禁止产生数据（AC-1.4）。

**决策**：无本地写入队列；网络不可用时禁用写入入口并明确提示；写失败保留用户输入 + 可重试；`clientRequestId` 先落 storage 防重复。

**备选方案**：本地队列 + 后台补传 —— 会引出「本地有数据云端没有」的合并/冲突/排序问题（PRD 5.2 已论证会破坏 P1 设计），且需要处理「离线期间用户删除/编辑同一条记录」的因果序。

**影响**：用户在地铁/电梯里无法记账（体验损失），但换来数据一致性与实现简单。**触发重新评估的条件**：登录失败率 > 2% 或内测中「没网记不了账」被明确抱怨（PRD 已列此条件）。

### ADR-009：幂等去重键独立存 `txn_dedup`（唯一索引 + 24h `expireAt`），而非 `transactions` 上的永久唯一索引 `[v0.2 新增]`

**状态**：已接受（对齐 PRD 8.2）

**背景**：PRD 8.2 要求「**窗口内**相同 `clientRequestId` 只落库一次，**超出窗口的同值视为新请求**」；AC-2.15 要求并发重复点击只产生 1 条记录。v0.1 的方案是 `transactions` 上建 `(openid, clientRequestId)` 唯一索引且不清理记录，其实际语义是「到记录被物理删除（≥30 天）为止都视为重复」，**与 PRD 8.2 的 24h 窗口矛盾**（且 v0.1 在 0.2 声称「对齐 PRD 8.2」，属声明与实现不一致）。测试第 1 轮审查意见 1 要求二选一，本 ADR 记录选型理由。

**决策**
1. **新增 `txn_dedup` 集合**承载幂等键，`(openid, clientRequestId)` 唯一索引，字段含 `expireAt = createdAt + 24h`、`txnId`、`requestHash`。
2. **窗口判定只看 `expireAt`**：`expireAt > now` → 重复；`expireAt ≤ now` → **放行新建**（事务内删除过期键后重插）。
3. **`transactions` 上不再建 `clientRequestId` 唯一索引**（字段保留，仅用于审计）。
4. 优先用**云函数端事务**保证「去重键 + 记录」原子；若平台事务能力不足，退化为「先插键 → 再插记录 → 失败删键补偿」+ 孤儿键两级兜底（4.8）。

**备选方案**
1. `transactions` 上永久唯一索引（v0.1 做法）：实现最简单、并发最安全，但**语义与 PRD 8.2 冲突，需 PM 改 PRD**；且「24h 后同 id 提交」会得到一个对用户无意义的冲突错误。**放弃**。
2. 记录上存 `clientRequestId` + `dedupExpireAt`，用普通索引 + 查询时判窗：省一个集合，但**失去唯一索引这一并发闸门**，两个并发请求可能同时通过判窗 → 产生重复记录，**直接违反 AC-2.15**。**放弃**。
3. 引入 Redis 分布式锁：解决同一问题，但引入新的单点与运维成本（3.5），对一个 24h 窗口的幂等需求属过度设计。**放弃**。

**影响**
- 更容易：与 PRD 8.2 / AC-2.15 双口径一致；去重窗口可独立调节（未来若 PM 改窗口，只改 `expireAt` 公式）；`transactions` 少一个唯一索引（写放大降低）。
- 更难：多一个集合与一套清理任务；`txn.create` 多一次事务（或补偿逻辑），**并发边界上多一个失败模式（R-9）**，必须由测试用专项用例覆盖（10.2）。
- 成本：约 +0.5~1.0 人日（已计入 8.1）。
- **不引入 `transactions` 唯一索引的代价**：若未来有人在别处绕过 `services/api.ts` 直接写库，将失去最后一道防重保险——但小程序端本就无直连权限（6.5），此风险已被架构隔离。

---

## 10. 测试配合要求（可测性设计）

### 10.1 给测试的契约要点

1. **所有业务错误码可断言**（5.2 表，共 24 条记录 = 成功码 `0` + 23 个错误码）；响应含 `requestId`，缺陷报告请附上它。
2. **主卡状态可精确断言**：`cardState` 是显式枚举；不适用字段为 `null`（如历史月的 `dailyAvailable`/`remainingDays` 为 `null`、`showProgress:false`），因此 AC-4.13「不得出现剩 N 天」可由接口断言 + UI 断言双重覆盖。
3. **历史月只切主卡口径**：断言历史月时，`topCategories` / `recentTransactions` **应返回该月真实数据**（非空，除非该月确实无数据）——这是审查意见 3 修正后的口径。
4. **同月一致性交叉断言**：`txn.list.summary` 与 `stats.monthly.totals` 在任意时刻必须相等（PRD 9 章），**包含「编辑记录跨月」之后**（见 10.2）。
5. **金额断言单位是分**：接口返回的 `amount`/`mainAmount` 都是分，UI 上是 `¥`，测试需明确两层断言，避免「接口对、UI 错」漏测。
6. **幂等窗口语义**：窗口 24h（`txn_dedup.expireAt`），**超窗同值放行新建**；测试需用 dev 环境造数（见 10.3）而非等待 24h。

### 10.2 需要测试重点覆盖的技术风险（与 7.6 对应）

| 用例 | 目标 |
|------|------|
| 同一 `clientRequestId` 并发/重复提交 3 次 | 只落 1 条（AC-2.15） |
| **同一 `clientRequestId` 在窗口内重复**（`expireAt > now`） | 返回首次结果 + `idempotent:true`；**库里仍 1 条** |
| **同一 `clientRequestId` 已过窗**（把 `txn_dedup.expireAt` 改成过去时间后重放） | **正常新建第 2 条记录**（PRD 8.2「超窗视为新请求」）—— 审查意见 1 的直接验证用例 |
| **同一 `clientRequestId` 窗口内但内容不同**（改金额后重放） | 返回 `10101` |
| **孤儿键**（造一条 `expireAt > now` 但 `txnId` 无对应记录的键后提交） | 删除孤儿键并正常新建，不返回 10101/500 |
| 提交过程中杀进程/断网后重试 | 复用同一 id，不产生重复（R-3） |
| **编辑记录使其跨月**（9 月记录改日期到 8 月） | `txn.update` 重算 `month`；**编辑后 8 月统计 +、9 月统计 −，且 `txn.list`（8 月/9 月）`summary` 与 `stats.monthly.totals` 同月合计一致**（AC-3.7 + PRD 9 章，审查意见 2） |
| **编辑日期到未来 / 早于 12 个月** | 返回 `10006`，记录不变（校验与创建期一致） |
| **编辑把 type 从支出改为收入但不改 categoryId** | 返回 `10007`，不产生「收入+餐饮」脏数据 |
| 用 A 的 token 读写 B 的记录 id | `30001`，B 数据不变（AC 越权专项） |
| 2000 条记录下 `stats.monthly` 性能 | AC-4.11 + R-2（统计值正确性，不只是快） |
| **AC-2.16 保存性能**（≤5000 条时 P95 ≤800ms） | 需在**热调用**下测（冷启动口径待 PM 确认，0.2 #1） |
| 2000 条中跨多个月的数据 | 归月正确（AC-2.7） |
| **历史月切换后 Top5 与最近记录非空且为该月数据** | AC-4.8/4.13 + 审查意见 3（断言与当前月 Top5 内容不同、最近记录 = 该月最后 3 笔） |
| 金额边界 0 / 0.001 / 0.005 / 100000 / 100001 / 100 笔 0.01 累加 | 精度（R-6） |
| 设备时间改到上月/下月/其他时区 | AC-1.8 / AC-2.8 / AC-3.9 |
| 写操作失败（模拟超时/500） | AC-2.14/2.18/3.14/3.15（不假成功） |
| 主卡 4 态两两迁移 | 不残留上一态数值/元素（AC-4.6/4.13/4.14） |

### 10.3 「时间旅行」调试开关（时间相关 AC 的可测性前提）

- 目的：测试无法修改云函数服务器的系统时间，而 AC-1.8 / AC-2.7 / AC-2.8 / AC-3.9 / AC-4.13 / AC-4.14 都依赖「服务端今天是几号」。
- 机制：**仅 dev 环境**（云环境变量 `ENABLE_TIME_TRAVEL=true`）时，接口接受可选 `debugServerDate: 'YYYY-MM-DD'`，服务端用它替代「今天」（**仅影响日期判定，不影响 `createdAt` 等真实时间戳**）。
- **附带能力**：同一开关下提供 `debugExpireDedupKey: true`，把指定 `clientRequestId` 的 `txn_dedup.expireAt` 篡改为过去时间，用于验证「超窗放行新建」（10.2）。**该能力同样仅 dev 可用。**
- 约束：`prod` 环境变量缺失即功能关闭；**CI 门禁校验生产配置**（8.4 红线 4）；接口返回中带回 `serverDate` 即为注入后的值，便于测试断言。
- 需 CEO 确认口径（0.3 #4）：允许存在调试开关（仅 dev），安全审查会关注此点。

### 10.4 合规相关的可测点

| 用例 | 期望 |
|------|------|
| 调用 `user.clearData` 后再登录 | 账本为空，统计归零，旧 token 失效 |
| 调用 `user.delete` 后 | `users`/`transactions`/`txn_dedup`/`sessions` 中该 openid 数据全部不存在 |
| 小程序端尝试直连数据库 | 无权限（拒绝），必须经云函数 |
| 请求日志中是否出现完整 openid / 备注原文 | 不应出现（脱敏） |
| 埋点事件名/参数不在白名单 | 被静默丢弃，不计入数据 |

---

## 11. 开放问题与技术债登记

| ID | 类型 | 内容 | Owner | 截止 | 状态 |
|----|------|------|-------|------|------|
| WR-1 | 待决策 | **性能 AC 口径是否含云函数冷启动**：AC-1.1 / **AC-2.16** / AC-4.11（v0.2 已补 AC-2.16，见 0.2 #1） | PM + 测试 | W1 评审 | 待确认 |
| WR-2 | 待决策 | 分类频次排序是否保留（0.2 #2） | PM | W1 评审 | 待确认 |
| WR-3 | 风险 | 备注当前不接内容安全接口（仅自己可见）；若 P2 做分享需补 `msgSecCheck` | 工程 + PM | P2 前 | 已登记 |
| WR-4 | 待实测 | 云开发平台限额清单（8.5，**含事务能力**）必须在 W1 实测并回填本文 | 工程 | W1 D5 | 待办 |
| WR-5 | 待决策 | OQ-2：数据删除（`clearData`/`delete`）是否纳入 P0 | PM + CEO | W1 评审 | 待确认 |
| WR-6 | 技术债 | `events` / `txn_dedup` / `transactions` 的物理清理依赖定时触发器；若触发器不可靠需人工巡检 | 工程 | W3 | 已登记 |
| WR-7 | 技术债 | `monthly_stats` 预聚合未实现（ADR-006 触发条件见 7.4） | 工程 | 触发后 | 已登记 |
| WR-8 | 待决策 | 是否允许 dev 环境「时间旅行 + 幂等键过期」调试开关（0.3 #4 / 10.3） | CEO | W1 评审 | 待确认 |
| WR-9 | 依赖 | 小程序主体资质与类目（PRD 12.1） | CEO | W1 D1 | 待确认 |
| WR-10 | 依赖 | 云开发套餐与费用预算（0.3 #2） | CEO | W1 D1 | 待确认 |
| **WR-11** | **依赖** | **人力与排期：2 名工程师 + 档位 B + W4 内测顺延 2 天（8.2，v0.2 新增）** | **CEO** | **W1 D1** | **待确认** |
| **WR-12** | **风险（已登记，无需 PM 决策）** | **幂等实现依赖云数据库事务；若事务能力不足则走补偿路径（R-9）** | **工程** | **W1 实测** | **待实测** |

---

## 12. 变更记录

| 版本 | 日期 | 变更 | 作者 |
|------|------|------|------|
| v0.1 | D0 前 | 初稿：整体架构与云开发选型、技术栈三张对比矩阵、数据模型（5 集合 + 索引）、12 个接口契约与错误码表、登录鉴权与 `serverDate` 下发、关键难点（金额精度/弱网/安全合规/统计性能）、里程碑与工作量估算、ADR-001~008、测试可测性设计 | 工程（架构） |
| **v0.2** | **D0 前** | **按测试第 1 轮审查意见（`docs/review-tech-plan-r1.md`）逐条修订 7 项**：<br>①【必修 1】幂等窗口语义与 PRD 8.2 对齐——新增 `txn_dedup` 集合（唯一索引 + 24h `expireAt`），超窗放行新建，移除 `transactions` 上的永久唯一索引；新增 ADR-009；4.8 / 5.1.5 / 5.4.3 / 2.3 同步改写；<br>②【必修 2】`txn.update` 明确 `date` 变更必须重算 `month`（并每次更新无条件重算以自愈）+ 复用创建期日期校验（`≤ serverDate`、滚动 12 个月）；补 10.2 跨月编辑「列表与统计同月合计一致」交叉断言；新增 R-10 与 CI 红线 5；<br>③【必修 3】修正历史月 `stats.monthly` 示例（`topCategories`/`recentTransactions` 返回该月真实数据），并补第 2 条设计要点显式说明「历史月只切主卡口径」；<br>④【应改 4】集合口径统一为「P0 6 个集合（3 业务 + 3 辅助）+ 1 个 P1 预留 `budgets`」，0.1/2.1/3.1/4.1/8.1 全部同步；<br>⑤【应改 5】新增 8.2 产能账户（可用 30.0 / 需求 35.0 / 差额 −5.0 / 处置后 +0.3）与「缓冲去向」表；**修正 v0.1 小计加法错误（30.5~31.0 误写为 32.5~33.0）**；明确「差额为负一律顺延 W4，禁止压缩 W3 自测」；<br>⑥【应改 6】错误码表标注实际条数（24 条记录 = 成功码 + 23 错误码），并更正 v0.1 汇报中「28 条」的口径错误；<br>⑦【应改 7】性能 AC 冷启动口径澄清范围由 AC-1.1/AC-4.11 扩至含 **AC-2.16**（0.2 #1、7.5、10.2、WR-1 同步）；<br>另：新增 R-9（事务能力不足）、8.5 增加事务能力核对项、10.3 增加幂等键过期调试能力、11 章新增 WR-11/WR-12、0.3 新增人力排期决策项、修正 2 处文字笔误（`month` 字段写法、状态 B 示例注释）。 | 工程（架构） |
| | | （评审意见请逐条追加，P0 契约变更必须在此登记并同步 PRD/测试计划） | |

---

**请测试重点审查（本方案自认的薄弱环节，v0.2 更新）**
1. **幂等语义（审查意见 1 的修订结果）**：`txn_dedup` + `expireAt` 判窗是否足以让 PRD 8.2 的「24h 内重复 / 超窗放行」两条断言都可被验证（10.2 已给出可造的用例）；**并请确认「事务不可用时的补偿路径」是否也被验收覆盖**（R-9）。
2. **跨月编辑一致性（审查意见 2 的修订结果）**：5.4.4 的「重算 `month` + 复用日期校验」是否足以支撑「编辑跨月后列表与统计同月合计一致」的交叉断言。
3. **历史月口径（审查意见 3 的修订结果）**：修正后的示例与设计要点 2 是否与 `design.md` 5.3/5.5「切月后 4 个区块同时切换、历史月最近记录 = 该月最后 3 笔」完全一致；为空数组的合法条件是否表述清楚（仅「该月确实无数据」）。
4. 5.4.7 的 `cardState` 枚举与 `null` 语义是否足以覆盖 PRD 6.4.1 / 6.4.2 主卡 4 态的全部断言（尤其 AC-4.13 的「不出现剩 N 天」）。
5. 5.4.8 游标分页在同日多笔 + 翻页 + 切换月份时的「无重复无跳项」是否可被验证（AC-3.3/3.10）。
6. 7.1 的「整数分 + double 存储」论证是否成立、边界用例是否足够。
7. 10.3 时间旅行开关（含幂等键过期注入）的测试方案是否满足时区/跨月/幂等窗口类 AC 的验证需求。
8. 「写操作失败不假成功」（AC-2.14/2.18/3.14/3.15）在客户端实现上是否存在被绕过或难以模拟的路径。
9. 8.2 产能账户的算术与「W4 内测顺延 2 天」是否被测试接受（这是本版唯一影响排期 Gate 的结论变更）。
