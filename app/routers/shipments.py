"""Shipment status and history endpoints."""

from json import JSONDecodeError
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ShipmentEvent
from app.services.webhook_service import deliver_status_event, get_subscriber_urls
from app.services.shipment_service import (
    IllegalShipmentTransitionError,
    InvalidShipmentStatusError,
    ShipmentNotFoundError,
    get_shipment_history,
    update_shipment_status,
)

router = APIRouter(prefix="/shipments", tags=["shipments"])


class ShipmentStatusOut(BaseModel):
    order_id: str
    status: str


class ShipmentEventOut(BaseModel):
    order_id: str
    old_status: str | None
    new_status: str
    timestamp: datetime


@router.patch("/{order_id}/status", response_model=ShipmentStatusOut)
async def patch_shipment_status(
    order_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ShipmentStatusOut:
    """Apply one valid status transition and record its history event."""
    try:
        payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid UTF-8 JSON.",
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be an object with a string 'status' field.",
        )

    try:
        event = update_shipment_status(db, order_id, payload["status"])
    except InvalidShipmentStatusError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ShipmentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except IllegalShipmentTransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    event_timestamp = event.timestamp
    if event_timestamp.tzinfo is None:
        event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)
    payload = {
        "event": "shipment.status_changed",
        "order_id": event.order_id,
        "old_status": event.old_status,
        "new_status": event.new_status,
        "timestamp": event_timestamp.isoformat(),
    }
    background_tasks.add_task(
        deliver_status_event,
        payload,
        get_subscriber_urls(db),
    )
    return ShipmentStatusOut(order_id=event.order_id, status=event.new_status)


@router.get("/{order_id}/history", response_model=list[ShipmentEventOut])
def shipment_history(
    order_id: str,
    db: Session = Depends(get_db),
) -> list[ShipmentEventOut]:
    """Return shipment status events in the order they were recorded."""
    try:
        events = get_shipment_history(db, order_id)
    except ShipmentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    return [
        ShipmentEventOut(
            order_id=event.order_id,
            old_status=event.old_status,
            new_status=event.new_status,
            timestamp=event.timestamp,
        )
        for event in events
    ]
