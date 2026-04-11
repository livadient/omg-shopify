"""APScheduler setup for agent cron jobs."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    """Register all agent jobs and start the scheduler."""
    tz = settings.agent_timezone

    # Agent 3: Ranking Advisor — daily Mon-Fri at 07:00
    from app.agents.ranking_advisor import generate_daily_report
    scheduler.add_job(
        generate_daily_report,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0, timezone=tz),
        id="ranking_advisor",
        name="Daily Ranking Advisor",
        replace_existing=True,
    )

    # Agent 1: Blog Writer — Tue & Fri at 05:00
    from app.agents.blog_writer import generate_proposal
    scheduler.add_job(
        generate_proposal,
        CronTrigger(day_of_week="tue,fri", hour=5, minute=0, timezone=tz),
        id="blog_writer",
        name="SEO Blog Writer",
        replace_existing=True,
    )

    # Agent 2: Design Creator — daily at 04:00
    from app.agents.design_creator import research_trends
    scheduler.add_job(
        research_trends,
        CronTrigger(hour=4, minute=0, timezone=tz),
        id="design_creator",
        name="Trend Research & Design Creator",
        replace_existing=True,
    )

    # Atlas Campaign Proposals — weekly Monday at 08:00 (all 3 markets)
    from app.agents.ranking_advisor import propose_all_campaigns
    scheduler.add_job(
        propose_all_campaigns,
        CronTrigger(day_of_week="mon", hour=8, minute=0, timezone=tz),
        id="campaign_proposals",
        name="Atlas Campaign Proposals (CY/GR/EU)",
        replace_existing=True,
    )

    # SEO optimization (Sphinx) — DISABLED: manually executing Atlas' recommendations instead
    # from app.seo_management import run_all as seo_run_all
    # scheduler.add_job(
    #     seo_run_all,
    #     CronTrigger(day_of_week="mon-fri", hour=4, minute=30, timezone=tz),
    #     id="seo_optimizer",
    #     name="SEO Optimization (all tasks)",
    #     replace_existing=True,
    # )

    # Translation Checker — daily at 02:00
    from app.agents.translation_checker import check_and_fix_translations
    scheduler.add_job(
        check_and_fix_translations,
        CronTrigger(hour=2, minute=0, timezone=tz),
        id="translation_checker",
        name="Greek Translation Checker",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(f"Agent scheduler started (timezone: {tz})")
    for job in scheduler.get_jobs():
        logger.info(f"  Scheduled: {job.name} — next run: {job.next_run_time}")


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Agent scheduler stopped")
