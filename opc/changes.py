"""任务调度的底层规则：哪些任务可以开始、CEO 的干预（变更单）影响哪些任务、怎样生效。

两种依赖：
- depends_on（完成后开始）：上游任务完成并通过审查后才能开始。A 完成后 B、C 同时开始，就是 B、C 都 depends_on A
- starts_after（开始后开始）：上游任务开始后就能开始，不用等它完成。D starts_after B：B 开始的同一批或之后才派发 D

变更单（CEO 在任务卡片上操作，或者由调度助手 / 成员的上报整理成调整建议、CEO 确认后提交）：
- rework：让一个任务返工（附意见）。所有直接或间接依赖它的任务（沿两种依赖往下）都要暂停，
  等它改完再继续；已经做过的下游任务恢复时带上上游的改动对比，用局部修改对齐
- pause / resume：暂停或继续一个任务
- retry：失败的任务重新做；因为它失败而被跳过的下游一起恢复

任务状态机（事件驱动：任务一满足条件就派发，不等同一批的其他任务）：

    pending ──派发──→ running ──做完──→ review ──派发审查──→ reviewing ──通过──→ done
                         │        （没有审查人直接 done）          ├──要求修改──→ rework ──派发──→ running …
                         │                                         └──多轮仍不通过──→ escalated ──CEO 裁决──→ done / rework / failed
                         └──暂停（interrupt）──→ 回到 pending / rework（记下执行循环的检查点，恢复时接着做）

- job：正在执行这个任务的工作运行 id。有 job 的任务不会再派发，直到这次运行结束（job_done）
- 运行期间收到的变更单直接改任务（例如下游被重置为 pending 并标记待同步），运行结束时以变更后的状态为准，
  只补上运行的产出文件和执行循环的检查点（见 merge_job_result）

这里只有纯函数和几个线程安全的小容器，不调用模型，方便单独测试。调度图（graph.py）调用它们。
"""
import threading

from .state import Task

# 已经开始过的状态（暂停后退回 pending 的任务用 started 标记）
STARTED_STATUSES = {"running", "review", "reviewing", "rework", "escalated", "done"}
CLOSED_STATUSES = {"cancelled", "skipped", "failed"}
FINISHED_STATUSES = CLOSED_STATUSES | {"done"}


def has_started(task: Task) -> bool:
    return bool(task.get("started")) or task.get("status") in STARTED_STATUSES


def ready_batch(tasks: list[Task]) -> tuple[list[Task], list[Task]]:
    """这一批要派发的任务：(去执行的, 去审查的)。

    去执行：待开始且完成依赖都已完成、开始依赖都已开始（或者和它在同一批开始）的任务，以及要返工的任务。
    暂停的任务、还有工作运行没结束的任务不派发。
    """
    by_id = {t["id"]: t for t in tasks}
    done = {t["id"] for t in tasks if t["status"] == "done"}
    free = [t for t in tasks if not t.get("paused") and not t.get("job")]
    # 返工也要等完成依赖都完成：例如上游正在返工时又让下游返工，下游要等上游改完，不能基于旧版本开工
    work = [t for t in free if t["status"] == "rework" and all(d in done for d in t.get("depends_on", []))]
    starting = {t["id"] for t in work}
    candidates = [t for t in free if t["status"] == "pending" and all(d in done for d in t.get("depends_on", []))]
    changed = True
    while changed:  # 开始依赖可以连锁：D 等 B 开始，E 等 D 开始，B 这一批开始，D、E 也可以这一批开始
        changed = False
        for t in candidates:
            if t["id"] in starting:
                continue
            if all(s in starting or (s in by_id and has_started(by_id[s])) for s in t.get("starts_after", [])):
                work.append(t)
                starting.add(t["id"])
                changed = True
    review = [t for t in free if t["status"] == "review"]
    return work, review


