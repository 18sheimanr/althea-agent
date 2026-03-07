import app as app_module


class FakeStore:
    def __init__(self, delivered_event_ids=None):
        self.delivered_event_ids = set(delivered_event_ids or [])
        self.debug_steps = []

    def append_message_event(self, **kwargs):
        return kwargs

    def load_conversation_context(self, conversation_id, history_limit=12):
        return {"rolling_summary": "prior context", "messages": []}

    def save_agent_response(self, **kwargs):
        return kwargs

    def was_reminder_delivered(self, event_id):
        return event_id in self.delivered_event_ids

    def mark_reminder_delivered(self, event_id):
        self.delivered_event_ids.add(event_id)

    def append_debug_step(self, **kwargs):
        self.debug_steps.append(kwargs)
        return kwargs


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def run_agent_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {"reply_text": "Triggered response", "trace": [{"event_id": "e1", "part_kinds": ["text"]}]}


def _patch_sms_send(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_send_sms_via_twilio",
        lambda to_number, body: {"sid": "SM123", "status": "queued", "to": to_number},
    )


def test_event_hook_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")
    _patch_sms_send(monkeypatch)

    client = app_module.app.test_client()
    response = client.post("/internal/events/agent-hook", json={"type": "reminder"})
    assert response.status_code == 403


def test_event_hook_requires_tasks_service_account_config(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "")
    _patch_sms_send(monkeypatch)

    client = app_module.app.test_client()
    response = client.post(
        "/internal/events/agent-hook",
        json={"type": "reminder"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 500


def test_event_hook_accepts_valid_oidc_and_service_account(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    fake_runtime = FakeRuntime()
    monkeypatch.setattr(app_module, "agent_runtime", fake_runtime)
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")
    _patch_sms_send(monkeypatch)

    def _fake_verify(token, req, audience):
        return {
            "iss": "https://accounts.google.com",
            "email": "tasks-caller@example.iam.gserviceaccount.com",
            "email_verified": True,
        }

    monkeypatch.setattr(app_module.id_token, "verify_oauth2_token", _fake_verify)

    client = app_module.app.test_client()
    response = client.post(
        "/internal/events/agent-hook",
        json={"type": "reminder", "title": "Take medicine"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["sms"]["sid"] == "SM123"
    assert len(fake_runtime.calls) == 1
    assert "A scheduled reminder is firing right now." in fake_runtime.calls[0]["user_text"]
    assert "Title: Take medicine" in fake_runtime.calls[0]["user_text"]
    assert any(step.get("step_type") == "twilio_send_result" for step in app_module.store.debug_steps)


def test_event_hook_idempotent_skip_duplicate_event_id(monkeypatch):
    """Duplicate event_id delivery returns 200 without re-processing."""
    fake_store = FakeStore(delivered_event_ids=["evt-123"])
    monkeypatch.setattr(app_module, "store", fake_store)
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")
    _patch_sms_send(monkeypatch)

    def _fake_verify(token, req, audience):
        return {
            "iss": "https://accounts.google.com",
            "email": "tasks-caller@example.iam.gserviceaccount.com",
            "email_verified": True,
        }

    monkeypatch.setattr(app_module.id_token, "verify_oauth2_token", _fake_verify)

    client = app_module.app.test_client()
    response = client.post(
        "/internal/events/agent-hook",
        json={"event_id": "evt-123", "type": "reminder", "title": "Already sent"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data.get("duplicate") is True
    assert data["sms"] is None
    assert any(step.get("step_type") == "reminder_duplicate_skip" for step in fake_store.debug_steps)
