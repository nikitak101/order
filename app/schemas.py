"""Pydantic schemas for order input and API responses."""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderItemIn(BaseModel):
    """A single SKU and quantity in an order."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    qty: int = Field(gt=0, strict=True)

    @field_validator("sku")
    @classmethod
    def sku_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sku must not be blank")
        return value


class OrderIn(BaseModel):
    """Standard order format accepted by every ingestion source."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    order_date: date
    destination: str = Field(min_length=2)
    items: list[OrderItemIn] = Field(min_length=1)

    @field_validator("order_id", "customer_name", "destination")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("order_date", mode="before")
    @classmethod
    def date_must_match_iso_format(cls, value: Any) -> Any:
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("order_date must match YYYY-MM-DD")
        return value


class OrderItemOut(BaseModel):
    sku: str
    qty: int


class OrderOut(BaseModel):
    order_id: str
    customer_name: str
    order_date: date
    destination: str
    items: list[OrderItemOut]


class RejectedRecordOut(BaseModel):
    id: int
    batch_id: str
    order_id: str | None
    raw_data: str
    errors: list[dict[str, Any]]


class RejectedRecordInBatch(BaseModel):
    order_id: str | None
    errors: list[dict[str, Any]]


class IngestionResponse(BaseModel):
    batch_id: str
    accepted: list[str]
    rejected: list[RejectedRecordInBatch]
