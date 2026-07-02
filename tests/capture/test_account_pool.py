"""Spec tests for the account pool (docs/tasks/M1-T10-account-pool.md).

`test_ban_rotates_out` is reformatted from the task spec's semicolon-joined statement into
ruff-clean multi-line form (same precedent as `tests/capture/test_base.py`) -- every assertion
below is identical to the spec. Two extra tests cover plain (non-ban) rotation/exhaustion and
`stats()`, which the spec's two given tests don't otherwise exercise.
"""

import pytest

from gw_geo.capture.account_pool import Account, AccountPool, NoAccountAvailable


class FakeSecrets:
    def load_accounts(self, config_ref):
        return [
            Account(id="a1", surface="chatgpt", persona="smb_buyer"),
            Account(id="a2", surface="chatgpt", persona="smb_buyer"),
        ]


def test_from_secrets_and_acquire_by_surface_persona():
    pool = AccountPool.from_secrets(FakeSecrets(), "ssm://accounts")
    a = pool.acquire(surface="chatgpt", persona="smb_buyer")
    assert a.surface == "chatgpt" and a.persona == "smb_buyer"
    with pytest.raises(NoAccountAvailable):
        pool.acquire(surface="grok", persona="smb_buyer")  # no grok accounts


def test_ban_rotates_out():
    pool = AccountPool.from_secrets(FakeSecrets(), "ref")
    a = pool.acquire(surface="chatgpt", persona="smb_buyer")
    pool.mark_banned(a)
    pool.release(a)
    b = pool.acquire(surface="chatgpt", persona="smb_buyer")
    assert b.id != a.id


def test_release_allows_reacquire_and_rotates_without_ban():
    pool = AccountPool.from_secrets(FakeSecrets(), "ref")
    first = pool.acquire(surface="chatgpt", persona="smb_buyer")
    second = pool.acquire(surface="chatgpt", persona="smb_buyer")
    assert {first.id, second.id} == {"a1", "a2"}
    with pytest.raises(NoAccountAvailable):
        pool.acquire(surface="chatgpt", persona="smb_buyer")  # both in use

    pool.release(first)
    assert pool.acquire(surface="chatgpt", persona="smb_buyer").id == first.id


def test_stats_reports_total_banned_in_use():
    pool = AccountPool.from_secrets(FakeSecrets(), "ref")
    a = pool.acquire(surface="chatgpt", persona="smb_buyer")
    pool.mark_banned(a)
    assert pool.stats() == {"total": 2, "banned": 1, "in_use": 0}
