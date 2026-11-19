"""Service interface behind the /api routes (D14, D29): the routes never know which one runs."""

from functools import lru_cache
from typing import Literal, Protocol

from app.schemas import (
    CreateThreadResponse,
    Plan,
    SettingsResponse,
    SettingsUpdate,
    TurnResponse,
)


class ThreadNotFound(Exception):
    """404: the thread does not exist."""


class Conflict(Exception):
    """409: the request does not match the state (D10)."""


class InvalidSettings(Exception):
    """422: a settings value is not valid (D24)."""


class AgentService(Protocol):
    def list_plans(self) -> list[Plan]: ...

    def get_settings(self) -> SettingsResponse: ...

    def update_settings(self, update: SettingsUpdate) -> SettingsResponse: ...

    def create_thread(self) -> CreateThreadResponse: ...

    def get_thread(self, thread_id: str) -> TurnResponse: ...

    def send_message(self, thread_id: str, text: str) -> TurnResponse: ...

    def decide(
        self, thread_id: str, interrupt_id: str, decision: Literal["approve", "reject"]
    ) -> TurnResponse: ...

    def graph(self) -> str: ...


def build_service(backend: Literal["mock", "real"]) -> AgentService:
    if backend == "mock":
        from app.mock import MockService

        return MockService()
    from app.agent.service import create_service

    return create_service()


@lru_cache
def get_service() -> AgentService:
    from app.config import settings

    return build_service(settings.agent_backend)
