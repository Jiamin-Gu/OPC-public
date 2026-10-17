## 🛠️ 技术交付物

### 产品需求文档（PRD）

```markdown
# PRD: [Feature / Initiative Name]
**Status**: Draft | In Review | Approved | In Development | Shipped
**Author**: [PM Name]  **Last Updated**: [Date]  **Version**: [X.X]
**Stakeholders**: [Eng Lead, Design Lead, Marketing, Legal if needed]

---

## 1. Problem Statement（问题陈述）
我们在解决什么具体的用户痛点或业务机会？
谁遇到了这个问题、频率如何、不解决的代价是什么？

**Evidence（证据）:**
- User research（用户研究）: [访谈发现, n=X]
- Behavioral data（行为数据）: [展示问题的指标]
- Support signal（客服信号）: [工单量 / 主题]
- Competitive signal（竞争信号）: [竞品做了或没做什么]

---

## 2. Goals & Success Metrics（目标与成功指标）
| Goal（目标） | Metric（指标） | Current Baseline（当前基线） | Target（目标值） | Measurement Window（度量窗口） |
|------|--------|-----------------|--------|--------------------|
| 提升激活率 | 完成设置的用户百分比 | 42% | 65% | 上线后 60 天 |
| 降低客服负担 | 该主题周工单数 | 120 | <40 | 上线后 90 天 |
| 提升留存 | 30 天回访率 | 58% | 68% | Q3 队列 |

---

## 3. Non-Goals（不做的事）
明确说明本次迭代不会涉及的内容。
- 本次不重新设计新手引导流程（独立项目，Q4）
- V1 不支持移动端（分析显示该功能移动端使用 <8%）
- 在验证基础行为之前不添加管理员级别的配置

---

## 4. User Personas & Stories（用户画像与故事）
**Primary Persona（主要画像）**: [Name] — [简要描述，如"中型企业运营经理，200 人公司，每天使用产品"]

核心用户故事及验收标准：

**Story 1**: 作为 [画像]，我想要 [操作] 以便 [可衡量的结果]。
**Acceptance Criteria（验收标准）**:
- [ ] Given [场景], when [操作], then [预期结果]
- [ ] Given [边界情况], when [操作], then [降级行为]
- [ ] Performance: [操作] 在 [Y]% 的请求中 [X]ms 内完成

**Story 2**: 作为 [画像]，我想要 [操作] 以便 [可衡量的结果]。
**Acceptance Criteria（验收标准）**:
- [ ] Given [场景], when [操作], then [预期结果]

---

## 5. Solution Overview（方案概述）
[对提议方案的叙述性描述——2–4 段]
[包括关键 UX 流程、主要交互和交付的核心价值]
[设计稿 / Figma 链接]

**Key Design Decisions（关键设计决策）:**
- [Decision 1]: 我们选择 [方案 A] 而非 [方案 B]，因为 [原因]。取舍：[我们放弃了什么]。
- [Decision 2]: 我们将 [X] 延后到 V2，因为 [原因]。

---

## 6. Technical Considerations（技术考量）
**Dependencies（依赖）**:
- [系统 / 团队 / API] — 需要用于 [原因] — Owner: [name] — Timeline risk: [High/Med/Low]

**Known Risks（已知风险）**:
| Risk（风险） | Likelihood（可能性） | Impact（影响） | Mitigation（缓解措施） |
|------|------------|--------|------------|
| 第三方 API 限流 | Medium | High | 实现请求队列 + 降级缓存 |
| 数据迁移复杂度 | Low | High | 第 1 周做 Spike 验证方案 |

**Open Questions（待解决问题，开发前必须解决）**:
- [ ] [问题] — Owner: [name] — Deadline: [date]
- [ ] [问题] — Owner: [name] — Deadline: [date]

---

## 7. Launch Plan（发布计划）
| Phase（阶段） | Date（日期） | Audience（受众） | Success Gate（通过标准） |
|-------|------|----------|-------------|
| Internal alpha | [date] | 团队 + 5 个设计合作伙伴 | 无 P0 Bug，核心流程完整 |
| Closed beta | [date] | 50 个已报名客户 | <5% 错误率, CSAT ≥ 4/5 |
| GA rollout | [date] | 2 周内 20% → 100% | 20% 时指标达标 |

**Rollback Criteria（回滚标准）**: 如果 [指标] 低于 [阈值] 或错误率超过 [X%]，回滚 Feature Flag 并通知值班人员。

---

## 8. Appendix（附录）
- [用户研究录像 / 笔记]
- [竞品分析文档]
- [设计稿（Figma 链接）]
- [数据分析仪表盘链接]
- [相关客服工单]
```

---

### 机会评估

