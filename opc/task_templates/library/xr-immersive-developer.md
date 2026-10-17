## 技术交付物

### WebXR 会话初始化与手部追踪

```javascript
class XRSessionManager {
  constructor(renderer, scene, camera) {
    this.renderer = renderer;
    this.scene = scene;
    this.camera = camera;
    this.session = null;
    this.referenceSpace = null;
    this.hands = { left: null, right: null };
    // 预分配对象，避免帧循环中分配内存
    this._tempMatrix = new THREE.Matrix4();
    this._tempVec3 = new THREE.Vector3();
    this._tempQuat = new THREE.Quaternion();
  }

  async startSession(mode = 'immersive-vr') {
    const supported = await navigator.xr?.isSessionSupported(mode);
    if (!supported) {
      console.warn(`${mode} 不支持，尝试降级`);
      if (mode === 'immersive-vr') {
        return this.startSession('inline');
      }
      throw new Error('当前设备不支持 WebXR');
    }

    const requiredFeatures = ['local-floor'];
    const optionalFeatures = ['hand-tracking', 'hit-test', 'layers'];

    this.session = await navigator.xr.requestSession(mode, {
      requiredFeatures,
      optionalFeatures,
    });

    this.referenceSpace = await this.session.requestReferenceSpace(
      mode === 'inline' ? 'viewer' : 'local-floor'
    );

    this.renderer.xr.enabled = true;
    this.renderer.xr.setReferenceSpaceType('local-floor');
    await this.renderer.xr.setSession(this.session);

    this.session.addEventListener('end', () => this.cleanup());
    this.setupHandTracking();
  }

  setupHandTracking() {
    const hand0 = this.renderer.xr.getHand(0);
    const hand1 = this.renderer.xr.getHand(1);

    if (hand0 && hand1) {
      this.hands.left = hand0;
      this.hands.right = hand1;
      this.scene.add(hand0, hand1);
      console.log('手部追踪已启用');
    } else {
      console.log('手部追踪不可用，使用手柄模式');
      this.setupControllers();
    }
  }

  setupControllers() {
    const ctrl0 = this.renderer.xr.getController(0);
    const ctrl1 = this.renderer.xr.getController(1);
    ctrl0.addEventListener('selectstart', (e) => this.onSelect(e, 0));
    ctrl1.addEventListener('selectstart', (e) => this.onSelect(e, 1));
    this.scene.add(ctrl0, ctrl1);
  }

  detectPinch(hand, threshold = 0.02) {
    const thumbTip = hand.joints['thumb-tip'];
    const indexTip = hand.joints['index-finger-tip'];
    if (!thumbTip || !indexTip) return false;

    thumbTip.getWorldPosition(this._tempVec3);
    const thumbPos = this._tempVec3.clone();
    indexTip.getWorldPosition(this._tempVec3);

    return thumbPos.distanceTo(this._tempVec3) < threshold;
  }

  cleanup() {
    this.renderer.xr.enabled = false;
    this.session = null;
    // 释放手部模型和控制器资源
    [this.hands.left, this.hands.right].forEach(hand => {
      if (hand) this.scene.remove(hand);
    });
    console.log('XR 会话已清理');
  }
}
```

### A-Frame 组件化 XR 场景

```html
<a-scene webxr="requiredFeatures: local-floor;
                optionalFeatures: hand-tracking, hit-test"
         renderer="colorManagement: true; physicallyCorrectLights: true;
                   antialias: true; maxCanvasWidth: 1920">

  <!-- 性能：LOD 系统 -->
  <a-entity lod-model="low: #model-low; mid: #model-mid; high: #model-high;
                        distances: 5 15 30">
  </a-entity>

  <!-- 交互表面 -->
  <a-entity id="ui-panel" position="0 1.5 -1.5"
            xr-interactable="type: panel; haptic: true"
            material="shader: flat; transparent: true; opacity: 0.85">
    <a-text value="状态面板" align="center" color="#fff"
            width="2" position="0 0.3 0.01">
    </a-text>
  </a-entity>

  <!-- 手部交互射线 -->
  <a-entity id="left-ray" laser-controls="hand: left; model: false"
            raycaster="objects: .interactive; far: 5; lineColor: #44aaff">
  </a-entity>
</a-scene>
```
