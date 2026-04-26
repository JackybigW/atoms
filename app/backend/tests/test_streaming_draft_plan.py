import pytest
import asyncio
from openmanus_runtime.schema import Message, AssistantResponse, ToolCall, Function
from openmanus_runtime.streaming import StreamingSWEAgent
from openmanus_runtime.tool.base import BaseTool, ToolResult
from openmanus_runtime.tool.tool_collection import ToolCollection

@pytest.mark.asyncio
async def test_streaming_agent_suppresses_raw_json_after_draft_plan():
    emitted_events = []
    async def mock_event_sink(event):
        emitted_events.append(event)
        
    class DummyLLM:
        def __init__(self):
            self.call_count = 0
            
        async def ask_tool(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                return AssistantResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            function=Function(
                                name="draft_plan",
                                arguments='{"request_key": "test", "items": [{"id": "1", "text": "Step 1"}]}'
                            )
                        )
                    ]
                )
            elif self.call_count == 2:
                # The LLM outputs the raw JSON!
                return AssistantResponse(
                    content='[{"id":"1","text":"Step 1"}]',
                    tool_calls=[
                        ToolCall(
                            id="call_2",
                            function=Function(name="bash", arguments='{"command": "echo 1"}')
                        )
                    ]
                )
            elif self.call_count == 3:
                return AssistantResponse(
                    content="Done!",
                    tool_calls=[
                        ToolCall(id="call_3", function=Function(name="terminate", arguments='{}'))
                    ]
                )
            return AssistantResponse(content="Fallback", tool_calls=[])
            
    # Mock tools
    class DummyBash(BaseTool):
        name: str = "bash"
        description: str = "bash"
        async def execute(self, **kwargs): return ToolResult(output="ok")
    class DummyDraftPlan(BaseTool):
        name: str = "draft_plan"
        description: str = "draft"
        async def execute(self, **kwargs): return ToolResult(output="Plan approved")
    class DummyTerminate(BaseTool):
        name: str = "terminate"
        description: str = "term"
        async def execute(self, **kwargs): return ToolResult(output="terminated")

    agent = StreamingSWEAgent(
        name="test_agent",
        available_tools=ToolCollection(DummyBash(), DummyDraftPlan(), DummyTerminate()),
        max_steps=5
    )
    # mock the llm properly
    agent.llm.ask_tool = DummyLLM().ask_tool
    agent.event_emitter = mock_event_sink
    
    await agent.run("Do something")
    
    # Collect all assistant events with content
    assistant_contents = [
        e.get("content") for e in emitted_events 
        if e.get("type") == "assistant" and e.get("content")
    ]
    
    # The bug: assistant_contents contains '[{"id":"1","text":"Step 1"}]'
    assert '[{"id":"1","text":"Step 1"}]' not in assistant_contents, "Raw JSON leaked into assistant content!"
