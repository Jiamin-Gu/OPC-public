"""链路追踪（可观测性）：记录一条指令从下令到汇报的完整过程，每一步多久、花了多少 token、在哪里出错。

一条指令是一条链路（trace），里面是一层层的步骤（span），结构和 OpenTelemetry 一样：

    指令（根）
    ├─ 规划              └─ 模型调用（模型、输入 / 输出 / 缓存 token、首字延迟、重试次数）
    ├─ 执行《写 PRD》（产品经理）
    │   ├─ 模型调用 ×N
    │   ├─ 工具 read_file / kb_search / write_file ……
    │   └─ 工具 code_agent → 编码 agent 自己的模型调用和工具（嵌套在里面）
    ├─ 审查《写 PRD》（工程师）……
    └─ 汇报

- 父子关系靠 contextvars 里的"当前步骤"：每个工作运行、角色执行循环、模型调用、工具调用开始时打开一个步骤，
  里面再发生的调用自动挂在它下面。跨线程的地方（执行引擎开工作运行、角色执行循环在空上下文里跑）显式传过去
- 存在公司目录的 traces.sqlite 里，步骤一开始就写入（进行中的也能看到），结束时补上结束时间和结果
- 链路 id 由公司 id + 指令 id 算出来：同一条指令分几次事件处理（下令、审批、任务完成），都挂在同一条链路上
- 没有打开的链路时（测试、命令行单独调用模型）什么都不记，不影响原来的逻辑
- 默认只记内容的开头几百字（够看出在干什么）；OPC_TRACE_CONTENT=full 时记完整的输入输出
- 可以按 OpenTelemetry 标准（OTLP/HTTP JSON）导出到 Jaeger、Langfuse 等，见 export_otlp
"""
import contextvars
import hashlib
import json
import os
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

PREVIEW = 300          # 默认记录的内容长度
KEEP_TRACES = 100      # 每家公司最多保留多少条链路（超过就删最早的）
_current: contextvars.ContextVar = contextvars.ContextVar("opc_trace_span", default=None)
_stores: dict[Path, "TraceStore"] = {}
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY, trace_id TEXT, parent_id TEXT, name TEXT, kind TEXT,
    start REAL, "end" REAL, status TEXT, attrs TEXT);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
