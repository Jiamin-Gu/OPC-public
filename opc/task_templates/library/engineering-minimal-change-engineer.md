## 📋 技术交付物

### 示例 1：bug 修复的最小方式 vs. 扩展方式

**任务**："修复 `paginatePosts` 中的偏移一错误。"

**❌ 过度热情的工程师的差异**（47 行变更）：
```typescript
// 重命名变量使其更清晰
// 添加输入验证
// 提取常量
// 添加 JSDoc
// 顺便清理了一下 import
// 加了几个防御性空值检查

const POSTS_PER_PAGE = 20;

/**
 * Paginates a list of posts with bounds checking.
 * @param posts - The full list of posts
 * @param pageNumber - The 1-indexed page number
 * @returns A slice of posts for the requested page
 */
export function paginatePosts(
  posts: Post[] | null | undefined,
  pageNumber: number
): Post[] {
  if (!posts || posts.length === 0) return [];
  if (pageNumber < 1) pageNumber = 1;
  const startIndex = (pageNumber - 1) * POSTS_PER_PAGE;
  const endIndex = startIndex + POSTS_PER_PAGE;
  return posts.slice(startIndex, endIndex);
}
```

**✅ 最小变更工程师的差异**（1 行变更）：
```diff
- const startIndex = pageNumber * POSTS_PER_PAGE;
+ const startIndex = (pageNumber - 1) * POSTS_PER_PAGE;
```

偏移一就是 bug。bug 修复了。PR 10 秒就能审完。膨胀版本中的"改进"各自都有自己的风险，值得各自的 PR——或者更可能的是，根本不值得一个 PR。

### 示例 2：新功能的最小方式 vs. 过度架构方式

**任务**："给 import 命令添加 `--dry-run` 标志。"

**❌ 过度架构**：引入 `RunMode` 枚举、`DryRunStrategy` 接口、`RunModeContext` 提供者，重构 import 命令使用策略模式，添加 `runMode` 配置字段，为"未来模式"暴露钩子。

**✅ 最小方式**：
```typescript
// 在 import 命令中
const dryRun = args.includes('--dry-run');

// 在写入点
if (dryRun) {
  console.log(`[dry-run] would write ${records.length} records`);
} else {
  await db.insertMany(records);
}
```

两个 `if` 分支。没有抽象。如果将来出现第三种"模式"，*那时再*提取。在那之前，策略模式就是没有回报的债务。

### 示例 3："范围检查"模板（每个 PR 提交前使用）

```markdown
## 范围自检

**原始任务描述：** [粘贴准确的任务描述]

**我触碰的文件：**
- [ ] file1.ts — 需要修改因为：[原因]
- [ ] file2.ts — 需要修改因为：[原因]

**我想添加但不会添加的行：**
- [ ] [那些"顺便"的事情——记为后续事项，不要包含在本次 PR 中]

**我不打算防御的假设场景：**
- [ ] [列出那些实际上不可能发生的情况]

**我考虑过但拒绝的抽象：**
- [ ] [辅助函数/类，因为重复次数 < 4 所以保留重复行]

**差异大小：** [新增 X 行，删除 Y 行]
**还能更小吗？** [是/否——如果是，让它更小]
```
