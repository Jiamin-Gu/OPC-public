"""公司存储、共享文件区、计划解析、角色工具的测试。"""
import json

import pytest

from opc.agents import execute_tool
from opc.company import CompanyStore, FileError
from opc.planner import PlanError, parse_plan
from opc.state import add_usage


@pytest.fixture
def company(tmp_path):
    return CompanyStore(tmp_path).create("公司", "目标", [{"id": "pm"}, {"id": "engineer", "model": "m2"}])


def test_role_llm_override_falls_back_to_default(company, monkeypatch):
    monkeypatch.setenv("OPC_MODEL", "default-model")
    assert company.llm_config("pm").model == "default-model"
    assert company.llm_config("engineer").model == "m2"


def test_files_sandbox_and_authors(company):
    assert company.write_file("docs/a.md", "hello", author="pm") is True
    assert company.write_file("docs/a.md", "hello2", author="engineer") is False
    assert company.read_file("docs/a.md") == "hello2"
    assert company.list_files()[0]["author"] == "engineer"
    with pytest.raises(FileError):
        company.read_file("../company.json")
    with pytest.raises(FileError):
        company.write_file("../../evil.txt", "x", author="pm")


def test_tools_return_errors_as_text(company):
    result, summary, wrote = execute_tool(company, "pm", "read_file", '{"path": "nope.md"}')
    assert result.startswith("错误") and summary["ok"] is False and not wrote
    result, _, wrote = execute_tool(company, "pm", "write_file", '{"path": "docs/x.md", "content": "c"}')
    assert "已创建" in result and wrote
    assert "docs/x.md" in execute_tool(company, "pm", "list_files", "{}")[0]
    assert execute_tool(company, "pm", "write_file", "{bad json")[0].startswith("错误")


def test_edit_file_changes_only_the_target_part(company):
    """返工时只改有问题的部分：其余内容一字不变；找不到、不唯一、没有变化都作为错误告诉模型。"""
    doc = "# PRD\n\n## 取消规则\n开课前 1 小时可取消。\n\n## 候补\n满员后可排队，最多 10 人。\n\n## 备注\n最多 10 人。\n"
    company.write_file("docs/PRD.md", doc, author="pm")
    edit = lambda **a: execute_tool(company, "engineer" if a.pop("by_engineer", False) else "pm", "edit_file",
                                    json.dumps({"path": "docs/PRD.md", **a}, ensure_ascii=False))
    result, summary, wrote = edit(old_string="开课前 1 小时可取消。", new_string="开课前 2 小时可取消，之后取消记爽约。")
    assert wrote and summary["ok"] and "共替换 1 处" in result
    assert company.read_file("docs/PRD.md") == doc.replace("开课前 1 小时可取消。", "开课前 2 小时可取消，之后取消记爽约。")
    for args, error in [({"old_string": "开课前 3 小时", "new_string": "x"}, "没有找到"),
                        ({"old_string": "最多 10 人。", "new_string": "最多 20 人。"}, "出现了 2 次"),
                        ({"old_string": "", "new_string": "x"}, "不能为空"),
                        ({"old_string": "## 候补", "new_string": "## 候补"}, "一样")]:
        result, summary, wrote = edit(**args)
        assert result.startswith("错误") and error in result and not wrote and summary["ok"] is False
    edit(old_string="最多 10 人。", new_string="最多 20 人。", replace_all=True)
    assert company.read_file("docs/PRD.md").count("最多 20 人。") == 2
    result, _, _ = execute_tool(company, "pm", "edit_file", '{"path": "docs/none.md", "old_string": "a", "new_string": "b"}')
    assert "文件不存在" in result
    edit(old_string="## 备注", new_string="## 附注", by_engineer=True)
    assert next(f for f in company.list_files() if f["path"] == "docs/PRD.md")["author"] == "engineer"


