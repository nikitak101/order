"""Shipment status transitions and history persistence."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, Shipment, ShipmentEvent

ALLOWED_STATUSES = {
    "CREATED",
    "PICKED",
    "SHIPPED",
    "IN_TRANSIT",
    "DELIVERED",
    "RETURNED",
}

ALLOWED_TRANSITIONS = {
    "CREATED": {"PICKED"},
    "PICKED": {"SHIPPED"},
    "SHIPPED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"DELIVERED"},
    "DELIVERED": {"RETURNED"},
    "RETURNED": set(),
}


class InvalidShipmentStatusError(ValueError):
    """Raised when a requested shipment status is not supported."""


class ShipmentNotFoundError(LookupError):
    """Raised when the order or its shipment does not exist."""


class IllegalShipmentTransitionError(ValueError):
    """Raised when a status change is not allowed from the current status."""


def update_shipment_status(
    db: Session,
    order_id: str,
    new_status: str,
) -> ShipmentEvent:
    """Validate, persist, and return one shipment status change."""
    if new_status not in ALLOWED_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_STATUSES))
        raise InvalidShipmentStatusError(
            f"Invalid status '{new_status}'. Allowed statuses: {allowed}."
        )

    order = db.scalar(select(Order).where(Order.order_id == order_id))
    if order is None:
        raise ShipmentNotFoundError(f"Order '{order_id}' was not found.")

    shipment = db.scalar(
        select(Shipment).where(Shipment.order_id == order_id)
    )
    if shipment is None:
        raise ShipmentNotFoundError(
            f"Shipment for order '{order_id}' was not found."
        )

    old_status = shipment.status
    if new_status not in ALLOWED_TRANSITIONS[old_status]:
        allowed_next = sorted(ALLOWED_TRANSITIONS[old_status])
        if allowed_next:
            expected = ", ".join(allowed_next)
            message = (
                f"Cannot change shipment status from {old_status} to {new_status}. "
                f"Allowed next status: {expected}."
            )
        else:
            message = (
                f"Cannot change shipment status from {old_status}; "
                "there are no further allowed transitions."
            )
        raise IllegalShipmentTransitionError(message)

    event = ShipmentEvent(
        order_id=order_id,
        old_status=old_status,
        new_status=new_status,
        timestamp=datetime.now(timezone.utc),
    )
    shipment.status = new_status
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def get_shipment_history(db: Session, order_id: str) -> list[ShipmentEvent]:
    """Return ordered status change events for an existing shipment."""
    order_exists = db.scalar(select(Order.order_id).where(Order.order_id == order_id))
    if order_exists is None:
        raise ShipmentNotFoundError(f"Order '{order_id}' was not found.")

    shipment_exists = db.scalar(
        select(Shipment.order_id).where(Shipment.order_id == order_id)
    )
    if shipment_exists is None:
        raise ShipmentNotFoundError(
            f"Shipment for order '{order_id}' was not found."
        )

    return list(
        db.scalars(
            select(ShipmentEvent)
            .where(ShipmentEvent.order_id == order_id)
            .order_by(ShipmentEvent.id)
        ).all()
    )
