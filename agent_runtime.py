import asyncio
import os
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from conversation_store import ConversationStore

CURRENT_CONVERSATION_ID: ContextVar[str] = ContextVar("conversation_id", default="")
CURRENT_PHONE_NUMBER: ContextVar[str] = ContextVar("phone_number", default="")
NEW_YORK_TZ = ZoneInfo("America/New_York")


def new_york_now_iso() -> str:
    return datetime.now(NEW_YORK_TZ).replace(microsecond=0).isoformat()


class AthenaAgentRuntime:
    def __init__(self, store: ConversationStore) -> None:
        self.store = store
        self.app_name = "athena_adk"
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._root_agent = None
        self._runner = None
        self._session_service = None

    def _create_event_tool(
        self,
        event_type: str,
        title: str,
        due_at: str = "",
        details: str = "",
    ) -> Dict[str, Any]:
        """Creates an event. event_type must be: full day, partial day, or reminder."""
        conversation_id = CURRENT_CONVERSATION_ID.get()
        phone_number = CURRENT_PHONE_NUMBER.get()
        if not conversation_id or not phone_number:
            return {"status": "error", "message": "Conversation context not available for event creation."}

        try:
            event = self.store.create_event(
                event_type=event_type,
                title=title,
                due_at=due_at or None,
                details=details,
                conversation_id=conversation_id,
                phone_number=phone_number,
            )
            return {"status": "ok", "event": event}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def _build_google_search_tool() -> Optional[Any]:
        try:
            from google.adk.tools.google_search_tool import GoogleSearchTool

            return GoogleSearchTool(bypass_multi_tools_limit=True)
        except ImportError:
            return None

    def _build_root_agent(self) -> Any:
        from google.adk.agents.llm_agent import Agent

        tools = [self._create_event_tool]
        google_search_tool = self._build_google_search_tool()
        if google_search_tool is not None:
            tools.append(google_search_tool)

        instruction = (
            "You are Athena, an SMS planning assistant. "
            "You help the user with scheduling and reminders. "
            "You can use Google Search when the user asks for current or web-based info. "
            "When asked to save a task/event, call create_event_tool. "
            "Event types allowed: full day, partial day, reminder. "
            "For reminders, always include due_at in ISO-8601 UTC format "
            "(example: 2026-03-01T15:30:00Z). "
            "The current datetime will be included in each user turn in America/New_York time; "
            "use New York local time, including daylight saving time, as the default reference timezone "
            "to resolve relative times like 'in 1 hour' or 'tomorrow morning'. "
            "Keep SMS responses very concise, chill, and casual, like a young dude texting. "
            "Usually reply in 1 short sentence, or 2 short sentences max. "
            "No corporate tone, no fluff, no long explanations."
        )
        return Agent(
            model=self.model_name,
            name="athena_sms_agent",
            description="SMS scheduler assistant for one user.",
            instruction=instruction,
            tools=tools,
        )

    def _ensure_runner(self) -> None:
        if self._runner is not None:
            return
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self._root_agent = self._build_root_agent()
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=self._root_agent,
            app_name=self.app_name,
            session_service=self._session_service,
        )

    @staticmethod
    def _history_messages_for_seed(
        context: Dict[str, Any], user_text: str
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = list(context.get("messages", []))
        if (
            messages
            and messages[-1].get("role") == "user"
            and messages[-1].get("content", "").strip() == user_text.strip()
        ):
            messages = messages[:-1]
        return messages[-20:]

    async def _seed_session_history(
        self, session: Any, context: Dict[str, Any], user_text: str
    ) -> None:
        from google.adk.events import Event
        from google.genai import types

        rolling_summary = context.get("rolling_summary", "").strip()
        if rolling_summary:
            summary_event = Event(
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part(text=f"Conversation summary: {rolling_summary}")],
                ),
            )
            self._session_service.append_event(session=session, event=summary_event)

        history = self._history_messages_for_seed(context=context, user_text=user_text)
        role_to_author = {
            "user": ("user", "user"),
            "assistant": ("model", "model"),
            "system": ("user", "user"),
        }
        for row in history:
            text = row.get("content", "").strip()
            if not text:
                continue
            role = row.get("role", "user")
            author, content_role = role_to_author.get(role, ("user", "user"))
            event = Event(
                author=author,
                content=types.Content(role=content_role, parts=[types.Part(text=text)]),
            )
            self._session_service.append_event(session=session, event=event)

    @staticmethod
    def _prompt_with_runtime_context(user_text: str) -> str:
        return (
            f"Current datetime (America/New_York): {new_york_now_iso()}\n"
            "Use this timestamp as the reference for any relative time requests.\n"
            f"User message: {user_text}"
        )

    async def _run_async(self, conversation_id: str, user_text: str, context: Dict[str, Any]) -> str:
        from google.genai import types

        self._ensure_runner()
        session_id = f"{conversation_id}-{int(time.time() * 1000)}"
        session = await self._session_service.create_session(
            app_name=self.app_name,
            user_id=conversation_id,
            session_id=session_id,
        )
        await self._seed_session_history(session=session, context=context, user_text=user_text)

        prompt_user_text = self._prompt_with_runtime_context(user_text)
        new_message = types.Content(role="user", parts=[types.Part(text=prompt_user_text)])
        response_chunks: List[str] = []
        async for event in self._runner.run_async(
            user_id=conversation_id,
            session_id=session_id,
            new_message=new_message,
        ):
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if not parts:
                continue
            chunk = "".join(part.text for part in parts if getattr(part, "text", ""))
            if chunk:
                response_chunks.append(chunk)
        if not response_chunks:
            return "I could not generate a response right now. Please try again."
        return response_chunks[-1]

    def run_agent_turn(
        self,
        conversation_id: str,
        phone_number: str,
        user_text: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        conversation_token = CURRENT_CONVERSATION_ID.set(conversation_id)
        phone_token = CURRENT_PHONE_NUMBER.set(phone_number)
        try:
            reply = asyncio.run(self._run_async(conversation_id, user_text, context))
            return {"reply_text": reply}
        finally:
            CURRENT_CONVERSATION_ID.reset(conversation_token)
            CURRENT_PHONE_NUMBER.reset(phone_token)
