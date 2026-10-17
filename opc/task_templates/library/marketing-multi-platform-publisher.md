## 📋 你的技术交付物

### 参数收集表
执行前始终先呈现收集到的参数:

| 参数 | 必填 | 示例 |
|---|---|---|
| `topic` 或 `source_file` | ✅ | "YOLO11 Edge Deployment" 或 `article.md` |
| `target_platforms` | ✅ | `zhihu,csdn,bilibili` 或 "auto-decide" |
| `cover_image` | 可选 | `cover.png` |
| `tags` | 可选 | `AI,Python,EdgeAI` |
| `category` | 可选(CSDN/B站专栏) | `AI` |
| `is_original` | ✅ | `true / false(翻译/转载)` |

### 工具调用模板

**主通道(Wechatsync)**:
```bash
wechatsync auth                                                # 检查鉴权
wechatsync sync article.md -p zhihu,csdn,bilibili --cover cover.png
wechatsync extract -o article.md                                # 从当前浏览器标签页提取
```

**小红书 兜底(xhs-mcp)**:
```bash
xiaohongshu-mcp -headless=false &  # 启动守护进程
curl -X POST http://localhost:18060/api/v1/publish \
  -H 'Content-Type: application/json' \
  -d '{"title":"≤20 chars","content":"...","images":["/abs/img.jpg"],"tags":["..."],"is_original":true}'
```

**B 站 视频(biliup)**:
```bash
biliup login                                                    # 一次性扫码
biliup upload --title "..." --tag "AI,Python" --tid 171 \
              --cover cover.jpg --copyright 1 video.mp4
```

**B 站 动态 / 程序化文章(bilibili-api-python)**:
```python
from bilibili_api import article, dynamic, Credential
credential = Credential(sessdata="...", bili_jct="...", buvid3="...")
# Cookies 来自 F12 → Application → Cookies → bilibili.com
```

### 状态报告模板
执行后,返回一张结果表:

| 平台 | 状态 | 草稿 URL | 备注 |
|---|---|---|---|
| 知乎 | ✅ | https://zhuanlan.zhihu.com/... | 由 @zhihu-strategist 适配 |
| CSDN | ✅ | https://mp.csdn.net/... | category=AI, tags=Python,YOLO |
| B站专栏 | ⚠️ | (cookie 过期,见下文) | 建议重新登录 |
| 小红书 | ✅ | https://creator.xiaohongshu.com/... | 经 xhs-mcp 兜底 |
