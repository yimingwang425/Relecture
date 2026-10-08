"""`AnthropicModel.stream` 的工具那一半（口径 F111），对着**真的 SDK 类型**断言。

客户端是替身（不出网），但流里的事件与快照用 `anthropic.types` 的真类造——
`to_dict()`、`content_block_start` 的形状都是 SDK 自己的，不是我们以为的。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

anthropic = pytest.importorskip("anthropic")
from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock  # noqa: E402

from explain.llm import AnthropicModel  # noqa: E402


class _Stream:
    def __init__(self, events, snapshot):
        self._events = events
        self.current_message_snapshot = snapshot

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)


class _Client:
    def __init__(self, events, snapshot):
        self.sent: dict[str, Any] = {}
        self.messages = SimpleNamespace(stream=self._stream)
        self._events, self._snapshot = events, snapshot

    def _stream(self, **kwargs):
        self.sent = kwargs
        return _Stream(self._events, self._snapshot)


def _message(content) -> Message:
    return Message(id="msg_1", type="message", role="assistant", model="claude-sonnet-5",
                   content=content, stop_reason="tool_use", stop_sequence=None,
                   usage={"input_tokens": 50, "output_tokens": 20})


def test_tool_use_is_parsed_and_content_is_kept_verbatim():
    blocks = [
        ThinkingBlock(type="thinking", thinking="", signature="sig-abc"),
        ToolUseBlock(type="tool_use", id="toolu_1", name="search_slices", input={"query": "Carnot"}),
    ]
    events = [SimpleNamespace(type="content_block_start", content_block=blocks[1])]
    client = _Client(events, _message(blocks))
    started: list[str] = []
    m = AnthropicModel(_client=client)
    tools = [{"name": "search_slices", "input_schema": {"type": "object"}}]
    c = m.stream("sys", [{"role": "user", "content": "q"}], tools=tools, on_tool=started.append)

    assert client.sent["tools"] == tools
    assert "tool_choice" not in client.sent
    assert started == ["search_slices"]
    assert [t.name for t in c.tool_calls] == ["search_slices"]
    assert c.tool_calls[0].input == {"query": "Carnot"}
    # thinking 块与签名原样保留：工具循环要把它接回 messages
    assert c.content[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    assert c.content[1]["type"] == "tool_use" and c.content[1]["id"] == "toolu_1"
    assert c.stop_reason == "tool_use"


def test_tool_choice_is_sent_when_given():
    client = _Client([], _message([TextBlock(type="text", text="答")]))
    AnthropicModel(_client=client).stream(
        "sys", [{"role": "user", "content": "q"}],
        tools=[{"name": "x", "input_schema": {"type": "object"}}], tool_choice={"type": "none"})
    assert client.sent["tool_choice"] == {"type": "none"}


def test_without_tools_the_request_is_unchanged():
    client = _Client([], _message([TextBlock(type="text", text="答")]))
    c = AnthropicModel(_client=client).stream("sys", [{"role": "user", "content": "q"}])
    assert "tools" not in client.sent and "tool_choice" not in client.sent
    assert c.tool_calls == () and c.content == ()
    assert "tool_calls" not in c.to_dict()
