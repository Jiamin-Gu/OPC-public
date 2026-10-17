"""HTTP + WebSocket 服务。创建/删除公司和读取文件走 HTTP，其余交互走 /ws。"""
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, model_settings
from ..company import FileError
from .guard import Guard
from .runtime import Hub

STATIC = Path(__file__).parent / "static"


class RoleIn(BaseModel):
    id: str
    model: dict | str | None = None  # 模型连接的引用 {connection, model}，空表示跟随公司默认
    provider: str = ""  # 以下三项是 v0.14 以前的写法（角色上直接写服务商、地址、密钥），仍然支持
    base_url: str = ""
    api_key: str = ""
    prompt_level: str = ""  # 旧接口的字段（v0.18 起角色提示词一律用完整版），仍然接受


class CompanyIn(BaseModel):
    name: str
    mission: str = ""
    roles: list[RoleIn]
    allow_run: bool = False
    web: bool = False  # 允许角色联网（经 MCP 服务器）
    prompt_level: str = "complete"  # 旧接口的字段（v0.18 起角色提示词一律用完整版），仍然接受
    model: dict | None = None  # 公司默认模型（模型连接的引用），空表示用全局默认
    effort: str = "standard"   # 投入档位：lean 节省 / standard 标准 / thorough 充分（只限轮数和次数，不限 token）


class ModelsIn(BaseModel):
    connections: list[dict]
    default: dict | None = None
    prices: dict[str, dict] = {}  # 连接 id -> {input, cached, output}（元/百万 token，可选，只用来估算花费）


class EffortIn(BaseModel):
    effort: str


class CompanyModelsIn(BaseModel):
    default: dict | None = None    # 公司默认模型，空表示用全局默认
    roles: dict[str, dict | None] = {}  # 角色 id -> 引用，空表示跟随公司默认；没列出的角色不变


def create_app(hub: Hub, token: str | None = None, hosts: set[str] | None = None) -> FastAPI:
    """token：访问令牌（见 guard.py），None 表示不检查（只在测试里这样用）。
    hosts：允许的 Host，正式运行时是 127.0.0.1 / localhost 加端口；默认只有测试客户端用的 testserver。"""
    app = FastAPI(title="OPC")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_middleware(Guard, token=token, hosts=hosts or {"testserver"})

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"  # 前端更新后刷新即可拿到新版本
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info():
        return {"app": "opc", "version": __version__, "pid": os.getpid(), "demo": hub.demo}

    @app.post("/api/companies")
    def create_company(body: CompanyIn):
        try:
            company = hub.store.create(body.name, body.mission, [r.model_dump() for r in body.roles],
                                       allow_run=body.allow_run, web=body.web, prompt_level=body.prompt_level,
                                       model=body.model, effort_level=body.effort)
        except ValueError as e:
            raise HTTPException(400, str(e))
        hub.broadcast(hub.home_event())
        return {"id": company.id}

    @app.get("/api/models")
    def get_models():
        """模型连接（密钥只显示有没有设置）。"""
        return model_settings.public(model_settings.load(hub.store.home))

    @app.put("/api/models")
    def put_models(body: ModelsIn):
        """保存模型连接和全局默认模型。密钥留空表示不改。"""
        try:
            saved = model_settings.save(body.connections, body.default, hub.store.home, prices=body.prices)
        except ValueError as e:
            raise HTTPException(400, str(e))
        hub.broadcast(hub.home_event(), *([hub.snapshot_event()] if hub.company else []))
        return model_settings.public(saved)

    @app.put("/api/company/models")
    def put_company_models(body: CompanyModelsIn):
        """改当前公司的默认模型和各角色的模型（之后开始的任务生效，正在执行的不受影响）。"""
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        unknown = [r for r in body.roles if r not in hub.company.role_ids]
        if unknown:
            raise HTTPException(400, f"公司里没有这些角色：{'、'.join(unknown)}")
        try:
            hub.company.set_models(body.default, body.roles)
        except ValueError as e:
            raise HTTPException(400, str(e))
        hub.broadcast(hub.snapshot_event())
        return {"ok": True}

    @app.put("/api/company/effort")
    def put_company_effort(body: EffortIn):
        """改当前公司的投入档位（之后开始的工作生效，正在执行的不受影响）。"""
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        try:
            hub.company.set_effort(body.effort)
        except ValueError as e:
            raise HTTPException(400, str(e))
        hub.broadcast(hub.snapshot_event())
        return {"ok": True}

    @app.delete("/api/companies/{company_id}")
    def delete_company(company_id: str):
        try:
            hub.delete_company(company_id)
        except ValueError as e:
            raise HTTPException(409, str(e))
        hub.broadcast(hub.home_event())
        return {"ok": True}

    @app.get("/api/file")
    def read_file(path: str):
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        try:
            return {"path": path, "content": hub.company.read_file(path)}
        except FileError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/kb/search")
    def kb_search(q: str = "", k: int = 8, mode: str = "hybrid"):
        """知识库只读搜索：先增量同步，再按关键词 / 向量 / 混合检索。"""
        kb, synced = hub.knowledge()
        if kb is None:
            raise HTTPException(404, "没有打开的公司")
        if mode not in ("hybrid", "keyword", "vector"):
            raise HTTPException(400, "mode 只能是 hybrid、keyword、vector")
        results = kb.search(q, max(1, min(k, 30)), mode) if q.strip() else []
        return {"results": results, "stats": kb.stats(), "synced": synced}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue()
        hub.attach(q)

        async def pump():
            while True:
                await ws.send_json(await q.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                hub.handle(await ws.receive_json())
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            hub.detach(q)

    return app
