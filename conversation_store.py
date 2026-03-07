import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from reminder_scheduler import ReminderTaskScheduler

ALLOWED_EVENT_TYPES = {"full day", "partial day", "reminder"}
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def conversation_id_for_phone(phone_number: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]", "_", phone_number or "unknown")
    return f"phone_{normalized}"


class ConversationStore:
    def __init__(
        self,
        db: firestore.Client,
        conversations_collection: str = "agent_conversations",
        events_collection: str = "agent_events",
        reminder_scheduler: Optional[ReminderTaskScheduler] = None,
    ) -> None:
        self.db = db
        self.conversations_collection = conversations_collection
        self.events_collection = events_collection
        self.reminder_scheduler = reminder_scheduler

    def _conversation_ref(self, conversation_id: str) -> firestore.DocumentReference:
        return self.db.collection(self.conversations_collection).document(conversation_id)

    def _next_sequence(self, conversation_id: str) -> int:
        convo_ref = self._conversation_ref(conversation_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: firestore.Transaction) -> int:
            snap = convo_ref.get(transaction=txn)
            if snap.exists:
                current = int(snap.to_dict().get("last_seq", 0))
                created_at = snap.to_dict().get("created_at", utc_now())
            else:
                current = 0
                created_at = utc_now()

            next_seq = current + 1
            txn.set(
                convo_ref,
                {
                    "last_seq": next_seq,
                    "created_at": created_at,
                    "updated_at": utc_now(),
                },
                merge=True,
            )
            return next_seq

        return _txn(transaction)

    def append_message_event(
        self,
        conversation_id: str,
        role: str,
        content: str,
        phone_number: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        seq = self._next_sequence(conversation_id)
        convo_ref = self._conversation_ref(conversation_id)
        payload: Dict[str, Any] = {
            "seq": seq,
            "role": role,
            "content": content,
            "phone_number": phone_number,
            "source": source,
            "created_at": utc_now(),
            "metadata": metadata or {},
        }
        convo_ref.collection("messages").document(f"{seq:012d}").set(payload)
        return payload

    def load_conversation_context(
        self,
        conversation_id: str,
        history_limit: int = 30,
        for_model_seed: bool = True,
    ) -> Dict[str, Any]:
        """Load conversation context. When for_model_seed=True, exclude trigger/audit rows from messages."""
        convo_ref = self._conversation_ref(conversation_id)
        convo_snap = convo_ref.get()
        convo_data = convo_snap.to_dict() if convo_snap.exists else {}

        # Fetch extra rows so we have enough after filtering
        fetch_limit = history_limit * 3 if for_model_seed else history_limit
        query = (
            convo_ref.collection("messages")
            .order_by("seq", direction=firestore.Query.DESCENDING)
            .limit(fetch_limit)
        )
        rows = [doc.to_dict() for doc in query.stream()]
        rows.reverse()

        if for_model_seed:
            # Exclude synthetic reminder triggers and optionally reminder-delivery replies
            rows = [
                r
                for r in rows
                if r.get("source") != "trigger"
                and r.get("metadata", {}).get("kind") not in ("reminder_trigger", "reminder_delivery_reply")
            ]
            rows = rows[-history_limit:]

        return {
            "conversation": convo_data,
            "messages": rows,
            "key_facts": convo_data.get("key_facts", {}),
        }

    def save_agent_response(
        self, conversation_id: str, phone_number: str, content: str, source: str = "agent"
    ) -> Dict[str, Any]:
        return self.append_message_event(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            phone_number=phone_number,
            source=source,
        )

    def append_debug_step(
        self,
        conversation_id: str,
        phone_number: str,
        step_type: str,
        flow: str,
        request_id: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
    ) -> Dict[str, Any]:
        """Write a structured debug timeline row for a conversation."""
        created_at = utc_now()
        row: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "phone_number": phone_number,
            "step_type": step_type,
            "flow": flow,
            "request_id": request_id,
            "event_id": event_id,
            "created_at": created_at,
            "payload": payload or {},
        }
        self._conversation_ref(conversation_id).collection("debug_steps").document().set(row)
        return row

    def list_debug_timeline(self, conversation_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        query = (
            self._conversation_ref(conversation_id)
            .collection("debug_steps")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        rows: List[Dict[str, Any]] = []
        for doc in query.stream():
            row = doc.to_dict()
            row["id"] = doc.id
            rows.append(row)
        return rows

    def list_messages(self, conversation_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        query = (
            self._conversation_ref(conversation_id)
            .collection("messages")
            .order_by("seq", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        rows: List[Dict[str, Any]] = []
        for doc in query.stream():
            row = doc.to_dict()
            row["id"] = doc.id
            rows.append(row)
        rows.reverse()
        return rows

    def update_conversation_state(
        self,
        conversation_id: str,
        key_facts: Optional[Dict[str, Any]] = None,
    ) -> None:
        updates: Dict[str, Any] = {"updated_at": utc_now()}
        if key_facts is not None:
            updates["key_facts"] = key_facts
        self._conversation_ref(conversation_id).set(updates, merge=True)

    def was_reminder_delivered(self, event_id: str) -> bool:
        """Return True if this reminder event has already been delivered (idempotency)."""
        if not event_id:
            return False
        doc = self.db.collection(self.events_collection).document(event_id).get()
        if not doc.exists:
            return False
        return doc.to_dict().get("delivery_completed_at") is not None

    def mark_reminder_delivered(self, event_id: str) -> None:
        """Mark a reminder event as delivered so retries are idempotent."""
        if not event_id:
            return
        self.db.collection(self.events_collection).document(event_id).set(
            {"delivery_completed_at": utc_now()}, merge=True
        )

    def create_event(
        self,
        event_type: str,
        title: str,
        phone_number: str,
        conversation_id: str,
        due_at: Optional[str] = None,
        details: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"event_type must be one of: {sorted(ALLOWED_EVENT_TYPES)}")

        payload: Dict[str, Any] = {
            "type": event_type,
            "title": title,
            "phone_number": phone_number,
            "conversation_id": conversation_id,
            "due_at": due_at,
            "details": details or "",
            "metadata": metadata or {},
            "created_at": utc_now(),
        }
        doc_ref = self.db.collection(self.events_collection).document()
        doc_ref.set(payload)
        payload["id"] = doc_ref.id

        if event_type == "reminder":
            if not due_at:
                raise ValueError("reminder events require due_at in ISO-8601 UTC format")
            if self.reminder_scheduler is None:
                logger.error(
                    "Reminder scheduler is not configured for conversation_id=%s phone_number=%s",
                    conversation_id,
                    phone_number,
                )
                raise RuntimeError("Reminder scheduler is not configured")

            try:
                task_name = self.reminder_scheduler.schedule_reminder(payload)
                schedule_updates = {
                    "task_name": task_name,
                    "schedule_status": "scheduled",
                    "scheduled_at": utc_now(),
                }
                doc_ref.set(schedule_updates, merge=True)
                payload.update(schedule_updates)
            except Exception as exc:
                logger.exception(
                    "Failed to schedule reminder event_id=%s conversation_id=%s phone_number=%s due_at=%s",
                    doc_ref.id,
                    conversation_id,
                    phone_number,
                    due_at,
                )
                doc_ref.set(
                    {
                        "schedule_status": "failed",
                        "schedule_error": str(exc),
                    },
                    merge=True,
                )
                raise
        return payload

    def list_reminders(self, limit: int = 25, phone_number: str = "") -> List[Dict[str, Any]]:
        query = self.db.collection(self.events_collection).where("type", "==", "reminder")
        if phone_number:
            query = query.where("phone_number", "==", phone_number)
        query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
        reminders: List[Dict[str, Any]] = []
        for doc in query.stream():
            row = doc.to_dict()
            row["id"] = doc.id
            reminders.append(row)
        return reminders
