"""OpenAI 兼容的流式 LLM 客户端。每个角色一个实例，可以接不同服务商的模型。"""
import threading
import time
from types import SimpleNamespace
from typing import Callable

import httpx
from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from . import tracing
from .config import LLMConfig

# 流式回调：(类型 "text" 或 "reasoning", 新增的文字)
DeltaFn = Callable[[str, str], None]
# 工具参数的流式回调：(第几个工具调用, 工具名, 新增的参数片段)。用来在界面上实时显示正在写的文档
ToolDeltaFn = Callable[[int, str, str], None]

# read：两次收到数据之间最多等多久。超过就认为连接卡住了，而不是像 SDK 默认那样一直等 10 分钟
TIMEOUT = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)
# 临时性错误的重试间隔（秒）。网络短暂中断（电脑休眠唤醒、代理重连）可能持续一两分钟，总等待要覆盖这种情况
# （实测 2026-10-12 断网约两分钟，之前只重试 22 秒，三家公司同时失败）
RETRY_DELAYS = (2, 5, 10, 20, 30, 60)
TRANSIENT_ERRORS = (APIConnectionError, InternalServerError, RateLimitError, httpx.TransportError)


class NetworkGate:
    """重试几次仍然失败时，角色不失败，而是原地暂停：每 PROBE_SECONDS 秒自动重试一次，恢复后从中断的那一步继续
    （角色已经做到一半的对话和工具调用都还在，不用重做）。界面上可以点「立即重试」，也可以点「停止」。

    所有正在等待的角色共用一个暂停状态，运行时通过 listeners 收到变化并推送给浏览器。
    """
    PROBE_SECONDS = 30

    def __init__(self):
        self._cond = threading.Condition()
        self._generation = 0          # 每次「立即重试」加一，唤醒所有正在等待的角色
        self.state: dict | None = None  # None 表示正常；暂停时是 {"since": 时间戳, "reason": 原因, "error": 错误}
        self.listeners: list[Callable[[], None]] = []
        self.max_wait: float | None = None  # 最多暂停多少秒；None 表示一直等，直到恢复或用户停止

    def wait(self, error: Exception, on_delta: DeltaFn | None) -> None:
        """暂停一个探测周期（或直到「立即重试」），然后返回让调用方重试。等待中用户点停止会从 on_delta 抛出异常。"""
        with self._cond:
            generation = self._generation
            changed = self.state is None
            if changed:
                self.state = {"since": time.time(), "reason": describe(error), "error": f"{type(error).__name__}: {error}"[:300]}
        if changed:
            self._notify()
        deadline = time.monotonic() + self.PROBE_SECONDS
        while time.monotonic() < deadline:
            if on_delta:
                on_delta("text", "")
            with self._cond:
                if self._generation != generation:
                    return
                self._cond.wait(0.5)

    def retry_now(self) -> None:
        with self._cond:
            self._generation += 1
            self._cond.notify_all()

    def online(self) -> None:
        """有请求成功了：解除暂停。"""
        if self.state is not None:
            self.clear()

    def clear(self) -> None:
        with self._cond:
            changed, self.state = self.state is not None, None
        if changed:
            self._notify()

    def _notify(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass


def describe(error: Exception) -> str:
    if isinstance(error, RateLimitError):
        return "模型服务限流或额度用完"
    return "连不上模型服务（网络中断或服务端故障）"


NETWORK = NetworkGate()


class LLM:
    def __init__(self, cfg: LLMConfig):
        # 重试由 chat() 自己处理：SDK 的自动重试管不到流式输出中途断开的情况
        self.client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=TIMEOUT, max_retries=0)
        self.model = cfg.model

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_delta: DeltaFn | None = None, json_mode: bool = False, on_tool_delta: ToolDeltaFn | None = None):
        """流式调用，边生成边通过 on_delta 推送，最后返回 content / reasoning_content / tool_calls / usage。

        on_delta 里抛出的异常（比如用户点了停止）会中断生成。网络超时、连接中断、限流、服务端错误会自动重试几次，
        仍然失败就暂停等待（见 NetworkGate），恢复后接着重试。
        """
        last = messages[-1].get("content") if messages else ""
        with tracing.span(f"模型调用 {self.model}", "llm", **{
                "gen_ai.operation.name": "chat", "gen_ai.request.model": self.model,
                "gen_ai.system": str(getattr(getattr(self.client, "base_url", None), "host", "") or ""), "opc.messages": len(messages),
                "opc.tools": len(tools or []), "opc.json_mode": json_mode,
                "opc.input_preview": tracing.preview(last or "")}) as span:
            started, first = time.monotonic(), []

            def mark(fn):  # 记下第一个字（或第一段工具参数）到达的时间：首字延迟
                def wrapped(*a):
                    if not first and a and a[-1]:
                        first.append(time.monotonic())
                    if fn:
                        return fn(*a)
                return wrapped
            on_delta_t, on_tool_t = mark(on_delta), (mark(on_tool_delta) if on_tool_delta else None)
            attempt, paused_at = 0, None
            while True:
                try:
                    reply = self._chat_once(messages, tools, on_delta_t, json_mode, on_tool_t)
                    NETWORK.online()
                    break
                except TRANSIENT_ERRORS as e:
                    if attempt < len(RETRY_DELAYS):
                        span.set(**{"opc.last_retry_error": f"{type(e).__name__}: {e}"[:200]})
                        self._wait(RETRY_DELAYS[attempt], on_delta)
                        attempt += 1
                        continue
                    paused_at = paused_at or time.monotonic()
                    if NETWORK.max_wait is not None and time.monotonic() - paused_at >= NETWORK.max_wait:
                        raise
                    NETWORK.wait(e, on_delta)
            u, calls = getattr(reply, "usage", None), getattr(reply, "tool_calls", None)
            finish, content = getattr(reply, "finish_reason", None), getattr(reply, "content", None)
            span.set(**{"gen_ai.usage.input_tokens": getattr(u, "prompt", None),
                        "gen_ai.usage.output_tokens": getattr(u, "completion", None),
                        "gen_ai.usage.cached_tokens": getattr(u, "cached", None),
                        "gen_ai.response.finish_reasons": [finish] if finish else None,
                        "opc.retries": attempt or None,
                        "opc.ttft_ms": int((first[0] - started) * 1000) if first else None,
                        "opc.tool_calls": [c.function.name for c in calls] if calls else None,
                        "opc.output_preview": tracing.preview(content) if content else None})
            return reply

    @staticmethod
    def _wait(seconds: float, on_delta: DeltaFn | None) -> None:
        """分成小段等待，每段之前调用一次 on_delta（空文字）：用户点停止时立即中断，不用等完这一轮。"""
        steps = max(1, int(seconds / 0.5))
        for _ in range(steps):
            if on_delta:
                on_delta("text", "")
            time.sleep(seconds / steps)

    def _chat_once(self, messages, tools, on_delta, json_mode, on_tool_delta=None):
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        stream = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.3, stream=True,
            stream_options={"include_usage": True}, **kwargs,
        )
        content, reasoning = [], []
        calls: dict[int, dict] = {}  # 工具调用按 index 分片到达，需要拼起来
        usage = None
        finish = None  # 结束原因：stop 正常结束，length 达到输出上限被截断
        try:
            for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                finish = getattr(chunk.choices[0], "finish_reason", None) or finish
                delta = chunk.choices[0].delta
                r = getattr(delta, "reasoning_content", None)  # 思考模型的推理过程
                if r:
                    reasoning.append(r)
                    if on_delta:
                        on_delta("reasoning", r)
                if delta.content:
                    content.append(delta.content)
                    if on_delta:
                        on_delta("text", delta.content)
                for tc in delta.tool_calls or []:
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        c["id"] = tc.id
                    if tc.function and tc.function.name:
                        c["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        c["arguments"] += tc.function.arguments
                        if on_tool_delta:
                            on_tool_delta(tc.index, c["name"], tc.function.arguments)
        finally:
            stream.close()
        tool_calls = [SimpleNamespace(id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
                      for _, c in sorted(calls.items())] or None
        return SimpleNamespace(
            content="".join(content),
            reasoning_content="".join(reasoning) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=SimpleNamespace(prompt=usage.prompt_tokens, completion=usage.completion_tokens,
                                  cached=cached_tokens(usage)) if usage else None,
        )


def cached_tokens(usage) -> int:
    """命中缓存的输入 token 数。DeepSeek 格式是 prompt_cache_hit_tokens；OpenAI 格式（Kimi、Qwen 等很多服务商）
    是 prompt_tokens_details.cached_tokens。只读其中一种会把命中一直统计成 0。"""
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if hit:
        return hit
    details = getattr(usage, "prompt_tokens_details", None)
    if isinstance(details, dict):
        return details.get("cached_tokens") or 0
    return getattr(details, "cached_tokens", 0) or 0
