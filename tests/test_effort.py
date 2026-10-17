"""投入档位和用量透明（v0.17）：档位只限轮数和次数，不限 token；每条指令、每个任务的用量都记着，可以按价格估算花费。"""
import pytest
from fastapi.testclient import TestClient

from opc import effort, model_settings
from opc.company import CompanyStore
from opc.jobs import ask_tool
from opc.state import sum_usage
from opc.web.runtime import Hub
from opc.web.server import create_app

from fakes import World, make_store
from harness import Env
from test_changes import TWO
from test_review import PLAN


def test_levels():
    lean, std, deep = (effort.limits(x) for x in ("lean", "standard", "thorough"))
    assert lean["review_rounds"] < std["review_rounds"] < deep["review_rounds"]
    assert lean["extensions"] < std["extensions"] < deep["extensions"] and lean["asks"] < std["asks"] < deep["asks"]
    assert effort.limits("nope") == std and not any("token" in k for k in std)  # 不限 token
    assert [e["id"] for e in effort.public()] == ["lean", "standard", "thorough"]


def test_company_effort(tmp_path):
    store = CompanyStore(tmp_path)
    assert store.create("公司", "", [{"id": "pm"}]).effort == "standard"
    company = store.create("公司", "", [{"id": "pm"}], effort_level="lean")
    assert company.effort == "lean" and company.public_meta()["effort"] == "lean"
    company.set_effort("thorough")
    assert store.load(company.id).effort == "thorough"
    with pytest.raises(ValueError):
        company.set_effort("max")
    with pytest.raises(ValueError):
        store.create("公司", "", [{"id": "pm"}], effort_level="max")


def test_lean_reviews_once_then_ceo_decides(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[PLAN], company_kw={"effort_level": "lean"})
    env.world.verdicts = {"写代码": [("changes", ["金额没有保留两位小数"])]}
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "escalated" and len(t2["reviews"]) == 1          # 节省：审查 1 轮就交给 CEO
    plan_prompt = next(m for r, m in env.world.calls if r is None and "规划助手" in m[0]["content"])[0]["content"]
    assert "「节省」档" in plan_prompt


def test_lean_does_not_auto_continue(tmp_path):
    env = Env(tmp_path, plans=[TWO], max_role_steps=4, company_kw={"effort_level": "lean"})
    env.world.endless = {"engineer"}
    env.start()
    b = env.task("b")
    assert b["status"] == "escalated" and b["reviews"][-1]["verdict"] == "incomplete"
    assert not any("步数用完了，接着做" in m["text"] for m in env.chans()["all"])  # 节省档不自动接着做


def test_effort_change_applies_to_later_work(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[PLAN])
    assert env.engine.limits()["review_rounds"] == effort.limits("standard")["review_rounds"]
    env.company.set_effort("thorough")
    assert env.engine.limits() == {**effort.limits("thorough")}
    assert "最多问 5 次" in ask_tool(env.company, "pm", 5)["function"]["description"]


def test_usage_by_directive_and_task(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[PLAN])
    env.start()
    state = env.state()
    by_directive = state["directive_usage"]["d1"]
    assert sum_usage(by_directive) == sum_usage(state["usage"])            # 没有私聊时，指令的用量就是全部
    assert by_directive["planner"]["calls"] == 2                            # 规划 + 汇报
    tasks = list(state["tasks"].values())
    assert all(t["usage"]["calls"] >= 2 for t in tasks)                     # 执行 + 审查都记在任务上
    assert sum(t["usage"]["calls"] for t in tasks) + 2 == sum_usage(state["usage"])["calls"]


def test_prices_and_api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPC_API_KEY", "sk-env")
    world = World()
    hub = Hub(make_store(tmp_path, world))
    client = TestClient(create_app(hub))
    res = client.put("/api/models", json={"connections": [{"id": "relay", "name": "中转", "provider": "custom",
                                                           "base_url": "http://127.0.0.1:8000/v1", "api_key": "k",
                                                           "models": ["m1"]}],
                                          "default": {"connection": "relay", "model": "m1"},
                                          "prices": {"relay": {"input": 1, "cached": 0.1, "output": "2"},
                                                     "env": {"input": 2}, "gone": {"input": 9}, "c": {"input": "x"}}})
    assert res.status_code == 200 and res.json()["prices"] == {"relay": {"input": 1.0, "cached": 0.1, "output": 2.0},
                                                               "env": {"input": 2.0, "cached": 0.0, "output": 0.0}}
    cid = client.post("/api/companies", json={"name": "x", "roles": [{"id": "pm"}], "effort": "lean"}).json()["id"]
    assert client.post("/api/companies", json={"name": "x", "roles": [{"id": "pm"}], "effort": "max"}).status_code == 400
    assert client.put("/api/company/effort", json={"effort": "thorough"}).status_code == 404
    hub.open_company(cid)
    public = hub.company.public_meta()
    assert public["effort"] == "lean" and public["roles"][0]["price"]["output"] == 2.0 and public["planner_price"]["input"] == 1.0
    assert client.put("/api/company/effort", json={"effort": "max"}).status_code == 400
    assert client.put("/api/company/effort", json={"effort": "thorough"}).status_code == 200
    assert hub.company.effort == "thorough"
    assert [e["id"] for e in hub.home_event()["efforts"]] == ["lean", "standard", "thorough"]
    assert model_settings.price(None, model_settings.load(hub.store.home)) is None
