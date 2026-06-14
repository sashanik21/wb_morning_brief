import re
from collections import defaultdict

FRAGRANCE_BRANDS = ["Tom Ford", "Chanel", "Dior", "Kilian", "Baccarat", "YSL"]
BRAND_ALIASES = {"Yves Saint Laurent": "YSL", "Maison Francis Kurkdjian": "Baccarat"}
TYPE_PATTERNS = {
    "eau_de_parfum": r"\b(edp|eau de parfum|парфюмерная вода)\b",
    "eau_de_toilette": r"\b(edt|eau de toilette|туалетная вода)\b",
    "perfume_oil": r"\b(масляные духи|масло|oil)\b",
    "perfume": r"\b(духи|parfum|perfume)\b",
}
FEMALE_HINTS = ("жен", "female", "woman", "women", "for her", "pour femme")
MALE_HINTS = ("муж", "male", "man", "men", "for him", "pour homme")
UNISEX_HINTS = ("унисекс", "unisex")
STOP_WORDS = (
    "масляные духи",
    "по мотивам",
    "парфюмерная вода",
    "туалетная вода",
    "духи",
    "eau de parfum",
    "eau de toilette",
    "edp",
    "edt",
    "perfume",
    "parfum",
    "oil",
)


def _to_number(value, default=0):
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace("%", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def _dynamic(current, previous):
    previous = _to_number(previous)
    if previous == 0:
        return 0
    return (_to_number(current) - previous) / previous * 100


def parse_perfume_title(title, brand_hint=""):
    raw_title = str(title or "").strip()
    normalized = raw_title
    volume_match = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:мл|ml)\b", normalized, re.I)
    volume_ml = None
    if volume_match:
        volume_ml = int(float(volume_match.group(1).replace(",", ".")))
        normalized = (
            normalized[: volume_match.start()] + normalized[volume_match.end() :]
        )

    lowered = normalized.lower()
    if any(hint in lowered for hint in UNISEX_HINTS):
        gender = "unisex"
    elif any(hint in lowered for hint in FEMALE_HINTS):
        gender = "female"
    elif any(hint in lowered for hint in MALE_HINTS):
        gender = "male"
    else:
        gender = "female"

    perfume_type = "perfume"
    for type_name, pattern in TYPE_PATTERNS.items():
        if re.search(pattern, lowered, re.I):
            perfume_type = type_name
            break

    brand = str(brand_hint or "").strip()
    for alias, canonical in BRAND_ALIASES.items():
        if alias.lower() in raw_title.lower() or alias.lower() == brand.lower():
            brand = canonical
    for cluster_brand in FRAGRANCE_BRANDS:
        if (
            cluster_brand.lower() in raw_title.lower()
            or cluster_brand.lower() == brand.lower()
        ):
            brand = cluster_brand
            normalized = re.sub(re.escape(cluster_brand), " ", normalized, flags=re.I)

    line = normalized
    for word in STOP_WORDS:
        line = re.sub(re.escape(word), " ", line, flags=re.I)
    for hint in FEMALE_HINTS + MALE_HINTS + UNISEX_HINTS:
        line = re.sub(re.escape(hint), " ", line, flags=re.I)
    line = re.sub(r"[^\wа-яА-ЯёЁ' -]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" -")
    if not line:
        line = raw_title or "Без линейки"

    return {
        "perfumeBrand": brand,
        "perfumeLine": line,
        "volumeMl": volume_ml,
        "gender": gender,
        "perfumeType": perfume_type,
        "fragranceCluster": brand if brand in FRAGRANCE_BRANDS else "Other",
    }


def enrich_record_with_perfume_fields(record):
    parsed = parse_perfume_title(
        record.get("title") or record.get("productName"),
        record.get("brandName") or record.get("brand"),
    )
    record.update(parsed)
    return record


def enrich_perfume_records(records):
    for record in records or []:
        if isinstance(record, dict):
            enrich_record_with_perfume_fields(record)
    return records


def _row_key(row):
    return (
        row.get("perfumeBrand") or row.get("brandName") or "",
        row.get("perfumeLine") or "",
    )


def _sku_role(row, ads_by_nm):
    nm_id = str(row.get("nmId") or "")
    ads = ads_by_nm.get(nm_id, {})
    traffic = _to_number(row.get("openCount"))
    revenue = _to_number(row.get("orderSum"))
    orders = _to_number(row.get("orderCount"))
    trend = _dynamic(
        row.get("orderCount"),
        row.get("pastOrderCount") or row.get("previousOrderCount"),
    )
    ad_spend = _to_number(ads.get("spend"))
    ads_share = ad_spend / revenue * 100 if revenue else 0
    organic_share = max(100 - ads_share, 0)
    if traffic == 0 and orders == 0 and revenue == 0:
        return "dead_sku"
    if trend <= -20:
        return "declining_sku"
    if ads_share >= 30:
        return "ads_dependent"
    if organic_share >= 85 and orders > 0:
        return "organic_leader"
    if revenue >= 5000 or orders >= 5:
        return "profit_driver"
    if traffic >= 100:
        return "traffic_driver"
    return "traffic_driver"


def build_perfume_intelligence(funnel_rows, ads_rows=None):
    ads_by_nm = defaultdict(lambda: {"spend": 0, "clicks": 0, "cpc": 0})
    for row in ads_rows or []:
        nm_id = str(row.get("nmId") or row.get("nm_id") or "")
        ads_by_nm[nm_id]["spend"] += _to_number(row.get("spend") or row.get("sum"))
        ads_by_nm[nm_id]["clicks"] += _to_number(row.get("clicks"))
        ads_by_nm[nm_id]["cpc"] = _to_number(row.get("cpc")) or ads_by_nm[nm_id]["cpc"]

    rows = enrich_perfume_records([dict(row) for row in funnel_rows or []])
    clusters = defaultdict(list)
    for row in rows:
        row["skuRole"] = _sku_role(row, ads_by_nm)
        clusters[_row_key(row)].append(row)

    insights = []
    volume_analytics = []
    for (brand, line), sku_rows in clusters.items():
        if not line or len(sku_rows) < 2:
            continue
        sorted_rows = sorted(
            sku_rows, key=lambda item: _to_number(item.get("volumeMl"))
        )
        for row in sorted_rows:
            volume_analytics.append(
                {
                    "brand": brand,
                    "line": line,
                    "volumeMl": row.get("volumeMl"),
                    "ctr": _to_number(row.get("ctr")),
                    "conversion": _to_number(row.get("cartToOrderPercent")),
                    "margin": _to_number(row.get("orderSum"))
                    / max(_to_number(row.get("orderCount")), 1),
                    "cpc": ads_by_nm.get(str(row.get("nmId") or ""), {}).get("cpc", 0),
                }
            )
        rising = [
            r
            for r in sku_rows
            if _dynamic(
                r.get("orderCount"),
                r.get("pastOrderCount") or r.get("previousOrderCount"),
            )
            >= 15
        ]
        falling = [
            r
            for r in sku_rows
            if _dynamic(
                r.get("orderCount"),
                r.get("pastOrderCount") or r.get("previousOrderCount"),
            )
            <= -15
        ]
        if rising and falling:
            insights.append(
                {
                    "type": "cannibalization",
                    "brand": brand,
                    "line": line,
                    "message": f"Возможна каннибализация между SKU одной линейки {line}: один объем растет, другой падает при пересечении спроса.",
                }
            )

    top = sorted(rows, key=lambda item: _to_number(item.get("orderSum")), reverse=True)[
        :3
    ]
    for row in top:
        title = row.get("perfumeLine") or row.get("title") or "SKU"
        volume = f" {row.get('volumeMl')} мл" if row.get("volumeMl") else ""
        role = row.get("skuRole")
        if role == "ads_dependent":
            text = f"{title}{volume} зависит от рекламы — органический спрос слабый."
        elif role == "declining_sku":
            text = f"{title}{volume} — ключевой SKU начал терять продажи."
        else:
            text = f"{title}{volume} остается важным SKU в роли {role}."
        insights.append({"type": "executive", "message": text, "skuRole": role})

    return {
        "rows": rows,
        "clusters": dict(clusters),
        "insights": insights,
        "volumeAnalytics": volume_analytics,
        "brandClusters": FRAGRANCE_BRANDS,
    }
