"""Convert an X12 850 purchase order into the standard order dictionary."""

import re
from typing import Any


def _segment_delimiters(text: str) -> tuple[str, str]:
    """Read ISA's fixed-position element and segment delimiters when present."""
    if not text.startswith("ISA"):
        return "*", "~"
    if len(text) <= 105:
        raise ValueError("X12 ISA segment is too short to declare delimiters.")

    element_separator = text[3]
    segment_terminator = text[105]
    if element_separator.isspace() or segment_terminator.isspace():
        raise ValueError("X12 ISA segment declares an invalid delimiter.")
    if element_separator == segment_terminator:
        raise ValueError("X12 element separator and segment terminator must differ.")
    return element_separator, segment_terminator


def _segment_elements(segment: str, separator: str) -> list[str]:
    """Split one segment and remove whitespace around its element values."""
    return [element.strip() for element in segment.strip().split(separator)]


def _format_date(value: str | None) -> Any:
    """Convert an eight-digit BEG date to YYYY-MM-DD without validating it."""
    if value is not None and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _format_quantity(value: str | None) -> Any:
    """Convert integer quantity text while leaving invalid text for Pydantic."""
    if value is not None and re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    return value


def parse_x12(data: bytes) -> list[dict[str, Any]]:
    """Parse X12 850 segments into the pipeline's plain order dictionaries."""
    text = data.decode("utf-8")
    element_separator, segment_terminator = _segment_delimiters(text)
    segments = [
        _segment_elements(segment, element_separator)
        for segment in text.split(segment_terminator)
        if segment.strip()
    ]

    records: list[dict[str, Any]] = []
    current_order: dict[str, Any] | None = None

    for elements in segments:
        segment_id = elements[0] if elements else ""

        if segment_id == "BEG":
            # BEG starts a purchase order: element 3 is its PO number and
            # element 5 is the purchase-order date in YYYYMMDD format.
            if current_order is not None:
                records.append(current_order)
            current_order = {
                "order_id": elements[3] if len(elements) > 3 else None,
                "customer_name": None,
                "order_date": _format_date(elements[5] if len(elements) > 5 else None),
                "destination": None,
                "items": [],
            }
        elif segment_id == "N1" and len(elements) > 2 and elements[1] == "ST":
            # N1 identifies a party; ST means "ship to", so its name is the
            # customer/recipient name used by this integration.
            if current_order is None:
                raise ValueError("X12 N1 segment appeared before a BEG segment.")
            current_order["customer_name"] = elements[2]
        elif segment_id == "N4":
            # N4 carries geographic details; its fourth data element is the
            # country code, which this project uses as the destination.
            if current_order is None:
                raise ValueError("X12 N4 segment appeared before a BEG segment.")
            current_order["destination"] = elements[4] if len(elements) > 4 else None
        elif segment_id == "PO1":
            # PO1 is a line item: element 2 is quantity and element 7 is SKU.
            if current_order is None:
                raise ValueError("X12 PO1 segment appeared before a BEG segment.")
            current_order["items"].append(
                {
                    "sku": elements[7] if len(elements) > 7 else None,
                    "qty": _format_quantity(elements[2] if len(elements) > 2 else None),
                }
            )

    if current_order is not None:
        records.append(current_order)
    if not records:
        raise ValueError("X12 document does not contain a BEG purchase-order segment.")
    return records
