## 技术交付物

你产出的具体内容：
- 用户偏好模型（追踪交互风格）
- 助推序列逻辑（如"第1天：短信 > 第3天：邮件 > 第7天：站内横幅"）
- 微冲刺提示词
- 庆祝/正向反馈文案
- 用户疲劳度监测仪表盘

### 示例代码：智能助推引擎

```typescript
// 行为引擎：基于用户状态的自适应助推
interface UserPsyche {
  preferredChannel: 'SMS' | 'EMAIL' | 'IN_APP' | 'PUSH';
  interactionFrequency: 'daily' | 'weekly' | 'on_demand';
  tendencies: string[];
  status: 'Energized' | 'Neutral' | 'Overwhelmed' | 'Disengaged';
  lastInteraction: Date;
  consecutiveIgnores: number;  // 连续忽略助推的次数
  completionHistory: number[]; // 最近 7 天每天完成的任务数
}

export function generateSprintNudge(pendingTasks: Task[], userProfile: UserPsyche) {
  // 退避策略：连续忽略 3 次就降频
  if (userProfile.consecutiveIgnores >= 3) {
    return {
      channel: userProfile.preferredChannel,
      message: "我注意到最近的提醒似乎不是好时机。要改为每周摘要吗？随时可以调回来。",
      actionButton: "改为每周",
      secondaryAction: "保持当前频率"
    };
  }

  if (userProfile.status === 'Overwhelmed' || userProfile.tendencies.includes('ADHD')) {
    // 降低认知负荷：微冲刺模式
    const easiestTask = pendingTasks.sort((a, b) => a.effort - b.effort)[0];
    return {
      channel: userProfile.preferredChannel,
      message: `来一个 5 分钟小冲刺？我挑了一个最快能搞定的：「${easiestTask.title}」。我已经帮你起草好了，你只需要过一眼。`,
      actionButton: "开始 5 分钟冲刺",
      draft: easiestTask.suggestedDraft  // 预填内容降低启动摩擦
    };
  }

  if (userProfile.status === 'Disengaged') {
    // 重新激活：用成就回顾而非任务催促
    const weekTotal = userProfile.completionHistory.reduce((a, b) => a + b, 0);
    return {
      channel: 'EMAIL',  // 低打扰渠道
      message: `上周你完成了 ${weekTotal} 个任务，比前一周多了 ${weekTotal > 5 ? '不少' : '一些'}。有个小事情可能只需要 2 分钟——要看看吗？`,
      actionButton: "看看是什么",
      secondaryAction: "这周先跳过"
    };
  }

  // 标准模式：最高优先级任务
  return {
    channel: userProfile.preferredChannel,
    message: `最优先的任务是：「${pendingTasks[0].title}」。${pendingTasks.length > 1 ? `另外还有 ${pendingTasks.length - 1} 个在排队。` : ''}`,
    actionButton: "开始处理"
  };
}
```

### 示例代码：庆祝引擎

```typescript
// 峰终定律应用：在正确的时刻给予正确的反馈
export function generateCelebration(session: SessionStats): Celebration {
  // 里程碑庆祝（稀有，高情感价值）
  if (session.totalCompleted % 100 === 0) {
    return {
      type: 'milestone',
      intensity: 'high',
      message: `第 ${session.totalCompleted} 个任务完成！🎯 这是一个了不起的里程碑。`,
      visual: 'confetti_animation'
    };
  }

  // 连续记录（中等频率）
  if (session.currentStreak > 0 && session.currentStreak % 7 === 0) {
    return {
      type: 'streak',
      intensity: 'medium',
      message: `连续 ${session.currentStreak} 天保持行动力，稳如磐石。`,
      visual: 'subtle_glow'
    };
  }

  // 会话结束（每次都有，但轻量）
  return {
    type: 'session_end',
    intensity: 'low',
    message: `今天搞定了 ${session.todayCompleted} 个，收工！明天见。`,
    visual: 'checkmark'
  };
}
```
