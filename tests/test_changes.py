"""任务调度的底层规则：并行、完成依赖和开始依赖；任务状态机（派发、运行结束、运行期间收到变更单、重启恢复）；
CEO 让某个任务返工时，下游马上暂停、上游改完后带着改动对比继续；按任务暂停和继续。
前半部分测纯函数，后半部分用假模型和执行引擎跑完整的流程。"""
import time

import pytest

from opc.changes import (TaskStops, apply_change, cancel_unfinished, dispatch, downstream, merge_job_result,
                         ready_batch, recover)

from harness import Env


def T(id_, status="pending", deps=(), after=(), **kw):
    return {"id": id_, "title": id_.upper(), "status": status, "depends_on": list(deps), "starts_after": list(after),
            "files": [], "round": 0, "reviews": [], **kw}


def ids(tasks):
    return [t["id"] for t in tasks]


# ---------------------------------------------------------------- 纯函数

def test_parallel_and_start_to_start_dependencies():
    a, b, c = T("a"), T("b", deps=["a"]), T("c", deps=["a"])
    d, e = T("d", after=["b"]), T("e", after=["d"])
    work, _ = ready_batch([a, b, c, d, e])
    assert ids(work) == ["a"]                          # B、C 等 A 完成；D 等 B 开始，E 等 D 开始
    a["status"] = "done"
    work, _ = ready_batch([a, b, c, d, e])
    assert sorted(ids(work)) == ["b", "c", "d", "e"]   # A 完成：B、C 并行；B 这一批开始，D、E 连锁跟着开始
    b2 = {**b, "status": "review"}                      # B 已经开始（做完在等审查）
    f = T("f", deps=["c"], after=["b"])                # 同时有两种依赖：C 完成 且 B 已开始
    assert ids(ready_batch([a, b2, {**c, "status": "done"}, f])[0]) == ["f"]
    assert ids(ready_batch([a, {**b, "started": True, "paused": True}, {**c, "status": "done"}, f])[0]) == ["f"]
    assert ids(ready_batch([a, b, {**c, "status": "done"}, f])[0]) == ["b", "f"]
    assert ready_batch([a, {**b, "paused": True}, c])[0] == [c]                         # 暂停的不派发
    assert ids(ready_batch([a, T("x", "rework"), T("y", "review")])[1]) == ["y"]


def test_downstream_follows_both_kinds_of_dependency():
    tasks = [T("a"), T("b", deps=["a"]), T("c", deps=["a"]), T("d", after=["b"]), T("e", deps=["d"]), T("z")]
    assert downstream(tasks, "a") == ["b", "c", "d", "e"]
    assert downstream(tasks, "b") == ["d", "e"] and downstream(tasks, "z") == []


def test_rework_order_pauses_downstream_and_marks_what_needs_resync():
    tasks = {t["id"]: t for t in [
        T("a", "done", files=["docs/a.md"], round=1),
        T("b", "pending", deps=["a"], started=True, files=["docs/b.md"]),   # 正在做（做到一半）
        T("c", "done", deps=["a"], files=["docs/c.md"], round=2),            # 已经做完
        T("d", "pending", after=["b"], started=True),                         # 跟着 B 开始的
        T("e", "pending", deps=["c"]),                                        # 还没开始
        T("f", "skipped", deps=["a"]),
    ]}
    shots = []
    updates, text = apply_change(tasks, {"kind": "rework", "task_id": "a", "feedback": "预约规则写错了"},
                                 snapshot=lambda t: shots.append(t["id"]) or "snap1")
    assert shots == ["a"] and updates["a"]["status"] == "rework" and updates["a"]["ceo_note"] == "预约规则写错了"
    assert updates["a"]["round_base"] == 1
    for tid in ("b", "c", "d", "e"):
        assert updates[tid]["status"] == "pending" and updates[tid]["started"] is False
    assert updates["b"]["resync"] == {"a": "snap1"} and updates["c"]["resync"] == {"a": "snap1"}
    assert updates["c"]["round_base"] == 2                       # 重新审查时从第 3 轮往上数，不会一上来就上报
    assert updates["d"]["resync"] == {"a": "snap1"}              # 已经开始过：也告诉它上游改了什么
    assert "resync" not in updates["e"]                          # 还没开始：直接读新版本就行
    assert updates["f"]["status"] == "pending" and "resync" not in updates["f"]  # 被跳过的（A 失败过）跟着恢复
    assert "《A》返工" in text and "《B》" in text and "《E》" in text


