# 小账本 · MVP 技术方案（TechPlan）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v0.2（修订稿：已按测试第 1 轮审查意见逐条修订，修订点见**附录 C 变更记录**与开头的「本版修订说明」） |
| 作者 | 工程师 |
| 阶段 | MVP（本阶段**只出文档、不写代码**，代码结构仅为方案描述） |
| 上游文档 | `docs/PRD.md` v0.2（产品经理）、`docs/InteractionDesign.md` v0.2（设计师） |
| 覆盖范围 | PRD 全部 P0 需求（US-01 ~ US-06）+ 支持性能力（埋点、错误处理、幂等、并发） |
| 关联下游 | 测试计划（测试）、代码实现（本人，下一阶段） |
| 阅读指引 | 测试：重点看 §5 接口、§6 时序、§7 关键难点、§13 风险与待确认；PM：重点看 §3 选型、§8 决策记录、§13 待确认问题；设计师：重点看 §5.7 客户端契约、§10 性能预算 |

> **证据说明**：本阶段没有真实的线上流量、压测数据与用户量。文中所有容量、耗时、并发数字均标注为**估算**并给出估算依据（§10），凡未验证的技术事实（云开发唯一索引错误码、`limit` 上限、`aria-*` 支持度等）均标注「**待 M0 真机/控制台核实**」，不作为既定结论。

---

## 0.1 本版修订说明（v0.2，给审查人复查用）

**必须修改的 3 项**

| # | 审查意见 | 处理 | 落点 |
| --- | --- | --- | --- |
| 1 | `record.update` 所需的 `version` 没有可取来源（影响 P0 US-05 编辑） | **已修改**：在 `record.list` 的 `items` 与 `home.summary` 的 `recent_records` 中**明确返回 `version`**；不新增 `record.get`（ADR-14）。同步 §5.6 `listRecords` 签名、§6.7 时序，并补充「`record.update` 未传的字段一律不修改（含 `note`）」，因为首页最近记录不下发 `note` | §5.3.2、§5.3.4、§5.3.6、§5.6、§6.7、ADR-14 |
| 2 | §5.2 路由表漏 `budget.get`、action 计数不符 | **已修改**：路由表补 `budget.get`（读+当前月可能写、幂等键 = `ensureMonthBudget`），**计数改为 11**（§5.2、附录 C 同步）；`budget.get` 的月份口径明确为**仅当前月，历史月/未来月一律 1001**（ADR-16） | §5.2、§5.3.7、ADR-16、附录 C |
| 3 | 分页硬上限 1000 条会静默截断、与组头/月合计口径冲突 | **已修改**：**取消 `page ≤ 50` 硬上限**，改为按 `has_more` 翻到底；接口新增 `total_count`，`has_more = page*page_size < total_count`；到底显示设计稿 N3 文案「本月就这些了」。游标分页的升级触发条件写具体（单月 >2000 条或分页 P95 >500ms），写入 ADR-6、§6.5、§7.4、§7.6 与 M2 的 E9 验证项 | §5.3.6、§6.5、§7.4、§7.6、ADR-6、§12 M2、§13-Q9 |

**建议项 8 条**

| # | 建议 | 处理 | 落点 |
| --- | --- | --- | --- |
| ① | D1–D21 未说明是自然日还是工作日 | **已修改**：明确 **D = 自然日**，并给出折算（21 自然日 ≈ 3 周 ≈ 15 个工作日）与节假日顺延规则 | §12 开头 |
| ② | 设计稿 Q2（我的页是否显示「已记录 X 笔 · 使用 N 天」）未回答 | **已回答**：MVP **建议省略该行**；若 PM 要求，用「已记录 X 笔 · 加入 N 天」实现（`user.profile` 条件性 action，**不计入 11 个**，规格已给，成本 ≈0.3 天）。理由与代价写在 §13-Q11 | §13-Q11 |
| ③ | `user.init.has_budget_this_month` 可能让首屏先闪引导态 | **已修改（比建议更彻底）**：**删除该字段**（不是加说明）——留着必然被误用，且它白搭一次预算查询；首屏是否引导态**只由 `home.summary.budget` 决定** | §5.3.1、§6.1 |
| ④ | `record.delete` 的 `version` 可选，与 E12 口径不一致 | **已修改**：删除入参**去掉 `version`**，明确 **last-write-wins**（不弹冲突提示）；并写清「编辑时记录已被另一设备删除 → 1005 静默重拉」这一半路径，因此 E12 仍然满足 | §5.3.5、§6.7、§7.5、ADR-15 |
| ⑤ | `track` 调用约定与 §5.1 不统一 | **已修改**：`track` 统一为 `{action:'track.report', payload:{events}, request_id, app_version}`，响应同样是标准包 `{code,msg,data,request_id}`，客户端复用同一个 `request.js` | §5.4 |
| ⑥ | 收入/支出的「其他」id 相同会让 `isValidCategory` 有歧义 | **已修改**：拆成 **`expense_other` / `income_other`**（展示名都叫「其他」），兜底分类由 `type` 决定；同步 records 兜底说明与客户端常量约定 | §4.2、§4.5 |
| ⑦ | `earliest_month` 在用户完全无账目时未定义 | **已修改**：定义为 **= 服务端当前月**（对齐 PRD §7.4-1 与设计 E16「无账目时左箭头置灰」），并补客户端判定规则与 `server_month` 出参 | §5.3.6 |
| ⑧ | 埋点金额分桶需 PM 尽快确认，否则 test-plan 指标口径要改 | **已修改**：**默认按分桶实现**（不等确认，避免阻塞测试写用例），并给出**桶边界与左闭右开归属规则**，使其可测；PRD §2.3 的 `amount` 映射为 `amount_bucket`。若 PM 否决需在 M4 前回复 | §5.5、§13-Q4 |

---

## 0. 结论摘要（先看这一段）

1. **架构选型**：微信小程序（原生）+ **微信云开发（云函数 + 云数据库）**，不用自建服务器 + MySQL。核心理由：云开发免服务器运维、免备案域名配置、云函数内直接拿 `OPENID`（免 `code2session`、免管理 `AppSecret`），把 3 周工期押在业务上（ADR-1）。
2. **后端形态**：**2 个云函数**（`api` 统一业务入口 + `track` 埋点入口），`api` 内部按领域拆 handler（`user/home/record/budget/stat`），共享 `common/*` 纯函数模块。放弃"每个领域一个云函数"，因为 6 个函数的部署/冷启动/公共代码复用成本高于单入口（ADR-2）。
3. **数据模型**：4 个集合 `users` / `records` / `budgets` / `events`；分类在 MVP 是**云函数常量表**，不建集合（ADR-7），且收入/支出的「其他」使用**各自独立的 id**（`expense_other` / `income_other`，§4.5）。金额一律**整数「分」**（ADR-4）；日期一律 `'YYYY-MM-DD'` 字符串（ADR-3）。
4. **幂等**：`records` 建**唯一索引 `(user_id, client_record_id)`**，`budgets` 建**唯一索引 `(user_id, month)`**；重复写冲突后回查并返回同一条（ADR-5）。编辑用 `version` 乐观锁（E12），**`version` 由列表/最近记录读接口直接下发**，无需额外补取接口（ADR-14）；**删除为 last-write-wins**（ADR-15）。
5. **时区**：云函数运行在 UTC，**所有"今天/本月/剩余天数"由服务端按 UTC+8 计算并下发**，客户端不信任设备时间（§7.2）。
6. **预算沿用（回答 PRD R10）**：仍由服务端 lazy 写入，但**只为「服务端当前月」写入**（与 PRD E15 语义一致），单一入口 `ensureMonthBudget()` + 唯一索引 + 冲突忽略（§7.5、ADR-9）。**此处与 PRD §7.1 字面"请求某月数据（首页/账单/统计）时"有细微差异，需 PM 确认**（§13-Q1）。
7. **删除**：MVP 物理删除，**不提前建 `deleted_at` 列**（回答 PRD §13.3 第 2 条，ADR-11）；P1 做撤销时用"字段不存在即未删除"策略，避免全量迁移。
8. **列表分页**：**无硬条数上限**（取消原 `page ≤ 50`），按 `has_more` 翻到底，接口下发 `total_count`；`month_totals`/`day_totals` 与分页解耦，翻到底后与可见条目必然一致（ADR-6、§5.3.6）。
9. **风险最大的一项不是技术**：小程序备案与《用户隐私保护指引》（PRD R2）在关键路径上，**M0 就要请 CEO 并行启动**，否则代码写完也不能上线（§12、§13）。

---

## 1. 范围与输入

### 1.1 本方案必须支撑的功能（来自 PRD §6 P0）

| 需求 | 技术落点 |
| --- | --- |
| US-01 静默登录 | `user.init`（云函数内取 `OPENID`，首次创建用户） |
| US-02 记支出 | `record.create`（幂等）+ 记账页自绘键盘 |
| US-03 记收入 | 同 US-02，`type=income` 走同一接口 |
| US-04 预算与剩余可用 | `budget.get/set/cancel` + `home.summary` + `ensureMonthBudget` |
| US-05 账单与账目管理 | `record.list/update/delete` |
| US-06 基础统计 | `stat.monthly` |
| 埋点（PRD §2.3） | `track.report` + `events` 集合 |

### 1.2 本方案不做（对齐 PRD §4）

不接银行/支付、不做多账本/共享账本、不做分类预算、不做推送、不做数据导出、不做深色模式、不做多币种、不做离线队列与本地草稿、不做撤销。**技术上对应**：不引入消息队列、不定时任务、不接第三方支付 SDK、不做本地持久化队列（ADR-12）。

### 1.3 待上一级确认后才能动工的前置项

| # | 前置项 | 阻塞什么 | 责任人 |
| --- | --- | --- | --- |
| P-1 | 小程序主体备案 + 隐私保护指引（收集 openid、记账数据、埋点） | 提审上线 | CEO |
| P-2 | 云开发环境与套餐（估算用量见 §10） | M0 起全部开发 | CEO（开通） |
| P-3 | 「我的」页方案 A+（3 Tab + 二级页）还是方案 B（4 Tab） | 页面数、是否要 `user.clearMyData` | CEO + PM（设计稿 §11-Q1） |
| P-4 | R3「清空我的账目」MVP 是否做 | 是否实现 `user.clearMyData` | CEO |
| P-5 | 埋点里的金额是否改为分桶上报 | `track` 的字段白名单 | PM（§13-Q4）**注：本方案已默认按分桶实现，PM 否决才需改** |

---

## 2. 整体架构

### 2.1 架构图

