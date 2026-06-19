import asyncio

from miniclaw import cron_scheduler as cron_module
from miniclaw.cron_scheduler import CronJob, CronScheduler


class _Agent:
    pass


def test_scheduler_does_not_fire_same_minute_twice(monkeypatch):
    scheduler = CronScheduler(_Agent())
    scheduler.add_job(CronJob("every-minute", "* * * * *", "check"))
    fired = []
    sleeps = 0

    async def fire(job):
        fired.append(job.name)

    async def sleep_once_then_cancel(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    scheduler._fire_job = fire
    monkeypatch.setattr(cron_module.asyncio, "sleep", sleep_once_then_cancel)

    try:
        asyncio.run(scheduler.run())
    except asyncio.CancelledError:
        pass

    assert fired == ["every-minute"]


def test_scheduler_isolates_a_failing_job(monkeypatch):
    scheduler = CronScheduler(_Agent())
    scheduler.add_job(CronJob("bad-job", "* * * * *", "check"))
    reached_sleep = False

    async def fail(_job):
        raise RuntimeError("provider unavailable")

    async def stop_after_loop(_seconds):
        nonlocal reached_sleep
        reached_sleep = True
        raise asyncio.CancelledError

    scheduler._fire_job = fail
    monkeypatch.setattr(cron_module.asyncio, "sleep", stop_after_loop)

    try:
        asyncio.run(scheduler.run())
    except asyncio.CancelledError:
        pass

    assert reached_sleep
