## 技术交付物

### 高质量 README 模板

```markdown
# 项目名称

> 一句话描述这个项目做什么以及为什么重要。

[![npm version](https://badge.fury.io/js/your-package.svg)](https://badge.fury.io/js/your-package)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 为什么需要这个

<!-- 2-3 句话：这个项目解决什么痛点。不是功能列表——是痛点。 -->

## 快速开始

<!-- 最短路径跑通。不讲理论。 -->

```bash
npm install your-package
```

```javascript
import { doTheThing } from 'your-package';

const result = await doTheThing({ input: 'hello' });
console.log(result); // "hello world"
```

## 安装

<!-- 完整的安装说明，包括前置条件 -->

**前置条件**：Node.js 18+，npm 9+

```bash
npm install your-package
# 或
yarn add your-package
```

## 使用

### 基础用法

<!-- 最常见的使用场景，完整可运行 -->

### 配置项

| 选项 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `timeout` | `number` | `5000` | 请求超时时间（毫秒） |
| `retries` | `number` | `3` | 失败重试次数 |

### 高级用法

<!-- 第二常见的使用场景 -->

## API 参考

查看 [完整 API 参考 ->](https://docs.yourproject.com/api)

## 参与贡献

查看 [CONTRIBUTING.md](CONTRIBUTING.md)

## 许可证

MIT © [Your Name](https://github.com/yourname)
```

### OpenAPI 文档示例

```yaml
# openapi.yml - 文档优先的 API 设计
openapi: 3.1.0
info:
  title: Orders API
  version: 2.0.0
  description: |
    Orders API 允许你创建、查询、更新和取消订单。

    ## 认证
    所有请求需要在 `Authorization` 头中携带 Bearer token。
    从[管理后台](https://app.example.com/settings/api)获取你的 API key。

    ## 限流
    每个 API key 限制 100 次/分钟。每个响应都包含限流相关的 header。
    详见[限流指南](https://docs.example.com/rate-limits)。

    ## 版本管理
    当前为 API v2。如果从 v1 升级，请查看[迁移指南](https://docs.example.com/v1-to-v2)。

paths:
  /orders:
    post:
      summary: 创建订单
      description: |
        创建一个新订单。订单初始状态为 `pending`，直到支付确认。
        订阅 `order.confirmed` webhook 以获取订单就绪通知。
      operationId: createOrder
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateOrderRequest'
            examples:
              standard_order:
                summary: 标准商品订单
                value:
                  customer_id: "cust_abc123"
                  items:
                    - product_id: "prod_xyz"
                      quantity: 2
                  shipping_address:
                    line1: "123 Main St"
                    city: "Seattle"
                    state: "WA"
                    postal_code: "98101"
                    country: "US"
      responses:
        '201':
          description: 订单创建成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Order'
        '400':
          description: 请求无效——查看 `error.code` 了解详情
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
              examples:
                missing_items:
                  value:
                    error:
                      code: "VALIDATION_ERROR"
                      message: "items 为必填项，且必须包含至少一个商品"
                      field: "items"
        '429':
          description: 超过限流限制
          headers:
            Retry-After:
              description: 限流重置前的剩余秒数
              schema:
                type: integer
```

### 教程结构模板

```markdown
# 教程：[目标成果] [预估时间]

**你将构建**：简要描述最终成果，附截图或演示链接。

**你将学到**：
- 概念 A
- 概念 B
- 概念 C

**前置条件**：
- [ ] 已安装 [工具 X](链接)（版本 Y+）
- [ ] 了解 [概念] 的基础知识
- [ ] 拥有 [服务] 的账号（[免费注册](链接)）

---

## 第 1 步：初始化项目

<!-- 先告诉读者要做什么以及为什么，然后再说怎么做 -->
首先创建一个新的项目目录并初始化。我们使用独立目录，
方便后续清理。

```bash
mkdir my-project && cd my-project
npm init -y
```

你应该看到如下输出：
```
Wrote to /path/to/my-project/package.json: { ... }
```

> **提示**：如果遇到 `EACCES` 错误，[修复 npm 权限](链接) 或使用 `npx`。

## 第 2 步：安装依赖

<!-- 每步只做一件事 -->

## 第 N 步：你构建了什么

<!-- 庆祝！总结成果。 -->

你构建了一个 [描述]。以下是你学到的：
- **概念 A**：工作原理和使用场景
- **概念 B**：核心要点

## 下一步

- [进阶教程：添加认证](链接)
- [参考：完整 API 文档](链接)
- [示例：生产级完整版本](链接)
```

### Docusaurus 配置

```javascript
// docusaurus.config.js
const config = {
  title: 'Project Docs',
  tagline: '构建 Project 所需的一切',
  url: 'https://docs.yourproject.com',
  baseUrl: '/',
  trailingSlash: false,

  presets: [['classic', {
    docs: {
      sidebarPath: require.resolve('./sidebars.js'),
      editUrl: 'https://github.com/org/repo/edit/main/docs/',
      showLastUpdateAuthor: true,
      showLastUpdateTime: true,
      versions: {
        current: { label: 'Next (未发布)', path: 'next' },
      },
    },
    blog: false,
    theme: { customCss: require.resolve('./src/css/custom.css') },
  }]],

  plugins: [
    ['@docusaurus/plugin-content-docs', {
      id: 'api',
      path: 'api',
      routeBasePath: 'api',
      sidebarPath: require.resolve('./sidebarsApi.js'),
    }],
    [require.resolve('@cmfcmf/docusaurus-search-local'), {
      indexDocs: true,
      language: 'en',
    }],
  ],

  themeConfig: {
    navbar: {
      items: [
        { type: 'doc', docId: 'intro', label: '指南' },
        { to: '/api', label: 'API 参考' },
        { type: 'docsVersionDropdown' },
        { href: 'https://github.com/org/repo', label: 'GitHub', position: 'right' },
      ],
    },
    algolia: {
      appId: 'YOUR_APP_ID',
      apiKey: 'YOUR_SEARCH_API_KEY',
      indexName: 'your_docs',
    },
  },
};
```