```
┌────────────────────────── 微信小程序（原生，竖屏锁定） ──────────────────────────┐
│  pages/                                                                          │
│   home（Tab1 首页）  record（记账/编辑，含自绘键盘）  bill（Tab2 账单）            │
│   stat（Tab3 统计）  budget（预算设置）  mine（我的，待 Q-P3 定）                  │
│  components/  btn / skeleton / dialog / toast / keypad / category-grid / money   │
│  utils/  request.js（callFunction 封装）  format.js（金额/百分比格式化）           │
│          time.js（仅用服务端 server_date 做展示锚点）  constants.js（错误码）      │
│  store/  user_key、分类表缓存、server_date（轻量，无状态库）                       │
└───────────────┬──────────────────────────────────────────────────────────────────┘
                │ wx.cloud.callFunction（免域名白名单、免鉴权头）
                │ 所有集合权限 = 仅管理端可读写 ⇒ 客户端永不直连数据库
                ▼
┌──────────────────────── 微信云开发（CloudBase） ────────────────────────────────┐
│  云函数 api（单入口，action 路由）                 云函数 track（埋点，独立）      │
│   ├ common/  time.js  money.js  validate.js  errors.js  db.js                   │
│   │          categories.js  budget.js  stat.js  record.js                       │
│   └ handlers/ user  home  record  budget  stat                                  │
│                                                                                 │
│  云数据库（文档型 / MongoDB 兼容）                                               │
│   users   records   budgets   events                                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块划分与职责（每个模块只做一件事）

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| 客户端 `pages/*` | 渲染、交互、四态、按 `onShow` 重拉、把用户输入变成字符串金额 | 不做业务计算（剩余可用/日均/百分比由服务端或 `utils/format.js` 的纯格式化函数完成）、不直连数据库 |
| `utils/request.js` | 统一 `callFunction`：超时、错误码 → 文案映射、`request_id`、请求序号（丢弃过期响应）、读接口重试 1 次 | 不做业务重试（写接口重试由用户触发，靠 `client_record_id` 幂等兜底） |
| 云函数 `api/handlers/*` | 参数校验 → 权限（取 OPENID）→ 调 `common/*` → 统一响应 | 不直接拼 SQL/不做格式化文案 |
| `common/time.js` | **唯一**的"今天是哪天/本月是哪月/剩余几天"来源（UTC+8） | 不做展示格式化（那是客户端的事） |
| `common/money.js` | 金额合法性、整数分运算、四舍五入到分、埋点金额分桶 | 不做展示（`¥1,500.00` 由客户端 `format.js`） |
| `common/validate.js` | 声明式入参校验（手写轻量版，见 ADR-6 相邻说明） | 不引入第三方 schema 库 |
| `common/budget.js` / `stat.js` / `record.js` | 领域逻辑（沿用写入、聚合、CRUD），被 `home`/`budget`/`record`/`stat` handler 与彼此复用 | 不做 HTTP/云函数壳 |
| 云函数 `track` | 埋点批量落库（限批大小 + 白名单 + 分桶） | 不参与任何业务判断；失败绝不影响业务 |

> **为什么把 `common/*` 抽成纯函数**：金额、时间、预算、聚合四块是 bug 高发区，也是**唯一能离线单测**的部分（§12.3）。接口先行的关键就是先冻结这四组函数签名（§5.6）。

### 2.3 代码目录结构（下一阶段落地，本阶段仅描述）

```
code/
├─ miniprogram/
│   ├─ app.js / app.json / app.wxss        # app.json 里 pages（3~4 Tab）、窗口与竖屏配置
│   ├─ pages/{home,record,bill,stat,budget,mine}/
│   ├─ components/{money,keypad,category-grid,skeleton,dialog,toast}/
│   ├─ utils/{request.js,format.js,time.js,constants.js}
│   └─ store/index.js
├─ cloudfunctions/
│   ├─ api/{index.js,handlers/*.js,common/*.js,package.json}
│   └─ track/{index.js,events-schema.js,package.json}
├─ scripts/{verify.js,seed.js}             # 只读对账脚本 / 造数脚本（测试环境）
└─ README.md                               # 运行与部署说明（下一阶段产出）
```

---

## 3. 技术选型及理由

### 3.1 后端方案评估（这是本方案最重要的一个选型）

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| **A. 微信云开发（云函数 + 云数据库）** | ① 免服务器与运维；② **免业务域名/HTTPS 证书配置**（云调用不受 request 域名白名单约束）；③ 云函数内直接取 `OPENID`，**免 `code2session`、免管理 `AppSecret`、免自定义登录态**；④ 微信侧有免费额度，MVP 成本可控；⑤ 天然与小程序同账号发布 | ① 文档型数据库无外键/无跨集合事务（本 MVP 也**不需要**事务，见 §7.5）；② 单次查询返回条数有上限（云函数端**待 M0 核实**，估算 1000，聚合管道不受此限）；③ 云函数冷启动影响首屏（§10） | **选 A** |
| B. 自建轻量后端（云服务器 + Node + MySQL） | ① 关系模型、唯一约束与事务成熟，统计/对账更顺手；② 不受云函数超时与查询条数限制；③ 数据在自己手里 | ① 要买服务器、配 HTTPS、配域名白名单；② 要实现 `code2session` 与登录态（保存/刷新 `session_key`），**必须管理 `AppSecret`**（密钥泄露风险更大）；③ 运维、备份、扩容全自己扛；④ 至少多花 3~5 天 | 不选 |
| C. 云开发 + 外部数据库/数仓 | 数据分析能力强 | MVP 阶段纯属过度设计 | 不选（P1 若需 BI 再把 `events`/`records` 导出到外部） |

**结论与代价（写成取舍）**：**选择 A（微信云开发），而不是 B（自建后端）**，因为本项目管理成本与登录链路的复杂度是最大浪费来源，而 MVP 的并发与数据量级（§10）远低于云开发天花板。**代价**：① 放弃了 MySQL 的关系约束与事务，幂等只能靠**唯一索引 + 回查**实现（ADR-5）；② 丧失了 SQL 自由度，统计要走聚合管道（§7.4）；③ 一旦将来要迁出，需要写导出与重建脚本（届时用 `scripts/` 导出 + 重建表，成本可估但不算小）；④ 冷启动是首屏 1.5s 目标的主要敌人。

### 3.2 选型清单

| 层 | 选型 | 理由 | 放弃的方案 / 代价 |
| --- | --- | --- | --- |
| 客户端框架 | **微信小程序原生**（WXML/WXSS/JS ES2018） | 只有微信端（PRD §4-11 不做 H5/App）；原生无构建链、包体最小、云开发 API 原生支持 | 放弃 Taro/uni-app（跨端能力本 MVP 用不上，多一层构建与调试成本）。代价：将来要跨端需重写 |
| 语言 | **全 JS + JSDoc 类型注释**；不引入 TS 构建步骤 | 云函数上传前不编译 TS，引入 TS 就得加 build 步骤（单人维护的额外风险点）；JSDoc 已能给编辑器提示 | 放弃编译期类型检查。**代价补偿**：`money.js`/`time.js`/`stat.js` 必须有单测（§12.3）。若团队熟悉 TS，客户端可改用官方 TS 模板，不影响本方案其余部分 |
| UI | **原生 WXML/WXSS + 自研 6 个基础组件 + 设计 token 落成 WXSS 变量** | 设计稿已给全套定制 token（颜色/字号/圆角/自绘键盘/等宽数字），组件库大部分用不上，反而要大量覆盖样式 | 放弃 TDesign / Vant Weapp。代价：弹窗/toast/骨架/键盘需自写（估 200~300 行） |
| 状态管理 | `app.globalData` + `store/index.js` + **页面 `onShow` 重拉** | 跨页共享的只有"分类表、user_key、server_date"三样；设计 §9.4-4 已要求三处同步靠重拉 | 放弃 MobX/Redux/小程序状态库。代价：重复请求（首屏两次调用，见 §10） |
| 后端 | **微信云开发云函数**（2 个） | 见 §3.1、ADR-2 | — |
| 数据库 | **云开发数据库**（文档型） | 与云函数同环境、免连接池、免运维 | 放弃 MySQL（见 §3.1 代价） |
| 鉴权 | `cloud.getWXContext().OPENID` | **免 `code2session`、免 AppSecret、免自建登录态**；杜绝密钥落代码（PRD §5.2 D2） | 放弃自建 JWT/session。代价：只能在微信生态内使用 |
| 分类数据 | 云函数**常量表** + 随 `user.init` 下发、客户端缓存 | 15 个预置分类是固定数据；放数据库要多一次查询与一套 CRUD，且 P1 自定义分类才需要集合 | 放弃 `categories` 集合（ADR-7）。代价：P1 需把常量表迁成集合 + 加一次迁移（数据量小，成本低） |
| 埋点 | **自建 `events` 集合 + `track` 云函数**（客户端批量上报） | 需自算"记账耗时中位数""页面转化"等自定义指标，且要能导出给 PM；官方自定义分析配置繁琐、聚合能力弱 | 放弃"只用官方自定义分析"。代价：多一个云函数 + 客户端一段队列逻辑（估 60 行） |
| 单测 | **Node 内置 `node:test` + `assert`**（零依赖） | 纯函数单测不需要 Jest 的 mock/快照/覆盖率设施；零依赖 = 云函数包体小、本地可离线跑 | 放弃 Jest。代价：无覆盖率报告（MVP 手工保证关键模块覆盖） |
| Lint | ESLint（本地，可选） | 低成本 | 放弃 CI（MVP 单人，不搭流水线） |
| 云函数依赖 | 仅 `wx-server-sdk`，**不引第三方库** | 依赖越少冷启动越快、包体越小 | 代价：schema 校验、日期处理都要手写（`common/validate.js`、`common/time.js`） |

---

## 4. 数据模型

> 通用约定：所有集合**权限设为「仅管理端可读写」**，客户端一律不直连数据库。`user_id` 一律 = 云函数取到的 `OPENID`，**绝不接受客户端传入**。时间戳字段统一存 `Date`（服务端写入）；业务日期字段统一存**字符串**（ADR-3）。

### 4.1 `users`（用户 + 沿用基准）

| 字段 | 类型 | 必填 | 规则 / 说明 |
| --- | --- | --- | --- |
| `_id` | string | 是 | **= openid**（天然唯一，免额外索引；该值永不返回客户端） |
| `created_at` | Date | 是 | 首次 `user.init` 写入 |
| `last_active_at` | Date | 是 | 仅在 `user.init` 时更新（不做每次请求更新，避免写热点）。**注意：该字段是"最近一次打开"，不是"活跃天数"**，不能用来算"使用 N 天"（§13-Q11） |
| `budget_baseline_cents` | int \| null | 是 | **沿用基准**= 用户最近一次**显式保存**的预算金额；`null` = 无基准（PRD §7.1、§13.1）。取消预算时置 `null` |
| `budget_baseline_set_at` | Date \| null | 否 | 便于排查"为什么沿用了这个数" |
| `schema_version` | int | 是 | 初始 1；P1 迁移时按此版本跑脚本 |
| `flags` | object | 否 | 预留开关（如 `p1_undo_enabled`），默认 `{}` |

**索引**：`_id` 天然唯一，无需额外索引。

> **为什么把沿用基准放在 `users` 而不是"查最近一条显式预算记录"**：PRD §7.1 规定「取消预算会清空沿用基准」。若从 `budgets` 推导，取消当前月后，**历史月份的显式记录仍在**，推导结果与"已清空"矛盾（E14 会失败）。因此基准必须是独立可清空的字段（ADR-9）。

### 4.2 `records`（账目）

| 字段 | 类型 | 必填 | 规则 / 说明 |
| --- | --- | --- | --- |
| `_id` | string | 是 | 自动生成 |
| `user_id` | string | 是 | 服务端注入的 openid |
| `client_record_id` | string | 是 | 客户端生成的 uuid（8~64 位），**幂等键** |
| `type` | string | 是 | `'expense'` \| `'income'`（枚举强校验） |
| `amount_cents` | int | 是 | **整数分**，1 ~ 99,999,999（PRD §7.2.1/§7.2.2）；服务端校验 `Number.isInteger` |
| `category_id` | string | 是 | 必须属于同 `type` 的常量表；未传或非法 → 服务端按 `type` 兜底（`expense_other` / `income_other`，见 §4.5）。**注意**：客户端一定会传，兜底只用于防御 |
| `date` | string | 是 | `'YYYY-MM-DD'`，正则 + 范围校验（下界 = 服务端「上月 1 日」，上界 = 服务端今天） |
| `month` | string | 是 | `'YYYY-MM'`，**服务端由 `date` 推导**，客户端传了也忽略（防脏数据） |
| `note` | string | 否 | ≤20 字符；MVP 客户端不发送、列表不展示；服务端仍做长度校验（防脏数据） |
| `created_at` | Date | 是 | 服务端写入 |
| `updated_at` | Date | 是 | 服务端写入 |
| `version` | int | 是 | 初始 1，每次更新 `+1`（乐观锁，E12）。**必须通过读接口下发**，否则客户端无法提交编辑（ADR-14） |

**索引（M0 在云开发控制台手动创建，附录 A 有核对清单）**

| # | 索引 | 唯一 | 用途 |
| --- | --- | --- | --- |
| I1 | `user_id`(asc) + `client_record_id`(asc) | **是** | 幂等去重（ADR-5） |
| I2 | `user_id`(asc) + `month`(asc) + `date`(desc) + `created_at`(desc) | 否 | 列表分页、按月聚合 |
| I3 | `user_id`(asc) + `date`(desc) + `created_at`(desc) | 否 | 首页最近 3 笔、最早账目月 |

### 4.3 `budgets`（月预算）

| 字段 | 类型 | 必填 | 规则 / 说明 |
| --- | --- | --- | --- |
| `_id` | string | 是 | 自动 |
| `user_id` | string | 是 | openid |
| `month` | string | 是 | `'YYYY-MM'`；**MVP 只允许写服务端当前月**（§7.3） |
| `amount_cents` | int | 是 | 1 ~ 99,999,999；**绝不写 0**（PRD D12、§7.2.2） |
| `source` | string | 是 | `'explicit'`（用户保存）\| `'inherited'`（服务端沿用写入） |
| `created_at` / `updated_at` | Date | 是 | 服务端 |

**索引**

| # | 索引 | 唯一 | 用途 |
| --- | --- | --- | --- |
| I4 | `user_id`(asc) + `month`(asc) | **是** | 一月一条 + 沿用写入幂等（R10 的答案） |

**「未设预算」的判定**（与 PRD §7.1 一致）：该 `user_id` + `month` **不存在记录**即未设置，**不用 0 表示**。

### 4.4 `events`（埋点，PRD §2.3）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_key` | string | `sha256(openid)` 前 16 位（**不存 openid 原文**，日志与埋点都只用化名） |
| `name` | string | 事件名，白名单（PRD §2.3 的 11 个事件） |
| `ts_client` | int | 客户端时间戳（仅供排查，不可信） |
| `server_ts` | Date | 服务端时间（**所有统计口径只用这个**） |
| `props` | object | 白名单字段，见 §5.5（金额字段为 `amount_bucket` 分桶，不存金额原文） |
| `app_version` | string | 便于分版本看数据 |

**索引**：`name`(asc) + `server_ts`(desc)；`user_key`(asc) + `server_ts`(desc)。
**保留期**：MVP 保留 180 天（估算存储量见 §10）；P1 加定时清理。**不存金额明细**，金额一律以分桶形式落入 `amount_bucket`（§5.5、§13-Q4）。

### 4.5 `categories`（MVP 不建集合）

MVP 分类是云函数常量表 `common/categories.js`（15 条，顺序即展示顺序，PRD §7.1），随 `user.init` 下发一次，客户端缓存到 `storage` 并内置一份兜底常量（云端失败也能渲染记账页）。

```js
// common/categories.js —— 接口（字段名与客户端约定，不可随意改）
// 关键：收入与支出的「其他」必须用不同的 id，否则 isValidCategory(id, type) 无法区分
//       展示名都为「其他」，只是 id 不同
EXPENSE = [
  {id:'food',          name:'餐饮', icon:'🍚', sort_order:1},
  {id:'transport',     name:'交通', icon:'🚌', sort_order:2},
  {id:'shopping',      name:'购物', icon:'🛍', sort_order:3},
  {id:'study',         name:'学习', icon:'📚', sort_order:4},
  {id:'entertainment', name:'娱乐', icon:'🎮', sort_order:5},
  {id:'communication', name:'通讯', icon:'📱', sort_order:6},
  {id:'housing',       name:'居住', icon:'🏠', sort_order:7},
  {id:'medical',       name:'医疗', icon:'💊', sort_order:8},
  {id:'gift',          name:'人情', icon:'🎁', sort_order:9},
  {id:'expense_other', name:'其他', icon:'⋯', sort_order:10}
]                                                                     // 10 条

INCOME = [
  {id:'allowance',     name:'生活费', icon:'💰', sort_order:1},
  {id:'part_time',     name:'兼职',   icon:'💼', sort_order:2},
  {id:'scholarship',   name:'奖学金', icon:'🎓', sort_order:3},
  {id:'refund',        name:'退款',   icon:'↩', sort_order:4},
  {id:'income_other',  name:'其他',   icon:'⋯', sort_order:5}
]                                                                     // 5 条

DEFAULT_CATEGORY = { expense:'food', income:'allowance' }   // PRD D3 固定默认值
FALLBACK_CATEGORY = { expense:'expense_other', income:'income_other' }  // 兜底（按 type 取）

getCategory(id, type)      -> {id,name,icon} | null
isValidCategory(id, type)  -> bool        // 无歧义：id 本身已绑定 type
```

> **取舍（ADR-17）**：选择**收支各自独立的 id**，而不是共用一个 `'other'`。因为 `category_id` 与 `type` 要互相校验，共用一个 id 会让 `isValidCategory('other','income')` 这类判断出现二义，也让统计聚合的分类归属需要额外判断 `type`。代价：`id` 与展示名不再一一对应（有 2 个分类都叫「其他」），排查数据时需记住这一点；`expense_other` 与 `income_other` 在 `stat.monthly` 里仍是两条独立分类（本来也不该合并）。

### 4.6 P1 数据演进（现在就写清楚，避免将来越改越乱）

| P1 需求 | 数据影响 | 迁移策略（**避免全量迁移**） |
| --- | --- | --- |
| US-20 删除撤销 | 需要软删除 | 新增 `deleted_at` 字段；**语义定为「字段不存在或为 null = 未删除」**，因此**不需要回填历史数据**（ADR-11）；再加定时清理任务；所有查询加默认过滤 |
| US-08 自定义分类 | 需要 `categories` 集合 | 常量表迁为集合（`user_id='preset'` 存预置 + 用户自定义）；预置数据由脚本导入；`records.category_id` 不变，**无需改动历史账目**（`expense_other`/`income_other` 作为预置 id 原样保留） |
| US-21 最近使用分类 | 需要按频次统计 | 可在 `stat` 聚合里带一个"近 30 天分类频次"查询，或加 `user_category_stats` 缓存集合 |
| US-07 备注 | 字段已预留 | 无需迁移，直接开始写入；列表行高已预留 136rpx（设计 §12.2） |
| P1 需"活跃天数/使用 N 天" | 需要日活记录 | 新增 `users.active_dates`（按月去重数组）或 `user_daily_active` 集合；MVP 不做（§13-Q11） |

---

## 5. 接口设计

### 5.1 统一调用与控制约定

```js
// 客户端 utils/request.js —— api 与 track 共用同一封装（v0.2 统一，见 §5.4）
call(name, action, payload, { fireAndForget }) -> Promise<data>
// 内部：wx.cloud.callFunction({ name, data: { action, payload, request_id, app_version } })
// 客户端超时 10s；读接口失败自动重试 1 次（幂等），写接口不自动重试（由用户点，client_record_id 复用 → 重试仍幂等）
// fireAndForget=true 时（埋点用）：不 await、不重试、失败静默丢弃
```

**统一响应包**（所有 action 一致，**`api` 与 `track` 都用这个包**）

```json
{ "code": 0, "msg": "ok", "data": { }, "request_id": "uuid" }
{ "code": 1004, "msg": "账目日期超出可选范围", "data": null, "request_id": "uuid" }
```

**错误码表**（客户端 `constants.js` 与云函数 `common/errors.js` **共用同一份**，不许各写一套）

| code | 常量 | 含义 | 客户端处理（对齐设计稿文案） |
| --- | --- | --- | --- |
| 0 | `OK` | 成功 | — |
| 1001 | `PARAM_INVALID` | 缺参/类型错/枚举非法/月份非法（含 `budget.*` 传非当前月、`record.list` 传未来月） | 提示「网络开小差了，请重试」+ 上报埋点（这是理论上不该出现的错） |
| 1002 | `AUTH_FAILED` | 取不到 `OPENID` | 「网络开小差了，请重试」；**不得把账目标记为已保存**（PRD US-01） |
| 1003 | `AMOUNT_OUT_OF_RANGE` | 金额非整数分或超出 1~99,999,999 | 用 PRD §7.2.1/§7.2.2 文案 |
| 1004 | `DATE_OUT_OF_RANGE` | 日期格式错或超出「上月 1 日 ~ 今天」 | 提示 + 日期复位为今天 |
| 1005 | `NOT_FOUND` | 账目不存在、不属于本人、或已被删除 | 静默重拉列表（编辑时遇到 = 该账目已被另一设备删除，§5.3.5） |
| 1006 | `VERSION_CONFLICT` | 乐观锁冲突（E12） | 「数据已更新，请返回重试」+ 重拉 |
| 2001 | `INTERNAL` | 未预期异常 | 「网络开小差了，请重试」+ 上报 `err_msg`（日志不含金额/备注） |

**权限**：每个 action 入口第一件事取 `cloud.getWXContext().OPENID`；取不到直接 `1002`。所有 DB 查询条件**必须**包含 `user_id`（代码评审检查项，防止越权读别人的数据）。

**幂等约定**：写接口重复提交**一律返回 `code 0`**，并在 `data.duplicated = true` 标记——不把"重复提交"当错误（PRD E6 只要求最终 1 条）。

### 5.2 `api` 云函数的 action 路由表（**共 11 个 action**）

| # | action | 覆盖需求 | 读/写 | 幂等键 / 冲突策略 |
| --- | --- | --- | --- | --- |
| 1 | `user.init` | US-01 | 首次写（建用户） | 自然幂等（upsert by `_id`） |
| 2 | `user.clearMyData` | R3（待 CEO 定） | 写 | 删除天然幂等 |
| 3 | `home.summary` | US-04、首页全部数据 | **读 + 当前月可能写**（沿用写入） | `ensureMonthBudget` 幂等 |
| 4 | `record.create` | US-02、US-03 | 写 | `client_record_id`（唯一索引 I1） |
| 5 | `record.update` | US-05 | 写 | `version` 乐观锁（1006） |
| 6 | `record.delete` | US-05 | 写 | **last-write-wins**，删不存在返回 `deleted:0` |
| 7 | `record.list` | US-05 账单页 | 读 | — |
| 8 | `budget.get` | US-04 预算页回填 | **读 + 当前月可能写**（沿用写入） | `ensureMonthBudget` 幂等；**仅当前月** |
| 9 | `budget.set` | US-04 | 写 | upsert（唯一索引 I4） |
| 10 | `budget.cancel` | US-04 | 写 | 删除天然幂等 |
| 11 | `stat.monthly` | US-06 | 读 | — |

> **计数说明（v0.2 修正）**：v0.1 此表标题写「9 个」但表内实际 10 行、且漏登记了 §5.3.7 已定义的 `budget.get`。本版**逐行核对为 11 个**（`budget.get` 独立成行），并要求附录 C 与实现的 `handlers` 路由 switch 与此表一一对应——**测试按此表做接口覆盖，表与代码必须同源**。
>
> **条件性 action（不计入上表 11 个）**：`user.profile`（`{records_count, joined_days, data_policy_version}`）仅在 PM 要求「我的」页显示「已记录 X 笔 · 加入 N 天」时才实现（§13-Q11）。**若采纳，本表计数变 12，必须先改本文档再动代码。**

### 5.3 接口详细规格

#### 5.3.1 `user.init`

```
入参：{}   （不接收任何用户标识；OPENID 由云函数上下文提供）
出参：
{
  is_new_user: boolean,
  user_key: string,              // sha256(openid) 前 16 位，供埋点/排查关联，不含 openid 原文
  server_date: 'YYYY-MM-DD',     // 服务端 UTC+8 今天（客户端所有日期锚点）
  server_month: 'YYYY-MM',
  categories: { expense: [...], income: [...] },
  default_category: { expense: 'food', income: 'allowance' },
  fallback_category: { expense: 'expense_other', income: 'income_other' },
  data_policy_version: 'v1'      // 隐私说明版本号，便于将来提示用户
}
错误：1002 1001 2001
```

行为：`users` 文档不存在则创建（`_id = OPENID`、`schema_version=1`、`budget_baseline_cents=null`）；存在则读。**不返回 openid 原文**。

> **v0.2 修订**：**删除了 v0.1 的 `has_budget_this_month` 字段**。原因：① 它在 `user.init` 时取的是"沿用写入之前"的值，而首屏引导态要靠 `home.summary`（它会触发沿用写入）决定，两个来源必然打架、首屏会先闪一下引导态；② 它白搭一次预算查询（首屏路径上不该有的开销）。**结论：首屏是否显示"设置本月预算"引导态，只由 `home.summary.budget` 是否为 `null` 决定**，客户端不许用其他来源判断（§6.1）。

#### 5.3.2 `home.summary`（首页一次调用拿齐）

```
入参：{}
出参：
{
  server_date, server_month, remaining_days,      // 剩余天数含今天，>=1（PRD §7.3-4）
  budget: { amount_cents, source, updated_at } | null,
  expense_cents, income_cents,
  remaining_cents: number | null,                 // null = 未设预算（绝不发 0 充数，PRD D9）
  daily_available_cents: number | null,           // null = 未设预算；超支时为 0
  overspent: boolean,                             // = remaining_cents < 0
  // ≤3 条，含收入；**v0.2 起必须返回 version**，供"最近记录点进编辑"直接提交（ADR-14）
  recent_records: [ {id,type,amount_cents,category_id,date,created_at,version} ]
}
错误：1002 2001
```

服务端步骤（顺序不可换）：

1. `server_date = todayStr()`；`month = monthStr(server_date)`
2. `budget = ensureMonthBudget(userId, month)` ← 沿用写入的唯一入口（§7.5）
3. `totals = monthTotals(userId, month)`（`income` 参与收入合计，**不参与剩余可用**，PRD D5）
4. `remaining = budget ? budget.amount_cents - totals.expense_cents : null`
5. `daily = remaining === null ? null : (remaining <= 0 ? 0 : divideRound(remaining, remainingDays(month)))`
6. `recent_records`：按 `date desc, created_at desc, _id desc` 取 3 条（含收入，PRD §7.4-3），**并带上 `version`**

> **关于 `version` 与设计稿 Q6**：设计 §5.1.3 建议首页最近记录可点进编辑（待 PM 确认）。本方案**无论 Q6 结论如何都下发 `version`**：多一个 int 的成本可忽略，而"点了能改却在保存时报缺参"是最糟的失败。若 PM 最终定为**只读**，客户端忽略该字段即可，接口无需回退。
> **注意**：`recent_records` **不下发 `note`**（MVP 不展示备注）。因此 `record.update` 必须支持"未传字段不修改"（§5.3.4），否则从首页进入编辑保存会把备注清空。

#### 5.3.3 `record.create`

```
入参：
{
  client_record_id: string(8~64),      // 必填，客户端 uuid
  type: 'expense'|'income',
  amount_cents: int,                   // 1 ~ 99,999,999
  category_id: string,                 // 必须属于同 type
  date: 'YYYY-MM-DD',                  // 上月1日 <= date <= 服务端今天
  note?: string                        // ≤20 字，MVP 客户端不传
}
出参：{ record: {id,type,amount_cents,category_id,date,note,created_at,updated_at,version}, duplicated: bool }
错误：1001 1002 1003 1004 2001
```

幂等实现（顺序即代码顺序，E6）：

```
1) 命中回查：records.where({user_id, client_record_id}).limit(1).get()
   → 命中：直接 return { record: 已有记录, duplicated: true }
2) 校验并 insert（month 由 date 推导，version=1）
3) 若 insert 报唯一索引冲突（duplicate key）：
   → 回查第 1 步再拿一次并返回 duplicated: true
   （这是并发下的正确路径，不是错误）
```

#### 5.3.4 `record.update`

```
入参：{ id, version, amount_cents?, category_id?, date?, note? }   // version 必填
出参：{ record }        // record 含最新 version，客户端可直接再次编辑
错误：1001 1002 1003 1004 1005 1006 2001
```

* **`version` 的来源（v0.2 明确，回答审查意见 1）**：来自 `record.list` 的 `items[].version`（§5.3.6）或 `home.summary.recent_records[].version`（§5.3.2）。编辑页**不需要额外请求**：列表行点击时把整条数据（含 `version`）带进编辑页。
* **未传的字段一律不修改**（含 `note`）：服务端只用入参里出现的字段拼 `data`。这条是硬规则，因为首页最近记录不下发 `note`；若不遵守，从首页进编辑保存会把备注清空。
* `amount_cents` / `category_id` / `date` 与 `type` 的校验与 `record.create` 完全一致（同一份 `common/validate.js` 规则）。**MVP 不允许改 `type`**（分段控件在编辑页置灰或改类型时报 1001——建议置灰，避免"改类型后分类不匹配"的组合；待 PM/设计确认，见 §13-Q12）。
* 用**条件更新**做乐观锁：`where({_id, user_id, version}).update({ data: { ..., version: _.inc(1), updated_at } })`；`updated === 0` → 再查一次是否存在：存在 → `1006`（E12），不存在 → `1005`。
* 日期校验（承接设计稿 §11-Q3 的建议）：`date` 若与原值相同 → **允许保留**（避免"昨天记的账今天不能保存"）；若与原来不同 → 必须满足 `>= 上月1日 && <= 今天`。
* `month` 随 `date` 重新推导（E7：本月改上月 → 两次统计都会变）。

#### 5.3.5 `record.delete`

```
入参：{ id }          // v0.2：去掉了可选的 version —— 见下方取舍
出参：{ deleted: 0|1 }
错误：1001 1002 2001
```

* MVP **物理删除**（PRD D8）：`where({_id, user_id}).remove()`。
* 删不存在（或不属于本人、或已被另一设备删除）→ `deleted: 0` 且 `code 0`（幂等友好，弱网重试不报错）。
* **冲突策略 = last-write-wins（ADR-15）**：删除**不做乐观锁、不弹冲突提示**。与 PRD E12 / 设计 §5.6 的关系写清楚：
  * 「一台改、一台删」：**先删者赢**；改的那台提交时记录已不存在 → 服务端返回 `1005` → 客户端「静默重拉列表」（§5.1 错误码表已定义），用户看到的最新状态就是"已被删除"，符合 E12「以服务端最新数据为准，不做协同编辑」。
  * 设计 §5.6 的「数据已更新，请返回重试」文案**只用于"编辑保存"**这一路径（1006）；删除路径不出现该文案。
  * 「两台同时删」：第一次 `deleted:1`，第二次 `deleted:0`，都返回 `code 0`。
  * **代价**：删除是不可撤销的（MVP 无撤销），若用户 A 删了、用户 B 正在编辑，B 会看到"记录不存在"而不是"你的修改与删除冲突"。可接受（MVP 只有单人使用语义）；**P1 做软删除 + 撤销时，必须重新评估这条**（ADR-15 已标注复评点）。
* 为什么去掉 `version` 参数：入参里留一个"可选但不生效"的字段，会让测试按"支持冲突检测"写用例、也会让客户端误以为能拿到 1006。**要么做（必填）、要么不做（不出现）**——本 MVP 选"不做"（ADR-15）。

#### 5.3.6 `record.list`（账单页）

```
入参：{ month: 'YYYY-MM', page: int >= 1, page_size: int }   // page_size 上限 50；page 无上限（v0.2）
出参：
{
  items: [ {id,type,amount_cents,category_id,date,note,created_at,version} ],  // v0.2：items 新增 version
  page, page_size, has_more,
  total_count: int,                 // v0.2 新增：该月账目总条数（含收入），分页与 UI 尾注的依据
  month_totals: { expense_cents, income_cents },
  day_totals: [ { date, expense_cents, income_cents } ],   // 当月所有有账目的日期
  server_month: 'YYYY-MM',          // v0.2 新增：右箭头置灰依据（客户端不信任本地时间）
  earliest_month: 'YYYY-MM',        // 仅 page=1 返回；无任何账目时 = server_month（见下）
  can_go_next: boolean              // = month < server_month，用于右箭头置灰（E16）
}
错误：1001 1002 2001
```

* `month > 服务端当前月` → `1001`（客户端本来就不该请求未来月，右箭头已置灰）。
* 排序：`date desc, created_at desc, _id desc`（加 `_id` 保证分页**稳定不重不漏**）。
* `day_totals` 与 `month_totals`：一次聚合管道拿到（§7.4），与分页解耦，**避免"日期组被分页切断导致组头合计不准"**这个隐蔽 bug。
* **分页语义（v0.2 重写，回答审查意见 3）**：
  * `page` 从 1 开始，**没有上限**（v0.1 的 `page ≤ 50` 已删除——它会让单月 >1000 条时第 51 页起静默消失，且与"组头/月合计按全月聚合"自相矛盾）。
  * `page_size` 上限 50，客户端默认 20。
  * `has_more = page * page_size < total_count`；`total_count` 用 `count()` 或与聚合管道同批拿到（一次查询，见 §7.4）。
  * `page` 越界（该页起始位置 ≥ `total_count` 且 `page > 1`）→ 返回 `items: []`、`has_more: false`、**`code 0`**（不报错，幂等友好，避免客户端竞态重拉时报错）。
  * 客户端逻辑：上滑 → `page += 1`，直到 `has_more === false`，此时列表底部显示设计稿 N3 文案「本月就这些了」，**不存在"剩余账目用户看不到"的状态**。
* **`earliest_month` 的定义（v0.2 补，回答审查意见 ⑦）**：
  * = 该用户**最早一笔账目所在月**（`records` 按 I3 取 `date` 最小的一条，取 `date.slice(0,7)`）。
  * **用户完全没有账目时 = `server_month`**（PRD §7.4-1「无账目时为当前月」、设计 §5.3.2「无账目时左箭头直接置灰」、E16）。
  * 客户端左箭头置灰规则：`month > earliest_month` 才可点，`month === earliest_month` 置灰。因无账目时两者相等，**天然置灰，不需要额外分支**。
  * 只在 `page === 1` 返回（翻页时不会变）；切月时客户端重新请求 `page=1` 拿到新值。

#### 5.3.7 `budget.get` / `budget.set` / `budget.cancel`

```
budget.get    入参 {month?}（缺省 = 服务端当前月；**month 必须 == 服务端当前月，否则 1001**）
              出参 {month, budget: {amount_cents, source, updated_at} | null,
                    baseline_cents: int|null, server_month}
              读/写属性：读；但**当前月**会调用 ensureMonthBudget（可能写归一条沿用预算）→ 幂等键同 home.summary
              错误：1001 1002 2001
budget.set    入参 {month, amount_cents}      // month 必须 == 服务端当前月
              出参 {budget: {...source:'explicit'}}
budget.cancel 入参 {month}                    // 必须 == 服务端当前月
              出参 {canceled: 0|1, baseline_cleared: true}
```

* **`budget.get` 的月份口径（v0.2 明确，回答审查意见 2）**：历史月与未来月**一律 `1001`**，与 `budget.set`/`budget.cancel` 完全一致。
  * 理由（ADR-16）：MVP 预算只有一个语义 ——「本月预算」（PRD §9.1 页面 5 只有一个预算页，没有历史预算回顾）。若 `budget.get` 允许历史月，就会出现"能读但不能写"的半开放接口，且客户端没有任何页面消费它 → 无收益的接口面。
  * **代价**：P1 若做"历史月份预算回顾"（不在 PRD 需求表内），需放开该校验并拆出只读分支（改动只在一个 handler，估 0.2 天）。
  * 读路径会触发沿用写入：与 `home.summary` 同源（`ensureMonthBudget`），保证预算页预填值与首页一致（设计 §5.5.3 边界④「沿用写入后首次进入本页输入框预填沿用金额」）。
* `amount_cents` 校验：整数、`1 <= x <= 99,999,999`；**0 一律 `1003`**（PRD §7.2.2、E13，绝不把 0 当取消）。
* `budget.set`：`upsert`（`where({user_id, month})` 更新，未命中则插入；唯一索引 I4 兜底，冲突则重试一次更新），成功后写 `users.budget_baseline_cents = amount_cents`（**沿用基准 = 最近一次显式保存**）。
* `budget.cancel`：删该月记录 + `users.budget_baseline_cents = null`（E14：之后跨月不再沿用）。**取消不存在的预算** → `canceled: 0` + `code 0`（幂等）。
* **只允许当前月**：MVP 预算只有"本月预算"一个语义（PRD §9.1 页面 5），历史/未来月直接 `1001`，防止把预算写进历史月份污染口径。

#### 5.3.8 `stat.monthly`（统计页）

```
入参：{month?}（默认当前月；MVP 页面固定当前月，参数为 P1 预留）
出参：
{
  month,
  expense_cents, income_cents, has_data: boolean,     // has_data = expense_cents > 0
  categories: [ { category_id, amount_cents } ]       // 仅支出，按 amount_cents desc，同额按分类表顺序
}
```

* **不返回百分比**：分母与格式化都由客户端 `format.js` 处理（服务端不做展示逻辑；同一套格式化代码只存在一处）。
* `expense_cents === 0` → `categories: []`、`has_data: false` → 客户端渲染空态，**不出现 NaN/除零**（PRD US-06）。
* 归月只按 `date`（PRD §7.3-7）。
* `category_id` 用 §4.5 的独立 id，因此收入「其他」与支出「其他」不会混在一条里。

#### 5.3.9 `user.clearMyData`（仅当 CEO 采纳 R3）

```
入参：{ confirm: true }      // 必须显式为 true，缺省直接 1001（防误调）
出参：{ deleted_records, deleted_budgets, partial: bool }
```

行为：循环删除该用户 `records`（每轮 `limit(1000).remove()`，直到删完或接近云函数超时时间）；删 `budgets`；重置 `budget_baseline_cents = null`。若未删完返回 `partial: true`，客户端提示"清理未完成，请再次点击"。**不做"注销账号"**（不在 MVP 范围）。

### 5.4 `track` 云函数

```
入参（与 §5.1 完全一致的封装，v0.2 统一）：
{
  action: 'track.report',
  payload: { events: [ { name, ts_client, props } ] },   // 一批 ≤20 条
  request_id, app_version
}
出参（标准响应包）：{ code: 0, msg: 'ok', data: { accepted: n, dropped: m }, request_id }
```

* **为什么统一成同一个包（回答审查意见 ⑤）**：客户端可以复用同一个 `utils/request.js`（超时、请求序号、错误码映射、`request_id` 全都不用写第二套）；测试也可以只按一种协议写用例。`track` 的调用方式：`request.call('track', 'track.report', {events}, {fireAndForget:true})`。**代价**：埋点也要带 `action`/`payload` 这层包装（多几十字节），可忽略。
* `name` 必须在白名单（PRD §2.3 的 11 个事件），否则丢弃并计入 `dropped`。
* `props` 按事件白名单校验字段名与长度；越界字段直接丢弃（不报错，埋点绝不能影响主流程）。**金额一律先由客户端转成 `amount_bucket`**（§5.5），服务端只接受桶名，不接收金额数字（即使客户端传了 `amount` 也丢弃）。
* 服务端补 `user_key`、`server_ts`、`app_version`；**不接收也不存 openid、备注、金额原文**。
* 客户端策略（关键）：事件先入内存队列；触发 `flush` 的时机 = 满 10 条 / 每 30 秒 / `onHide`；**fire-and-forget**（不 await、失败即丢弃、最多保留 50 条）；埋点写库失败**绝不能**让保存账目失败。
* 不做服务端限流（MVP）：风险记录见 §13-R7。

### 5.5 埋点字段白名单与金额分桶（与 PRD §2.3 对齐）

**金额分桶（v0.2 定稿，回答审查意见 ⑧）**：PRD §2.3 的 `amount` 字段在埋点里**统一上报为 `amount_bucket`**，不报金额原文。

| 桶名 | 含义（元，**左开右闭**：`(下界, 上界]`） | 覆盖的分值 |
| --- | --- | --- |
| `b0_1` | ≤ ¥1.00 | 1 ~ 100 分 |
| `b1_10` | (¥1.00, ¥10.00] | 101 ~ 1000 分 |
| `b10_50` | (¥10.00, ¥50.00] | 1001 ~ 5000 分 |
| `b50_100` | (¥50.00, ¥100.00] | 5001 ~ 10000 分 |
| `b100_plus` | > ¥100.00 | 10001 分及以上 |

* 边界规则唯一且可测：**恰好 ¥10.00 → `b1_10`**；**恰好 ¥50.00 → `b10_50`**；**恰好 ¥100.00 → `b50_100`**（由 `common/money.js` 的 `bucketOfAmount(cents)` 单一实现，客户端不自己算）。
* 为什么默认分桶而不等 PM 确认：**test-plan 要用到"记账金额分布"类用例口径**，等确认会阻塞测试；且分桶本身也是隐私更小的方案（PRD R2 方向一致）。若 PM 否决，需要改的是 `events-schema.js` 一个函数 + 测试用例口径（§13-Q4）。

| 事件 | 允许字段 |
| --- | --- |
| `app_open` | `scene`, `is_new_user` |
| `login_silent_fail` | `err_code`, `err_msg`（截断 200 字） |
| `record_page_open` | `entry`(home/list/stat), `type` |
| `record_save_success` | `type`, `category_id`, `amount_bucket`, `date`, `duration_ms` |
| `record_save_fail` | `reason`(network/validate/server) |
| `budget_set` | `month`, `amount_bucket`, `is_first_time`, `is_inherited` |
| `budget_cancel` | `month`, `had_amount` |
| `list_month_switch` | `from_month`, `to_month` |
| `record_edit` / `record_delete` | `record_id`（可用 `_id` 本身，不含金额）, `field_changed` |
| `stat_view` | `month`, `has_data` |

> `record_save_success` 的触发时机：**收到 `code 0` 响应之后**（不是点击时），否则 `duration_ms` 中位数不可信（设计稿 §9.5 已明确）。

### 5.6 `common/*` 内部接口（模块之间的约定，先冻结再写码）

```js
/* common/time.js —— 全系统唯一的"今天"来源 */
nowShanghai(): Date                     // new Date(Date.now() + 8*3600*1000)，只许用 getUTC* 读取
todayStr(): 'YYYY-MM-DD'
monthStr(dateStr): 'YYYY-MM'
monthStartStr(month): 'YYYY-MM-DD'
lastDayOfMonth(month): number           // 1~31，覆盖闰年 2 月
remainingDays(month): number            // 该月最后一天 - 今天 + 1；month != 当前月时返回 0
dateLowerBound(): 'YYYY-MM-DD'          // 上月 1 日（PRD D7 下界）
isValidDateStr(s): boolean
compareDate(a, b): -1|0|1               // 字符串比较，禁止 new Date('YYYY-MM-DD')

