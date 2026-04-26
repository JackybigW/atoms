import uuid
from typing import Any, Awaitable, Callable, Optional

from pydantic import Field

from openmanus_runtime.agent.swe import SWEAgent
from openmanus_runtime.config import LLMSettings, config
from openmanus_runtime.llm import LLM
from openmanus_runtime.schema import Message
from openmanus_runtime.tool.file_operators import FileOperator
from openmanus_runtime.tool.str_replace_editor import StrReplaceEditor
from services.workspace_event_emitter import WorkspaceEventEmitter


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class StreamingSWEAgent(SWEAgent):
    event_emitter: Optional[EventEmitter] = Field(default=None, exclude=True)

    async def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_emitter is None:
            return
        await self.event_emitter({"type": event_type, **payload})

    async def step(self) -> str:
        prev_len = len(self.memory.messages)
        should_act = await self.think()
        new_messages = self.memory.messages[prev_len:]
        workspace_events = WorkspaceEventEmitter(self.event_emitter) if self.event_emitter is not None else None

        for message in new_messages:
            if message.role != "assistant":
                continue

            # Suppress content when the message also calls draft_plan: the plan UI
            # handles display via draft_plan.* events; emitting raw content here would
            # show the JSON arguments as a chat bubble before the plan card appears.
            tool_names = {tc.function.name for tc in (message.tool_calls or [])}
            
            should_emit_content = True
            if "draft_plan" in tool_names:
                should_emit_content = False
            elif message.content:
                # Check if it's just the raw JSON of the plan (which can happen immediately after draft_plan returns)
                c_stripped = message.content.strip()
                if c_stripped.startswith("```json"):
                    c_stripped = c_stripped[7:].strip()
                if c_stripped.endswith("```"):
                    c_stripped = c_stripped[:-3].strip()
                    
                if c_stripped.startswith("[") and c_stripped.endswith("]") and '"id"' in c_stripped and '"text"' in c_stripped:
                    try:
                        import json
                        parsed = json.loads(c_stripped)
                        if isinstance(parsed, list) and len(parsed) > 0 and "id" in parsed[0] and "text" in parsed[0]:
                            should_emit_content = False
                    except Exception:
                        pass

            if (message.content or message.thinking) and should_emit_content:
                await self._emit(
                    "assistant",
                    content=message.content,
                    thinking=message.thinking,
                    agent=self.name,
                )

            if message.tool_calls:
                _SILENT_TOOLS = frozenset({"draft_plan", "todo_write", "load_skill"})
                for tool_call in message.tool_calls:
                    if workspace_events is not None:
                        if tool_call.function.name not in _SILENT_TOOLS:
                            await workspace_events.progress(f"Running {tool_call.function.name}")
                            await workspace_events.terminal_log(f"$ tool {tool_call.function.name}")

        if not should_act:
            return "Thinking complete - no action needed"

        return await self.act()

    async def act(self) -> str:
        if not self.tool_calls:
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        workspace_events = WorkspaceEventEmitter(self.event_emitter) if self.event_emitter is not None else None
        for command in self.tool_calls:
            self._current_base64_image = None
            result = await self.execute_tool(command)
            tool_msg = Message.tool_message(
                content=result,
                tool_call_id=command.id,
                name=command.function.name,
                base64_image=self._current_base64_image,
            )
            self.memory.add_message(tool_msg)
            if workspace_events is not None:
                await workspace_events.terminal_log(result)
            results.append(result)

        return "\n\n".join(results)


    @classmethod
    def build_for_workspace(
        cls,
        llm,
        event_emitter: EventEmitter,
        file_operator: FileOperator,
        bash_session,
    ) -> "StreamingSWEAgent":
        """Create a StreamingSWEAgent scoped to a project workspace.

        Args:
            llm: LLM instance (or None for default).
            event_emitter: Async callable for SSE events.
            file_operator: A ProjectFileOperator mapping container paths to host.
            bash_session: A ContainerBashSession (or None for local bash).
        """
        from openmanus_runtime.tool.bash import ContainerBash, Bash
        from openmanus_runtime.tool import Terminate, ToolCollection

        editor = StrReplaceEditor.with_operator(file_operator)

        if bash_session is not None:
            bash_tool = ContainerBash.with_existing_session(bash_session)
        else:
            bash_tool = Bash()

        tools = ToolCollection(bash_tool, editor, Terminate())

        kwargs = {"available_tools": tools, "event_emitter": event_emitter}
        if llm is not None:
            kwargs["llm"] = llm

        return cls(**kwargs)


def build_agent_llm(model: Optional[str]) -> Optional[LLM]:
    if not model:
        return None

    base_settings = config.llm["default"]
    filtered = LLMSettings(
        model=model,
        base_url=base_settings.base_url,
        api_key=base_settings.api_key,
        max_tokens=base_settings.max_tokens,
        max_input_tokens=base_settings.max_input_tokens,
        temperature=base_settings.temperature,
        api_type=base_settings.api_type,
        api_version=base_settings.api_version,
    )
    return LLM(
        config_name=f"request_{uuid.uuid4().hex}",
        llm_config={"default": filtered},
    )
