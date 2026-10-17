## 技术交付物

### WebMCP 就绪评分卡

```markdown
# WebMCP 就绪审计：[站点/产品名称]
## 日期：[YYYY-MM-DD]

| 任务流程             | 可发现 | 可发起 | 可完成 | 中断点              | 优先级 |
|-----------------------|--------|--------|--------|---------------------|--------|
| 预约                  | ✅ 是   | ⚠️ 部分 | ❌ 否   | 步骤 3：日期选择器   | P1     |
| 提交线索表单          | ❌ 否   | ❌ 否   | ❌ 否   | 未声明              | P1     |
| 创建账户              | ✅ 是   | ✅ 是   | ✅ 是   | —                   | 已完成 |
| 订阅通讯              | ❌ 否   | ❌ 否   | ❌ 否   | 未声明              | P2     |
| 下载资源              | ✅ 是   | ✅ 是   | ⚠️ 部分 | 门槛：需要邮箱       | P2     |

**总体任务完成率**：1/5（20%）
**目标（30 天）**：4/5（80%）
```

### 声明式 WebMCP 标记模板

```html
<!-- 修改前：标准联系表单——智能体完全不知道这是做什么的 -->
<form action="/contact" method="POST">
  <input type="text" name="name" placeholder="Your name">
  <input type="email" name="email" placeholder="Email address">
  <textarea name="message" placeholder="Your message"></textarea>
  <button type="submit">Send</button>
</form>

<!-- 修改后：WebMCP 声明式——智能体清楚知道有哪些可用操作 -->
<form
  action="/contact"
  method="POST"
  data-mcp-action="send-inquiry"
  data-mcp-description="Send a business inquiry to the team. Provide your name, email address, and a description of your project or question."
  data-mcp-params='{"required": ["name", "email", "message"], "optional": []}'
>
  <input
    type="text"
    name="name"
    data-mcp-param="name"
    data-mcp-description="Full name of the person sending the inquiry"
  >
  <input
    type="email"
    name="email"
    data-mcp-param="email"
    data-mcp-description="Email address for reply"
  >
  <textarea
    name="message"
    data-mcp-param="message"
    data-mcp-description="Description of the project, question, or request"
  ></textarea>
  <button type="submit">Send</button>
</form>
```

### 命令式 WebMCP 注册模板

```javascript
// 用于动态操作（依赖用户状态、上下文敏感或 SPA 驱动的流程）
// 需要浏览器支持 navigator.mcpActions（Chrome/Edge 2026+）

if ('mcpActions' in navigator) {
  // 注册一个动态预约操作，仅在有可用库存时才有意义
  navigator.mcpActions.register({
    id: 'book-appointment',
    name: 'Book Appointment',
    description: 'Schedule a consultation appointment. Available slots are shown in real time. Provide preferred date range and contact details.',
    parameters: {
      type: 'object',
      required: ['preferred_date', 'preferred_time', 'name', 'email'],
      properties: {
        preferred_date: {
          type: 'string',
          format: 'date',
          description: 'Preferred appointment date in YYYY-MM-DD format'
        },
        preferred_time: {
          type: 'string',
          enum: ['morning', 'afternoon', 'evening'],
          description: 'Preferred time of day'
        },
        name: {
          type: 'string',
          description: 'Full name of the person booking'
        },
        email: {
          type: 'string',
          format: 'email',
          description: 'Email address for confirmation'
        }
      }
    },
    handler: async (params) => {
      const response = await fetch('/api/bookings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
      });
      const result = await response.json();
      return {
        success: response.ok,
        confirmation_id: result.booking_id,
        message: response.ok
          ? `Appointment booked for ${params.preferred_date}. Confirmation sent to ${params.email}.`
          : `Booking failed: ${result.error}`
      };
    }
  });
}
```

### MCP Actions 发现端点

```json
// 发布地址：https://yourdomain.com/mcp-actions.json
// 在 <head> 中引用：<link rel="mcp-actions" href="/mcp-actions.json">

{
  "version": "1.0",
  "site": "https://yourdomain.com",
  "actions": [
    {
      "id": "send-inquiry",
      "name": "Send Inquiry",
      "description": "Send a business inquiry to the team",
      "method": "declarative",
      "endpoint": "/contact",
      "parameters": {
        "required": ["name", "email", "message"]
      }
    },
    {
      "id": "book-appointment",
      "name": "Book Appointment",
      "description": "Schedule a consultation appointment",
      "method": "imperative",
      "availability": "dynamic"
    }
  ]
}
```

### 智能体摩擦点地图模板

```markdown
# 智能体摩擦点地图：[任务流程名称]
## 测试智能体：[智能体名称] | 日期：[YYYY-MM-DD]

步骤 1：着陆页 → [状态：✅ 通过 / ⚠️ 降级 / ❌ 失败]
- 智能体操作：导航至 /book
- 观察：通过声明式标记发现操作
- 问题：无

步骤 2：日期选择 → [状态：❌ 失败]
- 智能体操作：尝试与日历组件交互
- 观察：JavaScript 日期选择器无法通过 MCP 参数访问
- 问题：自定义 JS 日历没有 `data-mcp-param` 属性
- 修复：在隐藏 input 上添加 data-mcp-param="appointment_date"；将 JS 日历替换为 <input type="date">

步骤 3：表单提交 → [状态：N/A——被步骤 2 阻断]
```
