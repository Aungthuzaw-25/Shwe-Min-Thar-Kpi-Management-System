from app.modules.tasks.models.task import TaskFrequency, TaskStatus


def test_task_status_enum_values_are_stable():
    assert TaskStatus.DRAFT.value == "Draft"
    assert TaskStatus.PENDING_L1.value == "Pending L1 Approval"
    assert TaskStatus.PENDING_L0.value == "Pending L0 Approval"
    assert TaskStatus.APPROVED.value == "Approved"
    assert TaskStatus.REJECTED.value == "Rejected"
    assert TaskStatus.COMPLETED.value == "Completed"


def test_task_frequency_enum_values_are_stable():
    assert TaskFrequency.ONCE.value == "ONCE"
    assert TaskFrequency.DAILY.value == "DAILY"
    assert TaskFrequency.WEEKLY.value == "WEEKLY"
    assert TaskFrequency.MONTHLY.value == "MONTHLY"
    assert TaskFrequency.YEARLY.value == "YEARLY"
