#!/usr/bin/env python3
"""
El Paso RentCast pipeline: sales prices + new construction trends.

Two independent pulls against the RentCast API, each writing to its own
append-only CSV with local dedup (no re-processing of records already
captured). Designed to run on a schedule via GitHub Actions:

    --mode sales           quarterly. Pulls /properties with a full
                            trailing-year saleDateRange window (default
                            365 days). Texas non-disclosure rules mean
                            only a small fraction of sales carry a public
                            price (~18/year observed for El Paso), so this
                            trades cadence for window width rather than
                            the reverse.
    --mode construction     monthly. Pulls /listings/sale filtered to
                            listingType=New Construction with a rolling
                            daysOld window (default 60 days).

Both windows are overridable per-run for ad hoc diagnostics without
editing code, e.g. to test how many disclosed sales exist over a much
longer lookback:

    python fetch_sales_and_construction.py --mode sales --sales-window-days 365

Usage:
    python fetch_sales_and_construction.py --mode sales
    python fetch_sales_and_construction.py --mode construction

Environment:
    RENTCAST_API_KEY   required

Output:
    data/el_paso_sales.csv
    data/el_paso_new_construction.csv
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import requests

API_BASE = "https://api.rentcast.io/v1"
DATA_DIR = Path(__file__).resolve().parent / "data"

CITY = "El Paso"
STATE = "TX"

# Rolling lookback windows. Both intentionally overlap the pull cadence
# so a record that posts late to county/MLS data still gets caught on
# a subsequent run instead of falling into a permanent blind spot.
# Sales defaults to a full year: Texas non-disclosure rules mean only a
# small fraction of actual sales carry a public price (confirmed via
# manual test: 18 disclosed sales over a trailing 365-day window for
# El Paso), so going wider doesn't meaningfully improve the catch rate
# and the practical ceiling is "however many disclose in a year."
DEFAULT_SALES_LOOKBACK_DAYS = 365        # quarterly cadence, ~4x overlap
DEFAULT_CONSTRUCTION_LOOKBACK_DAYS = 60  # monthly cadence, ~2x overlap

SALES_CSV = DATA_DIR / "el_paso_sales.csv"
CONSTRUCTION_CSV = DATA_DIR / "el_paso_new_construction.csv"

SALES_FIELDS = [
    "id", "formattedAddress", "zipCode", "county", "latitude", "longitude",
    "propertyType", "bedrooms", "bathrooms", "squareFootage", "lotSize",
    "yearBuilt", "lastSaleDate", "lastSalePrice", "price_per_sqft",
    "pulled_at",
]

CONSTRUCTION_FIELDS = [
    "id", "formattedAddress", "zipCode", "county", "latitude", "longitude",
    "propertyType", "bedrooms", "bathrooms", "squareFootage", "lotSize",
    "yearBuilt", "hoaFee", "status", "price", "price_per_sqft",
    "listedDate", "removedDate", "lastSeenDate", "daysOnMarket",
    "builderName", "subdivision", "mlsName",
    # feature/amenity fields, pulled from the "features" block when present
    "roomCount", "floorCount", "garage", "garageSpaces", "pool",
    "fireplace", "cooling", "coolingType", "heating", "heatingType",
    "exteriorType", "foundationType", "architectureType",
    "pulled_at",
]


def get_api_key() -> str:
    key = os.environ.get("RENTCAST_API_KEY")
    if not key:
        sys.exit("RENTCAST_API_KEY environment variable is not set.")
    return key


def api_get(path: str, params: dict, api_key: str) -> list:
    """Single paginated GET, up to 500 records, with basic retry on 429s."""
    headers = {"Accept": "application/json", "X-Api-Key": api_key}
    for attempt in range(3):
        resp = requests.get(f"{API_BASE}{path}", params=params, headers=headers, timeout=30)
        if resp.status_code == 429:
            wait = 5 * (attempt + 1)
            print(f"Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return []


def load_existing_keys(csv_path: Path, key_fields: tuple) -> dict:
    """
    Returns {record_id: composite_key_value_used_for_change_detection}
    so we can decide append / update-in-place / skip for each new record.
    """
    existing = {}
    if not csv_path.exists():
        return existing
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rid = row.get("id")
            if rid:
                existing[rid] = tuple(row.get(k, "") for k in key_fields)
    return existing


def rewrite_csv_with_updates(csv_path: Path, fields: list, updated_rows: dict):
    """
    updated_rows: {id: row_dict}. Rewrites the full file with these rows
    replacing any existing row of the same id, preserving all other rows.
    """
    all_rows = {}
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                all_rows[row["id"]] = row
    all_rows.update(updated_rows)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in all_rows.values():
            writer.writerow({k: row.get(k, "") for k in fields})


def fetch_sales(api_key: str, window_days: int):
    print(f"Fetching sales, saleDateRange=*:{window_days}")
    records = api_get(
        "/properties",
        {
            "city": CITY,
            "state": STATE,
            "saleDateRange": f"*:{window_days}",
            "limit": 500,
            "includeTotalCount": "true",
        },
        api_key,
    )
    print(f"Retrieved {len(records)} property records")

    # Only keep records where a sale price was actually disclosed.
    priced = [r for r in records if r.get("lastSalePrice") not in (None, "", 0)]
    print(f"{len(priced)} of {len(records)} have a disclosed lastSalePrice")

    existing = load_existing_keys(SALES_CSV, key_fields=("lastSaleDate", "lastSalePrice"))
    pulled_at = time.strftime("%Y-%m-%d")
    to_write = {}

    for r in priced:
        rid = r.get("id")
        if not rid:
            continue
        change_key = (str(r.get("lastSaleDate", "")), str(r.get("lastSalePrice", "")))
        if rid in existing and existing[rid] == change_key:
            continue  # already have this exact sale record, skip

        sqft = r.get("squareFootage")
        price = r.get("lastSalePrice")
        pps = round(price / sqft, 2) if sqft and price else ""

        to_write[rid] = {
            "id": rid,
            "formattedAddress": r.get("formattedAddress", ""),
            "zipCode": r.get("zipCode", ""),
            "county": r.get("county", ""),
            "latitude": r.get("latitude", ""),
            "longitude": r.get("longitude", ""),
            "propertyType": r.get("propertyType", ""),
            "bedrooms": r.get("bedrooms", ""),
            "bathrooms": r.get("bathrooms", ""),
            "squareFootage": sqft or "",
            "lotSize": r.get("lotSize", ""),
            "yearBuilt": r.get("yearBuilt", ""),
            "lastSaleDate": r.get("lastSaleDate", ""),
            "lastSalePrice": price or "",
            "price_per_sqft": pps,
            "pulled_at": pulled_at,
        }

    print(f"{len(to_write)} new or updated sale records to write")
    if to_write:
        rewrite_csv_with_updates(SALES_CSV, SALES_FIELDS, to_write)
    print(f"Done. {SALES_CSV} updated.")


def fetch_construction(api_key: str, window_days: int):
    print(f"Fetching new construction, daysOld=*:{window_days}")
    records = api_get(
        "/listings/sale",
        {
            "city": CITY,
            "state": STATE,
            "listingType": "New Construction",
            "daysOld": f"*:{window_days}",
            "status": "Active",
            "limit": 500,
            "includeTotalCount": "true",
        },
        api_key,
    )
    print(f"Retrieved {len(records)} active new-construction listings")

    # Also pull inactive/sold in the same window so we can track completions,
    # not just active inventory. Same call budget impact: one extra request.
    inactive = api_get(
        "/listings/sale",
        {
            "city": CITY,
            "state": STATE,
            "listingType": "New Construction",
            "daysOld": f"*:{window_days}",
            "status": "Inactive",
            "limit": 500,
        },
        api_key,
    )
    print(f"Retrieved {len(inactive)} inactive/sold new-construction listings")
    records = records + inactive

    existing = load_existing_keys(CONSTRUCTION_CSV, key_fields=("status", "price", "lastSeenDate"))
    pulled_at = time.strftime("%Y-%m-%d")
    to_write = {}

    for r in records:
        rid = r.get("id")
        if not rid:
            continue
        change_key = (str(r.get("status", "")), str(r.get("price", "")), str(r.get("lastSeenDate", "")))
        if rid in existing and existing[rid] == change_key:
            continue

        sqft = r.get("squareFootage")
        price = r.get("price")
        pps = round(price / sqft, 2) if sqft and price else ""

        hoa = r.get("hoa") or {}
        builder = r.get("builder") or {}
        # "features" is documented on the /properties model; RentCast's
        # /listings/sale records include it when the underlying county/MLS
        # data has it, but coverage is inconsistent, especially for brand
        # new construction that hasn't been fully processed by the county
        # yet. Fields simply come back blank when unavailable.
        features = r.get("features") or {}

        listed = r.get("listedDate", "")
        last_seen = r.get("lastSeenDate", "")
        days_on_market = ""
        if listed and last_seen:
            try:
                from datetime import datetime
                d1 = datetime.fromisoformat(listed.replace("Z", "+00:00"))
                d2 = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                days_on_market = (d2 - d1).days
            except ValueError:
                pass

        to_write[rid] = {
            "id": rid,
            "formattedAddress": r.get("formattedAddress", ""),
            "zipCode": r.get("zipCode", ""),
            "county": r.get("county", ""),
            "latitude": r.get("latitude", ""),
            "longitude": r.get("longitude", ""),
            "propertyType": r.get("propertyType", ""),
            "bedrooms": r.get("bedrooms", ""),
            "bathrooms": r.get("bathrooms", ""),
            "squareFootage": sqft or "",
            "lotSize": r.get("lotSize", ""),
            "yearBuilt": r.get("yearBuilt", ""),
            "hoaFee": hoa.get("fee", ""),
            "status": r.get("status", ""),
            "price": price or "",
            "price_per_sqft": pps,
            "listedDate": listed,
            "removedDate": r.get("removedDate", ""),
            "lastSeenDate": last_seen,
            "daysOnMarket": days_on_market,
            "builderName": builder.get("name", ""),
            "subdivision": builder.get("development", "") or r.get("subdivision", ""),
            "mlsName": r.get("mlsName", ""),
            "roomCount": features.get("roomCount", ""),
            "floorCount": features.get("floorCount", ""),
            "garage": features.get("garage", ""),
            "garageSpaces": features.get("garageSpaces", ""),
            "pool": features.get("pool", ""),
            "fireplace": features.get("fireplace", ""),
            "cooling": features.get("cooling", ""),
            "coolingType": features.get("coolingType", ""),
            "heating": features.get("heating", ""),
            "heatingType": features.get("heatingType", ""),
            "exteriorType": features.get("exteriorType", ""),
            "foundationType": features.get("foundationType", ""),
            "architectureType": features.get("architectureType", ""),
            "pulled_at": pulled_at,
        }

    print(f"{len(to_write)} new or updated construction records to write")
    if to_write:
        rewrite_csv_with_updates(CONSTRUCTION_CSV, CONSTRUCTION_FIELDS, to_write)
    print(f"Done. {CONSTRUCTION_CSV} updated.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["sales", "construction"], required=True)
    parser.add_argument(
        "--sales-window-days", type=int, default=DEFAULT_SALES_LOOKBACK_DAYS,
        help=f"Rolling lookback window in days for the sales pull (default {DEFAULT_SALES_LOOKBACK_DAYS}).",
    )
    parser.add_argument(
        "--construction-window-days", type=int, default=DEFAULT_CONSTRUCTION_LOOKBACK_DAYS,
        help=f"Rolling lookback window in days for the construction pull (default {DEFAULT_CONSTRUCTION_LOOKBACK_DAYS}).",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "sales":
        fetch_sales(api_key, args.sales_window_days)
    else:
        fetch_construction(api_key, args.construction_window_days)


if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