```markdown
# Opportunity Assessment: [Name]
**Submitted by**: [PM]  **Date**: [date]  **Decision needed by**: [date]

---

## 1. Why Now?（为什么是现在？）
什么市场信号、用户行为变化或竞争压力让这件事今天变得紧迫？
如果我们推迟 6 个月会怎样？

---

## 2. User Evidence（用户证据）
**Interviews（访谈）** (n=X):
- 关键主题 1: "[代表性引用]" — 在 X/Y 次访谈中观察到
- 关键主题 2: "[代表性引用]" — 在 X/Y 次访谈中观察到

**Behavioral Data（行为数据）**:
- [指标]: [当前状态] — 表明 [解读]
- [漏斗步骤]: X% 流失 — [关于原因的假设]

**Support Signal（客服信号）**:
- 每月 X 个包含 [主题] 的工单 — [占总量的百分比]
- NPS 贬损者评论: [反复出现的主题]

---

## 3. Business Case（商业论证）
- **Revenue impact（收入影响）**: [预估 ARR 增长、流失减少或追加销售机会]
- **Cost impact（成本影响）**: [客服成本降低、基础设施节省等]
- **Strategic fit（战略契合）**: [与当前 OKR 的关联——引用具体目标]
- **Market sizing（市场规模）**: [与该功能空间相关的 TAM/SAM 背景]

---

## 4. RICE Prioritization Score（RICE 优先级评分）
| Factor（因素） | Value（值） | Notes（备注） |
|--------|-------|-------|
| Reach | [X users/quarter] | 来源: [分析 / 估算] |
| Impact | [0.25 / 0.5 / 1 / 2 / 3] | [理由] |
| Confidence | [X%] | 基于: [访谈 / 数据 / 类似功能] |
| Effort | [X person-months] | 工程 T-shirt: [S/M/L/XL] |
| **RICE Score** | **(R × I × C) ÷ E = XX** | |

---

## 5. Options Considered（备选方案）
| Option（方案） | Pros（优势） | Cons（劣势） | Effort（工作量） |
|--------|------|------|--------|
| 构建完整功能 | [优势] | [劣势] | L |
| MVP / 缩小范围版本 | [优势] | [劣势] | M |
| 购买 / 集成合作伙伴 | [优势] | [劣势] | S |
| 延后 2 个季度 | [优势] | [劣势] | — |

---

## 6. Recommendation（建议）
**Decision**: Build / Explore further / Defer / Kill

**Rationale（理由）**: [2–3 句话说明为什么给出此建议、什么证据驱动了它、什么条件会改变决策]

**Next step if approved（批准后下一步）**: [如 "安排 [日期] 那周的设计冲刺"]
**Owner**: [name]
```

---

### 路线图（Now / Next / Later）

```markdown
# Product Roadmap — [Team / Product Area] — [Quarter Year]

## 🌟 North Star Metric（北极星指标）
[最能衡量用户是否获得价值、业务是否健康的单一指标]
**Current**: [当前值]  **Target by EOY**: [年底目标值]

## Supporting Metrics Dashboard（支撑指标看板）
| Metric（指标） | Current（当前值） | Target（目标值） | Trend（趋势） |
|--------|---------|--------|-------|
| [激活率] | X% | Y% | ↑/↓/→ |
| [D30 留存] | X% | Y% | ↑/↓/→ |
| [功能采用率] | X% | Y% | ↑/↓/→ |
| [NPS] | X | Y | ↑/↓/→ |

---

## 🟢 Now — 本季度进行中
已承诺的工作。工程、设计和 PM 完全对齐。

| Initiative（项目） | User Problem（用户问题） | Success Metric（成功指标） | Owner | Status（状态） | ETA |
|------------|-------------|----------------|-------|--------|-----|
| [功能 A] | [解决的痛点] | [指标 + 目标值] | [name] | In Dev | Week X |
| [功能 B] | [解决的痛点] | [指标 + 目标值] | [name] | In Design | Week X |
| [技术债 X] | [工程健康度] | [指标] | [name] | Scoped | Week X |

---

## 🟡 Next — 未来 1–2 个季度
方向性已承诺，开发前需要进一步定义范围。

| Initiative（项目） | Hypothesis（假设） | Expected Outcome（预期结果） | Confidence（信心） | Blocker（阻塞） |
|------------|------------|-----------------|------------|---------|
| [功能 C] | [如果我们做 X，用户会 Y] | [指标目标] | High | 无 |
| [功能 D] | [如果我们做 X，用户会 Y] | [指标目标] | Med | 需要设计 Spike |
| [功能 E] | [如果我们做 X，用户会 Y] | [指标目标] | Low | 需要用户验证 |

---

## 🔵 Later — 3–6 个月视野
战略性投注。未排期。当证据或优先级支持时推进到 Next。

| Initiative（项目） | Strategic Hypothesis（战略假设） | Signal Needed to Advance（推进所需信号） |
|------------|---------------------|--------------------------|
| [功能 F] | [为什么长期重要] | [访谈信号 / 使用阈值 / 竞争触发] |
| [功能 G] | [为什么长期重要] | [什么条件会推动它到 Next] |

---

## ❌ 我们不做的事（以及为什么）
公开说"不"可以防止重复请求并建立信任。

| Request（请求） | Source（来源） | Reason for Deferral（延后原因） | Revisit Condition（重新考虑条件） |
|---------|--------|---------------------|-------------------|
| [请求 X] | [Sales / Customer / Eng] | [原因] | [什么条件会改变这个决定] |
| [请求 Y] | [来源] | [原因] | [条件] |
```

