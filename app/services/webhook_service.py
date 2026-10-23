"""Webhook URL registration and signed shipment event delivery."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.models import WebhookSubscription

logger = logging.getLogger("app.webhooks")
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)


class InvalidWebhookUrlError(ValueError):
    """Raised when a subscription URL is malformed or disallowed."""


def _is_blocked_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_webhook_url(url: str) -> str:
    """Validate the scheme and prevent local/private destinations by default."""
    if not url or url != url.strip():
        raise InvalidWebhookUrlError("URL must not be empty or contain surrounding spaces.")

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        parsed.port  # Access validates malformed/out-of-range ports.
    except ValueError as error:
        raise InvalidWebhookUrlError("URL is malformed.") from error

    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise InvalidWebhookUrlError("URL must use http or https and include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidWebhookUrlError("URL must not include embedded credentials.")
    if config.ALLOW_LOCAL_WEBHOOKS:
        return url

    normalized_host = hostname.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise InvalidWebhookUrlError("Localhost webhook URLs are disabled.")

    try:
        addresses = {str(ipaddress.ip_address(normalized_host))}
    except ValueError:
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(
                    normalized_host,
                    parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror as error:
            raise InvalidWebhookUrlError("Webhook hostname could not be resolved.") from error

    if not addresses:
        raise InvalidWebhookUrlError("Webhook hostname did not resolve to an IP address.")
    if any(_is_blocked_address(address) for address in addresses):
        raise InvalidWebhookUrlError(
            "Private, loopback, and local webhook addresses are disabled."
        )
    return url


def subscribe_webhook(db: Session, url: str) -> tuple[WebhookSubscription, bool]:
    """Store a validated URL once; return whether this call created it."""
    validated_url = validate_webhook_url(url)
    existing = db.scalar(
        select(WebhookSubscription).where(WebhookSubscription.url == validated_url)
    )
    if existing is not None:
        return existing, False

    subscription = WebhookSubscription(url=validated_url)
    db.add(subscription)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.url == validated_url
            )
        )
        if existing is None:
            raise
        return existing, False

    db.refresh(subscription)
    return subscription, True


def get_subscriber_urls(db: Session) -> list[str]:
    """Return all registered webhook destinations."""
    return list(db.scalars(select(WebhookSubscription.url).order_by(WebhookSubscription.id)))


def _signature(timestamp: str, body: bytes, secret: str) -> str:
    signed_content = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()


def verify_signature(
    timestamp: str,
    body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Check a webhook signature using constant-time comparison."""
    expected = _signature(timestamp, body, secret)
    return hmac.compare_digest(expected, signature)


async def deliver_status_event(
    payload: dict[str, Any],
    subscriber_urls: list[str],
) -> None:
    """POST a signed event to subscribers, retrying each failed delivery."""
    if not subscriber_urls:
        return

    secret = config.WEBHOOK_SECRET
    if not secret:
        logger.error(
            "order_id=%s event=%s webhook delivery failed: WEBHOOK_SECRET is not configured",
            payload.get("order_id", "<unknown>"),
            payload.get("event", "<unknown>"),
        )
        return

    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in subscriber_urls:
            destination = urlsplit(url).hostname or "<unknown>"
            delivered = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                timestamp = str(int(time.time()))
                signature = _signature(timestamp, body, secret)
                logger.info(
                    "order_id=%s webhook attempt=%d/%d url=%s",
                    payload.get("order_id", "<unknown>"),
                    attempt,
                    MAX_ATTEMPTS,
                    destination,
                )
                try:
                    response = await client.post(
                        url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-Timestamp": timestamp,
                            "X-Signature": signature,
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPError as error:
                    logger.warning(
                        "order_id=%s webhook attempt=%d/%d failed: %s",
                        payload.get("order_id", "<unknown>"),
                        attempt,
                        MAX_ATTEMPTS,
                        error,
                    )
                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
                    continue

                logger.info(
                    "order_id=%s webhook delivered attempt=%d/%d status_code=%d",
                    payload.get("order_id", "<unknown>"),
                    attempt,
                    MAX_ATTEMPTS,
                    response.status_code,
                )
                delivered = True
                break

            if not delivered:
                logger.error(
                    "order_id=%s webhook delivery failed after %d attempts host=%s",
                    payload.get("order_id", "<unknown>"),
                    MAX_ATTEMPTS,
                    destination,
                )
