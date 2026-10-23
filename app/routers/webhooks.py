"""Webhook subscription endpoint and signed test receiver."""

import json
import logging
import time
from json import JSONDecodeError
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.services.webhook_service import (
    InvalidWebhookUrlError,
    subscribe_webhook,
    verify_signature,
)

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger("app.webhooks")


class WebhookSubscriptionIn(BaseModel):
    url: str


class WebhookSubscriptionOut(BaseModel):
    url: str
    created: bool


@router.post("/webhooks/subscribe", response_model=WebhookSubscriptionOut)
async def subscribe(
    request: Request,
    db: Session = Depends(get_db),
) -> WebhookSubscriptionOut:
    """Register an HTTP or HTTPS URL for shipment events."""
    try:
        payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid UTF-8 JSON.",
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain a string 'url' field.",
        )

    try:
        subscription, created = subscribe_webhook(db, payload["url"])
    except InvalidWebhookUrlError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return WebhookSubscriptionOut(url=subscription.url, created=created)


@router.post("/webhook-receiver")
async def webhook_receiver(request: Request) -> dict[str, str]:
    """Verify and log signed webhook events for local demonstration."""
    timestamp = request.headers.get("X-Timestamp")
    signature = request.headers.get("X-Signature")
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature headers.")
    if not config.WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured.")

    try:
        timestamp_seconds = int(timestamp)
    except ValueError as error:
        raise HTTPException(status_code=401, detail="Invalid webhook timestamp.") from error
    now_seconds = int(time.time())
    if timestamp_seconds < now_seconds - 300 or timestamp_seconds > now_seconds + 300:
        raise HTTPException(status_code=401, detail="Webhook timestamp is expired or invalid.")

    body = await request.body()
    if not verify_signature(timestamp, body, signature, config.WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload: Any = json.loads(body)
    except (JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")

    event = payload.get("event")
    order_id = payload.get("order_id")
    if not isinstance(event, str) or not isinstance(order_id, str):
        raise HTTPException(
            status_code=400,
            detail="Webhook payload must include string event and order_id fields.",
        )

    logger.info(
        "receiver accepted event=%s order_id=%s old_status=%s new_status=%s",
        event,
        order_id,
        payload.get("old_status", "<none>"),
        payload.get("new_status", "<none>"),
    )
    return {"status": "received"}
