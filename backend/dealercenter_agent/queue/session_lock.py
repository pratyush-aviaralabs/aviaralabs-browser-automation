"""Cross-process lock ensuring only one real DealerCenter browser
session is ever active at a time — across the web process (manual Run,
Login/Refresh, Inspector) and the future workflow-run queue worker
(separate process, can't see the web process's in-memory
_ACTIVE_RUN_ID check). Needed because two concurrent logins to the
same account previously corrupted the shared session file (confirmed
live, 2026-11-17) — this generalizes that same guarantee across
process boundaries.

TTL is a dead-process safety net, not the primary release path — every
caller should release explicitly (try/finally) the moment its browser
session ends, success or failure."""

import uuid

from redis.exceptions import WatchError

from .connection import get_redis_connection

LOCK_KEY = "dealercenter_agent:active_dealercenter_session"
LOCK_TTL_SECONDS = 30 * 60


def try_acquire_session_lock() -> str | None:
    """Returns a token to pass to release_session_lock later, or None
    if another session already holds the lock."""
    token = uuid.uuid4().hex
    redis = get_redis_connection()
    acquired = redis.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS)
    return token if acquired else None


def release_session_lock(token: str) -> None:
    """Only releases if we're still the holder — WATCH/MULTI/EXEC makes
    the compare-and-delete atomic (no separate Lua/EVAL dependency), so
    a lock that's already expired and been re-acquired by someone else
    is never accidentally released out from under them."""
    redis = get_redis_connection()
    with redis.pipeline() as pipe:
        try:
            pipe.watch(LOCK_KEY)
            current = pipe.get(LOCK_KEY)
            if current is not None and current.decode() == token:
                pipe.multi()
                pipe.delete(LOCK_KEY)
                pipe.execute()
            else:
                pipe.unwatch()
        except WatchError:
            # Someone else changed the key between our GET and DELETE —
            # exactly the case we must NOT delete in.
            pass


def is_session_lock_held() -> bool:
    return bool(get_redis_connection().exists(LOCK_KEY))
