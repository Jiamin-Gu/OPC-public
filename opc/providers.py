"""模型服务商预设：默认接口地址和常用模型，给设置界面用（和 Coding-agent 的 providers.py 保持一致）。

所有服务商都按标准的 OpenAI 兼容接口调用：接口地址 + 密钥 + 模型名，不加任何额外的请求头。
预设里只有 DeepSeek 官方；其他服务商（以及自己搭的中转、代理）选"自定义"，填它的接口地址即可。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str                       # 默认接口地址；自定义服务商为空，由用户填写
    models: list[str] = field(default_factory=list)  # 常用模型（界面上的建议，也可以手动输入别的）
    note: str = ""


PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider("deepseek", "DeepSeek 官方", "https://api.deepseek.com", ["deepseek-flash", "deepseek-pro"],
             note="在 platform.deepseek.com 创建 API Key，按用量计费"),
    Provider("custom", "自定义（OpenAI 兼容）", "", note="填写服务商提供的接口地址"),
]}


def detect(base_url: str) -> str:
    """没有指定服务商时，按接口地址判断（兼容只写了 OPC_BASE_URL 的旧配置）。"""
    for p in PROVIDERS.values():
        if p.base_url and base_url.rstrip("/") == p.base_url:
            return p.id
    return "custom"


def normalize(provider_id: str | None) -> str:
    """不认识的服务商名（比如本机旧配置里写的、已经去掉的预设）当作自定义，接口地址照旧用配置里写的。"""
    return provider_id if provider_id in PROVIDERS else "custom"


def get(provider_id: str) -> Provider:
    return PROVIDERS[normalize(provider_id)]


def public_list() -> list[dict]:
    """给界面用的服务商列表。"""
    return [{"id": p.id, "name": p.name, "base_url": p.base_url, "models": p.models, "note": p.note}
            for p in PROVIDERS.values()]
