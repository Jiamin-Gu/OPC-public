"""消息记录：所有频道的消息存在 LangGraph 的 Store 里，只追加。

之前消息放在图的 state 里，LangGraph 每一步都把完整的消息历史再存一份检查点（几十条长消息后每个检查点
就有几百 KB，数据库随使用平方增长）。现在 state 里只留任务看板这类小数据，消息单独存：
- 命名空间：("messages", 公司 id, 频道)，频道是 "all"（全公司）或角色 id
- key：严格递增的写入序号（纳秒时间）+ 消息 id，按 key 排序就是消息的先后顺序
节点通过 runtime.store 拿到 Store；运行时和导出、知识库直接读。
"""
import threading
import time

from langgraph.store.base import BaseStore

ALL = "all"
PAGE = 1000
_seq_lock = threading.Lock()
_last_seq = 0


def _next_seq() -> int:
    """严格递增的序号（纳秒时间）：Windows 的时钟精度只有几毫秒，连续写入时 time_ns() 会相同，顺序就乱了。"""
    global _last_seq
    with _seq_lock:
        _last_seq = max(time.time_ns(), _last_seq + 1)
        return _last_seq


class MessageLog:
    def __init__(self, store: BaseStore, company_id: str):
        self.store, self.company_id = store, company_id

    def _ns(self, channel: str) -> tuple[str, ...]:
        return ("messages", self.company_id, channel)

    def append(self, channel: str, *messages: dict) -> None:
        for m in messages:
            self.store.put(self._ns(channel), f"{_next_seq():020d}-{m['id']}", dict(m))

    def read(self, channel: str, last: int | None = None) -> list[dict]:
        items, offset = [], 0
        while True:
            page = self.store.search(self._ns(channel), limit=PAGE, offset=offset)
            items += page
            if len(page) < PAGE:
                break
            offset += PAGE
        msgs = [it.value for it in sorted(items, key=lambda it: it.key)]
        return msgs[-last:] if last else msgs

    def channels(self) -> list[str]:
        return [ns[2] for ns in self.store.list_namespaces(prefix=("messages", self.company_id), limit=1000)]

    def all(self) -> dict[str, list[dict]]:
        out = {ch: self.read(ch) for ch in self.channels()}
        return {ch: msgs for ch, msgs in out.items() if msgs}  # 删光的频道有的 Store 还会列出来

    def notes(self, directive_id: str | None, role_id: str) -> list[dict]:
        """这条指令下 CEO 发给全公司或发给这个角色的实时插话。"""
        if not directive_id:
            return []
        out = []
        for ch in (ALL, role_id):  # 用 Store 的字段筛选：每个任务开始时都要读，不能每次都读完整个频道
            items = self.store.search(self._ns(ch), filter={"kind": "interject", "directive_id": directive_id},
                                      limit=PAGE)
            out += [it.value for it in sorted(items, key=lambda it: it.key)]
        return out

    def delete_all(self) -> None:
        for ch in self.channels():
            for it in self.store.search(self._ns(ch), limit=100_000):
                self.store.delete(self._ns(ch), it.key)

    def import_channels(self, channels: dict[str, list[dict]]) -> int:
        """把旧版本存在检查点 state 里的消息搬过来（按原来的顺序）。返回搬了多少条。"""
        n = 0
        for ch, msgs in channels.items():
            self.append(ch, *msgs)
            n += len(msgs)
        return n


def open_sqlite_store(path) -> BaseStore:
    """和检查点放在同一个 SQLite 文件里（不同的表）。Store 需要自动提交模式的连接。"""
    import sqlite3

    from langgraph.store.sqlite import SqliteStore
    store = SqliteStore(sqlite3.connect(str(path), check_same_thread=False, isolation_level=None))
    store.setup()
    return store


def migrate_legacy(checkpointer, store: BaseStore, company_id: str) -> int:
    """v0.7 及以前消息存在检查点 state 的 channels 里：第一次打开时搬进 Store（Store 里已经有消息就不动）。"""
    log = MessageLog(store, company_id)
    if log.all():
        return 0
    tup = checkpointer.get_tuple({"configurable": {"thread_id": company_id}})
    legacy = (tup.checkpoint.get("channel_values", {}).get("channels") if tup else None) or {}
    return log.import_channels(legacy)
