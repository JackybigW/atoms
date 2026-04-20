from typing import Literal, Optional

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    prompt: str = Field(..., description="Prompt to send to the agent.")
    agent: Literal["swe"] = Field(default="swe", description="Agent runtime to use.")
    model: Optional[str] = Field(default=None, description="Optional model override.")
    project_id: int = Field(..., description="Project context (required).")
