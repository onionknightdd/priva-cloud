"""Login/registration attempt limiting.

The limiter was keyed on the username alone, which got it backwards in both
directions: rotating usernames spent an unlimited budget, while five bad guesses
against a known username locked that account's real owner out. Registration was
not limited at all, so an unauthenticated caller could spend cost-12 bcrypt CPU
and pending-row inserts without bound.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from priva_control_panel.services.auth import LoginRateLimiter, client_ip


def _burn(limiter: LoginRateLimiter, username: str, ip: str, times: int) -> None:
    for _ in range(times):
        limiter.record_failure(username, ip)


def test_one_source_cannot_lock_out_the_real_owner():
    """The account-lockout DoS: the victim must still be able to log in from
    their own address while an attacker is being throttled at theirs."""
    limiter = LoginRateLimiter(max_attempts=5)
    _burn(limiter, "victim", "203.0.113.9", 5)

    with pytest.raises(HTTPException) as exc:
        limiter.check("victim", "203.0.113.9")      # attacker is blocked
    assert exc.value.status_code == 429

    limiter.check("victim", "198.51.100.4")          # the owner is not


def test_rotating_usernames_still_hits_the_per_ip_ceiling():
    """Per-username buckets alone gave an attacker an unlimited total budget."""
    limiter = LoginRateLimiter(max_attempts=5, max_per_ip=30)
    for i in range(30):
        limiter.record_failure(f"user{i}", "203.0.113.9")

    with pytest.raises(HTTPException, match="from this address"):
        limiter.check("user999", "203.0.113.9")


def test_success_clears_the_account_bucket_but_not_the_ip_budget():
    limiter = LoginRateLimiter(max_attempts=5, max_per_ip=30)
    _burn(limiter, "alice", "203.0.113.9", 4)
    limiter.reset("alice", "203.0.113.9")
    limiter.check("alice", "203.0.113.9")            # account bucket cleared

    for i in range(30):
        limiter.record_failure(f"u{i}", "203.0.113.9")
    limiter.reset("alice", "203.0.113.9")
    with pytest.raises(HTTPException, match="from this address"):
        limiter.check("alice", "203.0.113.9")        # one valid login can't launder it


def test_attempts_expire_with_the_window(monkeypatch):
    import priva_control_panel.services.auth as A

    now = [1000.0]
    monkeypatch.setattr(A.time, "time", lambda: now[0])
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=60)
    _burn(limiter, "alice", "203.0.113.9", 5)
    with pytest.raises(HTTPException):
        limiter.check("alice", "203.0.113.9")

    now[0] += 61
    limiter.check("alice", "203.0.113.9")


def test_expired_buckets_are_swept(monkeypatch):
    """The dict is keyed by attacker-supplied values — it must not grow without
    bound while an attacker rotates usernames."""
    import priva_control_panel.services.auth as A

    now = [1000.0]
    monkeypatch.setattr(A.time, "time", lambda: now[0])
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=60)
    for i in range(500):
        limiter.record_failure(f"user{i}", f"198.51.100.{i % 256}")
    assert len(limiter._attempts) > 100

    now[0] += 61
    limiter.check("someone", "203.0.113.1")
    assert len(limiter._attempts) <= 2   # only the fresh probe's own buckets


def test_client_ip_prefers_the_forwarded_hop():
    from starlette.requests import Request

    behind_proxy = Request({
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")],
        "client": ("10.0.0.1", 1234),
    })
    assert client_ip(behind_proxy) == "203.0.113.9"

    direct = Request({"type": "http", "headers": [], "client": ("198.51.100.7", 1234)})
    assert client_ip(direct) == "198.51.100.7"
