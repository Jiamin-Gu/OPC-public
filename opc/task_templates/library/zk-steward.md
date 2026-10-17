## 技术交付物

### 笔记与任务收尾检查清单

- Luhmann 四原则检查（表格或列表形式）。
- 归档路径和至少 2 个链接描述。
- 日志条目（意图 / 变更 / 开放循环）；可选在顶部放置 Hub 三元组（核心链接 / 标签 / 开放循环）。
- 新笔记：链接提议者输出（链接候选 + 关键词建议）；可分享性判断及归档位置。

### 文件命名

- `YYYYMMDD_简短描述.md`（或你所在地区的日期格式 + 短标识）。

### 交付物模板（任务收尾）

```markdown
## 验证
- [ ] Luhmann 四原则（原子 / 连接 / 有机 / 对话）
- [ ] 归档路径 + 至少 2 个链接
- [ ] 日志已更新
- [ ] 开放循环：已将"容易遗忘"的事项提升到开放循环文件
- [ ] 如为新笔记：链接候选 + 关键词建议 + 可分享性
```

### 日志条目示例

```markdown
### [YYYYMMDD] 简短任务标题

- **意图**：用户想要完成什么。
- **变更**：做了什么（文件、链接、决策）。
- **开放循环**：[ ] 未解决事项 1；[ ] 未解决事项 2（或"无。"）
```

### 深度阅读输出示例（结构笔记）

深度学习运行（如书籍/长视频）后，结构笔记将原子笔记串联成可导航的阅读顺序和逻辑树。以 Karpathy 的 *Deep Dive into LLMs like ChatGPT* 为例：

```markdown
---
type: Structure_Note
tags: [LLM, AI-infrastructure, deep-learning]
links: ["[[Index_LLM_Stack]]", "[[Index_AI_Observations]]"]
---

# [标题] 结构笔记

> **上下文**：何时、为何、在哪个项目下创建。
> **默认读者**：六个月后的自己——这个结构是自包含的。

## 概览（5 个问题）
1. 它解决什么问题？
2. 核心机制是什么？
3. 关键概念（3-5 个）→ 每个链接到原子笔记 [[YYYYMMDD_Atomic_Topic]]
4. 与已知方法相比如何？
5. 一句话总结（Feynman 测试）

## 逻辑树
命题 1：……
├─ [[Atomic_Note_A]]
├─ [[Atomic_Note_B]]
└─ [[Atomic_Note_C]]
命题 2：……
└─ [[Atomic_Note_D]]

## 阅读顺序
1. **[[Atomic_Note_A]]** — 原因：……
2. **[[Atomic_Note_B]]** — 原因：……
```

配套输出：执行计划（`YYYYMMDD_01_[书名]_执行计划.md`）、原子/方法笔记、主题索引笔记、工作流审计报告。参见 [zk-steward-companion](https://github.com/mikonos/zk-steward-companion) 中的 **deep-learning** 技能。
