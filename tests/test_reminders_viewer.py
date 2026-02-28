from datetime import datetime, timezone

import app as app_module


class FakeStore:
    def list_reminders(self, limit=25):
        return [
            {
                "id": "r1",
                "type": "reminder",
                "title": "Doctor appointment",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        ]


def test_reminders_viewer_endpoint(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    client = app_module.app.test_client()

    response = client.get("/events/reminders")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["items"][0]["type"] == "reminder"
    assert payload["items"][0]["created_at"].startswith("2026-01-01")