def dispatch(task: Task, kind: str, job: str, thread: str | None = None) -> Task:
    """派发：记下工作运行 id。执行时状态是 running（返工的记下原来是 rework，暂停时退回去），审查时是 reviewing。"""
    if kind == "review":
        return {**task, "status": "reviewing", "job": job}
    return {**task, "status": "running", "run_from": task["status"], "started": True, "job": job,
            "worker": task.get("worker") or thread}


def merge_job_result(cur: Task, job: str, result: Task | None, paused: bool, files: list[str],
                     kind: str = "work") -> Task | None:
    """工作运行结束（job_done）时更新任务。返回 None 表示这个结果已经过时（任务后来被重新派发或停止了）。

    - 运行期间没有变更：直接用运行的结果
    - 运行期间收到了变更单（任务被重置、返工、暂停），或者运行被暂停：以当前状态为准，
      只补上写过的文件；执行被暂停时记下检查点，下次带着原对话接着做；审查被暂停就不算数，之后重新审查
    """
    if cur.get("job") != job:
        return None
    merged_files = sorted(set(cur.get("files") or []) | set(files or []))
    if cur["status"] == "reviewing":            # 审查：运行期间没被打断才采用结论
        if result is not None and not paused:
            return {**result, "job": None}
        return {**cur, "status": "review", "job": None}
    if cur["status"] == "running" and result is not None and not paused:
        return {**result, "job": None, "files": result.get("files", merged_files)}
    # 被暂停，或者运行期间状态被变更单改过
    status = cur["run_from"] if cur["status"] == "running" else cur["status"]
    worked = paused and kind == "work"  # 被暂停的是负责人自己的执行（审查被暂停和负责人的进度无关）
    keep_worker = worked and status in ("pending", "rework")
    return {**cur, "status": status, "job": None, "files": merged_files,
            "interrupted": True if worked else cur.get("interrupted"), "worker": cur.get("worker") if keep_worker else None,
            "limited": False if worked else cur.get("limited")}


def cancel_unfinished(tasks: list[Task]) -> dict[str, Task]:
    """CEO 停止了公司：这条指令下没完成的任务都标记为已取消。"""
    return {t["id"]: {**t, "status": "cancelled", "job": None} for t in tasks if t["status"] not in FINISHED_STATUSES}


def recover(tasks: list[Task]) -> dict[str, Task]:
    """程序重启：还挂着工作运行的任务都没跑完。执行中的退回去（执行循环的检查点还在就能接着做），审查中的重新审查。"""
    out = {}
    for t in tasks:
        if not t.get("job"):
            continue
        if t["status"] == "reviewing":
            out[t["id"]] = {**t, "status": "review", "job": None}
        elif t["status"] == "running":
            out[t["id"]] = {**t, "status": t.get("run_from") or "pending", "job": None, "interrupted": True}
        else:
            out[t["id"]] = {**t, "job": None}
    return out


