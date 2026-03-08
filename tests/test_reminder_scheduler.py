import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from reminder_scheduler import parse_due_at_utc, NEW_YORK_TZ

def test_parse_due_at_utc_naive():
    # Naive string should be treated as New York time
    due_at = "2026-03-01T15:30:00"
    dt_utc = parse_due_at_utc(due_at)
    
    # In March 2026, NY is EST (UTC-5) until March 8, 2026.
    # So 15:30 NY is 20:30 UTC.
    assert dt_utc.year == 2026
    assert dt_utc.month == 3
    assert dt_utc.day == 1
    assert dt_utc.hour == 20
    assert dt_utc.minute == 30
    assert dt_utc.tzinfo == timezone.utc

def test_parse_due_at_utc_aware_z():
    # Aware string with 'Z' should be treated as UTC
    due_at = "2026-03-01T15:30:00Z"
    dt_utc = parse_due_at_utc(due_at)
    
    assert dt_utc.hour == 15
    assert dt_utc.minute == 30
    assert dt_utc.tzinfo == timezone.utc

def test_parse_due_at_utc_dst_transition():
    # March 8, 2026 is when DST starts (clocks jump 2am -> 3am)
    # 1:59 AM EST -> 6:59 AM UTC
    # 3:00 AM EDT -> 7:00 AM UTC
    
    # 1:30 AM EST (NY)
    dt_est = parse_due_at_utc("2026-03-08T01:30:00")
    assert dt_est.hour == 6
    assert dt_est.minute == 30
    
    # 3:30 AM EDT (NY)
    dt_edt = parse_due_at_utc("2026-03-08T03:30:00")
    assert dt_edt.hour == 7
    assert dt_edt.minute == 30

def test_parse_due_at_invalid():
    with pytest.raises(ValueError, match="Invalid datetime format"):
        parse_due_at_utc("not-a-date")
