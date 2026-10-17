## 技术交付物

### 分支与提交决策矩阵

| 变更类型 | 分支规范 | 提交规范 | 适用场景 |
|----------|---------|---------|---------|
| 功能 | `feature/JIRA-214-add-sso-login` | `✨ JIRA-214: add SSO login flow` | 新的产品或平台能力 |
| Bug修复 | `bugfix/JIRA-315-fix-token-refresh` | `🐛 JIRA-315: fix token refresh race` | 非生产关键的缺陷修复 |
| 紧急修复 | `hotfix/JIRA-411-patch-auth-bypass` | `🐛 JIRA-411: patch auth bypass check` | 从 `main` 拉出的生产关键修复 |
| 重构 | `feature/JIRA-522-refactor-audit-service` | `♻️ JIRA-522: refactor audit service boundaries` | 有Jira任务追踪的结构性清理 |
| 文档 | `feature/JIRA-623-document-api-errors` | `📚 JIRA-623: document API error catalog` | 有Jira任务的文档工作 |
| 测试 | `bugfix/JIRA-724-cover-session-timeouts` | `🧪 JIRA-724: add session timeout regression tests` | 关联缺陷或功能的纯测试变更 |
| 配置 | `feature/JIRA-811-add-ci-policy-check` | `🔧 JIRA-811: add branch policy validation` | 配置或工作流策略变更 |
| 依赖 | `bugfix/JIRA-902-upgrade-actions` | `📦 JIRA-902: upgrade GitHub Actions versions` | 依赖或平台升级 |

如果上层工具要求外部前缀，保留仓库分支规范在其内部，例如：`codex/feature/JIRA-214-add-sso-login`。

### 官方Gitmoji参考

- 主要参考：[gitmoji.dev](https://gitmoji.dev/) 查看当前emoji目录及语义
- 权威来源：[github.com/carloscuesta/gitmoji](https://github.com/carloscuesta/gitmoji) 上游项目及使用模型
- 本仓库默认：添加全新Agent使用 `✨`，因为Gitmoji定义它代表新功能；仅在变更限于已有Agent或贡献文档的更新时使用 `📚`

### 提交与分支校验钩子

```bash
#!/usr/bin/env bash
set -euo pipefail

message_file="${1:?commit message file is required}"
branch="$(git rev-parse --abbrev-ref HEAD)"
subject="$(head -n 1 "$message_file")"

branch_regex='^(feature|bugfix|hotfix)/[A-Z]+-[0-9]+-[a-z0-9-]+$|^release/[0-9]+\.[0-9]+\.[0-9]+$'
commit_regex='^(🚀|✨|🐛|♻️|📚|🧪|💄|🔧|📦) [A-Z]+-[0-9]+: .+$'

if [[ ! "$branch" =~ $branch_regex ]]; then
  echo "Invalid branch name: $branch" >&2
  echo "Use feature/JIRA-ID-description, bugfix/JIRA-ID-description, hotfix/JIRA-ID-description, or release/version." >&2
  exit 1
fi

if [[ "$branch" != release/* && ! "$subject" =~ $commit_regex ]]; then
  echo "Invalid commit subject: $subject" >&2
  echo "Use: <gitmoji> JIRA-ID: short description" >&2
  exit 1
fi
```

### PR模板

```markdown
## 这个PR做了什么？
实现了 **JIRA-214**，添加SSO登录流程并加固了Token刷新处理。

## Jira链接
- 工单：JIRA-214
- 分支：feature/JIRA-214-add-sso-login

## 变更摘要
- 添加SSO回调控制器和Provider接入
- 添加过期刷新Token的回归测试覆盖
- 补充新登录配置路径的文档

## 风险与安全评审
- 认证流程变更：是
- 密钥处理变更：否
- 回滚方案：回滚分支并禁用Provider开关

## 测试
- 单元测试：通过
- 集成测试：在Staging环境通过
- 手动验证：在Staging环境验证了登录和登出流程
```

### 交付规划模板

```markdown
# Jira交付包

## 工单
- Jira：JIRA-315
- 目标：修复Token刷新竞态条件，不改变公共API

## 计划分支
- bugfix/JIRA-315-fix-token-refresh

## 计划提交
1. 🐛 JIRA-315: fix refresh token race in auth service
2. 🧪 JIRA-315: add concurrent refresh regression tests
3. 📚 JIRA-315: document token refresh failure modes

## 评审说明
- 风险区域：认证和会话过期
- 安全检查：确认敏感Token不出现在日志中
- 回滚：如需回滚，撤销提交1并禁用并发刷新路径
```
