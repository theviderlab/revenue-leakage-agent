"""Scripted chat model for tests: returns queued AIMessages in order, no network."""

from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedLLM(BaseChatModel):
    steps: list[AIMessage] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        if not self.steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        return ChatResult(generations=[ChatGeneration(message=self.steps.pop(0))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedLLM":
        # The LangChain fake chat models do not implement bind_tools.
        return self

    def queue(self, *steps: AIMessage) -> "ScriptedLLM":
        self.steps.extend(steps)
        return self


def call(name: str, call_id: str | None = None, **args: Any) -> ToolCall:
    return ToolCall(name=name, args=args, id=call_id or f"call_{uuid4().hex[:8]}")


def ai(*calls: ToolCall, text: str = "") -> AIMessage:
    return AIMessage(content=text, tool_calls=list(calls))
