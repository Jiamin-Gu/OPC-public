## 技术交付物

### 完整的 MCP 服务器（TypeScript）

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({
  name: "sales-crm-server",
  version: "1.0.0",
});

// ---- 工具：搜索客户 ----
server.tool(
  "search_customers",
  {
    query: z.string().describe("搜索关键词：客户名称、邮箱或电话"),
    region: z.string().optional().describe("按区域过滤，如 '华东'、'华南'"),
    limit: z.number().min(1).max(50).default(10).describe("返回结果数量上限"),
  },
  async ({ query, region, limit }) => {
    try {
      const customers = await db.customers.search({
        query,
        region,
        limit,
      });

      if (customers.length === 0) {
        return {
          content: [{
            type: "text",
            text: `未找到匹配"${query}"的客户。建议：\n` +
                  `- 检查关键词拼写\n` +
                  `- 尝试用邮箱或电话搜索\n` +
                  `- 去掉区域过滤条件扩大范围`,
          }],
        };
      }

      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            total: customers.length,
            customers: customers.map(c => ({
              id: c.id,
              name: c.name,
              email: c.email,
              region: c.region,
              last_activity: c.lastActivityAt,
            })),
          }, null, 2),
        }],
      };
    } catch (error) {
      return {
        content: [{
          type: "text",
          text: `搜索失败：${error.message}。` +
                `如果持续失败，请检查数据库连接状态。`,
        }],
        isError: true,
      };
    }
  }
);

// ---- 工具：创建工单 ----
server.tool(
  "create_support_ticket",
  {
    customer_id: z.string().describe("客户 ID，格式 CUS-XXXXX"),
    subject: z.string().min(5).max(200).describe("工单标题，5-200 字"),
    priority: z.enum(["low", "medium", "high", "urgent"])
      .describe("优先级：low=一般咨询, medium=功能问题, high=影响业务, urgent=系统不可用"),
    description: z.string().describe("问题详细描述"),
  },
  async ({ customer_id, subject, priority, description }) => {
    // 先验证客户存在
    const customer = await db.customers.findById(customer_id);
    if (!customer) {
      return {
        content: [{
          type: "text",
          text: `客户 ID "${customer_id}" 不存在。` +
                `请先用 search_customers 工具查找正确的客户 ID。`,
        }],
        isError: true,
      };
    }

    const ticket = await db.tickets.create({
      customerId: customer_id,
      subject,
      priority,
      description,
      status: "open",
      createdAt: new Date().toISOString(),
    });

    return {
      content: [{
        type: "text",
        text: JSON.stringify({
          ticket_id: ticket.id,
          status: "open",
          message: `工单已创建，编号 ${ticket.id}，已分配给 ${customer.region} 区域的值班工程师。`,
        }, null, 2),
      }],
    };
  }
);

// ---- 资源：销售仪表盘数据 ----
server.resource(
  "dashboard://sales/summary",
  "sales_dashboard",
  async () => {
    const summary = await db.metrics.getDashboardSummary();
    return {
      contents: [{
        uri: "dashboard://sales/summary",
        mimeType: "application/json",
        text: JSON.stringify(summary, null, 2),
      }],
    };
  }
);

// ---- 启动服务器 ----
const transport = new StdioServerTransport();
await server.connect(transport);
```

### Python MCP 服务器

```python
from mcp.server import Server
from mcp.types import Tool, TextContent
from pydantic import BaseModel, Field
import json

app = Server("analytics-server")


class QueryParams(BaseModel):
    sql: str = Field(description="只读 SQL 查询，禁止 INSERT/UPDATE/DELETE")
    timeout_seconds: int = Field(default=30, ge=1, le=120,
                                  description="查询超时秒数")


@app.tool("run_analytics_query")
async def run_query(params: QueryParams) -> list[TextContent]:
    """
    在只读副本上执行分析查询。
    仅支持 SELECT 语句。结果限制在 1000 行以内。
    """
    sql_upper = params.sql.strip().upper()

    # 安全检查：只允许 SELECT
    if not sql_upper.startswith("SELECT"):
        return [TextContent(
            type="text",
            text="错误：只允许 SELECT 查询。"
                 "如需修改数据，请使用对应的业务工具。"
        )]

    # 禁止危险关键字
    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE"]
    for keyword in dangerous:
        if keyword in sql_upper:
            return [TextContent(
                type="text",
                text=f"错误：查询中包含禁止关键字 {keyword}。"
                     f"此工具仅支持只读查询。"
            )]

    try:
        rows = await db.execute_readonly(
            params.sql,
            timeout=params.timeout_seconds,
            row_limit=1000,
        )
        return [TextContent(
            type="text",
            text=json.dumps({
                "row_count": len(rows),
                "rows": rows[:100],  # 返回前 100 行
                "truncated": len(rows) > 100,
                "total_available": len(rows),
            }, ensure_ascii=False, indent=2)
        )]
    except TimeoutError:
        return [TextContent(
            type="text",
            text=f"查询在 {params.timeout_seconds}s 内未完成。"
                 f"建议：添加 WHERE 条件或 LIMIT 子句缩小范围。"
        )]
```

### MCP 工具测试框架

```typescript
import { describe, it, expect } from "vitest";
import { createTestClient } from "./test-helpers.js";

describe("search_customers 工具", () => {
  const client = createTestClient();

  it("搜索到结果时返回结构化 JSON", async () => {
    const result = await client.callTool("search_customers", {
      query: "张三",
      limit: 5,
    });

    expect(result.isError).toBeFalsy();
    const data = JSON.parse(result.content[0].text);
    expect(data.customers).toBeInstanceOf(Array);
    expect(data.customers.length).toBeLessThanOrEqual(5);
    expect(data.customers[0]).toHaveProperty("id");
    expect(data.customers[0]).toHaveProperty("name");
  });

  it("无结果时返回可操作建议", async () => {
    const result = await client.callTool("search_customers", {
      query: "xyznotexist12345",
    });

    expect(result.isError).toBeFalsy();
    expect(result.content[0].text).toContain("建议");
  });

  it("拒绝超出范围的 limit", async () => {
    await expect(
      client.callTool("search_customers", { query: "test", limit: 100 })
    ).rejects.toThrow(); // Zod 校验应拦截
  });
});

describe("create_support_ticket 工具", () => {
  it("客户不存在时返回明确错误和建议", async () => {
    const result = await client.callTool("create_support_ticket", {
      customer_id: "CUS-INVALID",
      subject: "测试工单",
      priority: "low",
      description: "测试描述",
    });

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("search_customers");
  });
});
```
