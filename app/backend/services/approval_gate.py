"""Approval gate for pre-approval write enforcement."""


class ApprovalGate:
    """Tracks draft_plan approval and implementation-plan authorship for a session."""

    def __init__(self, requires_approval: bool = False):
        self.requires_approval = requires_approval
        self._approved = False
        self._approved_request_key: str | None = None
        self._plan_path: str | None = None

    def approve(self, request_key: str | None = None) -> None:
        self._approved = True
        self._approved_request_key = request_key

    def record_plan_written(self, path: str) -> None:
        """Called when the agent writes any file under docs/plans/."""
        self._plan_path = path

    @property
    def is_locked(self) -> bool:
        """True if writes should be blocked (approval required but not yet granted)."""
        return self.requires_approval and not self._approved

    @property
    def plan_required_but_not_written(self) -> bool:
        """True if an implementation plan is expected but has not been written yet."""
        return self.requires_approval and self._approved and self._plan_path is None

    @property
    def plan_path(self) -> str | None:
        return self._plan_path

    @property
    def approved_request_key(self) -> str | None:
        return self._approved_request_key

    def check_write(self, path) -> None:
        """Raise ToolError if writes are not yet permitted."""
        normalized = str(path)
        is_plan_write = normalized.startswith("/workspace/docs/plans/") and normalized.endswith(".md")
        if self.is_locked:
            from openmanus_runtime.exceptions import ToolError
            raise ToolError(
                "Implementation writes are not allowed until the draft plan is approved. "
                "Call draft_plan first and wait for user approval."
            )
        if self.plan_required_but_not_written and not is_plan_write:
            from openmanus_runtime.exceptions import ToolError
            raise ToolError(
                "Implementation writes are blocked until the implementation plan exists. "
                "Write docs/plans/YYYY-MM-DD-{feature}.md first."
            )

    def check_todo_write(self) -> None:
        """Raise ToolError if todo_write is called before an implementation plan exists."""
        if self.plan_required_but_not_written:
            from openmanus_runtime.exceptions import ToolError
            raise ToolError(
                "Write an implementation plan to docs/plans/YYYY-MM-DD-{feature}.md first. "
                "Use str_replace_editor to create the plan file, then call todo_write."
            )
