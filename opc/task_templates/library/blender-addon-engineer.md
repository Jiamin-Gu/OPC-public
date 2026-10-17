## 技术交付物

### 资源验证 Operator
```python
import bpy

class PIPELINE_OT_validate_assets(bpy.types.Operator):
    bl_idname = "pipeline.validate_assets"
    bl_label = "Validate Assets"
    bl_description = "Check naming, transforms, and material slots before export"

    def execute(self, context):
        issues = []
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue

            if obj.name != obj.name.strip():
                issues.append(f"{obj.name}: leading/trailing whitespace in object name")

            if any(abs(s - 1.0) > 0.0001 for s in obj.scale):
                issues.append(f"{obj.name}: unapplied scale")

            if len(obj.material_slots) == 0:
                issues.append(f"{obj.name}: missing material slot")

        if issues:
            self.report({'WARNING'}, f"Validation found {len(issues)} issue(s). See system console.")
            for issue in issues:
                print("[VALIDATION]", issue)
            return {'CANCELLED'}

        self.report({'INFO'}, "Validation passed")
        return {'FINISHED'}
```

### 导出预设面板
```python
class PIPELINE_PT_export_panel(bpy.types.Panel):
    bl_label = "Pipeline Export"
    bl_idname = "PIPELINE_PT_export_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pipeline"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "pipeline_export_path")
        layout.prop(scene, "pipeline_target", text="Target")
        layout.operator("pipeline.validate_assets", icon="CHECKMARK")
        layout.operator("pipeline.export_selected", icon="EXPORT")


class PIPELINE_OT_export_selected(bpy.types.Operator):
    bl_idname = "pipeline.export_selected"
    bl_label = "Export Selected"

    def execute(self, context):
        export_path = context.scene.pipeline_export_path
        bpy.ops.export_scene.gltf(
            filepath=export_path,
            use_selection=True,
            export_apply=True,
            export_texcoords=True,
            export_normals=True,
        )
        self.report({'INFO'}, f"Exported selection to {export_path}")
        return {'FINISHED'}
```

### 命名审计报告
```python
def build_naming_report(objects):
    report = {"ok": [], "problems": []}
    for obj in objects:
        if "." in obj.name and obj.name[-3:].isdigit():
            report["problems"].append(f"{obj.name}: Blender duplicate suffix detected")
        elif " " in obj.name:
            report["problems"].append(f"{obj.name}: spaces in name")
        else:
            report["ok"].append(obj.name)
    return report
```

### 交付物示例
- 包含 `AddonPreferences`、自定义 Operator、Panel 和 Property Group 的 Blender 插件脚手架
- 资源验证清单，涵盖命名、变换、原点、材质槽和 Collection 放置
- 面向 FBX、glTF 或 USD 的引擎交接导出器，带可重复的预设规则

### 验证报告模板
```markdown
# 资源验证报告——[场景或 Collection 名称]

## 概要
- 扫描对象数：24
- 通过：18
- 警告：4
- 错误：2

## 错误
| 对象 | 规则 | 详情 | 建议修复 |
|---|---|---|---|
| SM_Crate_A | 变换 | X 轴未应用缩放 | 检查缩放后再有意识地应用 |
| SM_Door Frame | 材质 | 未分配材质 | 分配默认材质或修正槽映射 |

## 警告
| 对象 | 规则 | 详情 | 建议修复 |
|---|---|---|---|
| SM_Wall Panel | 命名 | 包含空格 | 将空格替换为下划线 |
| SM_Pipe.001 | 命名 | 检测到 Blender 重复后缀 | 重命名为确定性的生产名称 |
```
