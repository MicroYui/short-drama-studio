"""Cooperative control at persisted decision/Tool boundaries.

An already-started operation finishes normally. The next operation yields to
the product session so it can apply user input or pause. This is control flow,
like asyncio cancellation, and must not become a failed Tool observation.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class ExecutionYielded(BaseException):
    """The session needs ownership before another operation may start."""


_control: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "agent_execution_control", default=None
)


def execution_boundary() -> None:
    control = _control.get()
    reason = None if control is None else control()
    if reason is not None:
        raise ExecutionYielded(reason)


@contextmanager
def execution_control(control: Callable[[], str | None]) -> Iterator[None]:
    token = _control.set(control)
    try:
        yield
    finally:
        _control.reset(token)
