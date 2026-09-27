"""Cooperative cancellation for long jobs (the web app's Stop button).

A job runs with a :class:`CancelToken` bound to its thread. Long waits check
it, and an open model stream registers a closer so Stop cuts the connection at
once (vLLM aborts a generation whose client disconnected) instead of letting
the model run on until its timeout.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional


class Cancelled(BaseException):
    """The user stopped the job. A BaseException, so the ``except Exception``
    retry and repair handlers along the way do not swallow it."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closers: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            closers, self._closers = list(self._closers), []
        for close in closers:
            try:
                close()
            except Exception:
                pass

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled("stopped by the user")

    def wait(self, timeout_s: float) -> bool:
        """Sleep up to ``timeout_s``; True when cancelled meanwhile."""
        return self._event.wait(timeout_s)

    @contextmanager
    def closing(self, close: Callable[[], None]) -> Iterator[None]:
        """Run ``close`` if the job is cancelled while the block runs."""
        with self._lock:
            self._closers.append(close)
        try:
            yield
        finally:
            with self._lock:
                if close in self._closers:
                    self._closers.remove(close)


_local = threading.local()
# Tokens of the jobs running now. Worker threads a job starts (parallel model
# calls, prefetch pools) have no thread-local token; they fall back to the
# most recent running job's, since model jobs run one at a time on one GPU.
_active: list[CancelToken] = []
_active_lock = threading.Lock()


def current() -> Optional[CancelToken]:
    token = getattr(_local, "token", None)
    if token is not None:
        return token
    with _active_lock:
        return _active[-1] if _active else None


@contextmanager
def bound(token: CancelToken) -> Iterator[CancelToken]:
    previous = getattr(_local, "token", None)
    _local.token = token
    with _active_lock:
        _active.append(token)
    try:
        yield token
    finally:
        _local.token = previous
        with _active_lock:
            if token in _active:
                _active.remove(token)


def check() -> None:
    token = current()
    if token is not None:
        token.check()
