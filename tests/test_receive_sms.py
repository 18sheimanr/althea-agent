import app as app_module


class FakeStore:
    def __init__(self) -> None:
        self.messages = []
        self.updated = []
        self.debug_steps = []

    def append_message_event(self, **kwargs):
        self.messages.append(kwargs)
        return kwargs

    def load_conversation_context(self, conversation_id, history_limit=12):
        return {"rolling_summary": "prior context", "messages": []}

    def save_agent_response(self, **kwargs):
        self.messages.append(kwargs)
        return kwargs

    def update_conversation_state(self, **kwargs):
        self.updated.append(kwargs)

    def append_debug_step(self, **kwargs):
        self.debug_steps.append(kwargs)
        return kwargs


class FakeRuntime:
    def run_agent_turn(self, **kwargs):
        return {"reply_text": "Agent reply"}


def _set_valid_twilio_validator(monkeypatch, is_valid=True):
    class _Validator:
        def __init__(self, token):
            self.token = token

        def validate(self, url, form, signature):
            return is_valid

    monkeypatch.setattr(app_module, "RequestValidator", _Validator)


def test_receive_sms_valid_signature_and_sender(monkeypatch):
    fake_store = FakeStore()
    monkeypatch.setattr(app_module, "store", fake_store)
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app_module, "TWILIO_ALLOWED_FROM", "+15555550100")
    _set_valid_twilio_validator(monkeypatch, is_valid=True)

    client = app_module.app.test_client()
    response = client.post(
        "/receive-sms",
        data={"From": "+15555550100", "Body": "Hello"},
        headers={"X-Twilio-Signature": "sig"},
        base_url="https://example.com",
    )

    assert response.status_code == 200
    assert "Agent reply" in response.get_data(as_text=True)
    assert fake_store.updated
    assert fake_store.debug_steps
    assert any(step.get("step_type") == "context_loaded" for step in fake_store.debug_steps)


def test_receive_sms_invalid_signature(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "TWILIO_AUTH_TOKEN", "test-token")
    _set_valid_twilio_validator(monkeypatch, is_valid=False)

    client = app_module.app.test_client()
    response = client.post(
        "/receive-sms",
        data={"From": "+15555550100", "Body": "Hello"},
        headers={"X-Twilio-Signature": "bad"},
        base_url="https://example.com",
    )

    assert response.status_code == 403


def test_receive_sms_disallowed_sender(monkeypatch):
    monkeypatch.setattr(app_module, "store", FakeStore())
    monkeypatch.setattr(app_module, "agent_runtime", FakeRuntime())
    monkeypatch.setattr(app_module, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app_module, "TWILIO_ALLOWED_FROM", "+15555550100")
    _set_valid_twilio_validator(monkeypatch, is_valid=True)

    client = app_module.app.test_client()
    response = client.post(
        "/receive-sms",
        data={"From": "+15550000000", "Body": "Hello"},
        headers={"X-Twilio-Signature": "sig"},
        base_url="https://example.com",
    )

    assert response.status_code == 403
