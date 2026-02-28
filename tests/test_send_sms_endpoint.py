import app as app_module


def test_send_sms_requires_body(monkeypatch):
    monkeypatch.setattr(
        app_module.id_token,
        "verify_oauth2_token",
        lambda token, req, audience: {
            "iss": "https://accounts.google.com",
            "email": "tasks-caller@example.iam.gserviceaccount.com",
            "email_verified": True,
        },
    )
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")
    monkeypatch.setattr(app_module, "TWILIO_ALLOWED_FROM", "+15555550100")

    client = app_module.app.test_client()
    response = client.post(
        "/send-sms",
        json={"to": "+15555550100"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 400


def test_send_sms_happy_path(monkeypatch):
    monkeypatch.setattr(
        app_module.id_token,
        "verify_oauth2_token",
        lambda token, req, audience: {
            "iss": "https://accounts.google.com",
            "email": "tasks-caller@example.iam.gserviceaccount.com",
            "email_verified": True,
        },
    )
    monkeypatch.setattr(app_module, "TASKS_CALLER_SERVICE_ACCOUNT", "tasks-caller@example.iam.gserviceaccount.com")
    monkeypatch.setattr(app_module, "TWILIO_ALLOWED_FROM", "+15555550100")
    monkeypatch.setattr(
        app_module,
        "_send_sms_via_twilio",
        lambda to_number, body: {"sid": "SM999", "status": "queued", "to": to_number},
    )

    client = app_module.app.test_client()
    response = client.post(
        "/send-sms",
        json={"to": "+15555550100", "body": "Reminder from agent"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["sms"]["sid"] == "SM999"
