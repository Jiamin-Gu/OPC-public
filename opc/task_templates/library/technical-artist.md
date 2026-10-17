## 技术交付物

### 资源预算规格表
```markdown
# 资源技术预算——[项目名称]

## 角色
| LOD  | 最大三角面 | 纹理分辨率    | Draw Call |
|------|-----------|--------------|-----------|
| LOD0 | 15,000    | 2048×2048    | 2–3       |
| LOD1 | 8,000     | 1024×1024    | 2         |
| LOD2 | 3,000     | 512×512      | 1         |
| LOD3 | 800       | 256×256      | 1         |

## 环境——主体道具
| LOD  | 最大三角面 | 纹理分辨率  |
|------|-----------|------------|
| LOD0 | 4,000     | 1024×1024  |
| LOD1 | 1,500     | 512×512    |
| LOD2 | 400       | 256×256    |

## VFX 粒子
- 屏幕同时最大粒子数：500（移动端）/ 2000（PC）
- 每个特效最大 overdraw 层数：3（移动端）/ 6（PC）
- 所有叠加特效：尽量用 alpha 裁切，只在预算批准后使用叠加混合

## 纹理压缩
| 类型        | PC   | 移动端      | 主机   |
|-------------|------|------------|--------|
| 反照率      | BC7  | ASTC 6×6   | BC7    |
| 法线贴图    | BC5  | ASTC 6×6   | BC5    |
| 粗糙度/AO   | BC4  | ASTC 8×8   | BC4    |
| UI 精灵     | BC7  | ASTC 4×4   | BC7    |
```

### 自定义 Shader——溶解效果（HLSL/ShaderLab）
```hlsl
// 溶解 shader——适用于 Unity URP，可适配其他管线
Shader "Custom/Dissolve"
{
    Properties
    {
        _BaseMap ("反照率", 2D) = "white" {}
        _DissolveMap ("溶解噪声", 2D) = "white" {}
        _DissolveAmount ("溶解程度", Range(0,1)) = 0
        _EdgeWidth ("边缘宽度", Range(0, 0.2)) = 0.05
        _EdgeColor ("边缘颜色", Color) = (1, 0.3, 0, 1)
    }
    SubShader
    {
        Tags { "RenderType"="TransparentCutout" "Queue"="AlphaTest" }
        HLSLPROGRAM
        // 顶点：标准变换
        // 片元：
        float dissolveValue = tex2D(_DissolveMap, i.uv).r;
        clip(dissolveValue - _DissolveAmount);
        float edge = step(dissolveValue, _DissolveAmount + _EdgeWidth);
        col = lerp(col, _EdgeColor, edge);
        ENDHLSL
    }
}
```

### VFX 性能审计清单
```markdown
## VFX 特效审查：[特效名称]

**目标平台**：[ ] PC  [ ] 主机  [ ] 移动端

粒子数量
- [ ] 最坏情况下测量的最大粒子数：___
- [ ] 在目标平台预算内：___

Overdraw
- [ ] 已检查 Overdraw 可视化器——层数：___
- [ ] 在限制范围内（移动端 ≤ 3，PC ≤ 6）：___

Shader 复杂度
- [ ] 已检查 Shader 复杂度图（绿/黄 OK，红 = 需修改）
- [ ] 移动端：粒子无逐像素光照

纹理
- [ ] 粒子纹理在共享图集中：是/否
- [ ] 纹理尺寸：___（移动端每种粒子类型最大 256×256）

GPU 开销
- [ ] 已在最坏密度下用引擎 GPU 分析器分析
- [ ] 帧时间贡献：___ms（预算：___ms）
```

### LOD 链验证脚本（Python——DCC 通用）
```python
# 根据项目预算验证 LOD 链面数
LOD_BUDGETS = {
    "character": [15000, 8000, 3000, 800],
    "hero_prop":  [4000, 1500, 400],
    "small_prop": [500, 200],
}

def validate_lod_chain(asset_name: str, asset_type: str, lod_poly_counts: list[int]) -> list[str]:
    errors = []
    budgets = LOD_BUDGETS.get(asset_type)
    if not budgets:
        return [f"未知资源类型：{asset_type}"]
    for i, (count, budget) in enumerate(zip(lod_poly_counts, budgets)):
        if count > budget:
            errors.append(f"{asset_name} LOD{i}：{count} 三角面超出预算 {budget}")
    return errors
```
