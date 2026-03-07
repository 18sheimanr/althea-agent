from datetime import datetime, timezone

import app as app_module


class FakeStore:
    def list_messages(self, conversation_id, limit=200):
        return [
            {
                "id": "m1",
                "seq": 1,
                "role": "user",
                "source": "sms",
                "content": "Hello",
                "created_at": datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
                "metadata": None,
            }
        ]

    def list_debug_timeline(self, conversation_id, limit=200):
        return [
            {
                "id": "d1",
                "step_type": "context_loaded",
                "flow": "sms",
                "request_id": "req-1",
                "created_at": datetime(2026, 3, 1, 14, 0, 1, tzinfo=timezone.utc),
                "payload": {"context": {"messages": []}},
            }
        ]

    def list_reminders(self, limit=200):
        return [
            {
                "id": "r1",
                "type": "reminder",
                "title": "Hydrate",
                "phone_number": "+15555550100",
                "created_at": datetime(2026, 3, 1, 13, 59, tzinfo=timezone.utc),
            },
            {
                "id": "r2",
                "type": "reminder",
                "title": "Other user row",
                "phone_number": "+15550001111",
                "created_at": datetime(2026, 3, 1, 13, 58, tzinfo=timezone.utc),
            },
        ]


def test_debugger_page_renders():
    client = app_module.app.test_client()
    response = client.get("/debugger")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Conversation Debugger" in html
    assert "const expandedPayloadKeys = new Set();" in html
    assert "details.addEventListener(\"toggle\"" in html
    assert "if (expandedPayloadKeys.has(detailsKey))" in html
    assert "setInterval(" not in html
    assert "Auto refresh (5s)" not in html
    assert "if (item.kind === \"message\" && requestId === \"no_request_id\") continue;" in html


def test_debugger_events_payload(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "TWILIO_ALLOWED_FROM", "+15555550100")

    client = app_module.app.test_client()
    response = client.get("/events/debugger?limit=50")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["phone_number"] == "+15555550100"
    assert payload["conversation_id"].startswith("phone_")
    assert payload["messages"][0]["id"] == "m1"
    assert payload["debug_steps"][0]["id"] == "d1"
    assert payload["reminders"][0]["id"] == "r1"
    assert all(row["phone_number"] == "+15555550100" for row in payload["reminders"])
    assert len(payload["timeline"]) == 3
    assert payload["timeline"][0]["kind"] == "debug_step"
