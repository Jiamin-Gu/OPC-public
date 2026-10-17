## 技术交付物

### A-Frame 座舱控件示例

```html
<a-scene>
  <!-- 座舱外壳 —— 固定参考框架 -->
  <a-entity id="cockpit-shell" position="0 0.8 -0.5">
    <!-- 主仪表盘面板 -->
    <a-entity id="dashboard" position="0 0.6 -0.4" rotation="-15 0 0">
      <a-plane width="1.2" height="0.5" color="#1a1a2e"
               material="shader: flat; opacity: 0.9">
      </a-plane>
      <!-- 速度指示器 -->
      <a-entity id="speed-gauge" position="-0.35 0.1 0.01"
                geometry="primitive: circle; radius: 0.12"
                material="color: #0f3460; shader: flat">
        <a-entity id="speed-needle" position="0 0 0.01"
                  geometry="primitive: plane; width: 0.01; height: 0.1"
                  material="color: #e94560; shader: flat"
                  animation="property: rotation; from: 0 0 -135;
                             to: 0 0 135; dur: 3000; loop: true">
        </a-entity>
      </a-entity>
    </a-entity>

    <!-- 操纵杆 —— 带约束的交互 -->
    <a-entity id="joystick" position="0.2 0.3 -0.2"
              class="interactive grabbable">
      <a-cylinder radius="0.015" height="0.25" color="#333"
                  material="metalness: 0.8; roughness: 0.3">
      </a-cylinder>
      <a-sphere radius="0.03" position="0 0.14 0" color="#e94560"
                material="metalness: 0.6; roughness: 0.4">
      </a-sphere>
    </a-entity>

    <!-- 油门推杆 -->
    <a-entity id="throttle" position="-0.3 0.25 -0.15"
              class="interactive slidable"
              data-axis="y" data-min="0" data-max="0.15">
      <a-box width="0.04" height="0.06" depth="0.04" color="#2d3436"
             material="metalness: 0.7; roughness: 0.4">
      </a-box>
    </a-entity>
  </a-entity>
</a-scene>
```

### 操纵杆约束逻辑（Three.js）

```javascript
class ConstrainedJoystick {
  constructor(mesh, config = {}) {
    this.mesh = mesh;
    this.maxAngle = config.maxAngle || 25; // 最大偏转角度
    this.deadzone = config.deadzone || 0.05; // 死区比例
    this.springK = config.springK || 8.0; // 回弹弹性系数
    this.damping = config.damping || 0.85; // 阻尼
    this.velocity = { x: 0, z: 0 };
    this.currentAngle = { x: 0, z: 0 };
    this.isGrabbed = false;
  }

  update(dt, grabPosition = null) {
    if (this.isGrabbed && grabPosition) {
      // 手部位置映射到偏转角度
      const targetX = this.mapToAngle(grabPosition.x);
      const targetZ = this.mapToAngle(grabPosition.z);
      this.currentAngle.x = THREE.MathUtils.lerp(
        this.currentAngle.x, targetX, 0.3
      );
      this.currentAngle.z = THREE.MathUtils.lerp(
        this.currentAngle.z, targetZ, 0.3
      );
    } else {
      // 弹簧回弹到中心
      this.velocity.x += -this.springK * this.currentAngle.x * dt;
      this.velocity.z += -this.springK * this.currentAngle.z * dt;
      this.velocity.x *= this.damping;
      this.velocity.z *= this.damping;
      this.currentAngle.x += this.velocity.x * dt;
      this.currentAngle.z += this.velocity.z * dt;
    }

    // 应用角度限制
    const maxRad = THREE.MathUtils.degToRad(this.maxAngle);
    this.currentAngle.x = THREE.MathUtils.clamp(
      this.currentAngle.x, -maxRad, maxRad
    );
    this.currentAngle.z = THREE.MathUtils.clamp(
      this.currentAngle.z, -maxRad, maxRad
    );
    this.mesh.rotation.set(this.currentAngle.x, 0, this.currentAngle.z);
  }

  getAxis() {
    const maxRad = THREE.MathUtils.degToRad(this.maxAngle);
    let x = this.currentAngle.x / maxRad;
    let z = this.currentAngle.z / maxRad;
    // 应用死区
    x = Math.abs(x) < this.deadzone ? 0 : x;
    z = Math.abs(z) < this.deadzone ? 0 : z;
    return { pitch: x, roll: z };
  }

  mapToAngle(handOffset) {
    return THREE.MathUtils.clamp(
      handOffset * 3.0,
      -THREE.MathUtils.degToRad(this.maxAngle),
      THREE.MathUtils.degToRad(this.maxAngle)
    );
  }
}
```
