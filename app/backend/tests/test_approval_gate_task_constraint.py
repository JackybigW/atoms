from services.approval_gate import ApprovalGate


def test_write_blocked_when_no_active_task_after_todos_written():
    gate = ApprovalGate(requires_approval=True)
    gate.approve(request_key="req-1")
    gate.record_plan_written("docs/plans/plan.md")
    gate.record_todo_written()
    # No task active yet — write should be blocked
    try:
        gate.check_write("/workspace/src/app.py")
        assert False, "Expected ToolError"
    except Exception as e:
        assert "no task is currently in_progress" in str(e)


def test_write_allowed_when_task_active():
    gate = ApprovalGate(requires_approval=True)
    gate.approve(request_key="req-1")
    gate.record_plan_written("docs/plans/plan.md")
    gate.record_todo_written()
    gate.notify_task_active()
    # Should not raise
    gate.check_write("/workspace/src/app.py")


def test_write_blocked_again_after_task_deactivated():
    gate = ApprovalGate(requires_approval=True)
    gate.approve(request_key="req-1")
    gate.record_plan_written("docs/plans/plan.md")
    gate.record_todo_written()
    gate.notify_task_active()
    gate.notify_no_active_task()
    try:
        gate.check_write("/workspace/src/app.py")
        assert False, "Expected ToolError"
    except Exception as e:
        assert "no task is currently in_progress" in str(e)


def test_write_not_gated_when_approval_not_required():
    gate = ApprovalGate(requires_approval=False)
    # No approval, no plan, no todo — should not gate
    gate.check_write("/workspace/src/app.py")
