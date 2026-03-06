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


def test_prompt_with_runtime_context_includes_current_datetime(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime.new_york_now_iso",
        lambda: "2026-03-06T15:15:00-05:00",
    )

    prompt = AthenaAgentRuntime._prompt_with_runtime_context("Remind me in 1 hour")

    assert "Current datetime (America/New_York): 2026-03-06T15:15:00-05:00" in prompt
    assert "Use this timestamp as the reference for any relative time requests." in prompt
    assert "User message: Remind me in 1 hour" in prompt