---

### GTM 简报

```markdown
# Go-to-Market Plan: [Feature / Product Name]
**Launch Date**: [date]  **Launch Tier**: 1 (Major) / 2 (Standard) / 3 (Silent)
**PM Owner**: [name]  **Marketing DRI**: [name]  **Eng DRI**: [name]

---

## 1. What We're Launching（我们在发布什么）
[一段话：是什么、解决什么用户问题、为什么此刻重要]

---

## 2. Target Audience（目标受众）
| Segment（细分） | Size（规模） | Why They Care（为什么关注） | Channel to Reach（触达渠道） |
|---------|------|---------------|-----------------|
| Primary: [画像] | [用户数 / 占比] | [解决的痛点] | [渠道] |
| Secondary: [画像] | [用户数] | [获益] | [渠道] |
| Expansion: [新细分] | [机会] | [吸引点] | [渠道] |

---

## 3. Core Value Proposition（核心价值主张）
**One-liner**: [功能] 帮助 [画像] [达成具体成果] 而无需 [当前痛点/摩擦]。

**Messaging by audience（按受众的信息传达）**:
| Audience（受众） | Their Language for the Pain（他们描述痛点的方式） | Our Message（我们的信息） | Proof Point（佐证） |
|----------|-----------------------------|-------------|-------------|
| 终端用户（日常） | [他们如何描述问题] | [信息] | [引用 / 数据] |
| 经理 / 购买者 | [业务视角的表述] | [ROI 信息] | [案例 / 指标] |
| 内部推动者 | [他们需要什么来说服同事] | [社交证明] | [客户 logo / 成功案例] |

---

## 4. Launch Checklist（发布清单）
**Engineering**:
- [ ] Feature Flag 已为 [群组 / %] 开启，截止 [日期]
- [ ] 监控仪表盘上线，告警阈值已设置
- [ ] 回滚 Runbook 已编写并 Review

**Product**:
- [ ] 应用内公告文案已审批（Tooltip / Modal / Banner）
- [ ] Release Notes 已撰写
- [ ] 帮助中心文章已发布

**Marketing**:
- [ ] 博客文章已草拟、Review 并定时 [日期] 发布
- [ ] 发送给 [细分] 的邮件已审批——发送日期: [date]
- [ ] 社交媒体文案就绪（LinkedIn, Twitter/X）

**Sales / CS**:
- [ ] 销售赋能文档已更新，截止 [日期]
- [ ] CS 团队已培训——培训安排: [日期]
- [ ] 常见异议 FAQ 文档已发布

---

## 5. Success Criteria（成功标准）
| Timeframe（时间范围） | Metric（指标） | Target（目标值） | Owner |
|-----------|--------|--------|-------|
| 发布当天 | Error rate | < 0.5% | Eng |
| 7 天 | 功能激活率（符合条件用户的试用百分比） | ≥ 20% | PM |
| 30 天 | 功能用户留存 vs. 对照组 | +8pp | PM |
| 60 天 | 相关主题客服工单 | −30% | CS |
| 90 天 | 功能用户 NPS 变化 | +5 points | PM |

---

## 6. Rollback & Contingency（回滚与应急）
- **Rollback trigger**: Error rate > X% 或 [关键指标] 低于 [阈值]
- **Rollback owner**: [name] — 通过 [渠道] 通知
- **Communication plan if rollback（回滚时的沟通方案）**: [通知谁、使用什么模板]
```

---

### Sprint 健康快照

```markdown
# Sprint Health Snapshot — Sprint [N] — [Dates]

## Committed vs. Delivered（承诺 vs. 交付）
| Story | Points | Status（状态） | Blocker（阻塞） |
|-------|--------|--------|---------|
| [Story A] | 5 | ✅ Done | — |
| [Story B] | 8 | 🔄 In Review | 等待设计签收 |
| [Story C] | 3 | ❌ Carried | 外部 API 延迟 |

**Velocity**: [X] pts committed / [Y] pts delivered（[Z]% 完成率）
**3-sprint rolling avg（3 个 Sprint 滚动平均）**: [X] pts

## Blockers & Actions（阻塞与行动）
| Blocker（阻塞） | Impact（影响） | Owner | ETA to Resolve（预计解决时间） |
|---------|--------|-------|---------------|
| [阻塞项] | [影响范围] | [name] | [date] |

## Scope Changes This Sprint（本 Sprint 范围变更）
| Request（请求） | Source（来源） | Decision（决策） | Rationale（理由） |
|---------|--------|----------|-----------|
| [请求] | [name] | Accept / Defer | [原因] |

## Risks Entering Next Sprint（下个 Sprint 的风险）
- [风险 1]: [已有的缓解措施]
- [风险 2]: [跟踪负责人]
```
