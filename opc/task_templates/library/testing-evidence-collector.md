## 技术交付物

### Bug 报告模板

```markdown
# Bug Report: [简洁描述问题]

## 基本信息
- **严重程度**：P0 / P1 / P2 / P3
- **所属模块**：[模块名]
- **发现版本**：v2.3.1 (build 456)
- **环境**：
  - OS: macOS 14.2 / iOS 17.1 / Windows 11
  - 浏览器: Chrome 120.0.6099.71
  - 设备: iPhone 15 Pro
  - 网络: WiFi / 4G / 弱网

## 复现步骤
### 前置条件
1. 使用已注册的免费用户账号登录
2. 账号内已有至少 3 个项目

### 操作步骤
1. 进入"项目列表"页面
2. 点击右上角"筛选"按钮
3. 选择标签 = "进行中"
4. 点击"应用筛选"
5. 等待 3 秒

### 实际结果
页面显示空白，控制台报错：
`TypeError: Cannot read property 'map' of undefined at ProjectList.tsx:45`

### 期望结果
显示标签为"进行中"的项目列表（测试数据中有 2 个）

## 复现概率
- 必现（10/10 次）

## 证据
### 截图
[附带标注的截图]

### 控制台日志
```
Uncaught TypeError: Cannot read property 'map' of undefined
    at ProjectList (ProjectList.tsx:45:23)
    at renderWithHooks (react-dom.development.js:14985)
```

### 网络请求
```
GET /api/v1/projects?tag=in_progress
Status: 200
Response: { "data": null, "pagination": {...} }
```
注意：data 字段为 null 而非空数组，前端未处理 null case。

## 影响范围
- 所有使用标签筛选功能的用户
- 不影响不使用筛选的场景
```
