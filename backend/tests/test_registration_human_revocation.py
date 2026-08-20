"""Human Discord revocations must outlive automatic /register retries.

Run directly: PYTHONPATH=. python tests/test_registration_human_revocation.py
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api.routes.auth import (
    _registration_roles_to_revoke,
    _registration_request_is_superseded,
    _revoke_registration_by_human,
)


def test_human_revocation_wins_over_older_and_equal_requests():
    revoked_at = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    assert _registration_request_is_superseded(revoked_at - timedelta(seconds=1), revoked_at)
    assert _registration_request_is_superseded(revoked_at, revoked_at)
    assert not _registration_request_is_superseded(revoked_at + timedelta(seconds=1), revoked_at)
    assert not _registration_request_is_superseded(revoked_at, None)


def test_human_revocation_deactivates_the_registration():
    registration = SimpleNamespace(active=True, human_revoked_at=None)
    revoked_at = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    _revoke_registration_by_human([registration], revoked_at)
    assert not registration.active
    assert registration.human_revoked_at == revoked_at


def test_massinfo_access_preserves_only_registration_roles():
    registration = SimpleNamespace(id=1, role_id=10)
    unrelated = SimpleNamespace(id=2, role_id=20)
    assert _registration_roles_to_revoke([registration, unrelated], {30}, False) == []
    assert _registration_roles_to_revoke([registration, unrelated], {10}, True) == []
    assert _registration_roles_to_revoke([registration, unrelated], {10}, False) == [registration]


if __name__ == "__main__":
    test_human_revocation_wins_over_older_and_equal_requests()
    test_human_revocation_deactivates_the_registration()
    test_massinfo_access_preserves_only_registration_roles()
    print("human registration revocation OK")