def test_edit_file_batch_is_all_or_nothing(company):
    """一次提交多处修改：按顺序生效；任何一处不对，整次都不生效，并指出是第几处。"""
    company.write_file("docs/a.md", "甲：1\n乙：2\n丙：3\n", author="pm")
    batch = lambda edits: execute_tool(company, "pm", "edit_file",
                                       json.dumps({"path": "docs/a.md", "edits": edits}, ensure_ascii=False))
    result, summary, wrote = batch([{"old_string": "甲：1", "new_string": "甲：10"},
                                    {"old_string": "甲：10\n乙：2", "new_string": "甲：10\n乙：20"},  # 基于上一处修改之后的内容
                                    {"old_string": "丙：3\n", "new_string": ""}])
    assert wrote and "3 处修改" in result and company.read_file("docs/a.md") == "甲：10\n乙：20\n"
    result, summary, wrote = batch([{"old_string": "甲：10", "new_string": "甲：100"},
                                    {"old_string": "不存在的原文", "new_string": "x"}])
    assert result.startswith("错误") and "第 2 处修改" in result and "都没有生效" in result and not wrote
    assert company.read_file("docs/a.md") == "甲：10\n乙：20\n"                     # 第 1 处也没有生效
    assert batch([])[0].startswith("错误") and batch("不是列表")[0].startswith("错误")


def test_parallel_edits_to_one_file_do_not_lose_updates(company):
    """两个同事同时改同一个文件的不同地方：读出、修改、写回在锁里完成，谁的修改都不会丢。"""
    import threading
    lines = [f"第 {i} 行" for i in range(40)]
    company.write_file("docs/shared.md", "\n".join(lines), author="pm")

    def worker(start):
        for i in range(start, 40, 2):
            execute_tool(company, "pm", "edit_file", json.dumps(
                {"path": "docs/shared.md", "edits": [{"old_string": f"第 {i} 行", "new_string": f"第 {i} 行（已改）"}]},
                ensure_ascii=False))
    done, seen = threading.Event(), []

    def reader():  # 同时有人在读：读到的总是完整的文件，写入也不会因为文件被打开而失败（Windows）
        while not done.is_set():
            seen.append(company.read_file("docs/shared.md").count("\n"))
    threads = [threading.Thread(target=worker, args=(s,)) for s in (0, 1)]
    r = threading.Thread(target=reader)
    r.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    done.set()
    r.join()
    assert seen and set(seen) == {39}                                                  # 从没读到写了一半的文件
    assert company.read_file("docs/shared.md").count("（已改）") == 40
    assert not [f for f in company.list_files() if f["path"].endswith(".tmp")]       # 临时文件不出现在文件区


def test_parse_plan_validates_and_drops_bad_dependencies():
    summary, tasks = parse_plan('前言 {"summary": "s", "tasks": ['
                                '{"key": "a", "title": "A", "assignee": "pm", "depends_on": ["b"]},'
                                '{"key": "b", "title": "B", "assignee": "engineer", "depends_on": ["a", "zzz"]}]} 后记',
                                ["pm", "engineer"])
    assert summary == "s"
    assert tasks[0]["depends_on"] == []          # 依赖后面的任务：去掉，避免环
    assert tasks[1]["depends_on"] == ["a"]       # 不存在的依赖：去掉
    with pytest.raises(PlanError):
        parse_plan('{"tasks": [{"title": "x", "assignee": "designer"}]}', ["pm"])
    with pytest.raises(PlanError):
        parse_plan("没有 JSON", ["pm"])


def test_reducers():
    assert add_usage({"pm": {"prompt": 1, "completion": 2, "calls": 1}},
                     {"pm": {"prompt": 10, "completion": 0, "cached": 8, "calls": 1}})["pm"] == {
        "prompt": 11, "completion": 2, "cached": 8, "calls": 2}


