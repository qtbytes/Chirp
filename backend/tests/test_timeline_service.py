from datetime import datetime, timezone

from app.services.timeline_service import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    created_at = datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc)
    cursor = encode_cursor(created_at, 42)

    decoded_created_at, decoded_id = decode_cursor(cursor)

    assert decoded_created_at == created_at
    assert decoded_id == 42


def test_decode_invalid_cursor() -> None:
    decoded_created_at, decoded_id = decode_cursor("bad-cursor")

    assert decoded_created_at is None
    assert decoded_id is None
