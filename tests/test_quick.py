"""小任务（v0.15）：一个人一次能做完的事，不用审批、不审查，汇报就是负责人的结果。"""
import json

from opc.planner import parse_plan

from harness import Env

QUICK = {"size": "small", "summary": "交给市场查一下", "tasks": [
    {"key": "t1", "title": "查旅游推广玩法", "detail": "列出 5 种", "assignee": "pm", "reviewer": "engineer",
     "deliverables": [{"path": "docs/a.md", "sections": ["一", "二", "三", "四"], "min_chars": 3000},
                      {"path": "docs/b.md"}]}]}


def test_small_plan_is_trimmed():
    _, tasks = parse_plan(json.dumps(QUICK), ["pm", "engineer"])
    t = tasks[0]
    assert t["quick"] and t["reviewer"] == ""                      # 小任务不审查
    assert [d["path"] for d in t["deliverables"]] == ["docs/a.md"]  # 最多一个产出文件
    assert t["deliverables"][0]["min_chars"] == 800 and len(t["deliverables"][0]["sections"]) == 3


def test_small_with_several_tasks_is_standard():
    plan = {"size": "small", "summary": "s", "tasks": [
        {"key": "t1", "title": "A", "assignee": "pm"},
        {"key": "t2", "title": "B", "assignee": "engineer", "depends_on": ["t1"]}]}
    _, tasks = parse_plan(json.dumps(plan), ["pm", "engineer"])
    assert not any(t.get("quick") for t in tasks) and tasks[0]["reviewer"] == "engineer"  # 照常规：上游安排审查


def test_quick_task_runs_without_approval_or_review(tmp_path):
    plan = {**QUICK, "tasks": [{**QUICK["tasks"][0], "deliverables": [{"path": "docs/pm.md"}]}]}  # 假模型写的文件
    env = Env(tmp_path, plans=[plan])
    env.directive("搜一下旅游推广")
    state = env.state()
    assert not state.get("approval")                                # 没有等审批
    task = next(iter(state["tasks"].values()))
    assert task["status"] == "done" and task["quick"] and not task.get("reviews")
    assert env.calls("engineer") == []                              # 没有审查
    brief = env.briefs("pm")[0]
    assert "这是一个小任务" in brief and "不需要写文件" not in brief  # 有产出文件时不说"不需要写文件"
    msgs = env.chans()["all"]
    assert [m["kind"] for m in msgs if m["kind"] in ("plan", "report")] == ["plan", "report"]
    plan = next(m for m in msgs if m["kind"] == "plan")
    report = msgs[-1]
    assert plan["quick"] and report["quick"] and report["text"] == task["result"]  # 汇报就是负责人的结果
    assert not [c for c in env.world.calls if c[0] is None and "项目助理" in c[1][0]["content"]]  # 没调用汇报助手


def test_quick_task_without_file_answers_in_summary(tmp_path):
    plan = {"size": "small", "summary": "s", "tasks": [{"key": "t1", "title": "回答", "detail": "", "assignee": "pm"}]}
    env = Env(tmp_path, plans=[plan])
    env.directive("这个功能值得做吗？")
    assert "不需要写文件" in env.briefs("pm")[0]
    assert next(iter(env.tasks().values()))["status"] == "done"


def test_standard_plan_still_waits_for_approval(tmp_path):
    env = Env(tmp_path)
    env.directive()
    assert env.state()["approval"]["type"] == "plan_review"
