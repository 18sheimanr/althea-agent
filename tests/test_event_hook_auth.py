import app as app_module


class FakeStore:
    def append_message_event(self, **kwargs):
        return kwargs

    def load_conversation_context(self, conversation_id, history_limit=12):
        return {"rolling_summary": "prior context", "messages": []}

    def save_agent_response(self, **kwargs):
        return kwargs


class FakeRuntime:
    def run_agent_turn(self, **kwargs):
        return {"reply_text": "Triggered response"}


def test_event_hook_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "EVENT_HOOK_TOKEN", "")
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")

    client = app_module.app.test_client()
    response = client.post("/internal/events/agent-hook", json={"type": "reminder"})
    assert response.status_code == 403


def test_event_hook_requires_tasks_service_account_config(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "EVENT_HOOK_TOKEN", "")
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "")

    client = app_module.app.test_client()
    response = client.post(
        "/internal/events/agent-hook",
        json={"type": "reminder"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 500


def test_event_hook_accepts_valid_oidc_and_service_account(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "EVENT_HOOK_TOKEN", "")
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")

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
