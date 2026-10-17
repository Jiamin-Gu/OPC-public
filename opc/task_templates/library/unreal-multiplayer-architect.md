## 技术交付物

### 复制 Actor 设置
```cpp
// AMyNetworkedActor.h
UCLASS()
class MYGAME_API AMyNetworkedActor : public AActor
{
    GENERATED_BODY()

public:
    AMyNetworkedActor();
    virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

    // 复制到所有客户端——带 RepNotify 用于客户端响应
    UPROPERTY(ReplicatedUsing=OnRep_Health)
    float Health = 100.f;

    // 仅复制到拥有者——私有状态
    UPROPERTY(Replicated)
    int32 PrivateInventoryCount = 0;

    UFUNCTION()
    void OnRep_Health();

    // 带验证的 Server RPC
    UFUNCTION(Server, Reliable, WithValidation)
    void ServerRequestInteract(AActor* Target);
    bool ServerRequestInteract_Validate(AActor* Target);
    void ServerRequestInteract_Implementation(AActor* Target);

    // 装饰效果用 Multicast
    UFUNCTION(NetMulticast, Unreliable)
    void MulticastPlayHitEffect(FVector HitLocation);
    void MulticastPlayHitEffect_Implementation(FVector HitLocation);
};

// AMyNetworkedActor.cpp
void AMyNetworkedActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(AMyNetworkedActor, Health);
    DOREPLIFETIME_CONDITION(AMyNetworkedActor, PrivateInventoryCount, COND_OwnerOnly);
}

bool AMyNetworkedActor::ServerRequestInteract_Validate(AActor* Target)
{
    // 服务端验证——拒绝不可能的请求
    if (!IsValid(Target)) return false;
    float Distance = FVector::Dist(GetActorLocation(), Target->GetActorLocation());
    return Distance < 200.f; // 最大交互距离
}

void AMyNetworkedActor::ServerRequestInteract_Implementation(AActor* Target)
{
    // 可以安全执行——验证已通过
    PerformInteraction(Target);
}
```

### GameMode / GameState 架构
```cpp
// AMyGameMode.h — 仅服务端，永不复制
UCLASS()
class MYGAME_API AMyGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    virtual void PostLogin(APlayerController* NewPlayer) override;
    virtual void Logout(AController* Exiting) override;
    void OnPlayerDied(APlayerController* DeadPlayer);
    bool CheckWinCondition();
};

// AMyGameState.h — 复制到所有客户端
UCLASS()
class MYGAME_API AMyGameState : public AGameStateBase
{
    GENERATED_BODY()
public:
    virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

    UPROPERTY(Replicated)
    int32 TeamAScore = 0;

    UPROPERTY(Replicated)
    float RoundTimeRemaining = 300.f;

    UPROPERTY(ReplicatedUsing=OnRep_GamePhase)
    EGamePhase CurrentPhase = EGamePhase::Warmup;

    UFUNCTION()
    void OnRep_GamePhase();
};

// AMyPlayerState.h — 复制到所有客户端
UCLASS()
class MYGAME_API AMyPlayerState : public APlayerState
{
    GENERATED_BODY()
public:
    UPROPERTY(Replicated) int32 Kills = 0;
    UPROPERTY(Replicated) int32 Deaths = 0;
    UPROPERTY(Replicated) FString SelectedCharacter;
};
```

### GAS 复制设置
```cpp
// 在角色头文件中——AbilitySystemComponent 必须正确设置以支持复制
UCLASS()
class MYGAME_API AMyCharacter : public ACharacter, public IAbilitySystemInterface
{
    GENERATED_BODY()

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="GAS")
    UAbilitySystemComponent* AbilitySystemComponent;

    UPROPERTY()
    UMyAttributeSet* AttributeSet;

public:
    virtual UAbilitySystemComponent* GetAbilitySystemComponent() const override
    { return AbilitySystemComponent; }

    virtual void PossessedBy(AController* NewController) override;  // 服务端：初始化 GAS
    virtual void OnRep_PlayerState() override;                       // 客户端：初始化 GAS
};

// 在 .cpp 中——客户端/服务端需要双路径初始化
void AMyCharacter::PossessedBy(AController* NewController)
{
    Super::PossessedBy(NewController);
    // 服务端路径
    AbilitySystemComponent->InitAbilityActorInfo(GetPlayerState(), this);
    AttributeSet = Cast<UMyAttributeSet>(AbilitySystemComponent->GetOrSpawnAttributes(UMyAttributeSet::StaticClass(), 1)[0]);
}

void AMyCharacter::OnRep_PlayerState()
{
    Super::OnRep_PlayerState();
    // 客户端路径——PlayerState 通过复制到达
    AbilitySystemComponent->InitAbilityActorInfo(GetPlayerState(), this);
}
```

### 网络频率优化
```cpp
// 在构造函数中按 Actor 类设置复制频率
AMyProjectile::AMyProjectile()
{
    bReplicates = true;
    NetUpdateFrequency = 100.f; // 高频——快速移动，精度关键
    MinNetUpdateFrequency = 33.f;
}

AMyNPCEnemy::AMyNPCEnemy()
{
    bReplicates = true;
    NetUpdateFrequency = 20.f;  // 较低——非玩家，位置通过插值
    MinNetUpdateFrequency = 5.f;
}

AMyEnvironmentActor::AMyEnvironmentActor()
{
    bReplicates = true;
    NetUpdateFrequency = 2.f;   // 极低——状态极少变化
    bOnlyRelevantToOwner = false;
}
```

### 专用服务器构建配置
```ini
# DefaultGame.ini — 服务器配置
[/Script/EngineSettings.GameMapsSettings]
GameDefaultMap=/Game/Maps/MainMenu
ServerDefaultMap=/Game/Maps/GameLevel

[/Script/Engine.GameNetworkManager]
TotalNetBandwidth=32000
MaxDynamicBandwidth=7000
MinDynamicBandwidth=4000

# Package.bat — 专用服务器构建
RunUAT.bat BuildCookRun
  -project="MyGame.uproject"
  -platform=Linux
  -server
  -serverconfig=Shipping
  -cook -build -stage -archive
  -archivedirectory="Build/Server"
```
