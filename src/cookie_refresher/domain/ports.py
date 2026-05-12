"""Abstract ports (interfaces) — define the shape of every external dependency."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional

from .entities import AgentResult, Job, SessionCookies, AgentStep


class IBrowserGateway(ABC):
    """Controls the headful browser running in the VNC sandbox container."""

    @abstractmethod
    async def start(self) -> None:
        """Start the browser sandbox container and wait until it is ready."""
        ...

    @abstractmethod
    async def navigate(self, url: str, wait_seconds: float = 6.0) -> None: ...

    @abstractmethod
    async def take_screenshot(self) -> bytes: ...

    @abstractmethod
    async def click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def double_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def triple_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def type_text(self, text: str) -> None: ...

    @abstractmethod
    async def press_key(self, key: str) -> None: ...

    @abstractmethod
    async def scroll(self, x: int, y: int, direction: str, amount: int) -> None: ...

    @abstractmethod
    async def right_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def left_click_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None: ...

    @abstractmethod
    async def close(self) -> None:
        """Stop the browser sandbox container and release held resources."""
        ...


class IVtrackGateway(ABC):
    """Delivers refreshed cookies to the vtrack FastAPI service."""

    @abstractmethod
    async def post_cookies(self, cookies: SessionCookies) -> bool: ...


class IAgentClient(ABC):
    """Wraps the AI provider that drives the ReAct loop."""

    @abstractmethod
    async def complete(self, messages: list[dict]) -> AgentStep: ...


class IJobStore(ABC):
    """Tracks the lifecycle of async refresh jobs."""

    @abstractmethod
    async def create(self) -> Job: ...

    @abstractmethod
    async def get(self, job_id: str) -> Optional[Job]: ...

    @abstractmethod
    async def update(self, job_id: str, result: AgentResult) -> None: ...
