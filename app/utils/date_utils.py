"""Compatibility wrappers around the shared Date Engine."""

from app.core.date_engine import (  # noqa: F401
    align_time_series,
    fill_missing_dates,
    get_current_period,
    get_previous_period,
    to_business_date,
)
