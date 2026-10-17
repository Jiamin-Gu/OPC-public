"""CEO 实时插话：执行过程中 CEO 说的话，送到正在工作的同事面前。

图在后台线程里运行，CEO 的消息从网页线程进来。插话由运行时直接写进消息记录（Store，见 messages.py），
同时放进这个线程安全的收件箱，让正在工作的角色立刻收到：
- 正在工作的角色每次调用模型之前都来查一次，有新消息就作为一条用户消息插进它的对话（listener）
- 之后才开始的任务，在任务说明里一次性带上这条指令下的所有插话（从消息记录里读）
"""
import threading

ALL = "all"


class Inbox:
    def __init__(self):
        self._lock = threading.Lock()
        self._items: list[tuple[str, dict]] = []  # (频道, 消息)：频道是 all 或某个角色 id

    def post(self, channel: str, message: dict) -> None:
        with self._lock:
            self._items.append((channel, message))

    def for_role(self, role_id: str) -> list[dict]:
        """发给全公司或发给这个角色的所有插话。"""
        with self._lock:
            return [m for ch, m in self._items if ch in (ALL, role_id)]

    def listener(self, role_id: str, seen_ids: set[str]):
        """返回一个函数：每次调用返回这个角色还没看过的插话（拼成一段文字），没有新的就返回空字符串。"""
        def poll() -> str:
            new = [m for m in self.for_role(role_id) if m["id"] not in seen_ids]
            seen_ids.update(m["id"] for m in new)
            return format_notes(new, live=True)
        return poll

def format_notes(notes: list[dict], live: bool = False) -> str:
    if not notes:
        return ""
    head = ("【CEO 刚刚在你工作时发来消息，请立即考虑，并在后续工作和最终回复里体现】" if live
            else "CEO 在执行过程中的指示（必须遵守，优先于之前的安排）：")
    return head + "\n" + "\n".join(f"- {m['text']}" for m in notes)
