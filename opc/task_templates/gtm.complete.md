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
