import pytest

from askdb.session import (
    MAX_INTERPRETATIONS,
    SESSION_TTL_SECONDS,
    AlreadyRunning,
    LastAnswer,
    RateLimited,
    SessionStore,
    UserLimiter,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


def test_default_expiry_is_thirty_minutes():
    assert SESSION_TTL_SECONDS == 30 * 60


def test_session_is_reused_until_it_goes_quiet(clock):
    store = SessionStore(clock=clock)
    first, ended = store.open(None, "u", "admin")
    assert not ended
    store.remember(first, "Active MRR", None)
    clock.advance(29 * 60)
    again, ended = store.open(first.id, "u", "admin")
    assert again is first and not ended and again.interpretations == ["Active MRR"]
    clock.advance(29 * 60)  # activity restarted the clock
    assert store.open(first.id, "u", "admin")[0] is first


def test_session_expires_after_thirty_quiet_minutes(clock):
    store = SessionStore(clock=clock)
    first, _ = store.open(None, "u", "admin")
    store.remember(first, "Active MRR", None)
    clock.advance(30 * 60 + 1)
    fresh, ended = store.open(first.id, "u", "admin")
    assert ended and fresh.id != first.id and fresh.interpretations == []


def test_unknown_session_id_starts_fresh_and_says_so(clock):
    fresh, ended = SessionStore(clock=clock).open("made-up", "u", "admin")
    assert ended and fresh.id != "made-up"


@pytest.mark.parametrize(("user", "role"), [("other", "admin"), ("u", "analyst")])
def test_another_identity_cannot_use_the_session(clock, user, role):
    store = SessionStore(clock=clock)
    mine, _ = store.open(None, "u", "admin")
    store.remember(mine, "Account owner breakdown", None)
    theirs, ended = store.open(mine.id, user, role)
    assert ended and theirs.id != mine.id and theirs.interpretations == []
    assert store.open(mine.id, "u", "admin")[0].interpretations == ["Account owner breakdown"]


def test_new_question_forgets_the_context(clock):
    store = SessionStore(clock=clock)
    s, _ = store.open(None, "u", "admin")
    store.remember(s, "Active MRR", LastAnswer("q", "Active MRR", "SELECT 1", 1))
    assert store.reset(s.id, "u", "admin")
    again, ended = store.open(s.id, "u", "admin")
    assert again is s and not ended
    assert s.interpretations == [] and s.last is None


def test_reset_needs_the_owner(clock):
    store = SessionStore(clock=clock)
    s, _ = store.open(None, "u", "admin")
    store.remember(s, "x", None)
    assert not store.reset(s.id, "intruder", "admin")
    assert not store.reset("nope", "u", "admin")
    assert s.interpretations == ["x"]


def test_only_recent_interpretations_are_kept(clock):
    store = SessionStore(clock=clock)
    s, _ = store.open(None, "u", "admin")
    for n in range(MAX_INTERPRETATIONS + 4):
        store.remember(s, f"reading {n}", None)
    assert len(s.interpretations) == MAX_INTERPRETATIONS
    assert s.interpretations[-1] == f"reading {MAX_INTERPRETATIONS + 3}"


def test_store_is_bounded(clock):
    store = SessionStore(clock=clock, max_sessions=3)
    ids = [store.open(None, f"u{n}", "admin")[0].id for n in range(5)]
    assert len(set(ids)) == 5
    assert len(store._sessions) == 3


# ------------------------------------------------------------------ limiter


def test_per_minute_limit_then_the_window_slides(clock):
    limiter = UserLimiter(per_minute=3, clock=clock)
    for _ in range(3):
        with limiter.request("u"):
            pass
    with pytest.raises(RateLimited), limiter.request("u"):
        pass
    clock.advance(61)
    with limiter.request("u"):
        pass


def test_limits_are_per_user(clock):
    limiter = UserLimiter(per_minute=1, clock=clock)
    with limiter.request("a"):
        pass
    with limiter.request("b"):
        pass
    with pytest.raises(RateLimited), limiter.request("a"):
        pass


def test_one_running_question_per_user(clock):
    limiter = UserLimiter(per_minute=10, clock=clock)
    with limiter.request("a"):
        with pytest.raises(AlreadyRunning), limiter.request("a"):
            pass
        with limiter.request("b"):  # someone else is fine
            pass
    with limiter.request("a"):  # released afterwards
        pass


def test_a_failing_request_releases_the_slot(clock):
    limiter = UserLimiter(per_minute=10, clock=clock)
    with pytest.raises(RuntimeError), limiter.request("a"):
        raise RuntimeError
    with limiter.request("a"):
        pass


def test_a_blocked_request_does_not_use_up_the_allowance(clock):
    limiter = UserLimiter(per_minute=2, clock=clock)
    with limiter.request("a"):
        for _ in range(5):
            with pytest.raises(AlreadyRunning), limiter.request("a"):
                pass
    with limiter.request("a"):
        pass
