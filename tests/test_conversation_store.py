from datetime import datetime, timezone

from conversation_store import ConversationStore, conversation_id_for_phone


class FakeSnapshot:
    def __init__(self, data=None):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data or {}


class FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class FakeQuery:
    def __init__(self, docs):
        self.docs = docs
        self._limit = None

    def where(self, field, op, value):
        assert op == "=="
        return FakeQuery([(doc_id, data) for doc_id, data in self.docs if data.get(field) == value])

    def order_by(self, field, direction=None):
        reverse = str(direction).lower().endswith("descending")
        return FakeQuery(sorted(self.docs, key=lambda item: item[1].get(field), reverse=reverse))

    def limit(self, value):
        self._limit = value
        return self

    def stream(self):
        selected = self.docs[: self._limit] if self._limit is not None else self.docs
        for doc_id, data in selected:
            yield FakeDoc(doc_id, data)


class FakeSubCollectionRef:
    def __init__(self, docs):
        self.docs = docs

    def document(self, doc_id):
        return FakeSettableDocRef(self.docs, doc_id)

    def order_by(self, field, direction=None):
        return FakeQuery(list(self.docs.items())).order_by(field, direction=direction)


class FakeSettableDocRef:
    def __init__(self, docs, doc_id):
        self.docs = docs
        self.doc_id = doc_id
        self.id = doc_id
        self.subcollections = {}

    def set(self, payload, merge=False):
        if merge and self.doc_id in self.docs:
            self.docs[self.doc_id].update(payload)
        else:
            self.docs[self.doc_id] = dict(payload)

    def get(self):
        return FakeSnapshot(self.docs.get(self.doc_id))

    def collection(self, name):
        if self.doc_id not in self.docs:
            self.docs[self.doc_id] = {}
        subkey = f"__sub__{name}"
        if subkey not in self.docs[self.doc_id]:
            self.docs[self.doc_id][subkey] = {}
        return FakeSubCollectionRef(self.docs[self.doc_id][subkey])


class FakeCollectionRef:
    def __init__(self, docs):
        self.docs = docs
        self._id = 0

    def document(self, doc_id=None):
        if doc_id is None:
            self._id += 1
            doc_id = f"doc-{self._id}"
        return FakeSettableDocRef(self.docs, doc_id)

    def where(self, field, op, value):
        return FakeQuery(list(self.docs.items())).where(field, op, value)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = {}
        return FakeCollectionRef(self.collections[name])


def test_conversation_id_for_phone():
    assert conversation_id_for_phone("+1 (555) 555-0100") == "phone__1__555__555_0100"


def test_create_event_validates_allowed_types():
    store = ConversationStore(FakeDB())
    event = store.create_event(
        event_type="reminder",
        title="Pay rent",
        phone_number="+15555550100",
        conversation_id="phone_1555",
    )
    assert event["type"] == "reminder"

    try:
        store.create_event(
            event_type="invalid",
            title="Nope",
            phone_number="+15555550100",
            conversation_id="phone_1555",
        )
        assert False, "Expected ValueError"
    except ValueError:
        assert True


def test_append_and_load_context_in_order(monkeypatch):
    store = ConversationStore(FakeDB())
    sequence = {"value": 0}

    def _fake_next_sequence(conversation_id):
        sequence["value"] += 1
        return sequence["value"]

    monkeypatch.setattr(store, "_next_sequence", _fake_next_sequence)
    store.append_message_event(
        conversation_id="phone_1555",
        role="user",
        content="First",
        phone_number="+15555550100",
        source="sms",
    )
    store.append_message_event(
        conversation_id="phone_1555",
        role="assistant",
        content="Second",
        phone_number="+15555550100",
        source="agent",
    )

    context = store.load_conversation_context("phone_1555", history_limit=10)
    assert [row["content"] for row in context["messages"]] == ["First", "Second"]
