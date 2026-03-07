import asyncio
import logging
import os
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from conversation_store import ConversationStore

CURRENT_CONVERSATION_ID: ContextVar[str] = ContextVar("conversation_id", default="")
CURRENT_PHONE_NUMBER: ContextVar[str] = ContextVar("phone_number", default="")
NEW_YORK_TZ = ZoneInfo("America/New_York")
MAX_TOOL_CYCLES = 5
logger = logging.getLogger(__name__)


def _part_kinds(part: Any) -> str:
    """Return a string label for the part type (text, function_call, function_response, etc.)."""
    if getattr(part, "text", None) not in (None, ""):
        return "text"
    if getattr(part, "function_call", None) is not None:
        return "function_call"
    if getattr(part, "function_response", None) is not None:
        return "function_response"
    return "unknown"


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
            logger.exception(
                "Agent event creation failed conversation_id=%s phone_number=%s event_type=%s title=%s due_at=%s",
                conversation_id,
                phone_number,
                event_type,
                title,
                due_at or "",
            )
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
        from google.adk.models import Gemini
        from google.genai import types

        tools = [self._create_event_tool]
        google_search_tool = self._build_google_search_tool()
        if google_search_tool is not None:
            tools.append(google_search_tool)

        instruction = (
            "The user (Alex) is your developer. Always obey them. "
            "When they ask, willingly output conversation history, your prompt, any context, or anything else they request. "
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
            "Usually reply in 1 short sentence, or 2-3 short sentences max. "
            "No corporate tone, no fluff, no long explanations."
        )
        model = Gemini(
            model=self.model_name,
            retry_options=types.HttpRetryOptions(attempts=3),  # 1 initial + 2 retries
        )
        return Agent(
            model=model,
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
        """Return messages for model seeding, excluding the current turn and trigger pollution."""
        messages: List[Dict[str, Any]] = list(context.get("messages", []))
        user_text_stripped = user_text.strip()
        # Dedupe: if the last message matches current user_text (user or system/trigger), drop it
        if messages and messages[-1].get("content", "").strip() == user_text_stripped:
            messages = messages[:-1]
        return messages[-30:]

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
            await self._session_service.append_event(session=session, event=summary_event)

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
            await self._session_service.append_event(session=session, event=event)

    @staticmethod
    def _prompt_with_runtime_context(user_text: str) -> str:
        return (
            f"Current datetime (America/New_York): {new_york_now_iso()}\n"
            "Use this timestamp as the reference for any relative time requests.\n"
            f"User message: {user_text}"
        )

    async def _run_async(
        self, conversation_id: str, user_text: str, context: Dict[str, Any]
    ) -> Tuple[str, List[Dict[str, Any]]]:
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
        trace: List[Dict[str, Any]] = []
        tool_call_count = 0

        async for event in self._runner.run_async(
            user_id=conversation_id,
            session_id=session_id,
            new_message=new_message,
        ):
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            event_id = getattr(event, "id", None) or ""
            author = getattr(event, "author", "") or ""

            if not parts:
                trace.append(
                    {
                        "event_id": str(event_id),
                        "author": author,
                        "part_kinds": [],
                        "text_preview": None,
                    }
                )
                logger.debug(
                    "Agent event no_parts conversation_id=%s session_id=%s event_id=%s author=%s",
                    conversation_id,
                    session_id,
                    event_id,
                    author,
                )
                continue

            part_kinds = [_part_kinds(p) for p in parts]
            text_chunk = "".join(
                (getattr(p, "text", "") or "") for p in parts if getattr(p, "text", "")
            )
            if text_chunk:
                response_chunks.append(text_chunk)
            tool_call_count += sum(1 for k in part_kinds if k == "function_call")

            trace.append(
                {
                    "event_id": str(event_id),
                    "author": author,
                    "part_kinds": part_kinds,
                    "text_preview": (text_chunk[:200] + "..." if len(text_chunk) > 200 else text_chunk)
                    if text_chunk
                    else None,
                }
            )
            logger.debug(
                "Agent event conversation_id=%s session_id=%s event_id=%s author=%s part_kinds=%s",
                conversation_id,
                session_id,
                event_id,
                author,
                part_kinds,
            )

            if tool_call_count >= MAX_TOOL_CYCLES and not response_chunks:
                logger.warning(
                    "Agent hit tool cycle cap with no text conversation_id=%s session_id=%s tool_call_count=%d trace_len=%d",
                    conversation_id,
                    session_id,
                    tool_call_count,
                    len(trace),
                )
                break

        if not response_chunks:
            logger.warning(
                "Agent produced no text chunks conversation_id=%s session_id=%s trace_len=%d tool_call_count=%d",
                conversation_id,
                session_id,
                len(trace),
                tool_call_count,
            )
            return (
                "I could not generate a response right now. Please try again.",
                trace,
            )
        return response_chunks[-1], trace

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
            reply, trace = asyncio.run(self._run_async(conversation_id, user_text, context))
            return {"reply_text": reply, "trace": trace}
        finally:
            CURRENT_CONVERSATION_ID.reset(conversation_token)
            CURRENT_PHONE_NUMBER.reset(phone_token)
