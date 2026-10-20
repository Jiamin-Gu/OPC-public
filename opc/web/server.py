"""HTTP + WebSocket 服务。创建/删除公司和读取文件走 HTTP，其余交互走 /ws。"""
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, model_settings
from ..company import FileError
from .. import tracing
from ..knowledge import service as kb_service
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
    kb: bool = True            # 角色能检索公司知识库（长期记忆）


class KbIn(BaseModel):
    enabled: bool


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
                                       model=body.model, effort_level=body.effort, kb=body.kb)
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

    @app.put("/api/company/kb")
    def put_company_kb(body: KbIn):
        """打开 / 关闭角色检索知识库（之后开始的工作生效）。"""
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        hub.company.set_kb(body.enabled)
        hub.broadcast(hub.snapshot_event())
        return {"ok": True}

    # ---------- 链路追踪（可观测性） ----------

    @app.get("/api/traces")
    def get_traces(limit: int = 30):
        """最近的链路：每条指令、每次私聊一条，带步骤数、模型调用次数、token、出错数。"""
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        return {"traces": tracing.store_for(hub.company).traces(max(1, min(limit, 100)))}

    @app.get("/api/trace")
    def get_trace(id: str = "", key: str = ""):
        """一条链路的全部步骤。按链路 id 或按 key（例如 directive:<指令 id>）查。"""
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        tid = id or (tracing.trace_id(hub.company, key) if key else "")
        spans = tracing.store_for(hub.company).trace(tid) if tid else []
        if not spans:
            raise HTTPException(404, "没有这条链路（以前的指令没有记录，v0.22 起才有）")
        return {"trace_id": tid, "spans": spans}

    # ---------- 资料库：公司资料（只属于当前公司）和个人资料（所有公司共用） ----------

    @app.get("/api/library")
    def get_library():
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        return kb_service.list_library(hub.company)

    @app.post("/api/library/{scope}")
    async def upload_library(scope: str, request: Request, name: str = ""):
        """上传一份资料：请求体就是文件内容，文件名放在 name 参数里（不用 multipart，省一个依赖）。
        同名文件会被替换。上传后立即解析、切块、入库，返回切了几块、解析失败的原因。"""
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        data = await request.body()
        try:
            return await asyncio.to_thread(kb_service.add_document, hub.company, scope, name, data)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/library/{scope}")
    def delete_library(scope: str, name: str):
        if not hub.company:
            raise HTTPException(404, "没有打开的公司")
        try:
            if not kb_service.remove_document(hub.company, scope, name):
                raise HTTPException(404, "没有这份资料")
        except ValueError as e:
            raise HTTPException(400, str(e))
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
        # 人看的搜索才重排（排序更准，多约 2.5 秒）；给角色的检索不重排，见 knowledge/rerank.py
        results = kb.search(q, max(1, min(k, 30)), mode, rerank=True) if q.strip() else []
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
