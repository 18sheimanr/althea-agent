import asyncio
import os
import time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from urllib.request import urlopen

from conversation_store import ConversationStore

CURRENT_CONVERSATION_ID: ContextVar[str] = ContextVar("conversation_id", default="")
CURRENT_PHONE_NUMBER: ContextVar[str] = ContextVar("phone_number", default="")


class AthenaAgentRuntime:
    def __init__(self, store: ConversationStore) -> None:
        self.store = store
        self.app_name = "athena_adk"
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._root_agent = None
        self._runner = None
        self._session_service = None

    def _google_search_tool(self, query: str) -> Dict[str, Any]:
        """Searches Google and returns top results as snippets."""
        api_key = os.getenv("GOOGLE_SEARCH_API_KEY", "")
        cx = os.getenv("GOOGLE_SEARCH_CX", "")
        if not api_key or not cx:
            return {
                "status": "error",
                "message": "Google search is not configured. Set GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX.",
            }

        encoded_query = quote_plus(query)
        url = (
            "https://www.googleapis.com/customsearch/v1"
            f"?key={api_key}&cx={cx}&q={encoded_query}&num=3"
        )
        with urlopen(url, timeout=8) as response:
            payload = response.read().decode("utf-8")

        return {"status": "ok", "query": query, "raw": payload}

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

        event = self.store.create_event(
            event_type=event_type,
            title=title,
            due_at=due_at or None,
            details=details,
            conversation_id=conversation_id,
            phone_number=phone_number,
        )
        return {"status": "ok", "event": event}

    def _build_root_agent(self) -> Any:
        from google.adk.agents.llm_agent import Agent

        instruction = (
            "You are Athena, an SMS planning assistant. "
            "You help the user with scheduling and reminders. "
            "When asked to save a task/event, call create_event_tool. "
            "Event types allowed: full day, partial day, reminder. "
            "When useful, call google_search_tool for factual lookups. "
            "Keep SMS responses concise and practical."
        )
        return Agent(
            model=self.model_name,
            name="athena_sms_agent",
            description="SMS scheduler assistant for one user.",
            instruction=instruction,
            tools=[self._google_search_tool, self._create_event_tool],
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

        new_message = types.Content(role="user", parts=[types.Part(text=user_text)])
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