/* common/money.js */
isValidCents(x, min = 1, max = 99999999): boolean   // 整数 + 范围
divideRoundCents(numerator, denominator): number    // denominator>0，Math.round，用于日均
bucketOfAmount(cents): 'b0_1'|'b1_10'|'b10_50'|'b50_100'|'b100_plus'   // §5.5 唯一实现

/* common/validate.js */
validate(schema, payload) -> { ok, errors: [{field, rule}], value }

/* common/errors.js */
AppError(code, msg) / ok(data) / fail(code, msg)    // 统一响应包的唯一构造入口

/* common/budget.js */
ensureMonthBudget(userId, month) -> budget|null     // 沿用写入唯一入口（§7.5）；month != 当前月直接返回 null
getBudget(userId, month) -> budget|null

/* common/stat.js */
monthTotals(userId, month) -> { expense_cents, income_cents }
dayTotals(userId, month)   -> [{ date, expense_cents, income_cents }]
categoryTotals(userId, month) -> [{ category_id, amount_cents }]
monthRecordCount(userId, month) -> number           // 供 record.list 的 total_count

/* common/record.js */
createRecord(userId, input) -> { record, duplicated }
updateRecord(userId, input) -> record                // input.version 必填；未出现字段不改
deleteRecord(userId, id) -> { deleted }              // last-write-wins，无 version
listRecords(userId, {month, page, page_size, serverMonth}) ->
  { items,                       // items 每项含 version（供编辑页直接提交，ADR-14）
    has_more,                    // = page*page_size < total_count
    total_count,
    month_totals, day_totals,
    earliest_month,              // 无账目时 = serverMonth（§5.3.6）
    can_go_next, server_month }
