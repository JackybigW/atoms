from services.approval_gate import ApprovalGate


def test_write_allowed_after_plan_even_without_active_task():
    gate = ApprovalGate(requires_approval=True)
    gate.approve(request_key="req-1")
    gate.record_plan_written("docs/plans/plan.md")

    gate.check_write("/workspace/src/app.py")


def test_write_not_gated_when_approval_not_required():
    gate = ApprovalGate(requires_approval=False)

    gate.check_write("/workspace/src/app.py")
