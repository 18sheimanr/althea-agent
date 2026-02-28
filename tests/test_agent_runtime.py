from agent_runtime import AthenaAgentRuntime


class DummyStore:
    pass


def test_history_seed_excludes_duplicate_latest_user_message():
    runtime = AthenaAgentRuntime(store=DummyStore())  # type: ignore[arg-type]
    context = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "new text"},
        ]
    }
    history = runtime._history_messages_for_seed(context=context, user_text="new text")
    assert [m["content"] for m in history] == ["hi", "hello"]
