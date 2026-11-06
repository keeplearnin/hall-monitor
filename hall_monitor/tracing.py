"""Langfuse tracing. Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set.

Spans carry IDs, versions, hashes and token counts only. Function arguments and return values
are never captured, because they hold claim text, medical details and policy records. A traced
function that raises marks its span as an error with the exception's type only: the message can
repeat the model's output, so it never reaches Langfuse. The Anthropic SDK's own spans default
to metadata only; don't set ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE=content.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _contained[**P, R](fn: Callable[P, R]) -> Callable[P, tuple[R | None, BaseException | None]]:
    """Run fn inside the span without letting an exception escape it: Langfuse would record the
    exception's message, and a validation error's message repeats the model's output (claim
    text). The span is marked as an error by type only; the caller re-raises the exception."""

    @functools.wraps(fn)
    def inner(*args: P.args, **kwargs: P.kwargs) -> tuple[R | None, BaseException | None]:
        try:
            return fn(*args, **kwargs), None
        except Exception as exc:
            try:
                from langfuse import get_client

                get_client().update_current_span(level="ERROR", status_message=type(exc).__name__)
            except Exception:  # tracing must never change what the claim does
                pass
            return None, exc

    return inner


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

                traced = langfuse_observe(
                    name=name, as_type=as_type, capture_input=False, capture_output=False
                )(_contained(fn))
            result, error = traced(*args, **kwargs)
            if error is not None:
                raise error
            return result

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
