"""Langfuse tracing. Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set."""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def observe(name: str, as_type: str = "span") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace the decorated function when tracing is on. Checked per call, so .env can load late."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        traced: Callable[P, R] | None = None

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            nonlocal traced
            if not enabled():
                return fn(*args, **kwargs)
            if traced is None:
                from langfuse import observe as langfuse_observe

                traced = langfuse_observe(name=name, as_type=as_type)(fn)
            return traced(*args, **kwargs)

        return wrapper

    return decorator


def update_span(**fields: Any) -> None:
    if enabled():
        from langfuse import get_client

        get_client().update_current_span(**fields)


def flush() -> None:
    if enabled():
        from langfuse import get_client

        get_client().flush()
