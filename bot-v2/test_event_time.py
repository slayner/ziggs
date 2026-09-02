from datetime import datetime, timezone

from cogs.event_cmd import _parse_event_time


NOW = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)


def check(value: str, expected: str | None) -> None:
    parsed = _parse_event_time(value, NOW)
    assert (parsed.isoformat() if parsed else None) == expected


if __name__ == "__main__":
    check("21h", "2026-07-24T21:00:00+00:00")
    check("21h30 BRT", "2026-07-25T00:30:00+00:00")
    check("24/07/2026 21h BRT", "2026-07-25T00:00:00+00:00")
    check("25/07 21h", "2026-07-25T21:00:00+00:00")
    check("25.07.2026 21:00 GMT", "2026-07-25T21:00:00+00:00")
    check("2026-07-25 11:30 CEST", "2026-07-25T09:30:00+00:00")
    check("11:30 CEST", "2026-07-25T09:30:00+00:00")
    check("31/02/2026 21h BRT", None)
    check("2026-07-23 21h UTC", None)
    print("event time parser: ok")
