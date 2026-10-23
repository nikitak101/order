"""Convert XML order files into standard dictionaries using defusedxml."""

import re
from typing import Any

from defusedxml import ElementTree


def _text_or_none(element: Any, path: str) -> str | None:
    child = element.find(path)
    return child.text.strip() if child is not None and child.text is not None else None


def _convert_quantity(value: str | None) -> Any:
    """Convert integer text to an integer while preserving invalid text."""
    if value is not None and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    return value


def parse_xml(data: bytes) -> list[dict[str, Any]]:
    """Parse an orders XML document without performing schema validation."""
    text = data.decode("utf-8")
    root = ElementTree.fromstring(text)
    if root.tag != "orders":
        raise ValueError("XML root element must be <orders>.")

    records: list[dict[str, Any]] = []
    for order_element in root.findall("order"):
        items_element = order_element.find("items")
        items = []
        if items_element is not None:
            for item_element in items_element.findall("item"):
                items.append(
                    {
                        "sku": _text_or_none(item_element, "sku"),
                        "qty": _convert_quantity(_text_or_none(item_element, "qty")),
                    }
                )
        records.append(
            {
                "order_id": _text_or_none(order_element, "order_id"),
                "customer_name": _text_or_none(order_element, "customer_name"),
                "order_date": _text_or_none(order_element, "order_date"),
                "destination": _text_or_none(order_element, "destination"),
                "items": items,
            }
        )

    if not records:
        raise ValueError("XML document does not contain any <order> elements.")
    return records
