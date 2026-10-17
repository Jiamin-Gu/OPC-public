"""模型连接（v0.14）：全局连接 → 公司默认 → 角色单独选择；密钥只存在 models.json，不写进公司配置。"""
import json

import pytest
from fastapi.testclient import TestClient

from opc import model_settings
from opc.company import CompanyStore
from opc.web.runtime import Hub
from opc.web.server import create_app

from fakes import World, make_store


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("OPC_PROVIDER", "deepseek")
    monkeypatch.setenv("OPC_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPC_API_KEY", "sk-env")
    monkeypatch.setenv("OPC_MODEL", "deepseek-flash")


def add_relay(home, key="sk-relay"):
    return model_settings.save([{"id": "relay", "name": "中转", "provider": "custom", "base_url": "http://127.0.0.1:8000/v1",
                                 "api_key": key, "models": ["deepseek-v4-flash", "glm-5"]}], None, home)


def test_env_connection_when_no_file(tmp_path):
    s = model_settings.load(tmp_path)
    assert [c["id"] for c in s["connections"]] == ["env"]
    assert s["default"] == {"connection": "env", "model": "deepseek-flash"}
    assert model_settings.default_config(s).api_key == "sk-env"


def test_save_keeps_key_and_never_sends_it(tmp_path):
    add_relay(tmp_path)
    s = model_settings.save([{"id": "relay", "name": "中转", "provider": "custom", "base_url": "http://127.0.0.1:8000/v1",
                              "api_key": "", "models": ["glm-5"]},
                             {"id": "env", "name": "篡改", "provider": "custom", "base_url": "http://x", "models": ["m"]}],
                            {"connection": "relay", "model": "glm-5"}, tmp_path)
    relay = model_settings.find(s["connections"], "relay")
    assert relay["api_key"] == "sk-relay" and relay["models"] == ["glm-5"]  # 密钥留空表示不改
    assert model_settings.find(s["connections"], "env")["name"].endswith("（环境变量）")  # 环境变量连接不能改
    stored = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in stored["connections"]] == ["relay"]  # 环境变量的密钥不写进文件
    pub = model_settings.public(s)
    assert "sk-relay" not in str(pub) and "sk-env" not in str(pub) and pub["connections"][1]["has_key"]
    assert pub["default_label"] == "中转 · glm-5"


def test_save_validation(tmp_path):
    with pytest.raises(ValueError):
        model_settings.save([{"provider": "nope", "models": ["m"]}], None, tmp_path)
    with pytest.raises(ValueError):
        model_settings.save([{"provider": "custom", "base_url": "", "models": ["m"]}], None, tmp_path)
    s = model_settings.save([{"id": "Bad Id!", "provider": "deepseek"}], None, tmp_path)
    saved = [c for c in s["connections"] if c["id"] != "env"][0]
    assert saved["id"].startswith("c-") and saved["models"] and saved["base_url"]  # 非法 id 重新分配，空的用服务商默认值


def test_three_layers(tmp_path):
    add_relay(tmp_path / "home")
    store = CompanyStore(tmp_path / "home")
    company = store.create("公司", "", [{"id": "pm"}, {"id": "engineer", "model": {"connection": "relay", "model": "glm-5"}}],
                           model={"connection": "relay", "model": "deepseek-v4-flash"})
    assert company.llm_config(None).model == "deepseek-v4-flash"           # 规划和汇报用公司默认
    assert company.llm_config("pm").base_url == "http://127.0.0.1:8000/v1"  # 跟随公司默认
    assert company.llm_config("engineer").model == "glm-5"                  # 角色单独选择
    assert "sk-relay" not in (company.root / "company.json").read_text(encoding="utf-8")  # 公司配置里没有密钥
    company.set_models(None, {"engineer": None})                           # 公司改回全局默认，工程师改为跟随
    assert company.llm_config("engineer").api_key == "sk-env" and company.llm_config(None).model == "deepseek-flash"
    reloaded = store.load(company.id)
    assert "model" not in reloaded.meta and "model" not in reloaded.meta["roles"][1]
    with pytest.raises(ValueError):
        company.set_models({"connection": "gone"}, {})


def test_deleted_connection_falls_back(tmp_path):
    home = tmp_path / "home"
    add_relay(home)
    company = CompanyStore(home).create("公司", "", [{"id": "pm", "model": {"connection": "relay", "model": "glm-5"}}])
    model_settings.save([], None, home)  # 删掉中转连接
    assert company.llm_config("pm").api_key == "sk-env"  # 退回到上一层，而不是报错


def test_legacy_role_fields_still_work_and_are_cleared(tmp_path):
    store = CompanyStore(tmp_path / "home")
    company = store.create("公司", "", [{"id": "pm", "provider": "custom", "base_url": "https://old.example/v1", "api_key": "sk-old"}])
    cfg = company.llm_config("pm")
    assert cfg.provider == "custom" and cfg.api_key == "sk-old" and cfg.base_url == "https://old.example/v1"
    assert company.public_meta()["roles"][0]["legacy_model"] is True
    company.set_models(None, {"pm": None})
    assert "sk-old" not in (company.root / "company.json").read_text(encoding="utf-8")
    assert company.llm_config("pm").api_key == "sk-env"


@pytest.fixture
def app(tmp_path):
    world = World()
    hub = Hub(make_store(tmp_path, world))
    return TestClient(create_app(hub)), hub


def test_models_api(app):
    client, hub = app
    res = client.put("/api/models", json={"connections": [{"name": "中转", "provider": "custom", "api_key": "sk-secret",
                                                           "base_url": "http://127.0.0.1:8000/v1", "models": ["m1"]}]})
    assert res.status_code == 200 and "sk-secret" not in res.text
    conn = [c for c in res.json()["connections"] if not c.get("env")][0]
    assert client.get("/api/models").json()["connections"][1]["has_key"]
    assert client.put("/api/models", json={"connections": [{"provider": "nope"}]}).status_code == 400

    cid = client.post("/api/companies", json={"name": "x", "roles": [{"id": "pm"}, {"id": "qa"}],
                                              "model": {"connection": conn["id"], "model": "m1"}}).json()["id"]
    assert client.put("/api/company/models", json={"roles": {}}).status_code == 404  # 没有打开的公司
    hub.open_company(cid)
    assert client.put("/api/company/models", json={"roles": {"designer": None}}).status_code == 400
    assert client.put("/api/company/models", json={"default": {"connection": "gone"}}).status_code == 400
    res = client.put("/api/company/models", json={"default": {"connection": conn["id"], "model": "m1"},
                                                  "roles": {"qa": {"connection": "env", "model": "deepseek-flash"}}})
    assert res.status_code == 200
    public = hub.company.public_meta()
    assert public["model_label"] == "中转 · m1"
    assert [r["model"] for r in public["roles"]] == ["m1", "deepseek-flash"]
    assert "sk-secret" not in str(public) and "sk-env" not in str(public)


def test_home_event_lists_library_and_models(app):
    client, hub = app
    ev = hub.home_event()
    assert len(ev["library"]) > 200 and all("duties" not in r for r in ev["library"])
    assert ev["models"]["default"]["connection"] == "env" and "sk-env" not in str(ev)