def test_no_extra_headers():
    """项目只按标准 OpenAI 兼容接口调用，不加额外请求头。"""
    from types import SimpleNamespace as NS

    from opc.config import LLMConfig
    from opc.llm import LLM

    class Stream(list):
        def close(self):
            pass

    llm = LLM(LLMConfig(api_key="k", base_url="http://127.0.0.1:8000/v1", model="m", provider="custom"))
    assert not llm.client.default_headers["User-Agent"].startswith("opc/")
    seen = []
    done = NS(choices=[NS(delta=NS(content="好", reasoning_content=None, tool_calls=None))], usage=None)
    llm.client = NS(chat=NS(completions=NS(create=lambda **kw: seen.append(kw) or Stream([done]))))
    llm.chat([{"role": "user", "content": "x"}])
    assert "extra_headers" not in seen[0]


def test_role_can_pick_a_different_provider(tmp_path, monkeypatch):
    for k, v in {"OPC_PROVIDER": "", "OPC_BASE_URL": "https://api.deepseek.com", "OPC_MODEL": "deepseek-flash",
                 "OPC_API_KEY": "default-key"}.items():
        monkeypatch.setenv(k, v)
    c = CompanyStore(tmp_path).create("公司", "", [
        {"id": "pm"},
        {"id": "engineer", "provider": "custom", "base_url": "http://127.0.0.1:8000/v1", "model": "m2", "api_key": "oc-key"},
        {"id": "qa", "provider": "custom", "base_url": "https://my.example.com/v1", "model": "m", "api_key": "k3"}])
    pm, eng, qa = (c.llm_config(r) for r in ("pm", "engineer", "qa"))
    assert (pm.provider, pm.base_url, pm.model, pm.api_key) == ("deepseek", "https://api.deepseek.com", "deepseek-flash", "default-key")
    # 选了别的服务商：地址和模型用那个服务商的默认值，不沿用默认配置的
    assert (eng.provider, eng.base_url, eng.model, eng.api_key) == ("custom", "http://127.0.0.1:8000/v1", "m2", "oc-key")
    assert (qa.provider, qa.base_url, qa.model) == ("custom", "https://my.example.com/v1", "m")
    assert c.public_meta()["roles"][1]["provider"] == "custom" and "oc-key" not in str(c.public_meta())
    with pytest.raises(ValueError, match="接口地址"):
        CompanyStore(tmp_path).create("x", "", [{"id": "pm", "provider": "custom"}])
    with pytest.raises(ValueError, match="服务商"):
        CompanyStore(tmp_path).create("x", "", [{"id": "pm", "provider": "nope"}])


def test_llm_retries_cover_a_two_minute_outage(monkeypatch):
    """网络中断约两分钟时不能直接失败；等待期间检查停止标志。"""
    import httpx
    from openai import APIConnectionError
    from opc import llm as llm_module
    from opc.config import LLMConfig
    assert sum(llm_module.RETRY_DELAYS) >= 120
    waits, calls = [], []
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: waits.append(s))
    client = llm_module.LLM(LLMConfig(api_key="k", base_url="http://x/v1", model="m"))

    def boom(*a, **k):
        calls.append(1)
        if len(calls) < 6:
            raise APIConnectionError(request=httpx.Request("POST", "http://x"))
        return "ok"
    monkeypatch.setattr(client, "_chat_once", boom)
    ticks = []
    assert client.chat([], on_delta=lambda kind, text: ticks.append(text)) == "ok"
    assert len(calls) == 6 and ticks and set(ticks) == {""}


def test_read_file_returns_whole_documents_and_pages_huge_ones():
    from opc.agents import MAX_READ_CHARS, read_segment
    doc = "# 设计\n" + "这一行是设计说明。\n" * 5000   # 约 5 万字：以前只能读到前 2 万字
    assert len(doc) < MAX_READ_CHARS and read_segment(doc) == doc
    first = read_segment(doc, 1, limit=10_000)
    assert first.endswith("继续读请用 start_line=1001）") and first.startswith("# 设计")
    last = read_segment(doc, 4500, limit=10_000)
    assert last.endswith("已读到结尾）") and "第 4500～5001 行" in last


