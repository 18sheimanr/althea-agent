from datetime import datetime, timezone
from typing import Any, Dict

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2


def parse_due_at_utc(due_at: str) -> datetime:
    normalized = due_at.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("due_at must include timezone information (use UTC ISO-8601)")
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
        created = self.client.create_task(parent=self.parent, task=task)
        return created.name
