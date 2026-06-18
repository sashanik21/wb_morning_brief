import os
import re
import unicodedata
from collections import Counter


def normalize_ads_key(value):
    text = str(value or "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"мл\.?|ml\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\s_\-]+", "", text)
    return text


def _nm_id(row):
    for key in ("nmId", "nmID", "nm_id", "nm"):
        value = row.get(key) if isinstance(row, dict) else None
        if value not in (None, ""):
            try:
                return int(float(str(value)))
            except (TypeError, ValueError):
                return None
    return None


def _product_row(row):
    product = row.get("product") if isinstance(row, dict) else None
    if isinstance(product, dict):
        return {**row, **product}
    return row if isinstance(row, dict) else {}


def _first(row, keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _product_indexes(products):
    by_nm = {}
    by_vendor = {}
    by_norm_vendor = {}
    product_list = []
    for row in products or []:
        product = _product_row(row)
        nm_id = _nm_id(product)
        vendor = _first(product, ["vendorCode", "vendor_code", "supplierArticle"])
        title = _first(product, ["title", "productName", "product_name", "name"])
        item = {"nmId": nm_id, "vendorCode": vendor, "title": title}
        if nm_id is not None:
            by_nm[nm_id] = item
        if vendor:
            by_vendor[str(vendor)] = item
            by_norm_vendor[normalize_ads_key(vendor)] = item
        product_list.append(item)
    return by_nm, by_vendor, by_norm_vendor, product_list


def _match_by_title(campaign_name, product_list):
    normalized_name = normalize_ads_key(campaign_name)
    matches = [
        item
        for item in product_list
        if item.get("title") and normalize_ads_key(item["title"]) in normalized_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None, "MULTI_MATCH_CONFLICT"
    return None


def attribute_ads_rows(ads_rows, products):
    by_nm, by_vendor, by_norm_vendor, product_list = _product_indexes(products)
    debug_rows = []
    matched_rows = []
    strategy_counts = Counter()

    for row in ads_rows or []:
        row = dict(row)
        api_nm_id = _nm_id(row)
        vendor = row.get("vendorCode") or ""
        campaign_name = row.get("campaignName") or ""
        matched = None
        strategy = "failed"
        confidence = 0
        status = "UNMATCHED"
        reason = "MATCH_FAILED"

        if api_nm_id is not None and api_nm_id in by_nm:
            matched = by_nm[api_nm_id]
            strategy = "nmId"
            confidence = 1.0
        elif vendor and str(vendor) in by_vendor:
            matched = by_vendor[str(vendor)]
            strategy = "vendorCode"
            confidence = 0.95
        elif vendor and normalize_ads_key(vendor) in by_norm_vendor:
            matched = by_norm_vendor[normalize_ads_key(vendor)]
            strategy = "normalized vendorCode"
            confidence = 0.9
        else:
            title_vendor_matches = [
                item
                for item in product_list
                if item.get("vendorCode")
                and normalize_ads_key(item["vendorCode"])
                in normalize_ads_key(campaign_name)
            ]
            if len(title_vendor_matches) == 1:
                matched = title_vendor_matches[0]
                strategy = "campaign title vendorCode"
                confidence = 0.8
            elif len(title_vendor_matches) > 1:
                reason = "MULTI_MATCH_CONFLICT"
            else:
                title_match = _match_by_title(campaign_name, product_list)
                if isinstance(title_match, tuple):
                    reason = title_match[1]
                elif title_match:
                    matched = title_match
                    strategy = "fuzzy title"
                    confidence = 0.65

        if matched:
            row["nmId"] = matched.get("nmId")
            row["vendorCode"] = matched.get("vendorCode") or vendor
            status = "MATCHED"
            reason = ""
        elif api_nm_id is None:
            reason = (
                "NO_NMID_IN_RESPONSE" if not vendor and not campaign_name else reason
            )

        row["matchStrategy"] = strategy
        row["matchConfidence"] = confidence
        row["matchStatus"] = status
        row["matchReason"] = reason
        strategy_counts[strategy] += 1
        debug_rows.append(
            {
                "campaignId": row.get("campaignId"),
                "campaignName": campaign_name,
                "advertId": row.get("advertId") or row.get("campaignId"),
                "apiNmId": api_nm_id or "",
                "matchedNmId": row.get("nmId") or "",
                "matchedVendorCode": row.get("vendorCode") or "",
                "matchStrategy": strategy,
                "matchConfidence": confidence,
                "matchStatus": status,
                "reason": reason,
            }
        )
        matched_rows.append(row)

    matched_count = sum(
        1 for row in matched_rows if row.get("matchStatus") == "MATCHED"
    )
    print(
        "ADS MATCHING: "
        f"rows={len(ads_rows or [])} matched={matched_count} "
        f"unmatched={len(ads_rows or []) - matched_count}"
    )
    if os.getenv("LOG_LEVEL", "summary").strip().lower() == "debug":
        print("MATCH STRATEGIES:")
        for name in (
            "nmId",
            "vendorCode",
            "normalized vendorCode",
            "campaign title vendorCode",
            "fuzzy title",
            "failed",
        ):
            print(f"{name}: {strategy_counts.get(name, 0)}")
    return matched_rows, debug_rows
