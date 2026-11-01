"""PricingAgent: high-level entry point for conversational and autonomous workflows."""
import logging
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from agent.llm_backend import LLMBackend, create_llm_backend
from agent.tools import build_agent_tools
from agent.conversation import (
    ConversationStore, SQLiteConversationStore,
    init_conversation_store, get_conversation_store,
)
from agent.react_loop import ReActLoop
from rag.pipeline import RAGPipeline
from src.services.pricing_alerts import redact_alert_tokens

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Structured response returned by PricingAgent methods."""
    reply: str
    conversation_id: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)


class PricingAgent:
    """Conversational + autonomous LLM pricing agent.

    Wraps a RAGPipeline, a configurable LLM backend, and a set of MCP tool
    wrappers.  Call ``initialize()`` once before using ``chat()`` or
    ``run_task()``.
    """

    def __init__(self, tool_manager=None):
        self._tool_manager = tool_manager
        self._rag: Optional[RAGPipeline] = None
        self._llm_backend: Optional[LLMBackend] = None
        self._conversation_store: Optional[ConversationStore] = None
        self._tools: List[Any] = []
        self._initialized = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Build RAG index, create LLM backend, and wire up tools."""
        if self._initialized:
            return

        # Lazy import to avoid circular imports
        from src.config.settings import settings

        try:
            self._conversation_store = get_conversation_store()
            logger.info("Conversation store: using shared singleton")
        except RuntimeError:
            # Not yet initialized (e.g. tests that bypass startup) — create one here
            await init_conversation_store(
                settings.conversation_db_path, settings.agent_max_history_turns,
                settings.conversation_retention_days,
            )
            self._conversation_store = get_conversation_store()
            logger.info("Conversation store: initialized by agent")

        # LLM backend
        api_key = self._get_api_key(settings)
        self._llm_backend = create_llm_backend(
            provider=settings.agent_llm_provider,
            api_key=api_key,
            model=settings.agent_model,
        )
        logger.info(
            "LLM backend: provider=%s model=%s",
            settings.agent_llm_provider,
            settings.agent_model,
        )

        # RAG pipeline
        self._rag = RAGPipeline(
            docs_path=settings.rag_docs_path,
            top_k=settings.rag_top_k,
        )
        try:
            from src.services.pricing_aggregator import PricingAggregatorService
            pricing_service = PricingAggregatorService()
            pricing_data = await pricing_service.get_all_pricing()
            await self._rag.build_index(pricing_data)
            logger.info("RAG index built with %d pricing models", len(pricing_data))
        except Exception as exc:
            logger.warning("Could not load live pricing for RAG index: %s", exc)
            await self._rag.build_index([])

        # Tools
        if self._tool_manager and self._rag:
            self._tools = build_agent_tools(self._tool_manager, self._rag)
        else:
            self._tools = []
        logger.info("PricingAgent ready with %d tools", len(self._tools))

        self._initialized = True

    @staticmethod
    def _sanitize_input(text: str) -> str:
        """Sanitize user input to prevent prompt injection and null-byte attacks.

        - NFKC-normalizes Unicode (prevents homoglyph attacks)
        - Strips null bytes and ASCII control characters (0x00–0x1F except \\t, \\n, \\r)
        """
        # NFKC normalization collapses visually identical but distinct Unicode code-points
        normalized = unicodedata.normalize("NFKC", text)
        # Remove null bytes and non-printable control characters while keeping tabs/newlines
        sanitized = "".join(
            ch for ch in normalized
            if ch in ("\t", "\n", "\r") or (ord(ch) >= 0x20)
        )
        return sanitized

    @staticmethod
    def _messages_for_turn(history, raw_message: str) -> List[Dict[str, Any]]:
        """History for the LLM, with this turn's user message unredacted.

        v1.81.0 (Q6): history stores the user turn with alert_tokens redacted, but the
        model must see the real token for this turn only (e.g. "delete my alert, token ...").
        """
        messages = history.to_messages()
        if messages and messages[-1].get("role") == "user":
            messages[-1] = {"role": "user", "content": raw_message}
        return messages

    @staticmethod
    def _get_api_key(settings) -> str:
        provider = settings.agent_llm_provider
        key = (
            settings.anthropic_api_key
            if provider == "anthropic"
            else settings.openai_api_key
        )
        if not key:
            raise ValueError(
                f"API key for provider '{provider}' is not configured. "
                f"Set {'ANTHROPIC_API_KEY' if provider == 'anthropic' else 'OPENAI_API_KEY'} "
                "in your environment or .env file."
            )
        return key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self, message: str, conversation_id: Optional[str] = None
    ) -> AgentResponse:
        """Handle a conversational message, maintaining per-session history."""
        await self._ensure_initialized()

        message = self._sanitize_input(message)
        conv_id, history = await self._conversation_store.get_or_create(conversation_id)
        history.add("user", redact_alert_tokens(message))

        from src.config.settings import settings
        loop = ReActLoop(
            llm_backend=self._llm_backend,
            tools=self._tools,
            max_iterations=settings.agent_max_iterations,
        )
        result = await loop.run(self._messages_for_turn(history, message))

        # v1.81.0 (Q6): the live reply keeps any alert_token; the stored turn never does.
        history.add("assistant", redact_alert_tokens(result.final_answer))
        await self._conversation_store.save(conv_id, history)

        return AgentResponse(
            reply=result.final_answer,
            conversation_id=conv_id,
            tool_calls=result.tool_calls,
            sources=self._extract_sources(result.tool_calls),
        )

    async def chat_stream(
        self, message: str, conversation_id: Optional[str] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream ReAct loop events for a conversational message."""
        await self._ensure_initialized()

        message = self._sanitize_input(message)
        conv_id, history = await self._conversation_store.get_or_create(conversation_id)
        history.add("user", redact_alert_tokens(message))

        from src.config.settings import settings
        loop = ReActLoop(
            llm_backend=self._llm_backend,
            tools=self._tools,
            max_iterations=settings.agent_max_iterations,
        )

        final_answer = ""
        all_tool_calls: List[Dict[str, Any]] = []
        async for event in loop.stream(self._messages_for_turn(history, message)):
            if event["type"] == "answer":
                final_answer = event["text"]
                all_tool_calls = event.get("tool_calls", [])
                yield {
                    **event,
                    "conversation_id": conv_id,
                    "sources": self._extract_sources(all_tool_calls),
                }
            else:
                yield event

        if final_answer:
            # v1.81.0 (Q6): streamed events keep any alert_token; the stored turn never does.
            history.add("assistant", redact_alert_tokens(final_answer))
            await self._conversation_store.save(conv_id, history)

    async def run_task(self, task: str) -> AgentResponse:
        """Run an autonomous multi-step task without conversation history."""
        await self._ensure_initialized()

        task = self._sanitize_input(task)
        from src.config.settings import settings
        loop = ReActLoop(
            llm_backend=self._llm_backend,
            tools=self._tools,
            max_iterations=settings.agent_max_iterations,
        )
        result = await loop.run([{"role": "user", "content": task}])

        return AgentResponse(
            reply=result.final_answer,
            conversation_id=str(uuid.uuid4()),
            tool_calls=result.tool_calls,
            sources=self._extract_sources(result.tool_calls),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    @staticmethod
    def _extract_sources(tool_calls: List[Dict[str, Any]]) -> List[str]:
        """Collect unique RAG source references from tool call results."""
        sources: List[str] = []
        for tc in tool_calls:
            if tc.get("tool") == "rag_retrieve":
                for chunk in tc.get("result", {}).get("chunks", []):
                    src = chunk.get("source", "")
                    if src and src not in sources:
                        sources.append(src)
        return sources
