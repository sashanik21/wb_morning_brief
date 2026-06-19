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


def normalize_report_date_series(values):
    """Normalize report_date-like values to Python DATE values using pandas semantics."""
    import pandas as pd

    return pd.to_datetime(values, errors="coerce").dt.date


def normalize_selected_date(value):
    """Normalize a selected date to a Python DATE value using pandas semantics."""
    if value in (None, ""):
        return None

    import pandas as pd

    parsed = pd.to_datetime(value, errors="coerce")
    if parsed is None or getattr(parsed, "isna", lambda: False)():
        return None
    try:
        if pd.isna(parsed):
            return None
    except TypeError:
        pass
    return parsed.date()


def date_debug_diagnostics(rows, selected_date, date_field="report_date", filtered_count=None):
    """Return structured diagnostics for DATE == DATE filters.

    The helper intentionally avoids changing business metrics. It only reports
    why a selected-date filter produced no rows or why fallback was needed.
    """
    import pandas as pd

    selected = normalize_selected_date(selected_date)
    date_fields = [field.strip() for field in str(date_field).split(",") if field.strip()]
    raw_values = [
        _first_present(row, date_fields) if isinstance(row, dict) else None
        for row in rows or []
    ]
    raw_series = pd.Series(raw_values, dtype="object")
    normalized = normalize_report_date_series(raw_series) if len(raw_series) else pd.Series([], dtype="object")
    valid_dates = [value for value in normalized.tolist() if pd.notna(value)]
    before_count = len(rows or [])
    if filtered_count is None:
        filtered_count = sum(1 for value in valid_dates if selected is not None and value == selected)

    range_count = 0
    if selected is not None:
        start = selected - timedelta(days=3)
        end = selected + timedelta(days=3)
        range_count = sum(1 for value in valid_dates if start <= value <= end)

    min_date = min(valid_dates).isoformat() if valid_dates else None
    max_date = max(valid_dates).isoformat() if valid_dates else None
    reason = None
    if filtered_count == 0:
        if before_count == 0:
            reason = "NO_DATA_IN_RANGE"
        elif not valid_dates and raw_values:
            reason = "DATATYPE_MISMATCH"
        elif selected is not None and range_count > 0:
            reason = "TIMEZONE_SHIFT"
        elif selected is not None and valid_dates and (selected < min(valid_dates) or selected > max(valid_dates)):
            reason = "NO_DATA_IN_RANGE"
        else:
            reason = "NO_DATA_FOR_SELECTED_DATE"

    return {
        "reason": reason,
        "selected_date": selected.isoformat() if selected else None,
        "min_report_date": min_date,
        "max_report_date": max_date,
        "rows_before_filter": before_count,
        "rows_after_filter": filtered_count,
        "report_date_dtype": str(raw_series.dtype),
        "rows_in_plus_minus_3_days": range_count,
        "date_field": date_field,
    }


def closest_available_date(available_dates, selected_date, max_shift_days=3):
    """Return closest available date within ±max_shift_days, or None."""
    selected = normalize_selected_date(selected_date)
    if selected is None:
        return None
    normalized = [normalize_selected_date(value) for value in available_dates or []]
    candidates = [value for value in normalized if value is not None and abs((value - selected).days) <= max_shift_days]
    if not candidates:
        return None
    return min(candidates, key=lambda value: (abs((value - selected).days), value)).isoformat()


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
