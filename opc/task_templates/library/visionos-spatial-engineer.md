## 技术交付物

### Liquid Glass 窗口应用骨架

```swift
import SwiftUI
import RealityKit

@main
struct SpatialApp: App {
    @State private var appModel = AppModel()

    var body: some Scene {
        // 主窗口 —— 带 Liquid Glass 效果
        WindowGroup(id: "main") {
            ContentView()
                .environment(appModel)
                .glassBackgroundEffect(displayMode: .always)
                .frame(
                    minWidth: 600, maxWidth: 1200,
                    minHeight: 400, maxHeight: 800
                )
        }
        .windowStyle(.plain)
        .defaultSize(width: 800, height: 600)

        // 体积式窗口 —— 展示 3D 内容
        WindowGroup(id: "volume-viewer") {
            VolumeContentView()
                .environment(appModel)
        }
        .windowStyle(.volumetric)
        .defaultSize(width: 0.5, height: 0.5, depth: 0.5, in: .meters)

        // 沉浸式空间
        ImmersiveSpace(id: "immersive") {
            ImmersiveView()
                .environment(appModel)
        }
        .immersionStyle(selection: .constant(.mixed), in: .mixed)
    }
}

@Observable
class AppModel {
    var selectedItem: String?
    var isImmersiveSpaceOpen = false

    // 体积内容的 3D 变换状态
    var rotation: Rotation3D = .identity
    var scale: Double = 1.0
}
```

### RealityKit 手势交互实体

```swift
import SwiftUI
import RealityKit

struct InteractiveModelView: View {
    @Environment(AppModel.self) var appModel
    @State private var modelEntity: ModelEntity?

    var body: some View {
        RealityView { content, attachments in
            // 加载 3D 模型
            guard let entity = try? await ModelEntity(
                named: "product_model",
                in: Bundle.main
            ) else { return }

            // 启用输入和碰撞
            entity.components.set(InputTargetComponent())
            entity.generateCollisionShapes(recursive: true)
            entity.components.set(HoverEffectComponent())

            // 添加 SwiftUI 附件作为标签
            if let label = attachments.entity(for: "info-label") {
                label.position = [0, 0.15, 0]
                entity.addChild(label)
            }

            content.add(entity)
            modelEntity = entity
        } update: { content, attachments in
            // 响应状态变化更新实体
            modelEntity?.transform.rotation = simd_quatf(appModel.rotation)
            let s = Float(appModel.scale)
            modelEntity?.transform.scale = [s, s, s]
        } attachments: {
            Attachment(id: "info-label") {
                Text(appModel.selectedItem ?? "点击查看详情")
                    .font(.caption)
                    .padding(8)
                    .glassBackgroundEffect()
            }
        }
        .gesture(
            DragGesture()
                .targetedToAnyEntity()
                .onChanged { value in
                    let delta = value.convert(value.translation3D, from: .local, to: .scene)
                    value.entity.position += SIMD3<Float>(
                        Float(delta.x) * 0.001,
                        Float(delta.y) * 0.001,
                        Float(delta.z) * 0.001
                    )
                }
        )
        .gesture(
            RotateGesture3D()
                .targetedToAnyEntity()
                .onChanged { value in
                    appModel.rotation = value.rotation
                }
        )
        .gesture(
            MagnifyGesture()
                .targetedToAnyEntity()
                .onChanged { value in
                    appModel.scale = max(0.5, min(3.0, value.magnification))
                }
        )
    }
}
```

### 空间小组件

```swift
import WidgetKit
import SwiftUI

struct SpatialWidget: Widget {
    let kind: String = "SpatialWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: Provider()) { entry in
            SpatialWidgetView(entry: entry)
                .containerBackground(.ultraThinMaterial, for: .widget)
        }
        .configurationDisplayName("空间数据面板")
        .description("在你的空间中放置实时数据卡片")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct SpatialWidgetView: View {
    let entry: SimpleEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "cube.transparent")
                    .foregroundStyle(.secondary)
                Text("空间监控")
                    .font(.headline)
            }
            Divider()
            LabeledContent("活跃实体", value: "\(entry.entityCount)")
            LabeledContent("帧率", value: "\(entry.fps) fps")
            LabeledContent("内存", value: "\(entry.memoryMB) MB")
        }
        .padding()
    }
}
```
