"""Shared business-date and period helpers for analytics pipelines."""

from datetime import date, datetime, timedelta

BUSINESS_DATE_FIELDS = (
    "created_at",
    "createdAt",
    "business_date",
    "businessDate",
    "report_date",
    "reportDate",
    "campaign_date",
    "campaignDate",
    "snapshot_date",
    "snapshotDate",
    "date",
)


def _first_present(row, fields):
    for field in fields:
        value = row.get(field) if isinstance(row, dict) else None
        if value not in (None, ""):
            return value
    return None


def _parse_date(value):
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    if " — " in text:
        text = text.split(" — ", 1)[0]

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def to_business_date(row):
    """Return normalized YYYY-MM-DD business date for a row.

    Primary rule: business_date is DATE(created_at). If created_at is absent,
    fall back to report_date, campaign_date, snapshot_date, then date.
    """
    parsed = _parse_date(_first_present(row or {}, BUSINESS_DATE_FIELDS))
    return parsed.isoformat() if parsed else None


def get_current_period(start, end):
    """Return inclusive current period boundaries as date objects."""
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date is None or end_date is None:
        raise ValueError("start and end must contain valid dates")
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def get_previous_period(start, end, shift_days=None):
    """Return previous inclusive period with the same length by default."""
    start_date, end_date = get_current_period(start, end)
    period_days = (end_date - start_date).days + 1
    shift = shift_days or period_days
    if shift <= 0:
        shift = period_days
    return start_date - timedelta(days=shift), end_date - timedelta(days=shift)


def align_time_series(data, date_field):
    """Copy rows and normalize their date field into business_date."""
    aligned = []
    for row in data or []:
        normalized = dict(row)
        normalized["business_date"] = to_business_date(
            {"created_at": row.get(date_field), **row}
        )
        aligned.append(normalized)
    return sorted(aligned, key=lambda item: item.get("business_date") or "")


def fill_missing_dates(data, start, end):
    """Return rows aligned to every date in [start, end], using None for gaps."""
    start_date, end_date = get_current_period(start, end)
    rows_by_date = {
        row.get("business_date") or to_business_date(row): dict(row)
        for row in data or []
        if row.get("business_date") or to_business_date(row)
    }

    filled = []
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        row = rows_by_date.get(key, {"business_date": key})
        row["business_date"] = key
        filled.append(row)
        current += timedelta(days=1)
    return filled
