"""Shared business-date and period helpers for analytics pipelines."""

from datetime import date, datetime, timedelta

BUSINESS_DATE_FIELDS = (
    "business_date",
    "businessDate",
    "report_date",
    "reportDate",
    "campaign_date",
    "campaignDate",
    "snapshot_date",
    "snapshotDate",
    "date",
    "created_at",
    "createdAt",
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

    Analytics date fields are preferred over technical timestamps. created_at is
    accepted only as a last-resort compatibility field for legacy rows.
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


def build_date_range(start, end):
    """Return an inclusive list of dates from start to end."""
    start_date, end_date = get_current_period(start, end)
    days = (end_date - start_date).days + 1
    return [start_date + timedelta(days=offset) for offset in range(days)]


def _align_records(records, date_column):
    rows = []
    row_dates = []
    for row in records or []:
        normalized = dict(row)
        row_date = _parse_date(normalized.get(date_column))
        if row_date is None:
            row_date = _parse_date(to_business_date(normalized))
        if row_date is None:
            continue
        normalized[date_column] = row_date.isoformat()
        normalized["business_date"] = row_date.isoformat()
        rows.append(normalized)
        row_dates.append(row_date)

    if not row_dates:
        return rows

    known_fields = []
    for row in rows:
        for field in row:
            if field not in known_fields:
                known_fields.append(field)

    rows_by_date = {row[date_column]: row for row in rows}
    aligned = []
    for current_date in build_date_range(min(row_dates), max(row_dates)):
        key = current_date.isoformat()
        row = rows_by_date.get(key)
        if row is None:
            row = {field: None for field in known_fields}
            row[date_column] = key
            row["business_date"] = key
        aligned.append(row)
    return aligned


def align_time_series(df, date_column="report_date"):
    """Align data to a continuous daily calendar without zero-filling gaps.

    The date column is normalized to DATE, a full min..max calendar is built,
    and source data is left-joined onto that calendar. Missing values stay NULL
    (``None``/``NaN`` depending on the input container), never zero.
    """
    if hasattr(df, "copy") and hasattr(df, "columns") and hasattr(df, "merge"):
        import pandas as pd

        if date_column not in df.columns:
            raise KeyError(f"{date_column} is required to align time series")

        data = df.copy()
        data[date_column] = pd.to_datetime(data[date_column], errors="coerce").dt.date
        data = data.dropna(subset=[date_column])
        if data.empty:
            return data

        calendar = pd.DataFrame(
            {date_column: build_date_range(data[date_column].min(), data[date_column].max())}
        )
        return calendar.merge(data, on=date_column, how="left")

    return _align_records(df, date_column)


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
