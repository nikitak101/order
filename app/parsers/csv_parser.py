"""Convert one-item-per-row CSV files into standard order dictionaries."""

import csv
import io
import re
from typing import Any


REQUIRED_COLUMNS = {
    "order_id",
    "customer_name",
    "order_date",
    "destination",
    "sku",
    "qty",
}


def _convert_quantity(value: str | None) -> Any:
    """Convert integer text to an integer while preserving invalid text."""
    if value is not None and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    return value


def parse_csv(data: bytes) -> list[dict[str, Any]]:
    """Decode UTF-8-SIG CSV and group item rows by order ID."""
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ValueError("CSV file is empty or has no header row.")
    if not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
        missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames))
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}.")

    grouped: dict[str, dict[str, Any]] = {}
    for row in reader:
        if None in row:
            raise ValueError("CSV row contains more values than the header.")
        order_id = row.get("order_id")
        group_key = order_id or ""
        if group_key not in grouped:
            grouped[group_key] = {
                "order_id": order_id,
                "customer_name": row.get("customer_name"),
                "order_date": row.get("order_date"),
                "destination": row.get("destination"),
                "items": [],
            }
        grouped[group_key]["items"].append(
            {
                "sku": row.get("sku"),
                "qty": _convert_quantity(row.get("qty")),
            }
        )

    if not grouped:
        raise ValueError("CSV file does not contain any order rows.")
    return list(grouped.values())
