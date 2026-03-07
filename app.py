import logging
import os
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Flask, Response, abort, jsonify, render_template, request
from google.auth.transport import requests as google_auth_requests
from google.cloud import firestore
from google.oauth2 import id_token
from twilio.rest import Client
from twilio.request_validator import RequestValidator

from agent_runtime import AthenaAgentRuntime
from conversation_store import ConversationStore, conversation_id_for_phone
from reminder_scheduler import ReminderTaskScheduler

logger = logging.getLogger(__name__)
app = Flask(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_ALLOWED_FROM = os.getenv("TWILIO_ALLOWED_FROM", "+15555550100")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")
CONVERSATIONS_COLLECTION = os.getenv("CONVERSATIONS_COLLECTION", "agent_conversations")
EVENTS_COLLECTION = os.getenv("EVENTS_COLLECTION", "agent_events")
INTERNAL_HOOK_AUDIENCE = os.getenv("INTERNAL_HOOK_AUDIENCE", "")
TASKS_CALLER_SERVICE_ACCOUNT = os.getenv("TASKS_CALLER_SERVICE_ACCOUNT", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TASKS_QUEUE_ID = os.getenv("TASKS_QUEUE_ID", "athena-reminders")
TASKS_LOCATION = os.getenv("TASKS_LOCATION", os.getenv("GCP_REGION", "us-central1"))

def _create_firestore_client() -> Any:
    # Local tests may run without ADC configured, so defer hard failures.
    try:
        return firestore.Client(project=PROJECT_ID) if PROJECT_ID else firestore.Client()
    except Exception:
        return None


db = _create_firestore_client()
reminder_scheduler = None
if (
    PROJECT_ID
    and INTERNAL_HOOK_AUDIENCE
    and TASKS_CALLER_SERVICE_ACCOUNT
    and TASKS_QUEUE_ID
    and TASKS_LOCATION
):
    reminder_scheduler = ReminderTaskScheduler(
        project_id=PROJECT_ID,
        location=TASKS_LOCATION,
        queue_id=TASKS_QUEUE_ID,
        target_url=INTERNAL_HOOK_AUDIENCE,
        service_account_email=TASKS_CALLER_SERVICE_ACCOUNT,
    )

store = (
    ConversationStore(
        db=db,
        conversations_collection=CONVERSATIONS_COLLECTION,
        events_collection=EVENTS_COLLECTION,
        reminder_scheduler=reminder_scheduler,
    )
    if db is not None
    else None
)
agent_runtime = AthenaAgentRuntime(store=store) if store is not None else None


def _twiml_message(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{escaped}</Message>
</Response>"""


def _serialize_datetimes(payload: Dict[str, Any]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, value in payload.items():
        output[key] = _serialize_value(value)
    return output


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def _shorten(text: str, max_len: int = 300) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _append_debug_step(
    conversation_id: str,
    phone_number: str,
    step_type: str,
    flow: str,
    request_id: str,
    payload: Dict[str, Any] | None = None,
    event_id: str = "",
) -> None:
    if store is None or not hasattr(store, "append_debug_step"):
        return
    try:
        store.append_debug_step(
            conversation_id=conversation_id,
            phone_number=phone_number,
            step_type=step_type,
            flow=flow,
            request_id=request_id,
            payload=payload or {},
            event_id=event_id,
        )
    except Exception:
        logger.exception(
            "Failed to append debug step conversation_id=%s request_id=%s step_type=%s",
            conversation_id,
            request_id,
            step_type,
        )


def _safe_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _message_request_id(row: Dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        request_id = metadata.get("request_id")
        return str(request_id) if request_id is not None else ""
    return ""


def _debugger_reminders(current_store: ConversationStore, limit: int, phone_number: str) -> list[dict]:
    """Avoid requiring a new Firestore index by filtering phone_number in application code."""
    fetch_limit = min(500, max(limit * 3, limit))
    reminders = current_store.list_reminders(limit=fetch_limit)
    filtered = [row for row in reminders if row.get("phone_number") == phone_number]
    return filtered[:limit]


def _assert_allowed_sender(phone_number: str) -> None:
    if phone_number != TWILIO_ALLOWED_FROM:
        abort(403, description="Sender is not allowed")


def _send_sms_via_twilio(to_number: str, body: str) -> Dict[str, Any]:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        abort(500, description="Twilio credentials are not configured")

    if not TWILIO_FROM_NUMBER and not TWILIO_MESSAGING_SERVICE_SID:
        abort(
            500,
            description="Set TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID for outbound SMS",
        )

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    create_kwargs: Dict[str, Any] = {"to": to_number, "body": body}
    if TWILIO_MESSAGING_SERVICE_SID:
        create_kwargs["messaging_service_sid"] = TWILIO_MESSAGING_SERVICE_SID
    else:
        create_kwargs["from_"] = TWILIO_FROM_NUMBER

    message = client.messages.create(**create_kwargs)
    return {
        "sid": message.sid,
        "status": message.status,
        "to": message.to,
    }


def _verify_internal_hook_identity() -> None:
    if not TASKS_CALLER_SERVICE_ACCOUNT:
        abort(500, description="TASKS_CALLER_SERVICE_ACCOUNT is not configured")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        abort(403, description="Missing bearer token")

    token = auth_header.split(" ", 1)[1].strip()
    audience = INTERNAL_HOOK_AUDIENCE or request.base_url

    try:
        claims = id_token.verify_oauth2_token(token, google_auth_requests.Request(), audience)
    except Exception:
        abort(403, description="Invalid identity token")

    issuer = claims.get("iss", "")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        abort(403, description="Invalid token issuer")

    if claims.get("email_verified") is not True:
        abort(403, description="Service account email is not verified")

    caller_email = claims.get("email", "")
    if caller_email != TASKS_CALLER_SERVICE_ACCOUNT:
        abort(403, description="Caller service account is not allowed")

def _require_store() -> ConversationStore:
    if store is None:
        abort(500, description="Firestore client is not configured")
    return store


def _require_agent_runtime() -> AthenaAgentRuntime:
    if agent_runtime is None:
        abort(500, description="Agent runtime is not configured")
    return agent_runtime


# Rate limit: 1 request per second per IP for /events/reminders
_reminders_rate_limit: Dict[str, float] = defaultdict(float)
_reminders_rate_limit_lock = threading.Lock()


def _check_reminders_rate_limit() -> None:
    client_ip = request.remote_addr or "unknown"
    now = datetime.now(timezone.utc).timestamp()
    with _reminders_rate_limit_lock:
        last = _reminders_rate_limit[client_ip]
        if now - last < 1.0:
            abort(429, description="Rate limit exceeded: 1 request per second")
        _reminders_rate_limit[client_ip] = now


def _twilio_webhook_url() -> str:
    """Build the URL Twilio used for signature validation (proxy-aware)."""
    proto = request.headers.get("X-Forwarded-Proto")
    if proto:
        scheme = proto.split(",")[0].strip().lower() or "https"
    else:
        scheme = request.scheme
    host = request.headers.get("X-Forwarded-Host") or request.host
    path = request.full_path.rstrip("?") if request.full_path else request.path
    return f"{scheme}://{host}{path}"


def _build_reminder_prompt(event_type: str, title: str, due_at: str, details: str) -> str:
    details_text = details.strip() or "(none)"
    due_at_text = due_at.strip() or "(unspecified)"
    return (
        "A scheduled reminder is firing right now.\n"
        "Send the user a short SMS reminder about it.\n"
        "Do not create or modify any events unless the user explicitly asked to reschedule.\n"
        f"Event type: {event_type}\n"
        f"Title: {title}\n"
        f"Due at: {due_at_text}\n"
        f"Details: {details_text}"
    )


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.get("/")
def index() -> Any:
    return render_template("index.html")


@app.get("/debugger")
def debugger_page() -> Any:
    return render_template("debugger.html")


@app.get("/privacy-policy")
def privacy_policy() -> Any:
    return render_template("privacy_policy.html")


@app.get("/terms-and-conditions")
def terms_and_conditions() -> Any:
    return render_template("terms_and_conditions.html")


@app.get("/events/reminders")
def list_reminders() -> Any:
    _check_reminders_rate_limit()
    reminders = _require_store().list_reminders(limit=25)
    return jsonify({"items": [_serialize_datetimes(item) for item in reminders]})


@app.get("/events/debugger")
def debugger_events() -> Any:
    limit_raw = request.args.get("limit", "200")
    try:
        limit = max(10, min(500, int(limit_raw)))
    except ValueError:
        abort(400, description="limit must be an integer")

    phone_number = request.args.get("phone_number", TWILIO_ALLOWED_FROM)
    _assert_allowed_sender(phone_number)
    conversation_id = conversation_id_for_phone(phone_number)
    current_store = _require_store()

    messages = []
    if hasattr(current_store, "list_messages"):
        messages = current_store.list_messages(conversation_id=conversation_id, limit=limit)
    debug_steps = []
    if hasattr(current_store, "list_debug_timeline"):
        debug_steps = current_store.list_debug_timeline(conversation_id=conversation_id, limit=limit)
    reminders = _debugger_reminders(current_store=current_store, limit=limit, phone_number=phone_number)

    timeline = []
    for row in messages:
        timeline.append(
            {
                "kind": "message",
                "created_at": row.get("created_at"),
                "request_id": _message_request_id(row),
                "data": row,
            }
        )
    for row in reminders:
        timeline.append(
            {
                "kind": "reminder_event",
                "created_at": row.get("created_at"),
                "request_id": "",
                "data": row,
            }
        )
    for row in debug_steps:
        timeline.append(
            {
                "kind": "debug_step",
                "created_at": row.get("created_at"),
                "request_id": row.get("request_id", ""),
                "data": row,
            }
        )

    timeline.sort(key=lambda item: _safe_timestamp(item.get("created_at")), reverse=True)
    payload = {
        "phone_number": phone_number,
        "conversation_id": conversation_id,
        "messages": [_serialize_datetimes(row) for row in messages],
        "reminders": [_serialize_datetimes(row) for row in reminders],
        "debug_steps": [_serialize_datetimes(row) for row in debug_steps],
        "timeline": [_serialize_datetimes(row) for row in timeline],
    }
    return jsonify(payload)


@app.post("/internal/events/agent-hook")
def event_hook() -> Any:
    _verify_internal_hook_identity()
    request_id = str(uuid.uuid4())

    payload = request.get_json(silent=True) or {}
    phone_number = payload.get("phone_number", TWILIO_ALLOWED_FROM)
    _assert_allowed_sender(phone_number)

    event_id = payload.get("event_id", "")
    event_type = payload.get("type", "reminder")
    title = payload.get("title", "Scheduled reminder")
    details = payload.get("details", "")
    due_at = payload.get("due_at", "")

    conversation_id = conversation_id_for_phone(phone_number)
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="trigger_received",
        flow="reminder_hook",
        request_id=request_id,
        payload={
            "event_id": event_id,
            "event_type": event_type,
            "title": title,
            "details": details,
            "due_at": due_at,
            "raw_payload": payload,
        },
        event_id=event_id,
    )

    if event_id and _require_store().was_reminder_delivered(event_id):
        logger.info(
            "Reminder delivery idempotent skip event_id=%s conversation_id=%s title=%s",
            event_id,
            conversation_id,
            title,
        )
        _append_debug_step(
            conversation_id=conversation_id,
            phone_number=phone_number,
            step_type="reminder_duplicate_skip",
            flow="reminder_hook",
            request_id=request_id,
            payload={"event_id": event_id, "title": title},
            event_id=event_id,
        )
        return jsonify({"ok": True, "reply_text": "(already delivered)", "sms": None, "duplicate": True})

    trigger_message = (
        f"Event trigger received. type={event_type}, title={title}, due_at={due_at}, details={details}"
    )
    reminder_prompt = _build_reminder_prompt(
        event_type=event_type,
        title=title,
        due_at=due_at,
        details=details,
    )
    _require_store().append_message_event(
        conversation_id=conversation_id,
        role="user",
        content=trigger_message,
        phone_number=phone_number,
        source="trigger",
        metadata=(
            {"kind": "reminder_trigger", "event_id": event_id, "request_id": request_id}
            if event_id
            else {"kind": "reminder_trigger", "request_id": request_id}
        ),
    )
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="trigger_saved",
        flow="reminder_hook",
        request_id=request_id,
        payload={"trigger_message": trigger_message},
        event_id=event_id,
    )
    context = _require_store().load_conversation_context(conversation_id)
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="context_loaded",
        flow="reminder_hook",
        request_id=request_id,
        payload={"context": _serialize_value(context)},
        event_id=event_id,
    )
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="llm_call_started",
        flow="reminder_hook",
        request_id=request_id,
        payload={"user_text": reminder_prompt},
        event_id=event_id,
    )

    try:
        result = _require_agent_runtime().run_agent_turn(
            conversation_id=conversation_id,
            phone_number=phone_number,
            user_text=reminder_prompt,
            context=context,
        )
    except Exception as exc:
        _append_debug_step(
            conversation_id=conversation_id,
            phone_number=phone_number,
            step_type="error",
            flow="reminder_hook",
            request_id=request_id,
            payload={"stage": "run_agent_turn", "error": str(exc)},
            event_id=event_id,
        )
        logger.exception(
            "Agent turn failed event_id=%s conversation_id=%s type=%s title=%s due_at=%s",
            event_id,
            conversation_id,
            event_type,
            title,
            due_at,
        )
        raise

    assistant_text = result["reply_text"]
    trace = result.get("trace", [])
    runtime_debug = result.get("debug", {})
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="llm_call_finished",
        flow="reminder_hook",
        request_id=request_id,
        payload={
            "assistant_text": assistant_text,
            "trace": trace,
            "runtime_debug": runtime_debug,
        },
        event_id=event_id,
    )

    _require_store().save_agent_response(
        conversation_id=conversation_id,
        phone_number=phone_number,
        content=assistant_text,
        source="assistant",
    )
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="assistant_saved",
        flow="reminder_hook",
        request_id=request_id,
        payload={"assistant_preview": _shorten(assistant_text)},
        event_id=event_id,
    )

    try:
        _append_debug_step(
            conversation_id=conversation_id,
            phone_number=phone_number,
            step_type="twilio_send_attempt",
            flow="reminder_hook",
            request_id=request_id,
            payload={"to_number": phone_number, "assistant_preview": _shorten(assistant_text)},
            event_id=event_id,
        )
        send_result = _send_sms_via_twilio(to_number=phone_number, body=assistant_text)
    except Exception as exc:
        _append_debug_step(
            conversation_id=conversation_id,
            phone_number=phone_number,
            step_type="error",
            flow="reminder_hook",
            request_id=request_id,
            payload={"stage": "twilio_send", "error": str(exc)},
            event_id=event_id,
        )
        logger.exception(
            "SMS send failed event_id=%s conversation_id=%s assistant_text=%s trace_len=%d",
            event_id,
            conversation_id,
            assistant_text[:100] if assistant_text else "",
            len(trace),
        )
        raise
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="twilio_send_result",
        flow="reminder_hook",
        request_id=request_id,
        payload={"send_result": send_result},
        event_id=event_id,
    )

    if event_id:
        _require_store().mark_reminder_delivered(event_id)
        _append_debug_step(
            conversation_id=conversation_id,
            phone_number=phone_number,
            step_type="reminder_marked_delivered",
            flow="reminder_hook",
            request_id=request_id,
            payload={"event_id": event_id},
            event_id=event_id,
        )

    return jsonify({"ok": True, "reply_text": assistant_text, "sms": send_result})


@app.post("/send-sms")
def send_sms() -> Any:
    _verify_internal_hook_identity()
    payload = request.get_json(silent=True) or {}
    body = (payload.get("body") or "").strip()
    if not body:
        abort(400, description="body is required")

    to_number = payload.get("to", TWILIO_ALLOWED_FROM)
    _assert_allowed_sender(to_number)
    send_result = _send_sms_via_twilio(to_number=to_number, body=body)
    return jsonify({"ok": True, "sms": send_result})


@app.post("/receive-sms")
def receive_sms() -> Response:
    if not TWILIO_AUTH_TOKEN:
        abort(500, description="TWILIO_AUTH_TOKEN is not configured")

    request_id = str(uuid.uuid4())
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    webhook_url = _twilio_webhook_url()
    is_valid = validator.validate(webhook_url, request.form.to_dict(flat=True), signature)
    if not is_valid:
        abort(403, description="Invalid Twilio signature")

    phone_number = request.form.get("From", "")
    _assert_allowed_sender(phone_number)
    incoming_text = request.form.get("Body", "").strip() or "(empty message)"

    conversation_id = conversation_id_for_phone(phone_number)
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="incoming_sms",
        flow="sms",
        request_id=request_id,
        payload={"body": incoming_text},
    )
    _require_store().append_message_event(
        conversation_id=conversation_id,
        role="user",
        content=incoming_text,
        phone_number=phone_number,
        source="sms",
        metadata={"request_id": request_id},
    )
    context = _require_store().load_conversation_context(conversation_id)
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="context_loaded",
        flow="sms",
        request_id=request_id,
        payload={"context": _serialize_value(context)},
    )
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="llm_call_started",
        flow="sms",
        request_id=request_id,
        payload={"user_text": incoming_text},
    )

    result = _require_agent_runtime().run_agent_turn(
        conversation_id=conversation_id,
        phone_number=phone_number,
        user_text=incoming_text,
        context=context,
    )
    assistant_text = result["reply_text"]
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="llm_call_finished",
        flow="sms",
        request_id=request_id,
        payload={
            "assistant_text": assistant_text,
            "trace": result.get("trace", []),
            "runtime_debug": result.get("debug", {}),
        },
    )
    _require_store().save_agent_response(
        conversation_id=conversation_id,
        phone_number=phone_number,
        content=assistant_text,
        source="agent",
    )
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="assistant_saved",
        flow="sms",
        request_id=request_id,
        payload={"assistant_preview": _shorten(assistant_text)},
    )

    rolling_summary = (
        f"{context.get('rolling_summary', '')}\n"
        f"User: {incoming_text}\n"
        f"Assistant: {assistant_text}"
    ).strip()[-2000:]
    _require_store().update_conversation_state(
        conversation_id=conversation_id, rolling_summary=rolling_summary
    )
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="conversation_state_updated",
        flow="sms",
        request_id=request_id,
        payload={"rolling_summary_preview": _shorten(rolling_summary)},
    )

    # Twilio reads TwiML from the webhook response and sends it back to the sender.
    twiml = _twiml_message(assistant_text)
    _append_debug_step(
        conversation_id=conversation_id,
        phone_number=phone_number,
        step_type="twiml_response_generated",
        flow="sms",
        request_id=request_id,
        payload={"assistant_preview": _shorten(assistant_text)},
    )
    return Response(twiml, status=200, mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
