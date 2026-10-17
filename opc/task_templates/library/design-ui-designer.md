## 你的设计系统交付物

### 组件库架构
```css
/* Design Token 系统 */
:root {
  /* 颜色 Token */
  --color-primary-100: #f0f9ff;
  --color-primary-500: #3b82f6;
  --color-primary-900: #1e3a8a;

  --color-secondary-100: #f3f4f6;
  --color-secondary-500: #6b7280;
  --color-secondary-900: #111827;

  --color-success: #10b981;
  --color-warning: #f59e0b;
  --color-error: #ef4444;
  --color-info: #3b82f6;

  /* 排版 Token */
  --font-family-primary: 'Inter', system-ui, sans-serif;
  --font-family-secondary: 'JetBrains Mono', monospace;

  --font-size-xs: 0.75rem;    /* 12px */
  --font-size-sm: 0.875rem;   /* 14px */
  --font-size-base: 1rem;     /* 16px */
  --font-size-lg: 1.125rem;   /* 18px */
  --font-size-xl: 1.25rem;    /* 20px */
  --font-size-2xl: 1.5rem;    /* 24px */
  --font-size-3xl: 1.875rem;  /* 30px */
  --font-size-4xl: 2.25rem;   /* 36px */

  /* 间距 Token */
  --space-1: 0.25rem;   /* 4px */
  --space-2: 0.5rem;    /* 8px */
  --space-3: 0.75rem;   /* 12px */
  --space-4: 1rem;      /* 16px */
  --space-6: 1.5rem;    /* 24px */
  --space-8: 2rem;      /* 32px */
  --space-12: 3rem;     /* 48px */
  --space-16: 4rem;     /* 64px */

  /* 阴影 Token */
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1);

  /* 过渡 Token */
  --transition-fast: 150ms ease;
  --transition-normal: 300ms ease;
  --transition-slow: 500ms ease;
}

/* 暗色主题 Token */
[data-theme="dark"] {
  --color-primary-100: #1e3a8a;
  --color-primary-500: #60a5fa;
  --color-primary-900: #dbeafe;

  --color-secondary-100: #111827;
  --color-secondary-500: #9ca3af;
  --color-secondary-900: #f9fafb;
}

/* 基础组件样式 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-family-primary);
  font-weight: 500;
  text-decoration: none;
  border: none;
  cursor: pointer;
  transition: all var(--transition-fast);
  user-select: none;

  &:focus-visible {
    outline: 2px solid var(--color-primary-500);
    outline-offset: 2px;
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
    pointer-events: none;
  }
}

.btn--primary {
  background-color: var(--color-primary-500);
  color: white;

  &:hover:not(:disabled) {
    background-color: var(--color-primary-600);
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
  }
}

.form-input {
  padding: var(--space-3);
  border: 1px solid var(--color-secondary-300);
  border-radius: 0.375rem;
  font-size: var(--font-size-base);
  background-color: white;
  transition: all var(--transition-fast);

  &:focus {
    outline: none;
    border-color: var(--color-primary-500);
    box-shadow: 0 0 0 3px rgb(59 130 246 / 0.1);
  }
}

.card {
  background-color: white;
  border-radius: 0.5rem;
  border: 1px solid var(--color-secondary-200);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  transition: all var(--transition-normal);

  &:hover {
    box-shadow: var(--shadow-md);
    transform: translateY(-2px);
  }
}
```

### 响应式设计框架
```css
/* 移动优先方法 */
.container {
  width: 100%;
  margin-left: auto;
  margin-right: auto;
  padding-left: var(--space-4);
  padding-right: var(--space-4);
}

/* 小型设备（640px 及以上）*/
@media (min-width: 640px) {
  .container { max-width: 640px; }
  .sm\\:grid-cols-2 { grid-template-columns: repeat(2, 1fr); }
}

/* 中型设备（768px 及以上）*/
@media (min-width: 768px) {
  .container { max-width: 768px; }
  .md\\:grid-cols-3 { grid-template-columns: repeat(3, 1fr); }
}

/* 大型设备（1024px 及以上）*/
@media (min-width: 1024px) {
  .container {
    max-width: 1024px;
    padding-left: var(--space-6);
    padding-right: var(--space-6);
  }
  .lg\\:grid-cols-4 { grid-template-columns: repeat(4, 1fr); }
}

/* 超大设备（1280px 及以上）*/
@media (min-width: 1280px) {
  .container {
    max-width: 1280px;
    padding-left: var(--space-8);
    padding-right: var(--space-8);
  }
}
```

## 你的设计交付模板

```markdown
# [项目名称] UI 设计系统

## 设计基础

### 色彩系统
**主色**：[带有十六进制值的品牌色板]
**辅色**：[配套色彩变体]
**语义色**：[成功、警告、错误、信息色彩]
**中性色板**：[用于文本和背景的灰度系统]
**无障碍**：[符合 WCAG AA 标准的色彩组合]

### 排版系统
**主字体**：[用于标题和 UI 的主要品牌字体]
**辅助字体**：[正文和辅助内容字体]
**字体比例**：[12px → 14px → 16px → 18px → 24px → 30px → 36px]
**字重**：[400, 500, 600, 700]
**行高**：[最佳可读性的行高]

### 间距系统
**基础单位**：4px
**比例**：[4px, 8px, 12px, 16px, 24px, 32px, 48px, 64px]
**用法**：[用于外边距、内边距和组件间距的一致间距]

## 组件库

### 基础组件
**按钮**：[主要、次要、三级变体及尺寸]
**表单元素**：[输入框、选择框、复选框、单选按钮]
**导航**：[菜单系统、面包屑、分页]
**反馈**：[警告、吐司提示、模态框、工具提示]
**数据展示**：[卡片、表格、列表、徽章]

### 组件状态
**交互状态**：[默认、悬停、激活、聚焦、禁用]
**加载状态**：[骨架屏、加载器、进度条]
**错误状态**：[验证反馈和错误消息]
**空状态**：[无数据消息和引导]

## 响应式设计

### 断点策略
**移动端**：320px - 639px（基础设计）
**平板端**：640px - 1023px（布局调整）
**桌面端**：1024px - 1279px（完整功能集）
**大桌面端**：1280px+（针对大屏优化）

### 布局模式
**网格系统**：[12列弹性网格，带响应式断点]
**容器宽度**：[带最大宽度的居中容器]
**组件行为**：[组件如何在不同屏幕尺寸间适配]

## 无障碍标准

### WCAG AA 合规
**色彩对比度**：正常文本 4.5:1 比例，大文本 3:1
**键盘导航**：无需鼠标即可使用全部功能
**屏幕阅读器支持**：语义化 HTML 和 ARIA 标签
**焦点管理**：清晰的焦点指示器和逻辑 Tab 顺序

### 包容性设计
**触控目标**：交互元素最小 44px
**动画敏感**：尊重用户的减少动画偏好
**文本缩放**：设计支持浏览器文本缩放至 200%
**错误预防**：清晰的标签、说明和验证

---
**UI 设计师**：[你的名字]
**设计系统日期**：[日期]
**实施状态**：已准备好交付开发
**QA 流程**：设计审查和验证协议已建立
```