@pytest.fixture
def gate(monkeypatch):
    from opc import llm as llm_module
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)      # 跳过前几次的短重试
    monkeypatch.setattr(llm_module.NetworkGate, "PROBE_SECONDS", 0.05)  # 暂停后每 0.05 秒探测一次
    llm_module.NETWORK.clear()
    yield llm_module
    llm_module.NETWORK.clear()
    llm_module.NETWORK.max_wait = None


def _flaky_client(llm_module, fail_times):
    import httpx
    from openai import APIConnectionError
    from opc.config import LLMConfig
    client = llm_module.LLM(LLMConfig(api_key="k", base_url="http://x/v1", model="m"))
    calls = []

    def once(*a, **k):
        calls.append(1)
        if len(calls) <= fail_times:
            raise APIConnectionError(request=httpx.Request("POST", "http://x"))
        return "ok"
    client._chat_once = once
    return client, calls


def test_long_outage_pauses_instead_of_failing_and_resumes(gate):
    """重试用完后不报错：进入暂停状态并通知界面，网络恢复后接着这一步继续。"""
    changes = []
    gate.NETWORK.listeners.append(lambda: changes.append(gate.NETWORK.state))
    try:
        client, calls = _flaky_client(gate, len(gate.RETRY_DELAYS) + 3)
        assert client.chat([]) == "ok"
    finally:
        gate.NETWORK.listeners.clear()
    assert len(calls) == len(gate.RETRY_DELAYS) + 4
    assert changes[0]["reason"].startswith("连不上模型服务") and "APIConnectionError" in changes[0]["error"]
    assert changes[-1] is None and gate.NETWORK.state is None   # 恢复后解除暂停
    assert len(changes) == 2                                     # 连续几次探测失败只通知一次


def test_pause_can_be_stopped_or_retried_immediately(gate):
    import threading
    import time as real_time
    from opc.agents import Cancelled
    gate.NetworkGate.PROBE_SECONDS = 60
    stop = threading.Event()

    def on_delta(kind, text):
        if stop.is_set():
            raise Cancelled()
    client, _ = _flaky_client(gate, 10**9)
    result = {}

    def run():
        try:
            client.chat([], on_delta=on_delta)
        except Cancelled:
            result["stopped"] = True
    t = threading.Thread(target=run)
    t.start()
    while gate.NETWORK.state is None:
        real_time.sleep(0.01)
    stop.set()
    gate.NETWORK.retry_now()      # 「停止」会顺便唤醒暂停中的角色
    t.join(2)
    assert result.get("stopped") and not t.is_alive()

    client, calls = _flaky_client(gate, len(gate.RETRY_DELAYS) + 1)
    t = threading.Thread(target=lambda: result.setdefault("ok", client.chat([])))
    t.start()
    while gate.NETWORK.state is None:
        real_time.sleep(0.01)
    gate.NETWORK.retry_now()      # 「立即重试」不用等 60 秒
    t.join(2)
    assert result["ok"] == "ok" and gate.NETWORK.state is None


def test_pause_has_optional_limit(gate):
    from openai import APIConnectionError
    gate.NETWORK.max_wait = 0.1
    client, _ = _flaky_client(gate, 10**9)
    with pytest.raises(APIConnectionError):
        client.chat([])


def test_cached_tokens_reads_both_formats():
    """缓存命中数：DeepSeek 格式（prompt_cache_hit_tokens）和 OpenAI 格式（prompt_tokens_details.cached_tokens）都要认，
    只读一种会把命中一直统计成 0（不少服务商返回的是 OpenAI 格式）。"""
    from types import SimpleNamespace
    from opc.llm import cached_tokens
    assert cached_tokens(SimpleNamespace(prompt_cache_hit_tokens=500)) == 500
    assert cached_tokens(SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=7168))) == 7168
    assert cached_tokens(SimpleNamespace(prompt_tokens_details={"cached_tokens": 12})) == 12
    assert cached_tokens(SimpleNamespace()) == 0
