from openmanus_runtime.tool.base import BaseTool


_TERMINATE_DESCRIPTION = """Terminate the interaction when the request is met OR if the assistant cannot proceed further with the task.
When you have finished all the tasks, call this tool to end the work."""


class Terminate(BaseTool):
    name: str = "terminate"
    description: str = _TERMINATE_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "The finish status of the interaction.",
                "enum": ["success", "failure"],
            },
            "summary": {
                "type": "string",
                "description": "A final summary of the work completed, to be shown to the user.",
            }
        },
        "required": ["status"],
    }

    async def execute(self, status: str, summary: str = "") -> str:
        """Finish the current execution"""
        if summary:
            return f"The interaction has been completed with status: {status}\nSummary: {summary}"
        return f"The interaction has been completed with status: {status}"