def test_pause_resume_orders():
    tasks = {"b": T("b", "pending"), "c": T("c", "done")}
    up, _ = apply_change(tasks, {"kind": "pause", "task_id": "b"})
    assert up["b"]["paused"] is True
    assert apply_change(tasks, {"kind": "pause", "task_id": "c"})[0] == {}   # 做完的不用暂停
    assert apply_change({"b": up["b"]}, {"kind": "resume", "task_id": "b"})[0]["b"]["paused"] is False




def test_stops():
    s = TaskStops()
    ev = s.event("b")
    s.stop("b")
    assert ev.is_set() and s.is_stopped("b")
    s.clear("b")
    assert not s.is_stopped("b")


def test_job_lifecycle_and_merging():
    """派发 → 运行结束；运行期间收到变更单时，以变更后的状态为准，只补上文件和执行循环的检查点。"""
    t = T("b", deps=["a"], files=["docs/old.md"])
    run = dispatch(t, "work", "j1", thread="th1")
    assert run["status"] == "running" and run["job"] == "j1" and run["worker"] == "th1" and run["run_from"] == "pending"
    assert ready_batch([T("a", "done"), run]) == ([], [])                  # 有工作运行的任务不会再派发
    done = {**run, "status": "review", "files": ["docs/b.md"], "worker": None}
    assert merge_job_result(run, "j1", done, False, ["docs/b.md"])["status"] == "review"
    assert merge_job_result(run, "j0", done, False, []) is None              # 过时的结果
    paused = merge_job_result(run, "j1", None, True, ["docs/half.md"])       # 做到一半被暂停
    assert paused["status"] == "pending" and paused["interrupted"] and paused["worker"] == "th1" and not paused["job"]
    assert paused["files"] == ["docs/half.md", "docs/old.md"]
    reset = {**run, "status": "pending", "resync": {"a": "snap"}}            # 运行期间上游返工，它被重置为待同步
    merged = merge_job_result(reset, "j1", done, False, ["docs/b.md"])       # 正好做完：结果作废，等上游改完再同步
    assert merged["status"] == "pending" and merged["resync"] == {"a": "snap"} and merged["files"] == ["docs/b.md", "docs/old.md"]
    rv = dispatch({**t, "status": "review"}, "review", "j2")
    assert rv["status"] == "reviewing" and merge_job_result(rv, "j2", None, True, [])["status"] == "review"


def test_recover_and_cancel_unfinished():
    tasks = [dispatch(T("a"), "work", "j1", "th1"), dispatch(T("b", "review"), "review", "j2"),
             T("c", "done"), {**T("d", "rework"), "job": None}]
    rec = recover(tasks)
    assert rec["a"]["status"] == "pending" and rec["a"]["interrupted"] and rec["a"]["worker"] == "th1"
    assert rec["b"]["status"] == "review" and set(rec) == {"a", "b"}
    assert set(cancel_unfinished(tasks)) == {"a", "b", "d"}


# ---------------------------------------------------------------- 完整的流程

PLAN = {"summary": "A 完成后 B、C 并行；D 等 B 开始", "tasks": [
    {"key": "a", "title": "写 PRD", "detail": "docs/pm.md", "assignee": "pm", "depends_on": []},
    {"key": "b", "title": "技术方案", "detail": "docs/engineer.md", "assignee": "engineer", "depends_on": ["a"]},
    {"key": "c", "title": "交互设计", "detail": "docs/designer.md", "assignee": "designer", "depends_on": ["a"]},
    {"key": "d", "title": "测试计划", "detail": "docs/qa.md", "assignee": "qa", "depends_on": [], "starts_after": ["b"]},
]}


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path, plans=[PLAN])
    e.world.delay = 0.2
    return e


