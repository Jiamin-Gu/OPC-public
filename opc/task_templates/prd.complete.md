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
