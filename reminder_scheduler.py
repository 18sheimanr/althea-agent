import logging
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

NEW_YORK_TZ = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)


def parse_due_at_utc(due_at: str) -> datetime:
    """Parses a string representing a wall-clock time in America/New_York (if no offset).
    Returns an aware datetime in UTC for use in scheduling.
    """
    normalized = due_at.strip()
    # Python 3.11+ handles 'Z' in fromisoformat, but for broader compatibility:
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        # datetime.fromisoformat handles many formats like:
        # 2026-03-01T15:30:00, 2026-03-01 15:30:00, 2026-03-01, etc.
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        logger.error("Failed to parse due_at string: '%s'. Error: %s", due_at, exc)
        raise ValueError(f"Invalid datetime format: {due_at}. Expected ISO-8601 like 2026-03-01T15:30:00") from exc

    # If the LLM did not provide any timezone info, assume New York time.
    # replace(tzinfo=...) handles naive-to-aware conversion correctly for clock-time interpretation.
    if parsed.tzinfo is None:
        # If the date is a simple date (YYYY-MM-DD), fromisoformat returns time at 00:00:00.
        parsed = parsed.replace(tzinfo=NEW_YORK_TZ)

    # Convert to UTC for Cloud Tasks scheduling. 
    # astimezone handles Daylight Savings transition correctly when converting to UTC.
    return parsed.astimezone(timezone.utc)


class ReminderTaskScheduler:
    def __init__(
        self,
        project_id: str,
        location: str,
        queue_id: str,
        target_url: str,
        service_account_email: str,
    ) -> None:
        self.client = tasks_v2.CloudTasksClient()
        self.parent = self.client.queue_path(project_id, location, queue_id)
        self.target_url = target_url
        self.service_account_email = service_account_email

    def enqueue_immediate_task(self, event_payload: Dict[str, Any]) -> str:
        """Enqueues a task to be processed as soon as possible (no schedule_time)."""
        import json

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self.target_url,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(event_payload).encode("utf-8"),
                "oidc_token": {
                    "service_account_email": self.service_account_email,
                    "audience": self.target_url,
                },
            },
        }
        created = self.client.create_task(parent=self.parent, task=task)
        return created.name

    def schedule_reminder(self, event_payload: Dict[str, Any]) -> str:
        due_at = event_payload.get("due_at")
        if not due_at:
            raise ValueError("reminder events require due_at")

        schedule_dt = parse_due_at_utc(due_at)
        schedule_ts = timestamp_pb2.Timestamp()
        schedule_ts.FromDatetime(schedule_dt)

        # Log for debugging instant triggers
        logger.info("Scheduling reminder: due_at=%s, parsed_utc=%s, now_utc=%s", 
                    due_at, schedule_dt.isoformat(), datetime.now(timezone.utc).isoformat())

        body = {
            "event_id": event_payload.get("id"),
            "type": event_payload.get("type", "reminder"),
            "title": event_payload.get("title", ""),
            "details": event_payload.get("details", ""),
            "due_at": event_payload.get("due_at", ""),
            "phone_number": event_payload.get("phone_number", ""),
        }

        import json

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self.target_url,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(body).encode("utf-8"),
                "oidc_token": {
                    "service_account_email": self.service_account_email,
                    "audience": self.target_url,
                },
            },
            "schedule_time": schedule_ts,
        }
        # Explicitly passing schedule_time as a separate argument can be more robust
        created = self.client.create_task(
            parent=self.parent, 
            task=task
        )
        return created.name
