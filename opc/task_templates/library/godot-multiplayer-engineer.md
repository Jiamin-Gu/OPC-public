## 技术交付物

### 服务端搭建（ENet）
```gdscript
# NetworkManager.gd — Autoload
extends Node

const PORT := 7777
const MAX_CLIENTS := 8

signal player_connected(peer_id: int)
signal player_disconnected(peer_id: int)
signal server_disconnected

func create_server() -> Error:
    var peer := ENetMultiplayerPeer.new()
    var error := peer.create_server(PORT, MAX_CLIENTS)
    if error != OK:
        return error
    multiplayer.multiplayer_peer = peer
    multiplayer.peer_connected.connect(_on_peer_connected)
    multiplayer.peer_disconnected.connect(_on_peer_disconnected)
    return OK

func join_server(address: String) -> Error:
    var peer := ENetMultiplayerPeer.new()
    var error := peer.create_client(address, PORT)
    if error != OK:
        return error
    multiplayer.multiplayer_peer = peer
    multiplayer.server_disconnected.connect(_on_server_disconnected)
    return OK

func disconnect_from_network() -> void:
    multiplayer.multiplayer_peer = null

func _on_peer_connected(peer_id: int) -> void:
    player_connected.emit(peer_id)

func _on_peer_disconnected(peer_id: int) -> void:
    player_disconnected.emit(peer_id)

func _on_server_disconnected() -> void:
    server_disconnected.emit()
    multiplayer.multiplayer_peer = null
```

### 服务端权威玩家控制器
```gdscript
# Player.gd
extends CharacterBody2D

# 由服务端拥有和验证的状态
var _server_position: Vector2 = Vector2.ZERO
var _health: float = 100.0

@onready var synchronizer: MultiplayerSynchronizer = $MultiplayerSynchronizer

func _ready() -> void:
    # 每个玩家节点的权威 = 该玩家的 peer ID
    set_multiplayer_authority(name.to_int())

func _physics_process(delta: float) -> void:
    if not is_multiplayer_authority():
        # 非权威方：仅接收同步状态
        return
    var input_dir := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
    velocity = input_dir * 200.0
    move_and_slide()

# 客户端向服务端发送输入
@rpc("any_peer", "unreliable")
func send_input(direction: Vector2) -> void:
    if not multiplayer.is_server():
        return
    # 服务端验证输入的合理性
    var sender_id := multiplayer.get_remote_sender_id()
    if sender_id != get_multiplayer_authority():
        return  # 拒绝：错误的 peer 为此玩家发送了输入
    velocity = direction.normalized() * 200.0
    move_and_slide()

# 服务端向所有客户端确认命中
@rpc("authority", "reliable", "call_local")
func take_damage(amount: float) -> void:
    _health -= amount
    if _health <= 0.0:
        _on_died()
```

### MultiplayerSynchronizer 配置
```gdscript
# 在场景中：Player.tscn
# 将 MultiplayerSynchronizer 作为 Player 节点的子节点
# 在 _ready 中或通过场景属性配置：

func _ready() -> void:
    var sync := $MultiplayerSynchronizer

    # 将位置同步给所有 peer——仅在变化时（不是每帧）
    var config := sync.replication_config
    # 通过编辑器添加：Property Path = "position"，Mode = ON_CHANGE
    # 或通过代码：
    var property_entry := SceneReplicationConfig.new()
    # 推荐使用编辑器——确保正确的序列化设置

    # 此 synchronizer 的权威 = 与节点权威相同
    # synchronizer 从权威方广播到其他所有方
```

### MultiplayerSpawner 设置
```gdscript
# GameWorld.gd — 在服务端
extends Node2D

@onready var spawner: MultiplayerSpawner = $MultiplayerSpawner

func _ready() -> void:
    if not multiplayer.is_server():
        return
    # 注册可以被生成的场景
    spawner.spawn_path = NodePath(".")  # 作为此节点的子节点生成

    # 连接玩家加入到生成逻辑
    NetworkManager.player_connected.connect(_on_player_connected)
    NetworkManager.player_disconnected.connect(_on_player_disconnected)

func _on_player_connected(peer_id: int) -> void:
    # 服务端为每个连接的 peer 生成一个玩家
    var player := preload("res://scenes/Player.tscn").instantiate()
    player.name = str(peer_id)  # 名称 = peer ID 用于权威查找
    add_child(player)           # MultiplayerSpawner 自动复制到所有 peer
    player.set_multiplayer_authority(peer_id)

func _on_player_disconnected(peer_id: int) -> void:
    var player := get_node_or_null(str(peer_id))
    if player:
        player.queue_free()  # MultiplayerSpawner 自动在各 peer 上移除
```

### RPC 安全模式
```gdscript
# 安全做法：在处理前验证发送者
@rpc("any_peer", "reliable")
func request_pick_up_item(item_id: int) -> void:
    if not multiplayer.is_server():
        return  # 只有服务端处理

    var sender_id := multiplayer.get_remote_sender_id()
    var player := get_player_by_peer_id(sender_id)

    if not is_instance_valid(player):
        return

    var item := get_item_by_id(item_id)
    if not is_instance_valid(item):
        return

    # 验证：玩家距离是否够近？
    if player.global_position.distance_to(item.global_position) > 100.0:
        return  # 拒绝：超出范围

    # 安全处理
    _give_item_to_player(player, item)
    confirm_item_pickup.rpc(sender_id, item_id)  # 确认回传给客户端

@rpc("authority", "reliable")
func confirm_item_pickup(peer_id: int, item_id: int) -> void:
    # 仅在客户端运行（由服务端权威方调用）
    if multiplayer.get_unique_id() == peer_id:
        UIManager.show_pickup_notification(item_id)
```