def test_parallel_and_start_to_start(env):
    env.start()
    assert all(env.task(k)["status"] == "done" for k in "abcd")
    (a_start, a_end), = env.spans("pm")
    (b_start, _), = env.spans("engineer")[:1]
    (c_start, _), = env.spans("designer")[:1]
    (d_start, _), = env.spans("qa")[:1]
    assert b_start >= a_end and c_start >= a_end                 # B、C 等 A 完成
    assert abs(b_start - c_start) < 0.15                         # B、C 并行
    assert abs(d_start - b_start) < 0.15 and d_start > a_start   # D 跟着 B 一起开始，不用等 B 完成
    assert "和你同时在进行" in env.briefs("qa")[0] and "技术方案" in env.briefs("qa")[0]


def test_rework_upstream_when_idle_resyncs_downstream_with_diff(env):
    env.start()
    n = {r: len(env.briefs(r)) for r in ("pm", "engineer", "designer", "qa")}
    env.change("rework", "a", "预约要支持候补").wait()
    pm = env.briefs("pm")[n["pm"]:]
    assert len(pm) == 1 and "CEO 看过之后要求修改" in pm[0] and "预约要支持候补" in pm[0]
    for role in ("engineer", "designer"):                       # 做完了的下游：带着改动对比同步
        new = env.briefs(role)[n[role]:]
        assert len(new) == 1 and "上游有改动" in new[0] and "+# pm 的产出（修改版）" in new[0] and "edit_file" in new[0]
    assert len(env.briefs("qa")) == n["qa"] + 1                  # 跟着 B 开始的 D 也重新做了一遍
    assert env.company.read_file("docs/pm.md") == "# pm 的产出（修改版）"
    assert env.task("a")["status"] == "done" and env.task("b")["status"] == "done" and env.task("b")["resync"] == {}
    channel = env.chans()["all"]
    assert any("返工：预约要支持候补" in m["text"] for m in channel) and channel[-1]["kind"] == "report"
    pm_rework_end = env.spans("pm")[-1][1]                       # 下游开始时上游已经改完
    assert all(s >= pm_rework_end for s, _ in env.spans("engineer")[-1:] + env.spans("designer")[-1:])


def test_rework_while_downstream_is_running_takes_effect_immediately(env):
    """B 做到一半时 CEO 发现 A 有问题：B 马上停下（不用等同时在做的 C），A 改完后 B 带着原对话和改动对比接着做。"""
    fired = []

    def intervene(role, messages):
        if role == "engineer" and not fired and "请你审查" not in messages[1]["content"]:
            fired.append(1)
            env.change("rework", "a", "取消规则要改成开课前 2 小时")
    env.world.on_call = intervene
    env.start()
    assert all(env.task(k)["status"] == "done" for k in "abcd")
    eng_calls = [m for m in env.calls("engineer") if "请你审查" not in m[1]["content"]]  # 不含它审查上游的调用
    assert len(env.briefs("engineer")) == 1                       # 只拿到过一次任务说明：恢复时没有从头开始
    first_brief = eng_calls[0][1]["content"]
    resumed = eng_calls[1]                                        # 恢复后第一次调用模型：原来的对话 + 一条恢复说明
    assert resumed[1]["content"] == first_brief and resumed[-1]["role"] == "user"
    note = resumed[-1]["content"]
    assert "暂停后恢复了" in note and "上游有改动" in note and "取消规则要改成开课前 2 小时" in note
    assert "+# pm 的产出（修改版）" in note                         # 带着上游的改动对比
    pm_end = env.spans("pm")[-1][1]
    eng_starts = [s for r, s, e in env.world.spans if r == "engineer"]  # 第一次调用被暂停打断（作废），恢复后两次
    assert len(eng_starts) == 3 and eng_starts[1] >= pm_end           # 恢复发生在 A 返工完成之后
    pm_rework_start = env.spans("pm")[-1][0]
    designer_first_end = env.spans("designer")[0][1]
    assert pm_rework_start < designer_first_end + 0.5              # A 的返工马上开始，没有等 C 做完那一批
    assert env.task("b")["worker"] is None                         # 做完后执行循环的检查点清掉了
    msgs = [m["text"] for m in env.chans()["all"]]
    assert any("已暂停" in t for t in msgs) and any("返工" in t for t in msgs)


