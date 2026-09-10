from datetime import UTC, datetime

from matoca_service.web.timezone import localize_datetime, parse_timezone


def test_parse_timezone_accepts_iana_name() -> None:
    assert parse_timezone("America/New_York").key == "America/New_York"


def test_parse_timezone_defaults_invalid_or_missing_to_tokyo() -> None:
    assert parse_timezone(None).key == "Asia/Tokyo"
    assert parse_timezone("").key == "Asia/Tokyo"
    assert parse_timezone("not/a-zone").key == "Asia/Tokyo"


def test_localize_datetime_preserves_instant_with_numeric_offset() -> None:
    value = datetime(2026, 9, 10, 3, tzinfo=UTC)

    assert localize_datetime(value, parse_timezone(None)).isoformat() == "2026-09-10T12:00:00+09:00"
