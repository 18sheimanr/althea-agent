from agent_runtime import AthenaAgentRuntime, format_datetime_simple
from datetime import datetime, timezone


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


def test_history_seed_dedupes_system_trigger_matching_user_text():
    """Defensive dedupe: last message is system/trigger with same content as user_text, should be dropped."""
    runtime = AthenaAgentRuntime(store=DummyStore())  # type: ignore[arg-type]
    trigger = "Event trigger received. type=reminder, title=go to bed, due_at=2026-03-07T06:00:00Z, details="
    context = {
        "messages": [
            {"role": "user", "content": "Remind me at 1am"},
            {"role": "assistant", "content": "Gotcha!"},
            {"role": "system", "content": trigger, "source": "trigger"},
        ]
    }
    history = runtime._history_messages_for_seed(context=context, user_text=trigger)
    assert [m["content"] for m in history] == ["Remind me at 1am", "Gotcha!"]


def test_prompt_with_runtime_context_includes_current_datetime(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime.format_datetime_simple",
        lambda dt: "3/6/2026 3:15pm",
    )

    prompt = AthenaAgentRuntime._prompt_with_runtime_context("Remind me in 1 hour")

    assert "Current datetime (America/New_York): 3/6/2026 3:15pm" in prompt
    assert "Use this timestamp as the reference for any relative time requests." in prompt
    assert "User message: Remind me in 1 hour" in prompt


def test_build_google_search_tool_enables_multi_tool_bypass():
    tool = AthenaAgentRuntime._build_google_search_tool()

    assert tool is not None
    assert getattr(tool, "bypass_multi_tools_limit", False) is True


def test_format_datetime_simple_est():
    # March 7, 2026 is EST
    dt = datetime(2026, 3, 7, 15, 30, tzinfo=timezone.utc)
    # 15:30 UTC -> 10:30 AM EST
    formatted = format_datetime_simple(dt)
    assert "10:30am est" in formatted


def test_format_datetime_simple_edt():
    # July 7, 2026 is EDT
    dt = datetime(2026, 7, 7, 15, 30, tzinfo=timezone.utc)
    # 15:30 UTC -> 11:30 AM EDT
    formatted = format_datetime_simple(dt)
    assert "11:30am edt" in formatted
