from typing import List, Optional

from pydantic import Field

from openmanus_runtime.agent.toolcall import ToolCallAgent
from openmanus_runtime.prompt.swe import SYSTEM_PROMPT
from openmanus_runtime.tool import Bash, StrReplaceEditor, Terminate, ToolCollection
from openmanus_runtime.tool.base import BaseTool


class SWEAgent(ToolCallAgent):
    """An agent that implements the SWEAgent paradigm for executing code and natural conversations."""

    name: str = "swe"
    description: str = "an autonomous AI programmer that interacts directly with the computer to solve tasks."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = ""

    available_tools: ToolCollection = ToolCollection(
        Bash(), StrReplaceEditor(), Terminate()
    )
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    max_steps: int = 20

    @classmethod
    def with_tools(
        cls,
        bash_tool: Optional[BaseTool] = None,
        editor_tool: Optional[BaseTool] = None,
        **kwargs,
    ) -> "SWEAgent":
        """Create a SWEAgent with custom tool instances."""
        tools = ToolCollection(
            bash_tool or Bash(),
            editor_tool or StrReplaceEditor(),
            Terminate(),
        )
        return cls(available_tools=tools, **kwargs)
