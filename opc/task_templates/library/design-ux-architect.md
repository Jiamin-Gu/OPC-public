## 技术交付物

### CSS 设计系统基础

```css
/* CSS 架构示例 */
:root {
  /* 亮色主题颜色 - 用项目规格中的实际颜色 */
  --bg-primary: [spec-light-bg];
  --bg-secondary: [spec-light-secondary];
  --text-primary: [spec-light-text];
  --text-secondary: [spec-light-text-muted];
  --border-color: [spec-light-border];

  /* 品牌色 - 来自项目规格 */
  --primary-color: [spec-primary];
  --secondary-color: [spec-secondary];
  --accent-color: [spec-accent];

  /* 字号阶梯 */
  --text-xs: 0.75rem;    /* 12px */
  --text-sm: 0.875rem;   /* 14px */
  --text-base: 1rem;     /* 16px */
  --text-lg: 1.125rem;   /* 18px */
  --text-xl: 1.25rem;    /* 20px */
  --text-2xl: 1.5rem;    /* 24px */
  --text-3xl: 1.875rem;  /* 30px */

  /* 间距系统 */
  --space-1: 0.25rem;    /* 4px */
  --space-2: 0.5rem;     /* 8px */
  --space-4: 1rem;       /* 16px */
  --space-6: 1.5rem;     /* 24px */
  --space-8: 2rem;       /* 32px */
  --space-12: 3rem;      /* 48px */
  --space-16: 4rem;      /* 64px */

  /* 布局系统 */
  --container-sm: 640px;
  --container-md: 768px;
  --container-lg: 1024px;
  --container-xl: 1280px;
}

/* 暗色主题 - 用项目规格中的暗色颜色 */
[data-theme="dark"] {
  --bg-primary: [spec-dark-bg];
  --bg-secondary: [spec-dark-secondary];
  --text-primary: [spec-dark-text];
  --text-secondary: [spec-dark-text-muted];
  --border-color: [spec-dark-border];
}

/* 跟随系统主题偏好 */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg-primary: [spec-dark-bg];
    --bg-secondary: [spec-dark-secondary];
    --text-primary: [spec-dark-text];
    --text-secondary: [spec-dark-text-muted];
    --border-color: [spec-dark-border];
  }
}

/* 基础排版 */
.text-heading-1 {
  font-size: var(--text-3xl);
  font-weight: 700;
  line-height: 1.2;
  margin-bottom: var(--space-6);
}

/* 布局组件 */
.container {
  width: 100%;
  max-width: var(--container-lg);
  margin: 0 auto;
  padding: 0 var(--space-4);
}

.grid-2-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-8);
}

@media (max-width: 768px) {
  .grid-2-col {
    grid-template-columns: 1fr;
    gap: var(--space-6);
  }
}

/* 主题切换组件 */
.theme-toggle {
  position: relative;
  display: inline-flex;
  align-items: center;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 24px;
  padding: 4px;
  transition: all 0.3s ease;
}

.theme-toggle-option {
  padding: 8px 12px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s ease;
}

.theme-toggle-option.active {
  background: var(--primary-500);
  color: white;
}

/* 全局主题基础样式 */
body {
  background-color: var(--bg-primary);
  color: var(--text-primary);
  transition: background-color 0.3s ease, color 0.3s ease;
}
```

### 布局框架规格

```markdown
## 布局架构

### 容器系统
- **手机**：满宽，左右 16px 内边距
- **平板**：768px 最大宽度，居中
- **桌面**：1024px 最大宽度，居中
- **大屏**：1280px 最大宽度，居中

### 网格模式
- **Hero 区域**：满屏高度，内容居中
- **内容网格**：桌面端双栏，手机端单栏
- **卡片布局**：CSS Grid + auto-fit，最小 300px
- **侧边栏布局**：主区域 2fr，侧栏 1fr，带间距

### 组件层级
1. **布局组件**：容器、网格、区块
2. **内容组件**：卡片、文章、媒体
3. **交互组件**：按钮、表单、导航
4. **工具组件**：间距、排版、颜色
```

### 主题切换 JavaScript 规格

