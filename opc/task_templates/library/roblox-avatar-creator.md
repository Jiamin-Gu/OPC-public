## 技术交付物

### 配件导出检查清单（DCC → Roblox Studio）
```markdown
## 配件导出检查清单

### 网格
- [ ] 三角面数：___（限制：配件 4,000，套装部件 10,000）
- [ ] 单一网格物体：是/否
- [ ] [0,1] 空间内单一 UV 通道：是/否
- [ ] [0,1] 外无重叠 UV：是/否
- [ ] 所有变换已应用（缩放=1，旋转=0）：是/否
- [ ] 轴心点在挂载位置：是/否
- [ ] 无零面积面或非流形几何体：是/否

### 纹理
- [ ] 分辨率：___ × ___（最大 1024×1024）
- [ ] 格式：PNG
- [ ] UV 岛有 2px+ 内边距：是/否
- [ ] 无版权内容：是/否
- [ ] 透明度在 alpha 通道处理：是/否

### 挂载
- [ ] 挂载对象存在且名称正确：___
- [ ] 已测试体型：[ ] Classic  [ ] R15 Normal  [ ] R15 Rthro
- [ ] 所有测试体型中无穿透默认虚拟形象网格：是/否

### 文件
- [ ] 格式：FBX（有绑定）/ OBJ（静态）
- [ ] 文件名遵循命名规范：[创作者名]_[物品名]_[类型]
```

### HumanoidDescription——体验内虚拟形象定制
```lua
-- ServerStorage/Modules/AvatarManager.lua
local Players = game:GetService("Players")

local AvatarManager = {}

-- 为玩家的虚拟形象应用完整套装
function AvatarManager.applyOutfit(player: Player, outfitData: table): ()
    local character = player.Character
    if not character then return end

    local humanoid = character:FindFirstChildOfClass("Humanoid")
    if not humanoid then return end

    local description = humanoid:GetAppliedDescription()

    -- 应用配件（通过资源 ID）
    if outfitData.hat then
        description.HatAccessory = tostring(outfitData.hat)
    end
    if outfitData.face then
        description.FaceAccessory = tostring(outfitData.face)
    end
    if outfitData.shirt then
        description.Shirt = outfitData.shirt
    end
    if outfitData.pants then
        description.Pants = outfitData.pants
    end

    -- 身体颜色
    if outfitData.bodyColors then
        description.HeadColor = outfitData.bodyColors.head or description.HeadColor
        description.TorsoColor = outfitData.bodyColors.torso or description.TorsoColor
    end

    -- 应用——此方法处理角色刷新
    humanoid:ApplyDescription(description)
end

-- 从 DataStore 加载玩家保存的套装并在生成时应用
function AvatarManager.applyPlayerSavedOutfit(player: Player): ()
    local DataManager = require(script.Parent.DataManager)
    local data = DataManager.getData(player)
    if data and data.outfit then
        AvatarManager.applyOutfit(player, data.outfit)
    end
end

return AvatarManager
```

### 分层服装笼设置（Blender）
```markdown
## 分层服装绑定要求

### 外部网格
- 游戏中可见的服装
- UV 映射，按规格贴图
- 绑定到 R15 骨骼（精确匹配 Roblox 公开的 R15 骨架）
- 导出名称：[物品名]

### 内部笼网格（_InnerCage）
- 与外部网格相同的拓扑但向内收缩约 0.01 个单位
- 定义服装如何包裹虚拟形象身体
- 不贴图——笼在游戏中不可见
- 导出名称：[物品名]_InnerCage

### 外部笼网格（_OuterCage）
- 让其他分层物品可以叠在此物品上
- 从外部网格略微向外扩展
- 导出名称：[物品名]_OuterCage

### 骨骼权重
- 所有顶点权重到正确的 R15 骨骼
- 无未加权的顶点（导致接缝处网格撕裂）
- 权重转移：使用 Roblox 提供的参考骨架确保正确的骨骼名称

### 测试要求
提交前在 Roblox Studio 中应用到所有提供的测试体型：
- Young、Classic、Normal、Rthro Narrow、Rthro Broad
- 验证在极端动画姿势下无穿透：idle、run、jump、sit
```

### Creator Marketplace 提交准备
```markdown
## 物品提交包：[物品名称]

### 元数据
- **物品名称**：[准确的、可搜索的、不误导的]
- **描述**：[清晰描述物品 + 它穿戴在什么身体部位]
- **类别**：[帽子 / 面部配件 / 肩部配件 / 衬衫 / 裤子 / 等]
- **价格**：[Robux——调研同类物品做市场定位]
- **限量**：[ ] 是（需要资格）  [ ] 否

### 资源文件
- [ ] 网格：[文件名].fbx / .obj
- [ ] 纹理：[文件名].png（最大 1024×1024）
- [ ] 图标缩略图：420×420 PNG——物品在中性背景上清晰展示

### 提交前验证
- [ ] Studio 内测试：物品在所有虚拟形象体型上正确渲染
- [ ] Studio 内测试：idle、walk、run、jump、sit 动画中无穿透
- [ ] 纹理：无版权、品牌标志或不当内容
- [ ] 网格：三角面数在限制内
- [ ] DCC 工具中已应用所有变换

### 审核风险标记（预检）
- [ ] 物品上有文字吗？（可能需要文字审核）
- [ ] 有现实品牌引用吗？→ 移除
- [ ] 是面部遮挡配件吗？（审核更严格）
- [ ] 是武器形状的配件吗？→ 先查看 Roblox 武器政策
```

### 体验内 UGC 商店 UI 流程
```lua
-- 客户端虚拟形象商店 UI
-- ReplicatedStorage/Modules/AvatarShopUI.lua
local Players = game:GetService("Players")
local MarketplaceService = game:GetService("MarketplaceService")

local AvatarShopUI = {}

-- 通过资源 ID 提示玩家购买 UGC 物品
function AvatarShopUI.promptPurchaseItem(assetId: number): ()
    local player = Players.LocalPlayer
    -- PromptPurchase 适用于 UGC 目录物品
    MarketplaceService:PromptPurchase(player, assetId)
end

-- 监听购买完成——将物品应用到虚拟形象
MarketplaceService.PromptPurchaseFinished:Connect(
    function(player: Player, assetId: number, isPurchased: boolean)
        if isPurchased then
            -- 通知服务端应用并持久化购买
            local Remotes = game.ReplicatedStorage.Remotes
            Remotes.ItemPurchased:FireServer(assetId)
        end
    end
)

return AvatarShopUI
```