def test_pause_then_resume_finishes(env):
    paused = []

    def pause_c(role, messages):
        if role == "pm" and not paused:
            paused.append(1)
            env.change("pause", "c")
    env.world.on_call = pause_c
    env.start()
    assert env.task("c")["paused"] and env.task("c")["status"] == "pending"
    assert all(env.task(k)["status"] == "done" for k in "abd")
    assert env.chans()["all"][-1]["kind"] != "report"            # 停在这里等 CEO，不出汇报
    env.world.on_call = None
    env.change("resume", "c").wait()
    assert env.task("c")["status"] == "done" and env.chans()["all"][-1]["kind"] == "report"


def test_rework_waits_for_its_upstream():
    """上游正在返工时又让下游返工：下游要等上游改完才开工，不能基于旧版本做。"""
    a, b = T("a", "rework"), T("b", "rework", deps=["a"])
    assert ids(ready_batch([a, b])[0]) == ["a"]
    assert ids(ready_batch([{**a, "status": "done"}, b])[0]) == ["b"]


def test_stop_signal_is_sent_after_the_change_is_applied(env):
    """变更单提交时按旧状态算该停谁，可能漏掉"排在它前面的事件刚派发的任务"。调度图处理完变更单后按新状态补发：
    这里直接把变更单放进队列（不经过提交时的那次信号），正在做的下游照样停下，上游改完后带着原对话接着做。"""
    fired = []

    def intervene(role, messages):
        if role == "engineer" and not fired and "请你审查" not in messages[1]["content"]:
            fired.append(1)
            b_id = env.task("b")["id"]
            env.engine.submit({"type": "change", "order": {"kind": "rework", "task_id": env.task("a")["id"],
                                                          "feedback": "取消规则要改"}})
            for _ in range(200):
                if env.engine.stops.is_stopped(b_id):
                    break
                time.sleep(0.01)
    env.world.on_call = intervene
    env.start()
    assert fired and all(env.task(k)["status"] == "done" for k in "abcd")
    eng = [m for m in env.calls("engineer") if "请你审查" not in m[1]["content"]]
    assert len(env.briefs("engineer")) == 1 and "暂停后恢复了" in eng[1][-1]["content"]


TWO = {"summary": "先写 PRD，再写技术方案", "tasks": [
    {"key": "a", "title": "写 PRD", "detail": "docs/pm.md", "assignee": "pm", "depends_on": []},
    {"key": "b", "title": "技术方案", "detail": "docs/engineer.md", "assignee": "engineer", "depends_on": ["a"],
     "reviewer": "qa"}]}


