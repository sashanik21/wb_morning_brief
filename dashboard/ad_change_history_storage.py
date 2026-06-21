"""Dashboard-safe storage helpers for ad change history imports."""

from datetime import date, datetime
from decimal import Decimal

from supabase_client import get_supabase_client


def _get_client():
    return get_supabase_client()


def _execute_read(query, table_name):
    try:
        response = query.execute()
    except Exception as error:
        print(f"WARNING: Supabase read failed for {table_name}: {error}")
        return []

    return response.data or []


def _execute_write(query, table_name):
    try:
        query.execute()
    except Exception as error:
        error_message = str(error)
        print(f"WARNING: Supabase write failed for {table_name}: {error}")
        return False, error_message

    return True, None


def _to_int(value):
    if value in (None, ""):
        return None

    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _string_or_none(value):
    if value in (None, ""):
        return None

    return str(value)


def _json_safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_safe_row(row):
    return {key: _json_safe_value(value) for key, value in (row or {}).items()}


def _drop_empty_required(rows, required_keys):
    return [
        row for row in rows if all(row.get(key) is not None for key in required_keys)
    ]


def _ad_change_history_text(value):
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _ad_change_history_dedupe_part(value):
    if value in (None, ""):
        return ""
    return str(value).strip().lower()


def _ad_change_history_dedupe_key(row):
    parts = [
        _ad_change_history_dedupe_part(row.get("seller_id")),
        _ad_change_history_dedupe_part(row.get("campaign_id")),
        _ad_change_history_dedupe_part(row.get("nm_id")),
        _ad_change_history_dedupe_part(row.get("cluster")),
        _ad_change_history_dedupe_part(row.get("change_type")),
        _ad_change_history_dedupe_part(row.get("old_value")),
        _ad_change_history_dedupe_part(row.get("new_value")),
        _ad_change_history_dedupe_part(row.get("changed_at")),
        _ad_change_history_dedupe_part(row.get("source")),
    ]
    return "|".join(parts)


def _normalize_ad_change_history_row(row, seller_id, import_id=None):
    normalized = {
        "import_id": import_id,
        "seller_id": _string_or_none(seller_id),
        "campaign_id": _to_int(row.get("campaign_id")),
        "nm_id": _to_int(row.get("nm_id")),
        "change_type": _ad_change_history_text(row.get("change_type")),
        "cluster": _ad_change_history_text(row.get("cluster")),
        "old_value": _ad_change_history_text(row.get("old_value")),
        "new_value": _ad_change_history_text(row.get("new_value")),
        "source": _ad_change_history_text(row.get("source")),
        "changed_at": _ad_change_history_text(row.get("changed_at")),
        "raw_row": _json_safe_row(row.get("raw_row") or row),
    }
    normalized["dedupe_key"] = _ad_change_history_dedupe_key(normalized)
    return _json_safe_row(normalized)


def _fetch_existing_ad_change_history_keys(dedupe_keys):
    if not dedupe_keys:
        return set()

    existing_keys = set()
    for start in range(0, len(dedupe_keys), 500):
        chunk = dedupe_keys[start : start + 500]
        rows = _execute_read(
            _get_client()
            .table("wb_ad_change_history")
            .select("dedupe_key")
            .in_("dedupe_key", chunk),
            "wb_ad_change_history",
        )
        existing_keys.update(row.get("dedupe_key") for row in rows if row.get("dedupe_key"))
    return existing_keys


def _find_existing_ad_change_history_import(seller_id, file_hash):
    rows = _execute_read(
        _get_client()
        .table("wb_ad_change_history_imports")
        .select("id,rows_total,rows_inserted,rows_skipped,error_message")
        .eq("seller_id", _string_or_none(seller_id))
        .eq("source_file_hash", file_hash)
        .limit(1),
        "wb_ad_change_history_imports",
    )
    return rows[0] if rows else None


def _create_ad_change_history_import(seller_id, file_name, file_hash, rows_total):
    payload = {
        "seller_id": _string_or_none(seller_id),
        "source_file_name": file_name,
        "source_file_hash": file_hash,
        "rows_total": rows_total,
        "rows_inserted": 0,
        "rows_skipped": 0,
        "status": "uploaded",
        "error_message": None,
        "uploaded_at": datetime.utcnow().isoformat(),
    }
    try:
        response = (
            _get_client()
            .table("wb_ad_change_history_imports")
            .insert(payload)
            .execute()
        )
        data = response.data or []
        return data[0].get("id") if data else None, None
    except Exception as error:
        error_message = str(error)
        print(f"WARNING: Supabase write failed for wb_ad_change_history_imports: {error}")
        return None, error_message


def _update_ad_change_history_import(import_id, rows_inserted, rows_skipped, error_message=None):
    if import_id is None:
        return
    payload = {
        "rows_inserted": rows_inserted,
        "rows_skipped": rows_skipped,
        "status": "failed" if error_message else "parsed",
        "error_message": error_message,
        "parsed_at": datetime.utcnow().isoformat(),
    }
    _execute_write(
        _get_client()
        .table("wb_ad_change_history_imports")
        .update(payload)
        .eq("id", import_id),
        "wb_ad_change_history_imports",
    )


def save_ad_change_history_rows(seller_id, rows, rows_total=None, import_id=None):
    total_rows = len(rows or []) if rows_total is None else rows_total
    normalized_rows = [
        _normalize_ad_change_history_row(row, seller_id, import_id=import_id)
        for row in rows or []
    ]
    normalized_rows = _drop_empty_required(
        normalized_rows, ["seller_id", "campaign_id", "changed_at", "dedupe_key"]
    )

    seen_keys = set()
    unique_rows = []
    for row in normalized_rows:
        dedupe_key = row.get("dedupe_key")
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        unique_rows.append(row)

    existing_keys = _fetch_existing_ad_change_history_keys(
        [row.get("dedupe_key") for row in unique_rows]
    )
    rows_to_insert = [
        row for row in unique_rows if row.get("dedupe_key") not in existing_keys
    ]
    rows_skipped = total_rows - len(rows_to_insert)

    error_message = None
    if rows_to_insert:
        success, error_message = _execute_write(
            _get_client()
            .table("wb_ad_change_history")
            .insert(rows_to_insert),
            "wb_ad_change_history",
        )
        if not success:
            rows_skipped = total_rows
            rows_to_insert = []

    return len(rows_to_insert), rows_skipped, error_message


def save_ad_change_history_import(seller_id, file_name, file_hash, rows, rows_total):
    existing_import = _find_existing_ad_change_history_import(seller_id, file_hash)
    if existing_import:
        return {
            "import_id": existing_import.get("id"),
            "rows_total": rows_total,
            "rows_inserted": 0,
            "rows_skipped": rows_total,
            "error": None,
        }

    import_id, create_error = _create_ad_change_history_import(
        seller_id, file_name, file_hash, rows_total
    )
    if create_error:
        return {
            "import_id": None,
            "rows_total": rows_total,
            "rows_inserted": 0,
            "rows_skipped": rows_total,
            "error": create_error,
        }

    rows_inserted, rows_skipped, error_message = save_ad_change_history_rows(
        seller_id=seller_id,
        rows=rows,
        rows_total=rows_total,
        import_id=import_id,
    )

    _update_ad_change_history_import(
        import_id, rows_inserted, rows_skipped, error_message=error_message
    )
    return {
        "import_id": import_id,
        "rows_total": rows_total,
        "rows_inserted": rows_inserted,
        "rows_skipped": rows_skipped,
        "error": error_message,
    }
