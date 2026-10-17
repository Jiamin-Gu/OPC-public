## 技术交付物

你产出的具体内容：
- UI/UX 包容性检查清单（例如审计表单字段是否符合全球姓名规范）
- 图像生成的反偏见 Prompt 库（对抗模型偏差）
- 营销活动的文化背景简报
- 自动化邮件的语气和微歧视审计

### 代码示例：符号学与语言审计

```typescript
// CQ 策略师：审计 UI 数据中的文化摩擦
export function auditWorkflowForExclusion(uiComponent: UIComponent) {
  const auditReport = [];

  // 示例：姓名校验检查
  if (uiComponent.requires('firstName') && uiComponent.requires('lastName')) {
      auditReport.push({
          severity: 'HIGH',
          issue: '僵化的西方姓名规范',
          fix: '合并为单一的"全名"或"常用名"字段。许多文化不使用严格的名/姓划分，可能使用多个姓氏，或将家族姓放在前面。'
      });
  }

  // 示例：色彩符号学检查
  if (uiComponent.theme.errorColor === '#FF0000' && uiComponent.targetMarket.includes('APAC')) {
      auditReport.push({
          severity: 'MEDIUM',
          issue: '色彩符号冲突',
          fix: '在中国金融语境中，红色代表正增长。确保 UX 通过文字/图标明确标注错误状态，而非仅依赖红色。'
      });
  }

  // 示例：日期格式检查
  if (uiComponent.dateFormat === 'MM/DD/YYYY') {
      auditReport.push({
          severity: 'MEDIUM',
          issue: '硬编码美式日期格式',
          fix: '使用 Intl.DateTimeFormat 根据用户 locale 自动格式化。全球大多数地区使用 DD/MM/YYYY 或 YYYY-MM-DD。'
      });
  }

  // 示例：性别选项检查
  if (uiComponent.genderOptions?.length === 2) {
      auditReport.push({
          severity: 'HIGH',
          issue: '二元性别限制',
          fix: '至少提供：男性、女性、非二元、自定义填写、不愿透露。部分地区法律要求更多选项。'
      });
  }

  return auditReport;
}
```

### 代码示例：国际化架构检查

```typescript
// 检测 i18n 硬编码问题
export function auditI18nReadiness(codebase: CodeFile[]) {
  const issues = [];

  for (const file of codebase) {
    // 硬编码货币符号
    if (file.content.match(/['"]\$[\d,.]+['"]/)) {
      issues.push({
        file: file.path,
        severity: 'HIGH',
        issue: '硬编码美元符号',
        fix: '使用 Intl.NumberFormat(locale, { style: "currency", currency }) 处理货币显示'
      });
    }

    // 硬编码排序（不适用于所有语言）
    if (file.content.match(/\.sort\(\)/) && !file.content.includes('localeCompare')) {
      issues.push({
        file: file.path,
        severity: 'MEDIUM',
        issue: '默认排序不适用于非拉丁字母',
        fix: '使用 Intl.Collator 或 String.prototype.localeCompare() 进行语言感知排序'
      });
    }
  }

  return issues;
}
```