def test_running_out_of_steps_is_not_treated_as_done(tmp_path):
    """步数用完不能当作做完交去审查：第一次带着原对话自动接着做，再用完交给 CEO（接受 / 接着做 / 放弃）。"""
    env = Env(tmp_path, plans=[TWO], max_role_steps=4)
    env.world.endless = {"engineer"}
    env.start()
    b = env.task("b")
    assert b["status"] == "escalated" and b["reviews"][-1]["verdict"] == "incomplete"
    assert not any(m["kind"] == "review" and m["task_id"] == b["id"] for m in env.chans()["all"])   # 没有交去审查
    texts = [m["text"] for m in env.chans()["all"]]
    assert sum("步数用完了，接着做" in t for t in texts) == 1
    eng = [m for m in env.calls("engineer") if "请你审查" not in m[1]["content"]]
    assert len(env.briefs("engineer")) == 1                         # 接着做时没有从头开始
    assert any("步数用完了，请接着完成剩下的部分" in m["content"] for m in eng[-1] if m["role"] == "user")
    assert any("只剩 2 步了" in c[-1]["content"] for c in eng)        # 快用完时提醒收尾

    env.world.endless = set()
    env.decide(b["id"], "rework", "先把接口部分写完")                  # 接着做：带着原对话和 CEO 的意见
    last = [m for m in env.calls("engineer") if "请你审查" not in m[1]["content"]]
    assert "CEO 的意见（必须遵守）：先把接口部分写完" in [m for m in last[len(eng)] if m["role"] == "user"][-1]["content"]
    b = env.task("b")
    assert b["status"] == "done" and not b.get("limited") and not b.get("worker")
    assert [r["verdict"] for r in b["reviews"]] == ["incomplete", "pass"]   # 做完以后照常审查


def test_accepting_an_unfinished_task_still_goes_to_review(tmp_path):
    env = Env(tmp_path, plans=[TWO], max_role_steps=4)
    env.world.endless = {"engineer"}
    env.start()
    env.world.endless = set()
    env.decide(env.task("b")["id"], "accept")
    b = env.task("b")
    assert b["status"] == "done" and [r["verdict"] for r in b["reviews"]] == ["incomplete", "pass"]


def test_retry_a_failed_task_revives_what_was_skipped(tmp_path):
    """执行出错（比如服务商临时故障）的任务可以重试，因为它失败而被跳过的下游一起恢复。"""
    env = Env(tmp_path, auto_approve=True)
    env.world.fail_role = "pm"
    env.directive()
    assert {t["assignee"]: t["status"] for t in env.tasks().values()} == {
        "pm": "failed", "engineer": "skipped", "designer": "skipped"}
    env.world.fail_role = None
    env.change("retry", "t1").wait()
    assert all(t["status"] == "done" for t in env.tasks().values())
    texts = [m["text"] for m in env.chans()["all"]]
    assert any("CEO 让《写 PRD》重新做" in t and "跳过的任务也恢复了" in t for t in texts)
    assert [m["kind"] for m in env.chans()["all"]].count("report") == 2      # 重新跑完再汇报一次


def test_new_directive_cancels_leftover_tasks(env):
    """上一条指令里没做完、也没在执行的任务（这里是被暂停的），下新指令时标记为已取消，不会一直挂着。"""
    fired = []

    def pause_b(role, messages):
        if role == "pm" and not fired:
            fired.append(1)
            env.change("pause", "b")
    env.world.on_call = pause_b
    env.start()
    assert env.task("b")["paused"] and env.task("b")["status"] == "pending"
    env.world.on_call = None
    env.directive("做一个新功能", did="d2")
    old = {t["id"].split("-")[-1]: t["status"] for t in env.tasks().values() if t["directive_id"] == "d1"}
    assert old["b"] == "cancelled" and old["a"] == "done"
    assert any("上一条指令里没做完的任务已取消" in m["text"] and "《技术方案》" in m["text"] for m in env.chans()["all"])


def test_old_coordinator_checkpoints_are_pruned(tmp_path):
    """调度图的检查点只保留最近的若干个，数据库不会越来越大；状态照常读取。"""
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    from opc import engine as engine_mod
    conn = sqlite3.connect(tmp_path / "ck.sqlite", check_same_thread=False)
    env = Env(tmp_path / "home", plans=[PLAN] * 3, checkpointer=SqliteSaver(conn))
    for i in range(3):
        env.start(did=f"d{i}")
    count = lambda: conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (env.company.id,)).fetchone()[0]  # noqa: E731
    before = count()
    env.engine._prune_checkpoints()
    assert before > engine_mod.KEEP_CHECKPOINTS >= count()   # 确实清理了
    assert env.state()["directive"]["id"] == "d2" and all(t["status"] == "done" for t in env.tasks().values()
                                                           if t["directive_id"] == "d2")
