"""
Tests for agent conversation loop behavior.

Production scenario that must be reproduced:
  - User sends "hello"
  - Agent uses str_replace_editor to view workspace (step 1, has tool call)
  - Agent uses bash to check directory (step 2, has tool call)
  - Agent sends pure text "I'm ready and waiting..." (step 3, no tool call)
  - Agent sends pure text "I'm here to assist!" (step 4, no tool call)
  - ... loops until max_steps or accidentally calls terminate

The bug: text-only loop continues even after real tool calls were made earlier,
because the previous fix only checked "no tools used at all", which is False here.
"""
import pytest
from typing import Any

from openmanus_runtime.agent.toolcall import ToolCallAgent
from openmanus_runtime.schema import AssistantResponse, Function, ToolCall
from openmanus_runtime.tool.base import BaseTool, ToolResult
from openmanus_runtime.tool.tool_collection import ToolCollection
from openmanus_runtime.tool import Terminate


# ---------------------------------------------------------------------------
# Minimal mock tools that replicate what SWEAgent uses
# ---------------------------------------------------------------------------

class MockStrReplaceEditorTool(BaseTool):
    name: str = "str_replace_editor"
    description: str = "View/edit files"

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(output="total 0\n(empty workspace)")


class MockBashTool(BaseTool):
    name: str = "bash"
    description: str = "Execute bash commands"

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(output="/workspace\n")


class MockDraftPlanTool(BaseTool):
    name: str = "draft_plan"
    description: str = "Draft a plan for the user"

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(output="Plan drafted and awaiting approval.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_response(content: str) -> AssistantResponse:
    return AssistantResponse(content=content, tool_calls=[])


def _tool_response(name: str, args: str = "{}") -> AssistantResponse:
    return AssistantResponse(
        content="",
        tool_calls=[ToolCall(id=f"call_{name}", function=Function(name=name, arguments=args))],
    )


def _make_agent(extra_tools: list | None = None, max_steps: int = 10) -> ToolCallAgent:
    tools = [Terminate()]
    if extra_tools:
        tools.extend(extra_tools)
    return ToolCallAgent(
        name="test_agent",
        available_tools=ToolCollection(*tools),
        max_steps=max_steps,
    )


def _make_repeating_mock(responses: list[AssistantResponse]):
    """Returns a fake ask_tool that replays responses, then repeats the last one."""
    call_count = {"n": 0}

    async def fake_ask_tool(*args, **kwargs):
        i = call_count["n"]
        call_count["n"] += 1
        return responses[i] if i < len(responses) else responses[-1]

    return fake_ask_tool, call_count


# ---------------------------------------------------------------------------
# THE CRITICAL TEST — reproduces the exact production "hello" loop
#
# Pattern: tool call → tool call → text → text → text → text → ...
# Current code: skips loop detection because tool_calls_made=True
# Expected: agent terminates after ≤2 consecutive text-only replies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hello_loop_with_prior_tool_calls_FAILS_currently():
    """
    Exact production scenario:
    Step 1: str_replace_editor (view /workspace) — tool call
    Step 2: bash (pwd) — tool call
    Step 3: pure text "I'm ready and waiting..."
    Step 4: pure text "I'm here to assist!"
    Step 5+: more pure text (should have stopped by now)

    The agent must stop within 5 LLM calls.
    Currently it loops to max_steps because the fix checks `not tool_calls_made`
    which is False once any tool has been used.
    """
    agent = _make_agent(
        extra_tools=[MockStrReplaceEditorTool(), MockBashTool()],
        max_steps=10,
    )

    responses = [
        # Steps that mirror production behavior
        _tool_response("str_replace_editor", '{"command": "view", "path": "/workspace"}'),
        _tool_response("bash", '{"command": "pwd"}'),
        _text_response("I'm ready and waiting for your request! What would you like me to help you build today?"),
        _text_response("I'm here and ready to assist! Just let me know what you'd like to build."),
        _text_response("Still waiting for your instructions..."),
        _text_response("Please let me know how I can help!"),
        _text_response("I'm available to assist with any development task."),
        _text_response("Whenever you're ready, just let me know!"),
        _text_response("I'm here and ready!"),
        _text_response("Still here!"),
    ]
    fake, call_count = _make_repeating_mock(responses)
    agent.llm.ask_tool = fake  # type: ignore[method-assign]

    await agent.run("hello")

    assert call_count["n"] <= 5, (
        f"Agent made {call_count['n']} LLM calls for 'hello' (with prior tool calls). "
        f"Expected ≤5. The text-only loop was not stopped after consecutive pure-text replies."
    )


# ---------------------------------------------------------------------------
# Pure conversational (no tools at all) — must also stop quickly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("user_input", ["hello", "hi", "你好", "hey there"])
async def test_pure_conversational_stops_quickly(user_input: str):
    """No tool calls at all — pure greeting. Should stop within 3 LLM calls."""
    agent = _make_agent(max_steps=20)
    fake, call_count = _make_repeating_mock([
        _text_response("Hello! How can I help you today?"),
        _text_response("I'm ready to help! What would you like to build?"),
        _text_response("Just let me know what you need!"),
        _text_response("Still here, ready to assist!"),
        _text_response("Waiting for your request..."),
    ])
    agent.llm.ask_tool = fake  # type: ignore[method-assign]

    await agent.run(user_input)

    assert call_count["n"] <= 3, (
        f"Agent made {call_count['n']} LLM calls for '{user_input}' (no tools). "
        f"Expected ≤3."
    )


# ---------------------------------------------------------------------------
# Implementation task — preamble text THEN tool calls must still work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_implementation_task_preamble_then_tools_works():
    """
    Preamble text in step 1, then tool calls — agent must NOT stop after preamble.
    """
    agent = _make_agent(extra_tools=[MockDraftPlanTool()])
    responses = [
        _text_response("I'll help you build that! Let me start by drafting a plan."),
        _tool_response("draft_plan", '{"plan": "1. Setup\\n2. Build\\n3. Test"}'),
        _tool_response("terminate", '{"status": "success"}'),
    ]
    fake, call_count = _make_repeating_mock(responses)
    agent.llm.ask_tool = fake  # type: ignore[method-assign]

    await agent.run("帮我写一个网站")

    tool_messages = [m for m in agent.messages if m.role == "tool"]
    called_tools = [m.name for m in tool_messages]

    assert "draft_plan" in called_tools, (
        f"Agent stopped before calling draft_plan. Tools called: {called_tools}"
    )
    assert call_count["n"] == 3, (
        f"Expected 3 LLM calls (preamble + draft_plan + terminate), got {call_count['n']}"
    )


# ---------------------------------------------------------------------------
# Implementation task — post-work summary text must still be emitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_implementation_task_summary_after_work():
    """
    After tool calls complete, agent sends a summary text — this must NOT be
    cut off. Agent should then call terminate (not loop on the summary).
    """
    agent = _make_agent(extra_tools=[MockBashTool()])
    responses = [
        _tool_response("bash", '{"command": "echo done"}'),
        _text_response("I've completed the task! Here's a summary of what was built."),
        _tool_response("terminate", '{"status": "success"}'),
    ]
    fake, call_count = _make_repeating_mock(responses)
    agent.llm.ask_tool = fake  # type: ignore[method-assign]

    await agent.run("完成一个任务")

    tool_messages = [m for m in agent.messages if m.role == "tool"]
    called_tools = [m.name for m in tool_messages]

    assert "terminate" in called_tools, "Agent did not reach terminate after summary"
    assert call_count["n"] == 3, (
        f"Expected 3 calls (bash + summary + terminate), got {call_count['n']}"
    )
