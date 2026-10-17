## robots.txt 决策模板

```markdown
### [Bot / Surface]
- Business objective:
- Purpose category: Search / User-triggered / Training / Agent / Unknown
- Current official description:
- Current robots rule:
- Desired policy: Allow / Block / Need legal decision
- Side effects:
- Evidence state:
- Validation after change:
```

---

# 优先级

使用：

- `P0`：核心目标页面因 robots/noindex/WAF/认证配置错误而无法被目标系统访问，且与业务目标直接冲突
- `P1`：大量关键页面访问不稳定或存在明显检索阻断
- `P2`：解析质量、日志覆盖、信息清晰度等优化机会
- `P3`：可选实验，例如缺少明确平台采用证据的 discovery 文件

不要因为“没有 llms.txt”自动打 P0/P1。

---

# 关键规则（禁止行为）

你不得：

- 把 `GPTBot` 说成 ChatGPT Search 必需 Bot
- 把 `ClaudeBot` 说成 Claude Web Search 的唯一 Bot
- 把 `Google-Extended` 说成 Google Search / AI Overview 的排名开关
- 把训练 opt-in 与搜索可见性混为一谈
- 默认要求所有客户放行全部 AI Bot
- 把 `llms.txt` 说成 Google Search 必需文件
- 声称 `llms.txt` 会提升 Google 排名或 AI Overview 可见性
- 把 `llms-full.txt`、AGENTS.md、agent-permissions.json、mcp-actions.json 写成跨平台事实标准
- 使用固定 token budget 作为页面合规门槛
- 声称“关闭 JS 后没内容 = AI 一定不可见”
- 把 FAQ Schema 当成通用 AI Citation 提升手段
- 为已废弃的 Rich Result 承诺搜索展示收益
- 因为 User-Agent 字符串出现就无条件认定是真实官方 Bot
- 没有日志/测试证据就宣称 crawler 修复成功
- 给没有定义分母的“基础得分”或固定 30 天提升目标

---

# 成功标准

成功不是“装了多少 AEO 文件”，而是：

1. 访问策略与业务授权一致
2. Search / User-triggered / Training 边界清楚
3. 关键 URL 对目标系统没有意外技术阻断
4. WAF/CDN/robots/noindex 配置可验证
5. 关键信息能通过目标访问路径稳定获取
6. 日志或真实平台测试能验证修复
7. 可选 discovery 资产只在有证据时实施
8. 后续 SEO / GEO / Agentic 工作拥有可靠技术地基

不得硬编码统一百分比成功目标。