```

---

## 6. 关键流程时序说明

### 6.1 首次打开（US-01，目标 1.5s 可交互）

```
客户端                        api 云函数                    云数据库
  │  onLaunch                    │                             │
  │──┬ user.init ───────────────►│                             │
  │  │                           │ getWXContext().OPENID       │
  │  │                           │─ users._id 查不到 → insert ─►│
  │  │                           │◄─ users 文档 ───────────────│
  │  │◄─ categories + server_date│   （**不再返回预算状态**，v0.2）
  │  └─ home.summary ───────────►│
  │                              │ ensureMonthBudget(当前月)    │  ← 新用户无基准，不写任何东西
  │                              │─ 聚合当月 expense/income ──►│
  │                              │─ 最近 3 笔（含 version）───►│
  │◄─ budget/合计/剩余可用/日均/最近3笔 ──────────────────────│
  │  渲染：**只按 home.summary.budget 判断**：null → 卡片 A 引导态；无账目 → 卡片 D 空态
  │  失败分支：任一调用失败 → 卡片级错误态 + 重试（TabBar 仍可用，PRD §9.4）
```

`user.init` 与 `home.summary` **并行发起**（`Promise.all`），用分类本地缓存兜住"init 慢"。分类表缓存命中时首页其实只等 1 次调用。
**首屏引导态的判定规则（v0.2 明确）**：只以 `home.summary.budget === null` 为准；`user.init` 不返回任何预算信息，因此不存在"先闪引导态"的问题。

### 6.2 保存一笔支出（US-02 / E6 幂等）

```
记账页(本地)               api: record.create             云数据库
  │ 用户输入 18.5 → amountStr='18.5'（字符串，不 parseFloat）
  │ 点保存：按钮立刻 disabled + loading
  │ client_record_id = uuid()  ← 本次会话生成一次，重试复用同一个
  │── create ──────────────────►│
  │                             │ 校验(type/category/amount/date)
  │                             │ month = date.slice(0,7)
  │                             │─ 查 (user_id, client_record_id) ─►│
  │                             │◄─ 未命中 ─────────────────────────│
  │                             │─ insert(version=1) ──────────────►│  ← 唯一索引 I1 兜底
  │                             │  （若冲突：回查 → duplicated=true）
  │◄─ {code:0, record, duplicated} ────────────────────────────────│
  │ navigateBack + toast「已记 ¥18.50 · 餐饮」 + 埋点 record_save_success
  │ 首页 onShow 重拉 home.summary（三处同步：首页/账单/统计都靠 onShow）
  │ 失败：toast「网络开小差了，请重试」，保留输入，不写任何本地缓存（PRD §4-13、设计 §5.2.5⑦）
