import pytest

from openmanus_runtime.agent.toolcall import ToolCallAgent
from openmanus_runtime.schema import AssistantResponse, Function, ToolCall


@pytest.mark.asyncio
async def test_toolcall_agent_finishes_after_plain_text_reply_without_tools():
    agent = ToolCallAgent(max_steps=20)
    calls = {"count": 0}

    async def fake_ask_tool(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return AssistantResponse(
                content="Hello Jacky! Tell me what you'd like to build.",
                tool_calls=[],
            )
        # next_step_prompt tells the agent to call terminate when done
        return AssistantResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_term",
                    function=Function(name="terminate", arguments='{"status": "success"}'),
                )
            ],
        )

    agent.llm.ask_tool = fake_ask_tool  # type: ignore[method-assign]

    await agent.run("我叫 jacky, 一个 ai")

    assert calls["count"] == 2, (
        f"Expected 2 LLM calls (reply + terminate), got {calls['count']}"
    )
    tool_messages = [m for m in agent.messages if m.role == "tool"]
    assert any("terminate" in (m.name or "") for m in tool_messages), (
        "Expected a terminate tool call"
    )