"""


def _hex(*parts: str, n: int = 32) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:n]


def full_content() -> bool:
    return os.getenv("OPC_TRACE_CONTENT", "").strip().lower() == "full"


def preview(text, limit: int = PREVIEW) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return text if full_content() or len(text) <= limit else text[:limit] + "……"


class TraceStore:
    """一家公司的链路数据（traces.sqlite）。多个线程同时写，用一把锁。"""

    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        # 链路数据丢最后几条也没关系：WAL + 不每次落盘，写入快很多（每个步骤开始、结束各写一次）
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def write(self, span: "Span") -> None:
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (span.span_id, span.trace_id, span.parent_id, span.name, span.kind, span.start,
                             span.end, span.status, json.dumps(span.attrs, ensure_ascii=False, default=str)))
            self.db.commit()

    def get(self, span_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,)).fetchone()
        return _row(row) if row else None

    def trace(self, trace_id: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY start", (trace_id,)).fetchall()
        return [_row(r) for r in rows]

    def traces(self, limit: int = 50) -> list[dict]:
        """最近的链路（根步骤）+ 统计：步骤数、模型调用次数、token、出错的步骤数。"""
        with self.lock:
            roots = self.db.execute("SELECT * FROM spans WHERE parent_id IS NULL ORDER BY start DESC, rowid DESC LIMIT ?",
                                    (limit,)).fetchall()
            out = []
            for r in roots:
                root = _row(r)
                stats = self.db.execute(
                    "SELECT COUNT(*), SUM(kind = 'llm'), SUM(status = 'error'), MAX(COALESCE(\"end\", start)) "
                    "FROM spans WHERE trace_id = ?", (root["trace_id"],)).fetchone()
                tokens = self.db.execute(
                    "SELECT attrs FROM spans WHERE trace_id = ? AND kind = 'llm'", (root["trace_id"],)).fetchall()
                usage = {"input": 0, "output": 0, "cached": 0}
                for (a,) in tokens:
                    a = json.loads(a or "{}")
                    usage["input"] += a.get("gen_ai.usage.input_tokens") or 0
                    usage["output"] += a.get("gen_ai.usage.output_tokens") or 0
                    usage["cached"] += a.get("gen_ai.usage.cached_tokens") or 0
                out.append({**root, "spans": stats[0], "llm_calls": stats[1] or 0, "errors": stats[2] or 0,
                            "last": stats[3], "usage": usage})
            return out

    def prune(self, keep: int = KEEP_TRACES) -> None:
        with self.lock:
            old = self.db.execute("SELECT trace_id FROM spans WHERE parent_id IS NULL ORDER BY start DESC, rowid DESC "
                                  "LIMIT -1 OFFSET ?", (keep,)).fetchall()
            for (tid,) in old:
                self.db.execute("DELETE FROM spans WHERE trace_id = ?", (tid,))
            if old:
                self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()


def _row(r) -> dict:
    return {"span_id": r[0], "trace_id": r[1], "parent_id": r[2], "name": r[3], "kind": r[4], "start": r[5],
            "end": r[6], "status": r[7], "attrs": json.loads(r[8] or "{}")}


def store_for(company) -> TraceStore:
    with _lock:
        path = Path(company.root) / "traces.sqlite"
        if path not in _stores:
            _stores[path] = TraceStore(path)
        return _stores[path]


def close(root: Path) -> None:
    """关掉一家公司的链路数据库（删除公司前，Windows 上文件被占用会删不掉）。"""
    with _lock:
        s = _stores.pop(Path(root) / "traces.sqlite", None)
    if s:
        s.close()


def reset() -> None:
    with _lock:
        for s in _stores.values():
            try:
                s.close()
            except Exception:
                pass
        _stores.clear()


class Span:
    def __init__(self, store: TraceStore | None, trace_id: str, span_id: str, parent_id: str | None,
                 name: str, kind: str, attrs: dict | None = None, start: float | None = None):
        self.store, self.trace_id, self.span_id, self.parent_id = store, trace_id, span_id, parent_id
        self.name, self.kind, self.attrs = name, kind, dict(attrs or {})
        self.start, self.end, self.status = start or time.time(), None, "running"

    def set(self, **attrs) -> "Span":
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})
        return self

    def child(self, name: str, kind: str, **attrs) -> "Span":
        span = Span(self.store, self.trace_id, os.urandom(8).hex(), self.span_id, name, kind, attrs)
        span._save()
        return span

    def finish(self, error: BaseException | str | None = None, status: str | None = None) -> None:
        if self.end is not None:
            return
        self.end = time.time()
        if error is not None:
            self.status = "error"
            self.attrs["error"] = error if isinstance(error, str) else f"{type(error).__name__}: {error}"[:500]
        else:
            self.status = status or "ok"
        self._save()
        _export(self)

    def _save(self) -> None:
        if self.store is not None:
            try:
                self.store.write(self)
            except Exception:  # 链路记录失败不能影响业务
                pass


class _Noop(Span):
    """没有打开的链路时用：所有操作都什么也不做。"""

    def __init__(self):
        super().__init__(None, "", "", None, "", "noop")

    def child(self, name, kind, **attrs):
        return self

    def finish(self, error=None, status=None):
        pass


NOOP = _Noop()


def current() -> Span | None:
    return _current.get()


@contextmanager
def use(span: Span | None):
    """在这段代码里把 span 当作"当前步骤"（里面打开的步骤挂在它下面）。span 是 None 时什么也不做。"""
    if span is None:
        yield span
        return
    token = _current.set(span)
    try:
        yield span
    finally:
        _current.reset(token)


@contextmanager
def span(name: str, kind: str = "step", **attrs):
    """在当前步骤下面打开一个子步骤，代码块结束时关闭（出异常记为出错，异常照常抛出）。没有当前步骤时什么也不记。"""
    parent = current()
    s = parent.child(name, kind, **attrs) if parent is not None else NOOP
    token = _current.set(s) if s is not NOOP else None
    try:
        yield s
    except BaseException as e:
        s.finish(error=e)
        raise
    else:
        s.finish()
    finally:
        if token is not None:
            _current.reset(token)


def trace_id(company, key: str) -> str:
    return _hex(company.id, key)


def root(company, key: str, name: str, kind: str = "directive", **attrs) -> Span:
    """一条链路的根步骤（例如 key = "directive:<指令 id>"）。同一个 key 再次调用拿到的是同一个根（不重新计时）。"""
    store = store_for(company)
    tid = trace_id(company, key)
    span_id = _hex(company.id, key, "root", n=16)
    existing = store.get(span_id)
    if existing:
        s = Span(store, tid, span_id, None, existing["name"], existing["kind"], existing["attrs"], existing["start"])
        s.end, s.status = existing["end"], existing["status"]
        if s.end is not None:  # 结束过又有新动作（例如汇报后 CEO 让某个任务返工）：重新打开
            s.end, s.status = None, "running"
            s._save()
        return s.set(**attrs)
    s = Span(store, tid, span_id, None, name, kind, {"key": key, **attrs})
    s._save()
    store.prune()
    return s


# ---------------------------------------------------------------- OTLP 导出（可选）

_queue: queue.Queue = queue.Queue()
_exporter: threading.Thread | None = None


def _export(span: Span) -> None:
    if span.store is None or not os.getenv("OPC_OTLP_ENDPOINT"):
        return
    global _exporter
    _queue.put(span)
    with _lock:
        if _exporter is None or not _exporter.is_alive():
            _exporter = threading.Thread(target=_export_loop, daemon=True, name="otlp-export")
            _exporter.start()


def _export_loop() -> None:
    while True:
        batch = [_queue.get()]
        time.sleep(1.0)  # 攒一秒再发
        while not _queue.empty() and len(batch) < 500:
            batch.append(_queue.get_nowait())
        try:
            export_otlp(batch)
        except Exception:
            pass  # 导出失败不影响 OPC 本身（收集端没开、网络不通）


def _value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    return {"stringValue": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)}


def otlp_payload(spans: list[Span]) -> dict:
    """OTLP/HTTP 的 JSON 格式（https://opentelemetry.io/docs/specs/otlp/）。模型调用的属性用 GenAI 语义约定的名字。"""
    kinds = {"llm": 3, "tool": 1}  # CLIENT / INTERNAL
    out = []
    for s in spans:
        item = {"traceId": s.trace_id, "spanId": s.span_id, "name": s.name, "kind": kinds.get(s.kind, 1),
                "startTimeUnixNano": str(int(s.start * 1e9)), "endTimeUnixNano": str(int((s.end or s.start) * 1e9)),
                "attributes": [{"key": k, "value": _value(v)} for k, v in s.attrs.items()]
                + [{"key": "opc.kind", "value": {"stringValue": s.kind}}],
                "status": {"code": 2, "message": str(s.attrs.get("error", ""))} if s.status == "error" else {"code": 1}}
        if s.parent_id:
            item["parentSpanId"] = s.parent_id
        out.append(item)
    from . import __version__
    return {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "opc"}},
                                    {"key": "service.version", "value": {"stringValue": __version__}}]},
        "scopeSpans": [{"scope": {"name": "opc.tracing"}, "spans": out}]}]}


def export_otlp(spans: list[Span], client=None) -> None:
    """发给 OPC_OTLP_ENDPOINT（例如 http://127.0.0.1:4318/v1/traces）。OPC_OTLP_HEADERS 写额外的请求头，
    "名字=值" 用逗号隔开（例如 Langfuse 的 Authorization=Basic ...）。"""
    import httpx
    endpoint = os.environ["OPC_OTLP_ENDPOINT"]
    headers = {"Content-Type": "application/json"}
    for pair in filter(None, (os.getenv("OPC_OTLP_HEADERS") or "").split(",")):
        k, _, v = pair.partition("=")
        headers[k.strip()] = v.strip()
    client = client or httpx.Client(timeout=10)
    client.post(endpoint, json=otlp_payload(spans), headers=headers).raise_for_status()