```

### 6.3 预算沿用写入（PRD §7.1、E15、回答 R10）

```
任意读路径（home.summary / budget.get，两者都只处理当前月）
  │
  ├─ ensureMonthBudget(userId, month):
  │     1. month !== server_month ？ → 直接返回 null（**不写历史月、不写未来月**）
  │     2. 查 budgets(user_id, month) → 命中则返回（带上 source 供前端判断是否显示沿用提示）
  │     3. 未命中 且 users.budget_baseline_cents != null
  │          → insert({ user_id, month, amount_cents: baseline, source:'inherited' })
  │          → 唯一索引 I4 冲突（并发）→ 回查并返回已存在记录（幂等，不报错）
  │     4. 未命中 且 无基准（新用户 / 已取消）→ 返回 null（首页走引导态）
  │
  └─ 结果：E15 得到满足——跨过 4、6 月直接到 7 月，只为 7 月写一条；
           并发 5 次调用最终只有 1 条 budget 记录。
```

### 6.4 设置 / 取消预算（US-04、E13、E14）

```
设置：预算页输入 1500（系统键盘）→ 本地校验 → budget.set{month=server_month, amount_cents:150000}
      → upsert budgets + users.budget_baseline_cents = 150000
      → 返回首页（onShow 重拉）→ 剩余可用/日均重算、沿用提示条消失
取消：点「取消预算」→ 模态确认（PRD §9.3 文案逐字）→ budget.cancel{month}
      → 删 budgets 记录 + baseline = null → 首页回引导态；次月不再沿用
      0 或空输入 → 本地置灰 + 提示「请输入大于 0 的预算金额」，不发请求（服务端亦会拒 1003）
```

### 6.5 账单列表 / 切换月份（US-05、E16、E9）

```
客户端                           api: record.list
  │ 进入账单 Tab（默认当前月）        │
  │── list{month, page:1} ────────►│ 校验 month 格式与"不得晚于当前月"
  │                                │ 聚合：day_totals + month_totals；count → total_count
  │                                │ 查询：earliest_month（page=1 时）
  │◄─ items(20, 含 version) + total_count + day_totals + earliest_month + server_month + can_go_next
  │ 渲染：日期组头用 day_totals（分页不切断组头合计）
  │ 切月：清空旧列表进骨架 + 请求序号 +1 → 过期响应按序号丢弃（防止旧月数据渲染到新月）
  │ 上滑：page += 1，**cap 到 has_more=false 为止**（v0.2：无 page 上限）
  │       └─ has_more=false 时底部显示「本月就这些了」（设计 N3），列表不会静默截断
  │ 左/右箭头：month > earliest_month 才可点左；month < server_month 才可点右（E16）
```

### 6.6 统计聚合（US-06）

```
stat 页 onShow → stat.monthly{month: server_month}
  → 聚合管道：$match{user_id, month, type:'expense'} → $group{_id:'$category_id', sum:'$amount_cents'} → $sort{sum:-1}
  → expense_cents / income_cents 由同一个 monthTotals 出（与首页同源，保证三处一致）
  → 客户端 formatPercent(amount, expense_cents)：total=0 时渲染空态（不除零）
```

### 6.7 编辑 / 删除 + 三处同步（US-05、E7、E8、E12）

```
编辑：列表行/首页最近记录点击 → 把整条数据（**含 version**）带进编辑页
      → record.update{id, version, ...改动字段} → 条件更新 version=_.inc(1)
      冲突(updated=0)：记录已不存在 → 1005 → 静默重拉列表（另一设备删掉了）
                      记录存在但 version 旧 → 1006「数据已更新，请返回重试」→ 客户端重拉
      成功 → 返回列表 → 列表/首页/统计 onShow 各自重拉（PRD §7.3-8）
      ※ 只提交用户改动过的字段；note 未改动则不传（首页最近记录不下发 note，§5.3.4）
删除：左滑/编辑页「删除」→ 二次确认（含金额与分类）→ record.delete{id}（物理删除，无 version，last-write-wins）
      删完该月最后一笔 → 列表/统计落空态；首页本月支出 ¥0.00、剩余可用 = 预算（E8）
      两设备并发删 → 先删者 deleted:1，后到者 deleted:0（都 code 0）
```

### 6.8 埋点上报（与业务完全解耦）

```
页面事件 → store 内内存队列 push（金额先经 bucketOfAmount 转成 amount_bucket）
  ├ 满 10 条 / 每 30s / onHide → request.call('track','track.report',{events},{fireAndForget:true})
  └ 失败/超时 → 丢弃（不落盘、不重试、不影响任何业务状态）
