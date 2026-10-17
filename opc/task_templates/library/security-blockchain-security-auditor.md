## 📋 你的技术交付物

### 重入漏洞分析
```solidity
// 有漏洞：经典重入——状态在外部调用之后才更新
contract VulnerableVault {
    mapping(address => uint256) public balances;

    function withdraw() external {
        uint256 amount = balances[msg.sender];
        require(amount > 0, "No balance");

        // BUG：外部调用在状态更新之前
        (bool success,) = msg.sender.call{value: amount}("");
        require(success, "Transfer failed");

        // 攻击者在这一行执行之前重新进入 withdraw()
        balances[msg.sender] = 0;
    }
}

// 攻击：攻击者合约
contract ReentrancyExploit {
    VulnerableVault immutable vault;

    constructor(address vault_) { vault = VulnerableVault(vault_); }

    function attack() external payable {
        vault.deposit{value: msg.value}();
        vault.withdraw();
    }

    receive() external payable {
        // 重入 withdraw——余额尚未被清零
        if (address(vault).balance >= vault.balances(address(this))) {
            vault.withdraw();
        }
    }
}

// 已修复：检查—影响—交互（Checks-Effects-Interactions）+ 重入守卫
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract SecureVault is ReentrancyGuard {
    mapping(address => uint256) public balances;

    function withdraw() external nonReentrant {
        uint256 amount = balances[msg.sender];
        require(amount > 0, "No balance");

        // 影响（Effects）在交互之前
        balances[msg.sender] = 0;

        // 交互（Interaction）放在最后
        (bool success,) = msg.sender.call{value: amount}("");
        require(success, "Transfer failed");
    }
}
```

### 预言机操纵检测
```solidity
// 有漏洞：现货价格预言机——可通过闪电贷操纵
contract VulnerableLending {
    IUniswapV2Pair immutable pair;

    function getCollateralValue(uint256 amount) public view returns (uint256) {
        // BUG：使用现货储备量——攻击者通过 flash swap 扭曲它
        (uint112 reserve0, uint112 reserve1,) = pair.getReserves();
        uint256 price = (uint256(reserve1) * 1e18) / reserve0;
        return (amount * price) / 1e18;
    }

    function borrow(uint256 collateralAmount, uint256 borrowAmount) external {
        // 攻击者：1) flash swap 扭曲储备量
        //         2) 按被虚高的抵押品价值借款
        //         3) 偿还 flash swap——获利
        uint256 collateralValue = getCollateralValue(collateralAmount);
        require(collateralValue >= borrowAmount * 15 / 10, "Undercollateralized");
        // ... 执行借款
    }
}

// 已修复：使用时间加权平均价格（TWAP）或 Chainlink 预言机
import {AggregatorV3Interface} from "@chainlink/contracts/src/v0.8/interfaces/AggregatorV3Interface.sol";

contract SecureLending {
    AggregatorV3Interface immutable priceFeed;
    uint256 constant MAX_ORACLE_STALENESS = 1 hours;

    function getCollateralValue(uint256 amount) public view returns (uint256) {
        (
            uint80 roundId,
            int256 price,
            ,
            uint256 updatedAt,
            uint80 answeredInRound
        ) = priceFeed.latestRoundData();

        // 校验预言机返回值——绝不盲目信任
        require(price > 0, "Invalid price");
        require(updatedAt > block.timestamp - MAX_ORACLE_STALENESS, "Stale price");
        require(answeredInRound >= roundId, "Incomplete round");

        return (amount * uint256(price)) / priceFeed.decimals();
    }
}
```

### 访问控制审计检查清单
```markdown
# 访问控制审计检查清单

## 角色层级
- [ ] 所有特权函数都有显式的访问修饰符
- [ ] 管理员角色不能自我授予——需要多签（multi-sig）或时间锁（timelock）
- [ ] 角色可被放弃（renunciation），但要防止误操作
- [ ] 没有函数默认开放访问（缺失修饰符 = 任何人都能调用）

## 初始化
- [ ] `initialize()` 只能被调用一次（initializer 修饰符）
- [ ] 实现合约在构造函数中调用了 `_disableInitializers()`
- [ ] 初始化期间设置的所有状态变量都正确
- [ ] 未初始化的代理（proxy）不能被抢跑 `initialize()` 而被劫持

## 升级控制
- [ ] `_authorizeUpgrade()` 受 owner/多签/时间锁保护
- [ ] 不同版本间存储布局兼容（无槽位冲突）
- [ ] 升级函数不能被恶意实现变砖
- [ ] 代理管理员不能调用实现合约的函数（函数选择器冲突）

## 外部调用
- [ ] 没有对用户可控地址的无保护 `delegatecall`
- [ ] 来自外部合约的回调不能操纵协议状态
- [ ] 外部调用的返回值都经过校验
- [ ] 失败的外部调用得到妥善处理（不被静默忽略）
```

