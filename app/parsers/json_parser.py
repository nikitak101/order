"""Convert JSON order payloads into standard dictionaries."""

import json
from typing import Any


def parse_json(data: bytes) -> list[dict[str, Any]]:
    """Decode UTF-8 JSON containing one order or a list of orders."""
    text = data.decode("utf-8")
    payload = json.loads(text)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(record, dict) for record in payload):
        return payload
    raise ValueError("JSON must contain an order object or a list of order objects.")
