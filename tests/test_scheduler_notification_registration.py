import logging

from app.background_jobs import scheduler as scheduler_module


class FakeScheduler:
    def __init__(self, **kwargs):
        self.jobs = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append(kwargs["id"])

    def start(self):
        pass


def test_batch_notification_noop_job_is_disabled_by_default(monkeypatch, caplog):
    monkeypatch.delenv("ENABLE_BATCH_NOTIFICATIONS", raising=False)
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", FakeScheduler)

    with caplog.at_level(logging.INFO):
        scheduler = scheduler_module.start_scheduler()

    assert "batch_notification_job" not in scheduler.jobs
    assert "delivery is not implemented" in caplog.text


def test_batch_notification_noop_job_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ENABLE_BATCH_NOTIFICATIONS", "true")
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", FakeScheduler)

    scheduler = scheduler_module.start_scheduler()

    assert "batch_notification_job" in scheduler.jobs