```javascript
// 主题管理系统
class ThemeManager {
  constructor() {
    this.currentTheme = this.getStoredTheme() || this.getSystemTheme();
    this.applyTheme(this.currentTheme);
    this.initializeToggle();
  }

  getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  getStoredTheme() {
    return localStorage.getItem('theme');
  }

  applyTheme(theme) {
    if (theme === 'system') {
      // 跟随系统时移除手动设置
      document.documentElement.removeAttribute('data-theme');
      localStorage.removeItem('theme');
    } else {
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem('theme', theme);
    }
    this.currentTheme = theme;
    this.updateToggleUI();
  }

  initializeToggle() {
    const toggle = document.querySelector('.theme-toggle');
    if (toggle) {
      toggle.addEventListener('click', (e) => {
        if (e.target.matches('.theme-toggle-option')) {
          const newTheme = e.target.dataset.theme;
          this.applyTheme(newTheme);
        }
      });
    }
  }

  updateToggleUI() {
    // 更新切换按钮的激活状态
    const options = document.querySelectorAll('.theme-toggle-option');
    options.forEach(option => {
      option.classList.toggle('active', option.dataset.theme === this.currentTheme);
    });
  }
}

// 页面加载后初始化主题管理
document.addEventListener('DOMContentLoaded', () => {
  new ThemeManager();
});
```

### UX 结构规格

```markdown
## 信息架构

### 页面层级
1. **主导航**：最多 5-7 个主要板块
2. **主题切换**：始终在头部/导航栏可见
3. **内容区块**：视觉上有清晰分隔，逻辑连贯
4. **行动召唤位置**：首屏上方、区块尾部、页脚
5. **辅助内容**：用户评价、功能介绍、联系方式

### 视觉权重体系
- **H1**：页面主标题，最大字号，最高对比度
- **H2**：区块标题，次要层级
- **H3**：子区块标题，第三层级
- **正文**：可读字号，足够对比度，舒适行高
- **行动召唤**：高对比度，足够大的点击区域，明确的文案
- **主题切换**：不抢眼但随时可用，位置固定

### 交互模式
- **导航**：平滑滚动到对应区块，当前状态高亮
- **主题切换**：切换后立即有视觉反馈，记住用户偏好
- **表单**：清晰的标签，实时校验反馈，进度指示
- **按钮**：悬停状态，焦点指示，加载状态
- **卡片**：微妙的悬停效果，明确的可点击区域
```

## 交付模板

```markdown
# [项目名] 技术架构与 UX 基础

## CSS 架构

### 设计系统变量
**文件**：`css/design-system.css`
- 语义化命名的色彩体系
- 一致比例的字号阶梯
- 基于 4px 网格的间距系统
- 可复用的组件 Token

### 布局框架
**文件**：`css/layout.css`
- 响应式容器系统
- 常用网格模式
- Flexbox 对齐工具
- 响应式工具类和断点

## UX 结构

### 信息架构
**页面流**：[内容的逻辑递进顺序]
**导航策略**：[菜单结构和用户路径]
**内容层级**：[H1 > H2 > H3 结构和视觉权重]

### 响应式策略
**Mobile First**：[320px+ 基础设计]
**平板**：[768px+ 增强]
**桌面**：[1024px+ 完整功能]
**大屏**：[1280px+ 优化]

### 无障碍基础
**键盘导航**：[Tab 顺序和焦点管理]
**屏幕阅读器**：[语义化 HTML 和 ARIA 标签]
**颜色对比度**：[最低满足 WCAG 2.1 AA]

## 开发实现指南

### 实现优先级
1. **基础搭建**：实现设计系统变量
2. **布局结构**：创建响应式容器和网格系统
3. **组件底层**：搭建可复用组件模板
4. **内容集成**：用正确的层级填充实际内容
5. **交互打磨**：实现悬停状态和动画效果
```

### 主题切换 HTML 模板

```html
<!-- 主题切换组件（放在头部/导航栏中） -->
<div class="theme-toggle" role="radiogroup" aria-label="主题选择">
  <button class="theme-toggle-option" data-theme="light" role="radio" aria-checked="false">
    Light
  </button>
  <button class="theme-toggle-option" data-theme="dark" role="radio" aria-checked="false">
    Dark
  </button>
  <button class="theme-toggle-option" data-theme="system" role="radio" aria-checked="true">
    System
  </button>
</div>
```

### 文件结构

```
css/
├── design-system.css    # 变量和 Token（含主题系统）
├── layout.css          # 网格和容器系统
├── components.css      # 可复用组件样式（含主题切换）
├── utilities.css       # 工具类
└── main.css            # 项目特定覆盖样式
js/
├── theme-manager.js     # 主题切换功能
└── main.js             # 项目特定 JavaScript
```

### 实现备注

**CSS 方法论**：[BEM、utility-first、或组件化方案]
**浏览器支持**：[现代浏览器，老浏览器优雅降级]
**性能**：[关键 CSS 内联，懒加载策略]
