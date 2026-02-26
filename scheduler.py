"""
Background scheduler for YouTube Channel Analyzer.
Runs daily/weekly alert checks via APScheduler.
"""
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
    return _scheduler


def start():
    import alerts
    from analyzer import load_api_key
    sched = get_scheduler()

    # Daily at 08:00
    sched.add_job(
        lambda: alerts.check_all_channels_for_alerts("daily"),
        CronTrigger(hour=8, minute=0),
        id="daily_alerts", replace_existing=True,
        coalesce=True, max_instances=1
    )
    # Weekly on Monday 08:00
    sched.add_job(
        lambda: alerts.check_all_channels_for_alerts("weekly"),
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="weekly_alerts", replace_existing=True,
        coalesce=True, max_instances=1
    )
    # Hourly new-video check (on the hour)
    sched.add_job(
        lambda: alerts.check_new_videos(load_api_key()),
        CronTrigger(minute=0),
        id="new_video_check", replace_existing=True,
        coalesce=True, max_instances=1
    )
    # Weekly PDF reports (Sunday 08:00)
    sched.add_job(
        lambda: alerts.send_weekly_pdf_reports(),
        CronTrigger(day_of_week="sun", hour=8),
        id="weekly_pdf", replace_existing=True,
        coalesce=True, max_instances=1
    )
    sched.start()
    atexit.register(lambda: sched.shutdown(wait=False))
