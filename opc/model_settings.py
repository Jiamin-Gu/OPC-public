"""模型连接：在设置里定义一次（服务商、接口地址、密钥、常用模型），公司和角色只选"用哪个连接的哪个模型"。

三层：全局默认 → 公司默认 → 角色单独选择（可选，不选就跟随公司）。改公司默认，所有跟随的角色一起变；
密钥只存在这里（~/.opc/models.json，和 .env 一样只在本机），不再写进每家公司的配置。

没有配置文件时，用环境变量（OPC_PROVIDER / OPC_BASE_URL / OPC_API_KEY / OPC_MODEL）作为唯一的连接"环境变量"，
它的密钥不会写进文件。引用写成 {"connection": 连接 id, "model": 模型名}。
"""
import json
import re
import threading
import uuid
from pathlib import Path

from . import providers
from .config import LLMConfig, default_llm_config, opc_home

ENV_ID = "env"
_lock = threading.Lock()


def _file(home=None):
    return Path(home or opc_home()) / "models.json"


def _env_connection() -> dict:
    env = default_llm_config()
    p = providers.get(env.provider)
    models = list(dict.fromkeys([env.model, *p.models]))
    return {"id": ENV_ID, "name": f"{p.name}（环境变量）", "provider": env.provider, "base_url": env.base_url,
            "api_key": env.api_key, "models": models, "env": True}


def load(home=None) -> dict:
    """{"connections": [...], "default": {"connection", "model"}}。环境变量里配了密钥（或者还没有别的连接）时，
    "环境变量"连接排在第一个。"""
    try:
        data = json.loads(_file(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    saved = [c for c in data.get("connections") or [] if isinstance(c, dict) and c.get("id") and c.get("id") != ENV_ID]
    env = _env_connection()
    connections = ([env] if env["api_key"] or not saved else []) + saved
    default = data.get("default") or {}
    if not find(connections, default.get("connection")):
        first = connections[0]
        default = {"connection": first["id"], "model": (first.get("models") or [""])[0]}
    ids = {c["id"] for c in connections}
    prices = {k: v for k, v in (data.get("prices") or {}).items() if k in ids and isinstance(v, dict)}
    return {"connections": connections, "default": default, "prices": prices}


def find(connections: list[dict], connection_id: str | None) -> dict | None:
    return next((c for c in connections if c["id"] == connection_id), None)


def clean_ref(ref, settings: dict) -> dict | None:
    """校验界面传来的引用：空的表示"跟随上一层"；连接不存在时报错。"""
    if not isinstance(ref, dict) or not ref.get("connection"):
        return None
    conn = find(settings["connections"], str(ref["connection"]))
    if not conn:
        raise ValueError(f"模型连接不存在：{ref['connection']}")
    model = str(ref.get("model") or "").strip()[:100] or (conn.get("models") or [""])[0]
    return {"connection": conn["id"], "model": model}


def config(ref: dict | None, settings: dict | None = None) -> LLMConfig | None:
    """按引用找到模型配置；连接不存在时返回 None（调用方退回到上一层）。"""
    if not ref:
        return None
    settings = settings or load()
    conn = find(settings["connections"], ref.get("connection"))
    if not conn:
        return None
    model = str(ref.get("model") or "").strip() or (conn.get("models") or [""])[0]
    return LLMConfig(api_key=conn.get("api_key", ""), base_url=conn.get("base_url") or providers.get(conn["provider"]).base_url,
                     model=model, provider=conn["provider"])


def default_config(settings: dict | None = None) -> LLMConfig:
    settings = settings or load()
    return config(settings["default"], settings) or default_llm_config()


def describe(ref: dict | None, settings: dict | None = None) -> str:
    """给界面看的"连接 · 模型"。"""
    settings = settings or load()
    conn = find(settings["connections"], (ref or {}).get("connection"))
    if not conn:
        return ""
    return f"{conn['name']} · {str((ref or {}).get('model') or '').strip() or (conn.get('models') or [''])[0]}"


def price(ref: dict | None, settings: dict) -> dict | None:
    """这个引用所在连接的价格 {input, cached, output}（元/百万 token），没填返回 None。"""
    conn = find(settings["connections"], (ref or {}).get("connection"))
    p = (settings.get("prices") or {}).get(conn["id"]) if conn else None
    return p if p and any(p.values()) else None


def _clean_price(p) -> dict | None:
    if not isinstance(p, dict):
        return None
    out = {}
    for k in ("input", "cached", "output"):
        try:
            out[k] = max(0.0, min(float(p.get(k) or 0), 10000.0))
        except (TypeError, ValueError):
            out[k] = 0.0
    return out if any(out.values()) else None


def public(settings: dict | None = None) -> dict:
    """发给浏览器：密钥只显示有没有设置。"""
    settings = settings or load()
    conns = [{**{k: v for k, v in c.items() if k != "api_key"}, "has_key": bool(c.get("api_key"))}
             for c in settings["connections"]]
    return {"connections": conns, "default": settings["default"], "default_label": describe(settings["default"], settings),
            "prices": settings.get("prices") or {}}


def save(connections: list[dict], default: dict | None, home=None, prices: dict | None = None) -> dict:
    """保存界面上编辑的连接。密钥留空表示不改；"环境变量"连接不能改（它来自 .env）。返回保存后的设置。"""
    with _lock:
        old = {c["id"]: c for c in load(home)["connections"]}
        out = []
        for c in connections:
            if not isinstance(c, dict) or c.get("id") == ENV_ID:
                continue
            provider = str(c.get("provider") or "custom")
            if provider not in providers.PROVIDERS:
                raise ValueError(f"未知的服务商：{provider}")
            name = str(c.get("name") or "").strip() or providers.get(provider).name
            base_url = str(c.get("base_url") or "").strip() or providers.get(provider).base_url
            if not base_url:
                raise ValueError(f"连接「{name}」需要填写接口地址")
            cid = str(c.get("id") or "").strip()
            if not re.fullmatch(r"[a-z0-9-]{1,40}", cid):
                cid = f"c-{uuid.uuid4().hex[:6]}"
            key = str(c.get("api_key") or "").strip() or old.get(cid, {}).get("api_key", "")
            models = [m.strip() for m in (c.get("models") or []) if str(m).strip()] or providers.get(provider).models[:1]
            if not models:
                raise ValueError(f"连接「{name}」至少要填一个模型名")
            out.append({"id": cid, "name": name, "provider": provider, "base_url": base_url, "api_key": key,
                        "models": list(dict.fromkeys(models))})
        _file(home).parent.mkdir(parents=True, exist_ok=True)
        tmp = _file(home).with_suffix(".tmp")
        # 价格可以给"环境变量"连接设置（它本身来自 .env，不能改）；新连接的临时 id 在上面可能被重新分配，按新 id 存
        valid = {c["id"] for c in out} | {ENV_ID}
        clean = {k: p for k, p in ((k, _clean_price(v)) for k, v in (prices or {}).items()) if k in valid and p}
        tmp.write_text(json.dumps({"connections": out, "default": default or {}, "prices": clean},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_file(home))
    return load(home)