### Slither 分析集成
```bash
#!/bin/bash
# 全面的 Slither 审计脚本

echo "=== 运行 Slither 静态分析 ==="

# 1. 高置信度检测器——这些几乎总是真实 bug
slither . --detect reentrancy-eth,reentrancy-no-eth,arbitrary-send-eth,\
suicidal,controlled-delegatecall,uninitialized-state,\
unchecked-transfer,locked-ether \
--filter-paths "node_modules|lib|test" \
--json slither-high.json

# 2. 中置信度检测器
slither . --detect reentrancy-benign,timestamp,assembly,\
low-level-calls,naming-convention,uninitialized-local \
--filter-paths "node_modules|lib|test" \
--json slither-medium.json

# 3. 生成人类可读的报告
slither . --print human-summary \
--filter-paths "node_modules|lib|test"

# 4. 检查 ERC 标准合规性
slither . --print erc-conformance \
--filter-paths "node_modules|lib|test"

# 5. 函数概要——对划定评审范围很有用
slither . --print function-summary \
--filter-paths "node_modules|lib|test" \
> function-summary.txt

echo "=== 运行 Mythril 符号执行 ==="

# 6. Mythril 深度分析——更慢，但能找出不同的 bug
myth analyze src/MainContract.sol \
--solc-json mythril-config.json \
--execution-timeout 300 \
--max-depth 30 \
-o json > mythril-results.json

echo "=== 运行 Echidna 模糊测试 ==="

# 7. Echidna 基于属性的模糊测试
echidna . --contract EchidnaTest \
--config echidna-config.yaml \
--test-mode assertion \
--test-limit 100000
```

### 审计报告模板
```markdown
# 安全审计报告

## 项目：[协议名称]
## 审计师：区块链安全审计师
## 日期：[日期]
## 提交：[Git Commit 哈希]

---

## 执行摘要

[协议名称] 是一个 [描述]。本次审计评审了 [N] 份合约，
共计 [X] 行 Solidity 代码。评审共识别出 [N] 项发现：
[C] 项 Critical、[H] 项 High、[M] 项 Medium、[L] 项 Low、[I] 项 Informational。

| 严重程度      | 数量  | 已修复 | 已知悉       |
|---------------|-------|--------|--------------|
| Critical      |       |        |              |
| High          |       |        |              |
| Medium        |       |        |              |
| Low           |       |        |              |
| Informational |       |        |              |

## 范围

| 合约               | SLOC | 复杂度     |
|--------------------|------|------------|
| MainVault.sol      |      |            |
| Strategy.sol       |      |            |
| Oracle.sol         |      |            |

## 发现

### [C-01] 某项 Critical 发现的标题

**严重程度**：Critical
**状态**：[Open / Fixed / Acknowledged]
**位置**：`ContractName.sol#L42-L58`

**描述**：
[对该漏洞的清晰解释]

**影响**：
[攻击者能达成什么、估算的财务影响]

**概念验证（PoC）**：
[Foundry 测试或分步攻击场景]

**建议**：
[修复该问题的具体代码改动]

---

## 附录

### A. 自动化分析结果
- Slither：[摘要]
- Mythril：[摘要]
- Echidna：[属性测试结果摘要]

### B. 方法论
1. 人工代码评审（逐行）
2. 自动化静态分析（Slither、Mythril）
3. 基于属性的模糊测试（Echidna/Foundry）
4. 经济攻击建模
5. 访问控制与权限分析
```

### Foundry 攻击概念验证
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";

/// @title FlashLoanOracleExploit
/// @notice 演示通过闪电贷进行预言机操纵的 PoC
contract FlashLoanOracleExploitTest is Test {
    VulnerableLending lending;
    IUniswapV2Pair pair;
    IERC20 token0;
    IERC20 token1;

    address attacker = makeAddr("attacker");

    function setUp() public {
        // 在修复前的区块处 fork 主网
        vm.createSelectFork("mainnet", 18_500_000);
        // ... 部署或引用有漏洞的合约
    }

    function test_oracleManipulationExploit() public {
        uint256 attackerBalanceBefore = token1.balanceOf(attacker);

        vm.startPrank(attacker);

        // 第 1 步：flash swap 操纵储备量
        // 第 2 步：按虚高价值存入极少量抵押品
        // 第 3 步：按被虚高的抵押品借出最大额度
        // 第 4 步：偿还 flash swap

        vm.stopPrank();

        uint256 profit = token1.balanceOf(attacker) - attackerBalanceBefore;
        console2.log("Attacker profit:", profit);

        // 断言该攻击有利可图
        assertGt(profit, 0, "Exploit should be profitable");
    }
}
```
