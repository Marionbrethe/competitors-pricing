"""
Export PriceRecords to CSV.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scraper.models import PriceRecord


def to_csv(records: list[PriceRecord], output_dir: str = "output", city: str = "") -> str:
    """
    Write *records* to a timestamped CSV file inside *output_dir*.

    Returns the path to the written file.
    """
    if not records:
        print("No records to export.")
        return ""

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    rows = [r.model_dump() for r in records]
    df = pd.DataFrame(rows)

    # Friendly column order
    col_order = ["company", "city", "location_name", "address", "size", "price", "currency", "price_unit", "scraped_at"]
    df = df[[c for c in col_order if c in df.columns]]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = city.lower().replace(" ", "_") if city else "all_cities"
    filename = f"{label}_{timestamp}.csv"
    path = os.path.join(output_dir, filename)

    df.to_csv(path, index=False)
    print(f"Saved {len(records)} record(s) → {path}")
    return path
