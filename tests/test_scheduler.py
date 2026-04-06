"""Tests for app/agents/scheduler.py — APScheduler job registration."""
from unittest.mock import MagicMock, patch

import pytest


class TestStartScheduler:
    def test_registers_all_jobs(self):
        """Verify that start_scheduler registers the expected 4 jobs."""
        with patch("app.agents.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.get_jobs.return_value = []
            from app.agents.scheduler import start_scheduler
            start_scheduler()
            # 4 add_job calls: ranking_advisor, blog_writer, design_creator, seo_optimizer
            assert mock_scheduler.add_job.call_count == 4
            mock_scheduler.start.assert_called_once()

    def test_job_ids(self):
        """Verify job IDs match expectations."""
        with patch("app.agents.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.get_jobs.return_value = []
            from app.agents.scheduler import start_scheduler
            start_scheduler()

            job_ids = [
                call.kwargs["id"]
                for call in mock_scheduler.add_job.call_args_list
            ]
            assert "ranking_advisor" in job_ids
            assert "blog_writer" in job_ids
            assert "design_creator" in job_ids
            assert "seo_optimizer" in job_ids

    def test_job_schedules(self):
        """Verify the cron triggers have expected hours."""
        with patch("app.agents.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.get_jobs.return_value = []
            from app.agents.scheduler import start_scheduler
            start_scheduler()

            schedule_map = {}
            for call in mock_scheduler.add_job.call_args_list:
                job_id = call.kwargs["id"]
                trigger = call.args[1]  # CronTrigger is the second positional arg
                # Extract hour from the CronTrigger fields
                hour_field = None
                for field in trigger.fields:
                    if field.name == "hour":
                        hour_field = str(field)
                        break
                schedule_map[job_id] = hour_field

            assert schedule_map["ranking_advisor"] == "7"
            assert schedule_map["blog_writer"] == "5"
            assert schedule_map["design_creator"] == "4"
            assert schedule_map["seo_optimizer"] == "4"


class TestStopScheduler:
    def test_stop_when_running(self):
        with patch("app.agents.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.running = True
            from app.agents.scheduler import stop_scheduler
            stop_scheduler()
            mock_scheduler.shutdown.assert_called_once_with(wait=False)

    def test_stop_when_not_running(self):
        with patch("app.agents.scheduler.scheduler") as mock_scheduler:
            mock_scheduler.running = False
            from app.agents.scheduler import stop_scheduler
            stop_scheduler()
            mock_scheduler.shutdown.assert_not_called()
