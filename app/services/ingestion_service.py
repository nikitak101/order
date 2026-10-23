"""Validate incoming order records and save accepted or rejected results."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, OrderLine, RejectedRecord, Shipment
from app.schemas import OrderIn

logger = logging.getLogger("app.ingestion")


def _error_details(error: ValidationError) -> list[dict[str, Any]]:
    """Convert Pydantic errors to readable field paths and messages."""
    details: list[dict[str, Any]] = []
    for issue in error.errors():
        location = ".".join(str(part) for part in issue["loc"]) or "record"
        details.append({"field": location, "message": issue["msg"]})
    return details


def _save_rejection(
    db: Session,
    record: Any,
    order_id: str | None,
    errors: list[dict[str, Any]],
    source_format: str,
    source_name: str,
    batch_id: str,
) -> None:
    """Persist one rejection independently so other records can continue."""
    raw_data = json.dumps(record, ensure_ascii=False, default=str)
    db.add(
        RejectedRecord(
            batch_id=batch_id,
            order_id=order_id,
            source_format=source_format,
            source_name=source_name,
            raw_data=raw_data,
            errors=json.dumps(errors, ensure_ascii=False),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def process_records(
    db: Session,
    records: list[Any],
    source_format: str,
    source_name: str,
    batch_id: str,
) -> dict[str, Any]:
    """Validate and persist records independently within one ingestion batch."""
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []

    logger.info(
        "batch_id=%s received record_count=%d source_format=%s",
        batch_id,
        len(records),
        source_format,
    )

    for record in records:
        order_id_value = record.get("order_id") if isinstance(record, dict) else None
        order_id = order_id_value if isinstance(order_id_value, str) else None
        try:
            order_input = OrderIn.model_validate(record)
        except ValidationError as error:
            errors = _error_details(error)
            _save_rejection(
                db, record, order_id, errors, source_format, source_name, batch_id
            )
            rejected.append({"order_id": order_id, "errors": errors})
            logger.error(
                "batch_id=%s rejected order_id=%s reasons=%s",
                batch_id,
                order_id or "<missing>",
                "; ".join(f"{item['field']}: {item['message']}" for item in errors),
            )
            continue

        order = Order(
            order_id=order_input.order_id,
            customer_name=order_input.customer_name,
            order_date=order_input.order_date,
            destination=order_input.destination,
            items=[
                OrderLine(sku=item.sku, qty=item.qty) for item in order_input.items
            ],
            shipment=Shipment(status="CREATED"),
        )
        db.add(order)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            errors = [
                {
                    "field": "order_id",
                    "message": f"order_id '{order_input.order_id}' already exists",
                }
            ]
            _save_rejection(
                db,
                record,
                order_input.order_id,
                errors,
                source_format,
                source_name,
                batch_id,
            )
            rejected.append({"order_id": order_input.order_id, "errors": errors})
            logger.error(
                "batch_id=%s rejected order_id=%s reasons=duplicate order_id",
                batch_id,
                order_input.order_id,
            )
            continue

        accepted.append(order_input.order_id)
        logger.info("batch_id=%s accepted order_id=%s", batch_id, order_input.order_id)

    return {"batch_id": batch_id, "accepted": accepted, "rejected": rejected}