def downstream(tasks: list[Task], task_id: str) -> list[str]:
    """直接或间接依赖这个任务的任务 id（沿完成依赖和开始依赖往下），按任务顺序排列。"""
    children: dict[str, list[str]] = {}
    for t in tasks:
        for parent in t.get("depends_on", []) + t.get("starts_after", []):
            children.setdefault(parent, []).append(t["id"])
    seen, stack = set(), [task_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return [t["id"] for t in tasks if t["id"] in seen]


def should_stop(task: Task) -> bool:
    """变更单处理完以后，还挂着工作运行、但已经不该继续的任务（被重置、要返工、被暂停）。"""
    return bool(task.get("job")) and (task.get("paused") or task["status"] not in ("running", "reviewing"))


def affected_running(tasks: list[Task], order: dict) -> list[str]:
    """变更单提交时，正在执行、需要马上停下的任务（还没开始的不用停，调度图处理变更单时会改它们的状态）。"""
    ids = [order["task_id"]] if order["kind"] in ("rework", "pause") else []
    if order["kind"] == "rework":
        ids += downstream(tasks, order["task_id"])
    return ids


def apply_change(tasks: dict[str, Task], order: dict, snapshot=None) -> tuple[dict[str, Task], str]:
    """把一张变更单作用到任务上，返回 (要更新的任务, 给全公司频道的说明)。

    snapshot(task) -> 快照标签：返工前给任务的产出文件拍快照，下游恢复时用来生成改动对比。
    """
    task = tasks.get(order["task_id"])
    if not task:
        return {}, ""
    name = f"《{task['title']}》"
    if order["kind"] == "pause":
        if task["status"] in CLOSED_STATUSES | {"done"}:
            return {}, ""
        return {task["id"]: {**task, "paused": True}}, f"CEO 暂停了{name}"
    if order["kind"] == "resume":
        return {task["id"]: {**task, "paused": False}}, f"CEO 让{name}继续"
    if order["kind"] == "retry":
        if task["status"] != "failed":
            return {}, ""
        updates = {task["id"]: {**task, "status": "pending", "started": False, "job": None, "worker": None,
                                "interrupted": False, "limited": False, "extensions": 0, "paused": False,
                                "round_base": task.get("round", 0)}}
        revived = _revive_skipped(tasks, task["id"], updates)
        text = f"CEO 让{name}重新做" + (f"\n跳过的任务也恢复了：{'、'.join(revived)}" if revived else "")
        return updates, text
    if order["kind"] != "rework":
        return {}, ""

    feedback = str(order.get("feedback") or "").strip() or "请重新检查并改进"
    tag = snapshot(task) if snapshot and task.get("files") else ""
    updates = {task["id"]: {**task, "status": "rework", "paused": False, "started": True, "ceo_note": feedback,
                            "round_base": task.get("round", 0), "interrupted": False, "limited": False,
                            "extensions": 0, "base_tag": tag or task.get("base_tag", ""), "note_checked": False,
                            "closing": False}}  # CEO 要求返工：之后照常复审，不是收尾复审
    if order.get("trusted"):  # CEO 确认读到的外部内容可信：这次执行不再拦运行命令和访问网址
        updates[task["id"]]["trusted"] = True
    waiting = []
    for tid in downstream(list(tasks.values()), task["id"]):
        t = tasks[tid]
        if t["status"] in ("cancelled", "failed"):  # 被跳过的（上游失败了）跟着恢复
            continue
        touched = has_started(t) or bool(t.get("files"))
        new = {**t, "status": "pending", "started": False, "round_base": t.get("round", 0), "closing": False}
        if touched and tag:  # 已经基于旧版本做过：恢复时带上改动对比
            new["resync"] = {**(t.get("resync") or {}), task["id"]: tag}
        updates[tid] = new
        waiting.append(f"《{t['title']}》")
    text = f"CEO 要求{name}返工：{feedback}"
    if waiting:
        text += f"\n依赖它的任务先暂停，等它改完再继续：{'、'.join(waiting)}"
    return updates, text


def _revive_skipped(tasks: dict[str, Task], task_id: str, updates: dict) -> list[str]:
    """因为这个任务失败而被跳过的下游，退回待开始。"""
    names = []
    for tid in downstream(list(tasks.values()), task_id):
        t = tasks[tid]
        if t["status"] == "skipped":
            updates[tid] = {**t, "status": "pending", "result": ""}
            names.append(f"《{t['title']}》")
    return names


class TaskStops:
    """按任务暂停的信号。执行中的任务每一步都检查，收到就在当前步骤结束时停下。"""

    def __init__(self):
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def event(self, task_id: str) -> threading.Event:
        with self._lock:
            return self._events.setdefault(task_id, threading.Event())

    def stop(self, task_id: str) -> None:
        self.event(task_id).set()

    def clear(self, task_id: str) -> None:
        """重置信号（不能换成新的 Event：正在运行的工作手里拿着的是原来那个）。"""
        self.event(task_id).clear()

    def is_stopped(self, task_id: str) -> bool:
        with self._lock:
            e = self._events.get(task_id)
        return bool(e and e.is_set())


class Either:
    """全公司停止 或 这个任务被暂停：角色和编码 agent 只调用 is_set()。"""

    def __init__(self, *events: threading.Event):
        self.events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self.events)
