"""
Automatic triggers. Run this once (`python scheduler.py`) on any always-on
host (a free-tier VM, a small VPS, or a cron-triggered GitHub Action) and it
fires the three daily/weekly jobs forever without manual intervention.

Or set RUN_SCHEDULER_IN_API=true and the API process runs the same schedule
itself (see api.py), so a server needs one service instead of two.
"""
from apscheduler.schedulers.blocking import BlockingScheduler

import config
from jobs.daily_run import is_running, run_daily_search_and_apply
from jobs.daily_report import run_daily_report
from jobs.weekly_news import run_weekly_news_digest


def _scheduled_search():
    # A search started from the dashboard and the 08:00 run would otherwise
    # process the same batch side by side. Only visible when both live in the
    # same process (RUN_SCHEDULER_IN_API); a separate scheduler.py process
    # can't see the API's run.
    if is_running():
        print("Scheduled search skipped: a search is already running.")
        return
    run_daily_search_and_apply()


def build_scheduler(scheduler_class=BlockingScheduler):
    """The permanent schedule, on SCHEDULER_TIMEZONE when one is set (a cloud
    VM's clock is UTC, which would move 08:00 to mid-morning in Cairo)."""
    kwargs = {"timezone": config.SCHEDULER_TIMEZONE} if config.SCHEDULER_TIMEZONE else {}
    scheduler = scheduler_class(**kwargs)
    scheduler.add_job(_scheduled_search, "cron", hour=8, id="daily_search_and_apply")
    scheduler.add_job(run_daily_report, "cron", hour=20, id="daily_report")
    scheduler.add_job(run_weekly_news_digest, "cron", day_of_week="mon", hour=9, id="weekly_news")
    return scheduler


# The one-time "run everything 1 minute after startup" verification block
# that used to live here is kept below rather than deleted -- automation is
# confirmed working now, and it was also a direct contributor to the
# "database is locked" failures (this test run firing while a manual
# "Search now" click was already mid-batch is exactly the kind of overlap
# that surfaced the bug). Add it back inside build_scheduler() if you need to
# re-verify end-to-end after a change, but remember it also fires a REAL
# Telegram send from the report jobs (since DRY_RUN=false), not a dry run.
#
# from datetime import datetime, timedelta
# soon = datetime.now() + timedelta(minutes=1)
# scheduler.add_job(run_daily_search_and_apply, "date", run_date=soon, id="daily_search_and_apply_test_run")
# scheduler.add_job(run_daily_report, "date", run_date=soon, id="daily_report_test_run")
# scheduler.add_job(run_weekly_news_digest, "date", run_date=soon, id="weekly_news_test_run")

if __name__ == "__main__":
    if config.RUN_SCHEDULER_IN_API:
        raise SystemExit("RUN_SCHEDULER_IN_API=true: the API already runs the schedule. "
                         "Running this too would fire every job twice.")
    tz = config.SCHEDULER_TIMEZONE or "the host's local time"
    print(f"Scheduler started ({tz}). Jobs: daily search+apply @08:00, "
          "daily report @20:00, weekly news Mon @09:00")
    build_scheduler().start()