```

---

## 7. 关键难点与对策

### 7.1 金额精度（PRD D11、E1、E2、E5）

| 风险 | 对策 |
| --- | --- |
| `parseFloat('18.5')*100 = 1849.999…` | 客户端**不解析浮点**：自绘键盘维护 `amountStr` 字符串，提交时用整数算法转分：`intPart*100 + (frac.length===1 ? frac*10 : frac)` |
| 浮点参与统计/预算计算 | 服务端**只做整数分加减与 `Math.round` 一次除法**（日均）；`divideRoundCents` 是唯一除法出口。值域 <1e8，双精度整数运算精确（依据：JS Number 安全整数上限 2^53≈9e15） |
| 展示与输入模型不一致 | 展示统一 `¥ + 千分位 + 2 位小数`（客户端 `format.js` 唯一实现）；输入中间态允许 `18.`（设计 §5.2.6） |
| 服务端信任客户端金额 | 服务端二次校验：`Number.isInteger` + 1~99,999,999；**非法即 1003**，防止构造请求写入脏数据 |
| 百分比浮点（66.9%） | 客户端用整数比：`(part*1000/total).toFixed(1)`，`total=0` 直接空态（不除零、不 NaN，PRD US-06） |
| 埋点泄露金额 | 上报前一律 `bucketOfAmount()` 转桶（§5.5），服务端只接受桶名 |

**验证**：`money.js` 单测覆盖 `0.1+0.2`、`18.5`、`1`（1 分）、`99999999`、`100000000`（越界）、`total=0`、¥10/¥8（E5）；分桶边界（100/1000/1001/5000/10000 分）；对账脚本比对 `records` 求和与 `stat`/`home` 返回值（§12.3）。

### 7.2 时区与日期边界（E3、E4、E11）

| 风险 | 对策 |
| --- | --- |
| 云函数运行环境是 UTC，直接 `new Date().getDate()` 会得到"前一天" | `common/time.js` 是**唯一**时间来源：`Date.now() + 8h` 后用 `getUTC*` 取值。**依据**：中国自 1991 年起全年 UTC+8、无夏令时，固定偏移是安全的 |
| `new Date('2025-03-18')` 被当作 UTC 午夜 → 比较/取值偏一天 | 业务日期一律用**字符串**，比较用字典序（`compareDate`），禁止 `new Date(dateStr)` |
| 用户改手机时间绕过"不能选未来/不能补记更早" | 所有"今天/本月/剩余天数/日期下界"由**服务端下发**（`server_date`/`server_month`）；客户端 picker 的 `start/end` 由 `server_date` 推导；服务端对 `date` 再做一次范围校验（1004） |
| 跨天（23:59 记账 → 00:01 查看，E3） | 服务端每次请求以**当次时刻**为准，天然自愈；客户端 `onShow` 时若 `server_date` 变化 → 重拉首页（剩余天数/今天重算，设计 §5.1.3、§9.4-6） |
| 月末/闰年 | `lastDayOfMonth` 用 `new Date(Date.UTC(y, m, 0)).getUTCDate()`（`m` 为 1-based 月），覆盖 2024-02-29 |
| 恰好月末最后一天（E4） | `remainingDays = 1` → 日均 = 剩余可用（`divideRound(remaining, 1)`） |
| 客户端用本地时间算"右箭头能不能点" | 右箭头依据服务端下发的 `server_month`（§5.3.6 出参），不用客户端 `Date` |

**验证**：`time.js` 单测固定输入时间戳（2025-03-31 23:59 CST = 15:59 UTC、2025-04-01 00:01 CST、2024-02-29、2025-01-01 00:00 CST = 2024-12-31 16:00 UTC）；真机手动改时区/系统时间各测一次；E3/E4/E11 用例。

### 7.3 预算计算（PRD §7.3、D5、D9）

| 规则 | 实现 |
| --- | --- |
| 剩余可用 = 预算 − 当月支出 | 整数减法；**收入不参与**（D5）。预算设置页常驻口径说明文案（PRD §9.3） |
| 未设预算 | `remaining_cents = null`、`daily_available_cents = null`；**服务端绝不发 0 冒充**（D9），客户端只渲染引导态（E14 验证） |
| 日均可用 | `remaining<=0 ? 0 : divideRound(remaining, remainingDays)`；剩余天数 ≥1；**不出现负数日均**（E5） |
| 超支 | `overspent = remaining < 0`；客户端显示「已超支 ¥xx.xx」（取绝对值）、进度条 100% 满格红色（设计 §5.1.4②） |
| 进度条 | 客户端算：`min(100, round(expense*100/budget))`，`budget=0` 不可能存在（D12 禁 0） |
| 预算只能设/读当前月 | `budget.set`/`budget.cancel`/`budget.get` 三者的 `month` 都必须 == `server_month`，否则 1001（ADR-16） |
| 改为 0 | 一律 1003（E13），取消只能走 `budget.cancel` |
| 引导态判定 | 只以 `home.summary.budget === null` 为准（§6.1） |

**验证**：US-04 的全部 Given/When/Then；E4/E5/E8/E13/E14/E15；并发沿用（§7.5）。

### 7.4 统计聚合与分页一致性

| 风险 | 对策 |
| --- | --- |
| 单次查询返回条数上限（云函数端，**具体数值 M0 核实**，估算 1000）导致"内存聚合"漏数据 | **用聚合管道**（`$match`/`$group`/`$sort`）在数据库侧完成聚合：返回行数 = 分类数（≤15），**不受条数上限影响**。放弃"查出来在云函数里 for 循环"（ADR-8） |
| 除零 / NaN / 空图表 | `expense_cents === 0` → `categories: []`、`has_data:false`；客户端空态，不渲染图表（PRD US-06） |
| 三处口径不一致 | `home.summary` 与 `stat.monthly` 共用 `common/stat.js`（同一函数、同一索引），首页/统计数字必然同源 |
| 归月错误 | 只用 `date`（`month` 字段由服务端从 `date` 推导并冗余存储），不按 `created_at`（PRD §7.3-7、E7） |
| **分页与合计"对不上"**（审查意见 3 的核心） | ① `month_totals`/`day_totals` 由聚合管道一次算出，**与分页解耦**；② `total_count` 与聚合在同一批查询里取，`has_more` 由它推导，**不存在"第 N 页后静默消失"**；③ **可测的一致性断言**：客户端把所有页翻完（`has_more=false`）后，`Σ items.amount_cents` 必须等于 `month_totals`（分收/支两类各算一次），且每个日期组头合计 = 该组可见条目之和。这条断言写进 M2 的 E9 验证项（§12） |
| 深分页性能 | 单用户单月账目**估算** ≤500（依据：日均 5~15 笔 × 30 天 = 150~450；E9 上限 1000）；走索引 I2 → 聚合**估** <200ms；最坏一页 `skip` ≈ 980 条，**估** 100~300ms。**触发升级条件**：实测单月 >2000 条或分页 P95 >500ms → 改游标分页（ADR-6） |

### 7.5 并发 / 重复提交（E6、E12、E15，回答 R10）

| 场景 | 机制 | 失败时的兜底 |
| --- | --- | --- |
| 连点保存 / 弱网重试 | 客户端按钮 `disabled + loading`（设计 §5.2.4） + 服务端**幂等**：`client_record_id` + 唯一索引 I1 | insert 冲突 → 回查返回同一条（`duplicated:true`），**最终只有 1 条**（E6） |
| 沿用预算并发写（R10 的核心） | 唯一索引 I4 + 冲突忽略 + 只在**当前月**写 + 单一入口 `ensureMonthBudget`（`home.summary` 与 `budget.get` 都走它） | 冲突 → 回查返回已存在记录；重复请求不产生重复记录（E15） |
| 两设备同时**编辑**同一笔（E12） | `version` 乐观锁：`where({_id,user_id,version}).update`，`updated===0` → 1006 | 客户端提示「数据已更新，请返回重试」并重拉；**不做协同编辑**（PRD E12） |
| 一台**编辑**、另一台**删除**（E12） | 删除 last-write-wins（ADR-15）；编辑方提交时记录已不存在 → **1005** → 静默重拉列表 | 结果 = 已删除，符合 E12「以服务端最新数据为准」；**不弹冲突提示**（删除是终态且已二次确认） |
| 重复删除 | 删不存在返回 `deleted:0`（`code 0`） | 幂等，不报错 |
| 菜单式防重（服务端限流） | **MVP 不做**（唯一索引已覆盖主要风险） | 已知风险见 §13-R7，P1 用 `users` 文档上的原子计数做限流 |

> **"不做事务"的理由**：本 MVP 没有任何"必须同时成功"的多文档写（① 记一笔账 = 单文档；② 沿用 = 单文档；③ 取消预算 = 删 budget + 改 user 两个文档，但二者**可接受不一致的窗口**——即删了预算但基准未清空，最坏结果只是"下次沿用又写回来"，用户重新取消一次即可）。**代价**：极端情况下（第 ③ 项在两次写之间崩溃）会出现"取消未彻底"的状态，需在日志里可发现；已记录为可接受风险（§13-R6）。

### 7.6 其他难点（简述）

| 难点 | 对策 |
| --- | --- |
| 记账页自绘键盘 vs 预算页系统键盘（设计 §11-Q5） | 键盘策略不同：记账页自绘（不调系统键盘，避免双键盘/顶起页面）、预算页 `type="digit"`。两者都必须在 **iPhone + 安卓各 1 台真机**验证（Q9/Q11 一并做） |
| 首屏 1.5s 与云函数冷启动冲突 | 并行调用 + 分类本地缓存 + 骨架屏（>300ms 才显示）+ 云函数零第三方依赖（包体小）+ M4 评估"定时触发保活"（代价：会产生额外调用计费） |
| 长列表（E9，单月 1000 条） | 分页 20 条 + 日期组头 `sticky`；**v0.2：无条数上限、按 `has_more` 翻到底**，到底显示「本月就这些了」；**估**单页 20 条在 1000 条总量下渲染无压力（依据：单月账目估算 ≤500）。若实测不达标（首屏渲染 >1s 或滚动帧率 <50fps）→ 启用简易虚拟列表（只渲染视口 ±1 屏），列为 M2 应急项 |
| 客户端竞态（切月/切 Tab 旧响应覆盖新响应） | `utils/request.js` 带自增 `seq`，回调时 `seq < current` 直接丢弃（设计 §5.3.2「切月必须清空旧数据」的技术落地） |
| 编辑页拿不到 `version` | 读接口直接下发（`items[]` 与 `recent_records[]`），不新增补取接口（ADR-14） |
| 数据库权限与越权 | 集合权限「仅管理端可读写」+ 所有查询强制带 `user_id` + 服务端不接受客户端传的 `user_id`（代码评审检查项） |
| 埋点影响主流程 | 埋点 fire-and-forget + 内存队列 + 失败丢弃（§5.4） |

---

## 8. 关键决策记录（每一条都是"选 A 不选 B，因为…；代价是…"）

| # | 决策 | 放弃的方案 | 理由 | 代价 |
| --- | --- | --- | --- | --- |
| **ADR-1** | 微信云开发（云函数 + 云数据库） | 自建服务器 + MySQL | 免运维、免域名备案配置、云函数直取 `OPENID`（免 `code2session`/AppSecret/登录态），把 3 周押在业务上 | 无关系约束与事务；无 SQL（统计走聚合管道）；冷启动；迁出成本（§3.1） |
| **ADR-2** | **2 个云函数**：`api`（单入口 action 路由）+ `track` | 每个领域一个云函数（user/home/record/budget/stat = 5~6 个） | 公共代码（`common/*`）天然共享、只需一次部署、日志集中、冷启动面更小；`track` 独立是为了失败隔离与配额隔离 | 单函数并发上限是全局的（MVP 量级无压力）；日志里各 action 需打 `action` 字段区分；函数代码包更大（估 <200KB，可接受） |
| **ADR-3** | 业务日期存 **`'YYYY-MM-DD'` 字符串** + 冗余 `month` | 存 `Date` / 时间戳 | 与 PRD §7.1 字段定义一致；字符串比较即时间序，**天然绕开时区偏移与 `new Date(str)` 的 UTC 陷阱**（§7.2）；`month` 冗余让按月查询/聚合走一条索引 | 无法直接做"跨月日期范围"的区间查询（本 MVP 不需要）；写入时多一步推导（服务端做，防脏数据） |
| **ADR-4** | 金额一律**整数「分」** | 浮点元 / `Decimal` 字符串 | PRD D11 明文要求；整数分在 1e8 值域内双精度精确；避免引入 decimal 库（云函数依赖越少越好） | 展示层每次都要除 100 并格式化（客户端 `format.js` 唯一实现）；输入层要自己做字符串→分转换（§7.1） |
| **ADR-5** | 幂等靠**唯一索引 + 冲突回查** | 分布式锁 / 去重表 / 事务 | 云开发无分布式锁，加锁反而更复杂；唯一索引是数据库层最强保证，天然处理并发 | **依赖唯一索引可创建**（M0 核实；若控制台不支持唯一索引，退化方案：先查后写 + `client_record_id` 在插入前二次查重 + 冲突时回查，概率上存在极小重复窗口，届时上报 CEO 并考虑改自建后端）；冲突路径需要一次额外查询（可接受） |
| **ADR-6** | 列表分页用 `skip/limit`，**按 `has_more` 翻到底、无 page 硬上限** | ① page ≤ 50 的硬上限；② 一次性返回全月；③ 游标分页 | ① 硬上限会**静默截断**并与"月合计/组头按全月聚合"矛盾（审查意见 3），必须去掉；② 一次返回全月会撞单次查询条数上限、首屏传输/渲染成本高；③ 游标分页客户端状态多（要记住三元组游标），MVP 单月量级（估 ≤500，E9 上限 1000）不值得 | 深分页性能随 `skip` 线性变差（1000 条时最深 skip≈980，**估** 100~300ms）。**触发升级条件**：实测单月 >2000 条或分页 P95 >500ms → P1 改游标分页（客户端只需把 `page` 换成 `cursor`，接口形状不变） |
| **ADR-7** | 分类用云函数**常量表** | 建 `categories` 集合 | 15 条固定数据放数据库 → 多一次查询、多一套 CRUD、多一份权限配置，无收益 | P1 自定义分类要迁移（常量表 → 集合）；**缓解**：字段名与顺序现在就按"将来是数据库行"来设计（`id/name/icon/sort_order/is_preset`），迁移只是搬数据 |
| **ADR-8** | 统计用**数据库聚合管道** | 云函数内取出记录内存聚合 | 聚合返回行数 = 分类数（≤15），**彻底绕开单次查询条数上限**；少一次全量传输 | 聚合管道调试不如本地循环直观；`$group` 结果顺序不稳 → 服务端显式 `$sort` 并做 tie-break 排序（按分类表顺序），保证同额分类展示稳定 |
| **ADR-9** | 沿用基准存 `users.budget_baseline_cents`，沿用写入**只在当前月**发生 | 从 `budgets` 推导基准 / 任意请求月份都写入 | 推导方案与 E14「取消后清空基准」矛盾（§4.1）；只在当前月写与 E15「不补写中间空月」语义一致，也避免浏览历史月时污染历史数据 | 与 PRD §7.1 字面"请求某月数据（首页/账单/统计）"有细微差异 → **需 PM 确认**（§13-Q1）；`home.summary` 与 `budget.get` 变成"读接口里有写"（语义不纯）→ 用单一入口 + 幂等约束 |
| **ADR-10** | 记账页**自绘键盘** / 预算页**系统键盘** `type="digit"` | 全站统一一种键盘 | 记账页追求 8 秒与"键盘含保存键"（设计 §5.2.1）；预算页是低频、要长按删除与小数点，系统键盘实现成本最低（设计 §11-Q5 建议） | 两套键盘逻辑 = 两处真机兼容验证（双键盘、遮挡、`env(safe-area-inset-bottom)`）；预算页在部分安卓机型 `type="digit"` 无小数点 → 需真机验证并准备 `type="number"` + 手工补小数点按钮的降级方案 |
| **ADR-11** | MVP **物理删除**，**不建 `deleted_at` 字段** | 提前建列（为 P1 撤销铺路） | MVP 无任何逻辑依赖该字段（PRD §7.1 已确认），提前建列 = 无收益的认知负担；云数据库无 schema，将来加字段**不需要 DDL/全量迁移** | P1 做撤销时要改所有查询加过滤条件（一次集中改动，风险可控）；**迁移策略**：语义定为"字段不存在或为 null = 未删除"，**不回填历史数据**；再补定时清理任务（PRD US-20 前置条件） |
| **ADR-12** | 不做本地草稿/离线队列 | 本地 pending 队列 + 后台重试 | PRD §4-13 明确不做；`client_record_id` + 幂等已解决"重试会不会重复"，不需要队列 | 弱网下用户必须手动再点保存（设计 §5.2.4 已定义为"失败保留输入 + 可再点"，体验可接受） |
| **ADR-13** | 埋点自建 `events` 集合，客户端批量上报，**金额分桶** | ① 只用微信官方自定义分析；② 上报金额原文 | 需要自算记账耗时中位数/页面转化，且数据要能导出给 PM 做假设验证（PRD §12）；分桶既够做分布分析又降低敏感度（PRD R2 方向一致） | 多一个云函数 + 客户端队列逻辑；埋点写入的配额消耗（估算见 §10）；不做服务端限流（§13-R7）；**不支持"精确金额分布"分析**（只能到桶） |
| **ADR-14** | 编辑所需的 `version` 由**读接口直接下发**（`record.list.items[]`、`home.summary.recent_records[]`） | 新增 `record.get` 补取接口 | 少一个 action、少一次网络往返、少一条可能失效的路径；编辑页本来就要列表数据（`items` 已含全部可编辑字段），加一个 `version` 是零成本 | 读接口 payload 每条多一个 int（可忽略）；**若将来出现"不经过列表直接打开编辑页"的入口（如分享/深链），必须补 `record.get`**——届时在 §5.2 登记，本 ADR 作废 |
| **ADR-15** | 删除**不做乐观锁**，last-write-wins，入参不含 `version` | 与编辑一样传 `version`、冲突返回 1006 | 删除已是"二次确认后的终态操作"，MVP 又没有撤销；为它加乐观锁只会多一条"冲突提示"文案和一条测试路径，换不来任何用户价值 | 并发"改+删"时编辑方只会拿到 1005（记录不存在），不会拿到"冲突"提示；**P1 做软删除 + 撤销时必须复评**（撤销需要"把删掉的记录改回来"，与并发编辑的组合语义要重新定义） |
| **ADR-16** | `budget.get` 的 `month` **必须为当前月**，历史月/未来月一律 1001 | 允许读历史月（只读分支） | MVP 只有一个"本月预算"页面（PRD §9.1 页面 5），没有任何页面消费历史预算；放开就会出现"能读不能写"的半开放接口与一条没人用的测试路径 | P1 若做历史预算回顾需放开校验 + 拆只读分支（估 0.2 天）；`budget.get` 与 `home.summary` 同为"读里带写（当前月沿用）"，语义不纯这一点在两处都需靠单一入口约束 |
| **ADR-17** | 收入/支出的「其他」使用**各自独立的 id**（`expense_other` / `income_other`） | 两者共用 `'other'` | 避免 `isValidCategory(id, type)` 的二义（审查意见 ⑥）；也让统计聚合的分类归属天然正确（不用再按 `type` 二次判断） | `id` 与展示名不再一一对应（有两个分类都叫「其他」），排查数据/画像时要记住；P1 自定义分类迁移时这两个 id 作为预置数据原样保留 |

---

## 9. 安全、隐私与合规

| 项 | 措施 |
| --- | --- |
| 密钥 | **代码与仓库里不出现任何密钥**。云开发方案下**不需要** `AppSecret`（免 `code2session`）；环境 ID 不是密钥，但仍放 `config.js` 单点管理，不硬编码散落。将来若做订阅消息，用云调用或从云函数环境变量读取，绝不写进代码 |
| 鉴权 | 每个 action 从 `cloud.getWXContext().OPENID` 取身份；**不接收客户端传的 `user_id`** |
| 数据库权限 | 所有集合设为**「仅管理端可读写」**；客户端**永不**调用 `wx.cloud.database()` |
| 越权防护 | 所有查询/更新/删除条件**必须**含 `user_id`；代码评审逐条检查（列为本项目唯一强制的评审项） |
| 输入校验 | 类型 / 枚举 / 范围 / 长度 / 字符集；金额必须整数、日期必须正则 + 范围；备注过滤控制字符，长度按字符计 |
| 输出最小化 | 不返回 openid 原文（用 `user_key = sha256(openid)[0:16]`）；错误信息不含内部堆栈与数据库字段名 |
| 日志 | 只记 `request_id` / `action` / `user_key` / 错误码；**不记 `amount_cents`、`note`**；金额只在必要排查时以分桶形式记录 |
| 隐私合规 | 需在《用户隐私保护指引》声明收集：微信标识（openid）、记账数据（金额/分类/日期）、**必要埋点**（PRD R2，责任人 CEO）；**埋点金额一律以分桶上报**（§5.5），不采金额原文 |
| 数据删除 | 提供 `user.clearMyData`（若 CEO 采纳 R3，二次确认 + 硬删）；不做账号注销（不在 MVP 范围，建议记录为已知缺口） |
| 备份 | 每周用 `scripts/verify.js` 的思路手动导出（只读，密钥走环境变量且不提交）；MVP 不做自动备份（云开发自带备份能力，M0 核实可用性） |

---

## 10. 性能与容量估算（**全部是估算，标注依据，无线上数据**）

| 指标 | 目标 | 估算依据与措施 |
| --- | --- | --- |
| 首屏可交互 | ≤ 1.5s（PRD US-01） | 冷启动 0.3~0.8s（**估**，机型差异大）+ 云函数冷启动 0.2~0.6s（**估**）+ 并行 2 次调用 + 网络 0.1~0.3s ≈ 1.2s（**估**）；分类本地缓存命中时只剩 1 次调用；骨架屏兜住观感（设计 §6 通用规则 1）。`user.init` 出参已去掉预算查询，首屏少一次读（v0.2） |
| 记账页打开 | ≤ 300ms（PRD §9.2-1） | 纯本地渲染，**无网络等待**（分类走缓存 + 内置兜底常量） |
| 保存响应 P95 | ≤ 1s | 热启动云函数 50~150ms（**估**）+ 写库 <50ms；冷启动风险用 M4 的保活评估兜底 |
| 账单列表首屏 | ≤ 1s（PRD US-05） | 每页 20 条 + 索引 I2 命中 |
| 账单翻页（最深一页） | ≤ 500ms | 单月 1000 条时最深 `skip`≈980，**估** 100~300ms（跳过索引条目）；超过则触发 ADR-6 的游标分页升级条件 |
| 统计聚合 | ≤ 500ms | 聚合管道 + 索引 I2；返回行数 ≤15 |
| 并发（估算） | MVP 目标 1000 DAU；峰值假设同时在线 50~100 人、写操作 ~5 QPS | 依据：1000 DAU × 每人每天 30 次云函数调用 = 3 万次/日 ≈ 峰值 5~10 QPS（**估**）——远低于云函数默认并发，无需扩容 |
| 云函数调用量 | ~3 万次/日（1000 DAU），月 ~90 万次 | 依据：`user.init` 1 次 + `home.summary` 5 次/人/日 + `record.*` 10 次 + `stat/list/budget` 8 次 + `track` ~6 次（批量）≈ 30 次/人/日 |
| 数据库读写 | ~5 万次/日，月 ~150 万次 | 依据：每人每天约 50 次读写（首页聚合 3 次 + 记账 2 次 + 列表 2 次 + 统计 1 次 + 埋点 1 次批量写 ≈ 9~15 次实际操作 × 页面重拉次数） |
| 存储 | ~2MB/用户（**估**） | 依据：1 万条记录 × ~200B ≈ 2MB；1000 用户 ≈ 2GB；`events` 每人每天 ~30 条 × 200B ≈ 6KB/日 → 千用户 ~6MB/日、180 天 ~1GB（保留期内） |
| 费用 | 待核实 | **必须在 M0 用云开发控制台的配额/计费页核对**（本阶段无账号环境，不做价格断言）；若免费额度不足，升级套餐的成本由 CEO 决策 |

> **本节的诚实说明**：以上全部是量级估算，用于判断"是否需要为性能做额外设计"。**结论是不需要**（无需消息队列、无需缓存层、无需分库分表、无需 CDN），这也正是选云开发 + 单体云函数的现实依据。任何"用户量达到 X 就会崩"的结论都要等真实数据。

---

## 11. 可观测性

| 手段 | 内容 |
| --- | --- |
| 云函数日志 | 每次调用打结构化一行：`{request_id, action, user_key, code, duration_ms, err_msg?}`；金额与备注不入日志 |
| 错误率 | 云开发控制台的云函数错误率 + 按 `code` 统计（在日志里筛 `code != 0`） |
| 业务指标 | `events` 集合：`record_page_open → record_save_success` 转化率与 `duration_ms` 中位数、`budget_set` 设置率、次周留存（按 `user_key` + `app_open`）、`amount_bucket` 分布 |
| 数据一致性 | `scripts/verify.js`（只读）：抽样用户，比对 `records` 求和 vs `home.summary`/`stat.monthly` 返回值；**以及分页翻到底的求和 vs `month_totals`**（§7.4）；发现不一致即人工排查（MVP 无自动告警） |
| 崩溃 | 小程序后台「异常」页面（不引入第三方 APM，MVP 从简） |
| 告警 | **MVP 不做自动告警**（无值班人力）；约定"每天人工看一次错误率 + 每天一次对账脚本"，由工程师在灰度期执行 |

---

## 12. 开发里程碑与验证方式

> **工期口径（v0.2 明确，回答建议①）**：**D = 自然日**（D1 = 开工日，含周末）。**21 个自然日 = 3 周 ≈ 15 个工作日**（按每周 5 个工作日折算，未扣法定节假日与个人请假）——与 PRD 给的「3 周开发资源」口径对齐。里程碑只按自然日排；若期间遇法定节假日，M5 顺延（缓冲 1 天已含在 M5 内）。
> 前提：P-1（备案 + 隐私指引）必须**并行**启动，否则 M5 会被卡住。

| 里程碑 | 工期 | 交付物 | 完成定义（DoD） | 验证方式 |
| --- | --- | --- | --- | --- |
| **M0 工程准备** | D1–D2 | 云开发环境、4 个集合 + 索引 I1~I4（附录 A 核对）、`common/{time,money,validate,errors}.js`、分类常量表（含 `expense_other`/`income_other`）、错误码表、`utils/request.js`、页面骨架（含 TabBar 与四态占位） | 索引在控制台可见且唯一索引创建成功（**ADR-5 的兜底前提在这里验证**）；本地 `npm test` 跑通 `time/money` 单测 | ① 单测（固定时间戳用例，§7.2）；② 控制台截图核对索引；③ 真机点一遍空页面、确认无报错 |
| **M1 登录 + 首页** | D3–D6 | `user.init`、`home.summary`、首页（骨架/引导态/空态/错误态/最近 3 笔含 `version`） | 新用户首开为引导态（判据**只有** `home.summary.budget === null`）；有预算时剩余可用/日均与手算一致 | ① `scripts/seed.js` 造数后逐条核对 PRD US-04 的 Given/When/Then（E4/E5/E8）；② 对账脚本；③ 冷启动耗时实测（真机 + 开发者工具 Performance 面板，记录 10 次取中位数） |
| **M2 记账 + 账单** | D7–D11 | `record.create/update/delete/list`、记账页（自绘键盘、日期 picker）、账单页（分页、切月、编辑、删除确认） | 3 步保存成功；连点只产生 1 条；切月不串数据；**编辑能从列表/首页两条入口都保存成功**；**列表翻到底不丢数据** | ① E1/E2/E6/E7/E8/E11/E12/E16/E17 用例逐条过；② 幂等专项：同一 `client_record_id` 并发 10 次 create → `records` 恰好 1 条；不同 id 并发 10 次 → 恰好 10 条；③ **分页专项（E9）**：造 1000 条（另加一组 1200 条）→ 翻到底 `has_more=false`、显示「本月就这些了」、`Σ items.amount_cents == month_totals`、每个日期组头合计 == 组内可见条目之和、无重复无遗漏（`_id` 去重后条数 == `total_count`）；④ 编辑专项：从首页最近记录进入并保存 → `version` 递增、`note` 未被清空；⑤ 埋点打点验证 `record_page_open → record_save_success` 中位数 ≤8s |
| **M3 预算 + 统计** | D12–D15 | `budget.get/set/cancel`、`ensureMonthBudget`、`stat.monthly`、预算页、统计页 | US-04/US-06 全量验收通过；沿用并发只写 1 条；`budget.get` 传历史月返回 1001 | ① E13/E14/E15 用例；② 并发沿用专项（同一用户并发 5 次 `home.summary` → `budgets` 恰好 1 条；`home.summary` 与 `budget.get` 并发 → 仍 1 条）；③ 除零专项（只有收入/完全无账目）；④ 单分类 100.0% 不崩溃；⑤ `budget.get` 月份越界 1001 用例 |
| **M4 埋点 + 联调 + 打磨** | D16–D18 | `track.report` + 全量事件上报（含 `amount_bucket`）、六页四态补全、字幕/等宽数字真机验证 | 埋点链路通且**不影响**业务（拔网线测保存仍成功）；四态逐页齐备；埋点中**不含金额原文** | ① 逐页对照设计稿 §6 状态总表；② 真机（iPhone + 安卓各 1）验证 `tabular-nums`（设计 Q9）与自绘键盘无双键盘；③ `aria-*` 支持度实测（设计 Q11），不支持则确认"可见文字自解释"退化；④ E10（保存前杀进程）/E18；⑤ 埋点分桶边界抽样（¥1.00/¥10.00/¥50.00/¥100.00 四种边界值各记一笔，核对落桶） |
| **M5 提审 + 灰度** | D19–D21（含 1 天缓冲） | 体验版 / 审核版、隐私指引与备案材料、灰度名单与数据看板 | 提审通过；灰度 20~50 人能用 | ① 灰度期每天看错误率、崩溃率（<0.5%）、首屏耗时、记账耗时中位数；② 每天跑一次对账脚本；③ 收集 PRD §12 假设的验证数据（A2 预算设置率、A4 分类分布） |

### 12.1 验证手段的分层（**本阶段未运行任何测试，以下为下一阶段要做的事**）

| 层 | 手段 | 能发现什么 | 局限（诚实说明） |
| --- | --- | --- | --- |
| 静态 | ESLint（本地手动跑）、代码评审**强制项**：每条 DB 查询是否带 `user_id` | 越权、低级语法错 | 不做 CI（单人团队） |
| 单元（离线可跑） | `node --test` 跑 `common/time.js`、`common/money.js`、`common/validate.js`、`common/stat.js` 的纯函数；`stat.js` 的聚合函数把"数据库调用"做成可注入参数，用内存假数据测试 | 金额精度、时区/闰年/月末、预算计算、除零、排序稳定性、分桶边界 | **不覆盖**云函数运行时、数据库真实行为、真机表现 |
| 集成（测试环境） | `scripts/seed.js` 造数（覆盖 E1~E18 场景，含 1000/1200 条单月数据）+ `scripts/verify.js` 只读对账 | 接口口径、幂等、并发、聚合一致性、分页完整性 | 需要测试环境与密钥（走环境变量，不提交）；密钥由 CEO 提供 |
| 端到端（真机） | 按 PRD §8 验收标准 + §10 边界 E1~E18 的手测清单；与测试同事的用例合并执行 | 四态、键盘、字体对齐、无障碍、跨天 | 人工，易漏；需按清单逐条打勾 |
| 数据验证（灰度） | 埋点看板：耗时中位数、转化率、预算设置率、错误码分布、金额分桶分布 | 是否达 PRD §2.2 目标值 | 依赖真实用户，MVP 前不可得 |

### 12.2 与测试同事的分工（明确边界，避免重复）

* **我（工程师）提供**：`scripts/seed.js`（一键造出 E1~E18 所需的账目/预算数据，含"单月 1000 条/1200 条"两种分页压力场景）、`scripts/verify.js`（对账 + 分页求和比对）、错误码表、可复现的错误注入开关（`MOCK_FAIL=1` 时写接口返回 2001，用于验证客户端错误态与"不产生脏数据"）、云函数日志查询指引。
* **测试同事负责**：用例集与执行、边界取值设计（尤其 E1/E13 的非法输入、E15 的跨月序列、E9 的分页完整性）、验收结论。
* **验收基准**：PRD §8 的 Given/When/Then 与 §10 的 E1~E18 是唯一基准；本方案不新增验收口径，只补充"技术上如何验证"。**接口覆盖以 §5.2 的 11 个 action 为准**（含 `budget.get`）；埋点金额口径以 §5.5 的 `amount_bucket` 为准。

### 12.3 单元测试清单（M0~M3 必须逐步补齐）

| 模块 | 必测用例 |
| --- | --- |
| `time.js` | 2025-03-31 23:59 CST / 2025-04-01 00:01 CST / 2024-02-29 / 2025-01-01 00:00 CST；`lastDayOfMonth` 各月；`dateLowerBound` 跨年（1 月的上月是去年 12 月）；`remainingDays` 月末=1 |
| `money.js` | 1 分、18.5 元、999999.99、100000000（越界）、非整数、`divideRound(87250, 14)=6232`、`divideRound(x,1)`；`bucketOfAmount` 边界（1/100/101/1000/1001/5000/5001/10000/10001 分） |
| `validate.js` | 缺参/类型错/枚举非法/超长备注/日期正则/`month` 非当前月 |
| `stat.js` | 汇总正确性、分类降序 + 同额 tie-break、空数据不 NaN、单分类 100% |
| `record.js`（分页） | `has_more` 与 `total_count` 的关系、越界页返回空、`earliest_month` 无账目时 = `server_month` |
| 幂等（集成） | 同 id 并发 10 次 → 1 条；预算沿用并发 5 次 → 1 条；`version` 冲突 → 1006；编辑已删除记录 → 1005 |

---

## 13. 风险与待确认问题

### 13.1 技术风险登记（含 PRD 点名要工程师回答的项）

| # | 风险 | 影响 | 对策 | 状态 |
| --- | --- | --- | --- | --- |
| **T1（=PRD R1）** | 微信 `wx.getUserProfile` 已不返回真实头像昵称 | 我的页无法展示真实资料 | **MVP 用系统默认头像 + 「微信用户」占位**，不调用 `getUserProfile`，不引入 `<button open-type="chooseAvatar">`（需用户主动点击，与"零门槛"目标冲突）；头像昵称下沉 P2。技术上可行但不建议 MVP 引入 | **已给出结论**（技术可行性：默认头像可行，无需任何授权） |
| **T2（=PRD R6）** | 订阅消息为一次性授权、送达不可保证 | P2 提醒能力可能不达预期 | MVP **不做推送**；替代 = 页内状态提示（P1 US-11，设计已定义 `--color-warning-*` token）。技术确认：云开发可用**云调用**发订阅消息（免 access_token），但必须先由用户点击 `wx.requestSubscribeMessage` 获取授权，**不能作为核心提醒手段** | **已给出结论** |
| **T3（=PRD R10）** | 沿用预算在"读路径里写"，并发可能重复写 / 只读语义不纯 | 数据一致性 | 唯一索引 I4 + 单一入口 `ensureMonthBudget` + **只为当前月写** + 冲突回查；E15 行为不变 | **已给出方案**；**其中"只为当前月"需 PM 确认**（§13-Q1） |
| T4 | 云开发控制台**唯一索引**创建能力（ADR-5 前提） | 幂等方案失效 | **M0 第一件事验证**；若不可用，退化方案（先查后写 + 二次查重 + 冲突回查）并上报 CEO 评估是否改自建后端 | 待 M0 核实 |
| T5 | 云开发单次查询条数上限（估 1000） | 内存聚合漏数据 | 统计一律走聚合管道（ADR-8）；`clearMyData` 循环删除按 1000/轮；分页与条数上限无关（单页 ≤50） | 待 M0 核实具体数值 |
| T6 | 取消预算的两次写（删 budget + 清 baseline）非事务 | 极端情况下"取消未彻底" | 可接受：最坏是下次沿用写回来，用户再取消一次；日志可发现；**不做事务**（云开发跨文档事务限制多，收益低） | 已接受 |
| T7 | 埋点无限流，可能被刷 | 配额消耗 | MVP 接受（无商业化、无攻击动机）；P1 用 `users` 文档原子计数限流 | 已接受（P1） |
| T8 | 云函数冷启动导致首屏超 1.5s（低端安卓 + 冷启动叠加） | US-01 验收 | 并行调用 + 本地缓存 + 骨架屏；M4 实测 10 次取中位数；若仍不达标 → 评估定时保活（有计费代价） | 待实测 |
| T9 | 自绘键盘 / `tabular-nums` / `aria-*` 的机型差异 | 交互与无障碍表现（设计 Q9/Q11） | M4 真机逐项验证；均已准备降级方案（等宽字体栈、可见文字自解释） | 待实测 |
| T10 | 备案与隐私指引未及时完成 | 无法上线 | M0 即请 CEO 启动（P-1） | **需 CEO 行动** |
| T11 | `skip` 深分页在极端数据量下变慢（ADR-6 取消硬上限后的新暴露面） | 翻页卡顿 | 已在 §7.4 给出实测触发条件（单月 >2000 条或 P95 >500ms → 游标分页）；M2 用 1000/1200 条造数实测 | 待实测（M2） |
| T12 | 「我的」页若要做"使用 N 天"需新增日活记录 | 可能误用 `last_active_at` 算出错误的"活跃天数" | MVP 不做该指标；若要做，用"加入 N 天"（`users.created_at` 推算，零存储成本），精确活跃天数下沉 P1（§13-Q11） | 待 PM 定 |

### 13.2 待确认问题（请相关同事逐条回复）

| # | 问题 | 我的建议 | 需谁定 |
| --- | --- | --- | --- |
| **Q1** | PRD §7.1 写「客户端请求某月数据（首页/账单/统计）时」做沿用写入，但 E15 要求"只给当前月写、不补写中间空月"。**我实现为"只为服务端当前月写入"**，浏览历史月不写入。是否同意？ | 同意按"只为当前月"实现（与 E15 一致，且避免污染历史数据） | **PM** |
| **Q2** | 「我的」页是方案 A+（3 Tab + 首页 `[⋯]` 二级页）还是方案 B（4 Tab）？影响页面数与 `user.clearMyData` 是否 MVP 就做 | 推荐 A+（不破坏 PRD D10，主流程干净） | **CEO + PM**（设计稿 §11-Q1） |
| **Q3** | R3「清空我的账目」MVP 是否做？ | 建议做（接口成本 ≤0.5 天，且是隐私合规的加分项）；不做则须记录为已知缺口 | **CEO** |
| **Q4** | 埋点里的 `amount` 是否改为**分桶**上报？ | **本方案已默认分桶**（`amount_bucket`，§5.5，桶边界已定义、可测），以免阻塞测试写用例。**PM 若否决，需在 M4 前回复**，届时改 `events-schema.js` 一处 + 测试口径 | **PM** |
| **Q5** | 编辑一笔"今天"的账目、到了第二天，是否允许原样保存？（设计 §11-Q3） | 允许（`date` 未改变则只做格式校验，不改则不做范围拦截），避免用户被卡住 | **PM** |
| **Q6** | 首页「最近记录」单条是否可点进编辑？（设计 §11-Q6） | 可点：接口已下发 `version`（§5.3.2），复用成本 ≈0；若定为只读，客户端忽略该字段即可，接口无需回退 | **PM** |
| **Q7** | 删除入口是否只保留左滑（PRD 原文"长按（或左滑）"）？（设计 §11-Q12） | 只保留左滑 + 编辑页删除按钮（设计已要求），避免手势口径分裂 | **PM + 测试** |
| **Q8** | 统计页是否需要在 MVP 支持切月？ | 建议不做（PRD 固定当前月）；服务端已支持 `month` 参数，P1 打开即可 | **PM** |
| **Q9** | 单月账目量级预期（我按 ≤500、E9 上限 1000 设计；分页已无硬上限，可一直翻到底） | 请 PM/测试确认量级；**若预期单月 >2000 条**（如未来做补记导入），需按 ADR-6 改游标分页（+0.5 天） | **PM / 测试** |
| **Q10** | 灰度期的"每日人工看错误率 + 对账"由谁执行？ | 建议工程师执行、测试复核 | PM / 测试 |
| **Q11** | 设计 §11-Q2：「我的」页要不要显示"已记录 X 笔 · 使用 N 天"？ | **建议 MVP 省略该行**（理由：需新增一个接口与一次 `count` 查询，对 PRD §2.2 的核心指标零贡献；且"使用 N 天"需要日活记录，现有字段算不出准确值）。若 PM 要求，MVP 可退化为 **「已记录 X 笔 · 加入 N 天」**（`joined_days` = `today − users.created_at + 1`，零存储成本），实现为条件性 action `user.profile`（§5.2 已给规格，**采纳则 action 数 11 → 12**），成本 ≈0.3 天，排在 M4 | **PM**（设计 Q2） |
| **Q12** | 编辑页是否允许改「支出/收入」类型？ | **建议不允许**（置灰）。理由：改类型会让 `category_id` 必须同时更换，多一条"分类与类型不匹配"的校验路径与测试路径，而 MVP 又没有任何验收标准要求改类型（US-05 只要求改金额/分类/日期）。若 PM 要求支持，需补充"切类型时分类自动重置为该类型默认分类"的规则（+0.3 天） | **PM + 设计** |

---

## 附录 A：M0 云开发控制台核对清单

| # | 项 | 期望 | 备注 |
| --- | --- | --- | --- |
| A1 | 创建集合 `users` / `records` / `budgets` / `events` | 4 个集合存在 | 权限均设「仅管理端可读写」 |
| A2 | `records` 索引 I1 `(user_id, client_record_id)` | **唯一索引创建成功** | ADR-5 的前提，失败则走退化方案并上报 |
| A3 | `records` 索引 I2 `(user_id, month, date desc, created_at desc)` | 创建成功 | 列表/聚合 |
| A4 | `records` 索引 I3 `(user_id, date desc, created_at desc)` | 创建成功 | 首页最近 3 笔、`earliest_month` |
| A5 | `budgets` 索引 I4 `(user_id, month)` | **唯一索引创建成功** | E15/R10 的前提 |
| A6 | `events` 索引 `(name, server_ts desc)`、`(user_key, server_ts desc)` | 创建成功 | |
| A7 | 单次查询返回条数上限、唯一索引冲突的 errCode | **记录实测值** | 写进代码注释；冲突判断以此为准（不猜）。影响 `clearMyData` 每轮 `limit` |
| A8 | 云函数超时、内存、并发默认值 | **记录实测值** | `user.clearMyData` 需要更长超时（估 20s 起） |
| A9 | 云开发配额/计费页面 | **记录 vs §10 估算** | 供 CEO 决定套餐 |

## 附录 B：错误码 × 场景速查（给测试做用例用）

| 场景 | 期望 code | 期望副作用 |
| --- | --- | --- |
| 金额 0 / 空 / 1.999 元（199.9 分，非整数） | 1003 | 不写库 |
| 金额 ¥999,999.99 | 0 | 写库，version=1 |
| 金额 > ¥999,999.99 | 1003 | 不写库 |
| 日期 = 上月 1 日（E11） | 0 | 写库，归上月 |
| 日期 = 上月 1 日再往前一天 | 1004 | 不写库 |
| 日期 = 明天 | 1004 | 不写库 |
| 同 `client_record_id` 提交两次 | 0 + `duplicated:true` | `records` 只有 1 条 |
| 编辑时 `version` 过期（记录仍在） | 1006 | 不写库，原记录不变 |
| 编辑时记录已被删除（另一设备） | 1005 | 不写库，客户端重拉列表 |
| 删除不存在的 `id` / 重复删除 | 0 + `deleted:0` | 无副作用 |
| 两设备并发删除同一笔 | 先 0 + `deleted:1`；后 0 + `deleted:0` | 最终 0 条 |
| 预算 0 / 留空 | 1003 | 不写库，基准不变 |
| 预算设/读/取消历史月（含 `budget.get`） | 1001 | 不写库、不返回数据 |
| 取消预算 | 0 + `canceled:1` | 删记录 + 基准置 null；取消不存在的预算返回 `canceled:0` |
| 请求未来月的账单 | 1001 | 返回空数据不写库 |
| `record.list` 的 `page` 越界（如 page=999） | 0 | `items:[]`、`has_more:false` |
| 用户完全无账目时 `record.list` | 0 | `earliest_month === server_month`（左箭头置灰）、`total_count:0`、`has_more:false` |
| 埋点金额字段 | 0 | `events` 里只有 `amount_bucket`，无金额原文；非法桶名被丢弃并计入 `dropped` |

## 附录 C：变更记录

| 版本 | 变更 | 作者 |
| --- | --- | --- |
| v0.1 | 首稿：架构与选型评估（云开发 vs 自建后端）、模块划分与目录结构、数据模型（users/records/budgets/events + 索引）、接口设计（2 个云函数 + action 路由 + 统一响应/错误码 + common 内部接口）、7 个关键流程时序、6 类关键难点对策（金额精度/时区/预算/聚合/并发/其他）、13 条决策记录、安全隐私合规、性能与容量估算、可观测性、5 个里程碑与分层验证方式、风险登记与 10 条待确认问题；回答 PRD R1/R6/R10 与 PRD §13.3「`deleted_at` 是否提前建列」，回答设计稿 Q9/Q11。（注：v0.1 正文曾把 action 数写作「9 个」，实际列出 10 个且漏登记 `budget.get`，本版已修正为 11 个） | 工程师 |
| v0.2 | 按测试第 1 轮审查意见修订：**① 接口契约自洽（P1）**——`record.list.items` 与 `home.summary.recent_records` 明确返回 `version`、不新增 `record.get`（ADR-14）、补充"`record.update` 未传字段不修改（含 note）"，同步 §5.6/§6.7；**② 接口清单完整（P2）**——路由表补 `budget.get` 并把 action 计数修正为 11、明确 `budget.get` 仅当前月（历史/未来月 1001，ADR-16）、§5.2 与附录 C 同步；**③ 分页不再静默截断（P2）**——删除 `page ≤ 50` 硬上限、新增 `total_count`/`server_month`、定义 `has_more` 与越界页行为、明确翻到底 UI（设计 N3）、把"翻到底求和 == `month_totals`"写成可测断言并落到 §7.4/§12 M2/附录 B，重写 ADR-6 并新增 T11；另采纳 8 条建议：里程碑 D 口径注明为自然日（21 自然日 ≈ 15 工作日）、回答设计 Q2（新增 §13-Q11，给出"加入 N 天"的降级方案与成本）、删除 `user.init.has_budget_this_month`（首屏引导态只由 `home.summary.budget` 决定）、`record.delete` 去掉 `version` 并明确 last-write-wins（ADR-15）、`track` 调用与响应统一为 §5.1 的封装（§5.4）、收支「其他」拆成 `expense_other`/`income_other`（ADR-17）、定义无账目时 `earliest_month = server_month`、埋点金额默认分桶并给出可测桶边界（§5.5）；新增 §13-Q12（编辑页是否允许改类型）；同步修正文档版本行对变更记录位置的交叉引用（§14 → 附录 C） | 工程师 |
