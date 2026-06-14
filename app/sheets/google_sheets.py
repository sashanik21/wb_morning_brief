"""Deprecated compatibility wrapper for the old storage import path.

The primary storage implementation now lives in app.storage.
"""

from app.storage.stub_storage import (
    create_tasks,
    get_change_log,
    get_products,
    get_sellers,
)

__all__ = [
    "create_tasks",
    "get_change_log",
    "get_products",
    "get_sellers",
]
