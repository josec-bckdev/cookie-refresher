"""Pure domain entities — no framework imports, no I/O."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus
    steps_taken: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class SessionCookies:
    """Value object representing a valid authenticated session."""
    cf_clearance: str
    ci_session: str

    def __post_init__(self) -> None:
        if not self.cf_clearance or not self.cf_clearance.strip():
            raise ValueError("cf_clearance cannot be empty")
        if not self.ci_session or not self.ci_session.strip():
            raise ValueError("ci_session cannot be empty")


@dataclass(frozen=True)
class ActionRequest:
    """A single browser action Claude wants to perform."""
    action_type: str
    params: dict
    tool_use_id: str


@dataclass(frozen=True)
class AgentStep:
    """One reasoning cycle output from the AI agent."""
    actions: list[ActionRequest]
    is_done: bool
    cookies: Optional[SessionCookies]
    reasoning: str


@dataclass
class AgentResult:
    """Final outcome of a full refresh session attempt."""
    success: bool
    cookies: Optional[SessionCookies]
    error: Optional[str]
    steps_taken: int

    @classmethod
    def ok(cls, cookies: SessionCookies, steps_taken: int) -> "AgentResult":
        """Factory for a successful result."""
        if cookies is None:
            raise ValueError("cookies required for a success result")
        return cls(success=True, cookies=cookies, error=None, steps_taken=steps_taken)

    @classmethod
    def fail(cls, error: str, steps_taken: int) -> "AgentResult":
        """Factory for a failed result."""
        if not error:
            raise ValueError("error message required for a failure result")
        return cls(success=False, cookies=None, error=error, steps_taken=steps_taken)
