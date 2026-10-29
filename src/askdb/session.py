"""Conversation sessions and per-user limits (SPEC 5.1, 7).

A session holds only text the user has already seen (interpretation lines) and a record of the
last answer for feedback and chips. It never holds result values. A session belongs to one user
and one role: presenting its id under any other identity finds nothing, so context cannot widen
permissions.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

SESSION_TTL_SECONDS = 30 * 60
MAX_INTERPRETATIONS = 6
MAX_SESSIONS = 10_000
RATE_PER_MINUTE = 20
RATE_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class LastAnswer:
    question: str
    interpretation: str | None
    sql: str  # for the feedback row; never sent to the user
    row_count: int
    chips: tuple = ()  # planner.Chip values offered with the answer


@dataclass
class Session:
    id: str
    user: str
    role: str
    touched: float
    interpretations: list[str] = field(default_factory=list)
    last: LastAnswer | None = None


class SessionStore:
    def __init__(
        self,
        ttl: float = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_sessions: int = MAX_SESSIONS,
    ):
        self.ttl = ttl
        self.clock = clock
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for sid in [s for s, v in self._sessions.items() if now - v.touched > self.ttl]:
            del self._sessions[sid]

    def open(self, session_id: str | None, user: str, role: str) -> tuple[Session, bool]:
        """Return (session, ended). `ended` is true when an id was given but is expired, unknown
        or belongs to someone else, so a fresh session was started instead."""
        with self._lock:
            now = self.clock()
            self._sweep(now)
            found = self._sessions.get(session_id) if session_id else None
            if found and found.user == user and found.role == role:
                found.touched = now
                return found, False
            while len(self._sessions) >= self.max_sessions:
                del self._sessions[next(iter(self._sessions))]  # oldest first
            session = Session(secrets.token_urlsafe(16), user, role, now)
            self._sessions[session.id] = session
            return session, bool(session_id)

    def reset(self, session_id: str, user: str, role: str) -> bool:
        """ "New question": forget the context. Returns False when there was nothing to reset."""
        with self._lock:
            self._sweep(self.clock())
            found = self._sessions.get(session_id)
            if not found or found.user != user or found.role != role:
                return False
            found.interpretations.clear()
            found.last = None
            found.touched = self.clock()
            return True

    def remember(self, session: Session, interpretation: str, last: LastAnswer | None) -> None:
        with self._lock:
            session.interpretations.append(interpretation)
            del session.interpretations[:-MAX_INTERPRETATIONS]
            if last is not None:
                session.last = last


class RateLimited(Exception):
    pass


class AlreadyRunning(Exception):
    pass


class UserLimiter:
    """Requests per minute and one running question per user."""

    def __init__(
        self,
        per_minute: int = RATE_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.per_minute = per_minute
        self.clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._running: set[str] = set()
        self._lock = threading.Lock()

    @contextmanager
    def request(self, user: str) -> Iterator[None]:
        with self._lock:
            now = self.clock()
            recent = self._seen.setdefault(user, deque())
            while recent and now - recent[0] >= RATE_WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= self.per_minute:
                raise RateLimited(user)
            if user in self._running:
                raise AlreadyRunning(user)
            recent.append(now)
            self._running.add(user)
        try:
            yield
        finally:
            with self._lock:
                self._running.discard(user)
