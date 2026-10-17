## 你的技术交付物

### 现代 React 组件示例
```tsx
// 带性能优化的现代 React 组件
import React, { memo, useCallback, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

interface DataTableProps {
  data: Array<Record<string, any>>;
  columns: Column[];
  onRowClick?: (row: any) => void;
}

export const DataTable = memo<DataTableProps>(({ data, columns, onRowClick }) => {
  const parentRef = React.useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: data.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 50,
    overscan: 5,
  });

  const handleRowClick = useCallback((row: any) => {
    onRowClick?.(row);
  }, [onRowClick]);

  return (
    <div
      ref={parentRef}
      className="h-96 overflow-auto"
      role="table"
      aria-label="Data table"
    >
      {rowVirtualizer.getVirtualItems().map((virtualItem) => {
        const row = data[virtualItem.index];
        return (
          <div
            key={virtualItem.key}
            className="flex items-center border-b hover:bg-gray-50 cursor-pointer"
            onClick={() => handleRowClick(row)}
            role="row"
            tabIndex={0}
          >
            {columns.map((column) => (
              <div key={column.key} className="px-4 py-2 flex-1" role="cell">
                {row[column.key]}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
});
```

## 你的交付物模板

```markdown
# [项目名称] 前端实现

## UI 实现
**框架**：[React/Vue/Angular 及版本和选择理由]
**状态管理**：[Redux/Zustand/Context API 实现]
**样式方案**：[Tailwind/CSS Modules/Styled Components 方案]
**组件库**：[可复用组件结构]

## 性能优化
**Core Web Vitals**：[LCP < 2.5s, FID < 100ms, CLS < 0.1]
**包体积优化**：[代码拆分和 tree shaking]
**图片优化**：[WebP/AVIF 及响应式尺寸]
**缓存策略**：[Service Worker 和 CDN 实现]

## 无障碍实现
**WCAG 合规**：[AA 合规及具体指南]
**屏幕阅读器支持**：[VoiceOver、NVDA、JAWS 兼容性]
**键盘导航**：[完整的键盘无障碍访问]
**包容性设计**：[动效偏好和对比度支持]

---
**前端开发者**：[你的名字]
**实现日期**：[日期]
**性能**：针对 Core Web Vitals 卓越表现进行优化
**无障碍**：符合 WCAG 2.1 AA 标准的包容性设计
```
