import fakeredis
import pytest

from dealercenter_agent.queue import session_lock


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(session_lock, "get_redis_connection", lambda: client)
    return client


def test_acquire_succeeds_when_lock_is_free(fake_redis):
    token = session_lock.try_acquire_session_lock()
    assert token is not None
    assert session_lock.is_session_lock_held()


def test_second_acquire_fails_while_first_holds_it(fake_redis):
    first_token = session_lock.try_acquire_session_lock()
    assert first_token is not None

    second_token = session_lock.try_acquire_session_lock()
    assert second_token is None


def test_release_frees_the_lock_for_the_next_acquire(fake_redis):
    token = session_lock.try_acquire_session_lock()
    session_lock.release_session_lock(token)

    assert not session_lock.is_session_lock_held()
    assert session_lock.try_acquire_session_lock() is not None


def test_release_with_wrong_token_does_not_release_someone_elses_lock(fake_redis):
    real_token = session_lock.try_acquire_session_lock()

    session_lock.release_session_lock("not-the-real-token")

    assert session_lock.is_session_lock_held()
    assert session_lock.try_acquire_session_lock() is None  # still held by real_token
    session_lock.release_session_lock(real_token)  # cleanup
